"""Privacy-safe, best-effort provider receipt instrumentation.

This module deliberately receives only identifiers and numeric usage counters.  It
does not accept prompts, payloads, response bodies, paths, headers or exceptions;
the receipt service is the durable enforcement point and this is the call-boundary
adapter used by authored author/VQA/image calls.
"""

from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from hashlib import sha256
from time import monotonic
from typing import Any, AsyncIterator, Iterator
from uuid import UUID
import asyncio
from weakref import WeakKeyDictionary

from services.provider_runtime_control import (
    JobKillConditions,
    ProviderCallLease,
    ProviderRuntimeControl,
    RuntimeLimits,
)
from services.provider_durable_admission import (
    durable_provider_runtime_required,
    reconcile_provider_call_detached,
    reserve_provider_call_detached,
)


_RECEIPT_OBSERVABILITY = {
    "attempted": 0,
    "persisted": 0,
    "failed": 0,
}


@dataclass(slots=True)
class _RuntimeState:
    control: ProviderRuntimeControl = field(
        default_factory=lambda: ProviderRuntimeControl(RuntimeLimits())
    )
    registration_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    registered_jobs: set[str] = field(default_factory=set)
    latest_job_keys: dict[str, str] = field(default_factory=dict)


_RUNTIME_STATES: WeakKeyDictionary[asyncio.AbstractEventLoop, _RuntimeState] = (
    WeakKeyDictionary()
)


def _runtime_state() -> _RuntimeState:
    loop = asyncio.get_running_loop()
    return _RUNTIME_STATES.setdefault(loop, _RuntimeState())


def provider_receipt_observability_snapshot() -> dict[str, int]:
    """Return low-cardinality, content-free receipt sink counters."""

    return dict(_RECEIPT_OBSERVABILITY)


@dataclass
class ProviderUsageScope:
    """Per-task non-sensitive identity for billable or local stage work."""

    job_id: UUID
    operation: str
    stage: str
    attempt_number: int
    slide_index: int | None = None
    _sequence: int = field(default=0, init=False, repr=False)
    _public_sequence: int = field(default=0, init=False, repr=False)
    _durable_leases: list[object] = field(default_factory=list, init=False, repr=False)

    def next_receipt_key_hash(self, outcome: str) -> str:
        # Persist only this digest.  The preimage stays in process memory and is
        # restricted to UUID/count/allowlisted tokens, never a request/provider id.
        self._sequence += 1
        key = (
            f"receipt-v1:{self.job_id}:{self.operation}:{self.stage}:"
            f"{self.attempt_number}:{self.slide_index}:{self._sequence}:{outcome}"
        )
        return sha256(key.encode("utf-8")).hexdigest()

    def next_runtime_request_id(self) -> str:
        self._sequence += 1
        return (
            f"call:{self.attempt_number}:{self.slide_index or 0}:"
            f"{self._sequence}"
        )

    def next_public_logical_call_ordinal(self) -> int:
        self._public_sequence += 1
        # Keep ordinals deterministic across process recovery while leaving
        # room for bounded physical retries inside one operation attempt.
        return max(1, self.attempt_number) * 1000 + self._public_sequence


_ACTIVE_SCOPE: ContextVar[ProviderUsageScope | None] = ContextVar(
    "provider_usage_scope", default=None
)
_PUBLIC_OPERATION_ID: ContextVar[UUID | None] = ContextVar(
    "public_provider_operation_id", default=None
)
_ACTIVE_PROVIDER_ADMISSION: ContextVar[ProviderCallLease | None] = ContextVar(
    "active_provider_call_admission", default=None
)


@contextmanager
def public_operation_scope(operation_id: UUID | None) -> Iterator[None]:
    token = _PUBLIC_OPERATION_ID.set(operation_id)
    try:
        yield
    finally:
        _PUBLIC_OPERATION_ID.reset(token)


@contextmanager
def provider_usage_scope(
    *,
    job_id: UUID | None,
    operation: str,
    stage: str,
    attempt_number: int = 0,
    slide_index: int | None = None,
) -> Iterator[None]:
    """Set receipt context for one bounded pipeline segment.

    Non-durable/legacy generation passes ``job_id=None`` and therefore create no
    receipt.  This avoids making anonymous local calls appear billable.
    """

    token: Token[ProviderUsageScope | None] | None = None
    if job_id is not None:
        token = _ACTIVE_SCOPE.set(
            ProviderUsageScope(
                job_id=job_id,
                operation=operation,
                stage=stage,
                attempt_number=attempt_number,
                slide_index=slide_index,
            )
        )
    try:
        yield
    finally:
        if token is not None:
            _ACTIVE_SCOPE.reset(token)


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= 2_000_000_000 else None


def _optional_env_int(name: str) -> int | None:
    import os

    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError(f"{name}_must_be_an_integer") from error
    if value < 0:
        raise RuntimeError(f"{name}_must_be_nonnegative")
    return value


