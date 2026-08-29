"""Transactional command and timeline service for the chat-first workspace."""

from __future__ import annotations

from copy import deepcopy
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import PurePosixPath
from typing import Any, Optional
import uuid

from sqlalchemy import func, literal, or_, union_all, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.workspace import (
    WorkspaceAttachmentInput,
    WorkspaceBootstrapRequest,
    WorkspaceCommandRequest,
    WorkspaceOperationActionRequest,
    canonical_workspace_key,
    is_sensitive_workspace_key,
)
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_DELETED,
    PresentationModel,
)
from models.workspace_operation_kinds import (
    WORKSPACE_DESTRUCTIVE_OPERATION_KINDS,
    get_workspace_operation_kind,
    is_workspace_bootstrap_kind,
    is_workspace_command_kind,
)
from models.sql.workspace import (
    WorkspaceActionReceipt,
    WorkspaceAsset,
    WorkspaceConversation,
    WorkspaceMessage,
    WorkspaceMessageAttachment,
    WorkspaceOperation,
    WorkspaceOperationEvent,
    WorkspaceOutbox,
    get_workspace_owner_scope,
)
from services.workspace_db_adapter import (
    bootstrap_workspace_turn,
    reserve_conversation_positions,
    reserve_operation_event_sequence,
)
from services.account_deletion_service import (
    AccountDeletionFenceConflict,
    require_active_account_generation,
)
from services.authored_composition_policy import (
    AUTHORED_CAPACITY_BOUNDED_REPAIR_REVISION,
)
from services.workspace_error_registry import resolve_workspace_error
from services.workspace_operation_titles import workspace_operation_title
from utils.datetime_utils import get_current_utc_datetime
from utils.owner_context import get_current_owner_id


_ASSET_WAIT_STATES = {"uploaded", "scanning", "processing"}
_ASSET_BLOCK_STATES = {"failed", "quarantined", "expired", "deleted"}
_TERMINAL_OPERATION_STATES = {"succeeded", "failed", "cancelled", "dead_letter"}
_DESTRUCTIVE_OPERATION_KINDS = WORKSPACE_DESTRUCTIVE_OPERATION_KINDS
_DEFAULT_DESTRUCTIVE_APPROVAL_TTL_SECONDS = 24 * 60 * 60
_SAFE_ERROR_MESSAGES = {
    "asset_processing_timeout": "An attachment did not finish processing in time.",
    "workspace_asset_failed": "An attachment failed processing and must be reprocessed.",
    "workspace_asset_quarantined": "An attachment was quarantined and must be replaced.",
    "workspace_asset_expired": "An attachment expired and must be uploaded again.",
    "workspace_asset_deleted": "An attachment was deleted and must be uploaded again.",
    "workspace_asset_not_ready": "An attachment is still being processed.",
    "workspace_asset_reprocess_required": "Reprocess the failed attachment before retrying.",
    "workspace_asset_reattach_required": "Replace the unavailable attachment before retrying.",
    "operation_state_conflict": "This action is no longer available.",
    "operation_not_retryable": "This operation cannot be retried. Submit a new command instead.",
    "revision_conflict": "The presentation changed in another session. Reload and try again.",
    "operation_recovery_required": "The operation is being recovered after an interruption.",
    "operation_dead_lettered": "The operation could not complete. Retry it from this conversation.",
    "event_cursor_expired": "Updates expired; reload the workspace timeline.",
    "approval_expired": "This approval request expired. Submit the command again to continue.",
    "unsupported_operation_type": "This workspace operation is not supported.",
    "version_not_found": "The selected presentation version no longer exists.",
    "version_restore_target_invalid": "Choose a valid presentation version to restore.",
    "version_snapshot_invalid": "The selected presentation version cannot be restored.",
    "version_restore_failed": "The presentation version could not be restored. Try again.",
    "stale_presentation_revision": "The presentation changed in another session. Reload and try again.",
}
_PRIVATE_PAYLOAD_KEYS = {
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


def _attachment_unavailable_code(
    asset: WorkspaceAsset | None,
    *,
    now: datetime,
) -> str | None:
    """Return a stable public reason when the immutable upload is not usable."""

    if asset is None:
        return "workspace_asset_deleted"
    if asset.deleted_at is not None or asset.state == "deleted":
        return "workspace_asset_deleted"
    if asset.expires_at is not None and _as_utc(asset.expires_at) <= now:
        return "workspace_asset_expired"
    return {
        "uploaded": "workspace_asset_not_ready",
        "scanning": "workspace_asset_not_ready",
        "processing": "workspace_asset_not_ready",
        "failed": "workspace_asset_failed",
        "quarantined": "workspace_asset_quarantined",
        "expired": "workspace_asset_expired",
    }.get(asset.state)


def _attachment_snapshot_failure_code(
    attachment: WorkspaceMessageAttachment,
) -> str | None:
    """Map at-attach-time failure metadata to the public error registry."""

    if attachment.asset_failure_code:
        return resolve_workspace_error(attachment.asset_failure_code).code
    return {
        "failed": "workspace_asset_failed",
        "quarantined": "workspace_asset_quarantined",
        "expired": "workspace_asset_expired",
        "deleted": "workspace_asset_deleted",
    }.get(attachment.asset_state)


def _attachment_safe_failure(
    attachment: WorkspaceMessageAttachment,
    *,
    unavailable_code: str | None,
) -> dict[str, Any] | None:
    """Project only allow-listed failure fields; raw scanner detail stays private."""

    code = _attachment_snapshot_failure_code(attachment)
    if code is None and unavailable_code in {
        "workspace_asset_failed",
        "workspace_asset_quarantined",
        "workspace_asset_expired",
        "workspace_asset_deleted",
    }:
        code = unavailable_code
    if code is None:
        return None
    definition = resolve_workspace_error(code)
    return {
        "failure_code": definition.code,
        "safe_message": definition.safe_message,
        "retryable": definition.retryable,
    }


class WorkspaceServiceError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        status_code: int = 400,
        retryable: bool = False,
        message: Optional[str] = None,
    ) -> None:
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        self.safe_message = message or _SAFE_ERROR_MESSAGES.get(code, code)
        super().__init__(code)


def canonical_payload_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def workspace_idempotency_hash(key: str) -> str:
    normalized = key.strip()
    if not normalized or len(normalized) > 255:
        raise WorkspaceServiceError("invalid_idempotency_key", status_code=400)
    return sha256(
        f"presenton.workspace/v1:{get_workspace_owner_scope()}:{normalized}".encode(
            "utf-8"
        )
    ).hexdigest()


def _safe_basename(value: str) -> str:
    return PurePosixPath(value.replace("\\", "/")).name[:512] or "attachment"


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


def _destructive_approval_ttl_seconds() -> int:
    raw = os.getenv(
        "WORKSPACE_DESTRUCTIVE_APPROVAL_TTL_SECONDS",
        str(_DEFAULT_DESTRUCTIVE_APPROVAL_TTL_SECONDS),
    )
    try:
        seconds = int(raw)
    except (TypeError, ValueError):
        seconds = _DEFAULT_DESTRUCTIVE_APPROVAL_TTL_SECONDS
    return max(60, min(seconds, 7 * 24 * 60 * 60))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _asset_operation_state(states: list[str]) -> str:
    if any(state in _ASSET_BLOCK_STATES for state in states):
        return "blocked"
    if any(state in _ASSET_WAIT_STATES for state in states):
        return "waiting_for_assets"
    return "queued"


def _owner_id_predicate(column):
    owner_id = get_current_owner_id()
    return column.is_(None) if owner_id is None else column == owner_id


def _asset_block_error(states: list[str]) -> Optional[str]:
    for state in ("quarantined", "expired", "deleted", "failed"):
        if state in states:
            return f"workspace_asset_{state}"
    return None


async def _presentation_or_404(
    session: AsyncSession, presentation_id: uuid.UUID
) -> PresentationModel:
    presentation = (
        await session.execute(
            select(PresentationModel).where(
                PresentationModel.id == presentation_id,
                _owner_id_predicate(PresentationModel.owner_id),
            )
        )
    ).scalar_one_or_none()
    if presentation is None:
        raise WorkspaceServiceError("presentation_not_found", status_code=404)
    return presentation


