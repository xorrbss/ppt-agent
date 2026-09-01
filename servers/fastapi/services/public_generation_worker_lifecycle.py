"""Public v2 generation-attempt lifecycle helpers.

This module keeps the public admission/fence/attempt authoritative while the
legacy generation-job row remains a compatibility projection.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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


PUBLIC_OPERATION_CONTRACT_VERSION = "public-operation/v2"
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

    deadline = (
        deadline_at
        if deadline_at is None or deadline_at.tzinfo is not None
        else deadline_at.replace(tzinfo=timezone.utc)
    )
    retry_at = decision.next_attempt_at
    if retry_at is not None and retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
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


def _safe_failure(payload: Mapping[str, object]) -> tuple[str, bool]:
    detail = payload.get("detail")
    nested = detail if isinstance(detail, Mapping) else {}
    code = payload.get("code") or nested.get("code")
    if not code and isinstance(detail, str):
        code = detail
    code = str(code or "presentation_generation_failed")[:128]
    explicit_retryable = payload.get("retryable", nested.get("retryable"))
    if isinstance(explicit_retryable, bool):
        return code, explicit_retryable
    status_code = payload.get("status_code")
    try:
        numeric_status = int(status_code)
    except (TypeError, ValueError):
        numeric_status = 500
    return code, numeric_status == 429 or numeric_status >= 500


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
    return normalized_code or "presentation_generation_failed", durable_retryable


async def claim_linked_generation_job(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    context: WorkspaceOperationExecutionContext,
    claim_linked: LinkedClaimCallable,
) -> tuple[uuid.UUID, str]:
    """Use the compatibility projection already joined to operation claim."""

    claim = context.claim
    try:
        linked_claim = await claim_linked(
            claim.operation_id,
            claim.attempt_token,
            claim.attempt_number,
        )
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
