"""Review budgets count possible dispatches, never a proven local wait."""

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from services import provider_usage_instrumentation as usage
from services.generation_core.drivers.svg import quality
from services.svg_final_selected_continuation import FinalSelectedEvidenceError
from services.svg_source_local_review_continuation import _review_physical_ordinal
from tests.unit.test_svg_review_read_retries import _harness, _provider, _run, _seal_snapshot
from utils.provider_overload import ProviderOutcomeError, ProviderQuotaPause


def test_cancelled_overload_backoff_keeps_the_undispatched_retry(monkeypatch):
    harness = _harness(monkeypatch, 5)

    async def cancel_backoff(seconds):
        assert seconds == 60
        assert harness.groups["0"]["status"] == "review_failed"
        assert harness.groups["0"]["review_attempt"] == 0
        raise asyncio.CancelledError()

    monkeypatch.setattr(quality.asyncio, "sleep", cancel_backoff)
    provider = AsyncMock(side_effect=ProviderOutcomeError(
        "public_provider_result_unknown", recovery_allowed=False, overload=True, cooldown_seconds=60))
    with pytest.raises(asyncio.CancelledError):
        _run(harness, provider)
    assert provider.await_count == 1
    monkeypatch.setattr(quality.asyncio, "sleep", AsyncMock())
    provider = AsyncMock(side_effect=_provider(harness))
    assert _run(harness, provider)["vision"]["designGate"]["status"] == "passed"
    assert provider.await_count == 1
    assert harness.groups["0"]["review_attempt"] == 1


@pytest.mark.parametrize("attempt", [0, 1])
@pytest.mark.parametrize("error", [asyncio.CancelledError(), ProviderQuotaPause(reset_at=10, observed_at=0)])
def test_proven_admission_stop_reuses_the_same_slot(monkeypatch, attempt, error):
    harness = _harness(monkeypatch, 5)
    if attempt:
        _run(harness, _provider(harness))
        harness.groups["0"].update(status="review_failed")
        harness.groups["0"].pop("advisory")

    monkeypatch.setattr(usage, "ensure_runtime_job", AsyncMock(side_effect=error))

    async def stopped(*args, **kwargs):
        async with usage.provider_call_admission():
            pytest.fail("SDK boundary must not be reached")

    with pytest.raises(type(error)):
        _run(harness, stopped)
    saved = deepcopy(harness.groups["0"])
    assert saved["status"] == "review_not_dispatched"
    assert saved["review_attempt"] == attempt
    for _ in range(2):
        with pytest.raises(type(error)):
            _run(harness, stopped)
        assert harness.groups["0"] == saved
    provider = AsyncMock(side_effect=_provider(harness))
    assert _run(harness, provider)["vision"]["designGate"]["status"] == "passed"
    assert provider.await_count == 1
    assert harness.groups["0"]["review_attempt"] == attempt


def test_cancelled_permit_wait_does_not_spend_a_review_slot(monkeypatch):
    harness = _harness(monkeypatch, 5)

    class CancelledPermit:
        async def __aenter__(self):
            raise asyncio.CancelledError()

        async def __aexit__(self, *args):
            pass

    harness.ctx.semaphore = CancelledPermit()
    provider = AsyncMock(side_effect=_provider(harness))
    with pytest.raises(asyncio.CancelledError):
        _run(harness, provider)
    provider.assert_not_awaited()
    assert harness.groups["0"]["status"] == "review_not_dispatched"
    del harness.ctx.semaphore
    assert _run(harness, provider)["vision"]["designGate"]["status"] == "passed"
    assert harness.groups["0"]["review_attempt"] == 0


def test_dispatch_observation_is_conservative_after_the_sdk_boundary():
    async def scenario():
        with usage.observe_provider_dispatch() as observation:
            with pytest.raises(asyncio.CancelledError):
                async with usage.provider_call_admission():
                    assert observation.sdk_may_have_started
                    raise asyncio.CancelledError()
            assert observation.admission_entered and observation.sdk_may_have_started
        assert usage._DISPATCH_OBSERVATION.get() is None

    asyncio.run(scenario())


@pytest.mark.parametrize("final_selected", [False, True])
def test_global_schema_retry_feedback_survives_resume_within_sealed_budget(monkeypatch, final_selected):
    harness = _harness(monkeypatch, 7)
    reviewed = _provider(harness)
    rejection = dict(skipped="design_review_schema_or_coverage_invalid",
                     schemaRejectionReason="global_review_fields", schemaRejectionFields=["deckFlow"])
    contexts = []

    async def provider(*args, **kwargs):
        context = kwargs["design_context"]
        if context["reviewScope"] == "global":
            contexts.append(deepcopy(context))
            if len(contexts) == 1:
                return rejection
            raise asyncio.CancelledError()
        return await reviewed(*args, **kwargs)

    # Interrupt before the retry is dispatched, leaving the schema rejection sealed.
    checkpoint = harness.ctx.quality_recovery_review_group_checkpoint
    async def interrupt(ordinal, marker=None):
        if (marker and marker.get("review_scope") == "global" and marker.get("review_attempt") == 1
                and marker.get("status") == "review_inflight"):
            raise asyncio.CancelledError()
        return await checkpoint(ordinal, marker)

    harness.ctx.quality_recovery_review_group_checkpoint = interrupt
    with pytest.raises(asyncio.CancelledError):
        _run(harness, provider, final_selected=final_selected)
    harness.ctx.quality_recovery_review_group_checkpoint = checkpoint

    async def resumed(*args, **kwargs):
        if kwargs["design_context"]["reviewScope"] == "global":
            contexts.append(deepcopy(kwargs["design_context"]))
        return await reviewed(*args, **kwargs)

    _run(harness, resumed, final_selected=final_selected)
    assert len(contexts) == 2
    assert contexts[1]["formatRetry"] == dict(attempt=1, reason="global_review_fields", fields=["deckFlow"])
    assert harness.groups["2"]["review_attempt"] == 1
    assert len(harness.groups["2"]["superseded_attempts"]) == 1


@pytest.mark.parametrize("mutation", ["missing_attempt", "bad_attempt", "bad_plan"])
def test_source_local_boundary_translates_invalid_plan_or_attempt(monkeypatch, mutation):
    harness = _harness(monkeypatch, 7)
    marker = {"review_attempt": 0}
    if mutation == "missing_attempt":
        marker.clear()
    elif mutation == "bad_attempt":
        marker["review_attempt"] = 2
    else:
        harness.snapshot["quality_recovery_plan"]["review_retry_limit"] = 2
        _seal_snapshot(harness.snapshot)
    with pytest.raises(FinalSelectedEvidenceError) as caught:
        _review_physical_ordinal(marker, 0, harness.snapshot)
    assert isinstance(caught.value.__cause__, ValueError)