async def _ensure_runtime_job(scope: ProviderUsageScope) -> str:
    runtime = _runtime_state()
    durable_job_id = str(scope.job_id)
    job_id = (
        durable_job_id
        if scope.attempt_number <= 0
        else f"{durable_job_id}:attempt:{scope.attempt_number}"
    )
    if job_id in runtime.registered_jobs:
        runtime.latest_job_keys[durable_job_id] = job_id
        return job_id
    async with runtime.registration_lock:
        if job_id in runtime.registered_jobs:
            return job_id
        deadline = _optional_env_int("PROVIDER_JOB_DEADLINE_SECONDS")
        await runtime.control.register_job(
            job_id,
            estimated_budget_microunits=_optional_env_int(
                "PROVIDER_JOB_ESTIMATED_BUDGET_MICROUNITS"
            ),
            actual_budget_microunits=_optional_env_int(
                "PROVIDER_JOB_ACTUAL_BUDGET_MICROUNITS"
            ),
            kill_conditions=JobKillConditions(
                deadline_seconds=float(deadline) if deadline is not None else None,
                max_total_requests=_optional_env_int(
                    "PROVIDER_JOB_MAX_TOTAL_REQUESTS"
                ),
                max_retry_requests=_optional_env_int(
                    "PROVIDER_JOB_MAX_RETRY_REQUESTS"
                ),
                max_consecutive_failures=_optional_env_int(
                    "PROVIDER_JOB_MAX_CONSECUTIVE_FAILURES"
                ),
            ),
        )
        runtime.registered_jobs.add(job_id)
        runtime.latest_job_keys[durable_job_id] = job_id
    return job_id


@asynccontextmanager
async def provider_call_admission(
    *,
    is_retry: bool = False,
    estimated_cost_microunits: int | None = None,
) -> AsyncIterator[ProviderCallLease | None]:
    """Enforce queue, RPM, budget, cancellation, and breaker before SDK I/O."""

    scope = _ACTIVE_SCOPE.get()
    if scope is None:
        yield None
        return
    active_lease = _ACTIVE_PROVIDER_ADMISSION.get()
    if active_lease is not None:
        yield active_lease
        return
    job_id = await _ensure_runtime_job(scope)
    estimated = estimated_cost_microunits
    if estimated is None:
        estimated = _optional_env_int("PROVIDER_CALL_ESTIMATED_COST_MICROUNITS")
    if estimated is None:
        # Unknown cost is not zero. Reserve a conservative accounting unit so
        # request amplification still consumes a configured job ceiling.
        estimated = 1
    request_id = scope.next_runtime_request_id()
    lease = await _runtime_state().control.admit(
        job_id,
        request_id,
        estimated_cost_microunits=estimated,
        is_retry=is_retry,
    )
    durable_lease = None
    public_lease = None
    public_started_at = monotonic()
    active_token = _ACTIVE_PROVIDER_ADMISSION.set(lease)
    try:
        if durable_provider_runtime_required():
            durable_lease = await reserve_provider_call_detached(
                job_id=scope.job_id,
                operation=scope.operation,
                stage=scope.stage,
                request_id=request_id,
                estimated_cost=estimated,
            )
        public_operation_id = _PUBLIC_OPERATION_ID.get()
        if public_operation_id is not None:
            from services.public_operation_admission import (
                reserve_public_provider_call_detached,
            )

            public_stage = {
                "illustrate": "illustration",
                "image": "illustration",
            }.get(scope.stage, scope.stage)
            public_lease = await reserve_public_provider_call_detached(
                operation_id=public_operation_id,
                stage=public_stage,
                slide_index=scope.slide_index,
                logical_call_ordinal=scope.next_public_logical_call_ordinal(),
                retry_class=(
                    "retry"
                    if is_retry
                    else ("resume" if scope.attempt_number > 1 else "initial")
                ),
                estimated_cost_microunits=estimated,
            )
        yield lease
    except BaseException as error:
        if public_lease is not None:
            from services.public_operation_admission import (
                reconcile_public_provider_call_detached,
            )

            await reconcile_public_provider_call_detached(
                public_lease,
                outcome=("cancelled" if isinstance(error, asyncio.CancelledError) else "failed"),
                latency_ms=max(0, round((monotonic() - public_started_at) * 1000)),
                actual_cost_microunits=None,
            )
        if durable_lease is not None:
            await reconcile_provider_call_detached(
                durable_lease,
                actual_cost=None,
                outcome="retryable_failure" if is_retry else "failure",
            )
        await lease.finish(
            actual_cost_microunits=None,
            outcome="retryable_failure" if is_retry else "failure",
        )
        raise
    else:
        if public_lease is not None:
            from services.public_operation_admission import (
                reconcile_public_provider_call_detached,
            )

            await reconcile_public_provider_call_detached(
                public_lease,
                outcome="succeeded",
                latency_ms=max(0, round((monotonic() - public_started_at) * 1000)),
                actual_cost_microunits=None,
            )
        if durable_lease is not None:
            await reconcile_provider_call_detached(
                durable_lease,
                actual_cost=None,
                outcome="success",
            )
            scope._durable_leases.append(durable_lease)
        await lease.finish(actual_cost_microunits=None, outcome="success")
    finally:
        _ACTIVE_PROVIDER_ADMISSION.reset(active_token)


