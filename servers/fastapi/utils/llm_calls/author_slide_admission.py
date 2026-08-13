"""Process-wide rate admission state for authored slide provider calls."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from weakref import WeakKeyDictionary


@dataclass
class AuthorRateState:
    lock: asyncio.Lock
    next_allowed: float = 0.0


AuthorRateStates = WeakKeyDictionary[asyncio.AbstractEventLoop, AuthorRateState]


def rate_state(states: AuthorRateStates) -> AuthorRateState:
    loop = asyncio.get_running_loop()
    state = states.get(loop)
    if state is None:
        state = AuthorRateState(lock=asyncio.Lock())
        states[loop] = state
    return state


async def wait_for_rate_slot(
    states: AuthorRateStates,
    requests_per_minute: float,
) -> None:
    """Space provider call starts and honor a process-wide 429 cooldown."""
    state = rate_state(states)
    interval = 60.0 / requests_per_minute if requests_per_minute > 0 else 0.0
    async with state.lock:
        loop = asyncio.get_running_loop()
        now = loop.time()
        delay = max(0.0, state.next_allowed - now)
        if delay:
            await asyncio.sleep(delay)
            now = max(loop.time(), state.next_allowed)
        state.next_allowed = max(now, state.next_allowed) + interval


def retry_after_seconds(error: BaseException) -> float | None:
    headers = getattr(error, "headers", None)
    response = getattr(error, "response", None)
    if headers is None and response is not None:
        headers = getattr(response, "headers", None)
    if headers is not None:
        try:
            raw = headers.get("retry-after") or headers.get("Retry-After")
            if raw is not None:
                return max(0.0, float(raw))
        except (AttributeError, TypeError, ValueError):
            pass
    match = re.search(
        r"(?:retry|try)\s+again\s+(?:in|after)\s+([\d.]+)\s*s",
        str(error),
        re.IGNORECASE,
    )
    if match:
        try:
            return max(0.0, float(match.group(1)))
        except ValueError:
            return None
    return None


def register_rate_limit(
    error: BaseException,
    states: AuthorRateStates,
    default_cooldown_seconds: float,
    max_cooldown_seconds: float,
) -> None:
    status_code = getattr(error, "status_code", None)
    response = getattr(error, "response", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)
    if status_code != 429 and "429" not in str(error):
        return
    retry_after = retry_after_seconds(error)
    cooldown = (
        retry_after if retry_after is not None else default_cooldown_seconds
    )
    cooldown = min(cooldown, max_cooldown_seconds)
    loop = asyncio.get_running_loop()
    state = rate_state(states)
    state.next_allowed = max(state.next_allowed, loop.time() + cooldown)


__all__ = [
    "AuthorRateState",
    "AuthorRateStates",
    "rate_state",
    "register_rate_limit",
    "retry_after_seconds",
    "wait_for_rate_slot",
]
