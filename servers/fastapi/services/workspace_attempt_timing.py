"""Per-attempt queue wait and execution time on the operation record.

The operation row keeps ``created_at`` (queued), ``started_at`` (first claim
only) and ``completed_at`` (terminal only), so after a retry nobody can tell
how long an attempt waited in the queue from how long it ran -- the two
numbers a deadline-anchor decision needs.  The runner stamps this object onto
``error_detail`` (every failed attempt) and ``result`` (a runner-finalized
success) under ``attempt_timing``:

* ``queue_wait_seconds``: operation ``created_at`` -> this attempt's claim;
* ``execution_seconds``: this attempt's claim -> its terminal transition.

The deadline anchor itself is not changed here.

Known gap: a self-published success (export publication, generation or
quality review that publishes a revision) is terminalized by its publisher,
not by the runner, so it carries no ``attempt_timing``.  For a single attempt
the numbers are derivable from ``created_at``/``started_at``/``completed_at``;
for a retried success the last attempt's claim time is lost.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from services.workspace_operation_handler_registry import WorkspaceOperationClaim
from utils.datetime_utils import as_utc

ATTEMPT_TIMING_KEY = "attempt_timing"
_TIMESTAMP_KEYS = ("queued_at", "attempt_claimed_at", "attempt_finished_at")
_SECONDS_KEYS = ("queue_wait_seconds", "execution_seconds")


def _iso(value: datetime | None) -> str | None:
    return as_utc(value).isoformat() if value is not None else None


def _seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round((as_utc(end) - as_utc(start)).total_seconds(), 3)


def attempt_timing(
    claim: WorkspaceOperationClaim,
    *,
    queued_at: datetime | None,
    finished_at: datetime,
) -> dict[str, Any]:
    claimed_at = claim.claimed_at
    return {
        "attempt_number": claim.attempt_number,
        "queued_at": _iso(queued_at),
        "attempt_claimed_at": _iso(claimed_at),
        "attempt_finished_at": _iso(finished_at),
        "queue_wait_seconds": _seconds(queued_at, claimed_at),
        "execution_seconds": _seconds(claimed_at, finished_at),
    }


def with_attempt_timing(
    payload: Mapping[str, Any] | None,
    claim: WorkspaceOperationClaim,
    *,
    queued_at: datetime | None,
    finished_at: datetime,
) -> dict[str, Any]:
    return {
        **(dict(payload) if isinstance(payload, Mapping) else {}),
        ATTEMPT_TIMING_KEY: attempt_timing(claim, queued_at=queued_at, finished_at=finished_at),
    }


def sanitize_attempt_timing(value: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only the known fields with their exact scalar types."""

    safe: dict[str, Any] = {}
    number = value.get("attempt_number")
    if isinstance(number, int) and not isinstance(number, bool) and number >= 0:
        safe["attempt_number"] = number
    for key in _TIMESTAMP_KEYS:
        stamp = value.get(key)
        safe[key] = stamp[:40] if isinstance(stamp, str) else None
    for key in _SECONDS_KEYS:
        seconds = value.get(key)
        safe[key] = (
            float(seconds)
            if isinstance(seconds, (int, float))
            and not isinstance(seconds, bool)
            and math.isfinite(seconds)
            else None
        )
    return safe


__all__ = [
    "ATTEMPT_TIMING_KEY",
    "attempt_timing",
    "sanitize_attempt_timing",
    "with_attempt_timing",
]
