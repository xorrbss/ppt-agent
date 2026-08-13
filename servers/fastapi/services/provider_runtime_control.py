"""In-process admission controls for metered provider calls.

The controller intentionally accepts only bounded identifiers and scalar cost
data. Provider payloads, URLs, credentials, headers, and response bodies do not
belong in this layer or its restart snapshot.

Durable job ownership remains with ``PresentationGenerationJob``. After a
process restart, callers restore this controller and resubmit durable work;
queued calls are never replayed implicitly. Calls that had already been admitted
retain their estimated spend/request counters and are restored with unknown
actual cost, which avoids treating unavailable provider usage as zero.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
import time
from typing import Callable

from services.provider_runtime_lease import ProviderCallLease
from services.provider_runtime_primitives import (
    OUTCOMES as _OUTCOMES,
    BackpressureError,
    BudgetExceededError,
    CancellationRequested,
    CancellationToken,
    CircuitOpenError,
    JobKillConditions,
    RetryAmplificationError,
    RuntimeControlError,
    RuntimeLimits,
    validate_identifier as _validate_identifier,
    validate_nonnegative as _validate_nonnegative,
    validate_optional_nonnegative as _validate_optional_nonnegative,
    validate_reason as _validate_reason,
)


@dataclass(slots=True)
class _JobState:
    job_id: str
    estimated_limit: int | None
    actual_limit: int | None
    kill_conditions: JobKillConditions
    deadline_at: float | None
    token: CancellationToken = field(default_factory=CancellationToken)
    estimated_consumed: int = 0
    estimated_reserved: int = 0
    actual_consumed: int = 0
    actual_cost_unavailable_count: int = 0
    total_requests: int = 0
    initial_requests: int = 0
    retry_requests: int = 0
    queued_initial: int = 0
    queued_retry: int = 0
    consecutive_failures: int = 0
    uncertain_inflight_requests: int = 0


@dataclass(slots=True)
class _QueuedAdmission:
    job: _JobState
    request_id: str
    estimated_cost: int
    is_retry: bool


class ProviderRuntimeControl:
    """Async bounded admission, rate limiting, budgets, and circuit breaking."""

    def __init__(
        self,
        limits: RuntimeLimits | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limits = limits or RuntimeLimits()
        self._clock = clock
        self._condition = asyncio.Condition()
        self._queue: deque[_QueuedAdmission] = deque()
        self._jobs: dict[str, _JobState] = {}
        self._in_flight = 0
        self._in_flight_by_job: dict[str, int] = {}
        self._rpm_timestamps: deque[float] = deque()
        self._circuit_failures = 0
        self._circuit_open_until = 0.0
        self._half_open_in_flight = False

    async def register_job(
        self,
        job_id: str,
        *,
        estimated_budget_microunits: int | None,
        actual_budget_microunits: int | None,
        kill_conditions: JobKillConditions | None = None,
    ) -> CancellationToken:
        """Register one job and return its cooperative cancellation token."""

        _validate_identifier(job_id, "job_id")
        _validate_optional_nonnegative(estimated_budget_microunits, "estimated_budget_microunits")
        _validate_optional_nonnegative(actual_budget_microunits, "actual_budget_microunits")
        conditions = kill_conditions or JobKillConditions()
        async with self._condition:
            if job_id in self._jobs:
                raise ValueError("job_id is already registered")
            deadline_at = (
                self._clock() + float(conditions.deadline_seconds) if conditions.deadline_seconds is not None else None
            )
            state = _JobState(
                job_id=job_id,
                estimated_limit=estimated_budget_microunits,
                actual_limit=actual_budget_microunits,
                kill_conditions=conditions,
                deadline_at=deadline_at,
            )
            self._jobs[job_id] = state
            return state.token

    def cancellation_token(self, job_id: str) -> CancellationToken:
        """Return a registered token without exposing mutable accounting state."""

        try:
            return self._jobs[job_id].token
        except KeyError as error:
            raise KeyError("job_id is not registered") from error

    async def cancel_job(self, job_id: str, reason: str = "operator_cancelled") -> None:
        """Cancel a job and release all of its queued budget reservations."""

        _validate_reason(reason)
        async with self._condition:
            job = self._job(job_id)
            job.token.cancel(reason)
            for request in tuple(self._queue):
                if request.job is job:
                    self._abort_queued(request)
            self._condition.notify_all()

    async def admit(
        self,
        job_id: str,
        request_id: str,
        *,
        estimated_cost_microunits: int,
        is_retry: bool = False,
    ) -> ProviderCallLease:
        """Queue and admit one call under FIFO, RPM, budget, and breaker limits."""

        _validate_identifier(request_id, "request_id")
        _validate_nonnegative(estimated_cost_microunits, "estimated_cost_microunits")
        if not isinstance(is_retry, bool):
            raise ValueError("is_retry must be a boolean")

        request: _QueuedAdmission | None = None
        async with self._condition:
            job = self._job(job_id)
            self._evaluate_kill_conditions(job)
            self._raise_if_circuit_open()
            if len(self._queue) >= self.limits.max_queue_size:
                raise BackpressureError("provider_admission_queue_full")
            self._reserve_request(job, estimated_cost_microunits, is_retry)
            request = _QueuedAdmission(
                job=job,
                request_id=request_id,
                estimated_cost=estimated_cost_microunits,
                is_retry=is_retry,
            )
            self._queue.append(request)
            self._condition.notify_all()
            try:
                while True:
                    self._evaluate_kill_conditions(job)
                    if self._circuit_is_open():
                        self._abort_queued(request)
                        raise CircuitOpenError("provider_circuit_open")
                    if request is self._queue[0] and self._can_start_call():
                        return self._start_call(request)
                    wait_seconds = self._next_wait_seconds(job)
                    try:
                        await asyncio.wait_for(self._condition.wait(), timeout=wait_seconds)
                    except TimeoutError:
                        pass
            except BaseException:
                if request in self._queue:
                    self._abort_queued(request)
                    self._condition.notify_all()
                raise

    async def job_status(self, job_id: str) -> dict[str, int | str | bool | None]:
        """Return scalar-only accounting suitable for metrics and tests."""

        async with self._condition:
            job = self._job(job_id)
            return {
                "job_id": job.job_id,
                "cancelled": job.token.cancelled,
                "cancellation_reason": job.token.reason,
                "estimated_budget_microunits": job.estimated_limit,
                "estimated_consumed_microunits": job.estimated_consumed,
                "estimated_reserved_microunits": job.estimated_reserved,
                "actual_budget_microunits": job.actual_limit,
                "actual_consumed_microunits": job.actual_consumed,
                "actual_cost_unavailable_count": job.actual_cost_unavailable_count,
                "total_requests": job.total_requests,
                "initial_requests": job.initial_requests,
                "retry_requests": job.retry_requests,
                "consecutive_failures": job.consecutive_failures,
                "in_flight": self._in_flight_by_job.get(job_id, 0),
            }

    async def runtime_status(self) -> dict[str, int | float | str]:
        """Return provider-wide bounded scalars for dashboards and alerts."""

        async with self._condition:
            now = self._clock()
            self._prune_rpm(now)
            return {
                "queue_depth": len(self._queue),
                "queue_capacity": self.limits.max_queue_size,
                "in_flight": self._in_flight,
                "concurrency_limit": self.limits.max_concurrency,
                "rpm_window_count": len(self._rpm_timestamps),
                "rpm_limit": self.limits.requests_per_minute,
                "circuit_state": self._circuit_snapshot_state(now),
                "circuit_consecutive_failures": self._circuit_failures,
                "circuit_open_remaining_seconds": max(0.0, self._circuit_open_until - now),
            }

    async def snapshot(self) -> dict[str, object]:
        """Return a JSON-safe, payload-free restart snapshot."""

        from services.provider_runtime_snapshot import snapshot_control

        return await snapshot_control(self)

    @classmethod
    def restore(
        cls,
        snapshot: dict[str, object],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> "ProviderRuntimeControl":
        """Strictly restore budget/rate/breaker counters from ``snapshot``."""

        from services.provider_runtime_snapshot import restore_control

        return restore_control(cls, snapshot, clock=clock)

    def _job(self, job_id: str) -> _JobState:
        _validate_identifier(job_id, "job_id")
        try:
            return self._jobs[job_id]
        except KeyError as error:
            raise KeyError("job_id is not registered") from error

    def _reserve_request(self, job: _JobState, estimated_cost: int, is_retry: bool) -> None:
        projected_cost = job.estimated_consumed + job.estimated_reserved + estimated_cost
        if job.estimated_limit is not None and projected_cost > job.estimated_limit:
            job.token.cancel("estimated_budget_exceeded")
            raise BudgetExceededError(kind="estimated", limit=job.estimated_limit, observed=projected_cost)

        projected_initial = job.initial_requests + job.queued_initial + (not is_retry)
        projected_retry = job.retry_requests + job.queued_retry + is_retry
        if is_retry and projected_initial == 0:
            job.token.cancel("retry_amplification_exceeded")
            raise RetryAmplificationError("retry_requires_an_initial_request")
        if is_retry and (
            (projected_initial + projected_retry) / projected_initial > self.limits.retry_amplification_ceiling
        ):
            job.token.cancel("retry_amplification_exceeded")
            raise RetryAmplificationError("retry_amplification_ceiling_exceeded")

        conditions = job.kill_conditions
        if (
            conditions.max_total_requests is not None
            and projected_initial + projected_retry > conditions.max_total_requests
        ):
            job.token.cancel("max_total_requests")
            raise CancellationRequested("max_total_requests")
        if conditions.max_retry_requests is not None and projected_retry > conditions.max_retry_requests:
            job.token.cancel("max_retry_requests")
            raise CancellationRequested("max_retry_requests")

        job.estimated_reserved += estimated_cost
        if is_retry:
            job.queued_retry += 1
        else:
            job.queued_initial += 1

    def _abort_queued(self, request: _QueuedAdmission) -> None:
        try:
            self._queue.remove(request)
        except ValueError:
            return
        request.job.estimated_reserved -= request.estimated_cost
        if request.is_retry:
            request.job.queued_retry -= 1
        else:
            request.job.queued_initial -= 1

    def _can_start_call(self) -> bool:
        now = self._clock()
        self._prune_rpm(now)
        if self._in_flight >= self.limits.max_concurrency:
            return False
        if len(self._rpm_timestamps) >= self.limits.requests_per_minute:
            return False
        if self._circuit_open_until and self._circuit_open_until <= now:
            return not self._half_open_in_flight
        return True

    def _start_call(self, request: _QueuedAdmission) -> ProviderCallLease:
        if not self._queue or self._queue[0] is not request:
            raise RuntimeError("admission queue order violated")
        self._queue.popleft()
        job = request.job
        job.estimated_reserved -= request.estimated_cost
        job.estimated_consumed += request.estimated_cost
        if request.is_retry:
            job.queued_retry -= 1
            job.retry_requests += 1
        else:
            job.queued_initial -= 1
            job.initial_requests += 1
        job.total_requests += 1
        self._in_flight += 1
        self._in_flight_by_job[job.job_id] = self._in_flight_by_job.get(job.job_id, 0) + 1
        now = self._clock()
        self._rpm_timestamps.append(now)
        half_open_probe = bool(self._circuit_open_until and self._circuit_open_until <= now)
        if half_open_probe:
            self._half_open_in_flight = True
        self._condition.notify_all()
        return ProviderCallLease(
            self,
            job_id=job.job_id,
            request_id=request.request_id,
            is_retry=request.is_retry,
            half_open_probe=half_open_probe,
        )

    async def _finish_lease(
        self,
        lease: ProviderCallLease,
        *,
        actual_cost_microunits: int | None,
        outcome: str,
    ) -> None:
        _validate_optional_nonnegative(actual_cost_microunits, "actual_cost_microunits")
        if outcome not in _OUTCOMES:
            raise ValueError("outcome is not allowlisted")
        budget_error: BudgetExceededError | None = None
        async with self._condition:
            if lease._finished:
                raise RuntimeControlError("provider_call_lease_already_finished")
            lease._finished = True
            job = self._job(lease.job_id)
            self._in_flight -= 1
            remaining = self._in_flight_by_job.get(job.job_id, 0) - 1
            if remaining > 0:
                self._in_flight_by_job[job.job_id] = remaining
            else:
                self._in_flight_by_job.pop(job.job_id, None)

            if actual_cost_microunits is None:
                job.actual_cost_unavailable_count += 1
            else:
                job.actual_consumed += actual_cost_microunits
                if job.actual_limit is not None and job.actual_consumed > job.actual_limit:
                    job.token.cancel("actual_budget_exceeded")
                    budget_error = BudgetExceededError(
                        kind="actual",
                        limit=job.actual_limit,
                        observed=job.actual_consumed,
                    )

            if outcome == "success":
                job.consecutive_failures = 0
                # A success from a call that was already in flight when another
                # call opened the circuit must not bypass the recovery timeout.
                if lease._half_open_probe or self._circuit_open_until == 0:
                    self._circuit_failures = 0
                    self._circuit_open_until = 0.0
                    self._half_open_in_flight = False
            else:
                job.consecutive_failures += 1
                if outcome == "retryable_failure":
                    self._circuit_failures += 1
                    if self._half_open_in_flight or self._circuit_failures >= self.limits.circuit_failure_threshold:
                        self._circuit_open_until = self._clock() + self.limits.circuit_reset_seconds
                        self._half_open_in_flight = False
                elif self._half_open_in_flight:
                    self._circuit_open_until = self._clock() + self.limits.circuit_reset_seconds
                    self._half_open_in_flight = False

            conditions = job.kill_conditions
            if (
                conditions.max_consecutive_failures is not None
                and job.consecutive_failures >= conditions.max_consecutive_failures
            ):
                job.token.cancel("max_consecutive_failures")
            self._condition.notify_all()
        if budget_error is not None:
            raise budget_error

    def _evaluate_kill_conditions(self, job: _JobState) -> None:
        if job.deadline_at is not None and self._clock() >= job.deadline_at:
            job.token.cancel("deadline_exceeded")
        job.token.raise_if_cancelled()

    def _circuit_is_open(self) -> bool:
        return self._circuit_open_until > self._clock()

    def _raise_if_circuit_open(self) -> None:
        if self._circuit_is_open():
            raise CircuitOpenError("provider_circuit_open")

    def _circuit_snapshot_state(self, now: float | None = None) -> str:
        observed_at = self._clock() if now is None else now
        if self._half_open_in_flight:
            return "half_open_inflight"
        if self._circuit_open_until > observed_at:
            return "open"
        if self._circuit_open_until:
            return "half_open_ready"
        return "closed"

    def _prune_rpm(self, now: float) -> None:
        cutoff = now - self.limits.rpm_window_seconds
        while self._rpm_timestamps and self._rpm_timestamps[0] <= cutoff:
            self._rpm_timestamps.popleft()

    def _next_wait_seconds(self, job: _JobState) -> float:
        """Bound polling so direct token cancellation is observed promptly."""

        now = self._clock()
        waits = [0.05]
        self._prune_rpm(now)
        if len(self._rpm_timestamps) >= self.limits.requests_per_minute:
            waits.append(
                max(
                    0.001,
                    self._rpm_timestamps[0] + self.limits.rpm_window_seconds - now,
                )
            )
        if job.deadline_at is not None:
            waits.append(max(0.001, job.deadline_at - now))
        return min(waits)


__all__ = [
    "BackpressureError",
    "BudgetExceededError",
    "CancellationRequested",
    "CancellationToken",
    "CircuitOpenError",
    "JobKillConditions",
    "ProviderCallLease",
    "ProviderRuntimeControl",
    "RetryAmplificationError",
    "RuntimeControlError",
    "RuntimeLimits",
]
