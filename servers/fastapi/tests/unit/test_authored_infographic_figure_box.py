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


