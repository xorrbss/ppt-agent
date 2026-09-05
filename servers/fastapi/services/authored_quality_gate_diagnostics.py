"""Why the QualityReceipt v2 unified gate refused to publish a deck.

Bench run 24 ended on ``authored_quality_receipt_gate_failed`` with nothing in
the log but the code, so the failing check could only be guessed at from the
deck. The receipt already knows: ``QualityReceiptV2.blockers`` names every
failed domain and ``statuses`` names each domain's verdict. The per-slide
capacity checks the caller built are the other half -- they say WHICH slides
carry the violation -- so they are summarised here alongside.

Everything here is content-free: check ids, statuses and slide indices only.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

LOGGER = logging.getLogger(__name__)

#: Statuses that are not a reason to look at a domain.
_UNREMARKABLE = frozenset({"passed", "not_applicable"})

#: Bench evidence stays readable: a 90-slide deck must not log 90 indices.
_MAX_SLIDES = 12
_MAX_VIOLATIONS = 8


def capacity_blocked_slide_indices(
    capacity_checks: Mapping[int, Mapping[str, object]],
) -> list[int]:
    """Slide indices whose capacity evidence is unobserved or failing."""

    blocked: list[int] = []
    for index, check in sorted(capacity_checks.items()):
        checks = check.get("checks")
        failed = isinstance(checks, Mapping) and not all(
            bool(value) for value in checks.values()
        )
        if check.get("browserObserved") is not True or failed:
            blocked.append(int(index))
    return blocked


def quality_receipt_gate_failure(
    receipt: Any,
    *,
    capacity_checks: Mapping[int, Mapping[str, object]] | None = None,
    violations: Sequence[object] = (),
) -> dict[str, object]:
    """One content-free summary of the blocked receipt, for log and message."""

    statuses = getattr(receipt, "statuses", None)
    summary: dict[str, object] = {
        "blockers": list(getattr(receipt, "blockers", ()) or ()),
        "statuses": {
            name: status
            for name, status in (statuses or {}).items()
            if status not in _UNREMARKABLE
        },
        "blockedSlides": capacity_blocked_slide_indices(capacity_checks or {})[
            :_MAX_SLIDES
        ],
        "violations": [str(item) for item in violations][:_MAX_VIOLATIONS],
    }
    return summary


def log_quality_receipt_gate_failure(summary: Mapping[str, object]) -> str:
    """Emit the one structured line and return the message for the raised error."""

    LOGGER.error(
        "[authored] authored_quality_receipt_gate_failed blockers=%s statuses=%s "
        "blocked_slides=%s violations=%s",
        summary.get("blockers"),
        summary.get("statuses"),
        summary.get("blockedSlides"),
        summary.get("violations"),
    )
    return (
        "QualityReceipt v2 unified gate blocked publication"
        f" blockers={summary.get('blockers')}"
        f" slides={summary.get('blockedSlides')}"
    )


__all__ = [
    "capacity_blocked_slide_indices",
    "log_quality_receipt_gate_failure",
    "quality_receipt_gate_failure",
]
