"""Render-and-gate step of the authored fit replay (design 5.2).

One slide is rendered in headless Chromium and judged by the pipeline's own
``capacity_violations`` plus ``inspect_authored_dom``; a slide that misses is
then re-rendered at each scale step through ``scale_authored_text`` until one
passes. Kept out of the CLI facade so the gate contract and the run state have
one home and the facade stays a thin entry point.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scripts import authored_fit_replay_buckets as buckets
from scripts import authored_fit_replay_inputs as inputs_lib


@dataclass
class GateResult:
    """One render plus both gate verdicts for one document."""

    violations: tuple[str, ...]
    issues: list[dict[str, Any]]
    fit_summary: dict[str, Any] | None
    telemetry_status: str
    blocking_issue_count: int
    render_millis: int
    png: bytes = b""

    @property
    def flagged(self) -> bool:
        # The design's verdict: a capacity violation, or anything the DOM
        # report raises, including a non-geometry finding.
        return (
            bool(self.violations)
            or self.telemetry_status != "available"
            or bool(self.issues)
        )

    @property
    def geometry_blocked(self) -> bool:
        return bool(self.violations) or self.blocking_issue_count > 0


@dataclass
class ReplayState:
    """Mutable run state: where output goes and what has been measured."""

    out_dir: Path
    png_dir: Path
    scales: tuple[float, ...]
    rows: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    contexts: list[inputs_lib.JobContext] = field(default_factory=list)
    parity: dict[str, Any] | None = None

    def skip(self, job_short: str, slide_index: int, reason: str, detail: str) -> None:
        self.skipped.append(
            {
                "job_short": job_short,
                "slide_index": slide_index,
                "reason": reason,
                "detail": detail,
            }
        )

    def write_png(self, job_short: str, index: int, scale: float, png: bytes) -> str:
        name = f"{job_short}-{index:02d}-{scale:.2f}.png"
        (self.png_dir / name).write_bytes(png)
        return f"png/{name}"


async def evaluate(html: str, context: inputs_lib.JobContext, index: int) -> GateResult:
    """Render once and run both gates exactly as the pipeline runs them."""

    from services.authored_composition_policy import (
        blocking_safety_issues,
        capacity_violations,
    )
    from utils.authored_dom_quality import inspect_authored_dom
    from utils.slide_capture import render_html_to_png_with_metrics

    capacity = context.capacity_plan.at(index)
    started = time.monotonic()
    capture = await render_html_to_png_with_metrics(html)
    elapsed = int((time.monotonic() - started) * 1000)
    metrics = capture.browser_metrics
    report = inspect_authored_dom(
        html,
        metrics,
        reference_mode=context.reference_mode,
        strict_structural_off_canvas=context.strict_structural_off_canvas,
    )
    return GateResult(
        violations=tuple(capacity_violations(html, metrics, capacity)),
        issues=[issue.to_dict() for issue in report.issues],
        fit_summary=dict(report.fit_summary) if report.fit_summary else None,
        telemetry_status=report.telemetry_status,
        blocking_issue_count=len(blocking_safety_issues(report)),
        render_millis=elapsed,
        png=capture.png,
    )


def _base_row(
    slide: inputs_lib.SlideInput, capacity: Any, html: str, base: GateResult, png: str
) -> dict[str, Any]:
    context = slide.job
    return {
        "job_id": str(context.job_id),
        "job_short": context.short,
        "job_state": context.state,
        "presentation_id": str(context.presentation_id),
        "slide_index": slide.slide_index,
        "role": slide.role,
        "archetype": slide.archetype,
        "source": slide.source,
        "code_version": context.code_version,
        "profile_id": context.profile.profile_id,
        "reference_mode": context.reference_mode,
        "prepared_html_changed": html != slide.html,
        "replay_deviations": list(context.deviations),
        "capacity": {
            "role": capacity.role,
            "title_max_chars": capacity.title_max_chars,
            "body_max_chars": capacity.body_max_chars,
            "max_visible_text_chars": capacity.max_visible_text_chars,
            "max_images": capacity.max_images,
            "max_qids": capacity.max_qids,
            "min_font_px": capacity.min_font_px,
        },
        "violations": list(base.violations),
        "issues": base.issues,
        "fit_summary": base.fit_summary,
        "telemetry_status": base.telemetry_status,
        "flagged": base.flagged,
        "blocking_issue_count": base.blocking_issue_count,
        "geometry_blocked": base.geometry_blocked,
        "render_millis": base.render_millis,
        "png": png,
        "worst_overflow_kind": buckets.worst_geometry_kind(base.issues),
        "blocking_categories": buckets.blocking_categories(base.issues),
        "scale_candidates": [],
        "first_passing_scale": None,
    }


async def _walk_ladder(
    state: ReplayState, slide: inputs_lib.SlideInput, html: str, capacity: Any, row: dict[str, Any]
) -> list[buckets.ScaleOutcome]:
    from utils.authored_text_scale import scale_authored_text

    outcomes: list[buckets.ScaleOutcome] = []
    for scale in state.scales:
        scaled = scale_authored_text(html, scale, floor_px=float(capacity.min_font_px))
        result = await evaluate(scaled.html, slide.job, slide.slide_index)
        outcome = buckets.ScaleOutcome(
            scale=scale,
            passed=not result.flagged,
            scaled_declarations=scaled.scaled_declarations,
            floored_declarations=scaled.floored_declarations,
            unscaled_declarations=scaled.unscaled_declarations,
        )
        outcomes.append(outcome)
        candidate: dict[str, Any] = {
            "scale": scale,
            "passed": outcome.passed,
            "violations": list(result.violations),
            "issue_count": len(result.issues),
            "blocking_issue_count": result.blocking_issue_count,
            "fit_summary": result.fit_summary,
            "scaled_declarations": scaled.scaled_declarations,
            "floored_declarations": scaled.floored_declarations,
            "unscaled_declarations": scaled.unscaled_declarations,
            "skipped_figure_declarations": scaled.skipped_figure_declarations,
            "floor_bound": buckets.is_floor_bound(outcome),
            "render_millis": result.render_millis,
            "png": None,
        }
        if outcome.passed:
            candidate["png"] = state.write_png(
                slide.job.short, slide.slide_index, scale, result.png
            )
            row["first_passing_scale"] = scale
        row["scale_candidates"].append(candidate)
        if outcome.passed:
            break
    return outcomes


async def replay_slide(
    state: ReplayState, slide: inputs_lib.SlideInput
) -> dict[str, Any]:
    context = slide.job
    capacity = context.capacity_plan.at(slide.slide_index)
    html = inputs_lib.prepare_for_render(
        slide.html, minimum_font_px=capacity.min_font_px
    )
    base = await evaluate(html, context, slide.slide_index)
    png = state.write_png(context.short, slide.slide_index, 1.0, base.png)
    row = _base_row(slide, capacity, html, base, png)
    outcomes: list[buckets.ScaleOutcome] = []
    if base.flagged and base.geometry_blocked:
        outcomes = await _walk_ladder(state, slide, html, capacity, row)
    row["bucket"] = buckets.classify_bucket(
        flagged=base.flagged,
        in_ladder_population=base.geometry_blocked,
        worst_kind=row["worst_overflow_kind"],
        outcomes=outcomes,
    )
    return row




__all__ = ["GateResult", "ReplayState", "evaluate", "replay_slide"]
