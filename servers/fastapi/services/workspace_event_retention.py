"""Bounded retention for high-frequency durable workspace progress events.

Only progress emitted by terminal operations is compacted.  Every conversation
keeps its own durable floor so global cursor gaps from other conversations can
never make a replay cursor look expired.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import os

from sqlalchemy import case, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.sql.workspace import (
    WORKSPACE_OPERATION_STATES,
    WorkspaceConversation,
    WorkspaceOperation,
    WorkspaceOperationEvent,
)
from services.database import async_session_maker
from utils.datetime_utils import get_current_utc_datetime


logger = logging.getLogger(__name__)

DEFAULT_WORKSPACE_EVENT_RETENTION_SECONDS = 30 * 24 * 60 * 60
DEFAULT_WORKSPACE_EVENT_RETENTION_MAINTENANCE_INTERVAL_SECONDS = 60 * 60
DEFAULT_WORKSPACE_EVENT_RETENTION_BATCH_SIZE = 500
MAX_WORKSPACE_EVENT_RETENTION_BATCH_SIZE = 10_000

WORKSPACE_EVENT_RETENTION_SECONDS_ENV = "WORKSPACE_EVENT_RETENTION_SECONDS"
WORKSPACE_EVENT_RETENTION_INTERVAL_SECONDS_ENV = (
    "WORKSPACE_EVENT_RETENTION_MAINTENANCE_INTERVAL_SECONDS"
)
WORKSPACE_EVENT_RETENTION_BATCH_SIZE_ENV = "WORKSPACE_EVENT_RETENTION_BATCH_SIZE"

_TERMINAL_OPERATION_STATES = frozenset(
    state
    for state in WORKSPACE_OPERATION_STATES
    if state in {"succeeded", "failed", "cancelled", "dead_letter"}
)
_DISABLED_VALUE = "disabled"


@dataclass(frozen=True)
class WorkspaceEventRetentionConfig:
    retention_seconds: int | None
    maintenance_interval_seconds: int
    batch_size: int

    @property
    def enabled(self) -> bool:
        return self.retention_seconds is not None


@dataclass(frozen=True)
class WorkspaceEventRetentionSummary:
    selected: int = 0
    deleted: int = 0
    conversations_advanced: int = 0


_maintenance_task: asyncio.Task[None] | None = None
_maintenance_stop: asyncio.Event | None = None


def _positive_int_env(name: str, default: int, *, maximum: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError as error:
        raise ValueError(f"{name}_must_be_a_positive_integer") from error
    if value <= 0 or (maximum is not None and value > maximum):
        suffix = (
            f"_between_1_and_{maximum}"
            if maximum is not None
            else "_must_be_a_positive_integer"
        )
        raise ValueError(f"{name}{suffix}")
    return value


def get_workspace_event_retention_config() -> WorkspaceEventRetentionConfig:
    raw_retention = os.getenv(WORKSPACE_EVENT_RETENTION_SECONDS_ENV)
    if raw_retention is not None and raw_retention.strip().lower() == _DISABLED_VALUE:
        retention_seconds = None
    else:
        retention_seconds = _positive_int_env(
            WORKSPACE_EVENT_RETENTION_SECONDS_ENV,
            DEFAULT_WORKSPACE_EVENT_RETENTION_SECONDS,
        )
    return WorkspaceEventRetentionConfig(
        retention_seconds=retention_seconds,
        maintenance_interval_seconds=_positive_int_env(
            WORKSPACE_EVENT_RETENTION_INTERVAL_SECONDS_ENV,
            DEFAULT_WORKSPACE_EVENT_RETENTION_MAINTENANCE_INTERVAL_SECONDS,
        ),
        batch_size=_positive_int_env(
            WORKSPACE_EVENT_RETENTION_BATCH_SIZE_ENV,
            DEFAULT_WORKSPACE_EVENT_RETENTION_BATCH_SIZE,
            maximum=MAX_WORKSPACE_EVENT_RETENTION_BATCH_SIZE,
        ),
    )


async def compact_workspace_progress_events(
    session: AsyncSession,
    *,
    older_than: datetime,
    batch_size: int = DEFAULT_WORKSPACE_EVENT_RETENTION_BATCH_SIZE,
) -> WorkspaceEventRetentionSummary:
    """Delete one batch and monotonically advance affected conversation floors.

    Candidate selection is lock-skipping on PostgreSQL.  The guarded delete and
    monotonic floor updates also make a duplicate selection harmless on SQLite
    or during a worker race.
    """

    if batch_size <= 0 or batch_size > MAX_WORKSPACE_EVENT_RETENTION_BATCH_SIZE:
        raise ValueError(
            f"batch_size_must_be_between_1_and_{MAX_WORKSPACE_EVENT_RETENTION_BATCH_SIZE}"
        )

    candidate_statement = (
        select(
            WorkspaceOperationEvent.cursor,
            WorkspaceOperation.conversation_id,
        )
        .join(
            WorkspaceOperation,
            WorkspaceOperation.id == WorkspaceOperationEvent.operation_id,
        )
        .where(
            WorkspaceOperation.state.in_(_TERMINAL_OPERATION_STATES),
            WorkspaceOperationEvent.event_type == "operation.progress",
            WorkspaceOperationEvent.created_at < older_than,
        )
        .order_by(WorkspaceOperationEvent.cursor.asc())
        .limit(batch_size)
    )
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        candidate_statement = candidate_statement.with_for_update(
            skip_locked=True,
            of=WorkspaceOperationEvent,
        )

    candidates = list((await session.execute(candidate_statement)).all())
    if not candidates:
        return WorkspaceEventRetentionSummary()

    cursors = [cursor for cursor, _conversation_id in candidates]
    floors: dict[object, int] = {}
    for cursor, conversation_id in candidates:
        floors[conversation_id] = max(floors.get(conversation_id, 0), cursor)

    terminal_operation_ids = select(WorkspaceOperation.id).where(
        WorkspaceOperation.state.in_(_TERMINAL_OPERATION_STATES)
    )
    deletion = await session.execute(
        delete(WorkspaceOperationEvent).where(
            WorkspaceOperationEvent.cursor.in_(cursors),
            WorkspaceOperationEvent.operation_id.in_(terminal_operation_ids),
            WorkspaceOperationEvent.event_type == "operation.progress",
            WorkspaceOperationEvent.created_at < older_than,
        )
    )

    conversations_advanced = 0
    for conversation_id, floor in floors.items():
        result = await session.execute(
            update(WorkspaceConversation)
            .where(
                WorkspaceConversation.id == conversation_id,
                WorkspaceConversation.event_retention_floor_cursor < floor,
            )
            .values(
                event_retention_floor_cursor=case(
                    (
                        WorkspaceConversation.event_retention_floor_cursor < floor,
                        floor,
                    ),
                    else_=WorkspaceConversation.event_retention_floor_cursor,
                )
            )
            .execution_options(synchronize_session=False)
        )
        conversations_advanced += int(result.rowcount == 1)

    await session.commit()
    return WorkspaceEventRetentionSummary(
        selected=len(candidates),
        deleted=max(0, deletion.rowcount or 0),
        conversations_advanced=conversations_advanced,
    )


async def run_workspace_event_retention(
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    now: datetime | None = None,
    config: WorkspaceEventRetentionConfig | None = None,
) -> WorkspaceEventRetentionSummary:
    active_config = config or get_workspace_event_retention_config()
    retention_seconds = active_config.retention_seconds
    if retention_seconds is None:
        return WorkspaceEventRetentionSummary()
    current = now or get_current_utc_datetime()
    cutoff = current - timedelta(seconds=retention_seconds)
    factory = session_factory or async_session_maker
    async with factory() as session:
        return await compact_workspace_progress_events(
            session,
            older_than=cutoff,
            batch_size=active_config.batch_size,
        )


async def _workspace_event_retention_loop(
    config: WorkspaceEventRetentionConfig,
) -> None:
    assert _maintenance_stop is not None
    while not _maintenance_stop.is_set():
        try:
            summary = await run_workspace_event_retention(config=config)
            if summary.deleted:
                logger.info(
                    "Workspace event retention completed: selected=%d "
                    "deleted=%d conversations_advanced=%d",
                    summary.selected,
                    summary.deleted,
                    summary.conversations_advanced,
                )
        except Exception:
            logger.exception("Workspace event retention iteration failed")
        try:
            await asyncio.wait_for(
                _maintenance_stop.wait(),
                timeout=config.maintenance_interval_seconds,
            )
        except TimeoutError:
            pass


async def start_workspace_event_retention() -> None:
    """Validate configuration, sweep once, then start periodic maintenance."""

    global _maintenance_stop, _maintenance_task
    config = get_workspace_event_retention_config()
    if not config.enabled:
        return
    if _maintenance_task is not None and not _maintenance_task.done():
        return
    _maintenance_stop = asyncio.Event()
    try:
        await run_workspace_event_retention(config=config)
    except Exception:
        logger.exception("Initial workspace event retention pass failed")
    _maintenance_task = asyncio.create_task(
        _workspace_event_retention_loop(config),
        name="workspace-event-retention",
    )


async def stop_workspace_event_retention() -> None:
    global _maintenance_stop, _maintenance_task
    if _maintenance_stop is not None:
        _maintenance_stop.set()
    if _maintenance_task is not None:
        await _maintenance_task
    _maintenance_task = None
    _maintenance_stop = None
