"""G0 anchor selection, gating and the deck's behaviour when the anchor fails."""

import asyncio
from types import SimpleNamespace

import pytest

from services import authored_presentation_service as authored_service
from services import authored_style_anchor_stage as stage
from utils.llm_calls.author_style_anchor import StyleAnchor


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
