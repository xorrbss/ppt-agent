from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
import uuid

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.presentation import PresentationModel
from models.sql.user import User
from models.sql.workspace import (
    WorkspaceConversation,
    WorkspaceOperation,
    WorkspaceOperationEvent,
    WorkspaceOutbox,
)
from services.presentation_publication_service import publish_presentation_revision
from services.workspace_operation_handler_registry import (
    WorkspaceOperationClaim,
    WorkspaceOperationResult,
)
from services.workspace_operation_runner import (
    WorkspaceOperationRunner,
    finalize_workspace_operation_success,
    reconcile_succeeded_workspace_operation_events,
)


TABLES = [
    User.__table__,
    PresentationModel.__table__,
    WorkspaceConversation.__table__,
    WorkspaceOperation.__table__,
    WorkspaceOperationEvent.__table__,
    WorkspaceOutbox.__table__,
]


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_safety(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection, tables=TABLES
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_running_operation(maker, *, kind: str):
    operation_id = uuid.uuid4()
    attempt_token = "publication-attempt"
    async with maker() as session, session.begin():
        presentation = PresentationModel(
            content="before",
            n_slides=1,
            language="en",
            layout={"name": "test"},
            revision=3,
        )
        session.add(presentation)
        await session.flush()
        conversation = WorkspaceConversation(presentation_id=presentation.id)
        session.add(conversation)
        await session.flush()
        operation = WorkspaceOperation(
            id=operation_id,
            conversation_id=conversation.id,
            presentation_id=presentation.id,
            anchor_position=1,
            kind=kind,
            state="running",
            idempotency_key_hash="a" * 64,
            request_hash="b" * 64,
            request_payload={"kind": kind},
            expected_revision=3,
            attempt_number=1,
            attempt_token=attempt_token,
            fencing_token=1,
        )
        session.add(operation)

    claim = WorkspaceOperationClaim(
        operation_id=operation_id,
        owner_id=None,
        owner_scope="anonymous",
        conversation_id=conversation.id,
        presentation_id=presentation.id,
        kind=kind,
        request_payload={"kind": kind},
        expected_revision=3,
        attempt_number=1,
        attempt_token=attempt_token,
        fencing_token=1,
    )
    return presentation.id, operation_id, claim


async def _terminal_counts(maker, operation_id):
    async with maker() as session:
        event_count = await session.scalar(
            select(func.count())
            .select_from(WorkspaceOperationEvent)
            .where(
                WorkspaceOperationEvent.operation_id == operation_id,
                WorkspaceOperationEvent.event_type == "operation.result",
            )
        )
        outbox_count = await session.scalar(
            select(func.count())
            .select_from(WorkspaceOutbox)
            .where(
                WorkspaceOutbox.operation_id == operation_id,
                WorkspaceOutbox.event_cursor.is_not(None),
            )
        )
    return int(event_count or 0), int(outbox_count or 0)


@pytest.mark.parametrize("kind", ["presentation.edit", "version.restore"])
def test_publication_commit_contains_terminal_event_and_outbox(kind: str, tmp_path: Path):
    asyncio.run(_publication_commit_contains_terminal_event_and_outbox(kind, tmp_path))


async def _publication_commit_contains_terminal_event_and_outbox(
    kind: str, tmp_path: Path
):
    engine, maker = await _database(tmp_path / f"{kind}-atomic.db")
    try:
        presentation_id, operation_id, claim = await _seed_running_operation(
            maker, kind=kind
        )
        result = {"kind": kind, "status": "completed", "revision": 4}

        async with maker() as session:
            revision = await publish_presentation_revision(
                session,
                presentation_id=presentation_id,
                expected_revision=3,
                operation_id=operation_id,
                attempt_token=claim.attempt_token,
                result=result,
            )
            await session.commit()

        # This is the crash boundary: the handler committed publication, but
        # the runner has not received its return value or run its finalizer.
        assert revision == 4
        assert await _terminal_counts(maker, operation_id) == (1, 1)
        async with maker() as session:
            presentation = await session.get(PresentationModel, presentation_id)
            operation = await session.get(WorkspaceOperation, operation_id)
            terminal_event = (
                await session.execute(
                    select(WorkspaceOperationEvent).where(
                        WorkspaceOperationEvent.operation_id == operation_id,
                        WorkspaceOperationEvent.event_type == "operation.result",
                    )
                )
            ).scalar_one()
            notification = (
                await session.execute(
                    select(WorkspaceOutbox).where(
                        WorkspaceOutbox.operation_id == operation_id,
                        WorkspaceOutbox.event_cursor == terminal_event.cursor,
                    )
                )
            ).scalar_one()
            assert presentation is not None and presentation.revision == 4
            assert operation is not None and operation.state == "succeeded"
            assert operation.result == result
            assert operation.result_revision == 4
            assert terminal_event.payload["state"] == "succeeded"
            assert terminal_event.payload["result_revision"] == 4
            assert notification.status == "pending"

        # Normal completion, retry, and startup repair all see the same durable
        # publication and cannot append a duplicate terminal notification.
        runner_result = WorkspaceOperationResult(payload=result, result_revision=4)
        assert await finalize_workspace_operation_success(
            claim, runner_result, session_factory=maker
        )
        assert await finalize_workspace_operation_success(
            claim, runner_result, session_factory=maker
        )
        assert (
            await reconcile_succeeded_workspace_operation_events(
                session_factory=maker
            )
            == 0
        )
        assert await _terminal_counts(maker, operation_id) == (1, 1)
    finally:
        await engine.dispose()


def test_runner_startup_repairs_legacy_success_once(tmp_path: Path):
    asyncio.run(_runner_startup_repairs_legacy_success_once(tmp_path))


async def _runner_startup_repairs_legacy_success_once(tmp_path: Path):
    engine, maker = await _database(tmp_path / "legacy-success.db")
    try:
        presentation_id, operation_id, _claim = await _seed_running_operation(
            maker, kind="presentation.edit"
        )
        now = datetime.now(timezone.utc)
        async with maker() as session, session.begin():
            operation = await session.get(WorkspaceOperation, operation_id)
            operation.state = "succeeded"
            operation.result = {"status": "completed", "revision": 4}
            operation.result_revision = 4
            operation.completed_at = now
            operation.attempt_token = None
            operation.lease_expires_at = None
            operation.version += 1
            presentation = await session.get(PresentationModel, presentation_id)
            presentation.revision = 4

        assert await _terminal_counts(maker, operation_id) == (0, 0)

        # Two cluster processes may start against the same stranded row.  The
        # version/missing-event CAS permits only one event/outbox transaction.
        first = WorkspaceOperationRunner(session_factory=maker, poll_seconds=60)
        second = WorkspaceOperationRunner(session_factory=maker, poll_seconds=60)
        await asyncio.wait_for(
            asyncio.gather(first.start(), second.start()), timeout=10
        )
        await asyncio.wait_for(
            asyncio.gather(first.stop(), second.stop()), timeout=10
        )

        assert await _terminal_counts(maker, operation_id) == (1, 1)
        assert (
            await reconcile_succeeded_workspace_operation_events(
                session_factory=maker
            )
            == 0
        )
        assert await _terminal_counts(maker, operation_id) == (1, 1)
    finally:
        await engine.dispose()


def test_terminal_event_failure_rolls_back_entire_publication(tmp_path: Path):
    asyncio.run(_terminal_event_failure_rolls_back_entire_publication(tmp_path))


async def _terminal_event_failure_rolls_back_entire_publication(tmp_path: Path):
    engine, maker = await _database(tmp_path / "atomic-rollback.db")
    try:
        presentation_id, operation_id, claim = await _seed_running_operation(
            maker, kind="presentation.edit"
        )

        async with maker() as session:
            with patch(
                "services.presentation_publication_service.append_operation_event",
                side_effect=RuntimeError("simulated_terminal_event_failure"),
            ):
                with pytest.raises(RuntimeError, match="simulated_terminal_event_failure"):
                    await publish_presentation_revision(
                        session,
                        presentation_id=presentation_id,
                        expected_revision=3,
                        operation_id=operation_id,
                        attempt_token=claim.attempt_token,
                        result={"status": "completed", "revision": 4},
                    )
            # Even a caller that catches the error and commits cannot persist
            # the projection/revision writes rolled back with the savepoint.
            await session.commit()

        async with maker() as session:
            presentation = await session.get(PresentationModel, presentation_id)
            operation = await session.get(WorkspaceOperation, operation_id)
            assert presentation is not None and presentation.revision == 3
            assert operation is not None and operation.state == "running"
            assert operation.result is None
            assert operation.result_revision is None
            assert operation.attempt_token == claim.attempt_token
        assert await _terminal_counts(maker, operation_id) == (0, 0)
    finally:
        await engine.dispose()
