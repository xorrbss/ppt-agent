"""Legacy stacked-page batch capture with deterministic crops."""
# ruff: noqa: ASYNC109

from __future__ import annotations

import asyncio
import io
import os
import tempfile
from collections.abc import Awaitable, Callable
from typing import List

SLIDE_W = 1280
SLIDE_H = 720


async def capture_slides_impl(
    presentation_id: str,
    n_slides: int,
    base_url: str = "http://127.0.0.1:5000",
    timeout: int = 90,
    *,
    env_enabled: Callable[[str], bool],
    find_chrome: Callable[[], str | None],
    run_chrome_screenshot: Callable[[List[str], int], bytes],
    wait_for_output: Callable[[str], Awaitable[bool]],
) -> List[bytes]:  # noqa: ASYNC109
    """Capture the deck via headless chrome and slice it into per-slide PNG bytes.
    Raises RuntimeError if no chrome is found or the screenshot fails."""
    from PIL import Image

    if not env_enabled("AUTHORED_RENDERER_LEGACY_ENABLED"):
        raise RuntimeError(
            "legacy deck URL capture is disabled; set "
            "AUTHORED_RENDERER_LEGACY_ENABLED=1 only for legacy read/export"
        )
    chrome = find_chrome()
    if not chrome:
        raise RuntimeError(
            "No chrome/chromium found for slide capture (set CHROME_PATH)."
        )
    n = max(1, int(n_slides))
    height = n * SLIDE_H
    url = f"{base_url}/pdf-maker?id={presentation_id}"
    # ignore_cleanup_errors: the detached headless-chrome child can outlive the
    # screenshot and keep the --user-data-dir profile locked; on Windows that turns
    # temp-dir teardown into a PermissionError. The PNG is already captured by then,
    # so leave the locked profile files for the OS to reap instead of crashing.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        out = os.path.join(td, "deck.png")
        args = [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-domain-reliability",
                "--disable-sync",
                "--metrics-recording-only",
                "--no-pings",
            # Isolated profile so the invocation always spawns its OWN headless
            # instance. Without it, on a desktop where the user already has Chrome
            # open, chrome.exe hands the request to the running instance and returns
            # immediately (exit 0, NO screenshot) → every slide degrades to a blank
            # placeholder. --no-first-run/--no-default-browser-check keep the fresh
            # profile from doing first-run setup that stalls under concurrency.
            f"--user-data-dir={os.path.join(td, 'profile')}",
            "--no-first-run",
            "--no-default-browser-check",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            f"--window-size={SLIDE_W},{height}",
            f"--screenshot={out}",
            "--virtual-time-budget=20000",
            url,
        ]
        await asyncio.to_thread(run_chrome_screenshot, args, timeout)
        if not await wait_for_output(out):
            raise RuntimeError("chrome screenshot produced no output file")
        img = Image.open(out).convert("RGB")
        slides: List[bytes] = []
        for i in range(n):
            top = i * SLIDE_H
            crop = img.crop((0, top, SLIDE_W, top + SLIDE_H))
            buf = io.BytesIO()
            crop.save(buf, format="PNG")
            slides.append(buf.getvalue())
        return slides