async def _lock_workspace_mutation_admission(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
) -> tuple[PresentationModel, int | None]:
    """Serialize a new mutation against account and presentation deletion.

    The account row is always fenced first, matching the account-deletion
    finalizer's lock order.  The presentation no-op UPDATE is the tombstone
    serialization point on both PostgreSQL and SQLite; the lock remains held
    until the caller commits or rolls back its command/action transaction.
    """

    owner_id = get_current_owner_id()
    account_generation: int | None = None
    if owner_id is not None:
        try:
            account_generation = await require_active_account_generation(
                session,
                user_id=owner_id,
            )
        except AccountDeletionFenceConflict as error:
            raise WorkspaceServiceError(
                "account_deletion_in_progress",
                status_code=409,
                retryable=False,
            ) from error

    admitted = (
        await session.execute(
            update(PresentationModel)
            .where(
                PresentationModel.id == presentation_id,
                _owner_id_predicate(PresentationModel.owner_id),
                PresentationModel.lifecycle_status
                != PRESENTATION_LIFECYCLE_DELETED,
            )
            # Explicitly suppress SQLAlchemy's ``updated_at`` onupdate hook:
            # admission takes a writer lock but is not itself a deck change.
            .values(updated_at=PresentationModel.updated_at)
            .returning(PresentationModel.id)
            .execution_options(synchronize_session=False)
        )
    ).scalar_one_or_none()
    if admitted is None:
        presentation = await _presentation_or_404(session, presentation_id)
        if presentation.lifecycle_status == PRESENTATION_LIFECYCLE_DELETED:
            raise WorkspaceServiceError(
                "presentation_deleted",
                status_code=409,
                retryable=False,
            )
        raise WorkspaceServiceError(
            "workspace_mutation_admission_conflict",
            status_code=409,
            retryable=True,
        )

    presentation = (
        await session.execute(
            select(PresentationModel)
            .where(
                PresentationModel.id == presentation_id,
                _owner_id_predicate(PresentationModel.owner_id),
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    return presentation, account_generation


async def _conversation_for_presentation(
    session: AsyncSession,
    presentation_id: uuid.UUID,
) -> Optional[WorkspaceConversation]:
    return (
        await session.execute(
            select(WorkspaceConversation).where(
                WorkspaceConversation.presentation_id == presentation_id,
                WorkspaceConversation.owner_scope == get_workspace_owner_scope(),
            )
        )
    ).scalar_one_or_none()


async def _get_or_create_conversation(
    session: AsyncSession,
    presentation_id: uuid.UUID,
) -> WorkspaceConversation:
    existing = await _conversation_for_presentation(session, presentation_id)
    if existing is not None:
        return existing
    conversation = WorkspaceConversation(
        presentation_id=presentation_id,
        owner_scope=get_workspace_owner_scope(),
    )
    session.add(conversation)
    await session.flush()
    return conversation


async def append_workspace_operation_event(
    session: AsyncSession,
    *,
    operation: WorkspaceOperation,
    event_type: str,
    payload: Optional[Mapping[str, Any]] = None,
) -> WorkspaceOperationEvent:
    """Append an event with an atomic per-operation sequence reservation."""

    sequence = await reserve_operation_event_sequence(session, operation.id)
    event = WorkspaceOperationEvent(
        owner_id=operation.owner_id,
        operation_id=operation.id,
        sequence=sequence,
        operation_version=operation.version,
        event_type=event_type,
        payload=dict(payload or {}),
    )
    session.add(event)
    await session.flush()
    return event


async def _enqueue_event_notification(
    session: AsyncSession,
    *,
    operation: WorkspaceOperation,
    event: WorkspaceOperationEvent,
    topic: str = "workspace.operation.event",
) -> WorkspaceOutbox:
    payload = {
        "operation_id": str(operation.id),
        "presentation_id": str(operation.presentation_id),
        "conversation_id": str(operation.conversation_id),
        "event_cursor": event.cursor,
        "event_type": event.event_type,
        "operation_version": event.operation_version,
        "status": operation.state,
    }
    dedupe = f"workspace-event:{operation.id}:{event.sequence}"
    row = WorkspaceOutbox(
        owner_id=operation.owner_id,
        owner_scope=operation.owner_scope,
        operation_id=operation.id,
        event_cursor=event.cursor,
        topic=topic,
        idempotency_key_hash=canonical_payload_hash({"key": dedupe}),
        payload_hash=canonical_payload_hash(payload),
        payload=payload,
    )
    session.add(row)
    await session.flush()
    return row


async def _append_message_created_event(
    session: AsyncSession,
    *,
    operation: WorkspaceOperation,
    message: WorkspaceMessage,
) -> WorkspaceOperationEvent:
    """Anchor a canonical message event to the run admitted with that message.

    Message content remains in ``workspace_messages`` and is recovered through
    the timeline.  The event contains only the stable identifier/role so SSE
    replay never duplicates user text or exposes request metadata.
    """

    event = await append_workspace_operation_event(
        session,
        operation=operation,
        event_type="message.created",
        payload={
            "status": operation.state,
            "message_id": str(message.id),
            "role": message.role,
        },
    )
    await _enqueue_event_notification(
        session,
        operation=operation,
        event=event,
    )
    return event


async def _existing_operation_for_key(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    kind: str,
    idempotency_hash: str,
) -> Optional[WorkspaceOperation]:
    return (
        await session.execute(
            select(WorkspaceOperation).where(
                WorkspaceOperation.owner_scope == get_workspace_owner_scope(),
                WorkspaceOperation.presentation_id == presentation_id,
                WorkspaceOperation.kind == kind,
                WorkspaceOperation.idempotency_key_hash == idempotency_hash,
            )
        )
    ).scalar_one_or_none()


async def _existing_action_receipt(
    session: AsyncSession,
    *,
    operation_id: uuid.UUID,
    action_type: str,
    idempotency_hash: str,
) -> Optional[WorkspaceActionReceipt]:
    return (
        await session.execute(
            select(WorkspaceActionReceipt).where(
                WorkspaceActionReceipt.owner_scope == get_workspace_owner_scope(),
                WorkspaceActionReceipt.operation_id == operation_id,
                WorkspaceActionReceipt.action_type == action_type,
                WorkspaceActionReceipt.idempotency_key_hash == idempotency_hash,
            )
        )
    ).scalar_one_or_none()


async def _receipt_result_operation(
    session: AsyncSession,
    receipt: WorkspaceActionReceipt,
) -> Optional[WorkspaceOperation]:
    result_operation_id = receipt.result_operation_id or receipt.operation_id
    return (
        await session.execute(
            select(WorkspaceOperation).where(
                WorkspaceOperation.id == result_operation_id,
                WorkspaceOperation.owner_scope == get_workspace_owner_scope(),
            )
        )
    ).scalar_one_or_none()


async def _replay_action_receipt_after_conflict(
    session: AsyncSession,
    *,
    operation_id: uuid.UUID,
    action_type: str,
    idempotency_hash: str,
    request_hash: str,
    missing_code: str = "operation_state_conflict",
) -> tuple[WorkspaceActionReceipt, WorkspaceOperation, bool]:
    """Replay the transaction that won a concurrent action CAS/insert race."""

    receipt = await _existing_action_receipt(
        session,
        operation_id=operation_id,
        action_type=action_type,
        idempotency_hash=idempotency_hash,
    )
    if receipt is None:
        raise WorkspaceServiceError(missing_code, status_code=409)
    if receipt.request_hash != request_hash:
        raise WorkspaceServiceError("idempotency_key_payload_conflict", status_code=409)
    result_operation = await _receipt_result_operation(session, receipt)
    if result_operation is None:
        raise WorkspaceServiceError("idempotency_record_incomplete", status_code=409)
    return receipt, result_operation, True


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


async def _first_message_for_operation(
    session: AsyncSession,
    operation: WorkspaceOperation,
) -> WorkspaceMessage:
    raw_id = operation.request_payload.get("user_message_id")
    message: Optional[WorkspaceMessage] = None
    if raw_id:
        try:
            message = await session.get(WorkspaceMessage, uuid.UUID(str(raw_id)))
        except (ValueError, TypeError):
            message = None
    if message is None:
        message = (
            await session.execute(
                select(WorkspaceMessage).where(
                    WorkspaceMessage.conversation_id == operation.conversation_id,
                    WorkspaceMessage.position == operation.anchor_position - 1,
                )
            )
        ).scalar_one_or_none()
    if message is None:
        raise WorkspaceServiceError("idempotency_record_incomplete", status_code=409)
    return message


async def _materialize_attachments(
    session: AsyncSession,
    *,
    conversation: WorkspaceConversation,
    message: WorkspaceMessage,
    asset_ids: list[uuid.UUID],
    attachments: list[WorkspaceAttachmentInput],
) -> list[WorkspaceAsset]:
    async def claim_existing(asset_id: uuid.UUID) -> WorkspaceAsset:
        asset = (
            await session.execute(
                select(WorkspaceAsset).where(
                    WorkspaceAsset.id == asset_id,
                    _owner_id_predicate(WorkspaceAsset.owner_id),
                )
            )
        ).scalar_one_or_none()
        if asset is None or asset.conversation_id not in (None, conversation.id):
            # Ownership and conversation mismatches are deliberately indistinguishable.
            raise WorkspaceServiceError("workspace_asset_not_found", status_code=404)
        if asset.conversation_id is None:
            values: dict[str, Any] = {
                "conversation_id": conversation.id,
                "version": WorkspaceAsset.version + 1,
                "updated_at": get_current_utc_datetime(),
            }
            if asset.state not in {"expired", "deleted"}:
                values["expires_at"] = None
            changed = (
                await session.execute(
                    update(WorkspaceAsset)
                    .where(
                        WorkspaceAsset.id == asset.id,
                        _owner_id_predicate(WorkspaceAsset.owner_id),
                        WorkspaceAsset.conversation_id.is_(None),
                        WorkspaceAsset.version == asset.version,
                    )
                    .values(**values)
                    .returning(WorkspaceAsset.id)
                )
            ).scalar_one_or_none()
            if changed is None:
                fresh = (
                    await session.execute(
                        select(WorkspaceAsset)
                        .where(
                            WorkspaceAsset.id == asset.id,
                            _owner_id_predicate(WorkspaceAsset.owner_id),
                        )
                        .execution_options(populate_existing=True)
                    )
                ).scalar_one_or_none()
                if fresh is None or fresh.conversation_id != conversation.id:
                    raise WorkspaceServiceError(
                        "workspace_asset_not_found", status_code=404
                    )
                return fresh
            asset = (
                await session.execute(
                    select(WorkspaceAsset)
                    .where(
                        WorkspaceAsset.id == asset.id,
                        _owner_id_predicate(WorkspaceAsset.owner_id),
                    )
                    .execution_options(populate_existing=True)
                )
            ).scalar_one()
        return asset

    assets: list[WorkspaceAsset] = []
    references: list[tuple[uuid.UUID | None, WorkspaceAttachmentInput | None]] = [
        (asset_id, None) for asset_id in asset_ids
    ] + [(item.asset_id, item) for item in attachments]
    for ordinal, (asset_id, item) in enumerate(references):
        if asset_id is not None:
            asset = await claim_existing(asset_id)
            if asset.sha256 is None or len(asset.sha256) != 64:
                raise WorkspaceServiceError(
                    "workspace_asset_hash_invalid", status_code=409
                )
            if item is not None and (
                item.sha256 is None or item.sha256 != asset.sha256
            ):
                raise WorkspaceServiceError(
                    "workspace_asset_hash_mismatch", status_code=409
                )
            # Existing asset rows are authoritative. Client metadata cannot rename,
            # resize, re-type, or forge the state of a previously uploaded asset.
            display_name = _safe_basename(asset.original_name)
            media_type = asset.media_type
            size_bytes = asset.size_bytes
        else:
            if item is None:  # Defensive: every metadata-only reference has an item.
                raise WorkspaceServiceError("workspace_asset_not_found", status_code=404)
            display_name = _safe_basename(item.file_name)
            asset_id = uuid.uuid4()
            asset = WorkspaceAsset(
                id=asset_id,
                conversation_id=conversation.id,
                state=item.status,
                original_name=display_name,
                media_type=item.media_type,
                size_bytes=item.size_bytes,
                storage_key=f"workspace-metadata/{conversation.id}/{asset_id}",
                sha256=item.sha256,
                failure_detail={"source": "legacy_metadata_compat"},
            )
            session.add(asset)
            await session.flush()
            media_type = item.media_type
            size_bytes = item.size_bytes
        session.add(
            WorkspaceMessageAttachment(
                message_id=message.id,
                asset_id=asset.id,
                asset_id_snapshot=asset.id,
                ordinal=ordinal,
                display_name=display_name,
                media_type=media_type,
                size_bytes=size_bytes,
                asset_state=asset.state,
                asset_sha256=asset.sha256,
                asset_failure_code=asset.failure_code,
                asset_failure_detail=deepcopy(asset.failure_detail),
            )
        )
        assets.append(asset)
    await session.flush()
    return assets


async def _bootstrap_workspace_once(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    request: WorkspaceBootstrapRequest,
    idempotency_hash: str,
    admitted_payload: dict[str, Any],
    request_hash: str,
    initial_operation_state: Optional[str],
) -> tuple[WorkspaceConversation, WorkspaceMessage, WorkspaceOperation, bool]:
    existing = await _existing_operation_for_key(
        session,
        presentation_id=presentation_id,
        kind=request.operation_type,
        idempotency_hash=idempotency_hash,
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise WorkspaceServiceError("idempotency_key_payload_conflict", status_code=409)
        message = await _first_message_for_operation(session, existing)
        conversation = await session.get(WorkspaceConversation, existing.conversation_id)
        if conversation is None:
            raise WorkspaceServiceError("idempotency_record_incomplete", status_code=409)
        return conversation, message, existing, True

    presentation, account_generation = await _lock_workspace_mutation_admission(
        session,
        presentation_id=presentation_id,
    )
    expected_revision = (
        presentation.revision
        if request.expected_revision is None
        else request.expected_revision
    )
    if presentation.revision != expected_revision:
        raise WorkspaceServiceError("revision_conflict", status_code=409, retryable=True)

    conversation = await _get_or_create_conversation(session, presentation_id)
    message_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    operation_payload = {
        **admitted_payload,
        "user_message_id": str(message_id),
        "account_deletion_generation": account_generation,
        # Filled from authoritative asset rows after the attachment snapshots exist.
        "asset_ids": [],
    }
    message, operation = await bootstrap_workspace_turn(
        session,
        conversation_id=conversation.id,
        message={
            "id": message_id,
            "content": request.prompt,
            "message_metadata": {
                "generation_settings": request.generation_settings,
                "bootstrap": True,
            },
        },
        operation={
            "id": operation_id,
            "owner_scope": get_workspace_owner_scope(),
            "presentation_id": presentation_id,
            "kind": request.operation_type,
            # Asset-derived state is assigned before the initial event is emitted.
            "state": "queued",
            "idempotency_key_hash": idempotency_hash,
            "request_hash": request_hash,
            "request_payload": operation_payload,
            "expected_revision": expected_revision,
        },
    )
    assets = await _materialize_attachments(
        session,
        conversation=conversation,
        message=message,
        asset_ids=request.asset_ids,
        attachments=request.attachments,
    )
    states = [asset.state for asset in assets]
    asset_state = _asset_operation_state(states)
    operation_state = (
        initial_operation_state
        if asset_state == "queued" and initial_operation_state
        else asset_state
    )
    operation.state = operation_state
    operation.error_code = _asset_block_error(states)
    operation.error_detail = (
        {"retryable": operation.error_code == "workspace_asset_failed"}
        if operation.error_code
        else None
    )
    operation.request_payload = {
        **operation_payload,
        "asset_ids": [str(asset.id) for asset in assets],
    }
    await _append_message_created_event(
        session,
        operation=operation,
        message=message,
    )
    initial_event = await append_workspace_operation_event(
        session,
        operation=operation,
        event_type=operation_state,
        payload={
            "status": operation_state,
            "message": (
                "Waiting for attachments."
                if operation_state == "waiting_for_assets"
                else "An attachment cannot be used."
                if operation_state == "blocked"
                else "Awaiting approval."
                if operation_state == "awaiting_approval"
                else "Queued."
            ),
            "error": (
                {"code": operation.error_code, "retryable": operation.error_code == "workspace_asset_failed"}
                if operation.error_code
                else None
            ),
        },
    )
    await _enqueue_event_notification(
        session,
        operation=operation,
        event=initial_event,
    )
    await session.flush()
    return conversation, message, operation, False


async def bootstrap_workspace(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    request: WorkspaceBootstrapRequest,
    commit: bool = True,
    initial_operation_state: Optional[str] = None,
    initial_state: Optional[str] = None,
) -> tuple[WorkspaceConversation, WorkspaceMessage, WorkspaceOperation, bool]:
    """Create the durable first user message and operation anchor exactly once.

    ``commit=False`` is a strict transaction-composition contract: this function
    only flushes and never commits, rolls back, or retries. The caller owns the
    surrounding presentation/job transaction.
    """

    if not is_workspace_bootstrap_kind(request.operation_type):
        raise WorkspaceServiceError(
            "unsupported_operation_type", status_code=422, retryable=False
        )
    if (
        initial_state is not None
        and initial_operation_state is not None
        and initial_state != initial_operation_state
    ):
        raise WorkspaceServiceError("initial_operation_state_conflict", status_code=400)
    # `initial_state` is retained temporarily for the generation endpoint that
    # shipped with the workspace bootstrap integration.  New callers use the
    # explicit name so this cannot be mistaken for an asset's initial state.
    effective_initial_state = initial_operation_state or initial_state
    if effective_initial_state not in {None, "queued", "awaiting_approval"}:
        raise WorkspaceServiceError("invalid_initial_operation_state", status_code=400)
    if (
        any(item.asset_id is None for item in request.attachments)
        and not request._allow_internal_legacy_metadata
    ):
        raise WorkspaceServiceError(
            "workspace_asset_id_required", status_code=400
        )
    idempotency_hash = workspace_idempotency_hash(request.idempotency_key)
    admitted_payload = {
        "presentation_id": str(presentation_id),
        "prompt": request.prompt,
        "generation_settings": request.generation_settings,
        "asset_ids": [str(asset_id) for asset_id in request.asset_ids],
        "attachments": [item.model_dump(mode="json") for item in request.attachments],
        "operation_type": request.operation_type,
        "expected_revision": request.expected_revision,
        "initial_operation_state": effective_initial_state,
    }
    request_hash = canonical_payload_hash(admitted_payload)

    attempts = 1 if not commit else 2
    for attempt in range(attempts):
        try:
            result = await _bootstrap_workspace_once(
                session,
                presentation_id=presentation_id,
                request=request,
                idempotency_hash=idempotency_hash,
                admitted_payload=admitted_payload,
                request_hash=request_hash,
                initial_operation_state=effective_initial_state,
            )
            if commit:
                await session.commit()
            return result
        except IntegrityError:
            if not commit:
                raise
            await session.rollback()
            existing = await _existing_operation_for_key(
                session,
                presentation_id=presentation_id,
                kind=request.operation_type,
                idempotency_hash=idempotency_hash,
            )
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise WorkspaceServiceError(
                        "idempotency_key_payload_conflict", status_code=409
                    )
                message = await _first_message_for_operation(session, existing)
                conversation = await _conversation_for_presentation(
                    session, existing.presentation_id
                )
                if conversation is None:
                    raise WorkspaceServiceError(
                        "idempotency_record_incomplete", status_code=409
                    )
                return conversation, message, existing, True
            if attempt + 1 >= attempts:
                raise WorkspaceServiceError(
                    "bootstrap_concurrency_conflict", status_code=409, retryable=True
                )
        except Exception:
            if commit:
                await session.rollback()
            raise
    raise WorkspaceServiceError(
        "bootstrap_concurrency_conflict", status_code=409, retryable=True
    )


async def activate_workspace_operation(
    session: AsyncSession,
    *,
    operation_id: uuid.UUID,
    request_hash: str,
    request_payload: Mapping[str, Any],
    expected_revision: int,
    expected_operation_version: Optional[int] = None,
    commit: bool = True,
) -> tuple[WorkspaceOperation, bool]:
    """CAS an approval-gated operation to queued and wake the durable runner."""

    operation = (
        await session.execute(
            select(WorkspaceOperation).where(
                WorkspaceOperation.id == operation_id,
                WorkspaceOperation.owner_scope == get_workspace_owner_scope(),
            )
        )
    ).scalar_one_or_none()
    if operation is None:
        raise WorkspaceServiceError("workspace_operation_not_found", status_code=404)
    if operation.state == "queued" and operation.request_hash == request_hash:
        return operation, True
    presentation, account_generation = await _lock_workspace_mutation_admission(
        session,
        presentation_id=operation.presentation_id,
    )
    if presentation.revision != expected_revision:
        raise WorkspaceServiceError("revision_conflict", status_code=409, retryable=True)
    if operation.state != "awaiting_approval":
        raise WorkspaceServiceError("operation_state_conflict", status_code=409)
    expected_version = (
        operation.version
        if expected_operation_version is None
        else expected_operation_version
    )
    merged_payload = {
        **operation.request_payload,
        **dict(request_payload),
        "account_deletion_generation": account_generation,
    }
    now = get_current_utc_datetime()
    changed = (
        await session.execute(
            update(WorkspaceOperation)
            .where(
                WorkspaceOperation.id == operation.id,
                WorkspaceOperation.owner_scope == get_workspace_owner_scope(),
                WorkspaceOperation.state == "awaiting_approval",
                WorkspaceOperation.version == expected_version,
            )
            .values(
                state="queued",
                request_hash=request_hash,
                request_payload=merged_payload,
                expected_revision=expected_revision,
                next_attempt_at=now,
                error_code=None,
                error_detail=None,
                version=WorkspaceOperation.version + 1,
                updated_at=now,
            )
            .returning(WorkspaceOperation.id)
        )
    ).scalar_one_or_none()
    if changed is None:
        raise WorkspaceServiceError("operation_state_conflict", status_code=409)
    operation = (
        await session.execute(
            select(WorkspaceOperation)
            .where(
                WorkspaceOperation.id == operation_id,
                WorkspaceOperation.owner_scope == get_workspace_owner_scope(),
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    event = await append_workspace_operation_event(
        session,
        operation=operation,
        event_type="queued",
        payload={"status": "queued", "message": "Queued."},
    )
    await _enqueue_event_notification(
        session,
        operation=operation,
        event=event,
        topic="workspace.operation.ready",
    )
    await session.flush()
    if commit:
        await session.commit()
    return operation, False


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
        "status": payload.get("status") if isinstance(payload, dict) else None,
        "message": payload.get("message") if isinstance(payload, dict) else None,
        "progress": (
            payload.get("progress", payload.get("percent"))
            if isinstance(payload, dict)
            else None
        ),
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


def _is_retryable_template_v2_outline_admission(operation: WorkspaceOperation) -> bool:
    """Recognize grounded Template V2 rows stranded by repaired admission gates.

    Older chat-first admissions copied the deck's immutable attachment snapshot
    IDs into a request that already contained grounded ``slides_markdown``.  The
    old validator rejected that harmless combination as a source-mode conflict
    and the generation adapter also misclassified valid reconstructed publication
    metadata as a missing native-clone source.  Both persisted
    ``retryable=false``.  Keep the exception narrow so genuinely unsupported
    raw-reference requests and unrelated native-source failures remain terminal.
    """

    if operation.error_code not in {
        "template_v2_source_mode_not_supported",
        "template_v2_native_source_invalid",
    }:
        return False
    payload = operation.request_payload
    generation_request = (
        payload.get("generation_request") if isinstance(payload, dict) else None
    )
    return bool(
        isinstance(generation_request, dict)
        and generation_request.get("strategy") == "template_v2"
        and generation_request.get("slides_markdown")
    )


def _operation_retryable(operation: WorkspaceOperation) -> bool | None:
    """Project only the safe retry decision, never raw error details."""

    if operation.state in {"failed", "dead_letter"}:
        if _is_retryable_template_v2_outline_admission(operation):
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


def _capacity_wait_projection(operation: WorkspaceOperation) -> tuple[bool, int | None]:
    """Return only durable, non-error capacity-wait metadata for clients."""

    progress = operation.progress if isinstance(operation.progress, Mapping) else {}
    capacity_waiting = bool(
        operation.state == "queued"
        and progress.get("stage") == "waiting_for_capacity"
        and progress.get("capacity_waiting") is True
    )
    queue_position = progress.get("queue_position")
    if (
        not capacity_waiting
        or isinstance(queue_position, bool)
        or not isinstance(queue_position, int)
        or queue_position < 1
    ):
        queue_position = None
    return capacity_waiting, queue_position


async def workspace_timeline(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    before_position: Optional[int] = None,
    limit: int = 50,
) -> dict[str, Any]:
    presentation = await _presentation_or_404(session, presentation_id)
    conversation = await _conversation_for_presentation(session, presentation_id)
    if conversation is None:
        return {
            "schema_version": 1,
            "conversation_id": None,
            "items": [],
            "next_before_position": None,
            "event_high_watermark": None,
            "has_more": False,
            "revision": presentation.revision,
        }

    # Capture the replay boundary before reading any top-level anchors. Under
    # PostgreSQL READ COMMITTED, each SELECT gets a fresh statement snapshot. If
    # this MAX were read after the message/operation queries, a concurrently
    # committed operation could be absent from ``items`` while its initial event
    # advanced the returned watermark. The next ``workspace_events(after=...)``
    # call would then skip that operation forever. A boundary captured first is
    # deliberately conservative: later events can be projected in this response,
    # but they remain replayable (and are deduplicated by their durable event key).
    high_watermark = await session.scalar(
        select(func.max(WorkspaceOperationEvent.cursor))
        .select_from(WorkspaceOperationEvent)
        .join(
            WorkspaceOperation,
            WorkspaceOperation.id == WorkspaceOperationEvent.operation_id,
        )
        .where(WorkspaceOperation.conversation_id == conversation.id)
    )
    durable_high_watermark = max(
        high_watermark or 0,
        conversation.event_retention_floor_cursor,
    )

    effective_limit = max(1, min(limit, 200))
    message_anchor_query = select(
        WorkspaceMessage.position.label("position"),
        literal("message").label("anchor_type"),
        WorkspaceMessage.id.label("anchor_id"),
    ).where(WorkspaceMessage.conversation_id == conversation.id)
    operation_anchor_query = select(
        WorkspaceOperation.anchor_position.label("position"),
        literal("operation").label("anchor_type"),
        WorkspaceOperation.id.label("anchor_id"),
    ).where(WorkspaceOperation.conversation_id == conversation.id)
    if before_position is not None:
        message_anchor_query = message_anchor_query.where(
            WorkspaceMessage.position < before_position
        )
        operation_anchor_query = operation_anchor_query.where(
            WorkspaceOperation.anchor_position < before_position
        )
    # Select both anchor types in one SQL statement. In READ COMMITTED, two
    # independent SELECTs could split an atomically committed message/operation
    # pair across statement snapshots. The UNION fixes the ordered page's IDs and
    # positions at one snapshot; subsequent queries only hydrate that fixed set.
    anchor_candidates = union_all(
        message_anchor_query,
        operation_anchor_query,
    ).subquery()
    anchor_rows = list(
        (
            await session.execute(
                select(
                    anchor_candidates.c.position,
                    anchor_candidates.c.anchor_type,
                    anchor_candidates.c.anchor_id,
                )
                .order_by(anchor_candidates.c.position.desc())
                .limit(effective_limit)
            )
        ).all()
    )
    anchor_rows.reverse()
    message_ids = [
        uuid.UUID(str(row.anchor_id))
        for row in anchor_rows
        if row.anchor_type == "message"
    ]
    operation_ids = [
        uuid.UUID(str(row.anchor_id))
        for row in anchor_rows
        if row.anchor_type == "operation"
    ]
    messages_by_id: dict[uuid.UUID, WorkspaceMessage] = {}
    if message_ids:
        messages_by_id = {
            message.id: message
            for message in (
                await session.scalars(
                    select(WorkspaceMessage).where(
                        WorkspaceMessage.id.in_(message_ids)
                    )
                )
            ).all()
        }
    operations_by_id: dict[uuid.UUID, WorkspaceOperation] = {}
    if operation_ids:
        operations_by_id = {
            operation.id: operation
            for operation in (
                await session.scalars(
                    select(WorkspaceOperation).where(
                        WorkspaceOperation.id.in_(operation_ids)
                    )
                )
            ).all()
        }
    selected: list[tuple[int, str, Any]] = []
    for anchor in anchor_rows:
        anchor_id = uuid.UUID(str(anchor.anchor_id))
        if anchor.anchor_type == "message":
            selected.append(
                (int(anchor.position), "message", messages_by_id[anchor_id])
            )
        else:
            selected.append(
                (int(anchor.position), "operation", operations_by_id[anchor_id])
            )

    attachments_by_message: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    if message_ids:
        attachment_rows = list(
            (
                await session.execute(
                    select(WorkspaceMessageAttachment, WorkspaceAsset)
                    .outerjoin(
                        WorkspaceAsset,
                        WorkspaceAsset.id == WorkspaceMessageAttachment.asset_id,
                    )
                    .where(WorkspaceMessageAttachment.message_id.in_(message_ids))
                    .order_by(WorkspaceMessageAttachment.ordinal.asc())
                )
            ).all()
        )
        now = get_current_utc_datetime()
        for attachment, joined_asset in attachment_rows:
            # The live row is capability metadata only.  Treat a mismatched
            # pointer/owner as unavailable rather than projecting a capability
            # for a different immutable upload.
            asset = joined_asset
            if asset is not None and (
                asset.id != attachment.asset_id_snapshot
                or asset.owner_id != attachment.owner_id
            ):
                asset = None
            unavailable_code = _attachment_unavailable_code(asset, now=now)
            safe_failure = _attachment_safe_failure(
                attachment,
                unavailable_code=unavailable_code,
            )
            can_download = asset is not None and unavailable_code is None
            download_url = (
                f"/api/v1/ppt/workspace/assets/{asset.id}/download"
                if can_download
                else None
            )
            attachments_by_message[attachment.message_id].append(
                {
                    "schema_version": 1,
                    "id": str(attachment.asset_id_snapshot),
                    "name": attachment.display_name,
                    "status": attachment.asset_state,
                    "current_state": asset.state if asset is not None else None,
                    "mime_type": attachment.media_type,
                    "size_bytes": attachment.size_bytes,
                    "sha256": attachment.asset_sha256,
                    "error": (
                        safe_failure["failure_code"]
                        if safe_failure is not None
                        else None
                    ),
                    "safe_failure": safe_failure,
                    "download_url": download_url,
                    "download_action": "download" if can_download else None,
                    "preview_url": None,
                    "preview_action": None,
                    "unavailable_reason": unavailable_code,
                }
            )

    events_by_operation: dict[uuid.UUID, list[dict[str, Any]]] = defaultdict(list)
    if operation_ids:
        event_rows = list(
            (
                await session.scalars(
                    select(WorkspaceOperationEvent)
                    .where(WorkspaceOperationEvent.operation_id.in_(operation_ids))
                    .order_by(
                        WorkspaceOperationEvent.operation_id,
                        WorkspaceOperationEvent.sequence,
                    )
                )
            ).all()
        )
        for event in event_rows:
            events_by_operation[event.operation_id].append(
                _event_to_wire(
                    event,
                    operation_anchor_position=operations_by_id[
                        event.operation_id
                    ].anchor_position,
                    operation_retry_count=operations_by_id[
                        event.operation_id
                    ].retry_count,
                    operation_result=(
                        operations_by_id[event.operation_id].result
                        if isinstance(
                            operations_by_id[event.operation_id].result,
                            Mapping,
                        )
                        else None
                    ),
                )
            )

    items: list[dict[str, Any]] = []
    for position, kind, row in selected:
        if kind == "message":
            metadata = row.message_metadata or {}
            items.append(
                {
                    "anchor_type": "message",
                    "id": str(row.id),
                    "position": position,
                    "role": row.role,
                    "content": row.content,
                    "created_at": row.created_at.isoformat()
                    if row.created_at
                    else None,
                    "attachments": attachments_by_message.get(row.id, []),
                    "generation_settings": _safe_payload(
                        metadata.get("generation_settings")
                    ),
                }
            )
        else:
            progress = row.progress or {}
            capacity_waiting, queue_position = _capacity_wait_projection(row)
            items.append(
                {
                    "anchor_type": "operation",
                    "id": str(row.id),
                    "operation_id": str(row.id),
                    "position": position,
                    "operation_type": row.kind,
                    "title": workspace_operation_title(row.kind),
                    "status": row.state,
                    "progress": progress.get("progress", progress.get("percent")),
                    "capacity_waiting": capacity_waiting,
                    "queue_position": queue_position,
                    "error": row.error_code,
                    "error_message": _SAFE_ERROR_MESSAGES.get(row.error_code or ""),
                    "retryable": _operation_retryable(row),
                    "retry_count": row.retry_count,
                    "result": _safe_payload(row.result),
                    "version": row.version,
                    "expected_revision": row.expected_revision,
                    "result_revision": row.result_revision,
                    "approval_expires_at": (
                        row.approval_expires_at.isoformat()
                        if row.approval_expires_at is not None
                        else None
                    ),
                    "retry_of_operation_id": (
                        str(row.retry_of_operation_id)
                        if row.retry_of_operation_id is not None
                        else None
                    ),
                    "created_at": row.created_at.isoformat()
                    if row.created_at
                    else None,
                    "updated_at": row.updated_at.isoformat()
                    if row.updated_at
                    else None,
                    "events": events_by_operation.get(row.id, []),
                }
            )

    min_position = min((position for position, _, _ in selected), default=None)
    has_more = False
    if min_position is not None:
        older_message = await session.scalar(
            select(func.count())
            .select_from(WorkspaceMessage)
            .where(
                WorkspaceMessage.conversation_id == conversation.id,
                WorkspaceMessage.position < min_position,
            )
        )
        older_operation = await session.scalar(
            select(func.count())
            .select_from(WorkspaceOperation)
            .where(
                WorkspaceOperation.conversation_id == conversation.id,
                WorkspaceOperation.anchor_position < min_position,
            )
        )
        has_more = bool((older_message or 0) + (older_operation or 0))

    return {
        "schema_version": 1,
        "conversation_id": conversation.id,
        "items": items,
        "next_before_position": min_position if has_more else None,
        "event_high_watermark": str(durable_high_watermark),
        "has_more": has_more,
        "revision": presentation.revision,
    }


def parse_event_cursor(value: Optional[str]) -> int:
    if value is None or not value.strip():
        return 0
    candidate = value.strip()
    if not candidate.isascii() or not candidate.isdecimal():
        raise WorkspaceServiceError("invalid_event_cursor", status_code=400)
    if len(candidate) > 1 and candidate.startswith("0"):
        raise WorkspaceServiceError("invalid_event_cursor", status_code=400)
    return int(candidate)


async def workspace_events(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    after: Optional[str] = None,
    limit: int = 200,
) -> dict[str, Any]:
    await _presentation_or_404(session, presentation_id)
    cursor = parse_event_cursor(after)
    conversation = await _conversation_for_presentation(session, presentation_id)
    if conversation is None:
        if after is not None and after.strip() and cursor > 0:
            raise WorkspaceServiceError(
                "event_cursor_expired",
                status_code=410,
                retryable=True,
            )
        return {
            "schema_version": 1,
            "events": [],
            "event_high_watermark": str(cursor),
        }
    if (
        after is not None
        and after.strip()
        and cursor < conversation.event_retention_floor_cursor
    ):
        raise WorkspaceServiceError(
            "event_cursor_expired",
            status_code=410,
            retryable=True,
        )
    latest_cursor = await session.scalar(
        select(func.max(WorkspaceOperationEvent.cursor))
        .join(
            WorkspaceOperation,
            WorkspaceOperation.id == WorkspaceOperationEvent.operation_id,
        )
        .where(WorkspaceOperation.conversation_id == conversation.id)
    )
    authoritative_high_watermark = max(
        int(latest_cursor or 0),
        conversation.event_retention_floor_cursor,
    )
    if after is not None and after.strip() and cursor > authoritative_high_watermark:
        raise WorkspaceServiceError(
            "event_cursor_expired",
            status_code=410,
            retryable=True,
        )
    effective_limit = max(1, min(limit, 500))
    rows = list(
        (
            await session.execute(
                select(
                    WorkspaceOperationEvent,
                    WorkspaceOperation.anchor_position,
                    WorkspaceOperation.retry_count,
                    WorkspaceOperation.result,
                )
                .join(
                    WorkspaceOperation,
                    WorkspaceOperation.id == WorkspaceOperationEvent.operation_id,
                )
                .where(
                    WorkspaceOperation.conversation_id == conversation.id,
                    WorkspaceOperationEvent.cursor > cursor,
                )
                .order_by(WorkspaceOperationEvent.cursor.asc())
                .limit(effective_limit)
            )
        ).all()
    )
    high_watermark = max(
        rows[-1][0].cursor if rows else cursor,
        conversation.event_retention_floor_cursor,
    )
    return {
        "schema_version": 1,
        "events": [
            _event_to_wire(
                event,
                operation_anchor_position=anchor_position,
                operation_retry_count=retry_count,
                operation_result=(
                    operation_result
                    if isinstance(operation_result, Mapping)
                    else None
                ),
            )
            for event, anchor_position, retry_count, operation_result in rows
        ],
        "event_high_watermark": str(high_watermark),
    }


async def create_workspace_command(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    request: WorkspaceCommandRequest,
) -> tuple[WorkspaceOperation, bool]:
    if not is_workspace_command_kind(request.command_type):
        raise WorkspaceServiceError(
            "unsupported_operation_type", status_code=422, retryable=False
        )
    idempotency_hash = workspace_idempotency_hash(request.idempotency_key)
    payload = {
        "presentation_id": str(presentation_id),
        "command": request.command,
        "command_type": request.command_type,
        "context": request.context,
        "expected_revision": request.expected_revision,
        "asset_ids": [str(asset_id) for asset_id in request.asset_ids],
    }
    request_hash = canonical_payload_hash(payload)
    existing = await _existing_operation_for_key(
        session,
        presentation_id=presentation_id,
        kind=request.command_type,
        idempotency_hash=idempotency_hash,
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise WorkspaceServiceError("idempotency_key_payload_conflict", status_code=409)
        return existing, True

    presentation, account_generation = await _lock_workspace_mutation_admission(
        session,
        presentation_id=presentation_id,
    )
    expected_revision = (
        presentation.revision
        if request.expected_revision is None
        else request.expected_revision
    )
    if presentation.revision != expected_revision:
        raise WorkspaceServiceError("revision_conflict", status_code=409, retryable=True)
    conversation = await _get_or_create_conversation(session, presentation_id)
    reservation = await reserve_conversation_positions(session, conversation.id, 2)
    message_position, operation_position = reservation.positions
    requires_approval = request.command_type in _DESTRUCTIVE_OPERATION_KINDS
    initial_state = "awaiting_approval" if requires_approval else "queued"
    approval_expires_at = (
        get_current_utc_datetime()
        + timedelta(seconds=_destructive_approval_ttl_seconds())
        if requires_approval
        else None
    )
    message = WorkspaceMessage(
        conversation_id=conversation.id,
        position=message_position,
        role="user",
        content=request.command,
        message_metadata={"command_type": request.command_type, "context": request.context},
    )
    operation = WorkspaceOperation(
        owner_scope=get_workspace_owner_scope(),
        conversation_id=conversation.id,
        presentation_id=presentation_id,
        anchor_position=operation_position,
        kind=request.command_type,
        state=initial_state,
        idempotency_key_hash=idempotency_hash,
        request_hash=request_hash,
        request_payload={
            **payload,
            "user_message_id": str(message.id),
            "account_deletion_generation": account_generation,
            # Asset transitions must restore this state after every referenced
            # asset becomes ready instead of bypassing destructive approval.
            "initial_operation_state": initial_state,
        },
        expected_revision=expected_revision,
        approval_expires_at=approval_expires_at,
    )
    session.add_all((message, operation))
    try:
        await session.flush()
        assets = await _materialize_attachments(
            session,
            conversation=conversation,
            message=message,
            asset_ids=request.asset_ids,
            attachments=[],
        )
        states = [asset.state for asset in assets]
        asset_state = _asset_operation_state(states)
        operation_state = initial_state if asset_state == "queued" else asset_state
        operation.state = operation_state
        operation.error_code = _asset_block_error(states)
        operation.error_detail = (
            {"retryable": operation.error_code == "workspace_asset_failed"}
            if operation.error_code
            else None
        )
        operation.request_payload = {
            **operation.request_payload,
            # Persist only IDs resolved from authoritative, owner-scoped rows.
            "asset_ids": [str(asset.id) for asset in assets],
        }
        await session.flush()
        await _append_message_created_event(
            session,
            operation=operation,
            message=message,
        )
        event = await append_workspace_operation_event(
            session,
            operation=operation,
            event_type=(
                "approval_required"
                if operation_state == "awaiting_approval"
                else operation_state
            ),
            payload={
                "status": operation_state,
                "message": (
                    "Waiting for attachments."
                    if operation_state == "waiting_for_assets"
                    else "An attachment cannot be used."
                    if operation_state == "blocked"
                    else "Awaiting approval."
                    if operation_state == "awaiting_approval"
                    else "Queued."
                ),
                "approval_expires_at": (
                    approval_expires_at.isoformat()
                    if operation_state == "awaiting_approval"
                    and approval_expires_at is not None
                    else None
                ),
                "error": (
                    {
                        "code": operation.error_code,
                        "retryable": operation.error_code
                        == "workspace_asset_failed",
                    }
                    if operation.error_code
                    else None
                ),
            },
        )
        await _enqueue_event_notification(session, operation=operation, event=event)
        await session.commit()
        return operation, False
    except IntegrityError:
        await session.rollback()
        existing = await _existing_operation_for_key(
            session,
            presentation_id=presentation_id,
            kind=request.command_type,
            idempotency_hash=idempotency_hash,
        )
        if existing is None or existing.request_hash != request_hash:
            raise WorkspaceServiceError("idempotency_key_payload_conflict", status_code=409)
        return existing, True
    except Exception:
        # Attachment admission happens after the message/operation pair is
        # staged. Keep asset claims and snapshots atomic with command creation
        # even when callers catch a service error and reuse this session.
        await session.rollback()
        raise


def _operation_asset_ids(operation: WorkspaceOperation) -> list[uuid.UUID]:
    raw_ids = operation.request_payload.get("asset_ids", [])
    if not isinstance(raw_ids, list):
        return []
    parsed: list[uuid.UUID] = []
    for raw_id in raw_ids:
        try:
            parsed.append(uuid.UUID(str(raw_id)))
        except (TypeError, ValueError):
            continue
    return parsed


async def _guard_blocked_operation_retry(
    session: AsyncSession,
    operation: WorkspaceOperation,
) -> None:
    asset_ids = _operation_asset_ids(operation)
    if not asset_ids:
        return
    assets = list(
        (
            await session.scalars(
                select(WorkspaceAsset).where(
                    WorkspaceAsset.id.in_(asset_ids),
                    _owner_id_predicate(WorkspaceAsset.owner_id),
                    WorkspaceAsset.conversation_id == operation.conversation_id,
                )
            )
        ).all()
    )
    if len(assets) != len(set(asset_ids)):
        raise WorkspaceServiceError(
            "workspace_asset_reattach_required", status_code=409
        )
    states = [asset.state for asset in assets]
    if any(state in {"quarantined", "expired", "deleted"} for state in states):
        raise WorkspaceServiceError(
            "workspace_asset_reattach_required", status_code=409
        )
    if "failed" in states:
        raise WorkspaceServiceError(
            "workspace_asset_reprocess_required",
            status_code=409,
            retryable=True,
        )
    if any(state in _ASSET_WAIT_STATES for state in states):
        raise WorkspaceServiceError(
            "workspace_asset_not_ready", status_code=409, retryable=True
        )


async def apply_workspace_action(
    session: AsyncSession,
    *,
    operation_id: uuid.UUID,
    request: WorkspaceOperationActionRequest,
) -> tuple[WorkspaceActionReceipt, WorkspaceOperation, bool]:
    idempotency_hash = workspace_idempotency_hash(request.idempotency_key)
    action_id = request.action_id or idempotency_hash[:32]
    request_payload = {
        "operation_id": str(operation_id),
        "action": request.action,
        "action_id": action_id,
        "expected_operation_version": request.expected_operation_version,
        "expected_revision": request.expected_revision,
    }
    if request.recovery_revision is not None:
        request_payload["recovery_revision"] = request.recovery_revision
    request_hash = canonical_payload_hash(request_payload)
    existing_receipt = await _existing_action_receipt(
        session,
        operation_id=operation_id,
        action_type=request.action,
        idempotency_hash=idempotency_hash,
    )
    if existing_receipt is not None:
        if existing_receipt.request_hash != request_hash:
            raise WorkspaceServiceError("idempotency_key_payload_conflict", status_code=409)
        operation = await _receipt_result_operation(session, existing_receipt)
        if operation is None:
            raise WorkspaceServiceError("idempotency_record_incomplete", status_code=409)
        return existing_receipt, operation, True

    operation = (
        await session.execute(
            select(WorkspaceOperation).where(
                WorkspaceOperation.id == operation_id,
                WorkspaceOperation.owner_scope == get_workspace_owner_scope(),
            )
        )
    ).scalar_one_or_none()
    if operation is None:
        raise WorkspaceServiceError("workspace_operation_not_found", status_code=404)
    if get_workspace_operation_kind(operation.kind) is None:
        raise WorkspaceServiceError(
            "unsupported_operation_type", status_code=422, retryable=False
        )
    presentation, account_generation = await _lock_workspace_mutation_admission(
        session,
        presentation_id=operation.presentation_id,
    )
    if (
        request.expected_revision is not None
        and presentation.revision != request.expected_revision
    ):
        raise WorkspaceServiceError("revision_conflict", status_code=409, retryable=True)

    allowed_states: tuple[str, ...]
    values: dict[str, Any]
    now = get_current_utc_datetime()
    retry_of_operation_id: Optional[uuid.UUID] = None
    terminal_retry = request.action == "retry" and operation.state in {
        "failed",
        "cancelled",
        "dead_letter",
    }
    explicit_capacity_recovery = _is_explicit_authored_capacity_recovery(
        operation,
        request,
    )
    if request.recovery_revision is not None and not explicit_capacity_recovery:
        raise WorkspaceServiceError("operation_not_retryable", status_code=409)
    if terminal_retry:
        if (
            request.expected_operation_version is not None
            and operation.version != request.expected_operation_version
        ):
            raise WorkspaceServiceError("operation_state_conflict", status_code=409)
        if (
            operation.state in {"failed", "dead_letter"}
            and isinstance(operation.error_detail, dict)
            and operation.error_detail.get("retryable") is False
            and not _is_retryable_template_v2_outline_admission(operation)
            and not explicit_capacity_recovery
        ):
            raise WorkspaceServiceError("operation_not_retryable", status_code=409)
        retry_of_operation_id = operation.id
        existing_retry = (
            await session.execute(
                select(WorkspaceOperation).where(
                    WorkspaceOperation.retry_of_operation_id == operation.id,
                    WorkspaceOperation.owner_scope == get_workspace_owner_scope(),
                )
            )
        ).scalar_one_or_none()
        if existing_retry is not None:
            await session.rollback()
            return await _replay_action_receipt_after_conflict(
                session,
                operation_id=operation_id,
                action_type=request.action,
                idempotency_hash=idempotency_hash,
                request_hash=request_hash,
            )
        reservation = await reserve_conversation_positions(
            session, operation.conversation_id, 1
        )
        source_operation = operation
        retry_payload = {
            **source_operation.request_payload,
            "retry_of_operation_id": str(source_operation.id),
            "account_deletion_generation": account_generation,
        }
        if explicit_capacity_recovery:
            retry_payload["recovery_revision"] = request.recovery_revision
        retry_request_hash = canonical_payload_hash(
            {
                "retry_of_operation_id": str(source_operation.id),
                "presentation_revision": presentation.revision,
                "request_payload": retry_payload,
            }
        )
        retry_operation_key_hash = sha256(
            (
                f"presenton.workspace.retry/v1:{idempotency_hash}:"
                f"{source_operation.id}"
            ).encode("utf-8")
        ).hexdigest()
        retry_requires_approval = source_operation.kind in _DESTRUCTIVE_OPERATION_KINDS
        retry_approval_expires_at = (
            now + timedelta(seconds=_destructive_approval_ttl_seconds())
            if retry_requires_approval
            else None
        )
        operation = WorkspaceOperation(
            owner_id=source_operation.owner_id,
            owner_scope=source_operation.owner_scope,
            conversation_id=source_operation.conversation_id,
            presentation_id=source_operation.presentation_id,
            retry_of_operation_id=source_operation.id,
            anchor_position=reservation.start,
            kind=source_operation.kind,
            state="awaiting_approval" if retry_requires_approval else "queued",
            idempotency_key_hash=retry_operation_key_hash,
            request_hash=retry_request_hash,
            request_payload=retry_payload,
            expected_revision=presentation.revision,
            approval_expires_at=retry_approval_expires_at,
            next_attempt_at=None if retry_requires_approval else now,
            max_attempts=source_operation.max_attempts,
        )
        session.add(operation)
        try:
            # Flush the self-referencing operation before its action receipt.
            # SQLAlchemy cannot infer object ordering from UUID values alone,
            # and SQLite enforces the receipt FK immediately.
            await session.flush()
            linked_job_id = _linked_generation_job_id(source_operation)
            if linked_job_id is not None or source_operation.kind in {
                "template.apply",
                "design.apply",
            }:
                from services.presentation_generation_job_service import (
                    rebind_workspace_linked_generation_job_for_retry,
                )

                rebound = await rebind_workspace_linked_generation_job_for_retry(
                    session,
                    source_operation.id,
                    operation.id,
                    expected_job_id=linked_job_id,
                    expected_presentation_id=source_operation.presentation_id,
                )
                if not rebound:
                    await session.rollback()
                    raise WorkspaceServiceError(
                        "operation_state_conflict", status_code=409
                    )
        except IntegrityError:
            await session.rollback()
            return await _replay_action_receipt_after_conflict(
                session,
                operation_id=operation_id,
                action_type=request.action,
                idempotency_hash=idempotency_hash,
                request_hash=request_hash,
            )
    elif request.action == "cancel":
        allowed_states = (
            "queued",
            "waiting_for_assets",
            "blocked",
            "running",
            "approval_required",
            "awaiting_approval",
            "cancelling",
        )
        values = {
            "state": "cancelling" if operation.state in {"running", "cancelling"} else "cancelled",
            "cancel_requested_at": now,
            "cancelled_at": None if operation.state in {"running", "cancelling"} else now,
            "completed_at": None if operation.state in {"running", "cancelling"} else now,
        }
    elif request.action == "approve":
        if (
            operation.expected_revision is not None
            and presentation.revision != operation.expected_revision
        ):
            raise WorkspaceServiceError(
                "revision_conflict", status_code=409, retryable=True
            )
        if (
            operation.approval_expires_at is not None
            and _as_utc(operation.approval_expires_at) <= _as_utc(now)
        ):
            raise WorkspaceServiceError("approval_expired", status_code=409)
        allowed_states = ("approval_required", "awaiting_approval")
        # The expiry bounds the approval action itself.  Clearing it makes the
        # consumed approval durable so a delayed or recovered worker does not
        # reinterpret an already-approved command as expired.
        values = {
            "state": "queued",
            "next_attempt_at": now,
            "approval_expires_at": None,
        }
    else:
        if operation.state == "blocked":
            await _guard_blocked_operation_retry(session, operation)
        allowed_states = ("failed", "cancelled", "dead_letter", "blocked")
        retry_requires_approval = operation.kind in _DESTRUCTIVE_OPERATION_KINDS
        values = {
            "state": "awaiting_approval" if retry_requires_approval else "queued",
            "attempt_number": 0,
            "retry_count": 0,
            "attempt_token": None,
            "lease_expires_at": None,
            "heartbeat_at": None,
            "next_attempt_at": None if retry_requires_approval else now,
            "approval_expires_at": (
                now + timedelta(seconds=_destructive_approval_ttl_seconds())
                if retry_requires_approval
                else None
            ),
            "cancel_requested_at": None,
            "cancelled_at": None,
            "completed_at": None,
            "error_code": None,
            "error_detail": None,
        }
    if not terminal_retry:
        values["version"] = WorkspaceOperation.version + 1
        values["updated_at"] = now
        statement = (
            update(WorkspaceOperation)
            .where(
                WorkspaceOperation.id == operation_id,
                WorkspaceOperation.owner_scope == get_workspace_owner_scope(),
                WorkspaceOperation.state.in_(allowed_states),
            )
            .values(**values)
            .returning(WorkspaceOperation.id)
        )
        if request.expected_operation_version is not None:
            statement = statement.where(
                WorkspaceOperation.version == request.expected_operation_version
            )
        changed = (await session.execute(statement)).scalar_one_or_none()
        if changed is None:
            await session.rollback()
            return await _replay_action_receipt_after_conflict(
                session,
                operation_id=operation_id,
                action_type=request.action,
                idempotency_hash=idempotency_hash,
                request_hash=request_hash,
            )
        await session.refresh(operation)
        linked_job_id = _linked_generation_job_id(operation)
        if (
            request.action == "cancel"
            and operation.state == "cancelled"
            and linked_job_id is not None
        ):
            from services.presentation_generation_job_service import (
                project_workspace_linked_generation_cancelled,
            )

            projected = await project_workspace_linked_generation_cancelled(
                session,
                operation.id,
                expected_job_id=linked_job_id,
            )
            if not projected:
                await session.rollback()
                raise WorkspaceServiceError("operation_state_conflict", status_code=409)

    result_payload = {"state": operation.state}
    if retry_of_operation_id is not None:
        result_payload.update(
            {
                "operation_id": str(operation.id),
                "retry_of_operation_id": str(retry_of_operation_id),
            }
        )

    receipt = WorkspaceActionReceipt(
        owner_scope=get_workspace_owner_scope(),
        operation_id=operation_id,
        result_operation_id=operation.id,
        action_id=action_id,
        action_type=request.action,
        status="applied",
        idempotency_key_hash=idempotency_hash,
        request_hash=request_hash,
        request_payload=request_payload,
        result_payload=result_payload,
        result_hash=canonical_payload_hash(result_payload),
        source_revision=request.expected_revision,
        result_revision=presentation.revision,
    )
    session.add(receipt)
    try:
        await session.flush()
        event = await append_workspace_operation_event(
            session,
            operation=operation,
            event_type=request.action,
            payload={
                "status": operation.state,
                "message": f"Action {request.action} applied.",
                **(
                    {"retry_of_operation_id": str(retry_of_operation_id)}
                    if retry_of_operation_id is not None
                    else {}
                ),
            },
        )
        await _enqueue_event_notification(
            session,
            operation=operation,
            event=event,
            topic=(
                "workspace.operation.ready"
                if request.action in {"retry", "approve"}
                and operation.state == "queued"
                else "workspace.operation.event"
            ),
        )
        await session.commit()
        from services.workspace_operation_runner import (
            notify_workspace_operation_state_change,
        )

        notify_workspace_operation_state_change(
            operation.id,
            cancel_requested=request.action == "cancel",
        )
        return receipt, operation, False
    except IntegrityError:
        await session.rollback()
        return await _replay_action_receipt_after_conflict(
            session,
            operation_id=operation_id,
            action_type=request.action,
            idempotency_hash=idempotency_hash,
            request_hash=request_hash,
            missing_code=(
                "operation_state_conflict"
                if retry_of_operation_id is not None
                else "idempotency_key_payload_conflict"
            ),
        )
