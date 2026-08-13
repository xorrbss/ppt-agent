"""Detached DB transactions for cross-process provider call admission."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
import re
import uuid

from services.database import async_session_maker
from services.provider_durable_runtime import (
    DurableReservationLease,
    admit_provider_rpm_and_circuit,
    reconcile_provider_call,
    record_provider_circuit_outcome,
    register_durable_budget,
    reserve_provider_call,
)
from services.provider_runtime_primitives import CancellationRequested
from utils.owner_context import get_current_owner_id


_TRUE = frozenset({"1", "true", "yes", "on"})
_MANAGED = frozenset({"staging", "production"})
_SERVICE_TOKEN = re.compile(r"[^a-z0-9._:-]+")


def durable_provider_runtime_required() -> bool:
    configured = (os.getenv("PROVIDER_DURABLE_RUNTIME_REQUIRED") or "").strip().lower()
    tier = (os.getenv("TEMPLATE_V2_DEPLOYMENT_TIER") or "local").strip().lower()
    return tier in _MANAGED or configured in _TRUE or (
        bool(configured) and configured not in {"0", "false", "no", "off"}
    )


def _integer(name: str, default: int, *, minimum: int = 1) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError(f"{name}_must_be_an_integer") from error
    if value < minimum:
        raise RuntimeError(f"{name}_must_be_at_least_{minimum}")
    return value


def _optional_limit(name: str) -> int | None:
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


def _service_key(operation: str, stage: str) -> str:
    token = _SERVICE_TOKEN.sub("-", f"{operation}.{stage}".strip().lower()).strip("-")
    if not token:
        raise ValueError("provider_service_key_invalid")
    return token[:128]


@dataclass(frozen=True, slots=True)
class DetachedDurableLease:
    reservation: DurableReservationLease
    service_key: str


async def reserve_provider_call_detached(
    *,
    job_id: uuid.UUID,
    operation: str,
    stage: str,
    request_id: str,
    estimated_cost: int,
) -> DetachedDurableLease:
    owner_id = get_current_owner_id()
    if owner_id is None:
        raise CancellationRequested("provider_durable_owner_required")
    service_key = _service_key(operation, stage)
    reservation_key = sha256(
        f"provider-reservation-v1:{job_id}:{request_id}".encode()
    ).hexdigest()
    async with async_session_maker() as session:
        budget = await register_durable_budget(
            session,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
            owner_id=owner_id,
            job_id=job_id,
            estimated_limit=_optional_limit(
                "PROVIDER_JOB_ESTIMATED_BUDGET_MICROUNITS"
            ),
            actual_limit=_optional_limit("PROVIDER_JOB_ACTUAL_BUDGET_MICROUNITS"),
        )
        await admit_provider_rpm_and_circuit(
            session,
            service_key=service_key,
            requests_per_minute=_integer("PROVIDER_RPM", 60),
            failure_threshold=_integer("PROVIDER_CIRCUIT_FAILURE_THRESHOLD", 5),
            reset_seconds=_integer("PROVIDER_CIRCUIT_RESET_SECONDS", 30),
        )
        reservation = await reserve_provider_call(
            session,
            budget_id=budget.id,
            reservation_key_hash=reservation_key,
            estimated_cost=estimated_cost,
            lease_seconds=_integer("PROVIDER_RESERVATION_LEASE_SECONDS", 120),
        )
        await session.commit()
    return DetachedDurableLease(reservation, service_key)


async def reconcile_provider_call_detached(
    lease: DetachedDurableLease,
    *,
    actual_cost: int | None,
    outcome: str,
) -> None:
    async with async_session_maker() as session:
        await reconcile_provider_call(
            session,
            lease.reservation,
            actual_cost=actual_cost,
            outcome=outcome,
        )
        await record_provider_circuit_outcome(
            session,
            service_key=lease.service_key,
            outcome=outcome,
            failure_threshold=_integer("PROVIDER_CIRCUIT_FAILURE_THRESHOLD", 5),
            reset_seconds=_integer("PROVIDER_CIRCUIT_RESET_SECONDS", 30),
        )
        await session.commit()


__all__ = [
    "DetachedDurableLease",
    "durable_provider_runtime_required",
    "reconcile_provider_call_detached",
    "reserve_provider_call_detached",
]
