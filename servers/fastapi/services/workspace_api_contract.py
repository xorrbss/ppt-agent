"""Public v1 projection helpers for workspace timelines and receipts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.workspace import (
    WorkspaceActionResponse,
    WorkspaceEventPageResponse,
    WorkspaceTimelineResponse,
)
from models.workspace_operation_kinds import get_workspace_operation_kind
from services.workspace_error_registry import resolve_workspace_error
from models.sql.workspace import WorkspaceOperation, get_workspace_owner_scope


def _as_utc(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def allowed_workspace_actions(
    operation: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Derive action controls from durable state; clients never guess them."""

    status = str(operation.get("status") or "")
    operation_type = str(operation.get("operation_type") or "")
    if get_workspace_operation_kind(operation_type) is None:
        return []
    actions: list[dict[str, Any]] = []
    current = now or datetime.now(timezone.utc)
    approval_expires_at = _as_utc(operation.get("approval_expires_at"))
    if status in {"approval_required", "awaiting_approval"}:
        if approval_expires_at is None or approval_expires_at > current:
            actions.append(
                {
                    "id": "approve",
                    "label": "Approve",
                    "variant": "primary",
                    "enabled": True,
                }
            )
        else:
            actions.append(
                {
                    "id": "approve",
                    "label": "Approve",
                    "variant": "primary",
                    "enabled": False,
                    "reason": "approval_expired",
                }
            )
    if status in {
        "queued",
        "waiting_for_assets",
        "blocked",
        "running",
        "approval_required",
        "awaiting_approval",
    }:
        actions.append(
            {
                "id": "cancel",
                "label": "Cancel",
                "variant": "danger",
                "enabled": True,
            }
        )
    if status in {"failed", "cancelled", "dead_letter", "blocked"} and (
        operation.get("retryable") is not False
    ):
        actions.append(
            {
                "id": "retry",
                "label": "Retry",
                "variant": "primary",
                "enabled": True,
            }
        )
    result = operation.get("result")
    if (
        status == "succeeded"
        and operation_type in {"export.pptx", "export.pdf"}
        and isinstance(result, Mapping)
        and result.get("artifact_id")
    ):
        actions.append(
            {
                "id": "download",
                "label": "Download",
                "variant": "primary",
                "enabled": True,
            }
        )
    elif status == "succeeded":
        # A visible, typed disabled control is safer than implying that a
        # successful mutation is reversible when the backend has no undo action.
        actions.append(
            {
                "id": "undo",
                "label": "Undo",
                "variant": "secondary",
                "enabled": False,
                "reason": "workspace_undo_not_supported",
            }
        )
    return actions


_STATUS_SUMMARIES = {
    "queued": "queued",
    "waiting_for_assets": "waiting for attachments",
    "blocked": "blocked",
    "running": "in progress",
    "approval_required": "awaiting approval",
    "awaiting_approval": "awaiting approval",
    "cancelling": "cancelling",
    "succeeded": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
    "dead_letter": "stopped after repeated failures",
}


