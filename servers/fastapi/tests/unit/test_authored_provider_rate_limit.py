from __future__ import annotations

import asyncio

import pytest

from utils.llm_calls import author_slide


def test_author_request_starts_are_spaced_by_configured_rpm(monkeypatch):
    author_slide._AUTHOR_RATE_STATES.clear()
    monkeypatch.setattr(author_slide, "AUTHOR_REQUESTS_PER_MINUTE", 60.0)
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(author_slide.asyncio, "sleep", fake_sleep)

    async def exercise() -> None:
        await author_slide._wait_for_author_rate_slot()
        await author_slide._wait_for_author_rate_slot()

    asyncio.run(exercise())

    assert sleeps == [pytest.approx(1.0, abs=0.05)]


def test_429_retry_after_is_shared_with_following_calls(monkeypatch):
    author_slide._AUTHOR_RATE_STATES.clear()
    monkeypatch.setattr(author_slide, "AUTHOR_REQUESTS_PER_MINUTE", 0.0)
    monkeypatch.setattr(author_slide, "AUTHOR_RATE_LIMIT_MAX_COOLDOWN_SECONDS", 60.0)
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(author_slide.asyncio, "sleep", fake_sleep)

    class RateLimited(RuntimeError):
        status_code = 429
        headers = {"Retry-After": "2.5"}

    async def exercise() -> None:
        author_slide._register_author_rate_limit(RateLimited("rate limited"))
        await author_slide._wait_for_author_rate_slot()

    asyncio.run(exercise())

    assert sleeps == [pytest.approx(2.5, abs=0.05)]


def test_non_rate_limit_error_does_not_create_cooldown(monkeypatch):
    author_slide._AUTHOR_RATE_STATES.clear()
    monkeypatch.setattr(author_slide, "AUTHOR_REQUESTS_PER_MINUTE", 0.0)
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(author_slide.asyncio, "sleep", fake_sleep)

    class Unavailable(RuntimeError):
        status_code = 503

    async def exercise() -> None:
        author_slide._register_author_rate_limit(Unavailable("temporarily unavailable"))
        await author_slide._wait_for_author_rate_slot()

    asyncio.run(exercise())

    assert sleeps == []
