"""What a DOM quality category means to every consumer, in one place.

Bench run 36 (K/document fixture, ``f0d2b126c``, 2026-09-05) died
``authored_composition_safety_gate_failed`` after 28.7 minutes because the
bounded quality-gate repair raised ``KeyError: 'text_intrudes_footer_zone'``
for slides 3..17.  ``utils.llm_calls.author_vision_qa`` mapped a DOM category
to a critique type through a dict literal that predated the two detectors
``f0d2b126c`` registered (``text_escapes_card``,
``text_intrudes_footer_zone``), and an unregistered key raised instead of
degrading.  A detector could therefore be added, registered as a publication
blocker, and still take the whole deck down the first time the repair path met
it.

This module is what stops the next detector from repeating that.  Every
category-to-something projection reads from here, every lookup has a defined
answer for an unknown category, and
``tests/unit/test_authored_dom_category_contract.py`` asserts that each
registered category is classified by each consumer.

It is also where a category's *status* is stated: detected-and-recorded, or
detected-and-blocking.  ``BLOCKING_SAFETY_CATEGORIES`` is derived here from
``DETECTED_GEOMETRY_CATEGORIES`` minus ``ADVISORY_ONLY_CATEGORIES``, so
promoting or demoting a detector is a one-line edit in one file rather than a
sweep through the policy, the residual gate, the repair rounds and the replay
tools.
"""

from __future__ import annotations

from collections.abc import Mapping

from utils.authored_dom_footer_zone import FOOTER_ZONE_CATEGORY
from utils.authored_dom_text_escape import TEXT_ESCAPE_CATEGORY
from utils.authored_empty_media import EMPTY_MEDIA_CATEGORY

#: Every value ``utils.authored_dom_quality.DomQualityIssue.category`` accepts,
#: in the order its ``Literal`` declares them.  The contract test asserts the
#: two stay identical, so a detector cannot widen the ``Literal`` without
#: coming through here.
DOM_QUALITY_CATEGORIES: tuple[str, ...] = (
    "overflow",
    "clipping",
    "off_canvas",
    "overlap",
    TEXT_ESCAPE_CATEGORY,
    FOOTER_ZONE_CATEGORY,
    "font",
    "image",
    "content",
    "contrast",
    EMPTY_MEDIA_CATEGORY,
)

#: Every geometry category a detector reports.  Membership here is what makes a
#: category *known*; whether it also blocks publication is decided immediately
#: below, so a detector never states its own status.
DETECTED_GEOMETRY_CATEGORIES = frozenset(
    {
        "overflow",
        "clipping",
        "off_canvas",
        "overlap",
        TEXT_ESCAPE_CATEGORY,
        FOOTER_ZONE_CATEGORY,
        EMPTY_MEDIA_CATEGORY,
    }
)

# Empty media is a measured composition defect, not an admission exception.
# This registry has no per-profile classification: ordinary decks spend their
# bounded remake budget; terminal verbatim source-reflow slides carry a flag.

#: Detected, recorded, briefed -- but never a reason on its own to flag a
#: slide, spend a remake or hold a deck back.
#:
#: ``FOOTER_ZONE_CATEGORY`` is here as a settled finding, not as a threshold
#: awaiting calibration.  The current detector was replayed offline over three
#: stored decks re-rendered through this branch's own ``inspect_authored_dom``:
#: the deck the product owner picked as BETTER in a blind paired round (run 22,
#: the parent, generated before this detector existed) carried the MOST footer
#: intrusions -- 16 of its 24 slides, n=29 occurrences, min 4 px, median 28 px,
#: max 56 px -- against run 37 (n=24, median 26 px, max 97 px) and run 39
#: (n=33, median 30 px, max 127 px).  The three medians sit within 4 px of each
#: other and the preferred deck's whole 4-56 px range lies inside the others',
#: so no threshold separates a deck the owner liked from one they did not.  The
#: category measures a property authored slides have always had, while blocking
#: on it cost real money: runs 37/38/39 each exhausted the deck remake ceiling
#: and still published 9-14 slides flagged, roughly doubling the repair stage
#: (989 s against the parent's 555 s).  ``text_escapes_card`` stays blocking on
#: the opposite evidence -- 1-2 occurrences per deck, two of them visually
#: confirmed as copy printed outside its card.
#:
#: Re-promoting a category is deleting its name from this set: every consumer
#: below and in ``services.authored_composition_policy`` derives from
#: ``BLOCKING_SAFETY_CATEGORIES``, and the text-scale wiring for the footer
#: category is deliberately left intact (see ``TEXT_SCALE_SHRINKABLE_CATEGORIES``).
ADVISORY_ONLY_CATEGORIES = frozenset({FOOTER_ZONE_CATEGORY})

