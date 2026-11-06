"""P6: overload cooldown outside the provider permit, Retry-After, one deck deadline."""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

from services.generation_core.drivers.svg import author, collect
from services.generation_core.drivers.svg.collect import _RunState
from services.generation_core.runtime.deck_cooldown import DeckCooldown
from services.generation_core.runtime.profile import ProviderPermits
from utils.provider_overload import (
    BoundedProviderCall, ProviderOutcomeError, provider_retry_after_seconds,
)


class _Error(Exception):
    def __init__(self, headers=None, message="429 rate limit"):
        super().__init__(message)
        self.headers = headers
        self.status_code = 429


class _Reservation:
    pending = True

    def __call__(self, ordinal):
        return None

    def claim(self):
        return None


def test_retry_after_header_is_read_and_bounded():
    assert provider_retry_after_seconds(_Error({"Retry-After": "7"})) == 7.0
    assert provider_retry_after_seconds(_Error({"retry-after": "99999"})) == 300.0
    assert provider_retry_after_seconds(_Error({"retry-after": "-3"})) is None
    assert provider_retry_after_seconds(_Error({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"})) is None
    assert provider_retry_after_seconds(_Error(None, "Please try again in 12s")) == 12.0
    assert provider_retry_after_seconds(_Error(None)) is None


def test_bounded_provider_call_uses_retry_after_for_its_cooldown():
    sleeps, calls = [], []

    def run(_remaining):
        calls.append(1)
        if len(calls) == 1:
            raise _Error({"retry-after": "4"})
        return "ok"

    call = BoundedProviderCall(run, threading.Event(), idempotency_capable=True,
                               reserve_retry=_Reservation(), sleep=sleeps.append)
    assert call() == "ok"
    assert sleeps == [4.0]


def test_deck_cooldown_is_one_shared_deadline():
    async def run():
        cooldown = DeckCooldown()
        cooldown.register(10)
        cooldown.register(4)  # a later, shorter overload must not shorten or stack it
        cooldown.register(0)
        return cooldown.remaining()

    assert 9.0 < asyncio.run(run()) <= 10.0


def test_wait_observes_deadline_extended_by_another_page(monkeypatch):
    async def run():
        loop = asyncio.get_running_loop()
        now, sleeps = [0.0], []
        cooldown = DeckCooldown()

        async def sleep(delay):
            if not sleeps:
                now[0] += 10.0
                cooldown.register(120.0)
                now[0] -= 10.0
            sleeps.append(delay)
            now[0] += delay

        with monkeypatch.context() as patch:
            patch.setattr(loop, "time", lambda: now[0])
            patch.setattr(asyncio, "sleep", sleep)
            cooldown.register(30.0)
            slept = await cooldown.wait(jitter=2.0)
            return sleeps, slept, now[0]

    assert asyncio.run(run()) == ([32.0, 100.0], 132.0, 132.0)


def test_author_overload_registers_cooldown_and_does_not_sleep(monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(author.asyncio, "sleep", fake_sleep)

    async def provider(*, before_dispatch, **_kwargs):
        await before_dispatch()
        raise ProviderOutcomeError("public_provider_overload", recovery_allowed=True, overload=True,
                                   cooldown_seconds=90.0, cooldown_from_retry_after=True)

    monkeypatch.setattr("services.generation_core.authoring.author_slide_svg.author_slide_svg", provider)

    async def run():
        ctx = SimpleNamespace(manifests=[{}], snapshot={}, canonical_assets=False, source_reflow=False,
                              raster_reconstruction=False, physical_kwargs={}, overload_cooldown=DeckCooldown())
        result = await author.attempt_author(
            ctx=ctx, request=SimpleNamespace(instructions=None, _document_canvas=None), index=0,
            content="s", role="content", count=1, payload={"attempts": []}, ordinal=0, provider_ordinal=1,
            job=None, state=_RunState(), checkpoint=AsyncMock(), async_session_maker=None)
        return result, ctx.overload_cooldown.remaining()

    result, remaining = asyncio.run(run())
    assert result.retry and result.cooldown_seconds == 90.0  # Retry-After honoured, above the old 60 s cap
    assert 89.0 < remaining <= 90.0
    assert sleeps == []


def test_cooldown_does_not_hold_a_provider_permit():
    """A page sleeping its cooldown leaves the permit free for another page."""

    async def run():
        permits = ProviderPermits(1)
        cooldown = DeckCooldown()
        cooldown.register(0.2)
        order = []

        async def page_a():
            async with permits.slot(repair=False):
                order.append("a-authored-429")
            await collect._sleep_overload_cooldown(SimpleNamespace(overload_cooldown=cooldown), 0.2)
            order.append("a-retry")

        async def page_b():
            await asyncio.sleep(0.01)
            async with permits.slot(repair=False):
                order.append("b-authored")

        await asyncio.gather(page_a(), page_b())
        return order

    assert asyncio.run(run()) == ["a-authored-429", "b-authored", "a-retry"]
