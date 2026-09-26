"""Owner-checked generation message identities and immutable attempt links."""

import uuid

from fastapi import HTTPException
from sqlalchemy import String, cast
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.sql.presentation import PRESENTATION_LIFECYCLE_DELETED, PresentationModel
from models.sql.workspace import (
    WorkspaceConversation,
    WorkspaceMessage,
    WorkspaceOperation,
    get_workspace_owner_scope,
)
from services.workspace_service import (
    WorkspaceServiceError,
    _lock_workspace_mutation_admission,
)
from utils.owner_context import get_current_owner_id

_TERMINAL = {"succeeded", "failed", "cancelled", "dead_letter"}


def _owner(column):
    owner_id = get_current_owner_id()
    return column.is_(None) if owner_id is None else column == owner_id


def _uuid(value):
    try:
        return uuid.UUID(str(value)) if value is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


async def _live_owned(session: AsyncSession, presentation_id: uuid.UUID):
    return await session.scalar(
        select(PresentationModel).where(
            PresentationModel.id == presentation_id,
            _owner(PresentationModel.owner_id),
            PresentationModel.lifecycle_status != PRESENTATION_LIFECYCLE_DELETED,
        )
    )


async def can_start_new_attempt(session: AsyncSession, presentation_id: uuid.UUID) -> bool:
    if await _live_owned(session, presentation_id) is None:
        return False
    operations = list(
        (
            await session.scalars(
                select(WorkspaceOperation)
                .where(
                    WorkspaceOperation.presentation_id == presentation_id,
                    WorkspaceOperation.owner_scope == get_workspace_owner_scope(),
                    _owner(WorkspaceOperation.owner_id),
                )
                .order_by(WorkspaceOperation.anchor_position.desc(), WorkspaceOperation.created_at.desc())
            )
        ).all()
    )
    generation = next((operation for operation in operations if operation.kind == "presentation.generate"), None)
    return bool(
        generation and generation.state in _TERMINAL and all(operation.state in _TERMINAL for operation in operations)
    )


async def validate_previous_attempt(
    session: AsyncSession,
    *,
    previous_presentation_id: uuid.UUID | None,
    presentation_id: uuid.UUID,
) -> None:
    if previous_presentation_id is None:
        return
    if previous_presentation_id == presentation_id:
        raise HTTPException(status_code=400, detail="generation_attempt_parent_invalid")
    try:
        # Shares the account/deletion lock order with other workspace writes.
        await _lock_workspace_mutation_admission(session, presentation_id=previous_presentation_id)
    except WorkspaceServiceError as error:
        raise HTTPException(status_code=error.status_code, detail=error.code) from error
    if not await can_start_new_attempt(session, previous_presentation_id):
        raise HTTPException(status_code=409, detail="generation_previous_attempt_not_terminal")


async def generation_context_fields(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    operation: WorkspaceOperation | None,
) -> dict:
    fields = {
        "conversation_id": None,
        "operation_id": None,
        "first_user_message_id": None,
        "previous_presentation_id": None,
        "next_presentation_ids": [],
        "can_start_new_attempt": False,
    }
    if await _live_owned(session, presentation_id) is None:
        return fields
    fields["can_start_new_attempt"] = await can_start_new_attempt(session, presentation_id)
    if (
        operation is not None
        and operation.presentation_id == presentation_id
        and operation.owner_scope == get_workspace_owner_scope()
        and operation.owner_id == get_current_owner_id()
    ):
        conversation = await session.scalar(
            select(WorkspaceConversation).where(
                WorkspaceConversation.id == operation.conversation_id,
                WorkspaceConversation.presentation_id == presentation_id,
                WorkspaceConversation.owner_scope == get_workspace_owner_scope(),
                _owner(WorkspaceConversation.owner_id),
            )
        )
        message_id = _uuid((operation.request_payload or {}).get("user_message_id"))
        if conversation is not None:
            message_query = select(WorkspaceMessage).where(
                WorkspaceMessage.conversation_id == conversation.id,
                WorkspaceMessage.position == operation.anchor_position - 1,
                WorkspaceMessage.role == "user",
                _owner(WorkspaceMessage.owner_id),
            )
            # Existing legacy operations can be resolved by their server-owned
            # anchor position; a supplied invalid ID is never treated as legacy.
            raw_message_id = (operation.request_payload or {}).get("user_message_id")
            if message_id is not None:
                message_query = message_query.where(WorkspaceMessage.id == message_id)
            message = None if raw_message_id is not None and message_id is None else await session.scalar(message_query)
            if message is not None:
                fields.update(
                    conversation_id=conversation.id, operation_id=operation.id, first_user_message_id=message.id
                )
        previous_id = _uuid((operation.request_payload or {}).get("previous_presentation_id"))
        if (
            previous_id is not None
            and previous_id != presentation_id
            and await _live_owned(session, previous_id) is not None
        ):
            fields["previous_presentation_id"] = previous_id
    candidate_children = (
        await session.execute(
            select(PresentationModel.id, WorkspaceOperation.request_payload)
            .join(
                WorkspaceOperation,
                WorkspaceOperation.presentation_id == PresentationModel.id,
            )
            .where(
                _owner(PresentationModel.owner_id),
                PresentationModel.lifecycle_status != PRESENTATION_LIFECYCLE_DELETED,
                WorkspaceOperation.owner_scope == get_workspace_owner_scope(),
                _owner(WorkspaceOperation.owner_id),
                WorkspaceOperation.kind == "presentation.generate",
                # Casting the JSON document to text avoids decoding unrelated strings.
                # PostgreSQL can reject a JSON string containing an escaped NUL during
                # ->> extraction, even when that operation belongs to another deck.
                cast(WorkspaceOperation.request_payload, String).contains(str(presentation_id)),
            )
            .order_by(WorkspaceOperation.created_at, WorkspaceOperation.id)
        )
    ).all()
    child_ids = [
        child_id
        for child_id, payload in candidate_children
        if isinstance(payload, dict) and payload.get("previous_presentation_id") == str(presentation_id)
    ]
    fields["next_presentation_ids"] = list(dict.fromkeys(child_ids))
    return fields
