"""Headless per-slide capture for the vision-QA pass. Pure subprocess chrome
screenshot (no browser dependency — consistent with export spawning an external
binary) + a deterministic PIL crop: the /pdf-maker page stacks slides at exactly
1280x720 with gap 0 / margin 0, so slide i is the box (0, i*720, 1280, (i+1)*720)."""

import asyncio
import io
import os
import pathlib
import signal
import subprocess
import tempfile
from typing import List, Optional

SLIDE_W = 1280
SLIDE_H = 720


def _kill_process_tree(proc: "subprocess.Popen") -> None:
    """Kill a chrome process AND its renderer/gpu children. `subprocess` timeout only
    kills the direct child, orphaning chrome's helper processes — and leaked chromes
    starve later launches (every subsequent render then hangs). Tree-kill prevents the
    cascade."""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            proc.kill()


def _run_chrome_screenshot(args: List[str], timeout: int) -> None:
    """Run a headless-chrome screenshot to completion, killing the whole process tree
    on timeout so a hung render leaks nothing. Callers check for the output file to
    decide success/failure — a timed-out render simply leaves no file."""
    popen_kwargs: dict = {"stdout": subprocess.PIPE, "stderr": subprocess.PIPE}
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(args, **popen_kwargs)
    try:
        proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc)
        try:
            proc.communicate(timeout=5)
        except Exception:
            pass


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
        await asyncio.to_thread(_run_chrome_screenshot, args, timeout)
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


async def render_html_to_png(html: str, timeout: int = 60) -> bytes:
    """Render a self-contained HTML document to an exact 1280x720 PNG via headless
    chrome. Used by the authored-mode pipeline (the HTML is authored in-memory, not
    served from /pdf-maker). Raises RuntimeError if no chrome is found or the
    screenshot fails."""
    from PIL import Image

    chrome = find_chrome()
    if not chrome:
        raise RuntimeError(
            "No chrome/chromium found for slide capture (set CHROME_PATH)."
        )
    with tempfile.TemporaryDirectory() as td:
        html_path = os.path.join(td, "slide.html")
        out = os.path.join(td, "slide.png")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        args = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            f"--window-size={SLIDE_W},{SLIDE_H}",
            f"--screenshot={out}",
            "--virtual-time-budget=20000",
            pathlib.Path(html_path).as_uri(),
        ]
        await asyncio.to_thread(_run_chrome_screenshot, args, timeout)
        if not os.path.isfile(out):
            raise RuntimeError("chrome screenshot produced no output file")
        img = Image.open(out).convert("RGB").crop((0, 0, SLIDE_W, SLIDE_H))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


# Bound concurrent headless-chrome subprocesses PROCESS-WIDE (not per deck): each render
# is a separate chrome process, and M concurrent decks would otherwise spawn M×N browsers
# and exhaust memory/CPU (the observed hang cascade). One shared semaphore caps total
# chrome across all authored generations in this process. Tune per deploy via
# AUTHORED_RENDER_CONCURRENCY (low-RAM hosts: 2; beefy: 8).
def _env_int(name: str, default: int) -> int:
    try:
        v = int(os.getenv(name, "").strip())
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


RENDER_CONCURRENCY = _env_int("AUTHORED_RENDER_CONCURRENCY", 4)
_render_sem: Optional[asyncio.Semaphore] = None


def _get_render_sem() -> asyncio.Semaphore:
    # Created lazily inside the running loop (first use is always within an async call).
    global _render_sem
    if _render_sem is None:
        _render_sem = asyncio.Semaphore(RENDER_CONCURRENCY)
    return _render_sem


def _placeholder_png() -> bytes:
    """A plain neutral 1280x720 PNG used when a single slide fails to render, so the
    deck still assembles (N valid images) instead of aborting on one bad slide."""
    from PIL import Image

    img = Image.new("RGB", (SLIDE_W, SLIDE_H), (248, 250, 252))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def render_html_list_to_pngs(htmls: List[str], timeout: int = 60) -> List[bytes]:
    """Render many authored HTML slides to PNG bytes (order preserved), with bounded
    concurrency and per-slide resilience: a slide that fails to render becomes a neutral
    placeholder so one failure can't abort the whole deck."""
    sem = _get_render_sem()

    async def render_one(html: str) -> bytes:
        async with sem:
            try:
                return await render_html_to_png(html, timeout=timeout)
            except Exception:
                return _placeholder_png()

    return list(await asyncio.gather(*[render_one(h) for h in htmls]))
