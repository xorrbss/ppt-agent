from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
import uuid

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.presentation import PresentationModel
from models.sql.user import User
from models.sql.workspace import (
    WorkspaceConversation,
    WorkspaceMessage,
    WorkspaceOperation,
    WorkspaceOperationEvent,
)
from services.workspace_event_retention import (
    DEFAULT_WORKSPACE_EVENT_RETENTION_BATCH_SIZE,
    DEFAULT_WORKSPACE_EVENT_RETENTION_MAINTENANCE_INTERVAL_SECONDS,
    DEFAULT_WORKSPACE_EVENT_RETENTION_SECONDS,
    MAX_WORKSPACE_EVENT_RETENTION_BATCH_SIZE,
    WORKSPACE_EVENT_RETENTION_BATCH_SIZE_ENV,
    WORKSPACE_EVENT_RETENTION_INTERVAL_SECONDS_ENV,
    WORKSPACE_EVENT_RETENTION_SECONDS_ENV,
    compact_workspace_progress_events,
    get_workspace_event_retention_config,
)
from utils.datetime_utils import get_current_utc_datetime


RETENTION_TABLES = [
    User.__table__,
    PresentationModel.__table__,
    WorkspaceConversation.__table__,
    WorkspaceMessage.__table__,
    WorkspaceOperation.__table__,
    WorkspaceOperationEvent.__table__,
]


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _configure_sqlite(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=RETENTION_TABLES,
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_workspace_event_retention_config_defaults_and_explicit_disable(
    monkeypatch: pytest.MonkeyPatch,
):
    for name in (
        WORKSPACE_EVENT_RETENTION_SECONDS_ENV,
        WORKSPACE_EVENT_RETENTION_INTERVAL_SECONDS_ENV,
        WORKSPACE_EVENT_RETENTION_BATCH_SIZE_ENV,
    ):
        monkeypatch.delenv(name, raising=False)

    assert get_workspace_event_retention_config().retention_seconds == (
        DEFAULT_WORKSPACE_EVENT_RETENTION_SECONDS
    )
    assert get_workspace_event_retention_config().maintenance_interval_seconds == (
        DEFAULT_WORKSPACE_EVENT_RETENTION_MAINTENANCE_INTERVAL_SECONDS
    )
    assert get_workspace_event_retention_config().batch_size == (
        DEFAULT_WORKSPACE_EVENT_RETENTION_BATCH_SIZE
    )

    monkeypatch.setenv(WORKSPACE_EVENT_RETENTION_SECONDS_ENV, " DiSaBlEd ")
    assert get_workspace_event_retention_config().enabled is False


@pytest.mark.parametrize(
    ("name", "value"),
    [
        (WORKSPACE_EVENT_RETENTION_SECONDS_ENV, "0"),
        (WORKSPACE_EVENT_RETENTION_SECONDS_ENV, "-1"),
        (WORKSPACE_EVENT_RETENTION_SECONDS_ENV, "not-a-number"),
        (WORKSPACE_EVENT_RETENTION_INTERVAL_SECONDS_ENV, "0"),
        (WORKSPACE_EVENT_RETENTION_BATCH_SIZE_ENV, "0"),
        (
            WORKSPACE_EVENT_RETENTION_BATCH_SIZE_ENV,
            str(MAX_WORKSPACE_EVENT_RETENTION_BATCH_SIZE + 1),
        ),
    ],
)
def test_workspace_event_retention_rejects_invalid_enabled_values(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
):
    for variable in (
        WORKSPACE_EVENT_RETENTION_SECONDS_ENV,
        WORKSPACE_EVENT_RETENTION_INTERVAL_SECONDS_ENV,
        WORKSPACE_EVENT_RETENTION_BATCH_SIZE_ENV,
    ):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError):
        get_workspace_event_retention_config()


def test_retention_is_bounded_scoped_monotonic_and_preserves_durable_history(
    tmp_path: Path,
):
    asyncio.run(_retention_is_bounded_scoped_and_preserves_history(tmp_path))


