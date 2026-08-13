"""Strict JSON-safe snapshot codec for process-local provider admission."""

from __future__ import annotations

from dataclasses import asdict
import time
from typing import Any, Callable

from services.provider_runtime_primitives import (
    CancellationToken,
    JobKillConditions,
    RuntimeLimits,
    SNAPSHOT_VERSION,
    optional_nonnegative_int,
    require_dict,
    require_keys,
    required_nonnegative_int,
    required_nonnegative_number,
    validate_identifier,
    validate_reason,
)


async def snapshot_control(control: Any) -> dict[str, object]:
    """Return a scalar-only snapshot without queued request payloads."""

    async with control._condition:
        now = control._clock()
        control._prune_rpm(now)
        jobs: list[dict[str, object]] = []
        for job_id in sorted(control._jobs):
            job = control._jobs[job_id]
            jobs.append(
                {
                    "job_id": job.job_id,
                    "estimated_limit": job.estimated_limit,
                    "actual_limit": job.actual_limit,
                    "kill_conditions": {
                        "max_total_requests": job.kill_conditions.max_total_requests,
                        "max_retry_requests": job.kill_conditions.max_retry_requests,
                        "max_consecutive_failures": (
                            job.kill_conditions.max_consecutive_failures
                        ),
                    },
                    "deadline_remaining_seconds": (
                        max(0.0, job.deadline_at - now)
                        if job.deadline_at is not None
                        else None
                    ),
                    "cancel_reason": job.token.reason,
                    "estimated_consumed": job.estimated_consumed,
                    "actual_consumed": job.actual_consumed,
                    "actual_cost_unavailable_count": job.actual_cost_unavailable_count,
                    "total_requests": job.total_requests,
                    "initial_requests": job.initial_requests,
                    "retry_requests": job.retry_requests,
                    "consecutive_failures": job.consecutive_failures,
                    "uncertain_inflight_requests": control._in_flight_by_job.get(
                        job_id, 0
                    ),
                }
            )
        return {
            "version": SNAPSHOT_VERSION,
            "limits": asdict(control.limits),
            "circuit": {
                "state": control._circuit_snapshot_state(now),
                "consecutive_failures": control._circuit_failures,
                "open_remaining_seconds": max(
                    0.0, control._circuit_open_until - now
                ),
            },
            "rpm_ages_seconds": [now - stamp for stamp in control._rpm_timestamps],
            "jobs": jobs,
        }


