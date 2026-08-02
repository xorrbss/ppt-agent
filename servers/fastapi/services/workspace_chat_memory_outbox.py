"""Post-publication delivery of workspace chat turns to external memory.

The outbox payload is metadata-only.  User and assistant text remain in the
already-published operation row and are loaded only by the delivery handler.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from typing import Any

from sqlalchemy import and_, or_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from models.sql.workspace import (
    WorkspaceActionReceipt,
    WorkspaceOperation,
    WorkspaceOutbox,
)
from services.chat.chat_memory_store import CHAT_MEMORY_STORE, ChatMemoryStore
from services.database import async_session_maker
from utils.datetime_utils import get_current_utc_datetime


WORKSPACE_CHAT_MEMORY_TOPIC = "workspace.chat.memory"
WORKSPACE_CHAT_MEMORY_RECEIPT_LEASE_SECONDS = 60


@dataclass(frozen=True, slots=True)
class _WorkspaceChatMemoryClaim:
    receipt_id: uuid.UUID
    attempt_token: str


def _payload_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _delivery_id(operation_id: uuid.UUID, result_revision: int) -> str:
    return f"workspace-chat:{operation_id}:revision:{result_revision}"


async def _claim_workspace_chat_memory_delivery(
    *,
    operation: WorkspaceOperation,
    presentation_id: uuid.UUID,
    conversation_id: uuid.UUID,
    result_revision: int,
    session_factory: async_sessionmaker[AsyncSession],
) -> _WorkspaceChatMemoryClaim | None:
    """CAS a durable receipt before entering the provider's check/add gap."""

    delivery_id = _delivery_id(operation.id, result_revision)
    request_payload = {
        "operation_id": str(operation.id),
        "presentation_id": str(presentation_id),
        "conversation_id": str(conversation_id),
        "result_revision": result_revision,
    }
    request_hash = _payload_hash(request_payload)
    idempotency_hash = _payload_hash(
        {"owner_scope": operation.owner_scope, "delivery_id": delivery_id}
    )
    now = get_current_utc_datetime()
    stale_before = now - timedelta(
        seconds=WORKSPACE_CHAT_MEMORY_RECEIPT_LEASE_SECONDS
    )
    attempt_token = uuid.uuid4().hex
    receipt = WorkspaceActionReceipt(
        owner_id=operation.owner_id,
        owner_scope=operation.owner_scope,
        operation_id=operation.id,
        result_operation_id=operation.id,
        action_id=delivery_id,
        action_type=WORKSPACE_CHAT_MEMORY_TOPIC,
        status="accepted",
        idempotency_key_hash=idempotency_hash,
        request_hash=request_hash,
        result_hash=attempt_token,
        request_payload=request_payload,
        source_revision=result_revision,
        updated_at=now,
    )

    async with session_factory() as session:
        session.add(receipt)
        try:
            await session.commit()
            return _WorkspaceChatMemoryClaim(receipt.id, attempt_token)
        except IntegrityError:
            await session.rollback()
            existing = (
                await session.execute(
                    select(WorkspaceActionReceipt)
                    .where(
                        WorkspaceActionReceipt.owner_scope
                        == operation.owner_scope,
                        WorkspaceActionReceipt.operation_id == operation.id,
                        WorkspaceActionReceipt.action_type
                        == WORKSPACE_CHAT_MEMORY_TOPIC,
                        WorkspaceActionReceipt.action_id == delivery_id,
                    )
                    .execution_options(skip_owner_scope=True)
                )
            ).scalar_one_or_none()
            if existing is None:
                # The integrity failure was unrelated to the delivery key.
                raise

        if (
            existing.idempotency_key_hash != idempotency_hash
            or existing.request_hash != request_hash
        ):
            raise ValueError("workspace_chat_memory_delivery_conflict")
        if existing.status == "applied":
            return None

        # A crashed/cancelled worker leaves an accepted receipt behind.  Only
        # one successor may reclaim it after the safety window; fresh receipts
        # fence concurrent workers before either reaches get_all()/add().
        reclaim = await session.execute(
            update(WorkspaceActionReceipt)
            .where(
                WorkspaceActionReceipt.id == existing.id,
                WorkspaceActionReceipt.owner_scope == operation.owner_scope,
                WorkspaceActionReceipt.result_hash == existing.result_hash,
                or_(
                    WorkspaceActionReceipt.status == "failed",
                    and_(
                        WorkspaceActionReceipt.status == "accepted",
                        WorkspaceActionReceipt.updated_at <= stale_before,
                    ),
                ),
            )
            .values(
                status="accepted",
                result_hash=attempt_token,
                result_payload=None,
                result_revision=None,
                updated_at=now,
            )
            .execution_options(
                skip_owner_scope=True,
                synchronize_session=False,
            )
        )
        await session.commit()
        if reclaim.rowcount == 1:
            return _WorkspaceChatMemoryClaim(existing.id, attempt_token)

    raise RuntimeError("workspace_chat_memory_delivery_in_progress")


