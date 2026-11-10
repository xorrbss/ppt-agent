from __future__ import annotations

import asyncio

import pytest

from services.generation_core.authoring import author_call_runtime
from utils.provider_idempotency import ProviderOutcomeError


def test_author_request_starts_are_spaced_by_configured_rpm(monkeypatch):
    author_call_runtime._AUTHOR_RATE_STATES.clear()
    monkeypatch.setattr(author_call_runtime, "AUTHOR_REQUESTS_PER_MINUTE", 60.0)
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def exercise() -> None:
        await author_call_runtime._wait_for_author_rate_slot()
        await author_call_runtime._wait_for_author_rate_slot()

    asyncio.run(exercise())

    assert sleeps == [pytest.approx(1.0, abs=0.05)]


def test_provider_classified_overload_is_shared_with_following_calls(monkeypatch):
    author_call_runtime._AUTHOR_RATE_STATES.clear()
    monkeypatch.setattr(author_call_runtime, "AUTHOR_REQUESTS_PER_MINUTE", 0.0)
    monkeypatch.setattr(author_call_runtime, "AUTHOR_RATE_LIMIT_MAX_COOLDOWN_SECONDS", 60.0)
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    async def exercise() -> None:
        author_call_runtime._register_author_rate_limit(
            ProviderOutcomeError(
                "public_provider_overload",
                recovery_allowed=True,
                overload=True,
                cooldown_seconds=2.5,
            )
        )
        await author_call_runtime._wait_for_author_rate_slot()

    asyncio.run(exercise())

    assert sleeps == [pytest.approx(2.5, abs=0.05)]


def test_non_rate_limit_error_does_not_create_cooldown(monkeypatch):
    author_call_runtime._AUTHOR_RATE_STATES.clear()
    monkeypatch.setattr(author_call_runtime, "AUTHOR_REQUESTS_PER_MINUTE", 0.0)
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    class Unavailable(RuntimeError):
        status_code = 503

    async def exercise() -> None:
        author_call_runtime._register_author_rate_limit(Unavailable("temporarily unavailable"))
        await author_call_runtime._wait_for_author_rate_slot()

    asyncio.run(exercise())

    assert sleeps == []
