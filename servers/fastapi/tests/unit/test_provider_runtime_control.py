from __future__ import annotations

import asyncio
import copy

import pytest

from services.provider_runtime_control import (
    BackpressureError,
    BudgetExceededError,
    CancellationRequested,
    CircuitOpenError,
    JobKillConditions,
    ProviderRuntimeControl,
    RetryAmplificationError,
    RuntimeControlError,
    RuntimeLimits,
)


async def _registered(
    limits: RuntimeLimits,
    *,
    estimated_budget: int = 10_000,
    actual_budget: int = 10_000,
    kill_conditions: JobKillConditions | None = None,
) -> ProviderRuntimeControl:
    control = ProviderRuntimeControl(limits)
    await control.register_job(
        "job-1",
        estimated_budget_microunits=estimated_budget,
        actual_budget_microunits=actual_budget,
        kill_conditions=kill_conditions,
    )
    return control


def test_bounded_queue_backpressure_and_cancellation_release_reservations() -> None:
    async def scenario() -> None:
        control = await _registered(
            RuntimeLimits(
                max_queue_size=1,
                max_concurrency=1,
                requests_per_minute=100,
            )
        )
        active = await control.admit("job-1", "request-1", estimated_cost_microunits=100)
        queued = asyncio.create_task(control.admit("job-1", "request-2", estimated_cost_microunits=200))
        await asyncio.sleep(0)
        with pytest.raises(BackpressureError, match="queue_full"):
            await control.admit("job-1", "request-3", estimated_cost_microunits=300)

        await control.cancel_job("job-1")
        with pytest.raises(CancellationRequested, match="operator_cancelled"):
            await queued
        status = await control.job_status("job-1")
        assert status["estimated_reserved_microunits"] == 0
        await active.finish(actual_cost_microunits=90, outcome="success")

    asyncio.run(scenario())


def test_concurrency_and_rpm_admission_are_both_enforced() -> None:
    async def scenario() -> None:
        control = await _registered(
            RuntimeLimits(
                max_queue_size=3,
                max_concurrency=1,
                requests_per_minute=2,
                rpm_window_seconds=0.04,
            )
        )
        first = await control.admit("job-1", "request-1", estimated_cost_microunits=10)
        second_task = asyncio.create_task(control.admit("job-1", "request-2", estimated_cost_microunits=10))
        await asyncio.sleep(0.005)
        assert not second_task.done()
        await first.finish(actual_cost_microunits=10, outcome="success")
        second = await asyncio.wait_for(second_task, timeout=0.1)
        await second.finish(actual_cost_microunits=10, outcome="success")

        third_task = asyncio.create_task(control.admit("job-1", "request-3", estimated_cost_microunits=10))
        await asyncio.sleep(0.005)
        assert not third_task.done(), "RPM window must delay an otherwise free slot"
        third = await asyncio.wait_for(third_task, timeout=0.15)
        await third.finish(actual_cost_microunits=10, outcome="success")

    asyncio.run(scenario())


def test_estimated_and_actual_budgets_and_unavailable_cost_are_distinct() -> None:
    async def scenario() -> None:
        control = await _registered(
            RuntimeLimits(requests_per_minute=100),
            estimated_budget=100,
            actual_budget=80,
        )
        first = await control.admit("job-1", "request-1", estimated_cost_microunits=60)
        await first.finish(actual_cost_microunits=None, outcome="success")
        status = await control.job_status("job-1")
        assert status["actual_consumed_microunits"] == 0
        assert status["actual_cost_unavailable_count"] == 1

        second = await control.admit("job-1", "request-2", estimated_cost_microunits=40)
        with pytest.raises(BudgetExceededError) as error:
            await second.finish(actual_cost_microunits=81, outcome="success")
        assert error.value.kind == "actual"
        with pytest.raises(CancellationRequested, match="actual_budget_exceeded"):
            await control.admit("job-1", "request-3", estimated_cost_microunits=0)

        estimated = await _registered(
            RuntimeLimits(requests_per_minute=100),
            estimated_budget=50,
        )
        with pytest.raises(BudgetExceededError) as estimated_error:
            await estimated.admit("job-1", "request-too-expensive", estimated_cost_microunits=51)
        assert estimated_error.value.kind == "estimated"

    asyncio.run(scenario())


