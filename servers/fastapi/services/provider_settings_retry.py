import asyncio
import random
from collections.abc import Awaitable, Callable


PROVIDER_SETTINGS_CAS_MAX_ATTEMPTS = 8
PROVIDER_SETTINGS_CAS_BASE_DELAY_SECONDS = 0.005
PROVIDER_SETTINGS_CAS_MAX_DELAY_SECONDS = 0.25
_JITTER_RATIO = 0.25


def provider_settings_retry_delay_seconds(
    attempt: int,
    *,
    jitter_source: Callable[[], float] = random.random,
) -> float:
    """Return capped exponential delay with bounded injectable jitter."""
    jitter = max(0.0, min(float(jitter_source()), 1.0))
    base = min(
        PROVIDER_SETTINGS_CAS_BASE_DELAY_SECONDS * (2**attempt),
        PROVIDER_SETTINGS_CAS_MAX_DELAY_SECONDS / (1.0 + _JITTER_RATIO),
    )
    return min(
        base * (1.0 + (_JITTER_RATIO * jitter)),
        PROVIDER_SETTINGS_CAS_MAX_DELAY_SECONDS,
    )


async def provider_settings_retry_pause(
    attempt: int,
    *,
    jitter_source: Callable[[], float] = random.random,
    sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    await sleeper(
        provider_settings_retry_delay_seconds(
            attempt,
            jitter_source=jitter_source,
        )
    )
