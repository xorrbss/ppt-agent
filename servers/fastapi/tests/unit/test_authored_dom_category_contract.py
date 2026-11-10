"""Every registered DOM category must be classified by every consumer.

Bench run 36 (K/document fixture, ``f0d2b126c``, 2026-09-05) is the reason this
file exists.  Two detectors were added and registered as publication blockers
(``text_escapes_card``, ``text_intrudes_footer_zone``), but the projection from
a DOM category to a critique type was a dict literal that still listed only the
older eight.  The first slide the bounded quality-gate repair tried to fix
raised ``KeyError: 'text_intrudes_footer_zone'``; the round failed for slides
3..17 and the deck died ``authored_composition_safety_gate_failed`` after 28.7
minutes.

The fix is not "add two keys" -- it is that a detector cannot be registered
without every consumer having an answer for it, and that no consumer may raise
on a category it has never heard of.  These tests are that contract.  Two of
them run every consumer over a category that does not exist at all
(``_UNKNOWN``), which is the closest thing available to the detector someone
adds next month.
"""

from __future__ import annotations

import typing

import pytest

from scripts.authored_fit_replay_buckets import (
    BLOCKING_CATEGORIES,
    blocking_categories,
)
from services.authored_composition_policy import blocking_safety_issues
from utils.authored_dom_categories import (
    ADVISORY_ONLY_CATEGORIES,
    BLOCKING_SAFETY_CATEGORIES,
    DETECTED_GEOMETRY_CATEGORIES,
    CRITIQUE_TYPE_BY_CATEGORY,
    DOM_QUALITY_CATEGORIES,
    RESIDUAL_BLOCKING_CATEGORIES,
    TEXT_SCALE_SHRINKABLE_CATEGORIES,
    TEXT_SCALE_UNFIXABLE_CATEGORIES,
    critique_type_for_category,
)
from utils.authored_dom_quality import DomQualityIssue, DomQualityReport
from utils.llm_calls.critique_slide import CritiqueIssue, IssueType

#: A category no detector has ever emitted: the next one, standing in.
_UNKNOWN = "a_detector_added_after_this_test_was_written"


def _issue(category: str, *, severity: str = "high") -> DomQualityIssue:
    return DomQualityIssue(
        category,  # type: ignore[arg-type]
        severity,  # type: ignore[arg-type]
        f"{category} measured on this slide",
        ("q-target",),
    )


def _report(*issues: DomQualityIssue) -> DomQualityReport:
    return DomQualityReport(
        "style-reference", tuple(issues), (), len(issues), "available"
    )


# ---------------------------------------------------------------------------
# The registry is the registry
# ---------------------------------------------------------------------------


def test_registry_matches_the_issue_models_own_literal():
    """A detector cannot widen ``DomQualityIssue`` without coming through here."""

    declared = typing.get_type_hints(DomQualityIssue)["category"]
    assert set(typing.get_args(declared)) == set(DOM_QUALITY_CATEGORIES)
    assert len(DOM_QUALITY_CATEGORIES) == len(set(DOM_QUALITY_CATEGORIES))


def test_every_blocking_category_is_registered():
    assert BLOCKING_SAFETY_CATEGORIES <= set(DOM_QUALITY_CATEGORIES)


def test_exactly_these_categories_block_publication():
    """The pin.  Promoting or demoting a category must edit this list too.

    ``text_intrudes_footer_zone`` is deliberately absent: replaying the current
    detector over three stored decks put its intrusion medians within 4 px of
    each other (28 px on the deck the product owner preferred in a blind paired
    round, 26 px and 30 px on the two they did not), and the preferred deck's
    whole 4-56 px range inside the others', so no threshold on it separates a
    liked deck from a disliked one -- while blocking on it exhausted the deck
    remake ceiling and roughly doubled the repair stage.  ``text_escapes_card``
    stays: 1-2 occurrences per deck, two of them confirmed real defects.
    """

    assert BLOCKING_SAFETY_CATEGORIES == {
        "empty_media_panel",
        "overflow",
        "clipping",
        "off_canvas",
        "overlap",
        "text_escapes_card",
    }
    assert ADVISORY_ONLY_CATEGORIES == {"text_intrudes_footer_zone"}
    # The two partition the geometry detectors: a detector is either blocking
    # or advisory, never neither and never both.
    assert BLOCKING_SAFETY_CATEGORIES | ADVISORY_ONLY_CATEGORIES == (
        DETECTED_GEOMETRY_CATEGORIES
    )
    assert not (BLOCKING_SAFETY_CATEGORIES & ADVISORY_ONLY_CATEGORIES)


# ---------------------------------------------------------------------------
# Every consumer has an answer for every registered category
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("category", DOM_QUALITY_CATEGORIES)
def test_every_registered_category_has_a_valid_critique_type(category: str):
    assert category in CRITIQUE_TYPE_BY_CATEGORY
    critique_type = critique_type_for_category(category)
    assert critique_type in typing.get_args(IssueType)
    # The Pydantic model is the real gate on the value, so build one.
    assert CritiqueIssue(type=critique_type, severity="high", detail="d").type == (
        critique_type
    )


@pytest.mark.parametrize("category", sorted(BLOCKING_SAFETY_CATEGORIES))
def test_every_blocking_category_reaches_every_blocking_consumer(category: str):
    report = _report(_issue(category))

    # The composition policy blocks publication on it.
    assert [issue.category for issue in blocking_safety_issues(report)] == [category]
    # The vision-QA residual gate refuses to publish it.
    assert category in RESIDUAL_BLOCKING_CATEGORIES
    # The offline replay counts it instead of reporting a cleaner deck.
    assert category in BLOCKING_CATEGORIES
    assert blocking_categories([{"category": category}]) == [category]
    # The A4 text-scale rung has decided whether shrinking type can fix it.
    assert (category in TEXT_SCALE_SHRINKABLE_CATEGORIES) != (
        category in TEXT_SCALE_UNFIXABLE_CATEGORIES
    )


def test_the_two_fit_detectors_are_geometry_critiques():
    """Run 36's two categories brief the model the way overflow does."""

    for category in ("text_escapes_card", "text_intrudes_footer_zone"):
        assert critique_type_for_category(category) == "overflow"


# ---------------------------------------------------------------------------
# An unknown category degrades; it never raises
# ---------------------------------------------------------------------------
