"""Ambient access to the absolute deadline pinned on the running operation.

``PresentationGenerationOperationHandler.execute`` already resolves the
operation's immutable ``deadline_at`` and wraps the whole pipeline in an
``asyncio.timeout`` built from it.  ``asyncio`` does not expose that deadline to
the code running underneath, so stages that need to size their own sub-budget
against what is left had nothing to read.

This module is that read.  The handler enters the scope once; any stage below
it asks how many seconds remain and gets ``None`` when it is not running under
a public operation at all (a local run, or the outline SSE stream).  Nothing
here can change the deadline -- it is pinned at admission and never moves.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

_OPERATION_DEADLINE: ContextVar[datetime | None] = ContextVar(
    "public_operation_deadline_at", default=None
)


@contextmanager
def public_operation_deadline_scope(
    deadline_at: datetime | None,
) -> Iterator[None]:
    """Publish ``deadline_at`` to everything running inside the block."""

    token = _OPERATION_DEADLINE.set(_as_utc(deadline_at))
    try:
        yield
    finally:
        _OPERATION_DEADLINE.reset(token)


def public_operation_deadline_at() -> datetime | None:
    """Return the pinned operation deadline, or ``None`` outside an operation."""

    return _OPERATION_DEADLINE.get()


def remaining_public_operation_seconds(*, now: datetime | None = None) -> float | None:
    """Return the seconds left on the pinned deadline, or ``None`` outside one.

    A deadline already in the past returns ``0.0`` rather than a negative
    number, so callers can treat the result as a non-negative allowance.
    """

    deadline = _OPERATION_DEADLINE.get()
    if deadline is None:
        return None
    reference = _as_utc(now) or datetime.now(timezone.utc)
    return max(0.0, (deadline - reference).total_seconds())


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


__all__ = [
    "public_operation_deadline_at",
    "public_operation_deadline_scope",
    "remaining_public_operation_seconds",
]