def _operation_card_context(
    operation: Mapping[str, Any],
    *,
    presentation_id: uuid.UUID | None,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Build a useful card projection without exposing request payload text."""

    operation_type = str(operation.get("operation_type") or "")
    status = str(operation.get("status") or "")
    spec = get_workspace_operation_kind(operation_type)
    actor = {"type": "user", "label": "You"}
    target = {
        "presentation_id": presentation_id,
        "summary": spec.target_summary if spec is not None else "Unsupported operation",
        # Target selectors live in private request payloads.  Until each
        # command has an explicit allow-list, do not reflect arbitrary context.
        "context": {},
    }
    activity = spec.activity_label if spec is not None else "Unsupported workspace operation"
    status_summary = _STATUS_SUMMARIES.get(status, "updated")
    return actor, target, f"{activity} {status_summary}."


def _project_event(value: Mapping[str, Any]) -> dict[str, Any]:
    event = dict(value)
    event["schema_version"] = 1
    # Runner events record their outcome as `state`, but the public contract
    # carries it as `status` and that is the only field a client applies to its
    # operation card. Without this the terminal event announces a version bump
    # and nothing else, leaving a finished operation displayed as still queued.
    if event.get("status") is None:
        state = event.get("state")
        if isinstance(state, str) and state:
            event["status"] = state
    # Never reflect a private/internal message supplied by the durable event.
    # Public text is derived only from the central allow-listed registry.
    event["safe_message"] = None
    error = event.get("error")
    if error is not None:
        retryable = (
            event.get("retryable")
            if isinstance(event.get("retryable"), bool)
            else None
        )
        error_code: object = error
        if isinstance(error, Mapping):
            error_code = error.get("code") or error.get("detail")
            if isinstance(error.get("retryable"), bool):
                retryable = error["retryable"]
        definition = resolve_workspace_error(
            error_code,
            retryable=retryable,
        )
        event["error"] = definition.code
        event["safe_message"] = definition.safe_message
        event["retryable"] = definition.retryable
    return event


def project_workspace_timeline(
    value: Mapping[str, Any],
    *,
    presentation_id: uuid.UUID | None = None,
) -> WorkspaceTimelineResponse:
    """Validate and enrich the private SQL projection at the HTTP boundary."""

    projected = dict(value)
    items: list[dict[str, Any]] = []
    for raw_item in value.get("items", []):
        item = dict(raw_item)
        item["schema_version"] = 1
        anchor_type = item.get("anchor_type")
        if anchor_type == "message":
            item["attachments"] = [
                {"schema_version": 1, **dict(attachment)}
                for attachment in item.get("attachments", [])
            ]
        elif anchor_type == "operation":
            item["operation_version"] = item.get("version")
            actor, target, activity_summary = _operation_card_context(
                item,
                presentation_id=presentation_id,
            )
            item["actor"] = actor
            item["target"] = target
            item["activity_summary"] = activity_summary
            item["allowed_actions"] = allowed_workspace_actions(item)
            operation_spec = get_workspace_operation_kind(item.get("operation_type"))
            error = (
                item.get("error")
                if operation_spec is not None
                else "unsupported_operation_type"
            )
            if error is not None:
                definition = resolve_workspace_error(
                    error,
                    retryable=(
                        False
                        if operation_spec is None
                        else item.get("retryable")
                        if isinstance(item.get("retryable"), bool)
                        else None
                    ),
                )
                item["error"] = definition.code
                item["error_message"] = definition.safe_message
                item["retryable"] = definition.retryable
            else:
                # A stale/private message must never survive when there is no
                # registered public error code to authorize it.
                item["error"] = None
                item["error_message"] = None
                item["retryable"] = None
            item["events"] = [
                _project_event(event) for event in item.get("events", [])
            ]
        else:
            raise ValueError("unsupported workspace timeline anchor")
        items.append(item)
    projected["items"] = items
    return WorkspaceTimelineResponse.model_validate(projected)


def project_workspace_event_page(
    value: Mapping[str, Any],
) -> WorkspaceEventPageResponse:
    projected = dict(value)
    projected["events"] = [
        _project_event(event) for event in value.get("events", [])
    ]
    return WorkspaceEventPageResponse.model_validate(projected)


def workspace_receipt_response(
    *,
    receipt_id: uuid.UUID,
    operation_id: uuid.UUID,
    status: str,
    duplicate: bool,
    timeline: WorkspaceTimelineResponse,
) -> WorkspaceActionResponse:
    """Build the receipt envelope while retaining legacy bare-timeline fields."""

    return WorkspaceActionResponse(
        receipt_id=receipt_id,
        operation_id=operation_id,
        status=status,
        duplicate=duplicate,
        timeline=timeline,
        conversation_id=timeline.conversation_id,
        items=timeline.items,
        next_before_position=timeline.next_before_position,
        event_high_watermark=timeline.event_high_watermark,
        has_more=timeline.has_more,
        revision=timeline.revision,
    )


async def resolve_workspace_operation_presentation_id(
    session: AsyncSession,
    operation_id: uuid.UUID,
) -> uuid.UUID | None:
    """Resolve cohort scope without mutating or disclosing another owner's row."""

    return (
        await session.execute(
            select(WorkspaceOperation.presentation_id).where(
                WorkspaceOperation.id == operation_id,
                WorkspaceOperation.owner_scope == get_workspace_owner_scope(),
            )
        )
    ).scalar_one_or_none()
