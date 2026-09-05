"""One backoff rule for every worker-lane poll loop.

Each idle poll of the worker lane costs a database round trip, and the
expensive ones (``claim_next_workspace_operation``,
``claim_next_workspace_outbox``) open ``BEGIN IMMEDIATE`` on SQLite, which
takes the single writer lock even when there is nothing to claim.  A 30-slide
deck spent thousands of those round trips contending with its own checkpoint
writes.

The rule here is deliberately small: exponential growth from a base interval up
to a ceiling while nothing happens, and an immediate reset the moment a poll
finds work.  Latency guarantees stay with the callers -- a loop that must react
within one second keeps polling at ``base`` with a cheap read and applies this
delay only to the expensive cycle.
"""

from __future__ import annotations

import asyncio

from utils.get_env_programme import get_workspace_poll_idle_ceiling_seconds


def next_poll_delay(idle_ticks: int, *, base: float, ceiling: float) -> float:
    """Seconds to wait before the next poll after ``idle_ticks`` empty polls.

    ``idle_ticks`` is the number of consecutive polls that found nothing; zero
    means the last poll did work, so the next one runs at ``base``.  The delay
    doubles per idle tick and is clamped to ``ceiling``.
    """

    safe_base = max(0.001, float(base))
    safe_ceiling = max(safe_base, float(ceiling))
    ticks = max(0, int(idle_ticks))
    if ticks == 0:
        return safe_base
    # Cap the exponent before multiplying so a long-idle worker cannot build a
    # float large enough to lose precision against the ceiling.
    exponent = min(ticks, 32)
    return min(safe_ceiling, safe_base * (2.0**exponent))


def idle_ceiling_seconds(base: float) -> float:
    """The configured idle ceiling, never below the loop's base interval."""

    return max(float(base), float(get_workspace_poll_idle_ceiling_seconds()))


class IdleCycle:
    """When a poll loop next has to pay for its expensive, write-locking cycle.

    The loop keeps ticking at its base interval and asks a cheap read whether
    anything is waiting; this object answers the other half of the question --
    "run the full cycle anyway, because the backed-off sweep is due" -- and
    resets the backoff the moment a cycle finds work.
    """

    __slots__ = ("base", "ceiling", "_idle_ticks", "_due_at")

    def __init__(self, base: float, *, ceiling: float | None = None) -> None:
        self.base = max(0.001, float(base))
        self.ceiling = idle_ceiling_seconds(self.base) if ceiling is None else float(ceiling)
        self._idle_ticks = 0
        self._due_at = 0.0

    def _now(self) -> float:
        return asyncio.get_running_loop().time()

    def due(self) -> bool:
        return self._now() >= self._due_at

    def record(self, did_work: object) -> None:
        self._idle_ticks = 0 if did_work else self._idle_ticks + 1
        self._due_at = self._now() + next_poll_delay(
            self._idle_ticks, base=self.base, ceiling=self.ceiling
        )


__all__ = ["IdleCycle", "idle_ceiling_seconds", "next_poll_delay"]
