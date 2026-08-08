import asyncio
import io
from pathlib import Path
import time

from PIL import Image

from utils import slide_capture


class _TrackingSemaphore:
    def __init__(self, limit: int) -> None:
        self._semaphore = asyncio.Semaphore(limit)
        self.active = 0
        self.peak = 0

    async def __aenter__(self) -> None:
        await self._semaphore.acquire()
        self.active += 1
        self.peak = max(self.peak, self.active)

    async def __aexit__(self, *_args: object) -> None:
        self.active -= 1
        self._semaphore.release()


def test_one_shot_fallback_is_process_wide_bounded(monkeypatch) -> None:
    tracker = _TrackingSemaphore(2)
    output = io.BytesIO()
    Image.new("RGB", (1280, 720), "white").save(output, format="PNG")
    png = output.getvalue()

    def fake_run(args: list[str], _timeout: int) -> bytes:
        screenshot = next(value for value in args if value.startswith("--screenshot="))
        time.sleep(0.02)
        Path(screenshot.split("=", 1)[1]).write_bytes(png)
        return b""

    monkeypatch.setattr(slide_capture, "CHROME_REUSE_ENABLED", False)
    monkeypatch.setattr(slide_capture, "RENDER_CACHE_SIZE", 0)
    monkeypatch.setattr(slide_capture, "_get_render_sem", lambda: tracker)
    monkeypatch.setattr(slide_capture, "find_chrome", lambda: "synthetic-chrome")
    monkeypatch.setattr(slide_capture, "_run_chrome_screenshot", fake_run)

    results = asyncio.run(
        slide_capture.render_html_list_to_pngs(
            [f"<html><body>{index}</body></html>" for index in range(8)]
        )
    )

    assert len(results) == 8
    assert all(result.startswith(b"\x89PNG") for result in results)
    assert tracker.active == 0
    assert tracker.peak == 2


def test_identical_concurrent_render_reuses_one_inflight_browser_work(monkeypatch) -> None:
    calls = 0

    async def fake_uncached(html: str, timeout: int = 60) -> bytes:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return b"rendered"

    slide_capture.reset_render_runtime(clear_caches=True, retire_session=False)
    monkeypatch.setattr(slide_capture, "RENDER_CACHE_SIZE", 4)
    monkeypatch.setattr(slide_capture, "_render_html_to_png_uncached", fake_uncached)

    async def scenario() -> tuple[bytes, ...]:
        return tuple(
            await asyncio.gather(
                *(slide_capture.render_html_to_png("same") for _ in range(6))
            )
        )

    assert asyncio.run(scenario()) == (b"rendered",) * 6
    assert calls == 1
