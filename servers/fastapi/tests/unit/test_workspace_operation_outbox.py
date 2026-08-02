from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import uuid

from sqlalchemy import event, select, update
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
from services.workspace_operation_outbox_dispatcher import (
    WorkspaceOperationOutboxDispatcher,
    WorkspaceOutboxTopicRegistry,
    claim_next_workspace_outbox,
    heartbeat_workspace_outbox_claim,
    mark_workspace_outbox_delivered,
    mark_workspace_outbox_failed,
)
from utils.datetime_utils import get_current_utc_datetime


OUTBOX_TABLES = [
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
                sync_connection, tables=OUTBOX_TABLES
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_outbox(
    maker,
    *,
    topic: str = "test.topic",
    payload: dict | None = None,
    ordinal: int = 1,
) -> uuid.UUID:
    async with maker() as session, session.begin():
        row = WorkspaceOutbox(
            topic=topic,
            idempotency_key_hash=f"{ordinal:064x}",
            payload_hash=f"{ordinal + 100:064x}",
            payload=payload or {"stage": "queued"},
        )
        session.add(row)
        await session.flush()
        return row.id


async def _outbox(maker, outbox_id: uuid.UUID) -> WorkspaceOutbox:
    async with maker() as session:
        return (
            await session.execute(
                select(WorkspaceOutbox)
                .where(WorkspaceOutbox.id == outbox_id)
                .execution_options(skip_owner_scope=True)
            )
        ).scalar_one()


def test_sqlite_outbox_claim_is_atomic_and_stale_lease_is_fenced(
    tmp_path: Path,
):
    asyncio.run(_sqlite_outbox_claim_is_atomic_and_fenced(tmp_path))


async def _sqlite_outbox_claim_is_atomic_and_fenced(tmp_path: Path):
    engine, maker = await _database(tmp_path / "outbox-claim.db")
    try:
        await _seed_outbox(maker)
        claims = await asyncio.gather(
            claim_next_workspace_outbox(session_factory=maker),
            claim_next_workspace_outbox(session_factory=maker),
        )
        claimed = [claim for claim in claims if claim is not None]
        assert len(claimed) == 1

        claim = claimed[0]
        stale = replace(claim)
        assert await heartbeat_workspace_outbox_claim(
            claim, session_factory=maker, lease_seconds=30
        )
        assert not await mark_workspace_outbox_delivered(
            stale, session_factory=maker
        )
        assert await mark_workspace_outbox_delivered(
            claim, session_factory=maker
        )
    finally:
        await engine.dispose()


def test_outbox_delivery_payload_and_errors_are_metadata_only(tmp_path: Path):
    asyncio.run(_outbox_delivery_payload_and_errors_are_metadata_only(tmp_path))


async def _outbox_delivery_payload_and_errors_are_metadata_only(tmp_path: Path):
    engine, maker = await _database(tmp_path / "outbox-secrets.db")
    try:
        row_id = await _seed_outbox(
            maker,
            payload={
                "stage": "generating",
                "percent": 25,
                "Cookie": "session=must-not-escape",
                "prompt": "private prompt",
                "file_path": "C:/private/input.pptx",
            },
        )
        claim = await claim_next_workspace_outbox(session_factory=maker)
        assert claim is not None
        assert claim.payload == {"stage": "generating", "percent": 25}

        assert await mark_workspace_outbox_failed(
            claim,
            code="provider_temporarily_unavailable",
            retryable=True,
            max_attempts=2,
            session_factory=maker,
        )
        stored = await _outbox(maker, row_id)
        assert stored.status == "failed"
        assert stored.last_error == {
            "error_code": "provider_temporarily_unavailable",
            "retryable": True,
        }

        async with maker() as session:
            await session.execute(
                update(WorkspaceOutbox)
                .where(WorkspaceOutbox.id == row_id)
                .values(
                    available_at=get_current_utc_datetime()
                    - timedelta(seconds=1)
                )
            )
            await session.commit()
        second_claim = await claim_next_workspace_outbox(
            session_factory=maker
        )
        assert second_claim is not None
        assert await mark_workspace_outbox_failed(
            second_claim,
            code="provider_temporarily_unavailable",
            retryable=True,
            max_attempts=2,
            session_factory=maker,
        )
        terminal = await _outbox(maker, row_id)
        assert terminal.status == "dead_letter"
        assert terminal.lease_expires_at is None
    finally:
        await engine.dispose()


def test_dispatcher_isolates_delivery_failures_and_dead_letters_unknown_topics(
    tmp_path: Path,
):
    asyncio.run(_dispatcher_isolates_delivery_failures(tmp_path))


async def _dispatcher_isolates_delivery_failures(tmp_path: Path):
    engine, maker = await _database(tmp_path / "outbox-dispatch.db")
    try:
        failed_id = await _seed_outbox(
            maker, topic="test.fail", ordinal=1
        )
        delivered_id = await _seed_outbox(
            maker, topic="test.ok", ordinal=2
        )
        unknown_id = await _seed_outbox(
            maker, topic="test.unknown", ordinal=3
        )
        delivered_payloads: list[dict] = []

        async def failing_handler(_payload):
            raise RuntimeError("sensitive provider response")

        async def successful_handler(payload):
            delivered_payloads.append(dict(payload))

        registry = WorkspaceOutboxTopicRegistry()
        registry.register("test.fail", failing_handler)
        registry.register("test.ok", successful_handler)
        dispatcher = WorkspaceOperationOutboxDispatcher(
            session_factory=maker,
            registry=registry,
            max_concurrency=3,
            max_attempts=2,
            heartbeat_seconds=60,
        )
        assert await dispatcher.dispatch_once() == 3
        tasks = tuple(dispatcher._active.values())
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)

        failed = await _outbox(maker, failed_id)
        delivered = await _outbox(maker, delivered_id)
        unknown = await _outbox(maker, unknown_id)
        assert failed.status == "failed"
        assert failed.last_error == {
            "error_code": "workspace_outbox_delivery_failed",
            "retryable": True,
        }
        assert delivered.status == "delivered"
        assert delivered_payloads == [{"stage": "queued"}]
        assert unknown.status == "dead_letter"
        assert unknown.last_error == {
            "error_code": "unsupported_outbox_topic",
            "retryable": False,
        }
    finally:
        await engine.dispose()