async def _retention_is_bounded_scoped_and_preserves_history(tmp_path: Path):
    engine, maker = await _database(tmp_path / "workspace-event-retention.db")
    now = get_current_utc_datetime()
    old = now - timedelta(days=31)
    recent = now - timedelta(days=1)
    owner_a = uuid.uuid4()
    owner_b = uuid.uuid4()

    try:
        async with maker() as session, session.begin():
            session.add_all(
                [
                    User(
                        id=owner_a,
                        username="retention-owner-a",
                        hashed_password="test-only",
                    ),
                    User(
                        id=owner_b,
                        username="retention-owner-b",
                        hashed_password="test-only",
                    ),
                ]
            )
            presentation_a = PresentationModel(
                owner_id=owner_a,
                content="owner a",
                n_slides=1,
                language="en",
                layout={"name": "test"},
            )
            presentation_b = PresentationModel(
                owner_id=owner_b,
                content="owner b",
                n_slides=1,
                language="en",
                layout={"name": "test"},
            )
            session.add_all([presentation_a, presentation_b])
            await session.flush()

            conversation_a = WorkspaceConversation(
                owner_id=owner_a,
                owner_scope=str(owner_a),
                presentation_id=presentation_a.id,
                next_position=4,
            )
            conversation_b = WorkspaceConversation(
                owner_id=owner_b,
                owner_scope=str(owner_b),
                presentation_id=presentation_b.id,
                next_position=3,
            )
            session.add_all([conversation_a, conversation_b])
            await session.flush()
            message_a = WorkspaceMessage(
                owner_id=owner_a,
                conversation_id=conversation_a.id,
                position=1,
                role="user",
                content="keep the durable message",
            )
            terminal_a = WorkspaceOperation(
                owner_id=owner_a,
                owner_scope=str(owner_a),
                conversation_id=conversation_a.id,
                presentation_id=presentation_a.id,
                anchor_position=2,
                kind="chat",
                state="succeeded",
                idempotency_key_hash="a" * 64,
                request_hash="b" * 64,
            )
            active_a = WorkspaceOperation(
                owner_id=owner_a,
                owner_scope=str(owner_a),
                conversation_id=conversation_a.id,
                presentation_id=presentation_a.id,
                anchor_position=3,
                kind="chat",
                state="running",
                idempotency_key_hash="c" * 64,
                request_hash="d" * 64,
            )
            terminal_b = WorkspaceOperation(
                owner_id=owner_b,
                owner_scope=str(owner_b),
                conversation_id=conversation_b.id,
                presentation_id=presentation_b.id,
                anchor_position=2,
                kind="chat",
                state="failed",
                idempotency_key_hash="e" * 64,
                request_hash="f" * 64,
            )
            session.add_all([message_a, terminal_a, active_a, terminal_b])
            await session.flush()

            events: list[WorkspaceOperationEvent] = []

            async def add_event(
                operation: WorkspaceOperation,
                owner_id: uuid.UUID,
                sequence: int,
                event_type: str,
                created_at,
            ) -> WorkspaceOperationEvent:
                row = WorkspaceOperationEvent(
                    owner_id=owner_id,
                    operation_id=operation.id,
                    sequence=sequence,
                    operation_version=0,
                    event_type=event_type,
                    payload={"type": event_type},
                    created_at=created_at,
                )
                session.add(row)
                await session.flush()
                events.append(row)
                return row

            progress_a_1 = await add_event(
                terminal_a, owner_a, 1, "operation.progress", old
            )
            progress_a_2 = await add_event(
                terminal_a, owner_a, 2, "operation.progress", old
            )
            preserved_types = (
                "operation.result",
                "operation.error",
                "operation.cancelled",
                "operation.action",
                "asset.ready",
            )
            for sequence, event_type in enumerate(preserved_types, start=3):
                await add_event(terminal_a, owner_a, sequence, event_type, old)
            recent_progress = await add_event(
                terminal_a, owner_a, 8, "operation.progress", recent
            )
            progress_b = await add_event(
                terminal_b, owner_b, 1, "operation.progress", old
            )
            active_progress = await add_event(
                active_a, owner_a, 1, "operation.progress", old
            )

            deleted_cursors = {
                progress_a_1.cursor,
                progress_a_2.cursor,
                progress_b.cursor,
            }
            retained_cursors = {
                event.cursor for event in events if event.cursor not in deleted_cursors
            }
            floor_a = progress_a_2.cursor
            floor_b = progress_b.cursor
            message_a_id = message_a.id
            terminal_a_id = terminal_a.id
            active_a_id = active_a.id

        async with maker() as session:
            first = await compact_workspace_progress_events(
                session,
                older_than=now - timedelta(days=30),
                batch_size=2,
            )
        assert first.selected == 2
        assert first.deleted == 2
        assert first.conversations_advanced == 1

        async with maker() as session:
            refreshed_a = await session.get(WorkspaceConversation, conversation_a.id)
            refreshed_b = await session.get(WorkspaceConversation, conversation_b.id)
            assert refreshed_a.event_retention_floor_cursor == floor_a
            assert refreshed_b.event_retention_floor_cursor == 0
            remaining = set(
                (await session.scalars(select(WorkspaceOperationEvent.cursor))).all()
            )
            assert progress_a_1.cursor not in remaining
            assert progress_a_2.cursor not in remaining
            assert retained_cursors.issubset(remaining)
            assert recent_progress.cursor in remaining
            assert active_progress.cursor in remaining
            assert await session.get(WorkspaceMessage, message_a_id) is not None
            assert await session.get(WorkspaceOperation, terminal_a_id) is not None
            assert await session.get(WorkspaceOperation, active_a_id) is not None

        async with maker() as session:
            second = await compact_workspace_progress_events(
                session,
                older_than=now - timedelta(days=30),
                batch_size=2,
            )
        assert second.selected == 1
        assert second.deleted == 1
        assert second.conversations_advanced == 1

        async with maker() as session:
            refreshed_a = await session.get(WorkspaceConversation, conversation_a.id)
            refreshed_b = await session.get(WorkspaceConversation, conversation_b.id)
            assert refreshed_a.event_retention_floor_cursor == floor_a
            assert refreshed_b.event_retention_floor_cursor == floor_b
            remaining = set(
                (await session.scalars(select(WorkspaceOperationEvent.cursor))).all()
            )
            assert progress_b.cursor not in remaining
            assert retained_cursors.issubset(remaining)

        async with maker() as session:
            third = await compact_workspace_progress_events(
                session,
                older_than=now - timedelta(days=30),
                batch_size=2,
            )
        assert third.selected == 0
        assert third.deleted == 0
        assert third.conversations_advanced == 0
    finally:
        await engine.dispose()
