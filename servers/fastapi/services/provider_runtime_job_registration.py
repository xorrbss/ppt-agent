"""Process-local provider runtime job registration seeded from durable receipts.

``ProviderRuntimeControl`` keeps retry-amplification counters per durable job,
but they live only as long as the worker process.  A resumed attempt (worker
restart, lease takeover by another process) starts with counters at zero, and
its first provider call is often a retry by design: the slide checkpoint
records the provider attempt ordinal of the interrupted call, so the resumed
call carries ordinal 2 and ``is_retry=True``.  The control then rejected it
with ``retry_requires_an_initial_request`` and cancelled the whole job.

Registration therefore seeds the process-local counters from the provider
usage receipts persisted by earlier attempts before the first call is admitted.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol
import uuid

from sqlalchemy.exc import SQLAlchemyError

from services.provider_runtime_control import (
    JobKillConditions,
    ProviderRuntimeControl,
)
from utils.env_settings.provider_credentials import provider_optional_limit

LOGGER = logging.getLogger(__name__)


class RuntimeJobRegistry(Protocol):
    """The per-event-loop registry owned by provider usage instrumentation."""

    control: ProviderRuntimeControl
    registration_lock: asyncio.Lock
    registered_jobs: set[str]
    latest_job_keys: dict[str, str]


class RuntimeJobScope(Protocol):
    """The subset of a provider usage scope that registration depends on."""

    job_id: uuid.UUID
    attempt_number: int


def optional_env_int(name: str) -> int | None:
    return provider_optional_limit(name)


async def persisted_request_counts(job_id: uuid.UUID) -> tuple[int, int]:
    """Read ``(initial, retry)`` provider request counts from durable receipts.

    Seeding is an accounting optimisation, never an admission authority: a
    resumed attempt must still start when the receipt store cannot be read
    (a database without the receipt table, a transient connection failure).
    An unreadable store therefore yields ``(0, 0)`` and a warning rather than
    failing the attempt that was being recovered.
    """

    # Imported lazily so instrumentation never depends on the database module
    # at import time; the session factory is resolved at call time.
    from services.database import async_session_maker
    from services.provider_usage_receipt_service import (
        persisted_provider_request_counts,
    )

    try:
        async with async_session_maker() as session:
            return await persisted_provider_request_counts(session, job_id)
    except SQLAlchemyError:
        LOGGER.warning(
            "Provider request counts unavailable for job %s; "
            "seeding the runtime job from zero",
            job_id,
            exc_info=True,
        )
        return 0, 0


async def ensure_runtime_job(
    runtime: RuntimeJobRegistry, scope: RuntimeJobScope
) -> str:
    """Register the durable job once per process and return its runtime key."""

    durable_job_id = str(scope.job_id)
    # A durable generation job has one provider budget across every operation
    # attempt.  Re-registering an attempt-suffixed runtime job resets the hard
    # cap after recovery and permits retry amplification.
    episode_limits = getattr(scope, "episode_limits", None)
    episode_operation = getattr(scope, "workspace_operation_id", None)
    if episode_operation is not None and episode_limits is None:
        raise RuntimeError("svg_partial_recovery_runtime_unbound")
    job_id = str(episode_operation) if episode_operation is not None else durable_job_id
    public_baseline = (await public_review_request_baseline(scope.job_id)
                       if getattr(scope, "public_only", False) else None)
    if job_id in runtime.registered_jobs:
        runtime.latest_job_keys[durable_job_id] = job_id
        return job_id
    async with runtime.registration_lock:
        if getattr(scope, "public_only", False):
            public_baseline = await public_review_request_baseline(scope.job_id)
        if job_id in runtime.registered_jobs:
            return job_id
        # An explicitly retried operation can start at scope attempt one while
        # retaining the same durable job and its earlier provider receipts.
        # Seed every new process registration from that job's real receipts;
        # a genuinely fresh job has no receipts and therefore seeds zero.
        if episode_limits is not None:
            initial_requests, retry_requests, lineage_total_limit, lineage_retry_limit, _, _ = episode_limits
            lineage_total_limit += lineage_retry_limit
            lineage_initial = lineage_retry = 0
        elif getattr(scope, "public_only", False):
            initial_requests, retry_requests, lineage_total_limit, lineage_retry_limit = public_baseline
            lineage_initial = lineage_retry = 0
        else:
            initial_requests, retry_requests = await persisted_request_counts(scope.job_id)
            lineage_initial, lineage_retry, lineage_total_limit, lineage_retry_limit = await verified_lineage_request_baseline(scope.job_id)
        total_limit = optional_env_int("PROVIDER_JOB_MAX_TOTAL_REQUESTS")
        retry_limit = optional_env_int("PROVIDER_JOB_MAX_RETRY_REQUESTS")
        if lineage_total_limit is not None:
            total_limit = min(total_limit, lineage_total_limit) if total_limit is not None else lineage_total_limit
            retry_limit = min(retry_limit, lineage_retry_limit) if retry_limit is not None else lineage_retry_limit
        deadline = optional_env_int("PROVIDER_JOB_DEADLINE_SECONDS")
        estimated_limit = optional_env_int("PROVIDER_JOB_ESTIMATED_BUDGET_MICROUNITS")
        actual_limit = optional_env_int("PROVIDER_JOB_ACTUAL_BUDGET_MICROUNITS")
        if episode_limits is not None:
            estimated_limit = min(estimated_limit, episode_limits[4]) if estimated_limit is not None else episode_limits[4]
            actual_limit = min(actual_limit, episode_limits[5]) if actual_limit is not None else episode_limits[5]
        await runtime.control.register_job(
            job_id,
            estimated_budget_microunits=estimated_limit,
            actual_budget_microunits=actual_limit,
            kill_conditions=JobKillConditions(
                deadline_seconds=float(deadline) if deadline is not None else None,
                max_total_requests=total_limit,
                max_retry_requests=retry_limit,
                max_consecutive_failures=optional_env_int(
                    "PROVIDER_JOB_MAX_CONSECUTIVE_FAILURES"
                ),
            ),
            initial_requests=initial_requests,
            retry_requests=retry_requests,
            lineage_initial_requests=lineage_initial,
            lineage_retry_requests=lineage_retry,
        )
        runtime.registered_jobs.add(job_id)
        runtime.latest_job_keys[durable_job_id] = job_id
    return job_id


async def public_review_request_baseline(operation_id):
    """Seed a review from its real workspace job and immutable public ledger."""
    from services.database import async_session_maker
    async with async_session_maker() as session:
        row = await require_public_review_job(session, operation_id=operation_id)
        return row.initial_request_count, row.retry_request_count, 1, 0


async def require_public_review_job(session, *, operation_id,
                                    expected_attempt_token=None, expected_fencing_token=None):
    """Validate durable review authority before registering or reserving spend."""
    from sqlalchemy import select
    from models.sql.public_admission import PublicOperationAdmission
    from models.sql.workspace import WorkspaceOperation, get_workspace_owner_scope
    from models.svg_quality_review import SvgQualityReviewRequest
    from services.public_operation_admission import _require_phase2_provider_contract
    from utils.datetime_utils import as_utc, get_current_utc_datetime
    from utils.owner_context import get_current_owner_id, get_current_tenant_id
    row = await session.scalar(select(PublicOperationAdmission).where(
        PublicOperationAdmission.operation_id == operation_id,
        PublicOperationAdmission.owner_id == get_current_owner_id(),
        PublicOperationAdmission.tenant_id == get_current_tenant_id(),
        PublicOperationAdmission.state == "active"))
    operation = await session.scalar(select(WorkspaceOperation).where(
        WorkspaceOperation.id == operation_id).with_for_update())
    if (row is None or operation is None or operation.kind != "quality.review"
            or operation.owner_id != row.owner_id or operation.state != "running"
            or operation.owner_scope != get_workspace_owner_scope()
            or operation.max_attempts != 1 or operation.attempt_number != 1
            or operation.cancel_requested_at is not None
            or operation.lease_expires_at is None
            or as_utc(operation.lease_expires_at) <= get_current_utc_datetime()
            or operation.expected_revision is None
            or row.planned_request_limit != 1 or row.retry_request_limit != 0):
        raise RuntimeError("public_review_admission_scope_invalid")
    SvgQualityReviewRequest.model_validate(operation.request_payload.get("context") or {})
    await _require_phase2_provider_contract(session, row,
        expected_attempt_token=expected_attempt_token,
        expected_fencing_token=expected_fencing_token)
    return row


async def verified_lineage_request_baseline(job_id):
    from uuid import UUID
    from services.database import async_session_maker
    from models.sql.presentation_generation_job import PresentationGenerationJob
    from models.sql.workspace import WorkspaceOperation
    from services.svg_timeout_repurchase import verified_append_only_consumption_baseline
    async with async_session_maker() as session:
        try:
            identity = UUID(str(job_id))
        except (ValueError, TypeError):
            return 0, 0, None, None
        job = await session.get(PresentationGenerationJob, identity)
        if job is None or not (job.request_payload or {}).get("_svg_recovery_lineage"):
            return 0, 0, None, None
        operation = await session.get(WorkspaceOperation, job.workspace_operation_id)
        return await verified_append_only_consumption_baseline(session, operation)
