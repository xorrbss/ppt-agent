from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional
import uuid

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.workspace import (
    WorkspaceConversation,
    WorkspaceMessage,
    WorkspaceOperation,
)
from utils.datetime_utils import get_current_utc_datetime
from utils.owner_context import get_current_owner_id


class WorkspacePositionConflict(RuntimeError):
    code = "workspace_position_conflict"


class WorkspaceEventSequenceConflict(RuntimeError):
    code = "workspace_event_sequence_conflict"


@dataclass(frozen=True)
class PositionReservation:
    start: int
    stop: int
    conversation_version: int

    @property
    def positions(self) -> range:
        return range(self.start, self.stop)


async def reserve_conversation_positions(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    count: int,
    expected_version: Optional[int] = None,
) -> PositionReservation:
    """Atomically reserve a contiguous top-level timeline range.

    The UPDATE is the serialization primitive on both SQLite and PostgreSQL.
    The caller owns the surrounding transaction and commit/rollback boundary.
    """

    if count < 1:
        raise ValueError("count must be at least 1")
    statement = (
        update(WorkspaceConversation)
        .where(WorkspaceConversation.id == conversation_id)
        .values(
            next_position=WorkspaceConversation.next_position + count,
            version=WorkspaceConversation.version + 1,
            updated_at=get_current_utc_datetime(),
        )
        .returning(
            WorkspaceConversation.next_position,
            WorkspaceConversation.version,
        )
    )
    owner_id = get_current_owner_id()
    if owner_id is not None:
        statement = statement.where(WorkspaceConversation.owner_id == owner_id)
    if expected_version is not None:
        statement = statement.where(WorkspaceConversation.version == expected_version)
    row = (await session.execute(statement)).one_or_none()
    if row is None:
        raise WorkspacePositionConflict(WorkspacePositionConflict.code)
    stop = int(row.next_position)
    return PositionReservation(
        start=stop - count,
        stop=stop,
        conversation_version=int(row.version),
    )


async def bootstrap_workspace_turn(
    session: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    message: Mapping[str, Any],
    operation: Mapping[str, Any],
) -> tuple[WorkspaceMessage, WorkspaceOperation]:
    """Persist the immutable first user message and operation anchor together."""

    reservation = await reserve_conversation_positions(session, conversation_id, 2)
    first_position, operation_position = reservation.positions
    workspace_message = WorkspaceMessage(
        **dict(message),
        conversation_id=conversation_id,
        position=first_position,
        role="user",
    )
    workspace_operation = WorkspaceOperation(
        **dict(operation),
        conversation_id=conversation_id,
        anchor_position=operation_position,
    )
    session.add_all((workspace_message, workspace_operation))
    await session.flush()
    return workspace_message, workspace_operation


async def reserve_operation_event_sequence(
    session: AsyncSession,
    operation_id: uuid.UUID,
) -> int:
    """Reserve one per-operation event sequence without touching its CAS version."""

    statement = (
        update(WorkspaceOperation)
        .where(WorkspaceOperation.id == operation_id)
        .values(
            next_event_sequence=WorkspaceOperation.next_event_sequence + 1,
            updated_at=get_current_utc_datetime(),
        )
        .returning(WorkspaceOperation.next_event_sequence)
    )
    owner_id = get_current_owner_id()
    if owner_id is not None:
        statement = statement.where(WorkspaceOperation.owner_id == owner_id)
    next_sequence = (await session.execute(statement)).scalar_one_or_none()
    if next_sequence is None:
        raise WorkspaceEventSequenceConflict(WorkspaceEventSequenceConflict.code)
    return int(next_sequence) - 1
