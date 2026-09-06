"""G0 anchor selection, gating and the deck's behaviour when the anchor fails."""

import asyncio
from types import SimpleNamespace

import pytest

from services import authored_presentation_pipeline as pipeline
from services import authored_presentation_service as authored_service
from services import authored_style_anchor_stage as stage
from services import authored_tail_streaming as tail_streaming
from tests.unit import test_authored_tail_streaming as tail_lane
from utils.llm_calls.author_deck import AuthoredPlannedBatchResult
from utils.llm_calls.author_style_anchor import StyleAnchor
from utils.slide_capture_deck_scope import current_render_deck_scope


_STYLED = (
    "<!DOCTYPE html><html><head><style>body{background:#0B1F3A}"
    ".card{border-radius:12px}</style></head><body>"
    "<div class='card' data-qid='card.one'>slide</div></body></html>"
)


def _run(coro):
    return asyncio.run(coro)


def _plan(count: int, roles=None, exempt=()):
    return SimpleNamespace(
        contents=tuple(f"slide {index}" for index in range(count)),
        roles=tuple(roles or (["COVER"] + ["CONTENT"] * (count - 1))),
        header_exempt_slides=frozenset(exempt),
        design_system="ANCHORED DESIGN SYSTEM",
    )


# --- anchor index choice ------------------------------------------------------


def test_anchor_is_the_first_ordinary_content_slide():
    plan = _plan(6, roles=["COVER", "AGENDA", "SECTION", "PROBLEM", "PILLARS", "CLOSING"])

    assert stage.anchor_slide_index(plan) == 3


def test_a_header_exempt_slide_is_never_the_anchor():
    plan = _plan(5, roles=["COVER", "CONTENT", "CONTENT", "CONTENT", "CLOSING"], exempt={1})

    assert stage.anchor_slide_index(plan) == 2


def test_a_roleless_deck_falls_back_to_the_slide_after_the_cover():
    plan = _plan(4, roles=["", "", "", ""])

    assert stage.anchor_slide_index(plan) == 1


def test_a_short_deck_spends_no_anchor_call():
    assert stage.anchor_slide_index(_plan(2)) is None


def test_a_resumed_job_never_re_authors_an_already_complete_anchor():
    plan = _plan(6, roles=["COVER", "CONTENT", "CONTENT", "CONTENT", "CONTENT", "CLOSING"])

    # Slides 1 and 2 are already checkpointed; the anchor moves to the first
    # slide this attempt actually has to author.
    assert stage.anchor_slide_index(plan, [3, 4]) == 3
    # Nothing left to author means no anchor call at all.
    assert stage.anchor_slide_index(plan, []) is None


# --- resolution ---------------------------------------------------------------


def _clean_gate(_html):
    return ()


async def _no_render(_html):
    return None


def test_a_clean_anchor_is_extracted_and_returned():
    authored = []

    async def author(plan, index, **_kwargs):
        authored.append(index)
        return _STYLED

    anchor, html, index = _run(
        stage.resolve_style_anchor(
            _plan(5),
            author=author,
            render=_no_render,
            inspect_blocking=_clean_gate,
            enabled=True,
        )
    )

    assert authored == [1]
    assert index == 1
    assert html == _STYLED
    assert isinstance(anchor, StyleAnchor)
    assert anchor.slide_index == 1
    assert "#0B1F3A" in anchor.prompt_block()


def test_a_defective_anchor_is_remade_once_and_then_used():
    hints = []

    async def author(plan, index, *, prior_failure_hint=None, **_kwargs):
        hints.append(prior_failure_hint)
        return _STYLED

    defects = [SimpleNamespace(category="overflow", message="card leaves the canvas")]
    seen = {"calls": 0}

    def gate(_html):
        seen["calls"] += 1
        return tuple(defects) if seen["calls"] == 1 else ()

    anchor, _html, _index = _run(
        stage.resolve_style_anchor(
            _plan(5),
            author=author,
            render=_no_render,
            inspect_blocking=gate,
            enabled=True,
        )
    )

    assert len(hints) == 2
    assert hints[0] is None
    assert "style anchor" in hints[1]
    assert "overflow" in hints[1]
    assert isinstance(anchor, StyleAnchor)


