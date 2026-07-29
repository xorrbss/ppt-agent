"""Headless per-slide capture for the vision-QA pass. Pure subprocess chrome
screenshot (no browser dependency — consistent with export spawning an external
binary) + a deterministic PIL crop: the /pdf-maker page stacks slides at exactly
1280x720 with gap 0 / margin 0, so slide i is the box (0, i*720, 1280, (i+1)*720)."""

import asyncio
import hashlib
import io
import logging
import os
import pathlib
import signal
import subprocess
import tempfile
import time
from collections import OrderedDict
from typing import Dict, List, Optional

SLIDE_W = 1280
SLIDE_H = 720
LOGGER = logging.getLogger(__name__)

_MINIMUM_AUTHORED_FONT_SCRIPT = r"""<script data-presenton-minimum-font-size>
(() => {
  const minimumPx = 12; // 12 CSS px = 9 PowerPoint points.
  const rules = [];
  [document.body, ...document.body.querySelectorAll("*")].forEach((element, index) => {
    if (["SCRIPT", "STYLE", "TEXTAREA", "NOSCRIPT", "TEMPLATE"].includes(element.tagName)) return;
    const hasOwnText = [...element.childNodes].some(
      (node) => node.nodeType === Node.TEXT_NODE && Boolean((node.textContent || "").trim())
    );
    if (hasOwnText && parseFloat(getComputedStyle(element).fontSize) < minimumPx) {
      element.style.setProperty("font-size", `${minimumPx}px`, "important");
    }
    ["::before", "::after"].forEach((pseudo) => {
      const style = getComputedStyle(element, pseudo);
      if (style.content && style.content !== "none" && style.content !== "normal" &&
          parseFloat(style.fontSize) < minimumPx) {
        const attribute = `data-presenton-min-font-${index}-${pseudo === "::before" ? "before" : "after"}`;
        element.setAttribute(attribute, "true");
        rules.push(`[${attribute}]${pseudo}{font-size:${minimumPx}px!important}`);
      }
    });
  });
  if (rules.length) {
    const sheet = document.createElement("style");
    sheet.textContent = rules.join("\n");
    document.head.appendChild(sheet);
  }
})();
</script>"""


def _with_minimum_authored_font_size(html: str) -> str:
    """Instrument only the temporary render document; persisted authored HTML stays clean."""
    closing_body = html.lower().rfind("</body>")
    if closing_body < 0:
        return html + _MINIMUM_AUTHORED_FONT_SCRIPT
    return html[:closing_body] + _MINIMUM_AUTHORED_FONT_SCRIPT + html[closing_body:]


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


