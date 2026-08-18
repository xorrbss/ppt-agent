"""Metadata-only observations for chat-first workspace operations.

The schema is intentionally closed.  It accepts durable identifiers and bounded
operational counters, but has no extension bag and no parameters for prompts,
message content, attachment names/paths, lease tokens, secrets or raw errors.
Logging is best-effort so telemetry can never change an operation transaction.
"""

from __future__ import annotations

import json
import logging
from math import isfinite
from typing import Literal
import uuid


LOGGER = logging.getLogger(__name__)

WorkspaceOperationObservationName = Literal[
    "accepted",
    "claimed",
    "running",
    "retry_scheduled",
    "cancel_requested",
    "cancelled",
    "recovered",
    "succeeded",
    "failed",
    "dead_letter",
    "blocked",
    "lease_lost",
    "released",
    "runner_fault",
]

_NAMES = frozenset(
    {
        "accepted",
        "claimed",
        "running",
        "retry_scheduled",
        "cancel_requested",
        "cancelled",
        "recovered",
        "succeeded",
        "failed",
        "dead_letter",
        "blocked",
        "lease_lost",
        "released",
        "runner_fault",
    }
)
_COMMAND_TYPES = frozenset(
    {
        "presentation.generate",
        "presentation.edit",
        "presentation.delete",
        "slide.edit",
        "slide.regenerate",
        "slide.regenerate.batch",
        "slide.delete",
        "version.restore",
        "template.apply",
        "design.apply",
        "quality.review",
        "export.pptx",
        "export.pdf",
    }
)
_RESULT_STATUSES = frozenset(
    {
        "queued",
        "waiting_for_assets",
        "blocked",
        "approval_required",
        "awaiting_approval",
        "running",
        "cancelling",
        "succeeded",
        "failed",
        "cancelled",
        "dead_letter",
    }
)
_ERROR_CODES = frozenset(
    {
        "app_data_directory_required",
        "asset_not_ready",
        "authored_batch_remake_all_slides_failed",
        "authored_batch_remake_master_pinned_unsupported",
        "authored_batch_remake_request_invalid",
        "authored_cluster_capacity_unavailable",
        "invalid_operation_handler_result",
        "operation_lease_lost",
        "operation_recovery_required",
        "presentation_edit_failed",
        "presentation_generation_failed",
        "presentation_generation_job_missing",
        "presentation_generation_job_not_linked",
        "presentation_not_found",
        "revision_conflict",
        "stale_presentation_revision",
        "unsafe_app_data_directory",
        "unsupported_operation_type",
        "workspace_artifact_revision_invalid",
        "workspace_artifact_source_missing",
        "workspace_artifact_source_outside_app_data",
        "workspace_asset_deleted",
        "workspace_asset_expired",
        "workspace_asset_failed",
        "workspace_asset_quarantined",
        "workspace_command_missing",
        "workspace_expected_revision_required",
        "workspace_operation_internal_error",
        "workspace_operation_lease_lost",
        "version_not_found",
        "version_restore_failed",
        "version_restore_target_invalid",
        "version_snapshot_invalid",
    }
)
_FENCE_OUTCOMES = frozenset(
    {
        "claim_acquired",
        "transition_committed",
        "publish_committed",
        "lease_lost",
        "released",
        "recovery_committed",
        "not_applicable",
    }
)
_MAX_COUNTER = 1_000_000_000
_MAX_DURATION_MS = 30 * 24 * 60 * 60 * 1000


def _uuid_text(value: uuid.UUID | str | None, *, field: str) -> str | None:
    if value is None:
        return None
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError(f"{field} must be a UUID") from error


def _bounded_int(
    value: int | None,
    *,
    field: str,
    maximum: int = _MAX_COUNTER,
) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if value < 0 or value > maximum:
        raise ValueError(f"{field} is outside the supported range")
    return value