async def _mark_workspace_chat_memory_delivery_applied(
    claim: _WorkspaceChatMemoryClaim,
    *,
    delivery_id: str,
    result_revision: int,
    session_factory: async_sessionmaker[AsyncSession],
) -> bool:
    now = get_current_utc_datetime()
    async with session_factory() as session:
        result = await session.execute(
            update(WorkspaceActionReceipt)
            .where(
                WorkspaceActionReceipt.id == claim.receipt_id,
                WorkspaceActionReceipt.status == "accepted",
                WorkspaceActionReceipt.result_hash == claim.attempt_token,
            )
            .values(
                status="applied",
                result_hash=_payload_hash(
                    {"delivery_id": delivery_id, "status": "applied"}
                ),
                result_revision=result_revision,
                updated_at=now,
            )
            .execution_options(
                skip_owner_scope=True,
                synchronize_session=False,
            )
        )
        await session.commit()
        return result.rowcount == 1


async def enqueue_workspace_chat_memory_delivery(
    session: AsyncSession,
    *,
    operation_id: uuid.UUID,
    owner_id: uuid.UUID | None,
    owner_scope: str,
    presentation_id: uuid.UUID,
    conversation_id: uuid.UUID,
    result_revision: int,
) -> WorkspaceOutbox:
    """Stage a memory delivery in the same transaction as publication."""

    payload = {
        "operation_id": str(operation_id),
        "presentation_id": str(presentation_id),
        "conversation_id": str(conversation_id),
        "result_revision": result_revision,
    }
    delivery_id = _delivery_id(operation_id, result_revision)
    row = WorkspaceOutbox(
        owner_id=owner_id,
        owner_scope=owner_scope,
        operation_id=operation_id,
        topic=WORKSPACE_CHAT_MEMORY_TOPIC,
        idempotency_key_hash=_payload_hash(
            {"owner_scope": owner_scope, "delivery_id": delivery_id}
        ),
        payload_hash=_payload_hash(payload),
        payload=payload,
    )
    session.add(row)
    await session.flush()
    return row


def _uuid_field(payload: Mapping[str, Any], name: str) -> uuid.UUID:
    value = payload.get(name)
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as error:
        raise ValueError("workspace_chat_memory_payload_invalid") from error


async def deliver_workspace_chat_memory(
    payload: Mapping[str, Any],
    *,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    memory_store: ChatMemoryStore = CHAT_MEMORY_STORE,
) -> None:
    """Deliver one committed turn behind a durable unique/CAS receipt."""

    operation_id = _uuid_field(payload, "operation_id")
    presentation_id = _uuid_field(payload, "presentation_id")
    conversation_id = _uuid_field(payload, "conversation_id")
    try:
        result_revision = int(payload["result_revision"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("workspace_chat_memory_payload_invalid") from error

    async with session_factory() as session:
        operation = (
            await session.execute(
                select(WorkspaceOperation)
                .where(
                    WorkspaceOperation.id == operation_id,
                    WorkspaceOperation.presentation_id == presentation_id,
                    WorkspaceOperation.conversation_id == conversation_id,
                    WorkspaceOperation.state == "succeeded",
                    WorkspaceOperation.result_revision == result_revision,
                )
                .execution_options(skip_owner_scope=True)
            )
        ).scalar_one_or_none()

    if operation is None:
        # A memory side effect must never outrun or contradict publication.
        raise ValueError("workspace_chat_memory_publication_missing")

    command = operation.request_payload.get("command")
    result = operation.result if isinstance(operation.result, Mapping) else {}
    summary = result.get("summary")
    if not isinstance(command, str) or not command.strip():
        raise ValueError("workspace_chat_memory_command_missing")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("workspace_chat_memory_summary_missing")

    delivery_id = _delivery_id(operation_id, result_revision)
    claim = await _claim_workspace_chat_memory_delivery(
        operation=operation,
        presentation_id=presentation_id,
        conversation_id=conversation_id,
        result_revision=result_revision,
        session_factory=session_factory,
    )
    if claim is None:
        return

    await memory_store.store_chat_turn(
        presentation_id=presentation_id,
        conversation_id=conversation_id,
        user_message=command.strip(),
        assistant_message=summary.strip(),
        delivery_id=delivery_id,
        strict=True,
    )
    if not await _mark_workspace_chat_memory_delivery_applied(
        claim,
        delivery_id=delivery_id,
        result_revision=result_revision,
        session_factory=session_factory,
    ):
        raise RuntimeError("workspace_chat_memory_delivery_fence_lost")
