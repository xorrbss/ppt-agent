"""The free-composition default for ordinary authored decks (2026-09-05).

An ordinary deck is authored the way the August decks were unless asked
otherwise: no deck-wide infographic plan or component library, no
server-frozen composition envelope in the prompt or at the gate, and the 12px
font floor. Two knobs restore the newer machinery; source-fidelity profiles
never lose their envelope.
"""

from __future__ import annotations


import pytest

from services.authored_composition_policy import (
    CompositionProfile,
    SlideCapacity,
    aggregate_owner_capacity_violations,
    build_authored_capacity_plan,
    capacity_violations,
    reduce_authored_owner_text_capacity,
)

_KNOBS = (
    "AUTHORED_INFOGRAPHIC_PLAN_ENABLED",
    "AUTHORED_COMPOSITION_ENVELOPE_ENABLED",
    "AUTHORED_VISUAL_STORY_PLAN_ENABLED",
)
_ORDINARY = CompositionProfile("ordinary", "authored", True, True, 1)
_VERBATIM = CompositionProfile("source-verbatim", "verbatim", False, True, 2)
_DENSE_HTML = "<section>" + "".join(
    f'<div data-qid="body.item{i}" style="font-size:12px">항목 {i} 설명 문장</div>'
    for i in range(80)
) + "</section>"


@pytest.fixture(autouse=True)
def _clean_knobs(monkeypatch):
    for key in _KNOBS:
        monkeypatch.delenv(key, raising=False)


def test_ordinary_envelope_is_relaxed_by_default() -> None:
    plan = build_authored_capacity_plan(("a", "b"), ("PROBLEM", "CLOSING"), _ORDINARY)

    for slide in plan.slides:
        assert slide.enforced is False
        assert slide.min_font_px == 12
        assert slide.prompt_brief(_ORDINARY) == ""
        assert slide.with_source_images(3).enforced is False
        # Nothing was stated to the model, so nothing can be violated: a slide
        # far past the 460-char / 40-qid envelope is admitted as it is, at the
        # author-time aggregate gate as well as at the render-time gate, and
        # the deterministic reducer leaves it alone.
        assert capacity_violations(_DENSE_HTML, None, slide) == ()
        assert aggregate_owner_capacity_violations(_DENSE_HTML, slide) == ()
        assert reduce_authored_owner_text_capacity(
            _DENSE_HTML, slide, profile_id="ordinary", allow_summarization=True
        ) == _DENSE_HTML
    assert plan.briefs() == ("", "")


def test_bound_ordinary_deck_still_trips_the_author_time_gate(monkeypatch) -> None:
    monkeypatch.setenv("AUTHORED_COMPOSITION_ENVELOPE_ENABLED", "true")
    slide = build_authored_capacity_plan(("a",), ("PROBLEM",), _ORDINARY).at(0)

    assert any(
        item.startswith("owner_qids:")
        for item in aggregate_owner_capacity_violations(_DENSE_HTML, slide)
    )


def test_source_fidelity_profiles_keep_their_envelope() -> None:
    slide = build_authored_capacity_plan(("a",), ("PROBLEM",), _VERBATIM).at(0)

    assert slide.enforced is True
    assert slide.min_font_px == 15
    assert "SERVER-FROZEN COMPOSITION ENVELOPE" in slide.prompt_brief(_VERBATIM)


def test_envelope_knob_restores_the_bound_ordinary_deck(monkeypatch) -> None:
    monkeypatch.setenv("AUTHORED_COMPOSITION_ENVELOPE_ENABLED", "true")
    slide = build_authored_capacity_plan(("a",), ("PROBLEM",), _ORDINARY).at(0)

    assert slide.enforced is True
    assert slide.min_font_px == 15
    assert "keep semantic targets <= 40" in slide.prompt_brief(_ORDINARY)


def test_hand_built_capacity_is_enforced_unless_told_otherwise() -> None:
    capacity = SlideCapacity(0, "PROBLEM", 20, 20, 40, 1, 4, min_font_px=16)

    assert capacity.enforced is True
    assert "never use text below 16px" in capacity.prompt_brief(_ORDINARY)