def test_dispatcher_fences_handler_when_heartbeat_loses_lease(
    tmp_path: Path,
    monkeypatch,
):
    asyncio.run(
        _dispatcher_fences_handler_when_heartbeat_loses_lease(
            tmp_path,
            monkeypatch,
        )
    )


async def _dispatcher_fences_handler_when_heartbeat_loses_lease(
    tmp_path: Path,
    monkeypatch,
):
    engine, maker = await _database(tmp_path / "outbox-lease-loss.db")
    try:
        row_id = await _seed_outbox(
            maker, topic="test.blocking", ordinal=1
        )
        started = asyncio.Event()
        cancelled = asyncio.Event()
        never_release = asyncio.Event()

        async def blocking_handler(_payload):
            started.set()
            try:
                await never_release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        async def lose_lease(_claim, **_kwargs):
            return False

        monkeypatch.setattr(
            "services.workspace_operation_outbox_dispatcher."
            "heartbeat_workspace_outbox_claim",
            lose_lease,
        )
        registry = WorkspaceOutboxTopicRegistry()
        registry.register("test.blocking", blocking_handler)
        dispatcher = WorkspaceOperationOutboxDispatcher(
            session_factory=maker,
            registry=registry,
            max_concurrency=1,
            heartbeat_seconds=0.01,
        )

        assert await dispatcher.dispatch_once() == 1
        tasks = tuple(dispatcher._active.values())
        await asyncio.wait_for(started.wait(), timeout=1)
        await asyncio.wait_for(cancelled.wait(), timeout=1)
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)

        stored = await _outbox(maker, row_id)
        assert stored.status == "delivering"
        assert stored.delivered_at is None
        assert stored.last_error is None
    finally:
        await engine.dispose()