async def cancel_provider_job(job_id: UUID, reason: str = "operator_cancelled") -> None:
    normalized = str(job_id)
    runtime = _runtime_state()
    matching = tuple(
        key
        for key in runtime.registered_jobs
        if key == normalized or key.startswith(f"{normalized}:attempt:")
    )
    for key in matching:
        await runtime.control.cancel_job(key, reason)


async def provider_runtime_job_status(
    job_id: UUID,
) -> dict[str, int | str | bool | None]:
    runtime = _runtime_state()
    normalized = str(job_id)
    return await runtime.control.job_status(
        runtime.latest_job_keys.get(normalized, normalized)
    )


def response_usage(response: object) -> tuple[str, int | None, int | None]:
    """Extract only standard numeric usage fields; absence is *unavailable*.

    No fallback token estimation occurs here: an estimate must be explicitly
    supplied by a pricing adapter, so absent provider usage can never become zero.
    """

    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return "unavailable", None, None

    def get(*names: str) -> Any:
        for name in names:
            value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
            if value is not None:
                return value
        return None

    input_tokens = _positive_int(get("input_tokens", "prompt_tokens"))
    output_tokens = _positive_int(get("output_tokens", "completion_tokens"))
    if input_tokens is None and output_tokens is None:
        return "unavailable", None, None
    return "reported", input_tokens, output_tokens


def _reported_cost(input_tokens: int | None, output_tokens: int | None) -> int | None:
    input_rate = _optional_env_int("PROVIDER_INPUT_MICROUNITS_PER_1K")
    output_rate = _optional_env_int("PROVIDER_OUTPUT_MICROUNITS_PER_1K")
    if input_rate is None or output_rate is None:
        return None
    return (
        ((input_tokens or 0) * input_rate + 999) // 1000
        + ((output_tokens or 0) * output_rate + 999) // 1000
    )


async def record_provider_call(
    *,
    provider: str,
    model: str,
    response: object | None,
    latency_ms: float,
    retry_class: str = "initial",
    outcome: str = "succeeded",
    pricing_version: str = "unpriced-v1",
) -> None:
    """Persist one provider-call receipt when a durable job scope is active.

    The local import prevents a provider call from depending on migrations during
    rollout. Any telemetry failure is intentionally isolated from generation.
    """

    scope = _ACTIVE_SCOPE.get()
    if scope is None:
        return
    _RECEIPT_OBSERVABILITY["attempted"] += 1
    try:
        from services.provider_usage_receipt_service import (
            ProviderUsageReceiptInput,
            record_provider_usage_receipt_detached,
        )

        usage_state, input_tokens, output_tokens = response_usage(response)
        reported_cost = _reported_cost(input_tokens, output_tokens)
        receipt = ProviderUsageReceiptInput(
            job_id=scope.job_id,
            operation=scope.operation,
            stage=scope.stage,
            provider=provider,
            model=model,
            receipt_key_hash=scope.next_receipt_key_hash("provider"),
            retry_class=retry_class,
            outcome=outcome,
            latency_ms=max(0, round(float(latency_ms))),
            usage_state=usage_state,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reported_cost_microunits=reported_cost,
            estimated_cost_microunits=None,
            pricing_version=pricing_version,
        )
        await record_provider_usage_receipt_detached(receipt)
        if scope._durable_leases:
            durable_lease = scope._durable_leases.pop(0)
            durable_outcome = "success" if outcome in {"success", "succeeded"} else outcome
            await reconcile_provider_call_detached(
                durable_lease,
                actual_cost=reported_cost,
                outcome=(
                    durable_outcome
                    if durable_outcome
                    in {"success", "failure", "retryable_failure", "cancelled"}
                    else "failure"
                ),
            )
        _RECEIPT_OBSERVABILITY["persisted"] += 1
    except Exception:
        # Receipt recording must never change an existing generation outcome.
        # Do not log exceptions here: they may contain upstream response content.
        _RECEIPT_OBSERVABILITY["failed"] += 1
        return


async def record_local_stage(*, latency_ms: float) -> None:
    """Record a stage latency with no provider usage or cost attached."""

    await record_provider_call(
        provider="local",
        model="none",
        response=None,
        latency_ms=latency_ms,
        retry_class="initial",
        pricing_version="not-applicable-v1",
    )


class ProviderCallTimer:
    """Small helper for call boundaries that need a monotonic elapsed time."""

    def __init__(self) -> None:
        self.started_at = monotonic()

    @property
    def elapsed_ms(self) -> float:
        return (monotonic() - self.started_at) * 1000.0


__all__ = [
    "ProviderCallTimer",
    "cancel_provider_job",
    "provider_call_admission",
    "public_operation_scope",
    "provider_runtime_job_status",
    "provider_usage_scope",
    "provider_receipt_observability_snapshot",
    "record_local_stage",
    "record_provider_call",
    "response_usage",
]