def test_retry_amplification_ceiling_and_kill_conditions() -> None:
    async def scenario() -> None:
        control = await _registered(
            RuntimeLimits(
                requests_per_minute=100,
                retry_amplification_ceiling=2,
            ),
            kill_conditions=JobKillConditions(max_consecutive_failures=2),
        )
        initial = await control.admit("job-1", "request-initial", estimated_cost_microunits=10)
        await initial.finish(actual_cost_microunits=10, outcome="failure")
        retry = await control.admit(
            "job-1",
            "request-retry-1",
            estimated_cost_microunits=10,
            is_retry=True,
        )
        await retry.finish(actual_cost_microunits=10, outcome="failure")
        with pytest.raises(CancellationRequested, match="max_consecutive_failures"):
            await control.admit(
                "job-1",
                "request-retry-2",
                estimated_cost_microunits=10,
                is_retry=True,
            )

        amplification = await _registered(
            RuntimeLimits(
                requests_per_minute=100,
                retry_amplification_ceiling=2,
            )
        )
        lease = await amplification.admit("job-1", "initial", estimated_cost_microunits=1)
        await lease.finish(actual_cost_microunits=1, outcome="success")
        lease = await amplification.admit("job-1", "retry-1", estimated_cost_microunits=1, is_retry=True)
        await lease.finish(actual_cost_microunits=1, outcome="failure")
        with pytest.raises(RetryAmplificationError):
            await amplification.admit("job-1", "retry-2", estimated_cost_microunits=1, is_retry=True)

    asyncio.run(scenario())


def test_deadline_kill_condition_cancels_before_provider_admission() -> None:
    async def scenario() -> None:
        control = await _registered(
            RuntimeLimits(requests_per_minute=100),
            kill_conditions=JobKillConditions(deadline_seconds=0.01),
        )
        await asyncio.sleep(0.02)
        with pytest.raises(CancellationRequested, match="deadline_exceeded"):
            await control.admit("job-1", "after-deadline", estimated_cost_microunits=1)
        status = await control.runtime_status()
        assert status["queue_depth"] == 0
        assert status["in_flight"] == 0

    asyncio.run(scenario())


def test_circuit_breaker_opens_and_allows_one_half_open_probe() -> None:
    async def scenario() -> None:
        control = await _registered(
            RuntimeLimits(
                requests_per_minute=100,
                circuit_failure_threshold=2,
                circuit_reset_seconds=0.03,
            )
        )
        for index in range(2):
            lease = await control.admit("job-1", f"failure-{index}", estimated_cost_microunits=1)
            await lease.finish(actual_cost_microunits=1, outcome="retryable_failure")
        with pytest.raises(CircuitOpenError):
            await control.admit("job-1", "blocked", estimated_cost_microunits=1)

        await asyncio.sleep(0.04)
        probe = await control.admit("job-1", "probe", estimated_cost_microunits=1)
        queued_probe = asyncio.create_task(control.admit("job-1", "queued-probe", estimated_cost_microunits=1))
        await asyncio.sleep(0.005)
        assert not queued_probe.done()
        await probe.finish(actual_cost_microunits=1, outcome="success")
        admitted = await asyncio.wait_for(queued_probe, timeout=0.1)
        await admitted.finish(actual_cost_microunits=1, outcome="success")

    asyncio.run(scenario())


