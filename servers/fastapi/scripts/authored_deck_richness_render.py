"""Thin, mockable wrapper around the one real Chromium render per slide.

Isolated into its own module so tests can monkeypatch
``render_html_to_png_with_metrics`` without pulling Chromium into the test
run, per the task's "mock the renderer, no Chromium" requirement.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from utils.slide_capture import render_html_to_png_with_metrics


@dataclass(frozen=True, slots=True)
class RenderOutcome:
    browser_metrics: Mapping[str, Any] | None
    error: str | None


async def render_slide_metrics(html: str, *, timeout_s: int) -> RenderOutcome:
    """Render one slide and return its browser metrics, never raising.

    A single bad slide (renderer policy rejection, Chromium crash, timeout)
    must not abort measurement of the rest of the deck; the failure is
    reported per-slide instead (``render_error`` downstream) so it shows up
    in ``slides.jsonl`` rather than silently vanishing.

    (Parameter named ``timeout_s`` rather than ``timeout``: ASYNC109 flags an
    async function with a plain ``timeout`` parameter as likely
    ``asyncio.wait_for`` misuse. This one only forwards the value to the
    renderer's own subprocess timeout -- the same pattern already exempted
    for ``utils.slide_capture`` in ``pyproject.toml``, which this script
    cannot edit -- so the parameter is renamed instead of suppressed.)
    """

    try:
        capture = await render_html_to_png_with_metrics(html, timeout=timeout_s)
    except Exception as error:  # noqa: BLE001 - measurement must survive one bad slide
        return RenderOutcome(browser_metrics=None, error=f"{type(error).__name__}: {error}")
    return RenderOutcome(browser_metrics=capture.browser_metrics, error=None)
