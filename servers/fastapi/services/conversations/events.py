"""Standard event projection over Workspace cursor retention and replay."""

from __future__ import annotations

from datetime import timezone
from typing import Any, Protocol
import uuid

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.sql.workspace import (
    WorkspaceMessage,
    WorkspaceOperation,
    WorkspaceOperationEvent,
)
from services.conversation_event_contract import (
    ConversationEventType,
    UnifiedConversationEvent,
    build_conversation_event,
)
from services.conversations.service import _conversation_and_presentation
from services.workspace_error_registry import resolve_workspace_error
from services.workspace_service import workspace_events


class ConversationEventPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, frozen=True)
    conversation_id: uuid.UUID
    events: list[UnifiedConversationEvent] = Field(default_factory=list)
    event_high_watermark: str | None = None


class LiveConversationEventSource(Protocol):
    """Optional process-local transient delta source used only by SSE.

    Implementations must never allocate durable cursors. Reconnect recovery is
    always the Workspace-backed assistant.checkpoint projection below.
    """

    async def next_event(
        self, *, conversation_id: uuid.UUID, after_checkpoint: str | None
    ) -> UnifiedConversationEvent | None: ...


def _event_type(
    event: WorkspaceOperationEvent,
    operation: WorkspaceOperation,
) -> ConversationEventType:
    try:
        candidate = ConversationEventType(event.event_type)
        # Token deltas are live-only and must never be projected from durable
        # SQL. A provider adapter may persist only the reconnect checkpoint.
        if candidate == ConversationEventType.ASSISTANT_DELTA:
            return ConversationEventType.ASSISTANT_CHECKPOINT
        return candidate
    except ValueError:
        pass

    state = str((event.payload or {}).get("state") or operation.state)
    if event.event_type in {"queued", "waiting_for_assets"} or state in {
        "queued",
        "waiting_for_assets",
    }:
        return ConversationEventType.RUN_QUEUED
    if event.event_type == "running" or (
        event.event_type == "operation.progress"
        and (event.payload or {}).get("stage") == "started"
    ):
        return ConversationEventType.RUN_STARTED
    if event.event_type == "approval_required" or state in {
        "approval_required",
        "awaiting_approval",
    }:
        return ConversationEventType.APPROVAL_REQUIRED
    if event.event_type in {"operation.error"} or state in {
        "failed",
        "dead_letter",
    }:
        return ConversationEventType.RUN_FAILED
    if event.event_type == "operation.result" and operation.result_revision is not None:
        source = operation.expected_revision
        if source is not None and operation.result_revision > source:
            return ConversationEventType.PRESENTATION_PUBLISHED
    if event.event_type == "operation.result" or state == "succeeded":
        return ConversationEventType.RUN_COMPLETED
    return ConversationEventType.RUN_STATUS


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value is not None else None
    except (TypeError, ValueError):
        return None


def _event_message_id(
    event: WorkspaceOperationEvent,
    operation: WorkspaceOperation,
) -> uuid.UUID | None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    return _uuid(payload.get("message_id")) or _uuid(
        operation.request_payload.get("user_message_id")
    )


def _event_data(
    event_type: ConversationEventType,
    event: WorkspaceOperationEvent,
    operation: WorkspaceOperation,
    message: WorkspaceMessage | None,
) -> tuple[dict[str, Any], uuid.UUID | None]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    state = str(payload.get("state") or payload.get("status") or operation.state)
    data: dict[str, Any] = {"status": state, "run_version": event.operation_version}
    progress = payload.get("progress", payload.get("percent"))
    if isinstance(progress, (int, float)):
        data["progress"] = progress
    message_id = _event_message_id(event, operation)

    if event_type == ConversationEventType.MESSAGE_CREATED:
        # The event log intentionally stores only a stable message reference.
        # Resolve content from the authoritative message row at projection time
        # instead of duplicating user text into operation payloads/outbox rows.
        if message is None or message.id != message_id:
            data["message_unavailable"] = True
            return data, message_id
        data = {
            "role": message.role,
            "content": message.content[:100_000],
        }
        return data, message_id

    if event_type == ConversationEventType.ASSISTANT_CHECKPOINT:
        assistant_message_id = _uuid(payload.get("message_id"))
        assistant_text = payload.get("assistant_text", payload.get("content"))
        if assistant_message_id is None or not isinstance(assistant_text, str):
            # A malformed legacy checkpoint cannot satisfy the standard
            # contract. Keep the durable cursor visible as run.status instead.
            data["checkpoint_unavailable"] = True
            return data, message_id
        data = {"content": assistant_text[:100_000]}
        return data, assistant_message_id

    if event_type in {
        ConversationEventType.TOOL_STARTED,
        ConversationEventType.TOOL_COMPLETED,
    }:
        tool_name = payload.get("tool_name")
        if isinstance(tool_name, str):
            data["tool"] = tool_name[:128]
        summary = payload.get("tool_summary")
        if isinstance(summary, str):
            data["summary"] = summary[:500]

    if event_type == ConversationEventType.APPROVAL_REQUIRED:
        # Approval is a state transition on the run itself; the run UUID is
        # therefore the stable approval identity used by action endpoints.
        data["approval_id"] = str(operation.id)
        if operation.approval_expires_at is not None:
            data["expires_at"] = operation.approval_expires_at.isoformat()

    if event_type == ConversationEventType.PRESENTATION_PUBLISHED:
        data["revision"] = int(operation.result_revision or 0)
        data["source_revision"] = operation.expected_revision
    if event_type in {
        ConversationEventType.RUN_COMPLETED,
        ConversationEventType.PRESENTATION_PUBLISHED,
    }:
        if isinstance(operation.result, dict):
            # Results are already sanitized by WorkspaceOperationRunner. Keep
            # the nested object intact but never expose request/provider data.
            data["result"] = operation.result
    if event_type == ConversationEventType.RUN_FAILED:
        definition = resolve_workspace_error(
            operation.error_code,
            retryable=(
                operation.error_detail.get("retryable")
                if isinstance(operation.error_detail, dict)
                and isinstance(operation.error_detail.get("retryable"), bool)
                else None
            ),
        )
        data.update(
            {
                "error_code": definition.code,
                "safe_message": definition.safe_message,
                "retryable": definition.retryable,
            }
        )
    return data, message_id


