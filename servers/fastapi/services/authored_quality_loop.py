"""Stage 4 quality loop: best-of-N first, then a narrowly scoped design critique.

The loop is off unless ``AUTHORED_QUALITY_LOOP_ENABLED`` is set, and with it off
this module does no work at all -- not even a render -- so an unflagged deck
behaves exactly as it did before the loop existed.

With it on, one figure slide goes through:

1. **Best-of-N** (``services.authored_figure_best_of``): the same authored HTML
   rendered with two or three density/emphasis class combinations, the winner
   chosen from geometry alone.
2. **Design critique** (``utils.llm_calls.author_design_critique``), only when
   the slide carries a figure or the DOM gate flagged the chosen render. This
   is the +80% wall-time trap the experiment measured, so it never runs over a
   whole deck. Its answer is a closed operation set; the corrected slide is
   re-rendered and kept only when it neither regresses the DOM gate nor scores
   worse than what it replaced.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from utils.authored_dom_quality import has_dom_quality_regression, inspect_authored_dom
from utils.authored_figure_variants import (
    apply_operations,
    candidate_score,
    find_figure,
    measure_candidate,
)
from utils.get_env import (
    get_authored_quality_loop_candidates,
    is_authored_design_critique_enabled,
    is_authored_quality_loop_enabled,
)
from utils.llm_calls.author_design_critique import critique_authored_design
from utils.llm_calls.author_slide import STYLE_REFERENCE
from services.authored_figure_best_of import (
    RenderCallable,
    default_render,
    render_best_figure_candidate,
)

#: A corrected render may not score worse than the one it replaces.
CORRECTION_MARGIN = 1e-6


@dataclass(frozen=True)
class QualityLoopOutcome:
    """What the loop produced for one slide, and what it cost."""

    html: str
    png: bytes | None = None
    browser_metrics: Mapping[str, Any] | None = None
    ran: bool = False
    changed: bool = False
    added_render_ms: float = 0.0
    receipt: dict[str, Any] = field(default_factory=dict)


def should_critique(*, has_figure: bool, dom_flagged: bool) -> bool:
    """Critique figure slides and DOM-flagged slides only -- never a whole deck."""
    return bool(has_figure or dom_flagged)


async def _corrected_candidate(
    *,
    html: str,
    png: bytes,
    metrics: Mapping[str, Any] | None,
    reference_mode: str,
    strict_structural_off_canvas: bool,
    slide_context: str | None,
    archetype: str | None,
    density_class: str | None,
    labels: Sequence[str],
    render: RenderCallable,
    critique_call,
) -> tuple[str, bytes, Mapping[str, Any] | None, float, dict[str, Any]]:
    """Ask for a critique, apply its closed operations, keep only an improvement."""
    receipt: dict[str, Any] = {}
    critique = await critique_call(
        png,
        slide_context=slide_context,
        archetype=archetype,
        density_class=density_class,
        labels=labels,
    )
    if critique is None:
        return html, png, metrics, 0.0, {"critique": "unavailable"}
    receipt["critique"] = critique.to_dict()
    receipt["critic_score"] = critique.critic_score
    operations = critique.operations()
    if not critique.needs_fix or not operations:
        return html, png, metrics, 0.0, receipt

    corrected, applied, refused = apply_operations(html, operations)
    receipt["critique_applied"] = [item.to_dict() for item in applied]
    receipt["critique_refused"] = list(refused)
    if not applied or corrected == html:
        return html, png, metrics, 0.0, receipt

    started = time.monotonic()
    corrected_png, corrected_metrics = await render(corrected)
    elapsed = (time.monotonic() - started) * 1000.0

    baseline_report = inspect_authored_dom(
        html,
        metrics,
        reference_mode=reference_mode,
        strict_structural_off_canvas=strict_structural_off_canvas,
    )
    corrected_report = inspect_authored_dom(
        corrected,
        corrected_metrics,
        reference_mode=reference_mode,
        strict_structural_off_canvas=strict_structural_off_canvas,
    )
    if has_dom_quality_regression(baseline_report, corrected_report):
        receipt["critique_result"] = "rejected_dom_regression"
        return html, png, metrics, elapsed, receipt
    if candidate_score(measure_candidate(corrected_metrics)) > candidate_score(
        measure_candidate(metrics)
    ) + CORRECTION_MARGIN:
        receipt["critique_result"] = "rejected_worse_geometry"
        return html, png, metrics, elapsed, receipt
    receipt["critique_result"] = "applied"
    return corrected, corrected_png, corrected_metrics, elapsed, receipt


async def run_authored_quality_loop(
    html: str,
    *,
    reference_mode: str = STYLE_REFERENCE,
    strict_structural_off_canvas: bool = False,
    slide_context: str | None = None,
    archetype: str | None = None,
    labels: Sequence[str] = (),
    settings: Any = None,
    render: RenderCallable | None = None,
    critique_call=None,
) -> QualityLoopOutcome:
    """Run the loop for one slide; returns the slide unchanged when it is off."""
    if not is_authored_quality_loop_enabled(settings):
        return QualityLoopOutcome(html=html)
    render_one = render or default_render
    best = await render_best_figure_candidate(
        html,
        reference_mode=reference_mode,
        max_candidates=get_authored_quality_loop_candidates(settings),
        strict_structural_off_canvas=strict_structural_off_canvas,
        render=render_one,
    )
    receipt: dict[str, Any] = {"best_of": best.receipt()}
    added = best.added_render_ms
    current_html, current_png, current_metrics = (
        best.html,
        best.png,
        best.browser_metrics,
    )

    figure = find_figure(current_html)
    chosen_report = best.selection.chosen.report if best.selection is not None else None
    dom_flagged = bool(chosen_report is not None and chosen_report.needs_attention)
    if is_authored_design_critique_enabled(settings) and should_critique(
        has_figure=figure is not None, dom_flagged=dom_flagged
    ):
        (
            current_html,
            current_png,
            current_metrics,
            critique_ms,
            critique_receipt,
        ) = await _corrected_candidate(
            html=current_html,
            png=current_png,
            metrics=current_metrics,
            reference_mode=reference_mode,
            strict_structural_off_canvas=strict_structural_off_canvas,
            slide_context=slide_context,
            archetype=archetype,
            density_class=figure.density_class if figure is not None else None,
            labels=labels,
            render=render_one,
            critique_call=critique_call or critique_authored_design,
        )
        added += critique_ms
        receipt.update(critique_receipt)

    receipt["added_render_ms"] = round(added, 1)
    return QualityLoopOutcome(
        html=current_html,
        png=current_png,
        browser_metrics=current_metrics,
        ran=True,
        changed=current_html != html,
        added_render_ms=added,
        receipt=receipt,
    )


__all__ = [
    "CORRECTION_MARGIN",
    "QualityLoopOutcome",
    "run_authored_quality_loop",
    "should_critique",
]