def test_an_anchor_that_stays_defective_is_dropped_not_raised():
    async def author(plan, index, **_kwargs):
        return _STYLED

    def gate(_html):
        return (SimpleNamespace(category="clipping", message="text is cut"),)

    anchor, html, index = _run(
        stage.resolve_style_anchor(
            _plan(5),
            author=author,
            render=_no_render,
            inspect_blocking=gate,
            enabled=True,
        )
    )

    assert anchor is None
    assert html == _STYLED
    assert index == 1


def test_an_anchor_with_no_call_left_is_used_only_when_it_is_clean():
    calls = []

    async def author(plan, index, **_kwargs):
        calls.append(index)
        return _STYLED

    def gate(_html):
        return (SimpleNamespace(category="overlap", message="two cards collide"),)

    anchor, _html, _index = _run(
        stage.resolve_style_anchor(
            _plan(5),
            author=author,
            render=_no_render,
            inspect_blocking=gate,
            remaining_calls=1,
            enabled=True,
        )
    )

    assert calls == [1]  # no remake: the two-call budget was already spent
    assert anchor is None


def test_an_anchor_author_failure_never_fails_the_deck():
    async def author(plan, index, **_kwargs):
        raise RuntimeError("provider down")

    anchor, html, index = _run(
        stage.resolve_style_anchor(
            _plan(5),
            author=author,
            render=_no_render,
            inspect_blocking=_clean_gate,
            enabled=True,
        )
    )

    assert (anchor, html, index) == (None, None, 1)


def test_a_render_that_cannot_run_skips_the_gate_rather_than_dropping_the_anchor():
    async def author(plan, index, **_kwargs):
        return _STYLED

    async def render(_html):
        raise RuntimeError("no chromium here")

    def gate(_html):
        raise AssertionError("the gate must not run without a render")

    anchor, _html, _index = _run(
        stage.resolve_style_anchor(
            _plan(5),
            author=author,
            render=render,
            inspect_blocking=gate,
            enabled=True,
        )
    )

    assert isinstance(anchor, StyleAnchor)


def test_the_knob_off_authors_no_anchor_at_all():
    async def author(plan, index, **_kwargs):
        raise AssertionError("no anchor call is allowed when the knob is off")

    assert _run(
        stage.resolve_style_anchor(
            _plan(5),
            author=author,
            render=_no_render,
            inspect_blocking=_clean_gate,
            enabled=False,
        )
    ) == (None, None, -1)


def test_accepts_style_anchor_reads_the_callable_signature():
    async def without(plan, index):
        return ""

    async def with_anchor(plan, index, *, style_anchor=None):
        return ""

    async def with_var_keyword(plan, index, **kwargs):
        return ""

    assert stage.accepts_style_anchor(without) is False
    assert stage.accepts_style_anchor(with_anchor) is True
    assert stage.accepts_style_anchor(with_var_keyword) is True


# --- the direct API lane ------------------------------------------------------


def test_direct_lane_anchor_is_authored_once_and_reused_by_the_pool(monkeypatch):
    """The anchor slide is authored before the pool and never authored twice."""

    authored: list[int] = []
    anchor_seen: list[object] = []

    async def fake_author(plan, index, **kwargs):
        authored.append(index)
        anchor_seen.append(kwargs.get("style_anchor"))
        return _STYLED

    async def fake_render(_html):
        return b"png"

    plan = _plan(4)
    monkeypatch.setattr(
        authored_service, "prepare_authored_deck", lambda *args, **kwargs: plan
    )
    monkeypatch.setattr(authored_service, "author_planned_slide", fake_author)
    monkeypatch.setattr(authored_service, "render_html_to_png", fake_render)

    result = _run(
        authored_service._author_illustrate_render_pipeline(
            SimpleNamespace(slides=[SimpleNamespace(content=f"s{i}") for i in range(4)]),
            SimpleNamespace(
                topic="t", language="English", primary="#2563EB", fonts="Arial"
            ),
            SimpleNamespace(id="default", illustration_prompt=None),
        )
    )

    assert len(result.pngs) == 4
    # Four slides, four author calls: the anchor's own call is reused.
    assert sorted(authored) == [0, 1, 2, 3]
    assert authored.count(1) == 1
    # Every call after the anchor carried it.
    assert anchor_seen[0] is None
    assert all(isinstance(seen, StyleAnchor) for seen in anchor_seen[1:])


