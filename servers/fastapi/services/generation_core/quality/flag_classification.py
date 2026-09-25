"""Separate a review requirement from a record of a repair that was not admitted.

Every flag a verbatim deck publishes carries the same reason string,
``composition_safety:gate_repair_not_admitted``, and ``reviewPages`` mirrors the
flag list one for one, so that string is what a reviewer is asked to act on.
Measured over four published runs of the 35-slide validation fixture, 32 of 53
flagged slides carry no blocking defect at all on the bytes that shipped, and 40
of 53 none of the geometry kind the ``composition_safety:`` prefix implies
(``artifacts/flagged-overreport-20260911/report.md``).

The cause is that one label carries two meanings.
``declare_unadmitted_gate_repair`` records that a bounded repair candidate was
not admitted -- a process outcome -- and the original slide publishes with that
flag attached. The deck is then re-measured, but ``partition_residual_failures``
only classifies residuals *by* the flag; nothing reconciles a flag the final
measurement contradicts.

This module stops conflating the two. It removes no entry and changes no
``reason`` string, so every existing consumer of the flag vocabulary keeps
reading what it read before; it adds one field saying which of the two meanings
an entry carries, and derives the reviewer-facing list from that.

The measured decks above only ever produced one kind of flag, so the rule may
not be read off them alone. A flag is also a review requirement whenever the
page that shipped is not the page the deck authored -- an author-stage
placeholder, or a remade slide -- because that is invisible to any gate reading
the published bytes: a placeholder is geometrically clean by construction. See
``published_a_substitute``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

#: The final gate still measures a defect on this slide's published bytes, or
#: the browser source readback could not see one of its source text blocks.
REVIEW_REQUIRED = "review_required"

#: One bounded repair was attempted and its candidate was not admitted. The
#: slide that shipped carries no measured defect; this is history, not a defect.
REPAIR_NOT_ADMITTED = "repair_not_admitted"

#: Sub-reason the browser source readback merges onto an already flagged entry.
SOURCE_VISIBILITY_REASON = "source_visibility"

CLASSIFICATION_KEY = "classification"

#: A flag whose reason starts with this published something the author stage
#: produced in place of the slide it could not author -- a deterministic
#: placeholder from ``authored_author_stage_placeholder.py``, or a simplified
#: retry. The page a reader sees is not the page the deck asked for, which is a
#: review requirement whatever a geometry gate measures on it: a placeholder is
#: clean by construction.
SUBSTITUTE_REASON_PREFIX = "author:"

#: ``remakeOutcome`` values that mean the slide which shipped is the original,
#: untouched. Every other outcome follows a remake, so a substitute shipped.
ORIGINAL_PRESERVED_OUTCOMES = frozenset({"verbatim_flag_only", "not_attempted"})


def _reasons_of(entry: Mapping[str, object]) -> tuple[str, ...]:
    raw = entry.get("reasons")
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        return tuple(item for item in raw if isinstance(item, str))
    reason = entry.get("reason")
    return (reason,) if isinstance(reason, str) else ()


def _residual_indices(residual_failures: Sequence[Mapping[str, object]]) -> frozenset[int]:
    indices: set[int] = set()
    for item in residual_failures:
        if not isinstance(item, Mapping):
            continue
        index = item.get("slide_index")
        if isinstance(index, int) and not isinstance(index, bool):
            indices.add(index)
    return frozenset(indices)


def published_a_substitute(entry: Mapping[str, object]) -> bool:
    """Whether the page that shipped is not the page the deck authored.

    Three things put a substitute on the page, and none of them leaves a defect
    for a geometry gate to measure -- a placeholder renders cleanly by
    construction -- so none can be recognised from the published bytes alone:

    * an ``author:`` reason, where the author stage gave up and
      ``authored_author_stage_placeholder.py`` rendered the outline instead;
    * a remake that ran (``remakeAttempted``), whose output replaced the first
      authored attempt whether or not the remake itself then passed;
    * any ``remakeOutcome`` outside ``ORIGINAL_PRESERVED_OUTCOMES``, which is
      how the deck records that it did not keep the original.
    """

    for reason in _reasons_of(entry):
        if reason.startswith(SUBSTITUTE_REASON_PREFIX):
            return True
    if entry.get("remakeAttempted") is True:
        return True
    outcome = entry.get("remakeOutcome")
    return isinstance(outcome, str) and outcome not in ORIGINAL_PRESERVED_OUTCOMES


def needs_review(
    entry: Mapping[str, object], *, residual_indices: frozenset[int]
) -> bool:
    """Whether a reviewer has something to look at on this slide.

    Either the page that shipped is not the page the deck authored, or something
    was measured on the page that did ship: the final quality gate recorded a
    residual issue for the slide, or the browser source readback could not see
    one of its source text blocks. A flag with none of those is the record of a
    repair attempt that was rejected while the authored slide shipped unchanged
    and clean -- history, and nothing a reviewer can act on.
    """

    if published_a_substitute(entry):
        return True
    index = entry.get("index")
    if isinstance(index, int) and not isinstance(index, bool) and index in residual_indices:
        return True
    if entry.get("sourceVisibilityBlockIds"):
        return True
    return SOURCE_VISIBILITY_REASON in _reasons_of(entry)


def classify_flagged_slides(
    flagged_slides: Sequence[Mapping[str, object]],
    *,
    residual_failures: Sequence[Mapping[str, object]] = (),
) -> list[dict[str, object]]:
    """Return the flags with a ``classification`` field, in their original order.

    Call this once the flag list is final -- after the browser source readback
    has merged its visibility sub-reasons, because a visibility failure is one
    of the two things that make a flag reviewable. Calling it earlier would
    classify a reviewable slide as history.
    """

    indices = _residual_indices(residual_failures)
    return [
        {
            **entry,
            CLASSIFICATION_KEY: (
                REVIEW_REQUIRED
                if needs_review(entry, residual_indices=indices)
                else REPAIR_NOT_ADMITTED
            ),
        }
        for entry in flagged_slides
        if isinstance(entry, Mapping)
    ]


def flag_pages(
    entries: Sequence[Mapping[str, object]],
    *,
    only_review_required: bool = False,
) -> list[dict[str, object]]:
    """The page shape the receipt has always carried: ``index`` and ``reasons``.

    ``only_review_required`` narrows it to the slides a reviewer must act on;
    without it the list is every flag, which is what the deck's own quality-pass
    and auto-repair accounting has always counted.
    """

    pages: list[dict[str, object]] = []
    for entry in entries:
        if only_review_required and entry.get(CLASSIFICATION_KEY) != REVIEW_REQUIRED:
            continue
        index = entry.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            continue
        reasons = list(dict.fromkeys(reason for reason in _reasons_of(entry) if reason))[:8]
        pages.append({"index": int(index), "reasons": reasons})
    return pages


def review_pages_for(
    classified_slides: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """The reviewer-facing page list: the slides with something to look at.

    Same shape the receipt has always carried, so a consumer reading
    ``reviewPages`` needs no change; the list is simply no longer padded with
    slides whose only story is that a repair attempt was rejected.
    """

    return flag_pages(classified_slides, only_review_required=True)


__all__ = [
    "CLASSIFICATION_KEY",
    "REPAIR_NOT_ADMITTED",
    "REVIEW_REQUIRED",
    "ORIGINAL_PRESERVED_OUTCOMES",
    "SOURCE_VISIBILITY_REASON",
    "SUBSTITUTE_REASON_PREFIX",
    "classify_flagged_slides",
    "flag_pages",
    "needs_review",
    "published_a_substitute",
    "review_pages_for",
]
