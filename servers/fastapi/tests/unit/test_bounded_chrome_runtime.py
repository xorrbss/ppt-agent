import asyncio
import base64
import io
from pathlib import Path
import time

from PIL import Image
import pytest

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
    monkeypatch.setenv("AUTHORED_RENDERER_MODE", "legacy")
    monkeypatch.setenv("AUTHORED_RENDERER_LEGACY_ENABLED", "true")

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


def test_metrics_recovery_uses_bounded_one_shot_outside_external_worker(
    monkeypatch,
) -> None:
    source = '<html><body><div data-qid="metric-owner">Text</div></body></html>'
    expected = {"width": 1280, "height": 720, "elements": []}
    calls: list[tuple[bool, str | None, str | None]] = []

    async def fake_one_shot(
        _html: str,
        timeout: int = 60,
        *,
        admitted_source: bool = False,
        cache_html: str | None = None,
        script_nonce: str | None = None,
    ) -> bytes:
        assert timeout == 2
        calls.append((admitted_source, cache_html, script_nonce))
        slide_capture._cache_browser_metrics(cache_html or _html, expected)
        return b"png"

    async def fail_shared_runtime(_html: str, timeout: int = 60) -> bytes:
        raise AssertionError("metrics recovery must not enter the shared CDP runtime")

    slide_capture.reset_render_runtime(clear_caches=True, retire_session=False)
    monkeypatch.setattr(
        slide_capture, "renderer_requires_external_worker", lambda: False
    )
    monkeypatch.setattr(
        slide_capture, "_render_html_to_png_subprocess", fake_one_shot
    )
    monkeypatch.setattr(
        slide_capture, "_render_html_to_png_uncached", fail_shared_runtime
    )

    assert asyncio.run(slide_capture.ensure_browser_metrics(source, timeout=2)) == expected
    assert len(calls) == 1
    assert calls[0][:2] == (True, source)
    assert calls[0][2]


@pytest.mark.parametrize("kind", ["render", "metrics"])
def test_shared_render_cancellation_preserves_other_waiter_and_reaps_last(
    monkeypatch, kind,
) -> None:
    async def scenario():
        started = asyncio.Event()
        finish = asyncio.Event()
        cancelled = asyncio.Event()
        calls = 0
        source = "shared-cancellation-" + kind

        async def fake_render(html, timeout=60):
            nonlocal calls
            calls += 1
            started.set()
            try:
                await finish.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            slide_capture._cache_browser_metrics(html, {"elements": []})
            return b"png"

        slide_capture.reset_render_runtime(clear_caches=True, retire_session=False)
        monkeypatch.setattr(slide_capture, "_render_html_to_png_uncached", fake_render)
        monkeypatch.setattr(slide_capture, "renderer_requires_external_worker", lambda: True)
        call = (slide_capture.render_html_to_png if kind == "render"
                else slide_capture.ensure_browser_metrics)
        inflight = (slide_capture._render_inflight if kind == "render"
                    else slide_capture._browser_metrics_inflight)
        first = asyncio.create_task(call(source))
        await started.wait()
        second = asyncio.create_task(call(source))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not cancelled.is_set()
        finish.set()
        result = await second
        if kind == "render":
            assert result == b"png"
        else:
            assert result["elements"] == []
            assert result["source_digest"].startswith("sha256:")
        assert calls == 1
        assert not inflight
        assert not slide_capture._shared_render_waiters

        started.clear()
        finish.clear()
        last = asyncio.create_task(call(source + "-new"))
        await started.wait()
        last.cancel()
        with pytest.raises(asyncio.CancelledError):
            await last
        assert cancelled.is_set()
        assert not inflight
        assert not slide_capture._shared_render_waiters

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["error", "timeout", "cancel"])
def test_reusable_chrome_failure_cleans_page_without_killing_parallel_sibling(
    monkeypatch, failure,
) -> None:
    from utils import slide_capture_reusable_render as reusable

    output = io.BytesIO()
    Image.new("RGB", (1280, 720), "white").save(output, format="PNG")
    encoded_png = base64.b64encode(output.getvalue()).decode("ascii")
    runtimes = []
    closed_targets = []
    targets = []

    class Runtime:
        base_url = "http://fake-chrome"
        session_count = 0
        active_sessions = 0
        retired = False
        close_count = 0

        def alive(self):
            return not self.close_count

        def close(self):
            self.close_count += 1

    def launch():
        runtime = Runtime()
        runtimes.append(runtime)
        return runtime

    class Context:
        status = 200

        def __init__(self, target_id=None):
            self.target_id = target_id

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def json(self):
            return {"id": self.target_id, "webSocketDebuggerUrl": self.target_id}

    class Session(Context):
        def __init__(self, **_kwargs):
            pass

        def put(self, _url):
            target_id = str(len(targets) + 1)
            targets.append(target_id)
            return Context(target_id)

        def get(self, url):
            closed_targets.append(url.rsplit("/", 1)[-1])
            return Context()

        def ws_connect(self, target_id, **_kwargs):
            return Context(target_id)

    monkeypatch.setattr(slide_capture, "_reusable_chrome", None)
    monkeypatch.setattr(slide_capture, "_retired_reusable_chromes", set())
    monkeypatch.setattr(slide_capture, "_launch_reusable_chrome", launch)
    monkeypatch.setattr(reusable.aiohttp, "ClientSession", Session)
    monkeypatch.setattr(slide_capture, "_instrument_authored_html", lambda html, **_: (html, "digest"))
    monkeypatch.setattr(slide_capture, "_runtime_value", lambda _: None)

    async def scenario():
        first_started = asyncio.Event()
        sibling_started = asyncio.Event()
        finish_sibling = asyncio.Event()

        async def command(ws, _ordinal, method, _params):
            if method == "Page.enable":
                if ws.target_id == "1":
                    first_started.set()
                    await sibling_started.wait()
                    if failure == "error":
                        raise RuntimeError("synthetic CDP failure")
                    await asyncio.Future()
                else:
                    sibling_started.set()
                    await finish_sibling.wait()
            if method == "Page.getFrameTree":
                return {"frameTree": {"frame": {"id": "frame"}}}
            if method == "Page.captureScreenshot":
                return {"data": encoded_png}
            return {}

        monkeypatch.setattr(slide_capture, "_cdp_command", command)
        first = asyncio.create_task(slide_capture._render_html_in_reusable_chrome(
            "first", timeout=1 if failure == "timeout" else 10
        ))
        await first_started.wait()
        sibling = asyncio.create_task(slide_capture._render_html_in_reusable_chrome(
            "sibling", timeout=10
        ))
        await sibling_started.wait()
        if failure == "cancel":
            first.cancel()
        expected_error = {"error": RuntimeError, "timeout": TimeoutError,
                          "cancel": asyncio.CancelledError}[failure]
        with pytest.raises(expected_error):
            await first
        shared = runtimes[0]
        assert shared.session_count == 2
        assert shared.active_sessions == 1
        assert shared.retired
        assert shared.close_count == 0
        assert closed_targets == ["1"]

        # New arrivals get a fresh runtime while the existing sibling finishes.
        replacement = slide_capture._get_reusable_chrome_sync()
        assert replacement is not shared
        slide_capture._release_reusable_chrome_sync(replacement)
        finish_sibling.set()
        png, _ = await sibling
        assert png.startswith(b"\x89PNG")
        assert closed_targets == ["1", "2"]
        assert shared.active_sessions == 0
        assert shared.close_count == 1
        assert not slide_capture._retired_reusable_chromes
        slide_capture._close_reusable_chrome()

    asyncio.run(scenario())
