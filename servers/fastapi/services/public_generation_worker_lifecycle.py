"""Public v2 generation-attempt lifecycle helpers.

This module keeps the public admission/fence/attempt authoritative while the
legacy generation-job row remains a compatibility projection.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from services.presentation_generation_attempt_timing import _safe_failure
from models.sql.workspace import WorkspaceOperation
from services.presentation_generation_job_service import (
    WorkspaceLinkedGenerationProjectionPoisoned,
)
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    WorkspaceOperationExecutionContext,
    WorkspaceOperationExecutionError,
    WorkspaceOperationLeaseLost,
)
from services.workspace_operation_retry_policy import WorkspaceOperationRetryDecision
from utils.datetime_utils import as_utc


PUBLIC_OPERATION_CONTRACT_VERSION = "public-operation/v2"
PUBLIC_CAPACITY_COMMIT_RESERVE_SECONDS = 10.0
_TERMINAL_PROVIDER_FAILURE_CODES = frozenset(
    {
        "retry_requires_an_initial_request",
        "retry_amplification_ceiling_exceeded",
    }
)
LinkedClaimCallable = Callable[
    [uuid.UUID, str, int],
    Awaitable[tuple[uuid.UUID, str] | None],
]


def clamp_retry_to_public_deadline(
    *,
    deadline_at: datetime | None,
    decision: WorkspaceOperationRetryDecision,
    current_retry_count: int,
    commit_reserve_seconds: int = 10,
) -> tuple[WorkspaceOperationRetryDecision, bool]:
    """Terminalize a retry whose first eligible instant misses the deadline."""

    deadline = as_utc(deadline_at) if deadline_at is not None else None
    retry_at = decision.next_attempt_at
    if retry_at is not None:
        retry_at = as_utc(retry_at)
    missed = bool(
        deadline is not None
        and decision.state == "queued"
        and (
            retry_at is None
            or deadline <= retry_at + timedelta(seconds=commit_reserve_seconds)
        )
    )
    if not missed:
        return decision, False
    return (
        WorkspaceOperationRetryDecision(
            state="deadline_exceeded",
            retry_count=current_retry_count,
            backoff_seconds=0,
            next_attempt_at=None,
        ),
        True,
    )


def recovery_retry_transition(
    *,
    stale: Mapping[str, object],
    decision: WorkspaceOperationRetryDecision,
    deadline_exceeded: bool,
    now: datetime,
) -> tuple[dict[str, object], dict[str, object]]:
    """Build the shared automatic-recovery event and durable projection."""

    code = (
        "public_deadline_exceeded"
        if deadline_exceeded
        else "operation_recovery_required"
    )
    attempt_token = stale.get("attempt_token")
    payload: dict[str, object] = {
        "state": decision.state,
        "error_code": code,
        "attempt_number": stale["attempt_number"],
        "fencing_token": stale["fencing_token"],
        "retryable": not deadline_exceeded,
        "retry_count": decision.retry_count,
        "max_attempts": stale["max_attempts"],
        "backoff_seconds": decision.backoff_seconds,
        "next_attempt_at": decision.next_attempt_at,
    }
    if attempt_token is not None:
        payload["reclaimed_attempt_token_sha256"] = sha256(
            str(attempt_token).encode("utf-8")
        ).hexdigest()
    projection: dict[str, object] = {
        "state": decision.state,
        "retry_count": decision.retry_count,
        "backoff_seconds": decision.backoff_seconds,
        "next_attempt_at": decision.next_attempt_at,
        "attempt_token": None,
        "lease_expires_at": None,
        "heartbeat_at": now,
        "error_code": code,
        "error_detail": {
            "error_code": code,
            "retryable": not deadline_exceeded,
        },
        "completed_at": (
            now
            if decision.state in {"failed", "dead_letter", "deadline_exceeded"}
            else None
        ),
    }
    return payload, projection


def durable_generation_failure(error: BaseException) -> tuple[str, bool]:
    """Reduce an external/provider exception to a stable durable decision."""

    if isinstance(error, WorkspaceOperationExecutionError):
        return error.code, error.retryable
    detail = getattr(error, "detail", None)
    if hasattr(detail, "model_dump"):
        detail = detail.model_dump(exclude_none=True)
    payload: dict[str, object] = {}
    if isinstance(detail, Mapping):
        payload.update(detail)
    elif isinstance(detail, str):
        payload["detail"] = detail
    code = getattr(error, "code", None)
    if isinstance(code, str) and code:
        payload["code"] = code
    retryable = getattr(error, "retryable", None)
    if isinstance(retryable, bool):
        payload["retryable"] = retryable
    status_code = getattr(error, "status_code", None)
    if isinstance(status_code, int) and not isinstance(status_code, bool):
        payload["status_code"] = status_code
    durable_code, durable_retryable = _safe_failure(payload)
    normalized_code = "".join(
        character
        if character in "abcdefghijklmnopqrstuvwxyz0123456789_"
        else "_"
        for character in durable_code.strip().lower()
    )[:128].strip("_")
    normalized_code = normalized_code or "presentation_generation_failed"
    if normalized_code in _TERMINAL_PROVIDER_FAILURE_CODES:
        # The provider runtime has already cancelled this logical job.  Retrying
        # the durable operation cannot create the missing initial request and
        # would otherwise keep its lease/heartbeat alive until recovery.
        return normalized_code, False
    return normalized_code, durable_retryable


async def public_generation_deadline(
    session_factory: async_sessionmaker[AsyncSession],
    context: WorkspaceOperationExecutionContext,
) -> datetime | None:
    claim = context.claim
    async with session_factory() as session:
        operation = await session.get(WorkspaceOperation, claim.operation_id)
        if (
            operation is None
            or operation.state != "running"
            or operation.attempt_token != claim.attempt_token
            or operation.fencing_token != claim.fencing_token
        ):
            raise WorkspaceOperationLeaseLost(
                "presentation_generation_lifecycle_fenced"
            )
        if operation.contract_version != PUBLIC_OPERATION_CONTRACT_VERSION:
            return None
        deadline = operation.deadline_at
        if deadline is None:
            raise NonRetryableWorkspaceOperationError(
                "public_operation_deadline_missing"
            )
        return as_utc(deadline)
async def claim_linked_generation_job(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    context: WorkspaceOperationExecutionContext,
    claim_linked: LinkedClaimCallable,
) -> tuple[uuid.UUID, str]:
    """Use the compatibility projection already joined to operation claim."""

    claim = context.claim
    public_deadline = await public_generation_deadline(session_factory, context)
    try:
        if public_deadline is None:
            linked_claim = await claim_linked(
                claim.operation_id,
                claim.attempt_token,
                claim.attempt_number,
            )
        else:
            claim_window = (
                public_deadline
                - datetime.now(timezone.utc)
                - timedelta(seconds=PUBLIC_CAPACITY_COMMIT_RESERVE_SECONDS)
            ).total_seconds()
            if claim_window <= 0:
                raise NonRetryableWorkspaceOperationError(
                    "public_deadline_exceeded",
                    detail={"error_code": "public_deadline_exceeded"},
                )
            try:
                async with asyncio.timeout(claim_window):
                    linked_claim = await claim_linked(
                        claim.operation_id,
                        claim.attempt_token,
                        claim.attempt_number,
                    )
            except TimeoutError as error:
                raise NonRetryableWorkspaceOperationError(
                    "public_deadline_exceeded",
                    detail={"error_code": "public_deadline_exceeded"},
                ) from error
    except WorkspaceLinkedGenerationProjectionPoisoned as error:
        raise NonRetryableWorkspaceOperationError(error.code) from error
    if linked_claim is None:
        raise WorkspaceOperationLeaseLost(
            "presentation_generation_compatibility_claim_lost"
        )
    return linked_claim


async def require_public_generation_publication(
    *,
    session: AsyncSession,
    context: WorkspaceOperationExecutionContext,
) -> WorkspaceOperation:
    """Reject a public handler success without its durable publication CAS."""

    operation = await session.get(
        WorkspaceOperation,
        context.claim.operation_id,
        populate_existing=True,
    )
    if operation is None:
        raise NonRetryableWorkspaceOperationError(
            "public_operation_authority_unavailable"
        )
    if operation.contract_version == PUBLIC_OPERATION_CONTRACT_VERSION and (
        operation.state != "succeeded"
        or operation.result_revision is None
        or not isinstance(operation.result, Mapping)
    ):
        raise NonRetryableWorkspaceOperationError(
            "public_generation_publication_missing"
        )
    return operation
