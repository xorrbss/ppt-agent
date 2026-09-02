"""Stage 4 quality loop: candidate rendering, selection wiring, and the critic."""

import asyncio

import pytest

from services.authored_figure_best_of import default_render, render_best_figure_candidate
from services.authored_quality_loop import run_authored_quality_loop, should_critique
from tests.unit.test_authored_figure_variants import FIGURE_HTML, QUIET_HTML
from utils.authored_dom_quality import build_compact_dom_map, ensure_authored_qids
from utils.authored_figure_variants import find_figure, set_emphasis
from utils.llm_calls.author_design_critique import (
    DesignCorrection,
    DesignCritique,
    DesignRubricScore,
)


def _metrics_for(html, *, overflow=0.0, shift=0.0):
    """Synthetic telemetry that covers exactly the slide's edit targets.

    Full coverage is what makes ``inspect_authored_dom`` report available
    telemetry, which is in turn what lets a variant be eligible at all.
    """
    enriched = ensure_authored_qids(html)
    elements = []
    for index, node in enumerate(build_compact_dom_map(enriched)):
        x = 80.0 + shift + (index % 4) * 280.0
        y = 80.0 + (index // 4) * 150.0
        elements.append(
            {
                "qid": node.qid,
                "parent_qid": "",
                "tag": node.tag,
                "text": node.text,
                "has_owned_text": bool(node.text),
                "bbox": {"x": x, "y": y, "width": 200.0, "height": 80.0},
                "client_width": 200.0,
                "client_height": 80.0,
                "scroll_width": 200.0 + (overflow if index == 0 else 0.0),
                "scroll_height": 80.0,
            }
        )
    return {"width": 1280, "height": 720, "elements": elements}


class _Renderer:
    """Records every render and answers with per-document telemetry."""

    def __init__(self, overflow_for=()):
        self.calls = []
        self._overflow_for = tuple(overflow_for)

    async def __call__(self, html):
        self.calls.append(html)
        overflow = 240.0 if html in self._overflow_for else 0.0
        return b"png:" + str(len(self.calls)).encode(), _metrics_for(
            html, overflow=overflow
        )


def _enable(monkeypatch, *, critique=False, candidates=3):
    monkeypatch.setenv("AUTHORED_QUALITY_LOOP_ENABLED", "true")
    monkeypatch.setenv("AUTHORED_QUALITY_LOOP_CANDIDATES", str(candidates))
    monkeypatch.setenv(
        "AUTHORED_DESIGN_CRITIQUE_ENABLED", "true" if critique else "false"
    )


def test_default_render_goes_through_the_shared_capture_entry_point(monkeypatch):
    from services import authored_figure_best_of

    seen = []

    class _Capture:
        png = b"png"
        browser_metrics = {"width": 1280, "height": 720, "elements": []}

    async def fake_capture(html):
        seen.append(html)
        return _Capture()

    monkeypatch.setattr(
        authored_figure_best_of, "render_html_to_png_with_metrics", fake_capture
    )

    png, metrics = asyncio.run(default_render(FIGURE_HTML))

    # One entry point means one Chromium session is reused across candidates.
    assert seen == [FIGURE_HTML]
    assert png == b"png"
    assert metrics["width"] == 1280


def test_best_of_renders_every_candidate_once_and_reports_the_added_time():
    renderer = _Renderer()

    result = asyncio.run(
        render_best_figure_candidate(FIGURE_HTML, max_candidates=3, render=renderer)
    )

    assert len(renderer.calls) == 3
    assert renderer.calls[0] == FIGURE_HTML
    assert result.extra_renders == 2
    receipt = result.receipt()
    assert receipt["extra_renders"] == 2
    assert receipt["added_render_ms"] >= 0.0
    assert receipt["baseline_render_ms"] >= 0.0
    assert len(receipt["candidates"]) == 3


def test_a_slide_without_a_figure_renders_exactly_once():
    renderer = _Renderer()

    result = asyncio.run(
        render_best_figure_candidate(QUIET_HTML, max_candidates=3, render=renderer)
    )

    assert len(renderer.calls) == 1
    assert result.extra_renders == 0
    assert result.added_render_ms == 0.0
    assert result.selection is None
    assert result.html == QUIET_HTML


def test_best_of_keeps_the_variant_that_measures_better():
    renderer = _Renderer(overflow_for=(FIGURE_HTML,))

    result = asyncio.run(
        render_best_figure_candidate(FIGURE_HTML, max_candidates=3, render=renderer)
    )

    assert result.changed is True
    assert result.html != FIGURE_HTML
    assert result.selection.chosen.variant.variant_id == "density-tighter"
    assert result.png == b"png:2"


def test_the_loop_does_nothing_at_all_while_it_is_off(monkeypatch):
    monkeypatch.delenv("AUTHORED_QUALITY_LOOP_ENABLED", raising=False)
    renderer = _Renderer()

    outcome = asyncio.run(run_authored_quality_loop(FIGURE_HTML, render=renderer))

    assert outcome.ran is False
    assert outcome.changed is False
    assert outcome.html == FIGURE_HTML
    assert renderer.calls == []


def test_the_loop_selects_a_variant_and_records_its_receipt(monkeypatch):
    _enable(monkeypatch)
    renderer = _Renderer(overflow_for=(FIGURE_HTML,))

    outcome = asyncio.run(run_authored_quality_loop(FIGURE_HTML, render=renderer))

    assert outcome.ran is True
    assert outcome.changed is True
    assert outcome.receipt["best_of"]["variant_chosen"] == "density-tighter"
    assert outcome.receipt["best_of"]["density"] == "n5"
    assert outcome.receipt["added_render_ms"] >= 0.0


def test_the_candidate_count_knob_bounds_the_extra_renders(monkeypatch):
    _enable(monkeypatch, candidates=2)
    renderer = _Renderer()

    asyncio.run(run_authored_quality_loop(FIGURE_HTML, render=renderer))

    assert len(renderer.calls) == 2


@pytest.mark.parametrize(
    ("has_figure", "dom_flagged", "expected"),
    [(True, False, True), (False, True, True), (True, True, True), (False, False, False)],
)
def test_the_critic_is_scoped_to_figure_and_flagged_slides(has_figure, dom_flagged, expected):
    assert should_critique(has_figure=has_figure, dom_flagged=dom_flagged) is expected


def _critique(corrections, *, needs_fix=True):
    async def call(_png, **_kwargs):
        return DesignCritique(
            needs_fix=needs_fix,
            scores=[DesignRubricScore(axis="balance", score=3, detail="uneven")],
            corrections=list(corrections),
        )

    return call


def test_the_critic_never_runs_for_a_quiet_unflagged_page(monkeypatch):
    _enable(monkeypatch, critique=True)
    called = False

    async def critique(_png, **_kwargs):
        nonlocal called
        called = True
        return None

    asyncio.run(
        run_authored_quality_loop(
            QUIET_HTML, render=_Renderer(), critique_call=critique
        )
    )

    assert called is False


def test_a_closed_correction_is_applied_and_re_rendered(monkeypatch):
    _enable(monkeypatch, critique=True)
    renderer = _Renderer()

    outcome = asyncio.run(
        run_authored_quality_loop(
            FIGURE_HTML,
            render=renderer,
            critique_call=_critique([DesignCorrection(kind="move_emphasis", node_index=2)]),
        )
    )

    assert outcome.receipt["critique_result"] == "applied"
    assert outcome.receipt["critic_score"] == 3.0
    assert find_figure(outcome.html).emphasis_index == 2
    # Three candidates plus one correction render.
    assert len(renderer.calls) == 4


def test_a_correction_the_figure_cannot_prove_is_refused_not_approximated(monkeypatch):
    _enable(monkeypatch, critique=True)
    renderer = _Renderer()

    outcome = asyncio.run(
        run_authored_quality_loop(
            FIGURE_HTML,
            render=renderer,
            critique_call=_critique(
                [
                    DesignCorrection(kind="shorten_label", node_index=0, text="새 문구"),
                    DesignCorrection(kind="move_emphasis", node_index=9),
                ]
            ),
        )
    )

    assert outcome.receipt["critique_applied"] == []
    assert len(outcome.receipt["critique_refused"]) == 2
    assert outcome.html == FIGURE_HTML
    assert len(renderer.calls) == 3


def test_a_critique_that_asks_for_nothing_leaves_the_slide_alone(monkeypatch):
    _enable(monkeypatch, critique=True)
    renderer = _Renderer()

    outcome = asyncio.run(
        run_authored_quality_loop(
            FIGURE_HTML, render=renderer, critique_call=_critique([], needs_fix=False)
        )
    )

    assert outcome.html == FIGURE_HTML
    assert "critique_applied" not in outcome.receipt
    assert len(renderer.calls) == 3


def test_an_unavailable_critic_leaves_the_selected_render_in_place(monkeypatch):
    _enable(monkeypatch, critique=True)

    async def unavailable(_png, **_kwargs):
        return None

    outcome = asyncio.run(
        run_authored_quality_loop(
            FIGURE_HTML, render=_Renderer(), critique_call=unavailable
        )
    )

    assert outcome.receipt["critique"] == "unavailable"
    assert outcome.html == FIGURE_HTML


def test_a_correction_that_regresses_the_dom_gate_is_rolled_back(monkeypatch):
    _enable(monkeypatch, critique=True)
    corrected = set_emphasis(FIGURE_HTML, 2)

    class _BrokenAfterCorrection(_Renderer):
        async def __call__(self, html):
            self.calls.append(html)
            overflow = 400.0 if html == corrected else 0.0
            return b"png", _metrics_for(html, overflow=overflow)

    outcome = asyncio.run(
        run_authored_quality_loop(
            FIGURE_HTML,
            render=_BrokenAfterCorrection(),
            critique_call=_critique([DesignCorrection(kind="move_emphasis", node_index=2)]),
        )
    )

    assert outcome.receipt["critique_result"] == "rejected_dom_regression"
    assert outcome.html == FIGURE_HTML


def test_a_correction_that_only_measures_worse_is_rolled_back(monkeypatch):
    _enable(monkeypatch, critique=True)
    corrected = set_emphasis(FIGURE_HTML, 2)

    class _UnbalancedAfterCorrection(_Renderer):
        async def __call__(self, html):
            self.calls.append(html)
            shift = 120.0 if html == corrected else 0.0
            return b"png", _metrics_for(html, shift=shift)

    outcome = asyncio.run(
        run_authored_quality_loop(
            FIGURE_HTML,
            render=_UnbalancedAfterCorrection(),
            critique_call=_critique([DesignCorrection(kind="move_emphasis", node_index=2)]),
        )
    )

    assert outcome.receipt["critique_result"] == "rejected_worse_geometry"
    assert outcome.html == FIGURE_HTML


async def _stage(**kwargs):
    """Run the adapter and return only its chosen document.

    The adapter returns the document AND the loop's receipt, because the
    measurement stage has no other channel to a slide authored in a parallel
    child task. Tests about the document unwrap it here; ``_stage_receipt``
    below is for the tests about the receipt itself.
    """
    from services.authored_quality_loop_stage import apply_slide_quality_loop

    html, _receipt = await apply_slide_quality_loop(**kwargs)
    return html


async def _stage_receipt(**kwargs):
    """Run the adapter and return only the receipt it reports."""
    from services.authored_quality_loop_stage import apply_slide_quality_loop

    _html, receipt = await apply_slide_quality_loop(**kwargs)
    return receipt


class _Checkpoint:
    def __init__(self, html, status="completed"):
        self.illustrated_html = html
        self.render_status = status


def test_the_pipeline_adapter_is_inert_while_the_loop_is_off(monkeypatch):
    monkeypatch.delenv("AUTHORED_QUALITY_LOOP_ENABLED", raising=False)

    html = asyncio.run(_stage(html=FIGURE_HTML, reference_mode="style_reference"))

    assert html == FIGURE_HTML


def test_exact_reconstruction_never_enters_the_loop(monkeypatch):
    _enable(monkeypatch)

    html = asyncio.run(
        _stage(html=FIGURE_HTML, reference_mode="exact_reconstruction")
    )

    assert html == FIGURE_HTML


def test_a_resumed_attempt_adopts_its_earlier_winner_without_re_rendering(monkeypatch):
    _enable(monkeypatch)
    from services import authored_quality_loop_stage

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("a resumed slide must not render its candidates again")

    monkeypatch.setattr(
        authored_quality_loop_stage, "run_authored_quality_loop", unexpected
    )
    winner = set_emphasis(FIGURE_HTML, 2)

    html = asyncio.run(
        _stage(
            html=FIGURE_HTML,
            checkpoint=_Checkpoint(winner),
            reference_mode="style_reference",
        )
    )

    assert html == winner


def test_a_different_document_does_not_count_as_an_earlier_winner(monkeypatch):
    _enable(monkeypatch)
    from services import authored_quality_loop_stage

    calls = []

    async def loop(html, **_kwargs):
        calls.append(html)

        class _Outcome:
            receipt = {}

        _Outcome.html = html
        return _Outcome()

    monkeypatch.setattr(authored_quality_loop_stage, "run_authored_quality_loop", loop)

    asyncio.run(
        _stage(
            html=FIGURE_HTML,
            checkpoint=_Checkpoint(QUIET_HTML),
            reference_mode="style_reference",
        )
    )

    assert calls == [FIGURE_HTML]


def test_a_failing_loop_keeps_the_authored_slide(monkeypatch):
    _enable(monkeypatch)
    from services import authored_quality_loop_stage

    async def broken(*_args, **_kwargs):
        raise RuntimeError("chromium is unavailable")

    monkeypatch.setattr(
        authored_quality_loop_stage, "run_authored_quality_loop", broken
    )

    html = asyncio.run(_stage(html=FIGURE_HTML, reference_mode="style_reference"))

    assert html == FIGURE_HTML


def test_the_adapter_reports_the_receipt_the_measurement_stage_persists(monkeypatch):
    """The loop's choice reaches the receipt writer, not only a log line.

    Slides are authored in parallel child tasks, so the receipt has to travel
    back with the slide's own result. The keys asserted here are exactly the
    ones ``build_slide_quality_records`` reads.
    """
    _enable(monkeypatch)
    from services import authored_quality_loop_stage
    from services.authored_slide_quality_receipt import build_slide_quality_records

    chosen = set_emphasis(FIGURE_HTML, 2)

    async def loop(_html, **_kwargs):
        class _Outcome:
            html = chosen
            receipt = {"best_of": {"variant_chosen": "density-tighter"}, "critic_score": 4.0}

        return _Outcome()

    monkeypatch.setattr(authored_quality_loop_stage, "run_authored_quality_loop", loop)

    receipt = asyncio.run(
        _stage_receipt(html=FIGURE_HTML, reference_mode="style_reference")
    )

    assert receipt["best_of"]["variant_chosen"] == "density-tighter"
    records = build_slide_quality_records(
        slide_count=1,
        variants={0: receipt["best_of"]["variant_chosen"]},
        critic_scores={0: receipt["critic_score"]},
    )
    assert records[0].variant_chosen == "density-tighter"
    assert records[0].critic_score == 4.0


def test_a_slide_the_loop_skipped_reports_no_receipt(monkeypatch):
    """An off loop reports an empty receipt, which records as absent not zero."""
    monkeypatch.delenv("AUTHORED_QUALITY_LOOP_ENABLED", raising=False)

    assert (
        asyncio.run(_stage_receipt(html=FIGURE_HTML, reference_mode="style_reference"))
        == {}
    )
