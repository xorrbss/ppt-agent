"""PageOutcome: one published page's quality facts, in a format-neutral shape.

The flag classification (``flag_classification.py``) and the terminal policy
must not read ``html_content`` or ``svg_content``; a driver reports each page it
is about to publish as a :class:`PageOutcome` and this module turns the deck's
outcomes into the per-page classification publication records.

The classification rule is the authored one, applied unchanged: a page is
flagged when its final measurement still fails (a residual) or when the
terminal policy put something on it (``flag``). ``classify_flagged_slides``
then decides which flags a reviewer must act on -- a residual, or a substitute
page, always needs review -- and a page is ``verified`` exactly when it does
not.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from services.generation_core.quality.flag_classification import (
    CLASSIFICATION_KEY,
    REVIEW_REQUIRED,
    classify_flagged_slides,
)
from services.generation_core.quality.terminal_slide_flag_only import flagged_entry

#: Classification of a page nothing flagged.
PASSED = "passed"

#: ``reason`` prefix of a flag recorded for a residual measured failure.
QUALITY_REASON_PREFIX = "quality:"


@dataclass(frozen=True, slots=True)
class PageOutcome:
    """What one page is about to publish, and what was measured on it."""

    index: int
    source_format: str
    source: str
    png_path: str | None
    quality: Mapping[str, Any]
    attempts: int
    failures: tuple[Mapping[str, Any], ...] = ()
    verified: bool = False
    #: The terminal-policy flag entry (``terminal_slide_flag_only.flagged_entry``
    #: shape) when this page is a fallback or a flag-only page.
    flag: Mapping[str, object] | None = None
    classification: str = PASSED

    @property
    def measured_failure(self) -> bool:
        return self.quality.get("passed") is not True


def _residual_flag(outcome: PageOutcome) -> dict[str, object]:
    kinds = sorted({str(item.get("kind") or "unknown") for item in outcome.failures}) or ["unknown"]
    return flagged_entry(
        index=outcome.index,
        reason=QUALITY_REASON_PREFIX + ",".join(kinds),
        attempted=False,
        outcome="not_attempted",
    )


def classify_page_outcomes(outcomes: Sequence[PageOutcome]) -> list[PageOutcome]:
    """Every outcome with ``classification`` and ``verified`` set, in order."""

    residual = [
        {"slide_index": outcome.index, "failures": [dict(item) for item in outcome.failures]}
        for outcome in outcomes
        if outcome.measured_failure
    ]
    flags: list[dict[str, object]] = []
    for outcome in outcomes:
        if outcome.flag is not None:
            flags.append(dict(outcome.flag))
        elif outcome.measured_failure:
            flags.append(_residual_flag(outcome))
    classified = {
        int(entry["index"]): entry
        for entry in classify_flagged_slides(flags, residual_failures=residual)
    }
    result: list[PageOutcome] = []
    for outcome in outcomes:
        entry = classified.get(outcome.index)
        classification = str(entry[CLASSIFICATION_KEY]) if entry is not None else PASSED
        result.append(replace(
            outcome,
            flag=entry if entry is not None else None,
            classification=classification,
            verified=classification != REVIEW_REQUIRED,
        ))
    return result


def deck_quality_summary(outcomes: Sequence[PageOutcome]) -> dict[str, object]:
    """The deck-level status publication and the endpoint report."""

    review = [outcome.index for outcome in outcomes if outcome.classification == REVIEW_REQUIRED]
    return {
        "status": REVIEW_REQUIRED if review else PASSED,
        "pageCount": len(outcomes),
        "passedPages": len(outcomes) - len(review),
        "flaggedPages": len(review),
        "reviewPages": review,
        "flaggedSlides": [dict(outcome.flag) for outcome in outcomes if outcome.flag is not None],
    }


def review_page_indices(qualities: Sequence[Mapping[str, Any]]) -> list[int]:
    """The one rule for a deck's ``reviewPages``: pages not verified-and-passed.

    Every writer of a published deck's review list reads it through here, so a
    later mutation (page renumbering, a page remake) can neither blanket-flag
    a classified deck nor clear a page it did not re-verify.
    """

    return [index for index, quality in enumerate(qualities)
            if not isinstance(quality, Mapping)
            or quality.get("verified") is not True or quality.get("passed") is not True]


__all__ = [
    "PASSED",
    "QUALITY_REASON_PREFIX",
    "PageOutcome",
    "classify_page_outcomes",
    "deck_quality_summary",
    "review_page_indices",
]