def _project_event(
    *,
    conversation_id: uuid.UUID,
    presentation_id: uuid.UUID,
    event: WorkspaceOperationEvent,
    operation: WorkspaceOperation,
    message: WorkspaceMessage | None,
) -> UnifiedConversationEvent:
    event_type = _event_type(event, operation)
    data, message_id = _event_data(event_type, event, operation, message)
    # Invalid durable checkpoint rows are downgraded to a status event; this
    # avoids fabricating assistant content while retaining monotonic replay.
    if (
        event_type == ConversationEventType.ASSISTANT_CHECKPOINT
        and "content" not in data
    ):
        event_type = ConversationEventType.RUN_STATUS
    if (
        event_type == ConversationEventType.MESSAGE_CREATED
        and ("role" not in data or "content" not in data)
    ):
        event_type = ConversationEventType.RUN_STATUS
    if event_type in {
        ConversationEventType.TOOL_STARTED,
        ConversationEventType.TOOL_COMPLETED,
    } and (
        not isinstance(data.get("tool"), str)
        or not isinstance(data.get("summary"), str)
    ):
        # Old or third-party rows that predate the bounded public tool summary
        # contract remain replayable, but cannot masquerade as a valid tool
        # envelope. Preserve their cursor as generic run status instead.
        event_type = ConversationEventType.RUN_STATUS
    occurred_at = event.created_at
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    return build_conversation_event(
        event_id=f"{event.operation_id}:{event.sequence}",
        event_type=event_type,
        conversation_id=conversation_id,
        run_id=event.operation_id,
        message_id=message_id,
        presentation_id=(
            presentation_id
            if event_type == ConversationEventType.PRESENTATION_PUBLISHED
            else None
        ),
        cursor=str(event.cursor),
        checkpoint_cursor=(
            str(event.cursor)
            if event_type == ConversationEventType.ASSISTANT_CHECKPOINT
            else None
        ),
        occurred_at=occurred_at,
        data=data,
    )


async def conversation_events(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    after: str | None = None,
    limit: int = 200,
) -> ConversationEventPage:
    """Replay standard events through the authoritative Workspace cursor API."""

    conversation, presentation = await _conversation_and_presentation(
        session, conversation_id
    )
    # This call is authoritative for cursor syntax, retention-floor rebase,
    # forward-cursor rejection and page high-watermark behavior.
    page = await workspace_events(
        session,
        presentation_id=presentation.id,
        after=after,
        limit=limit,
    )
    cursors = [int(value["cursor"]) for value in page["events"]]
    rows: list[tuple[WorkspaceOperationEvent, WorkspaceOperation]] = []
    if cursors:
        rows = list(
            (
                await session.execute(
                    select(WorkspaceOperationEvent, WorkspaceOperation)
                    .join(
                        WorkspaceOperation,
                        WorkspaceOperation.id == WorkspaceOperationEvent.operation_id,
                    )
                    .where(
                        WorkspaceOperationEvent.cursor.in_(cursors),
                        WorkspaceOperation.conversation_id == conversation.id,
                    )
                    .order_by(WorkspaceOperationEvent.cursor.asc())
                )
            ).all()
        )
    message_ids = {
        message_id
        for event, operation in rows
        if (message_id := _event_message_id(event, operation)) is not None
    }
    messages_by_id: dict[uuid.UUID, WorkspaceMessage] = {}
    if message_ids:
        messages_by_id = {
            message.id: message
            for message in (
                await session.execute(
                    select(WorkspaceMessage).where(
                        WorkspaceMessage.id.in_(message_ids),
                        WorkspaceMessage.conversation_id == conversation.id,
                    )
                )
            ).scalars()
        }
    return ConversationEventPage(
        conversation_id=conversation.id,
        events=[
            _project_event(
                conversation_id=conversation.id,
                presentation_id=presentation.id,
                event=event,
                operation=operation,
                message=messages_by_id.get(_event_message_id(event, operation)),
            )
            for event, operation in rows
        ],
        event_high_watermark=page["event_high_watermark"],
    )