#: Every category the composition policy treats as a publication blocker: a
#: detected geometry category that has not been demoted to advisory.  Consumed
#: by ``services.authored_composition_policy.blocking_safety_issues``, the
#: vision-QA residual gate, the legacy repair-trigger merge and the offline
#: replay buckets, so the status is stated exactly once -- here.
BLOCKING_SAFETY_CATEGORIES = frozenset(
    DETECTED_GEOMETRY_CATEGORIES - ADVISORY_ONLY_CATEGORIES
)

#: Categories whose presence lets the cheap deterministic critique stand in for
#: the vision-QA model call in ``utils.llm_calls.author_vision_qa``.
#:
#: The repair round asks the model to look at a slide only when the measured DOM
#: has nothing actionable to say.  That cascade is sound for a measured geometry
#: miss -- the browser already knows more about a clipped box than a model
#: looking at a picture of it -- but it is wrong for a category that is not a
#: reason to spend a repair in the first place.  Replayed offline over 905
#: stored bench slides (41 runs under ``artifacts/``), ``contrast`` fired on
#: 97.79% of them, so the deterministic critique was never empty and the model
#: call was unreachable: on the 24-slide run 48 deck, 0 of 24 slides could reach
#: it and 20 of the 24 were held by ``contrast`` alone.
#:
#: ``contrast`` is not in ``DETECTED_GEOMETRY_CATEGORIES``, so no other consumer
#: treats it as a blocker, and ``ADVISORY_ONLY_CATEGORIES`` is already declared
#: as "never a reason on its own to flag a slide, spend a remake or hold a deck
#: back".  Neither may consume the repair budget or veto the model either: they
#: travel to the model as briefing material instead.  An unregistered category
#: supersedes, which keeps a new detector fail-closed exactly as today.
VQA_SUPPRESSING_CATEGORIES = frozenset(
    set(DOM_QUALITY_CATEGORIES) - ADVISORY_ONLY_CATEGORIES - {"contrast"}
)


def deterministic_findings_supersede_vqa(categories: object) -> bool:
    """Whether these DOM categories make the vision-QA model call redundant."""

    return any(
        str(category) not in (set(DOM_QUALITY_CATEGORIES) - VQA_SUPPRESSING_CATEGORIES)
        for category in categories or ()
    )


#: What an unknown category degrades to.  A detector that raised an issue at
#: all measured a geometry miss, and ``overflow`` is the critique type whose
#: brief tells the model to make the content fit the box it was given -- the
#: safe reading of "something does not fit" when we cannot be more specific.
GENERIC_GEOMETRY_CRITIQUE_TYPE = "overflow"

#: DOM category -> ``utils.llm_calls.critique_slide.IssueType``.
#: ``text_escapes_card`` and ``text_intrudes_footer_zone`` are fit problems --
#: copy printed outside the card that owns it, or below the footer safe line --
#: so they take the same type ``overflow``/``clipping`` take, and the remake
#: brief tells the model to keep the copy inside its card and above the footer.
CRITIQUE_TYPE_BY_CATEGORY: Mapping[str, str] = {
    "overflow": "overflow",
    "clipping": "overflow",
    "off_canvas": "overflow",
    "overlap": "overlap",
    "contrast": "low-contrast",
    "font": "overflow",
    "image": "other",
    "content": "other",
    TEXT_ESCAPE_CATEGORY: "overflow",
    FOOTER_ZONE_CATEGORY: "overflow",
    EMPTY_MEDIA_CATEGORY: "other",
}


