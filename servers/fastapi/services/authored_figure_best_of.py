"""Render a figure slide as a few CSS-only candidates and keep the best one.

Cost is the whole point of this module, so it does exactly one thing beyond the
render the pipeline already pays for: it renders the extra candidates through
the *same* capture entry point (``render_html_to_png_with_metrics``), one after
another. That entry point keeps a single reusable Chromium process when
``AUTHORED_CHROME_REUSE`` is on (the default), so N candidates cost N page loads
in one browser rather than N browser launches, and the winner stays in the
render cache for the pipeline's own render stage to pick up.

No provider call is made here, and none can be: a candidate is the baseline
document with different density/emphasis classes.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from utils.authored_dom_quality import inspect_authored_dom
from utils.authored_figure_variants import (
    FigureSelection,
    RenderedCandidate,
    figure_variants,
    measure_candidate,
    select_candidate,
)
from services.generation_core.authoring.author_slide import STYLE_REFERENCE
from services.authored_admission_capture import capture_once
from utils.slide_capture import render_html_to_png_with_metrics
from utils.slide_capture_result import RenderCapture

LegacyRenderResult = tuple[bytes, Mapping[str, Any] | None]
RenderResult = RenderCapture | LegacyRenderResult
RenderCallable = Callable[[str], Awaitable[RenderResult]]


def resolve_render_result(
    requested_html: str, result: RenderResult,
) -> tuple[str, bytes, Mapping[str, Any] | None]:
    """Expose the exact captured document while retaining legacy test adapters."""

    if isinstance(result, RenderCapture):
        if result.prepared_html is None:
            raise RuntimeError("authored figure capture preparation unavailable")
        return result.prepared_html, result.png, result.browser_metrics
    png, metrics = result
    return requested_html, png, metrics


async def default_render(html: str) -> RenderResult:
    """Adapt the capture entry point to a plain ``(png, metrics)`` pair.

    Candidate variants differ by document, so each is a distinct capture; the
    owner is here for the variant a caller offers twice, and for the counters.
    """
    capture = await capture_once(html, capture=render_html_to_png_with_metrics)
    return capture


@dataclass(frozen=True)
class FigureBestOfResult:
    """The winning render plus what it cost to find it."""

    html: str
    png: bytes
    browser_metrics: Mapping[str, Any] | None
    selection: FigureSelection | None
    extra_renders: int
    baseline_render_ms: float
    added_render_ms: float

    @property
    def changed(self) -> bool:
        return self.selection is not None and self.selection.improved

    def receipt(self) -> dict[str, Any]:
        """The fields the quality receipt and the timing report read."""
        payload: dict[str, Any] = {
            "extra_renders": self.extra_renders,
            "baseline_render_ms": round(self.baseline_render_ms, 1),
            "added_render_ms": round(self.added_render_ms, 1),
        }
        if self.selection is not None:
            payload.update(self.selection.to_dict())
        return payload


async def render_best_figure_candidate(
    html: str,
    *,
    reference_mode: str = STYLE_REFERENCE,
    max_candidates: int = 3,
    strict_structural_off_canvas: bool = False,
    render: RenderCallable | None = None,
) -> FigureBestOfResult:
    """Render the candidate set for one slide and return the selected render.

    A slide without an archetype figure renders once and returns immediately,
    so the caller pays nothing for the slides this stage cannot help.
    """
    render_one = render or default_render
    variants = figure_variants(html, max_candidates=max_candidates)

    started = time.monotonic()
    baseline_html, baseline_png, baseline_metrics = resolve_render_result(
        variants[0].html, await render_one(variants[0].html)
    )
    baseline_render_ms = (time.monotonic() - started) * 1000.0
    if len(variants) == 1:
        return FigureBestOfResult(
            html=baseline_html,
            png=baseline_png,
            browser_metrics=baseline_metrics,
            selection=None,
            extra_renders=0,
            baseline_render_ms=baseline_render_ms,
            added_render_ms=0.0,
        )

    def report_for(candidate_html: str, metrics: Mapping[str, Any] | None):
        return inspect_authored_dom(
            candidate_html,
            metrics,
            reference_mode=reference_mode,
            strict_structural_off_canvas=strict_structural_off_canvas,
        )

    rendered = [
        RenderedCandidate(
            variant=variants[0],
            metrics=measure_candidate(baseline_metrics),
            report=report_for(baseline_html, baseline_metrics),
        )
    ]
    payloads: list[tuple[str, bytes, Mapping[str, Any] | None]] = [
        (baseline_html, baseline_png, baseline_metrics)
    ]
    extra_started = time.monotonic()
    for variant in variants[1:]:
        prepared_html, png, metrics = resolve_render_result(
            variant.html, await render_one(variant.html)
        )
        payloads.append((prepared_html, png, metrics))
        rendered.append(
            RenderedCandidate(
                variant=variant,
                metrics=measure_candidate(metrics),
                report=report_for(prepared_html, metrics),
            )
        )
    added_render_ms = (time.monotonic() - extra_started) * 1000.0

    selection = select_candidate(rendered)
    chosen_html, chosen_png, chosen_metrics = payloads[selection.chosen_index]
    return FigureBestOfResult(
        html=chosen_html,
        png=chosen_png,
        browser_metrics=chosen_metrics,
        selection=selection,
        extra_renders=len(variants) - 1,
        baseline_render_ms=baseline_render_ms,
        added_render_ms=added_render_ms,
    )


__all__ = [
    "FigureBestOfResult",
    "RenderCallable",
    "RenderResult",
    "default_render",
    "render_best_figure_candidate",
    "resolve_render_result",
]
