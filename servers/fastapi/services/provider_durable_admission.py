"""Detached DB transactions for cross-process provider call admission."""

from __future__ import annotations

from dataclasses import dataclass
import re
import uuid

from services.database import async_session_maker
from services.provider_durable_runtime import (
    DurableReservationLease,
    admit_provider_rpm_and_circuit,
    lock_provider_circuit,
    reconcile_provider_call,
    record_provider_circuit_outcome,
    register_durable_budget,
    reserve_provider_call,
)
from services.provider_runtime_primitives import BudgetExceededError, CancellationRequested
from utils.env_settings.provider_credentials import (
    provider_optional_limit,
    provider_required_int,
)
from utils.get_env import (
    get_provider_durable_runtime_required_env,
    get_template_v2_deployment_tier_env,
    get_vnext_tenant_id,
)
from utils.owner_context import get_current_owner_id
from utils.hashing import sha256_hex


_TRUE = frozenset({"1", "true", "yes", "on"})
_MANAGED = frozenset({"staging", "production"})
_SERVICE_TOKEN = re.compile(r"[^a-z0-9._:-]+")


def durable_provider_runtime_required() -> bool:
    configured = (get_provider_durable_runtime_required_env() or "").strip().lower()
    tier = (get_template_v2_deployment_tier_env() or "local").strip().lower()
    return tier in _MANAGED or configured in _TRUE or (
        bool(configured) and configured not in {"0", "false", "no", "off"}
    )


def _integer(name: str, default: int, *, minimum: int = 1) -> int:
    return provider_required_int(name, default, minimum=minimum)


def _optional_limit(name: str) -> int | None:
    return provider_optional_limit(name)


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
    workspace_operation_id: uuid.UUID | None = None,
    expected_attempt_token: str | None = None,
    expected_fencing_token: int | None = None,
    slide_index: int | None = None,
    is_retry: bool = False,
    quality_repair_context: object | None = None,
) -> DetachedDurableLease:
    owner_id = get_current_owner_id()
    if owner_id is None:
        raise CancellationRequested("provider_durable_owner_required")
    service_key = _service_key(operation, stage)
    reservation_key = sha256_hex(f"provider-reservation-v1:{job_id}:{request_id}".encode())
    async with async_session_maker() as session:
        tenant_id = get_vnext_tenant_id()
        episode = None
        header_retry = None
        if workspace_operation_id is not None:
            if not expected_attempt_token or expected_fencing_token is None:
                raise CancellationRequested("provider_workspace_job_scope_invalid")
            if workspace_operation_id == job_id and operation == "quality_review" and stage == "vqa":
                from services.provider_runtime_job_registration import require_public_review_job
                admission = await require_public_review_job(session,
                    operation_id=workspace_operation_id,
                    expected_attempt_token=expected_attempt_token,
                    expected_fencing_token=expected_fencing_token,
                    expected_operation_kind="quality.review")
            elif workspace_operation_id == job_id and operation == "authored_generation" and stage in {"header", "author", "repair"}:
                from sqlalchemy import select
                from models.sql.public_admission import PublicOperationAdmission
                from models.sql.workspace import WorkspaceOperation
                from services.workspace_svg_header_design_operation_handler import require_header_runtime_admission
                current = await session.get(WorkspaceOperation, workspace_operation_id, with_for_update=True)
                public_admission = await session.scalar(select(PublicOperationAdmission).where(
                    PublicOperationAdmission.operation_id == workspace_operation_id))
                admission = await require_header_runtime_admission(session, operation=current, admission=public_admission,
                    expected_attempt_token=expected_attempt_token, expected_fencing_token=expected_fencing_token)
                if current.retry_of_operation_id is not None:
                    header_retry = current.request_payload["svg_header_retry"]
            else:
                from services.svg_partial_recovery import runtime_admission
                episode = await runtime_admission(session, job_id=job_id,
                    operation_id=workspace_operation_id,
                    expected_attempt_token=expected_attempt_token,
                    expected_fencing_token=expected_fencing_token,
                    slide_index=slide_index, stage=stage,
                    **(dict(is_retry=is_retry, quality_repair_context=quality_repair_context)
                       if quality_repair_context is not None else {}))
                if episode is None:
                    raise CancellationRequested("provider_workspace_job_scope_invalid")
                admission = episode
            tenant_id = admission.tenant_id
        estimated_limit = _optional_limit("PROVIDER_JOB_ESTIMATED_BUDGET_MICROUNITS")
        actual_limit = _optional_limit("PROVIDER_JOB_ACTUAL_BUDGET_MICROUNITS")
        if header_retry is not None:
            from services.workspace_svg_header_retry import retry_runtime_cost_limits
            estimated_limit, actual_limit = retry_runtime_cost_limits(header_retry, estimated_limit, actual_limit)
        if episode is not None:
            estimated_limit = min(estimated_limit, episode.estimated_cost_limit_microunits) if estimated_limit is not None else episode.estimated_cost_limit_microunits
            actual_limit = min(actual_limit, episode.actual_cost_limit_microunits) if actual_limit is not None else episode.actual_cost_limit_microunits
        budget = await register_durable_budget(
            session,
            tenant_id=tenant_id,
            owner_id=owner_id,
            job_id=None if workspace_operation_id is not None else job_id,
            workspace_operation_id=workspace_operation_id,
            estimated_limit=estimated_limit,
            actual_limit=actual_limit,
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
        # Circuit before budget, the order reserve_provider_call_detached uses;
        # the reverse order deadlocked a concurrent reservation (2026-10-21).
        await lock_provider_circuit(session, lease.service_key)
        try:
            await reconcile_provider_call(
                session,
                lease.reservation,
                actual_cost=actual_cost,
                outcome=outcome,
            )
        except BudgetExceededError as error:
            if error.kind != "actual":
                raise
            # The provider already spent this cost. Preserve the reconciled
            # receipt and cancellation before propagating the enforcement error.
            await session.commit()
            raise
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
