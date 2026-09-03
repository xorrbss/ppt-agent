"""Unit tests for the Composer fit ladder, estimate and measurement wrapper.

No Chromium: the renderer, measurer and estimator are scripted fakes.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import pytest

from services.composer.contracts import Measurement
from services.composer.estimate import (
    GAP_PX,
    MAX_GROUPS_PER_ROW,
    SAFETY_MARGIN,
    _row_grouped_height_px,
    comparison_height_px,
    content_area,
    estimate_fit,
    estimated_content_height,
    paragraph_font_px,
    paragraphs_height_px,
    process_height_px,
    process_per_row,
    template_font_px,
)
from services.composer.fit import ComposerMeasurementError, fit_slide
from services.composer.fit_ladder import (
    TERMINAL_PAGE_SCALES,
    TYPE_SCALE_STEPS,
    archetype_columns,
    blamed_components,
    ladder_steps,
    source_factor_steps,
    step_changes_title,
    step_label,
    title_only_blocking,
)
from services.composer.measure import issue_summaries, measurement_from_capture
from services.composer.plan import (
    BODY_FLOOR_PX,
    PX_PER_PT,
    TEMPLATE_TYPE_SCALE_PX,
    ComposerComponent,
    ComposerDeckPlan,
    ComposerSlidePlan,
    ComposerTokens,
    FitParameters,
    Paragraph,
    SourceRunRef,
    TextRun,
)
from services.composer.render import render_slide
from services.composer.render_css import px
from services.source_content.source_stamp_dom import deterministic_source_qid
from utils.authored_dom_quality import STYLE_REFERENCE, DomQualityIssue, DomQualityReport

KOREAN_SENTENCE = "회사의 연간 매출은 전년 대비 12퍼센트 성장했으며 신규 고객 확보가 주요 동력이었습니다. "
ENGLISH_SENTENCE = (
    "Annual revenue grew twelve percent over the prior year, driven mainly by new customer acquisition and renewals. "
)


def _tokens() -> ComposerTokens:
    return ComposerTokens(
        background="#FFFFFF",
        surface="#F4F6F8",
        text_primary="#111827",
        text_secondary="#4B5563",
        accent="#0F62FE",
        accent_soft="#D0E2FF",
        rule="#E5E7EB",
    )


def _paragraph(text: str, block: str, index: int, *, size_pt: float | None = None, bullet: bool = False) -> Paragraph:
    binding = f"{block}:run:{index}"
    return Paragraph(
        text=text,
        runs=[TextRun(text=text, size_pt=size_pt)],
        bullet=bullet,
        source=SourceRunRef(block_id=block, run_start=index, run_end=index, binding_id=binding),
    )


def _text_component(
    component_id: str,
    paragraphs: list[Paragraph],
    *,
    kind: str = "paragraph",
    size_pt: float | None = None,
) -> ComposerComponent:
    return ComposerComponent(
        component_id=component_id,
        kind=kind,
        semantic_role="body",
        paragraphs=paragraphs,
        source_size_pt=size_pt,
    )


def _plan(
    chars: int,
    *,
    size_pt: float | None = None,
    components: int = 1,
    sentence: str = KOREAN_SENTENCE,
) -> ComposerSlidePlan:
    per_component = max(1, chars // components)
    made: list[ComposerComponent] = []
    for index in range(components):
        text = (sentence * (per_component // len(sentence) + 1))[:per_component]
        paragraph = _paragraph(text, f"b{index}", 0, size_pt=size_pt)
        made.append(_text_component(f"body-{index}", [paragraph], size_pt=size_pt))
    title = ComposerComponent(
        component_id="title",
        kind="title",
        semantic_role="title",
        paragraphs=[_paragraph("분기 실적 요약", "t0", 0, size_pt=28.0 if size_pt else None)],
    )
    return ComposerSlidePlan(
        slide_index=3,
        source_unit_id="unit-3",
        archetype="text",
        title=title,
        components=made,
        text_chars=chars,
    )


def _deck(plan: ComposerSlidePlan) -> ComposerDeckPlan:
    return ComposerDeckPlan(slides=[plan], tokens=_tokens())


def fake_render(plan: ComposerSlidePlan, tokens: ComposerTokens, params: FitParameters, deck: ComposerDeckPlan) -> str:
    del tokens, deck
    encoded = json.dumps(params.model_dump(), sort_keys=True)
    return f"<html data-slide=\"{plan.slide_index}\" data-params='{encoded}'></html>"


def params_of(html: str) -> FitParameters:
    payload = html.split("data-params='", 1)[1].rsplit("'", 1)[0]
    return FitParameters.model_validate(json.loads(payload))


@dataclass
class ScriptedMeasurer:
    """Blocking issues for the first ``blocked`` calls, then clean."""

    blocked: int
    issues: tuple[str, ...] = ("overflow: text exceeds its box (source-block-deadbeef00000000)",)
    telemetry: list[bool] = field(default_factory=list)
    calls: list[FitParameters] = field(default_factory=list)

    async def __call__(self, html: str, plan: ComposerSlidePlan) -> Measurement:
        del plan
        index = len(self.calls)
        self.calls.append(params_of(html))
        available = self.telemetry[index] if index < len(self.telemetry) else True
        blocking = self.issues if index < self.blocked else ()
        return Measurement(metrics={"call": index}, png=b"png", blocking_issues=blocking, telemetry_available=available)


@dataclass
class ScriptedEstimator:
    verdicts: list[bool]
    calls: list[FitParameters] = field(default_factory=list)

    def __call__(self, plan, tokens, params: FitParameters, deck) -> bool:
        del plan, tokens, deck
        self.calls.append(params)
        index = len(self.calls) - 1
        return self.verdicts[index] if index < len(self.verdicts) else True


def run(coro):
    return asyncio.run(coro)


# --- ladder_steps -------------------------------------------------------------


def test_ladder_steps_sequence_with_source_sizes():
    plan = _plan(900, size_pt=10.0)
    steps = list(ladder_steps(plan, _deck(plan)))
    rungs = [s.rung for s in steps]
    assert rungs == [0, 1] + [2] * 3 + [3] * 3 + [4] * 4 + [5]
    assert steps[0].model_dump() == FitParameters(rung=0, columns=2).model_dump()
    assert (steps[1].density, steps[1].columns, steps[1].chrome_level) == (1, 3, 0)
    assert [s.type_scale for s in steps if s.rung == 2] == list(TYPE_SCALE_STEPS)
    assert all(s.chrome_level == 0 and s.density == 1 for s in steps if s.rung == 2)
    assert [s.chrome_level for s in steps if s.rung == 3] == [1, 2, 3]
    assert all(s.type_scale == 0.75 for s in steps if s.rung >= 3)
    rung4 = [s for s in steps if s.rung == 4]
    assert all(s.source_relative and s.chrome_level == 3 for s in rung4)
    start = BODY_FLOOR_PX / (10.0 * PX_PER_PT)
    expected_factors = [start, 1 + (start - 1) * 2 / 3, 1 + (start - 1) / 3, 1.0]
    assert [s.source_factor for s in rung4] == pytest.approx(expected_factors, abs=1e-3)
    terminal = steps[-1]
    assert terminal.source_geometry and terminal.chrome_level == 3 and terminal.page_scale == 1.0
    assert terminal.box_text_scale == {}


def test_ladder_skips_rung_4_without_source_sizes():
    plan = _plan(300)
    steps = list(ladder_steps(plan, _deck(plan)))
    assert 4 not in {s.rung for s in steps}
    assert [s.rung for s in steps][-2:] == [3, 5]
    assert steps[0].columns == 1 and steps[1].columns == 2


def test_archetype_columns_and_source_factor_steps():
    assert archetype_columns(_plan(600)) == 1
    assert archetype_columns(_plan(601)) == 2
    assert archetype_columns(_plan(1400)) == 2
    assert archetype_columns(_plan(1401)) == 3
    process = _plan(2000).model_copy(update={"archetype": "process"})
    assert archetype_columns(process) == 1
    assert source_factor_steps(18.0 * PX_PER_PT) == (1.0,)
    assert source_factor_steps(10.0 * PX_PER_PT) == (1.125, 1.0833, 1.0417, 1.0)


def test_step_labels():
    assert step_label(FitParameters(rung=2, type_scale=0.85)) == "rung 2 type_scale 0.85"
    assert step_label(FitParameters(rung=3, chrome_level=2)) == "rung 3 chrome 2"
    assert step_label(FitParameters(rung=5, source_geometry=True, page_scale=0.98)) == "rung 5 page_scale 0.98"
    assert (
        step_label(FitParameters(rung=5, source_geometry=True, box_text_scale={"a": 0.95}))
        == "rung 5 box_text_scale 0.95"
    )


def test_title_only_blocking_and_step_changes_title():
    plan = _plan(300)
    title_qid = deterministic_source_qid(plan.title.paragraphs[0].source.binding_id)
    assert title_only_blocking(plan, ()) is False
    assert title_only_blocking(
        plan, ("off_canvas: element extends outside the slide canvas (cx-title-stack)",)
    ) is True
    assert title_only_blocking(plan, (f"overflow: text exceeds its box ({title_qid})",)) is True
    assert title_only_blocking(plan, ("off_canvas: element extends outside the slide canvas (cx-cover-stack)",)) is True
    assert title_only_blocking(plan, ("overflow: text exceeds its box (body-0)",)) is False
    # a mix of a title issue and a body issue is not title-only.
    assert (
        title_only_blocking(
            plan,
            (
                f"overflow: text exceeds its box ({title_qid})",
                "overflow: text exceeds its box (body-0)",
            ),
        )
        is False
    )
    # rung 2's type_scale never reaches the title (render_title only applies
    # type_scale at rung >= 3); rung 3's chrome_level does, via the band width.
    assert step_changes_title(FitParameters(rung=0, columns=1)) is False
    assert step_changes_title(FitParameters(rung=2, type_scale=0.90)) is False
    assert step_changes_title(FitParameters(rung=3, chrome_level=1, type_scale=0.75)) is True
    assert step_changes_title(FitParameters(rung=4, source_relative=True, source_factor=1.05)) is False
    assert step_changes_title(FitParameters(rung=5, source_geometry=True)) is False


def test_title_only_issue_skips_the_rest_of_rung_2_but_not_rung_3():
    """A step whose blocking issues are all about the title band cannot be fixed by
    the next body-shrinking step of the same rung, so the ladder should move on to
    the next rung instead of trying the remaining rung 2 type_scale steps."""

    plan = _plan(300)
    deck = _deck(plan)
    calls: list[FitParameters] = []

    async def measure(html: str, _plan: ComposerSlidePlan) -> Measurement:
        params = params_of(html)
        calls.append(params)
        if params.rung == 2:
            return Measurement(
                metrics={},
                png=b"x",
                blocking_issues=("off_canvas: element extends outside the slide canvas (cx-title-stack)",),
            )
        if params.rung == 3 and params.chrome_level == 1:
            return Measurement(metrics={}, png=b"x", blocking_issues=())
        return Measurement(
            metrics={}, png=b"x", blocking_issues=("overflow: text exceeds its box (body-0)",)
        )

    result = run(fit_slide(plan, _tokens(), deck, render=fake_render, measure=measure, estimate=None))
    assert [p.rung for p in calls] == [0, 1, 2, 3]
    assert calls[2].type_scale == TYPE_SCALE_STEPS[0]
    assert result.receipt.rung == 3 and result.receipt.parameters.chrome_level == 1
    assert result.receipt.renders == 4
    assert any("title-only blocking issue, skipping the rest of rung 2" in line for line in result.receipt.trail)


# --- fit_slide ----------------------------------------------------------------


def test_estimate_first_skips_render_and_stops_at_first_clean_measurement():
    plan = _plan(900, size_pt=10.0)
    deck = _deck(plan)
    estimator = ScriptedEstimator([False, False, True])
    measurer = ScriptedMeasurer(blocked=1)
    result = run(fit_slide(plan, _tokens(), deck, render=fake_render, measure=measurer, estimate=estimator))
    # rung 0 and rung 1 estimated over; rung 2 @0.90 rendered and blocked; rung 2 @0.82 clean.
    assert [p.rung for p in measurer.calls] == [2, 2]
    assert [p.type_scale for p in measurer.calls] == [0.90, 0.82]
    assert result.receipt.renders == 2
    assert result.receipt.rung == 2
    assert result.receipt.parameters.type_scale == 0.82
    assert result.receipt.estimate_used is True
    assert result.receipt.blocking_issues == []
    assert result.receipt.trail == [
        "rung 0 columns 2: estimated over",
        "rung 1 density 1 columns 3: estimated over",
        "rung 2 type_scale 0.90: overflow: text exceeds its box (source-block-deadbeef00000000)",
    ]
    assert result.metrics == {"call": 1}
    assert params_of(result.html).type_scale == 0.82


def test_estimator_rejecting_everything_forces_one_template_render_before_rung_5():
    """Estimates are conservative by design and must never be the sole reason a
    slide lands in source geometry: when every rung 0-4 step is estimated over,
    one real render of the last template step is forced before rung 5, and
    Chromium -- not the estimate -- decides whether it actually fits."""

    plan = _plan(900, size_pt=10.0)
    deck = _deck(plan)
    estimator = ScriptedEstimator([False] * 12)
    measurer = ScriptedMeasurer(blocked=0)
    result = run(fit_slide(plan, _tokens(), deck, render=fake_render, measure=measurer, estimate=estimator))
    assert result.receipt.renders == 1
    assert result.receipt.rung != 5
    assert result.receipt.rung == 4
    forced = measurer.calls[0]
    assert not forced.source_geometry and forced.source_relative and forced.source_factor == 1.0
    assert result.receipt.trail[-1] == (
        "estimate rejected every step through rung 4 source_factor 1.0000: forcing one template render"
    )
    assert all(line.endswith(": estimated over") for line in result.receipt.trail[:-1])
    assert len(result.receipt.trail) == 13
    # the estimator is never asked about a source-geometry step
    assert not any(p.source_geometry for p in estimator.calls)


def test_estimate_rejecting_everything_falls_through_the_forced_render_to_rung_5():
    """When even the forced template render is rejected, the walk still ends at
    the terminal rung -- the estimator is never asked about a source-geometry
    step, whether the forced render fits or not."""

    plan = _plan(900, size_pt=10.0)
    deck = _deck(plan)
    estimator = ScriptedEstimator([False] * 20)
    measurer = ScriptedMeasurer(blocked=100)
    result = run(fit_slide(plan, _tokens(), deck, render=fake_render, measure=measurer, estimate=estimator))
    assert result.receipt.rung == 5
    # call 0 is the forced rung-4 template render, not the rung-5 entry.
    assert measurer.calls[0].rung == 4 and not measurer.calls[0].source_geometry
    assert measurer.calls[1].source_geometry and measurer.calls[1].page_scale == 1.0
    assert not any(p.source_geometry for p in estimator.calls)


def test_without_estimator_every_rung_renders_until_clean():
    plan = _plan(300)
    deck = _deck(plan)
    measurer = ScriptedMeasurer(blocked=3)
    result = run(fit_slide(plan, _tokens(), deck, render=fake_render, measure=measurer, estimate=None))
    assert [(p.rung, p.type_scale) for p in measurer.calls] == [(0, 1.0), (1, 1.0), (2, 0.90), (2, 0.82)]
    assert result.receipt.estimate_used is False
    assert result.receipt.renders == 4
    assert result.receipt.rung == 2


def test_max_renders_jumps_to_terminal_page_scale():
    """Budget exhaustion never skips the rung 5 entry (page_scale 1.0): that
    render always happens once, and only the *next* terminal step (box shrink
    or partial page scale) is skipped straight to page_scale 0.90."""

    plan = _plan(900, size_pt=10.0)
    deck = _deck(plan)
    measurer = ScriptedMeasurer(blocked=100)
    result = run(
        fit_slide(plan, _tokens(), deck, render=fake_render, measure=measurer, estimate=None, max_renders=3)
    )
    assert [p.rung for p in measurer.calls] == [0, 1, 2, 5, 5]
    assert [p.page_scale for p in measurer.calls[-2:]] == [1.0, 0.90]
    final = measurer.calls[-1]
    assert final.source_geometry and final.page_scale == 0.90 and final.chrome_level == 3
    assert result.receipt.renders == 5
    assert result.receipt.rung == 5
    assert result.receipt.parameters.page_scale == 0.90
    assert result.receipt.blocking_issues == list(measurer.issues)
    assert any("render budget 3 spent" in line and "page_scale 0.90" in line for line in result.receipt.trail)
    assert result.receipt.trail[-1].startswith("rung 5 page_scale 0.90: accepted with 1 residual blocking issue(s)")


def test_terminal_rung_shrinks_blamed_boxes_then_scales_page():
    """Rung 5 spends at most three renders, however badly a slide is blocked:
    the page_scale 1.0 entry, one box-text shrink to 0.90 for the components
    the browser blamed, then the floor page_scale 0.90 -- never the old finer
    0.95/0.90 box steps x 0.98..0.90 page steps ladder (up to eight renders)."""

    plan = _plan(900, size_pt=10.0, components=2)
    deck = _deck(plan)
    blamed_qid = deterministic_source_qid(plan.components[1].paragraphs[0].source.binding_id)
    measurer = ScriptedMeasurer(blocked=100, issues=(f"overflow: text exceeds its box ({blamed_qid})",))
    result = run(
        fit_slide(plan, _tokens(), deck, render=fake_render, measure=measurer, estimate=ScriptedEstimator([False] * 20))
    )
    calls = measurer.calls
    # call 0 is the forced rung-4 template render (the estimator rejected every
    # rung 0-4 step); calls[1:] are the rung 5 walk.
    assert calls[0].rung == 4 and not calls[0].source_geometry
    assert all(p.rung == 5 and p.source_geometry for p in calls[1:])
    rung5_calls = calls[1:]
    assert len(rung5_calls) <= 3
    assert [p.page_scale for p in rung5_calls] == [1.0, 1.0, *TERMINAL_PAGE_SCALES]
    assert rung5_calls[0].box_text_scale == {}
    assert rung5_calls[1].box_text_scale == {"body-1": 0.90}
    assert rung5_calls[2].box_text_scale == {"body-1": 0.90}
    assert result.receipt.renders == 1 + len(rung5_calls)
    assert result.receipt.renders <= 1 + 3
    assert result.receipt.parameters.page_scale == 0.90
    assert result.receipt.blocking_issues == list(measurer.issues)
    # 12 estimated-over lines, then the forced rung-4 render's own rejection,
    # then: entry rejected, box 0.90 rejected, page_scale 0.90 accepted.
    assert result.receipt.trail[12] == (
        "estimate rejected every step through rung 4 source_factor 1.0000: forcing one template render"
    )
    assert result.receipt.trail[13].startswith("rung 4 source_factor 1.0000: overflow")
    rung5_trail = result.receipt.trail[14:]
    assert rung5_trail[0].startswith("rung 5 page_scale 1.00: overflow")
    assert rung5_trail[1].startswith("rung 5 box_text_scale 0.90: overflow")
    assert result.receipt.trail[-1].startswith("rung 5 page_scale 0.90: accepted with 1 residual")


def test_terminal_rung_never_spends_more_than_three_renders():
    """However many blocking issues rung 5 keeps hitting, the walk must not
    keep spending renders past the page_scale 0.90 floor: that render is
    always accepted regardless of its own residual issues."""

    plan = _plan(300)
    deck = _deck(plan)
    measurer = ScriptedMeasurer(blocked=1000)
    result = run(fit_slide(plan, _tokens(), deck, render=fake_render, measure=measurer, estimate=None))
    assert result.receipt.rung == 5
    rung5_calls = [p for p in measurer.calls if p.rung == 5]
    assert len(rung5_calls) == 3
    assert [p.page_scale for p in rung5_calls] == [1.0, 1.0, 0.90]
    assert result.receipt.parameters.page_scale == 0.90


def test_unnamed_issues_blame_every_text_component():
    plan = _plan(900, components=2)
    assert blamed_components(plan, ("overlap: visible element boxes substantially overlap",)) == (
        "title",
        "body-0",
        "body-1",
    )
    assert blamed_components(plan, ("overflow: box (body-0)",)) == ("body-0",)
    assert blamed_components(plan, ("overflow: box (b1:run:0)",)) == ("body-1",)


def test_measurement_error_only_on_final_render_without_telemetry():
    plan = _plan(300)
    deck = _deck(plan)
    # Telemetry missing on an intermediate render is a rejection, not an error.
    measurer = ScriptedMeasurer(blocked=0, telemetry=[False, True])
    result = run(fit_slide(plan, _tokens(), deck, render=fake_render, measure=measurer, estimate=None))
    assert result.receipt.renders == 2
    assert result.receipt.trail == ["rung 0 columns 1: telemetry unavailable"]

    failing = ScriptedMeasurer(blocked=100, telemetry=[False] * 50)
    with pytest.raises(ComposerMeasurementError):
        run(fit_slide(plan, _tokens(), deck, render=fake_render, measure=failing, estimate=None, max_renders=2))
    assert failing.calls[-1].page_scale == 0.90


def test_missing_bindings_reject_a_step():
    plan = _plan(300)
    deck = _deck(plan)
    verdicts = iter([("b0:run:0",), ()])

    async def measure(html: str, _plan: ComposerSlidePlan) -> Measurement:
        del html
        return Measurement(metrics={}, png=None, missing_bindings=next(verdicts))

    result = run(fit_slide(plan, _tokens(), deck, render=fake_render, measure=measure, estimate=None))
    assert result.receipt.renders == 2
    assert result.receipt.trail == ["rung 0 columns 1: missing bindings: b0:run:0"]


def test_fit_slide_rejects_zero_render_budget():
    plan = _plan(300)
    with pytest.raises(ValueError):
        run(
            fit_slide(
                plan,
                _tokens(),
                _deck(plan),
                render=fake_render,
                measure=ScriptedMeasurer(0),
                estimate=None,
                max_renders=0,
            )
        )


# --- estimate_fit -------------------------------------------------------------


def test_estimate_small_paragraph_fits_at_rung_0():
    plan = _plan(120)
    deck = _deck(plan)
    params = FitParameters(rung=0, columns=1)
    height = estimated_content_height(plan, params, deck)
    assert 0 < height < content_area(params)[1] / SAFETY_MARGIN
    assert estimate_fit(plan, _tokens(), params, deck) is True


@pytest.mark.parametrize(
    ("chars", "sentence"),
    [(3000, ENGLISH_SENTENCE), (2400, KOREAN_SENTENCE)],
)
def test_estimate_dense_page_needs_the_floor_and_compact_chrome(chars: int, sentence: str):
    plan = _plan(chars, components=6, sentence=sentence)
    deck = _deck(plan)
    rung0 = FitParameters(rung=0, columns=3)
    assert estimate_fit(plan, _tokens(), rung0, deck) is False
    floor = FitParameters(rung=3, chrome_level=3, density=1, type_scale=0.75, columns=3)
    assert estimate_fit(plan, _tokens(), floor, deck) is True
    assert estimated_content_height(plan, floor, deck) < estimated_content_height(plan, rung0, deck)


def test_estimate_full_em_korean_page_exceeds_the_template_floor():
    # 3,000 CJK glyphs at 15 px advance one em each: their line boxes alone outgrow
    # the 1232x648 content area, which is exactly why rungs 4 and 5 exist.
    plan = _plan(3000, components=6, sentence=KOREAN_SENTENCE)
    deck = _deck(plan)
    floor = FitParameters(rung=3, chrome_level=3, density=1, type_scale=0.75, columns=3)
    assert estimate_fit(plan, _tokens(), floor, deck) is False


def test_estimate_source_geometry_is_left_to_the_browser():
    plan = _plan(30000)
    params = FitParameters(rung=5, chrome_level=3, source_geometry=True)
    assert estimate_fit(plan, _tokens(), params, _deck(plan)) is True


def _labelled_group(prefix: str, texts: list[str]) -> list[Paragraph]:
    return [_paragraph(text, f"{prefix}{index}", 0) for index, text in enumerate(texts)]


def test_kicker_and_comparison_groups_fit_at_rung_3_floor():
    """Regression for a comparison slide shaped like ``final-43`` plans.json
    slide 6 (``source-slide:7``): a short "02"-style kicker paragraph, a kpi
    stat, a closing note, four images and a three-column comparison block
    (9/5/9 short rows) at the dense two-column layout.

    The estimator used to reject every rung 0-3 step for this shape: it
    summed all four images at their full natural height (up to 0.35 of the
    content area *each*, more than the whole budget before any text was
    measured) and stacked the comparison block at the *full* content width on
    top of the flow text instead of the width the two-column render actually
    gives it (see ``estimate.py``'s module docstring). Chromium accepts this
    shape at chrome levels well above the rung-3 floor, so the estimate must
    not still be rejecting it there.
    """

    kicker = _text_component(
        "kicker",
        [_paragraph("02", "k0", 0, size_pt=10.5), _paragraph("제안 개요", "k0", 1, size_pt=10.5)],
    )
    stat = _text_component("stat", [_paragraph("52%", "s0", 0, size_pt=24.0)], kind="kpi")
    note_text = "※ 공수 · 기간 수치는 제안 기준선에 맞춘 예시이며, 실제 성과는 고객 기준선 실측에 따라 달라집니다."
    note = _text_component("note", [_paragraph(note_text, "n0", 0, size_pt=9.0)])
    comparison = ComposerComponent(
        component_id="cmp",
        kind="comparison",
        semantic_role="body",
        group_headers=[
            _paragraph("제안 배경", "h0", 0),
            _paragraph("제안 목적", "h1", 0),
            None,
        ],
        groups=[
            _labelled_group(
                "g0-",
                [
                    "AI 로 코드 생성은 빨라졌지만, SI 비용은 생성 이후에서 발생합니다.",
                    "제안 범위",
                    "요구사항 정의",
                    "설계 · 디자인 · 코드 · 테스트",
                    "모듈 임포트",
                    "CI/CD 검증",
                    "승인",
                    "배포",
                    "운영까지 DevAX 18개 제품과 공통 기반을 적용합니다.",
                ],
            ),
            _labelled_group(
                "g1-",
                [
                    "재사용",
                    "적응",
                    "최소 생성 원칙과 요구사항 자동 싱크, 원버튼 배포로 공수를 줄입니다.",
                    "기대 효과",
                    "사례 기준 구축 공수 평균 52% 절감, 100MD 기준 43MD 모델.",
                ],
            ),
            _labelled_group(
                "g2-",
                [
                    "핵심 수치 (제안 기준선 예시)",
                    "구축 공수 절감 · 4개 사례 평균",
                    "100",
                    "43 MD",
                    "조건부 공수 산정 모델",
                    "85%",
                    "AI 자율 수행률 · 사람은 승인 · 예외",
                    "0건",
                    "승인 후 중대 보안 결함 · 성능 회귀 목표",
                ],
            ),
        ],
    )
    images = [
        ComposerComponent(
            component_id=f"img{index}", kind="image", semantic_role="body", asset_id=f"a{index}", aspect_ratio=1.0
        )
        for index in range(4)
    ]
    title = ComposerComponent(
        component_id="title",
        kind="title",
        semantic_role="title",
        paragraphs=[_paragraph("제안 개요 (Executive Summary)", "t0", 0, size_pt=24.0)],
    )
    plan = ComposerSlidePlan(
        slide_index=6,
        source_unit_id="unit-6",
        archetype="comparison",
        title=title,
        components=[kicker, comparison, stat, note],
        images=images,
        text_chars=776,
    )
    deck = _deck(plan)
    floor = FitParameters(rung=3, chrome_level=3, density=1, type_scale=0.75, columns=2)
    assert estimate_fit(plan, _tokens(), floor, deck) is True


def test_estimate_measures_wide_rows_at_full_width_and_flow_runs_separately():
    """``[paragraph, comparison, paragraph]``: the estimate must mirror
    ``render_chrome``'s flow-run/wide-row split -- the comparison measured at
    the *full* content width and added as its own sequential segment, each
    surrounding paragraph measured at its own run's ``column_width`` -- not
    the old single pool bin-packed at one ``column_width`` for everything,
    which squeezed the comparison to a fraction of its real width and
    overestimated its height by roughly the column count."""

    lead = _text_component("lead", [_paragraph("도입부 문단입니다.", "b0", 0)])
    tail = _text_component("tail", [_paragraph("마무리 문단입니다.", "b1", 0)])
    comparison = ComposerComponent(
        component_id="cmp", kind="comparison", semantic_role="body",
        group_headers=[None, None, None], groups=[_group(f"g{i}-", 9) for i in range(3)],
    )
    plan = _plan(0)
    plan.components = [lead, comparison, tail]
    params = FitParameters(rung=0, columns=2)
    deck = _deck(plan)
    area_width, _area_height = content_area(params)
    gap = GAP_PX[int(params.density)]
    column_width = (area_width - gap) / 2

    expected = (
        paragraphs_height_px(lead.paragraphs, lead, params, deck, width_px=column_width)
        + gap
        + comparison_height_px(comparison, params, deck, width_px=area_width)
        + gap
        + paragraphs_height_px(tail.paragraphs, tail, params, deck, width_px=column_width)
    )
    assert estimated_content_height(plan, params, deck) == pytest.approx(expected)

    # The pre-fix model measured the comparison squeezed into one of the two
    # columns; that is strictly taller than the full-width measurement above.
    squeezed = comparison_height_px(comparison, params, deck, width_px=column_width)
    full_width = comparison_height_px(comparison, params, deck, width_px=area_width)
    assert squeezed > full_width


def _group(prefix: str, count: int) -> list[Paragraph]:
    return [_paragraph(ENGLISH_SENTENCE, f"{prefix}{i}", 0) for i in range(count)]


def test_comparison_columns_are_modeled_side_by_side_not_stacked():
    """``render_comparison`` never wraps: all of a comparison's columns share one
    row. The estimate must match that, or it overestimates the height by roughly
    the column count and rejects slides the renderer would have accepted."""

    plan = _plan(0)
    two_columns = ComposerComponent(
        component_id="cmp", kind="comparison", semantic_role="body",
        group_headers=[None, None], groups=[_group("g0-", 4), _group("g1-", 4)],
    )
    # The same eight paragraphs, but as a single column -- what the old model
    # effectively produced by never splitting the row at all.
    single_column = ComposerComponent(
        component_id="cmp", kind="comparison", semantic_role="body",
        group_headers=[None], groups=[_group("g0-", 4) + _group("g1-", 4)],
    )
    params = FitParameters(rung=0, columns=1)
    area_width, area_height = content_area(params)
    side_by_side = comparison_height_px(two_columns, params, _deck(plan), width_px=area_width)
    stacked = comparison_height_px(single_column, params, _deck(plan), width_px=area_width)
    assert side_by_side < stacked
    assert side_by_side * SAFETY_MARGIN <= area_height
    assert stacked * SAFETY_MARGIN > area_height

    plan.components = [two_columns]
    assert estimate_fit(plan, _tokens(), params, _deck(plan)) is True
    plan.components = [single_column]
    assert estimate_fit(plan, _tokens(), params, _deck(plan)) is False


def test_process_steps_wrap_at_four_per_row_like_the_renderer():
    """``render_process`` puts up to ``MAX_GROUPS_PER_ROW`` steps in one row and
    wraps to exactly two rows past it -- never one row per group."""

    plan = _plan(0)
    groups = [_group(f"g{i}-", 2) for i in range(4)]
    four_steps = ComposerComponent(
        component_id="proc", kind="process", semantic_role="body",
        group_headers=[None] * 4, groups=groups,
    )
    single_column = ComposerComponent(
        component_id="proc", kind="process", semantic_role="body",
        group_headers=[None], groups=[sum(groups, [])],
    )
    params = FitParameters(rung=0, columns=1)
    area_width, area_height = content_area(params)
    one_row = process_height_px(four_steps, params, _deck(plan), width_px=area_width)
    stacked = process_height_px(single_column, params, _deck(plan), width_px=area_width)
    assert one_row < stacked
    assert one_row * SAFETY_MARGIN <= area_height
    assert stacked * SAFETY_MARGIN > area_height

    plan.components = [four_steps]
    assert estimate_fit(plan, _tokens(), params, _deck(plan)) is True
    plan.components = [single_column]
    assert estimate_fit(plan, _tokens(), params, _deck(plan)) is False


def test_process_wraps_to_exactly_two_rows_past_max_groups_per_row():
    plan = _plan(0)
    groups = [_group(f"g{i}-", 1) for i in range(MAX_GROUPS_PER_ROW * 2 + 1)]
    component = ComposerComponent(
        component_id="proc", kind="process", semantic_role="body",
        group_headers=[None] * len(groups), groups=groups,
    )
    params = FitParameters(rung=0, columns=1)
    area_width, _area_height = content_area(params)
    two_row_height = process_height_px(component, params, _deck(plan), width_px=area_width)
    # The pre-fix model capped per_row = min(len(groups), MAX_GROUPS_PER_ROW)
    # regardless of count, so 9 groups needed 3 rows of 4; the renderer (and the
    # fixed estimate) only ever wraps process steps to two rows.
    over_wrapped = _row_grouped_height_px(
        component, params, _deck(plan), width_px=area_width, per_row=MAX_GROUPS_PER_ROW
    )
    assert two_row_height < over_wrapped


def test_process_dense_six_long_steps_wraps_two_rows_and_render_agrees():
    """6 steps already always wraps to two rows by ``MAX_GROUPS_PER_ROW`` count
    alone; this exercises that at density 1, with genuinely long step bodies,
    the rendered CSS grid (``render_process``'s ``process_per_row``) and the
    estimate (``estimate_components.process_per_row``) still agree, and that
    dense step text drops to the supporting scale (``render_context.
    size_role`` / ``estimate_components.type_scale_key``'s process override --
    see ``composer-offline-eval`` final2-43 slides 12-15/32)."""

    plan = _plan(0)
    groups = [_group(f"g{i}-", 1) for i in range(6)]
    component = ComposerComponent(
        component_id="proc", kind="process", semantic_role="body",
        group_headers=[None] * len(groups), groups=groups,
    )
    plan.components = [component]
    deck = _deck(plan)
    params = FitParameters(rung=1, density=1, columns=2)
    area_width, _area_height = content_area(params)

    per_row = process_per_row(component, params, deck, width_px=area_width)
    assert per_row == 3  # two rows of three, never one row of six

    html = render_slide(plan, _tokens(), params, deck)
    style = f"grid-template-columns:repeat({per_row},minmax(0,1fr))"
    assert f'class="cx-component cx-process" style="{style}"' in html
    assert f"font-size:{px(TEMPLATE_TYPE_SCALE_PX['supporting'])}" in html
    assert f"font-size:{px(TEMPLATE_TYPE_SCALE_PX['body'])}" not in html


def test_font_sizes_follow_the_parameters():
    plan = _plan(300, size_pt=10.0)
    deck = _deck(plan)
    body = plan.components[0]
    assert template_font_px(body, FitParameters(type_scale=1.0)) == 20.0
    assert template_font_px(body, FitParameters(type_scale=0.75)) == 15.0
    assert template_font_px(body, FitParameters(type_scale=0.5)) == BODY_FLOOR_PX
    relative = FitParameters(rung=4, source_relative=True, source_factor=1.125, type_scale=0.75)
    assert paragraph_font_px(body.paragraphs[0], body, relative, deck) == pytest.approx(15.0)
    unsized = _plan(300).components[0]
    assert paragraph_font_px(unsized.paragraphs[0], unsized, relative, deck) == 15.0


# --- measure ------------------------------------------------------------------


def test_issue_summaries_keep_only_safety_categories():
    report = DomQualityReport(
        STYLE_REFERENCE,
        (
            DomQualityIssue("overflow", "high", "text exceeds its box", ("q1",)),
            DomQualityIssue("contrast", "medium", "low contrast", ("q2",)),
            DomQualityIssue("overlap", "medium", "boxes overlap", ("q3", "q4")),
            DomQualityIssue("font", "medium", "document fonts did not finish loading"),
        ),
        (),
        4,
    )
    assert issue_summaries(report) == (
        "overflow: text exceeds its box (q1)",
        "overlap: boxes overlap (q3, q4)",
    )


def test_measurement_from_capture_classifies_telemetry():
    html = '<html><body><p data-qid="copy">본문</p></body></html>'
    unavailable = measurement_from_capture(html, None, b"png")
    assert unavailable.telemetry_available is False
    assert unavailable.blocking_issues == ()
    assert unavailable.metrics == {} and unavailable.png == b"png"
    assert unavailable.missing_bindings is None
    assert unavailable.notes == ("telemetry unavailable",)

    metrics = {
        "width": 1280,
        "height": 720,
        "fonts_ready": True,
        "elements": [
            {
                "qid": "copy",
                "tag": "p",
                "bbox": {"x": 40, "y": 100, "width": 400, "height": 30},
                "clientWidth": 400,
                "clientHeight": 30,
                "scrollWidth": 400,
                "scrollHeight": 30,
                "fontSize": 20,
                "font_loaded": True,
                "rendered_text": "본문",
                "computed_style": {"color": "#111111", "backgroundColor": "#ffffff", "fontSize": "20px"},
            }
        ],
    }
    available = measurement_from_capture(html, metrics, None)
    assert available.telemetry_available is True
    assert available.blocking_issues == ()
    assert available.metrics["elements"][0]["qid"] == "copy"
