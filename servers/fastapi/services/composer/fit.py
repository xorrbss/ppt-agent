"""The fit ladder: estimate, render, measure, escalate.

``fit_slide`` walks ``fit_ladder.ladder_steps`` for one slide. Before a step
is rendered the estimator may reject it ("estimated over"), which skips the
browser. The first estimate-accepted step is rendered and measured; a clean
measurement (no blocking issue, telemetry available, no missing binding)
ends the ladder. Otherwise the next step is tried until the terminal rung.

Rung 5 always terminates, in at most three renders: its entry is source
geometry at page_scale 1.0; when blocked, the components the browser blamed
shrink their text to 0.90; when still blocked, the whole page scales to
0.90. That page_scale 0.90 render is accepted regardless and the residual
blocking issues are recorded in the receipt. Geometry never raises; only a
final render without browser telemetry raises ``ComposerMeasurementError``.

The render budget ``max_renders`` bounds the walk. The rung 5 entry
(page_scale 1.0) is always attempted once, budget or not, because it is the
whole point of the terminal rung: the source page already laid this content
out once, so that render is the cheapest chance at acceptance. Once the
budget is spent, any further terminal step (box shrink or partial page scale)
is skipped and the ladder jumps straight to the terminal page_scale 0.90 for
one more render.

Within a rung with more than one step (rung 2's type_scale steps, rung 4's
source_factor steps), a step whose blocking issues are entirely about the
title band or cover stack ends the rung early: shrinking body text or content
cannot fix a title problem, so the remaining steps of that rung are skipped
and the walk moves straight to the next rung (see ``fit_ladder.
title_only_blocking`` and ``step_changes_title``).

The estimator (``estimate.py``) is conservative by construction and can
reject every rung 0-4 step without ever calling the browser. That must never
be the sole reason a slide lands in source geometry: if no render has
happened by the time the walk falls off the end of rung 0-4, one real render
of the last template step (rung 3's floor, or rung 4's last source factor
when the plan carries sizes) is forced before the terminal rung, so Chromium
-- not the estimate -- gets the final say on whether the template ever fit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from services.composer.contracts import Estimator, Measurement, Measurer, Renderer
from services.composer.fit_ladder import (
    BOX_TEXT_SCALE_STEPS,
    TERMINAL_PAGE_SCALE,
    TERMINAL_PAGE_SCALES,
    blamed_components,
    ladder_steps,
    step_changes_title,
    step_label,
    title_only_blocking,
)
from services.composer.plan import (
    ComposerDeckPlan,
    ComposerSlidePlan,
    ComposerSlideResult,
    ComposerTokens,
    FitParameters,
    FitReceipt,
)

DEFAULT_MAX_RENDERS = 10


class ComposerMeasurementError(RuntimeError):
    """The final render produced no usable browser telemetry."""


def accepted(measurement: Measurement) -> bool:
    return (
        measurement.telemetry_available
        and not measurement.blocking_issues
        and not measurement.missing_bindings
    )


def rejection_reason(measurement: Measurement) -> str:
    parts: list[str] = []
    if not measurement.telemetry_available:
        parts.append("telemetry unavailable")
    parts.extend(measurement.blocking_issues)
    if measurement.missing_bindings:
        parts.append("missing bindings: " + ", ".join(measurement.missing_bindings))
    return "; ".join(parts) or "rejected"


@dataclass
class _Walk:
    """Mutable state of one ladder walk; the receipt is built from it."""

    plan: ComposerSlidePlan
    tokens: ComposerTokens
    deck: ComposerDeckPlan
    render: Renderer
    measure: Measurer
    estimate: Estimator | None
    max_renders: int
    renders: int = 0
    trail: list[str] = field(default_factory=list)

    @property
    def budget_spent(self) -> bool:
        return self.renders >= self.max_renders

    async def attempt(self, params: FitParameters) -> tuple[str, Measurement]:
        html = self.render(self.plan, self.tokens, params, self.deck)
        measurement = await self.measure(html, self.plan)
        self.renders += 1
        return html, measurement

    def estimated_over(self, params: FitParameters) -> bool:
        if self.estimate is None or params.source_geometry:
            return False
        if self.estimate(self.plan, self.tokens, params, self.deck):
            return False
        self.trail.append(f"{step_label(params)}: estimated over")
        return True

    def result(self, html: str, params: FitParameters, measurement: Measurement) -> ComposerSlideResult:
        receipt = FitReceipt(
            slide_index=self.plan.slide_index,
            rung=params.rung,
            parameters=params,
            renders=self.renders,
            estimate_used=self.estimate is not None,
            blocking_issues=list(measurement.blocking_issues),
            trail=list(self.trail),
        )
        return ComposerSlideResult(
            slide_index=self.plan.slide_index,
            html=html,
            receipt=receipt,
            metrics=dict(measurement.metrics),
            png_path=None,
        )


def _is_terminal(params: FitParameters) -> bool:
    return params.page_scale <= TERMINAL_PAGE_SCALE + 1e-9


def _next_terminal_step(
    walk: _Walk,
    params: FitParameters,
    measurement: Measurement,
    box_steps: list[float],
    page_scales: list[float],
    box_text_scale: dict[str, float],
) -> FitParameters:
    if box_steps:
        scale = box_steps.pop(0)
        for component_id in blamed_components(walk.plan, measurement.blocking_issues):
            box_text_scale[component_id] = scale
        return params.model_copy(update={"box_text_scale": dict(box_text_scale)})
    return params.model_copy(update={"page_scale": page_scales.pop(0)})


async def _terminal_rung(walk: _Walk, entry: FitParameters) -> ComposerSlideResult:
    params = entry
    box_steps = list(BOX_TEXT_SCALE_STEPS)
    page_scales = list(TERMINAL_PAGE_SCALES)
    box_text_scale: dict[str, float] = {}
    first = True
    while True:
        if not first and not _is_terminal(params) and walk.budget_spent:
            walk.trail.append(
                f"render budget {walk.max_renders} spent before {step_label(params)}: "
                f"jumping to rung 5 page_scale {TERMINAL_PAGE_SCALE:.2f}"
            )
            params = params.model_copy(update={"page_scale": TERMINAL_PAGE_SCALE})
        first = False
        html, measurement = await walk.attempt(params)
        if accepted(measurement):
            return walk.result(html, params, measurement)
        if _is_terminal(params):
            if not measurement.telemetry_available:
                raise ComposerMeasurementError(
                    f"slide {walk.plan.slide_index}: browser telemetry unavailable on the final render "
                    f"({step_label(params)}, {walk.renders} renders)"
                )
            walk.trail.append(
                f"{step_label(params)}: accepted with {len(measurement.blocking_issues)} residual "
                f"blocking issue(s): {rejection_reason(measurement)}"
            )
            return walk.result(html, params, measurement)
        walk.trail.append(f"{step_label(params)}: {rejection_reason(measurement)}")
        params = _next_terminal_step(walk, params, measurement, box_steps, page_scales, box_text_scale)


async def fit_slide(
    plan: ComposerSlidePlan,
    tokens: ComposerTokens,
    deck: ComposerDeckPlan,
    *,
    render: Renderer,
    measure: Measurer,
    estimate: Estimator | None,
    max_renders: int = DEFAULT_MAX_RENDERS,
) -> ComposerSlideResult:
    """Fit one slide: walk the ladder, render at least once, always return a result."""

    if max_renders < 1:
        raise ValueError("max_renders must be at least 1")
    walk = _Walk(plan, tokens, deck, render, measure, estimate, max_renders)
    steps = list(ladder_steps(plan, deck))
    entry = steps[-1]
    body_steps = steps[:-1]
    index = 0
    while index < len(body_steps):
        if walk.budget_spent:
            break
        params = body_steps[index]
        if walk.estimated_over(params):
            index += 1
            continue
        html, measurement = await walk.attempt(params)
        if accepted(measurement):
            return walk.result(html, params, measurement)
        walk.trail.append(f"{step_label(params)}: {rejection_reason(measurement)}")
        if not step_changes_title(params) and title_only_blocking(plan, measurement.blocking_issues):
            rung = params.rung
            skip_to = index + 1
            while skip_to < len(body_steps) and body_steps[skip_to].rung == rung:
                skip_to += 1
            if skip_to > index + 1:
                walk.trail.append(
                    f"{step_label(params)}: title-only blocking issue, skipping the rest of rung {int(rung)}"
                )
            index = skip_to
            continue
        index += 1
    if walk.renders == 0 and body_steps:
        # The estimator rejected every rung 0-4 step without ever calling the
        # browser. Estimates are conservative by design (see estimate.py) and
        # must not be the sole reason a slide is set in source geometry: force
        # one real render of the last template step (the rung 3 floor, or
        # rung 4's closest-to-1.0 source factor when the plan carries sizes)
        # and let Chromium decide before falling through to rung 5.
        params = body_steps[-1]
        walk.trail.append(
            f"estimate rejected every step through {step_label(params)}: forcing one template render"
        )
        html, measurement = await walk.attempt(params)
        if accepted(measurement):
            return walk.result(html, params, measurement)
        walk.trail.append(f"{step_label(params)}: {rejection_reason(measurement)}")
    return await _terminal_rung(walk, entry)


__all__ = [
    "DEFAULT_MAX_RENDERS",
    "ComposerMeasurementError",
    "accepted",
    "fit_slide",
    "ladder_steps",
    "rejection_reason",
    "step_label",
]