def _duration(value: int | float | None, *, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    if not isfinite(float(value)) or value < 0 or value > _MAX_DURATION_MS:
        raise ValueError(f"{field} is outside the supported range")
    return round(float(value))


def build_workspace_operation_observation(
    *,
    name: WorkspaceOperationObservationName,
    operation_id: uuid.UUID | str | None = None,
    presentation_id: uuid.UUID | str | None = None,
    conversation_id: uuid.UUID | str | None = None,
    command_type: str | None = None,
    result_status: str | None = None,
    error_code: str | None = None,
    duration_ms: int | float | None = None,
    event_lag_ms: int | float | None = None,
    attempt_number: int | None = None,
    retry_count: int | None = None,
    recovery_count: int | None = None,
    expected_revision: int | None = None,
    result_revision: int | None = None,
    fencing_token: int | None = None,
    revision_conflict: bool | None = None,
    fence_outcome: str | None = None,
) -> dict[str, object]:
    """Build the closed v1 workspace-operation observation payload."""

    if name not in _NAMES:
        raise ValueError("unsupported workspace operation observation")
    if result_status is not None and result_status not in _RESULT_STATUSES:
        raise ValueError("unsupported workspace operation status")
    if fence_outcome is not None and fence_outcome not in _FENCE_OUTCOMES:
        raise ValueError("unsupported workspace operation fence outcome")
    if revision_conflict is not None and not isinstance(revision_conflict, bool):
        raise ValueError("revision_conflict must be boolean")

    event: dict[str, object] = {
        "schema_version": 1,
        "pipeline": "workspace_operation",
        "name": name,
    }
    identifiers = {
        "operation_id": _uuid_text(operation_id, field="operation_id"),
        "presentation_id": _uuid_text(presentation_id, field="presentation_id"),
        "conversation_id": _uuid_text(conversation_id, field="conversation_id"),
    }
    event.update({key: value for key, value in identifiers.items() if value is not None})

    if command_type is not None:
        event["command_type"] = (
            command_type if command_type in _COMMAND_TYPES else "other"
        )
    if result_status is not None:
        event["result_status"] = result_status
    if error_code is not None:
        event["error_code"] = error_code if error_code in _ERROR_CODES else "other"
    if fence_outcome is not None:
        event["fence_outcome"] = fence_outcome
    if revision_conflict is not None:
        event["revision_conflict"] = revision_conflict

    numeric_values = {
        "duration_ms": _duration(duration_ms, field="duration_ms"),
        "event_lag_ms": _duration(event_lag_ms, field="event_lag_ms"),
        "attempt_number": _bounded_int(attempt_number, field="attempt_number"),
        "retry_count": _bounded_int(retry_count, field="retry_count"),
        "recovery_count": _bounded_int(recovery_count, field="recovery_count"),
        "expected_revision": _bounded_int(
            expected_revision, field="expected_revision"
        ),
        "result_revision": _bounded_int(result_revision, field="result_revision"),
        "fencing_token": _bounded_int(fencing_token, field="fencing_token"),
    }
    event.update({key: value for key, value in numeric_values.items() if value is not None})
    return event


def log_workspace_operation_observation(
    *,
    name: WorkspaceOperationObservationName,
    operation_id: uuid.UUID | str | None = None,
    presentation_id: uuid.UUID | str | None = None,
    conversation_id: uuid.UUID | str | None = None,
    command_type: str | None = None,
    result_status: str | None = None,
    error_code: str | None = None,
    duration_ms: int | float | None = None,
    event_lag_ms: int | float | None = None,
    attempt_number: int | None = None,
    retry_count: int | None = None,
    recovery_count: int | None = None,
    expected_revision: int | None = None,
    result_revision: int | None = None,
    fencing_token: int | None = None,
    revision_conflict: bool | None = None,
    fence_outcome: str | None = None,
    logger: logging.Logger = LOGGER,
) -> dict[str, object] | None:
    """Emit one best-effort structured observation without workflow coupling."""

    try:
        event = build_workspace_operation_observation(
            name=name,
            operation_id=operation_id,
            presentation_id=presentation_id,
            conversation_id=conversation_id,
            command_type=command_type,
            result_status=result_status,
            error_code=error_code,
            duration_ms=duration_ms,
            event_lag_ms=event_lag_ms,
            attempt_number=attempt_number,
            retry_count=retry_count,
            recovery_count=recovery_count,
            expected_revision=expected_revision,
            result_revision=result_revision,
            fencing_token=fencing_token,
            revision_conflict=revision_conflict,
            fence_outcome=fence_outcome,
        )
        # The exporter is intentionally imported lazily to keep this builder
        # usable in startup-critical paths and to make metrics non-authoritative.
        from services.workspace_metrics import WORKSPACE_METRICS

        try:
            logger.info(
                "workspace_operation %s",
                json.dumps(event, sort_keys=True, separators=(",", ":")),
            )
        except Exception:
            WORKSPACE_METRICS.record_signal_failure("operation_observation")
        try:
            WORKSPACE_METRICS.observe_operation(event)
        except Exception:
            # The exporter itself is the failed sink, so only a safe logger
            # marker is available. Never include event contents here.
            try:
                logger.warning("Workspace operation metric recording failed")
            except Exception:
                pass
        return event
    except Exception:
        # Observability is deliberately non-authoritative.  In particular,
        # logger/transport failures must not roll back a published revision.
        return None
