"""Headless per-slide capture for the vision-QA pass. Pure subprocess chrome
screenshot (no browser dependency — consistent with export spawning an external
binary) + a deterministic PIL crop: the /pdf-maker page stacks slides at exactly
1280x720 with gap 0 / margin 0, so slide i is the box (0, i*720, 1280, (i+1)*720)."""

import asyncio
import io
import os
import subprocess
import tempfile
from typing import List, Optional

SLIDE_W = 1280
SLIDE_H = 720


def find_chrome() -> Optional[str]:
    for env in ("CHROME_PATH", "PUPPETEER_EXECUTABLE_PATH", "CHROMIUM_PATH"):
        p = os.getenv(env)
        if p and os.path.isfile(p):
            return p
    for cand in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
    ):
        if os.path.isfile(cand):
            return cand
    return None


async def capture_slides(
    presentation_id: str,
    n_slides: int,
    base_url: str = "http://127.0.0.1:5000",
    timeout: int = 90,
) -> List[bytes]:
    """Capture the deck via headless chrome and slice it into per-slide PNG bytes.
    Raises RuntimeError if no chrome is found or the screenshot fails."""
    from PIL import Image

    chrome = find_chrome()
    if not chrome:
        raise RuntimeError(
            "No chrome/chromium found for slide capture (set CHROME_PATH)."
        )
    n = max(1, int(n_slides))
    height = n * SLIDE_H
    url = f"{base_url}/pdf-maker?id={presentation_id}"
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "deck.png")
        args = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            f"--window-size={SLIDE_W},{height}",
            f"--screenshot={out}",
            "--virtual-time-budget=20000",
            url,
        ]
        await asyncio.to_thread(
            subprocess.run, args, timeout=timeout, capture_output=True
        )
        if not os.path.isfile(out):
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
