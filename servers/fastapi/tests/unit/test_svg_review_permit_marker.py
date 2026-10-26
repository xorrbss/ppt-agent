"""A durable physical read marker distinguishes permit waits from purchases."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from services.generation_core.drivers.svg.quality import redispatchable_review_status
from tests.unit.test_svg_review_read_retries import _harness, _provider, _run


@pytest.mark.parametrize("permit_api", ["semaphore", "provider_permits"])
@pytest.mark.parametrize("attempt", [0, 1])
def test_crash_while_waiting_reuses_the_same_physical_read(monkeypatch, permit_api, attempt):
    harness = _harness(monkeypatch, 5)
    if attempt:
        _run(harness, _provider(harness))
        harness.groups["0"].update(status="review_failed")
        harness.groups["0"].pop("advisory")
    crashed = {}

    class WaitingPermit:
        async def __aenter__(self):
            crashed.update(deepcopy(harness.groups))
            raise asyncio.CancelledError()

        async def __aexit__(self, *args):
            pass

        def slot(self, *, repair):
            assert repair is False
            return self

    setattr(harness.ctx, permit_api, WaitingPermit())
    provider = AsyncMock(side_effect=_provider(harness))
    with pytest.raises(asyncio.CancelledError):
        _run(harness, provider)
    provider.assert_not_awaited()
    waiting = crashed["0"]
    assert waiting["status"] == "review_not_dispatched"
    assert redispatchable_review_status(waiting["status"])
    assert waiting["failureDiagnostics"] == {}
    assert waiting["review_attempt"] == attempt
    assert len(waiting["superseded_attempts"]) == attempt
    # Restore only the state durable before cancellation cleanup, as on reclaim.
    harness.groups.clear()
    harness.groups.update(crashed)
    delattr(harness.ctx, permit_api)
    assert _run(harness, provider)["vision"]["designGate"]["status"] == "passed"
    assert provider.await_count == 1
    assert harness.groups["0"]["review_attempt"] == attempt
    assert harness.groups["0"]["physical_ordinal"] == waiting["physical_ordinal"]
    assert harness.groups["0"]["superseded_attempts"] == waiting["superseded_attempts"]


def test_inflight_is_persisted_inside_the_acquired_permit(monkeypatch):
    harness = _harness(monkeypatch, 5)
    held = False
    writes = []
    checkpoint = harness.ctx.quality_recovery_review_group_checkpoint

    async def observe(ordinal, marker=None):
        if marker is not None:
            writes.append((marker["status"], held))
            if marker["status"] == "review_inflight":
                assert held
        return await checkpoint(ordinal, marker)

    class Permit:
        async def __aenter__(self):
            nonlocal held
            assert harness.groups["0"]["status"] == "review_not_dispatched"
            held = True

        async def __aexit__(self, *args):
            nonlocal held
            held = False

    reviewed = _provider(harness)

    async def provider(*args, **kwargs):
        assert held and harness.groups["0"]["status"] == "review_inflight"
        return await reviewed(*args, **kwargs)

    harness.ctx.semaphore = Permit()
    harness.ctx.quality_recovery_review_group_checkpoint = observe
    assert _run(harness, provider)["vision"]["designGate"]["status"] == "passed"
    assert ("review_not_dispatched", False) in writes
    assert ("review_inflight", True) in writes


@pytest.mark.parametrize("attempt", [0, 1])
def test_crash_after_dispatch_consumes_that_read_and_never_rebuys_it(monkeypatch, attempt):
    harness = _harness(monkeypatch, 5)
    if attempt:
        _run(harness, _provider(harness))
        harness.groups["0"].update(status="review_failed")
        harness.groups["0"].pop("advisory")
    crashed = {}

    async def interrupted(*args, **kwargs):
        crashed.update(deepcopy(harness.groups))
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        _run(harness, interrupted)
    assert crashed["0"]["status"] == "review_inflight"
    harness.groups.clear()
    harness.groups.update(crashed)
    provider = AsyncMock(side_effect=_provider(harness))
    result = _run(harness, provider)
    assert provider.await_count == (1 if attempt == 0 else 0)
    assert harness.groups["0"]["review_attempt"] == 1
    if attempt == 0:
        assert result["vision"]["designGate"]["status"] == "passed"
        assert harness.groups["0"]["superseded_attempts"][0]["status"] == "review_inflight"
    else:
        assert result["vision"]["designGate"]["status"] == "unreviewed"