def test_preexisting_inflight_success_does_not_close_an_open_circuit() -> None:
    async def scenario() -> None:
        control = await _registered(
            RuntimeLimits(
                max_concurrency=2,
                requests_per_minute=100,
                circuit_failure_threshold=1,
                circuit_reset_seconds=0.05,
            )
        )
        failing = await control.admit("job-1", "failing-call", estimated_cost_microunits=1)
        preexisting = await control.admit("job-1", "preexisting-call", estimated_cost_microunits=1)
        await failing.finish(actual_cost_microunits=1, outcome="retryable_failure")
        await preexisting.finish(actual_cost_microunits=1, outcome="success")
        with pytest.raises(CircuitOpenError):
            await control.admit("job-1", "must-remain-blocked", estimated_cost_microunits=1)

    asyncio.run(scenario())


def test_snapshot_restore_is_resume_safe_and_rejects_extra_private_fields() -> None:
    async def scenario() -> None:
        control = await _registered(
            RuntimeLimits(
                max_concurrency=1,
                requests_per_minute=100,
                retry_amplification_ceiling=2,
            ),
            estimated_budget=100,
            actual_budget=100,
        )
        in_flight = await control.admit("job-1", "not-persisted", estimated_cost_microunits=40)
        queued = asyncio.create_task(control.admit("job-1", "also-not-persisted", estimated_cost_microunits=50))
        await asyncio.sleep(0)
        snapshot = await control.snapshot()
        assert "not-persisted" not in repr(snapshot)
        assert "also-not-persisted" not in repr(snapshot)

        restored = ProviderRuntimeControl.restore(snapshot)
        status = await restored.job_status("job-1")
        assert status["estimated_consumed_microunits"] == 40
        assert status["estimated_reserved_microunits"] == 0
        assert status["total_requests"] == 1
        assert status["actual_cost_unavailable_count"] == 1
        resumed = await restored.admit(
            "job-1",
            "resume-retry",
            estimated_cost_microunits=50,
            is_retry=True,
        )
        await resumed.finish(actual_cost_microunits=45, outcome="success")
        resumed_status = await restored.job_status("job-1")
        assert resumed_status["estimated_consumed_microunits"] == 90
        assert resumed_status["actual_consumed_microunits"] == 45

        poisoned = copy.deepcopy(snapshot)
        poisoned["jobs"][0]["prompt"] = "private customer content"
        with pytest.raises(ValueError, match="allowlist"):
            ProviderRuntimeControl.restore(poisoned)

        await control.cancel_job("job-1")
        with pytest.raises(CancellationRequested):
            await queued
        await in_flight.finish(actual_cost_microunits=40, outcome="success")

    asyncio.run(scenario())


def test_restore_of_closed_circuit_does_not_serialize_admission() -> None:
    async def scenario() -> None:
        control = await _registered(RuntimeLimits(max_concurrency=2, requests_per_minute=100))
        snapshot = await control.snapshot()
        assert snapshot["circuit"]["state"] == "closed"
        restored = ProviderRuntimeControl.restore(snapshot)
        first = await restored.admit("job-1", "parallel-1", estimated_cost_microunits=1)
        second = await asyncio.wait_for(
            restored.admit("job-1", "parallel-2", estimated_cost_microunits=1),
            timeout=0.05,
        )
        await first.finish(actual_cost_microunits=1, outcome="success")
        await second.finish(actual_cost_microunits=1, outcome="success")

    asyncio.run(scenario())


def test_lease_is_exactly_once_and_identifiers_reject_secret_bearing_text() -> None:
    async def scenario() -> None:
        control = await _registered(RuntimeLimits(requests_per_minute=100))
        with pytest.raises(ValueError, match="safe identifier"):
            await control.admit(
                "job-1",
                "Authorization: Bearer secret",
                estimated_cost_microunits=1,
            )
        with pytest.raises(ValueError, match="safe code"):
            await control.cancel_job("job-1", "api_key=secret")
        lease = await control.admit("job-1", "request-once", estimated_cost_microunits=1)
        await lease.finish(actual_cost_microunits=1, outcome="success")
        with pytest.raises(RuntimeControlError, match="already_finished"):
            await lease.finish(actual_cost_microunits=1, outcome="success")

    asyncio.run(scenario())