def test_direct_lane_anchor_failure_leaves_the_deck_authoring_as_before(monkeypatch):
    calls: list[int] = []

    async def fake_author(plan, index, **kwargs):
        calls.append(index)
        if index == 1 and len(calls) == 1:
            raise RuntimeError("anchor call failed")
        assert kwargs.get("style_anchor") is None
        return _STYLED

    async def fake_render(_html):
        return b"png"

    plan = _plan(4)
    monkeypatch.setattr(
        authored_service, "prepare_authored_deck", lambda *args, **kwargs: plan
    )
    monkeypatch.setattr(authored_service, "author_planned_slide", fake_author)
    monkeypatch.setattr(authored_service, "render_html_to_png", fake_render)

    result = _run(
        authored_service._author_illustrate_render_pipeline(
            SimpleNamespace(slides=[SimpleNamespace(content=f"s{i}") for i in range(4)]),
            SimpleNamespace(
                topic="t", language="English", primary="#2563EB", fonts="Arial"
            ),
            SimpleNamespace(id="default", illustration_prompt=None),
        )
    )

    assert len(result.pngs) == 4
    assert calls[0] == 1  # the anchor was attempted first
    assert sorted(calls[1:]) == [0, 1, 2, 3]


def test_the_correction_hint_names_the_defects_and_the_anchors_duty():
    hint = stage.anchor_correction_hint(
        [SimpleNamespace(category="overflow", message="the table spills")]
    )

    assert "style anchor" in hint
    assert "overflow: the table spills" in hint
    assert "1280x720" in hint


@pytest.mark.parametrize("profile", [None, SimpleNamespace(strict_dom_gate=False)])
def test_a_deck_without_the_strict_dom_gate_skips_the_anchor_gate(profile):
    assert (
        stage.anchor_blocking_defects(
            _STYLED, composition_profile=profile, reference_mode="style_reference"
        )
        == ()
    )


# --- the job lane: the anchor is rendered under the deck's gate and scope -----


def test_the_job_lane_render_step_holds_the_gate_and_binds_the_deck_scope():
    """The exact line bench run 30 tripped over.

    ``set_render_deck_scope`` is synchronous, so naming it in the same
    ``async with`` as the render gate raised ``TypeError`` before the render
    started and skipped the anchor gate on every deck.
    """

    gate = asyncio.Semaphore(1)
    seen: dict[str, object] = {}

    async def render_html(html):
        seen["html"] = html
        seen["scope"] = current_render_deck_scope()
        seen["gate_held"] = gate.locked()
        return b"png"

    step = stage.anchor_render_step(gate, deck_scope="deck-7", render_html=render_html)
    _run(step(_STYLED))

    assert seen == {"html": _STYLED, "scope": "deck-7", "gate_held": True}
    assert not gate.locked()
    assert current_render_deck_scope() is None


def test_the_job_lane_render_step_lets_a_render_failure_reach_the_caller():
    gate = asyncio.Semaphore(1)

    async def render_html(_html):
        raise RuntimeError("no chromium here")

    step = stage.anchor_render_step(gate, deck_scope=None, render_html=render_html)

    with pytest.raises(RuntimeError):
        _run(step(_STYLED))
    # The gate is released even when the render fails, or the deck's tail would
    # render one slide fewer at a time for the rest of the attempt.
    assert not gate.locked()


