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
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from services.authored_composition_policy import blocking_safety_issues
from services.composer.contracts import Measurement
from services.composer.plan import ComposerSlidePlan
from utils.authored_dom_quality import STYLE_REFERENCE, DomQualityReport, inspect_authored_dom

DEFAULT_RENDER_TIMEOUT_SECONDS = 60


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

    del plan
    # Lazy: slide_capture pulls aiohttp and the renderer runtime at import time.
    from utils.slide_capture import render_html_to_png_with_metrics

    capture = await render_html_to_png_with_metrics(html, timeout=render_timeout_seconds)
    return measurement_from_capture(
        html,
        capture.browser_metrics,
        capture.png,
        strict_structural_off_canvas=strict_structural_off_canvas,
    )


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
    "ChromiumMeasurer",
    "issue_summaries",
    "measure_slide",
    "measurement_from_capture",
]
