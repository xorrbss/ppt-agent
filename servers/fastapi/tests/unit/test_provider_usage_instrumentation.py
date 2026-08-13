from types import SimpleNamespace
import asyncio
import uuid

import pytest

from services.provider_usage_instrumentation import (
    cancel_provider_job,
    provider_call_admission,
    provider_runtime_job_status,
    provider_receipt_observability_snapshot,
    provider_usage_scope,
    response_usage,
)
from services.provider_runtime_control import (
    BudgetExceededError,
    CancellationRequested,
)


def test_response_usage_uses_only_standard_numeric_counters() -> None:
    response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=34)
    )

    assert response_usage(response) == ("reported", 12, 34)


def test_response_usage_missing_or_non_numeric_is_unavailable_not_zero() -> None:
    assert response_usage(SimpleNamespace()) == ("unavailable", None, None)
    assert response_usage({"usage": {"input_tokens": "12"}}) == (
        "unavailable",
        None,
        None,
    )


def test_receipt_sink_observability_is_scalar_only() -> None:
    snapshot = provider_receipt_observability_snapshot()
    assert set(snapshot) == {"attempted", "persisted", "failed"}
    assert all(isinstance(value, int) and value >= 0 for value in snapshot.values())


def test_actual_call_admission_enforces_budget_before_second_sdk_call(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        job_id = uuid.uuid4()
        monkeypatch.setenv("PROVIDER_JOB_ESTIMATED_BUDGET_MICROUNITS", "1")
        monkeypatch.setenv("PROVIDER_CALL_ESTIMATED_COST_MICROUNITS", "1")
        with provider_usage_scope(
            job_id=job_id,
            operation="generate",
            stage="author",
        ):
            async with provider_call_admission():
                pass
            with pytest.raises(BudgetExceededError):
                async with provider_call_admission():
                    pytest.fail("budget gate admitted a second SDK call")
        status = await provider_runtime_job_status(job_id)
        assert status["total_requests"] == 1
        assert status["estimated_consumed_microunits"] == 1

    asyncio.run(scenario())


def test_actual_call_admission_observes_job_cancellation(monkeypatch) -> None:
    async def scenario() -> None:
        job_id = uuid.uuid4()
        monkeypatch.delenv(
            "PROVIDER_JOB_ESTIMATED_BUDGET_MICROUNITS", raising=False
        )
        with provider_usage_scope(
            job_id=job_id,
            operation="generate",
            stage="image",
        ):
            async with provider_call_admission():
                pass
            await cancel_provider_job(job_id, "operator_cancelled")
            with pytest.raises(CancellationRequested):
                async with provider_call_admission():
                    pytest.fail("cancelled job reached the SDK boundary")

    asyncio.run(scenario())


def test_required_durable_admission_reserves_and_reconciles_before_sdk(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        calls = []
        durable_lease = object()

        async def reserve(**kwargs):
            calls.append(("reserve", kwargs))
            return durable_lease

        async def reconcile(lease, **kwargs):
            calls.append(("reconcile", lease, kwargs))

        monkeypatch.setattr(
            "services.provider_usage_instrumentation.durable_provider_runtime_required",
            lambda: True,
        )
        monkeypatch.setattr(
            "services.provider_usage_instrumentation.reserve_provider_call_detached",
            reserve,
        )
        monkeypatch.setattr(
            "services.provider_usage_instrumentation.reconcile_provider_call_detached",
            reconcile,
        )
        job_id = uuid.uuid4()
        with provider_usage_scope(
            job_id=job_id,
            operation="generate",
            stage="author",
        ):
            async with provider_call_admission(estimated_cost_microunits=7):
                calls.append(("sdk",))

        assert calls[0][0] == "reserve"
        assert calls[0][1]["job_id"] == job_id
        assert calls[0][1]["estimated_cost"] == 7
        assert calls[1] == ("sdk",)
        assert calls[2] == (
            "reconcile",
            durable_lease,
            {"actual_cost": None, "outcome": "success"},
        )

    asyncio.run(scenario())
