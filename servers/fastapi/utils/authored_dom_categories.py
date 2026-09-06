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
"""

from __future__ import annotations

from collections.abc import Mapping

from utils.authored_dom_footer_zone import FOOTER_ZONE_CATEGORY
from utils.authored_dom_text_escape import (
    BLOCKING_SAFETY_CATEGORIES,
    TEXT_ESCAPE_CATEGORY,
)

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
    "BLOCKING_SAFETY_CATEGORIES",
    "CRITIQUE_TYPE_BY_CATEGORY",
    "DOM_QUALITY_CATEGORIES",
    "GENERIC_GEOMETRY_CRITIQUE_TYPE",
    "RATIO_MAGNITUDE_CATEGORIES",
    "RESIDUAL_BLOCKING_CATEGORIES",
    "TEXT_SCALE_SHRINKABLE_CATEGORIES",
    "TEXT_SCALE_UNFIXABLE_CATEGORIES",
    "blocking_categories_in_order",
    "critique_type_for_category",
]
