"""Vector print-to-PDF over the same isolated CDP pipe as slide capture.

The screenshot path (``utils.slide_capture_pipe_render``) and this module share
``drive_admitted_htmls_over_pipe``: each admitted document still gets its own
disposable BrowserContext/target/session, blocked network, and the same
font/image readiness wait. Only the capture differs: ``Page.printToPDF``
keeps text as text (with the fonts Chromium laid it out in embedded and
subset), where ``Page.captureScreenshot`` flattens it into pixels.
"""

from __future__ import annotations

import base64
import pathlib
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from utils.renderer_isolation import AdmittedHtml
from utils.slide_capture_pipe_render import PageCommand, drive_admitted_htmls_over_pipe

# CSS lays out print pages at 96 px per inch.
CSS_PX_PER_INCH = 96
# Chromium accepts printToPDF scales in this closed range.
_MIN_PRINT_SCALE = 0.1
_MAX_PRINT_SCALE = 2.0


def print_scale(
    *, width_px: int, height_px: int, paper_width_in: float, paper_height_in: float,
) -> float:
    """Uniform scale that maps the CSS canvas exactly onto the physical page.

    A 16:9 authored canvas is 96 px/in already (scale 1); document canvases
    (A4) carry more CSS pixels than their paper holds at 96 px/in, so the
    layout width must be the canvas width, not the paper width.
    """

    if width_px <= 0 or height_px <= 0 or paper_width_in <= 0 or paper_height_in <= 0:
        raise ValueError("print canvas size must be positive")
    scale = paper_width_in * CSS_PX_PER_INCH / width_px
    vertical = paper_height_in * CSS_PX_PER_INCH / height_px
    if abs(scale - vertical) > 1e-3 * scale:
        raise ValueError("print canvas aspect ratio does not match its paper")
    if not _MIN_PRINT_SCALE <= scale <= _MAX_PRINT_SCALE:
        raise ValueError("print canvas scale is outside the supported range")
    return scale


def print_admitted_htmls_to_pdf_over_pipe(
    admitted_htmls: Sequence[AdmittedHtml],
    timeout: int,
    *,
    width_px: int,
    height_px: int,
    paper_width_in: float,
    paper_height_in: float,
    blocked_urls: Sequence[str],
    chrome_pipe: Callable[[str, pathlib.Path], Any],
    find_chrome: Callable[[], str | None],
    instrument_html: Callable[..., tuple[str, str]],
) -> list[bytes]:
    """Print each admitted document to one single-page PDF, in order."""

    scale = print_scale(
        width_px=width_px, height_px=height_px,
        paper_width_in=paper_width_in, paper_height_in=paper_height_in,
    )
    params = {
        "paperWidth": paper_width_in,
        "paperHeight": paper_height_in,
        "marginTop": 0,
        "marginBottom": 0,
        "marginLeft": 0,
        "marginRight": 0,
        "printBackground": True,
        "preferCSSPageSize": False,
        "scale": scale,
        # One slide is one page; a rounding overflow must never add a page.
        "pageRanges": "1",
        "displayHeaderFooter": False,
    }

    def capture(
        page_command: PageCommand,
        _frame_id: str,
        _admitted: AdmittedHtml,
        _ready: Mapping[str, Any],
    ) -> bytes:
        printed = page_command("Page.printToPDF", params)
        payload = base64.b64decode(str(printed.get("data") or ""), validate=True)
        if not payload.startswith(b"%PDF-"):
            raise RuntimeError("Chrome DevTools printToPDF returned no PDF")
        return payload

    return drive_admitted_htmls_over_pipe(
        admitted_htmls, timeout, width_px=width_px, height_px=height_px,
        blocked_urls=blocked_urls, chrome_pipe=chrome_pipe,
        find_chrome=find_chrome, instrument_html=instrument_html,
        capture=capture,
    )


__all__ = ["CSS_PX_PER_INCH", "print_admitted_htmls_to_pdf_over_pipe", "print_scale"]
