"""One overload cooldown per deck, slept outside any provider permit."""
from __future__ import annotations

import asyncio
import random

#: Extra spread (seconds, at most) added after the shared deadline so pages that
#: waited on the same cooldown do not all re-dispatch on the same instant.
MAX_RETRY_JITTER_SECONDS = 5.0


def retry_jitter(cooldown_seconds: float) -> float:
    return random.uniform(0.0, min(MAX_RETRY_JITTER_SECONDS, 0.1 * max(0.0, cooldown_seconds)))


class DeckCooldown:
    """Deck-scoped cooldown deadline shared by every page of one attempt.

    A page that hit an overload registers its cooldown; the deadline only moves
    forward (``max``), so N concurrent overloads do not stack N cooldowns, and
    every page backing off waits for the same deadline instead of its own
    private timer. Waiting holds no provider permit.
    """

    def __init__(self) -> None:
        self._deadline = 0.0

    def register(self, seconds: float) -> None:
        now = asyncio.get_running_loop().time()
        self._deadline = max(self._deadline, now + max(0.0, float(seconds)))

    def remaining(self) -> float:
        return max(0.0, self._deadline - asyncio.get_running_loop().time())

    async def wait(self, *, jitter: float = 0.0) -> float:
        """Sleep until the shared deadline (plus ``jitter``); return the slept seconds."""
        delay = self.remaining() + max(0.0, jitter)
        if delay > 0.0:
            await asyncio.sleep(delay)
        return delay


__all__ = ["DeckCooldown", "MAX_RETRY_JITTER_SECONDS", "retry_jitter"]
