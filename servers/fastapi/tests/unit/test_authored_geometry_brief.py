"""The GEOMETRY paragraph that replaces a relaxed composition envelope.

Pins two things (2026-09-05): a relaxed envelope (ordinary decks with
``AUTHORED_COMPOSITION_ENVELOPE_ENABLED`` unset) now carries a short geometry
reminder instead of a blank brief, and an enforced envelope is completely
unaffected -- it still gets only the SERVER-FROZEN COMPOSITION ENVELOPE text.
"""

from __future__ import annotations

from types import SimpleNamespace

from models.authored_header_contract import default_header_contract
from services.authored_composition_policy import (
    CompositionProfile,
    build_authored_capacity_plan,
)
from utils.authored_geometry_brief import geometry_brief
from utils.authored_infographic.contracts import (
    CONTENT_BOTTOM_PX_BY_PAGE_NUMBER_MODE,
    content_bottom_px,
)
from utils.llm_calls import author_deck as author_deck_module

_ORDINARY = CompositionProfile("ordinary", "authored", True, True, 1)
_VERBATIM = CompositionProfile("source-verbatim", "verbatim", False, True, 2)
_NO_BUDGET_WORDS = ("characters", "qids")


def test_geometry_brief_states_frame_bottom_line_and_overlap() -> None:
    brief = geometry_brief()

    assert brief.startswith("GEOMETRY:")
    assert "1280x720" in brief
    assert "y=688px" in brief  # no page-number mode means the wider content field
    assert "overlap" in brief
    assert "overflow:hidden" in brief
    for word in _NO_BUDGET_WORDS:
        assert word not in brief


def test_geometry_brief_bottom_line_matches_the_rules_source_of_truth() -> None:
    for mode, expected in CONTENT_BOTTOM_PX_BY_PAGE_NUMBER_MODE.items():
        brief = geometry_brief(page_number_mode=mode)
        assert f"y={content_bottom_px(mode)}px" in brief
        assert f"y={expected}px" in brief

    # An unnumbered deck keeps its own, wider margin -- never the 648px line.
    assert "y=688px" in geometry_brief(page_number_mode="none")
    assert "y=648px" not in geometry_brief(page_number_mode="none")


def test_geometry_brief_leads_with_header_contract_when_in_force() -> None:
    without_header = geometry_brief(header_in_force=False)
    assert "header contract" not in without_header
    assert "closing page" not in without_header

    with_header = geometry_brief(header_in_force=True)
    assert with_header.startswith("GEOMETRY: The header band")
    assert "closing page" in with_header
    assert "mandatory" in with_header


def test_relaxed_capacity_brief_carries_geometry_not_budgets() -> None:
    relaxed = build_authored_capacity_plan(("a", "b"), ("PROBLEM", "CLOSING"), _ORDINARY)
    plan = SimpleNamespace(
        capacity_plan=relaxed,
        text_plan=None,
        page_number_mode="template",
        roles=("PROBLEM", "CLOSING"),
        header_contract_at=lambda index: default_header_contract(),
    )

    brief = author_deck_module._capacity_brief(plan, 1, relaxed.at(1))

    assert brief is not None
    assert brief.startswith("GEOMETRY:")
    assert "y=648px" in brief
    for word in _NO_BUDGET_WORDS:
        assert word not in brief
    assert "SERVER-FROZEN COMPOSITION ENVELOPE" not in brief


def test_relaxed_capacity_brief_omits_header_sentence_for_cover() -> None:
    relaxed = build_authored_capacity_plan(("a",), ("COVER",), _ORDINARY)
    plan = SimpleNamespace(
        capacity_plan=relaxed,
        text_plan=None,
        page_number_mode="template",
        roles=("COVER",),
        # COVER is excluded from the header contract by default, so
        # ``applies_to_role`` must suppress the header sentence here.
        header_contract_at=lambda index: default_header_contract(),
    )

    brief = author_deck_module._capacity_brief(plan, 0, relaxed.at(0))

    assert brief is not None and "header contract" not in brief


def test_relaxed_capacity_brief_degrades_safely_with_no_header_or_mode_info() -> None:
    relaxed = build_authored_capacity_plan(("a",), ("PROBLEM",), _ORDINARY)
    # A stand-in plan carrying neither page_number_mode nor header_contract_at,
    # the same tolerance the pre-existing capacity_plan/text_plan lookups get.
    plan = SimpleNamespace(capacity_plan=relaxed, text_plan=None)

    brief = author_deck_module._capacity_brief(plan, 0, relaxed.at(0))

    assert brief is not None and brief.startswith("GEOMETRY:")
    assert "y=688px" in brief
    assert "header contract" not in brief


def test_enforced_capacity_brief_is_unchanged() -> None:
    bound = build_authored_capacity_plan(("a",), ("PROBLEM",), _VERBATIM)
    plan = SimpleNamespace(capacity_plan=bound, text_plan=None)

    brief = author_deck_module._capacity_brief(plan, 0, bound.at(0))

    assert brief is not None
    assert brief.startswith("SERVER-FROZEN COMPOSITION ENVELOPE")
    assert "GEOMETRY:" not in brief