async def _wait_for_output(out: str, timeout: float = 15.0) -> bool:
    """Poll for the --screenshot file after the chrome process we waited on exits.
    On Windows chrome.exe is a thin launcher: it spawns the real browser child and
    returns, so `communicate()` can complete BEFORE the detached child finishes
    writing the screenshot. Checking once immediately races that write and spuriously
    reports 'no output file' (→ blank placeholder). Poll briefly instead."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.isfile(out) and os.path.getsize(out) > 0:
            return True
        await asyncio.sleep(0.25)
    return os.path.isfile(out) and os.path.getsize(out) > 0


def _playwright_headless_shell_candidates() -> List[pathlib.Path]:
    """Return installed Playwright headless-shell executables, newest first.

    Desktop Chrome can hand a headless request to an already-running browser and
    exit successfully without writing a screenshot. Playwright's standalone
    headless shell does not share the user's browser profile/process, so it is the
    most reliable automatic choice for server-side slide rendering.
    """
    roots: List[pathlib.Path] = []
    configured_root = os.getenv("PLAYWRIGHT_BROWSERS_PATH")
    if configured_root and configured_root != "0":
        roots.append(pathlib.Path(configured_root).expanduser())

    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        roots.append(pathlib.Path(local_app_data) / "ms-playwright")
    roots.extend(
        [
            pathlib.Path.home() / ".cache" / "ms-playwright",
            pathlib.Path.home() / "Library" / "Caches" / "ms-playwright",
        ]
    )

    found: List[pathlib.Path] = []
    seen = set()
    for root in roots:
        try:
            resolved_root = root.resolve()
        except OSError:
            resolved_root = root
        if resolved_root in seen or not root.is_dir():
            continue
        seen.add(resolved_root)
        for candidate in root.glob(
            "chromium_headless_shell-*/*/chrome-headless-shell*"
        ):
            if candidate.name not in {
                "chrome-headless-shell",
                "chrome-headless-shell.exe",
            }:
                continue
            if candidate.is_file():
                found.append(candidate)

    def revision(candidate: pathlib.Path) -> int:
        for part in candidate.parts:
            prefix = "chromium_headless_shell-"
            if part.startswith(prefix):
                try:
                    return int(part[len(prefix) :])
                except ValueError:
                    return -1
        return -1

    return sorted(found, key=revision, reverse=True)


def find_chrome() -> Optional[str]:
    for env in ("CHROME_PATH", "PUPPETEER_EXECUTABLE_PATH", "CHROMIUM_PATH"):
        p = os.getenv(env)
        if p and os.path.isfile(p):
            return p
    headless_shells = _playwright_headless_shell_candidates()
    if headless_shells:
        return str(headless_shells[0])
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
            "--no-sandbox",
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
        await asyncio.to_thread(_run_chrome_screenshot, args, timeout)
        if not await _wait_for_output(out):
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


async def _render_html_to_png_uncached(html: str, timeout: int = 60) -> bytes:
    """Render a self-contained HTML document to an exact 1280x720 PNG via headless
    chrome. Used by the authored-mode pipeline (the HTML is authored in-memory, not
    served from /pdf-maker). Raises RuntimeError if no chrome is found or the
    screenshot fails.

    Bounded by the process-wide render semaphore (AUTHORED_RENDER_CONCURRENCY) so EVERY
    caller — the deck render AND the vision-QA re-render — shares one cap on concurrent
    headless-chrome subprocesses (the M×N hang cascade guard)."""
    from PIL import Image

    chrome = find_chrome()
    if not chrome:
        raise RuntimeError(
            "No chrome/chromium found for slide capture (set CHROME_PATH)."
        )
    async with _get_render_sem():
        # ignore_cleanup_errors: see capture_slides — the detached chrome child can
        # hold the profile lock past screenshot capture, which would otherwise fail
        # temp-dir teardown on Windows after the PNG is already read.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            html_path = os.path.join(td, "slide.html")
            out = os.path.join(td, "slide.png")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(_with_minimum_authored_font_size(html))
            args = [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                # Isolated profile — see capture_slides: without it chrome.exe hands
                # off to a user's already-running Chrome and writes no screenshot,
                # silently blanking every authored slide.
                f"--user-data-dir={os.path.join(td, 'profile')}",
                "--no-first-run",
                "--no-default-browser-check",
                "--hide-scrollbars",
                "--force-device-scale-factor=1",
                f"--window-size={SLIDE_W},{SLIDE_H}",
                f"--screenshot={out}",
                "--virtual-time-budget=20000",
                pathlib.Path(html_path).as_uri(),
            ]
            await asyncio.to_thread(_run_chrome_screenshot, args, timeout)
            if not await _wait_for_output(out):
                raise RuntimeError("chrome screenshot produced no output file")
            img = Image.open(out).convert("RGB").crop((0, 0, SLIDE_W, SLIDE_H))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()


async def render_html_to_png(html: str, timeout: int = 60) -> bytes:
    """Render one authored slide, reusing successful identical renders in-process.

    Vision QA and repeated generation/export attempts can request the exact same HTML.
    A small memory-bounded LRU avoids relaunching Chrome, while the in-flight table
    prevents concurrent duplicate requests from doing the same work twice.
    """
    if RENDER_CACHE_SIZE <= 0:
        return await _render_html_to_png_uncached(html, timeout=timeout)

    key = hashlib.sha256(
        f"authored-render-v2-minimum-9pt\0{timeout}\0{html}".encode("utf-8")
    ).hexdigest()
    cached = _render_cache.get(key)
    if cached is not None:
        _render_cache.move_to_end(key)
        return cached

    task = _render_inflight.get(key)
    if task is None:
        task = asyncio.create_task(_render_html_to_png_uncached(html, timeout=timeout))
        _render_inflight[key] = task
    try:
        rendered = await asyncio.shield(task)
    finally:
        if _render_inflight.get(key) is task and task.done():
            _render_inflight.pop(key, None)

    _render_cache[key] = rendered
    _render_cache.move_to_end(key)
    while len(_render_cache) > RENDER_CACHE_SIZE:
        _render_cache.popitem(last=False)
    return rendered


# Bound concurrent headless-chrome subprocesses PROCESS-WIDE (not per deck): each render
# is a separate chrome process, and M concurrent decks would otherwise spawn M×N browsers
# and exhaust memory/CPU (the observed hang cascade). One shared semaphore, acquired at the
# single chokepoint (render_html_to_png), caps total chrome across all authored generations
# and all callers in this process. Tune per deploy via AUTHORED_RENDER_CONCURRENCY
# (low-RAM hosts: 2; beefy: 8).
def _env_int(name: str, default: int) -> int:
    try:
        v = int(os.getenv(name, "").strip())
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def _env_nonnegative_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "").strip())
        return value if value >= 0 else default
    except (TypeError, ValueError):
        return default


RENDER_CACHE_SIZE = _env_nonnegative_int("AUTHORED_RENDER_CACHE_SIZE", 32)
_render_cache: "OrderedDict[str, bytes]" = OrderedDict()
_render_inflight: Dict[str, "asyncio.Task[bytes]"] = {}
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
    """Render many authored HTML slides to PNG bytes (order preserved), with per-slide
    resilience: a slide that fails to render becomes a neutral placeholder so one failure
    can't abort the whole deck. Concurrency is bounded inside render_html_to_png."""

    async def render_one(index: int, html: str) -> tuple[bytes, bool]:
        try:
            return await render_html_to_png(html, timeout=timeout), False
        except Exception as exc:
            LOGGER.warning(
                "Authored slide render failed at index %d: %s", index, exc
            )
            return _placeholder_png(), True

    results = list(
        await asyncio.gather(*[render_one(index, html) for index, html in enumerate(htmls)])
    )
    if results and all(failed for _, failed in results):
        raise RuntimeError(
            "Every authored slide failed to render. Install Playwright's Chromium "
            "headless shell or set CHROME_PATH to a working headless browser."
        )
    return [png for png, _ in results]