def critique_type_for_category(category: object) -> str:
    """The critique type a DOM category feeds, never raising on an unknown one.

    The caller always carries the raw category name in the issue detail, so an
    unregistered category still reaches the model as a named geometry defect
    rather than killing the round.
    """

    return CRITIQUE_TYPE_BY_CATEGORY.get(str(category), GENERIC_GEOMETRY_CRITIQUE_TYPE)


#: Categories whose magnitude is a ratio of "how much bigger than its box",
#: which is what bounds a shrink and what names the worst overflow kind.
#: ``off_canvas`` and ``clipping`` carry magnitudes of other shapes.
RATIO_MAGNITUDE_CATEGORIES = frozenset(
    {"overflow", TEXT_ESCAPE_CATEGORY, FOOTER_ZONE_CATEGORY}
)

#: Categories a smaller type size can actually resolve: the copy is too big for
#: the box, and every one of these carries a ratio for the A4 rung to bound the
#: shrink with (``utils.authored_dom_fit_magnitude.MAGNITUDE_CATEGORIES``).
#:
#: ``FOOTER_ZONE_CATEGORY`` stays listed here although, being advisory, it
#: cannot reach the rung today: the rung reads the gate failures, and those
#: carry only ``blocking_safety_issues``.  The wiring is kept intact
#: deliberately, so re-promoting the category is the one-line deletion from
#: ``ADVISORY_ONLY_CATEGORIES`` and nothing else.
TEXT_SCALE_SHRINKABLE_CATEGORIES = frozenset(
    {"overflow", "clipping", TEXT_ESCAPE_CATEGORY, FOOTER_ZONE_CATEGORY}
)

#: Blocking categories the rung must decline: an overlap or an off-canvas
#: element is a composition error, and a smaller font just makes a wrong layout
#: smaller.  Derived rather than listed so a blocking category added without a
#: shrinkable declaration defaults to "do not spend a render on it".
TEXT_SCALE_UNFIXABLE_CATEGORIES = frozenset(
    BLOCKING_SAFETY_CATEGORIES - TEXT_SCALE_SHRINKABLE_CATEGORIES
)

#: What the vision-QA residual gate refuses to publish: every publication
#: blocker plus ``image``, which that gate has always treated as one.
RESIDUAL_BLOCKING_CATEGORIES = frozenset(BLOCKING_SAFETY_CATEGORIES | {"image"})


def blocking_categories_in_order(categories: object) -> list[str]:
    """The blocking categories present, in ``DOM_QUALITY_CATEGORIES`` order."""

    raised = {str(category) for category in categories or ()}
    return [
        category
        for category in DOM_QUALITY_CATEGORIES
        if category in BLOCKING_SAFETY_CATEGORIES and category in raised
    ]


__all__ = [
    "ADVISORY_ONLY_CATEGORIES",
    "BLOCKING_SAFETY_CATEGORIES",
    "CRITIQUE_TYPE_BY_CATEGORY",
    "DETECTED_GEOMETRY_CATEGORIES",
    "DOM_QUALITY_CATEGORIES",
    "GENERIC_GEOMETRY_CRITIQUE_TYPE",
    "RATIO_MAGNITUDE_CATEGORIES",
    "RESIDUAL_BLOCKING_CATEGORIES",
    "TEXT_SCALE_SHRINKABLE_CATEGORIES",
    "TEXT_SCALE_UNFIXABLE_CATEGORIES",
    "VQA_SUPPRESSING_CATEGORIES",
    "blocking_categories_in_order",
    "critique_type_for_category",
    "deterministic_findings_supersede_vqa",
]
