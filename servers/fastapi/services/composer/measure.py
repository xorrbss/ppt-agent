"""Chromium measurement wrapper and the blocking-defect predicate.

``measure_slide`` renders one slide with the existing instrumentation
(``utils.slide_capture.render_html_to_png_with_metrics``), classifies the
browser telemetry with ``inspect_authored_dom`` in ``style_reference`` mode
and keeps only the publication-safety categories that
``services.authored_composition_policy.blocking_safety_issues`` names
(overflow, clipping, off-canvas, overlap). Typography and contrast remain
design signals and never block the ladder.

``missing_bindings`` is left ``None``: the source readback assertion is the
pipeline's job. The Chromium runtime is imported lazily so this module (and
the ladder) import without a browser present; unit tests pass their own
``Measurer``.

``content_area_fill`` reads the same telemetry from the other side: not
"did anything overflow" but "how much of the content area was used", which is
what the ladder's grow step needs to decide that a page is sparse.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from services.authored_composition_policy import blocking_safety_issues
from services.composer.canvas import declared_canvas_dimensions
from services.composer.contracts import Measurement
from services.composer.plan import ComposerSlidePlan
from utils.authored_dom_quality import (
    STYLE_REFERENCE,
    DomQualityReport,
    inspect_authored_dom,
    normalize_browser_metrics,
)

DEFAULT_RENDER_TIMEOUT_SECONDS = 60

#: A browser box may land a fraction of a pixel outside its container from
#: sub-pixel rounding; boxes are matched to the content area within this.
EDGE_TOLERANCE_PX = 1.0


def content_area_fill(metrics: Mapping[str, Any] | None) -> float | None:
    """Share of the content area the rendered content actually occupies.

    The content section is located by geometry, never by QID string:
    ``render_css.stylesheet`` gives exactly three boxes the full canvas width
    at x=0 -- ``.cx-title-band`` (flush to the top), ``.cx-content`` and
    ``.cx-footer-band`` (flush to the bottom) -- so the one full-width box
    touching neither canvas edge is the content area, and it is the *rendered*
    one, so a title band that grew because its title wrapped is already
    accounted for. Everything else the renderer emits sits inside the section's
    horizontal padding and is narrower.

    Returns the used height over the section height, or ``None`` when the
    telemetry does not show exactly one such box: a ``cover`` slide has no
    content section, and rung 5 lays the page out with absolute boxes instead.
    Only boxes wholly inside the section count toward the used height, so this
    is meaningful only for a measurement that already has no blocking issue --
    which is the only place the ladder asks for it.
    """

    normalized = normalize_browser_metrics(metrics if isinstance(metrics, Mapping) else None)
    elements = normalized["elements"]
    width = float(normalized["viewport"]["width"])
    height = float(normalized["viewport"]["height"])
    if not elements or width <= 0 or height <= 0:
        return None
    sections = [
        item
        for item in elements
        if abs(float(item["bbox"]["x"])) <= EDGE_TOLERANCE_PX
        and abs(float(item["bbox"]["width"]) - width) <= EDGE_TOLERANCE_PX
        and float(item["bbox"]["y"]) > EDGE_TOLERANCE_PX
        and float(item["bbox"]["y"]) + float(item["bbox"]["height"]) < height - EDGE_TOLERANCE_PX
    ]
    if len(sections) != 1:
        return None
    section = sections[0]
    top = float(section["bbox"]["y"])
    bottom = top + float(section["bbox"]["height"])
    if bottom - top <= 0:
        return None
    used = top
    for item in elements:
        if item is section:
            continue
        box = item["bbox"]
        item_top, item_height = float(box["y"]), float(box["height"])
        if item_height <= 0 or float(box["width"]) <= 0:
            continue
        if item_top < top - EDGE_TOLERANCE_PX or item_top + item_height > bottom + EDGE_TOLERANCE_PX:
            continue
        used = max(used, item_top + item_height)
    return (used - top) / (bottom - top)



def issue_summaries(report: DomQualityReport) -> tuple[str, ...]:
    """``"category: detail (qid, qid)"`` for every blocking safety issue of the report."""

    summaries: list[str] = []
    for issue in blocking_safety_issues(report):
        qids = ", ".join(issue.qids)
        summaries.append(f"{issue.category}: {issue.detail} ({qids})" if qids else f"{issue.category}: {issue.detail}")
    return tuple(summaries)


def measurement_from_capture(
    html: str,
    metrics: Mapping[str, Any] | None,
    png: bytes | None,
    *,
    strict_structural_off_canvas: bool = False,
) -> Measurement:
    """Classify one capture. Pure: no browser call, so tests can feed synthetic telemetry."""

    report = inspect_authored_dom(
        html,
        metrics,
        reference_mode=STYLE_REFERENCE,
        strict_structural_off_canvas=strict_structural_off_canvas,
    )
    telemetry_available = report.telemetry_status == "available"
    notes = () if telemetry_available else (f"telemetry {report.telemetry_status}",)
    return Measurement(
        metrics=dict(metrics) if isinstance(metrics, Mapping) else {},
        png=png,
        blocking_issues=issue_summaries(report),
        missing_bindings=None,
        telemetry_available=telemetry_available,
        notes=notes,
    )


async def measure_slide(
    html: str,
    plan: ComposerSlidePlan,
    *,
    render_timeout_seconds: int = DEFAULT_RENDER_TIMEOUT_SECONDS,
    strict_structural_off_canvas: bool = False,
) -> Measurement:
    """Render ``html`` in Chromium and classify the telemetry.

    ``plan`` is part of the ``Measurer`` signature for measurers that verify
    bindings; this one measures geometry only and leaves the readback to the
    pipeline.
    """

    from services.composer.source_topology import reconcile_source_image_overlaps
    # Lazy: slide_capture pulls aiohttp and the renderer runtime at import time.
    from utils.slide_capture import render_html_to_png_with_metrics

    canvas_width, canvas_height = declared_canvas_dimensions(html)
    capture = await render_html_to_png_with_metrics(html, timeout=render_timeout_seconds, width_px=canvas_width, height_px=canvas_height)
    measured = measurement_from_capture(
        html,
        capture.browser_metrics,
        capture.png,
        strict_structural_off_canvas=strict_structural_off_canvas,
    )
    return reconcile_source_image_overlaps(html, plan, measured)


class ChromiumMeasurer:
    """Production ``Measurer``: one Chromium capture per call."""

    def __init__(
        self,
        *,
        render_timeout_seconds: int = DEFAULT_RENDER_TIMEOUT_SECONDS,
        strict_structural_off_canvas: bool = False,
    ) -> None:
        self.render_timeout_seconds = render_timeout_seconds
        self.strict_structural_off_canvas = strict_structural_off_canvas

    async def __call__(self, html: str, plan: ComposerSlidePlan) -> Measurement:
        return await measure_slide(
            html,
            plan,
            render_timeout_seconds=self.render_timeout_seconds,
            strict_structural_off_canvas=self.strict_structural_off_canvas,
        )


__all__ = [
    "DEFAULT_RENDER_TIMEOUT_SECONDS",
    "EDGE_TOLERANCE_PX",
    "ChromiumMeasurer",
    "content_area_fill",
    "issue_summaries",
    "measure_slide",
    "measurement_from_capture",
]
