"""The figure contract must state how far down the page the figure reaches.

Regression for the 2026-09-05 finding: on 203 real figure slides the model's
figure container stopped a median 76px above the deck's bottom content line and
the figure's own lowest text 108px above it, leaving a blank band across the
foot of every figure page. The contract's only vertical statement was the floor
``at least 700x380px``, and the design system's only other one is the upper
bound ("content must end at or above y=648px"), so a floor was read as a target.

These tests pin the sentences that now state the lower bound, the way
``test_authored_takeaway_spacing`` pins the plan prompt's spacing rule: the fix
is prompt wording, and wording is only a fix while it is still in the prompt.
"""

from __future__ import annotations

from models.authored_infographic_plan import AuthoredSlideInfographic, InfographicNode
from utils.authored_infographic.contracts import (
    CONTENT_BOTTOM_PX_BY_PAGE_NUMBER_MODE,
    DEFAULT_CONTENT_BOTTOM_PX,
    LOW_HEIGHT_MAX_PX,
    content_bottom_px,
    slot_contract,
)
from utils.llm_calls.author_infographic_marker import infographic_marker
from utils.llm_calls.author_slide_rules import PAGE_NUMBER_RULES_BY_MODE

_NODES = (
    InfographicNode(label="진단", desc="수집 범위 확정"),
    InfographicNode(label="구축", desc="표준 설비 연결"),
    InfographicNode(label="확산", desc="전 라인 적용"),
)


def _slide(weight: str = "standard", archetype: str = "process") -> AuthoredSlideInfographic:
    return AuthoredSlideInfographic(
        index=13,
        archetype=archetype,  # type: ignore[arg-type]
        weight=weight,  # type: ignore[arg-type]
        takeaway="검증 후 개통한다",
        nodes=_NODES,
    )


def test_every_page_number_mode_has_a_bottom_content_line() -> None:
    # The contract must resolve a line for each mode the design system states
    # rules for, or a deck would be sized against a line it never renders.
    assert set(CONTENT_BOTTOM_PX_BY_PAGE_NUMBER_MODE) == set(PAGE_NUMBER_RULES_BY_MODE)


def test_the_line_is_the_one_the_page_number_rules_state() -> None:
    # 648 is the footer safe zone a numbered deck reserves; 688 is the bottom
    # margin an unnumbered one keeps. Both numbers are quoted in the rules the
    # same prompt carries, so a drift here would contradict them in one prompt.
    assert "y=648px" in PAGE_NUMBER_RULES_BY_MODE["template"]
    assert "y=688px" in PAGE_NUMBER_RULES_BY_MODE["none"]
    assert content_bottom_px("template") == 648
    assert content_bottom_px("companyStandard") == 648
    assert content_bottom_px("none") == 688
    assert content_bottom_px(None) == DEFAULT_CONTENT_BOTTOM_PX


def test_the_contract_states_the_bottom_line_not_a_size_floor() -> None:
    contract = slot_contract(_slide(), page_number_mode="none")
    assert "bottom edge lands ON y=688px" in contract
    assert "height is 688 minus the container's own top" in contract
    # The old floor was the whole vertical instruction and was read as the
    # target; it must not come back as the only thing the model is told.
    assert "at least 700x380px" not in contract


def test_the_contract_names_the_dead_band_as_the_defect() -> None:
    contract = slot_contract(_slide(), page_number_mode="template")
    assert "y=648px" in contract
    assert "blank band across the foot of the slide" in contract
    assert "defect rather than restraint" in contract
    # A model that cannot fill the height must not solve it with furniture.
    assert "never fill a shortfall with an empty spacer" in contract
    assert "430px" in contract


def test_a_hero_page_is_still_told_the_figure_is_the_page() -> None:
    hero = slot_contract(_slide("hero"), page_number_mode="none")
    standard = slot_contract(_slide("standard"), page_number_mode="none")
    assert "the whole content area under the header" in hero
    assert "everything the header, the takeaway and any side text leave" in standard
    # Both reach the same line: a standard page is narrower, never shorter.
    assert "bottom edge lands ON y=688px" in hero
    assert "bottom edge lands ON y=688px" in standard


def test_the_standard_weight_no_longer_invites_a_band_under_the_figure() -> None:
    marker = infographic_marker(_slide(), page_number_mode="none")
    assert "never as a band under it" in marker
    assert "reaches the bottom content line" in marker
    assert "sits beside or below it" not in marker


def test_the_marker_passes_the_deck_mode_through_to_the_contract() -> None:
    numbered = infographic_marker(_slide(), page_number_mode="template")
    unnumbered = infographic_marker(_slide(), page_number_mode="none")
    assert "y=648px" in numbered and "y=688px" not in numbered
    assert "y=688px" in unnumbered and "y=648px" not in unnumbered


def test_low_height_archetypes_are_pinned() -> None:
    # A regression that quietly dropped an archetype from this table would
    # reintroduce the exact blank-band defect this module fixes, just moved
    # inside the figure — pin the whole table, not just its presence.
    assert LOW_HEIGHT_MAX_PX == {
        "kpi": 240,
        "gauge": 320,
        "before_after": 280,
        "comparison": 280,
        "icon_list": 200,
        "checklist": 260,
        "timeline": 190,
    }


def test_a_low_height_archetype_states_its_ceiling_not_the_bottom_line() -> None:
    # kpi (a row of large numbers) cannot honestly stretch to 430px+: reaching
    # the bottom line would pad each tile with blank interior space instead of
    # removing the band, which is the same defect the 430px floor exists to
    # remove for every other archetype.
    contract = slot_contract(_slide(archetype="kpi"), page_number_mode="template")
    assert "height of exactly 240px" in contract
    assert "honest maximum a kpi figure reaches" in contract
    assert "bottom edge lands ON" not in contract
    assert "Never make the box shorter than 430px" not in contract
    # It still names the bottom line the rest of the content area may use.
    assert "y=648px" in contract
    assert "text column of its own BESIDE the figure" in contract
    assert "centre the figure vertically" in contract

    marker = infographic_marker(_slide(archetype="kpi"), page_number_mode="template")
    assert "intrinsic height ceiling" in marker
    assert "two-column layout" in marker
    assert "still reaches the bottom content line" not in marker


def test_a_tall_archetype_still_reaches_the_bottom_line() -> None:
    # hierarchy (a root with children under it) can honestly fill the box the
    # library's own fitting rules give it, so it keeps the original rule: the
    # bottom edge of the figure box lands ON the content line, not short of it.
    assert "hierarchy" not in LOW_HEIGHT_MAX_PX
    contract = slot_contract(_slide(archetype="hierarchy"), page_number_mode="template")
    assert "bottom edge lands ON y=648px" in contract
    assert "blank band across the foot of the slide" in contract
    assert "defect rather than restraint" in contract
    assert "430px" in contract

    marker = infographic_marker(_slide(archetype="hierarchy"), page_number_mode="template")
    assert "never as a band under it" in marker
    assert "reaches the bottom content line" in marker
    assert "intrinsic height ceiling" not in marker