def restore_control(
    control_type: Any,
    snapshot: dict[str, object],
    *,
    clock: Callable[[], float] = time.monotonic,
) -> Any:
    """Restore process-local counters conservatively after process loss."""

    from services.provider_runtime_control import _JobState

    require_keys(snapshot, {"version", "limits", "circuit", "rpm_ages_seconds", "jobs"})
    if snapshot["version"] != SNAPSHOT_VERSION:
        raise ValueError("unsupported runtime snapshot version")
    limits_data = require_dict(snapshot["limits"], "limits")
    require_keys(limits_data, set(RuntimeLimits.__dataclass_fields__))
    limits = RuntimeLimits(**limits_data)
    control = control_type(limits, clock=clock)
    now = clock()

    circuit = require_dict(snapshot["circuit"], "circuit")
    require_keys(circuit, {"state", "consecutive_failures", "open_remaining_seconds"})
    control._circuit_failures = required_nonnegative_int(
        circuit["consecutive_failures"], "circuit.consecutive_failures"
    )
    open_remaining = required_nonnegative_number(
        circuit["open_remaining_seconds"], "circuit.open_remaining_seconds"
    )
    state = circuit["state"]
    if state == "closed":
        if open_remaining != 0:
            raise ValueError("closed circuit cannot have remaining open time")
        control._circuit_open_until = 0.0
    elif state == "open":
        if open_remaining <= 0:
            raise ValueError("open circuit must have remaining open time")
        control._circuit_open_until = now + open_remaining
    elif state == "half_open_ready":
        if open_remaining != 0:
            raise ValueError("half-open circuit cannot have remaining open time")
        control._circuit_open_until = now
    elif state == "half_open_inflight":
        control._circuit_open_until = now + limits.circuit_reset_seconds
    else:
        raise ValueError("circuit state is not allowlisted")

    ages = snapshot["rpm_ages_seconds"]
    if not isinstance(ages, list):
        raise ValueError("rpm_ages_seconds must be a list")
    for raw_age in ages:
        age = required_nonnegative_number(raw_age, "rpm age")
        if age < limits.rpm_window_seconds:
            control._rpm_timestamps.append(now - age)

    jobs = snapshot["jobs"]
    if not isinstance(jobs, list):
        raise ValueError("jobs must be a list")
    expected_keys = {
        "job_id", "estimated_limit", "actual_limit", "kill_conditions",
        "deadline_remaining_seconds", "cancel_reason", "estimated_consumed",
        "actual_consumed", "actual_cost_unavailable_count", "total_requests",
        "initial_requests", "retry_requests", "consecutive_failures",
        "uncertain_inflight_requests",
    }
    for raw_job in jobs:
        value = require_dict(raw_job, "job")
        require_keys(value, expected_keys)
        job_id = value["job_id"]
        validate_identifier(job_id, "job_id")
        if job_id in control._jobs:
            raise ValueError("snapshot contains duplicate job_id")
        kill_data = require_dict(value["kill_conditions"], "kill_conditions")
        require_keys(
            kill_data,
            {"max_total_requests", "max_retry_requests", "max_consecutive_failures"},
        )
        conditions = JobKillConditions(
            max_total_requests=optional_nonnegative_int(
                kill_data["max_total_requests"], "max_total_requests", positive=True
            ),
            max_retry_requests=optional_nonnegative_int(
                kill_data["max_retry_requests"], "max_retry_requests", positive=True
            ),
            max_consecutive_failures=optional_nonnegative_int(
                kill_data["max_consecutive_failures"],
                "max_consecutive_failures",
                positive=True,
            ),
        )
        remaining_raw = value["deadline_remaining_seconds"]
        remaining = (
            None
            if remaining_raw is None
            else required_nonnegative_number(remaining_raw, "deadline_remaining_seconds")
        )
        uncertain = required_nonnegative_int(
            value["uncertain_inflight_requests"], "uncertain_inflight_requests"
        )
        token = CancellationToken()
        reason = value["cancel_reason"]
        if reason is not None:
            validate_reason(reason)
            token.cancel(reason)
        job = _JobState(
            job_id=job_id,
            estimated_limit=optional_nonnegative_int(value["estimated_limit"], "estimated_limit"),
            actual_limit=optional_nonnegative_int(value["actual_limit"], "actual_limit"),
            kill_conditions=conditions,
            deadline_at=now + remaining if remaining is not None else None,
            token=token,
            estimated_consumed=required_nonnegative_int(value["estimated_consumed"], "estimated_consumed"),
            actual_consumed=required_nonnegative_int(value["actual_consumed"], "actual_consumed"),
            actual_cost_unavailable_count=(
                required_nonnegative_int(
                    value["actual_cost_unavailable_count"],
                    "actual_cost_unavailable_count",
                )
                + uncertain
            ),
            total_requests=required_nonnegative_int(value["total_requests"], "total_requests"),
            initial_requests=required_nonnegative_int(value["initial_requests"], "initial_requests"),
            retry_requests=required_nonnegative_int(value["retry_requests"], "retry_requests"),
            consecutive_failures=required_nonnegative_int(
                value["consecutive_failures"], "consecutive_failures"
            ),
        )
        if job.initial_requests + job.retry_requests != job.total_requests:
            raise ValueError("job request counters are inconsistent")
        if job.estimated_limit is not None and job.estimated_consumed > job.estimated_limit:
            token.cancel("estimated_budget_exceeded")
        if job.actual_limit is not None and job.actual_consumed > job.actual_limit:
            token.cancel("actual_budget_exceeded")
        control._jobs[job_id] = job
    return control
