"""Safe workspace timeline projection and retry eligibility."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.workspace import (
    WorkspaceOperationActionRequest, canonical_workspace_key, is_sensitive_workspace_key,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.workspace import WorkspaceOperation, WorkspaceOperationEvent
from services.workspace_recovery_revision import AUTHORED_CAPACITY_BOUNDED_REPAIR_REVISION
from services.workspace_payload_sanitizers import sanitize_public_progress_fields
from utils.owner_context import get_current_owner_id, get_current_tenant_id


_PRIVATE_PAYLOAD_KEYS = {
    "svg_partial_recovery_archive",
    "svg_partial_recovery_manifest",
    "attachment_file_name",
    "attachment_url",
    "cookie",
    "credentials",
    "file_path",
    "prompt",
    "provider_response",
    "stack",
    "stack_trace",
    "storage_key",
    "token",
    "trace",
}


def _safe_payload(value: Any) -> Any:
    """Remove operational secrets from private timeline projection responses."""

    if isinstance(value, Mapping):
        return {
            str(key): _safe_payload(child)
            for key, child in value.items()
            if canonical_workspace_key(key) not in _PRIVATE_PAYLOAD_KEYS
            and not is_sensitive_workspace_key(key)
        }
    if isinstance(value, list):
        return [_safe_payload(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_payload(item) for item in value]
    return value


def _linked_generation_job_id(operation: WorkspaceOperation) -> uuid.UUID | None:
    if operation.kind != "presentation.generate" or not isinstance(
        operation.request_payload, Mapping
    ):
        return None
    raw_job_id = operation.request_payload.get("job_id")
    if raw_job_id is None:
        return None
    try:
        return uuid.UUID(str(raw_job_id))
    except (TypeError, ValueError, AttributeError):
        return None


def _event_status(payload: Any) -> str | None:
    """Return the event's operation status, accepting the runner's ``state`` key."""

    if not isinstance(payload, dict):
        return None
    for key in ("status", "state"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _event_to_wire(
    event: WorkspaceOperationEvent,
    *,
    operation_anchor_position: int | None = None,
    operation_retry_count: int | None = None,
    operation_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _safe_payload(event.payload or {})
    queue_position = payload.get("queue_position") if isinstance(payload, dict) else None
    if (
        isinstance(queue_position, bool)
        or not isinstance(queue_position, int)
        or queue_position < 1
    ):
        queue_position = None
    retry_count = payload.get("retry_count") if isinstance(payload, dict) else None
    if isinstance(retry_count, bool) or not isinstance(retry_count, int) or retry_count < 0:
        retry_count = operation_retry_count
    provider_attempt_count = None
    for candidate in (
        operation_result,
        (
            operation_result.get("generation_timing_receipt")
            if isinstance(operation_result, Mapping)
            else None
        ),
    ):
        if not isinstance(candidate, Mapping):
            continue
        raw_count = candidate.get(
            "providerAttemptCount", candidate.get("provider_attempt_count")
        )
        if (
            not isinstance(raw_count, bool)
            and isinstance(raw_count, int)
            and raw_count >= 0
        ):
            provider_attempt_count = min(raw_count, 1_000_000)
            break
    reason_code = payload.get("reason_code") if isinstance(payload, dict) else None
    error_code = payload.get("error_code") if isinstance(payload, dict) else None
    recovery_reason = reason_code
    if recovery_reason is None and error_code == "operation_recovery_required":
        recovery_reason = error_code
    if not (
        isinstance(recovery_reason, str)
        and 1 <= len(recovery_reason) <= 128
        and all(char.isalnum() or char in "_.-" for char in recovery_reason)
    ):
        recovery_reason = None
    event_state = (
        payload.get("state", payload.get("status"))
        if isinstance(payload, dict)
        else None
    )
    if retry_count is None:
        recovery_status = "unavailable"
    elif retry_count == 0 and recovery_reason is None:
        recovery_status = "not_required"
    elif event_state in {"queued", "waiting_for_assets", "blocked"}:
        recovery_status = "scheduled"
    elif event_state in {"running", "cancelling"}:
        recovery_status = "in_progress"
    elif event_state == "succeeded":
        recovery_status = "recovered"
    elif event_state in {"failed", "dead_letter", "cancelled"}:
        recovery_status = "failed"
    else:
        recovery_status = "required"
    return {
        "id": f"{event.operation_id}:{event.sequence}",
        "operation_id": str(event.operation_id),
        "operation_version": event.operation_version,
        "sequence": event.sequence,
        "cursor": str(event.cursor) if event.cursor is not None else None,
        "operation_anchor_position": operation_anchor_position,
        "type": event.event_type,
        # The runner records an outcome under ``state`` and the public contract
        # carries it as ``status``; the client applies only ``status`` to its
        # operation card. Reading ``status`` alone shipped every terminal
        # transition as a version bump with no outcome, so a dead-lettered or
        # deadline-exceeded generation stayed "in progress" at its last percent
        # with a cancel button until the page was reloaded.
        "status": _event_status(payload),
        "message": payload.get("message") if isinstance(payload, dict) else None,
        "progress": (
            payload.get("progress", payload.get("percent"))
            if isinstance(payload, dict)
            else None
        ),
        **sanitize_public_progress_fields(payload if isinstance(payload, dict) else {}),
        "error": (
            payload.get("error_code", payload.get("error"))
            if isinstance(payload, dict)
            else None
        ),
        "retryable": (
            payload.get("retryable")
            if isinstance(payload, dict)
            and isinstance(payload.get("retryable"), bool)
            else None
        ),
        "providerAttemptCount": provider_attempt_count,
        "retryCount": retry_count,
        "recoveryReason": recovery_reason,
        "recoveryStatus": recovery_status,
        "capacity_waiting": (
            payload.get("capacity_waiting")
            if isinstance(payload, dict)
            and isinstance(payload.get("capacity_waiting"), bool)
            else None
        ),
        "queue_position": queue_position,
        "result": payload.get("result") if isinstance(payload, dict) else None,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def _is_retryable_raster_tenant_handoff(operation: WorkspaceOperation) -> bool:
    if operation.kind != "presentation.generate" or operation.error_code != "raster_tenant_required":
        return False
    payload = operation.request_payload
    generation_request = payload.get("generation_request") if isinstance(payload, dict) else None
    return bool(
        isinstance(generation_request, dict)
        and generation_request.get("source_mode") == "raster_reconstruction"
        and generation_request.get("strategy") == "svg"
        and isinstance(generation_request.get("raster_approval_pin"), dict)
        and len(generation_request.get("reference_snapshot_ids") or ()) == 1
    )


async def _verify_raster_tenant_handoff_retry(session: AsyncSession, operation: WorkspaceOperation) -> bool:
    if not _is_retryable_raster_tenant_handoff(operation):
        return False
    from services.presentation_generation_pipeline import _request_from_durable_payload
    from services.source_content.pipeline import SourceContentError, admit_source_content_request
    owner, tenant = get_current_owner_id(), get_current_tenant_id()
    if owner is None or owner != operation.owner_id or not tenant:
        return False
    job = await session.scalar(select(PresentationGenerationJob).where(
        PresentationGenerationJob.workspace_operation_id == operation.id,
        PresentationGenerationJob.presentation_id == operation.presentation_id,
        PresentationGenerationJob.state == "failed",
    ))
    linked_id = _linked_generation_job_id(operation)
    if (job is None or linked_id is None or job.id != linked_id
            or job.request_payload != operation.request_payload.get("generation_request")):
        return False
    try:
        request = _request_from_durable_payload(job.request_payload)
        await admit_source_content_request(
            request, sql_session=session, owner_id=str(owner), tenant_id=tenant,
        )
    except (SourceContentError, ValueError, TypeError):
        return False
    return True


def _operation_retryable(operation: WorkspaceOperation) -> bool | None:
    """Project only the safe retry decision, never raw error details."""

    if operation.state in {"failed", "dead_letter"}:
        if _is_retryable_raster_tenant_handoff(operation):
            return True
        detail = operation.error_detail
        return not (isinstance(detail, dict) and detail.get("retryable") is False)
    if operation.state in {"cancelled", "blocked"}:
        # Historical rows did not persist retryability, and cancelled work is
        # always safe to submit as a new operation.
        return True
    return None


def _is_explicit_authored_capacity_recovery(
    operation: WorkspaceOperation,
    request: WorkspaceOperationActionRequest,
) -> bool:
    """Admit only the deterministic authored-capacity repair revision.

    This is intentionally not projected as ``retryable=true``. The original
    failure remains terminal; an operator must submit the exact server-supported
    recovery revision after deploying the bounded one-cycle repair.
    """

    if request.recovery_revision != AUTHORED_CAPACITY_BOUNDED_REPAIR_REVISION:
        return False
    if (
        request.action != "retry"
        or operation.kind != "presentation.generate"
        or operation.state not in {"failed", "dead_letter"}
        or operation.error_code != "authored_slide_capacity_exceeded"
    ):
        return False
    if not (
        isinstance(operation.error_detail, dict)
        and operation.error_detail.get("retryable") is False
    ):
        return False
    payload = operation.request_payload
    generation_request = (
        payload.get("generation_request") if isinstance(payload, dict) else None
    )
    return bool(
        isinstance(generation_request, dict)
        and generation_request.get("strategy") == "authored"
        and _linked_generation_job_id(operation) is not None
    )


def _content_message_fields(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """``content_key``/``content_params`` for a server-written user message.

    ``create_workspace_command`` records the ``UserMessage`` behind a command
    it wrote on the user's behalf; the frontend renders that key in the UI
    locale and falls back to ``content`` for user-typed commands.
    """

    if not isinstance(metadata, Mapping):
        return {}
    key = metadata.get("content_key")
    if not isinstance(key, str) or not key:
        return {}
    params = metadata.get("content_params")
    fields: dict[str, Any] = {"content_key": key}
    if isinstance(params, Mapping) and params:
        fields["content_params"] = _safe_payload(dict(params))
    return fields