_ANCHORED_SLIDE = (
    "<!DOCTYPE html><html><head><style>body{background:#0B1F3A}"
    ".card{border-radius:12px}</style></head><body>"
    "<h1 data-qid='slide-{index}.title'>slide-{index}</h1>"
    "</body></html>"
)


def _job_lane_deck(tmp_path, monkeypatch, *, render, slide_count=4):
    """Drive the real job-lane pipeline; return (result, order, gate_calls, anchors)."""

    monkeypatch.setenv("AUTHORED_STYLE_ANCHOR_ENABLED", "true")
    monkeypatch.setenv("AUTHORED_TAIL_STREAMING_ENABLED", "false")
    order: list[str] = []
    gate_calls: list[str] = []
    anchors: list[object] = []

    async def batch(_plan, indexes, **kwargs):
        selected = tuple(indexes)
        order.extend(f"author-{index}" for index in selected)
        anchors.append(kwargs.get("style_anchor"))
        return AuthoredPlannedBatchResult(
            htmls={
                index: _ANCHORED_SLIDE.replace("{index}", str(index))
                for index in selected
            },
            failures={},
        )

    async def recording_render(html):
        order.append("render")
        return await render(html)

    def anchor_gate(html, **_kwargs):
        gate_calls.append(html)
        return ()

    async def scenario():
        engine, maker = await tail_lane._checkpoint_database(tmp_path, monkeypatch)
        tail_lane._install_pipeline_fakes(
            monkeypatch,
            slide_count=slide_count,
            batch=batch,
            render=recording_render,
        )
        monkeypatch.setattr(pipeline, "anchor_blocking_defects", anchor_gate)
        try:
            job = await tail_lane._running_authored_job(
                maker, total_slides=slide_count
            )
            return await tail_lane._drive_pipeline(job)
        finally:
            await engine.dispose()

    return _run(scenario()), order, gate_calls, anchors


def test_the_job_lane_renders_gates_and_hands_on_its_anchor(tmp_path, monkeypatch):
    """G0 end to end on the lane the UI uses: render, gate, then inherit."""

    async def render(_html):
        return b"png"

    result, order, gate_calls, anchors = _job_lane_deck(
        tmp_path, monkeypatch, render=render
    )

    assert len(result.pngs) == 4
    # The anchor slide is authored alone and rendered before anything else is
    # authored: with the gate skipped this render never happened at all.
    assert order[:3] == ["author-1", "render", "author-0"] or order[:2] == [
        "author-1",
        "render",
    ]
    assert len(gate_calls) == 1
    assert "slide-1" in gate_calls[0]
    # The anchor's own call carries no anchor; every later call inherits it.
    assert anchors[0] is None
    assert all(isinstance(anchor, StyleAnchor) for anchor in anchors[1:])
    assert anchors[1:]


def test_a_job_lane_render_failure_still_hands_the_deck_its_anchor(
    tmp_path, monkeypatch
):
    """No anchor at all is worse than an ungated one; the remake round remains."""

    renders = {"count": 0}

    async def render(_html):
        renders["count"] += 1
        if renders["count"] == 1:
            raise RuntimeError("renderer lease unavailable")
        return b"png"

    result, _order, gate_calls, anchors = _job_lane_deck(
        tmp_path, monkeypatch, render=render
    )

    assert len(result.pngs) == 4
    # The gate could not run without a render...
    assert gate_calls == []
    # ...and the deck still authored every later slide against the anchor.
    assert anchors[0] is None
    assert all(isinstance(anchor, StyleAnchor) for anchor in anchors[1:])
    assert anchors[1:]


# --- the anchor's own bookkeeping ---------------------------------------------


_LANES = {
    # (AUTHORED_TAIL_STREAMING_ENABLED, AUTHORED_WAVE_BARRIER_DISABLED)
    "streamed": ("true", "true"),
    "barrier": ("false", "false"),
}


