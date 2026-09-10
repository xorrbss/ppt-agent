"""Ambient public-operation budget for authored local fitting."""
from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar


_DEADLINE: ContextVar[float | None] = ContextVar(
    "authored_local_fit_deadline", default=None
)


@contextmanager
def authored_local_fit_deadline_scope(
    remaining_seconds: float | None,
) -> Iterator[None]:
    deadline = (
        time.monotonic() + max(0.0, float(remaining_seconds))
        if remaining_seconds is not None
        else None
    )
    token = _DEADLINE.set(deadline)
    try:
        yield
    finally:
        _DEADLINE.reset(token)


def remaining_authored_local_fit_seconds(*, cap: float = 120.0) -> float:
    deadline = _DEADLINE.get()
    if deadline is None:
        return max(0.0, float(cap))
    return max(0.0, min(float(cap), deadline - time.monotonic()))


__all__ = [
    "authored_local_fit_deadline_scope",
    "remaining_authored_local_fit_seconds",
]
