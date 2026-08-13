"""DB-backed provider budget reservation, reconciliation, RPM and circuit CAS."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import re
import secrets
import uuid

from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.sql.presentation_generation_job import PresentationGenerationJob  # noqa: F401
from models.sql.provider_runtime_reservation import (
    ProviderRuntimeBudget,
    ProviderRuntimeCircuit,
    ProviderRuntimeReservation,
)
from models.sql.user import User  # noqa: F401
from services.provider_runtime_primitives import (
    BudgetExceededError,
    CancellationRequested,
    CircuitOpenError,
)
from utils.datetime_utils import get_current_utc_datetime


_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_SERVICE_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_OUTCOMES = frozenset({"success", "failure", "retryable_failure", "cancelled"})
_CIRCUIT_CAS_ATTEMPTS = 8


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _require_hash(value: str, field: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise ValueError(f"{field} must be a SHA-256 hex digest")


def _require_nonnegative(value: int | None, field: str) -> None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or value < 0
    ):
        raise ValueError(f"{field} must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class DurableReservationLease:
    budget_id: uuid.UUID
    reservation_id: uuid.UUID
    reservation_key_hash: str
    lease_token: str
    estimated_cost: int


async def register_durable_budget(
    session: AsyncSession,
    *,
    tenant_id: str,
    owner_id: uuid.UUID,
    job_id: uuid.UUID,
    estimated_limit: int | None,
    actual_limit: int | None,
) -> ProviderRuntimeBudget:
    """Create a job budget idempotently without accepting provider payloads."""

    _require_nonnegative(estimated_limit, "estimated_limit")
    _require_nonnegative(actual_limit, "actual_limit")
    row = await session.scalar(
        select(ProviderRuntimeBudget).where(
            ProviderRuntimeBudget.tenant_id == tenant_id,
            ProviderRuntimeBudget.owner_id == owner_id,
            ProviderRuntimeBudget.job_id == job_id,
        )
    )
    if row is not None:
        if row.estimated_limit != estimated_limit or row.actual_limit != actual_limit:
            raise ValueError("provider_budget_registration_conflict")
        return row
    row = ProviderRuntimeBudget(
        tenant_id=tenant_id,
        owner_id=owner_id,
        job_id=job_id,
        estimated_limit=estimated_limit,
        actual_limit=actual_limit,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        row = await session.scalar(
            select(ProviderRuntimeBudget).where(
                ProviderRuntimeBudget.tenant_id == tenant_id,
                ProviderRuntimeBudget.owner_id == owner_id,
                ProviderRuntimeBudget.job_id == job_id,
            )
        )
        if row is None:
            raise
        if row.estimated_limit != estimated_limit or row.actual_limit != actual_limit:
            raise ValueError("provider_budget_registration_conflict") from None
    return row


async def reserve_provider_call(
    session: AsyncSession,
    *,
    budget_id: uuid.UUID,
    reservation_key_hash: str,
    estimated_cost: int,
    lease_seconds: int = 120,
) -> DurableReservationLease:
    """Atomically reserve estimated cost once across all worker processes."""

    _require_hash(reservation_key_hash, "reservation_key_hash")
    _require_nonnegative(estimated_cost, "estimated_cost")
    if isinstance(lease_seconds, bool) or not 1 <= lease_seconds <= 3600:
        raise ValueError("lease_seconds must be between 1 and 3600")
    existing = await session.scalar(
        select(ProviderRuntimeReservation).where(
            ProviderRuntimeReservation.budget_id == budget_id,
            ProviderRuntimeReservation.reservation_key_hash == reservation_key_hash,
        )
    )
    if existing is not None:
        raise ValueError("provider_reservation_duplicate")
    budget = await session.get(ProviderRuntimeBudget, budget_id)
    if budget is None or budget.state != "active":
        raise CancellationRequested("budget_inactive")
    projected = budget.estimated_consumed + budget.estimated_reserved + estimated_cost
    if budget.estimated_limit is not None and projected > budget.estimated_limit:
        budget.state = "cancelled"
        budget.cancellation_reason = "estimated_budget_exceeded"
        budget.version += 1
        await session.flush()
        raise BudgetExceededError(
            kind="estimated", limit=budget.estimated_limit, observed=projected
        )
    expected_version = budget.version
    result = await session.execute(
        update(ProviderRuntimeBudget)
        .where(
            ProviderRuntimeBudget.id == budget_id,
            ProviderRuntimeBudget.state == "active",
            ProviderRuntimeBudget.version == expected_version,
            or_(
                ProviderRuntimeBudget.estimated_limit.is_(None),
                ProviderRuntimeBudget.estimated_consumed
                + ProviderRuntimeBudget.estimated_reserved
                + estimated_cost
                <= ProviderRuntimeBudget.estimated_limit,
            ),
        )
        .values(
            estimated_reserved=ProviderRuntimeBudget.estimated_reserved
            + estimated_cost,
            request_count=ProviderRuntimeBudget.request_count + 1,
            version=ProviderRuntimeBudget.version + 1,
            updated_at=get_current_utc_datetime(),
        )
    )
    if result.rowcount != 1:
        raise RuntimeError("provider_budget_cas_conflict")
    token = secrets.token_hex(32)
    reservation = ProviderRuntimeReservation(
        budget_id=budget_id,
        reservation_key_hash=reservation_key_hash,
        estimated_cost=estimated_cost,
        lease_token_hash=sha256(token.encode()).hexdigest(),
        lease_expires_at=get_current_utc_datetime() + timedelta(seconds=lease_seconds),
    )
    try:
        async with session.begin_nested():
            session.add(reservation)
            await session.flush()
    except IntegrityError as error:
        raise ValueError("provider_reservation_duplicate") from error
    return DurableReservationLease(
        budget_id=budget_id,
        reservation_id=reservation.id,
        reservation_key_hash=reservation_key_hash,
        lease_token=token,
        estimated_cost=estimated_cost,
    )


async def reconcile_provider_call(
    session: AsyncSession,
    lease: DurableReservationLease,
    *,
    actual_cost: int | None,
    outcome: str,
) -> ProviderRuntimeReservation:
    """Reconcile a reservation exactly once; unavailable actual cost stays NULL."""

    _require_nonnegative(actual_cost, "actual_cost")
    if outcome not in _OUTCOMES:
        raise ValueError("provider_outcome_not_allowlisted")
    row = await session.get(ProviderRuntimeReservation, lease.reservation_id)
    if (
        row is None
        or row.budget_id != lease.budget_id
        or row.reservation_key_hash != lease.reservation_key_hash
        or row.lease_token_hash != sha256(lease.lease_token.encode()).hexdigest()
    ):
        raise ValueError("provider_reservation_lease_mismatch")
    if row.state == "uncertain" and actual_cost is not None and row.outcome == outcome:
        budget = await session.get(ProviderRuntimeBudget, lease.budget_id)
        if budget is None:
            raise CancellationRequested("budget_missing")
        budget.actual_consumed += actual_cost
        budget.version += 1
        row.actual_cost = actual_cost
        row.state = "reconciled"
        if budget.actual_limit is not None and budget.actual_consumed > budget.actual_limit:
            budget.state = "cancelled"
            budget.cancellation_reason = "actual_budget_exceeded"
            await session.flush()
            raise BudgetExceededError(
                kind="actual",
                limit=budget.actual_limit,
                observed=budget.actual_consumed,
            )
        await session.flush()
        return row
    if row.state != "reserved":
        if row.actual_cost == actual_cost and row.outcome == outcome:
            return row
        raise ValueError("provider_reservation_already_reconciled")
    budget = await session.get(ProviderRuntimeBudget, lease.budget_id)
    if budget is None:
        raise CancellationRequested("budget_missing")
    budget.estimated_reserved -= row.estimated_cost
    budget.estimated_consumed += row.estimated_cost
    if actual_cost is not None:
        budget.actual_consumed += actual_cost
    budget.version += 1
    row.actual_cost = actual_cost
    row.outcome = outcome
    row.state = "reconciled" if actual_cost is not None else "uncertain"
    if budget.actual_limit is not None and budget.actual_consumed > budget.actual_limit:
        budget.state = "cancelled"
        budget.cancellation_reason = "actual_budget_exceeded"
        await session.flush()
        raise BudgetExceededError(
            kind="actual",
            limit=budget.actual_limit,
            observed=budget.actual_consumed,
        )
    await session.flush()
    return row


async def recover_expired_reservations(
    session: AsyncSession, *, now=None
) -> int:
    """Convert crash-abandoned reservations to charged/unknown receipts."""

    observed_at = now or get_current_utc_datetime()
    rows = list(
        (
            await session.execute(
                select(ProviderRuntimeReservation).where(
                    ProviderRuntimeReservation.state == "reserved",
                    ProviderRuntimeReservation.lease_expires_at <= observed_at,
                )
            )
        ).scalars()
    )
    for row in rows:
        budget = await session.get(ProviderRuntimeBudget, row.budget_id)
        if budget is not None:
            budget.estimated_reserved -= row.estimated_cost
            budget.estimated_consumed += row.estimated_cost
            budget.version += 1
        row.state = "uncertain"
        row.outcome = "failure"
    await session.flush()
    return len(rows)


async def _load_provider_circuit(
    session: AsyncSession, service_key: str
) -> ProviderRuntimeCircuit | None:
    return await session.scalar(
        select(ProviderRuntimeCircuit)
        .where(ProviderRuntimeCircuit.service_key == service_key)
        .execution_options(populate_existing=True)
    )


async def _ensure_provider_circuit(
    session: AsyncSession, *, service_key: str, now: datetime
) -> ProviderRuntimeCircuit:
    circuit = await _load_provider_circuit(session, service_key)
    if circuit is not None:
        return circuit

    values = {
        "id": uuid.uuid4(),
        "service_key": service_key,
        "rpm_window_started_at": now,
    }
    dialect = session.bind.dialect.name if session.bind is not None else ""
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert

        statement = insert(ProviderRuntimeCircuit).values(**values)
        await session.execute(statement.on_conflict_do_nothing(index_elements=["service_key"]))
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert

        statement = insert(ProviderRuntimeCircuit).values(**values)
        await session.execute(statement.on_conflict_do_nothing(index_elements=["service_key"]))
    else:
        candidate = ProviderRuntimeCircuit(**values)
        try:
            async with session.begin_nested():
                session.add(candidate)
                await session.flush()
        except IntegrityError:
            pass

    circuit = await _load_provider_circuit(session, service_key)
    if circuit is None:
        raise RuntimeError("provider_circuit_registration_conflict")
    return circuit


async def admit_provider_rpm_and_circuit(
    session: AsyncSession,
    *,
    service_key: str,
    requests_per_minute: int,
    failure_threshold: int,
    reset_seconds: int,
) -> ProviderRuntimeCircuit:
    """Reserve one durable RPM slot and reject an open circuit."""

    if not _SERVICE_RE.fullmatch(service_key):
        raise ValueError("provider_service_key_invalid")
    for value, field in (
        (requests_per_minute, "requests_per_minute"),
        (failure_threshold, "failure_threshold"),
        (reset_seconds, "reset_seconds"),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{field} must be positive")
    now = get_current_utc_datetime()
    circuit = await _ensure_provider_circuit(
        session, service_key=service_key, now=now
    )
    for _attempt in range(_CIRCUIT_CAS_ATTEMPTS):
        state = circuit.state
        if state == "open":
            if circuit.open_until is not None and _as_utc(circuit.open_until) > now:
                raise CircuitOpenError("provider_circuit_open")
            state = "half_open"
        window_started_at = circuit.rpm_window_started_at
        rpm_count = circuit.rpm_count
        if now - _as_utc(window_started_at) >= timedelta(minutes=1):
            window_started_at = now
            rpm_count = 0
        if rpm_count >= requests_per_minute:
            raise RuntimeError("provider_rpm_limit_reached")
        result = await session.execute(
            update(ProviderRuntimeCircuit)
            .where(
                ProviderRuntimeCircuit.id == circuit.id,
                ProviderRuntimeCircuit.version == circuit.version,
            )
            .values(
                state=state,
                rpm_window_started_at=window_started_at,
                rpm_count=rpm_count + 1,
                version=ProviderRuntimeCircuit.version + 1,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 1:
            await session.refresh(circuit)
            return circuit
        session.expire(circuit)
        circuit = await _load_provider_circuit(session, service_key)
        if circuit is None:
            raise RuntimeError("provider_circuit_missing")
    raise RuntimeError("provider_circuit_cas_conflict")


async def record_provider_circuit_outcome(
    session: AsyncSession,
    *,
    service_key: str,
    outcome: str,
    failure_threshold: int,
    reset_seconds: int,
) -> None:
    if outcome not in _OUTCOMES:
        raise ValueError("provider_outcome_not_allowlisted")
    circuit = await _load_provider_circuit(session, service_key)
    if circuit is None:
        raise ValueError("provider_circuit_missing")
    now = get_current_utc_datetime()
    for _attempt in range(_CIRCUIT_CAS_ATTEMPTS):
        state = circuit.state
        failures = circuit.consecutive_failures
        open_until = circuit.open_until
        if outcome == "success":
            state = "closed"
            failures = 0
            open_until = None
        elif outcome == "retryable_failure":
            failures += 1
            if failures >= failure_threshold:
                state = "open"
                open_until = now + timedelta(seconds=reset_seconds)
        result = await session.execute(
            update(ProviderRuntimeCircuit)
            .where(
                ProviderRuntimeCircuit.id == circuit.id,
                ProviderRuntimeCircuit.version == circuit.version,
            )
            .values(
                state=state,
                consecutive_failures=failures,
                open_until=open_until,
                version=ProviderRuntimeCircuit.version + 1,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 1:
            return
        session.expire(circuit)
        circuit = await _load_provider_circuit(session, service_key)
        if circuit is None:
            raise ValueError("provider_circuit_missing")
    raise RuntimeError("provider_circuit_cas_conflict")


__all__ = [
    "DurableReservationLease",
    "admit_provider_rpm_and_circuit",
    "record_provider_circuit_outcome",
    "recover_expired_reservations",
    "reconcile_provider_call",
    "register_durable_budget",
    "reserve_provider_call",
]