@pytest.mark.parametrize("lane", sorted(_LANES))
def test_a_slide_the_anchor_stage_rescued_is_not_still_a_failure(
    tmp_path, monkeypatch, lane
):
    """Bench run 35: a failed remake re-failed a slide the deck had accepted.

    The anchor's first call authored slide 1, which the pass booked as
    successful and published to the tail.  Its gate then found blocking defects
    and asked for one remake, whose provider-attempt reservation raised --
    ``complete_stage`` clears the slide's attempt metadata, so ordinal 2
    conflicts with the stored count of zero.  The pass booked that raise as a
    failure of slide 1 and the recovery planner then refused the deck with
    ``failed_slide_indices must not include successful slides: [1]``.
    """

    streaming, barrier_disabled = _LANES[lane]
    monkeypatch.setenv("AUTHORED_STYLE_ANCHOR_ENABLED", "true")
    monkeypatch.setenv("AUTHORED_TAIL_STREAMING_ENABLED", streaming)
    monkeypatch.setenv("AUTHORED_WAVE_BARRIER_DISABLED", barrier_disabled)
    slide_count = 4
    gate_calls: list[str] = []
    recovery_calls: list[tuple[int, ...]] = []
    planned_recoveries: list[list[int]] = []

    async def batch(
        _plan,
        indexes,
        *,
        recovery=False,
        attempt_reservers=None,
        attempt_observers=None,
        **_kwargs,
    ):
        # Reserve each slide's provider ordinal exactly as the real
        # ``author_planned_batch`` does: the anchor's remake is refused here,
        # because completing the author stage cleared the slide's attempt
        # metadata and ordinal 2 no longer follows the stored count.
        selected = tuple(indexes)
        ordinal = 2 if recovery else 1
        for index in selected:
            reserver = (attempt_reservers or {}).get(index)
            if reserver is not None:
                await reserver(ordinal)
            observer = (attempt_observers or {}).get(index)
            if observer is not None:
                observer(ordinal)
        if recovery:
            recovery_calls.append(selected)
        return AuthoredPlannedBatchResult(
            htmls={
                index: _ANCHORED_SLIDE.replace("{index}", str(index))
                for index in selected
            },
            failures={},
        )

    async def render(_html):
        return b"png"

    def anchor_gate(html, **_kwargs):
        gate_calls.append(html)
        # The anchor's first render is defective, so the stage spends its one
        # remake -- the call whose reservation raises against a completed
        # author stage.
        return (
            (SimpleNamespace(category="overflow", message="the card clips"),)
            if len(gate_calls) == 1
            else ()
        )

    real_plan_recovery = tail_streaming.plan_authored_recovery_batches

    def recording_plan_recovery(failed, **kwargs):
        planned_recoveries.append(list(failed))
        return real_plan_recovery(failed, **kwargs)

    async def scenario():
        engine, maker = await tail_lane._checkpoint_database(tmp_path, monkeypatch)
        tail_lane._install_pipeline_fakes(
            monkeypatch, slide_count=slide_count, batch=batch, render=render
        )
        monkeypatch.setattr(pipeline, "anchor_blocking_defects", anchor_gate)
        monkeypatch.setattr(
            tail_streaming,
            "plan_authored_recovery_batches",
            recording_plan_recovery,
        )
        try:
            job = await tail_lane._running_authored_job(
                maker, total_slides=slide_count
            )
            return await tail_lane._drive_pipeline(job)
        finally:
            await engine.dispose()

    result = _run(scenario())

    # The deck publishes: the anchor slide is authored exactly once and its
    # failed remake costs it nothing.
    assert len(result.pngs) == slide_count
    assert sum("slide-1" in html for html in result.deck.htmls) == 1
    # The remake never reached the provider -- its reservation raised first --
    # and the recovery planner was never offered the anchor's index.
    assert recovery_calls == []
    assert planned_recoveries == [[]]
    # The gate ran once: the remake never produced HTML to re-inspect, so the
    # deck simply authored on without an anchor.
    assert len(gate_calls) == 1
