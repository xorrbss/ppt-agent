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
from services.workspace_operation_handler_registry import (
    WorkspaceOperationHandlerRegistry,
    WorkspaceOperationResult,
)
from services.workspace_operation_runner import (
    WorkspaceOperationRunner,
    claim_next_workspace_operation,
    finalize_workspace_operation_success,
    heartbeat_workspace_operation,
    recover_expired_workspace_operations,
    request_workspace_operation_cancellation,
)
from utils.datetime_utils import get_current_utc_datetime
from utils.owner_context import (
    get_current_owner_id,
    reset_current_owner_id,
    set_current_owner_id,
)


OWNER_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
RUNNER_TABLES = [
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
                sync_connection, tables=RUNNER_TABLES
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_operations(
    maker,
    *,
    count: int = 1,
    owner_id: uuid.UUID | None = None,
    revision: int = 0,
    expected_revision: int | None = None,
    kind: str = "test.operation",
) -> tuple[uuid.UUID, list[uuid.UUID]]:
    owner_token = set_current_owner_id(owner_id)
    try:
        async with maker() as session, session.begin():
            if owner_id is not None:
                session.add(
                    User(
                        id=owner_id,
                        username=f"owner-{owner_id}",
                        hashed_password="test-only",
                    )
                )
            presentation = PresentationModel(
                content="runner test",
                n_slides=1,
                language="en",
                layout={"name": "test"},
                revision=revision,
            )
            session.add(presentation)
            await session.flush()
            conversation = WorkspaceConversation(
                presentation_id=presentation.id,
                next_position=count + 1,
            )
            session.add(conversation)
            await session.flush()
            operations: list[WorkspaceOperation] = []
            for index in range(count):
                operation = WorkspaceOperation(
                    conversation_id=conversation.id,
                    presentation_id=presentation.id,
                    anchor_position=index + 1,
                    kind=kind,
                    idempotency_key_hash=f"{index + 1:064x}",
                    request_hash=f"{index + 101:064x}",
                    request_payload={"ordinal": index},
                    expected_revision=expected_revision,
                )
                session.add(operation)
                operations.append(operation)
            await session.flush()
            return presentation.id, [operation.id for operation in operations]
    finally:
        reset_current_owner_id(owner_token)


async def _operation(maker, operation_id: uuid.UUID) -> WorkspaceOperation:
    async with maker() as session:
        operation = (
            await session.execute(
                select(WorkspaceOperation)
                .where(WorkspaceOperation.id == operation_id)
                .execution_options(skip_owner_scope=True)
            )
        ).scalar_one()
        return operation


async def _events(maker, operation_id: uuid.UUID) -> list[WorkspaceOperationEvent]:
    async with maker() as session:
        return list(
            (
                await session.execute(
                    select(WorkspaceOperationEvent)
                    .where(WorkspaceOperationEvent.operation_id == operation_id)
                    .order_by(WorkspaceOperationEvent.sequence)
                    .execution_options(skip_owner_scope=True)
                )
            )
            .scalars()
            .all()
        )


async def _drain_once(runner: WorkspaceOperationRunner) -> int:
    dispatched = await runner.dispatch_once()
    tasks = tuple(runner._active.values())
    if tasks:
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)
    return dispatched


def test_sqlite_claims_serialize_operations_for_the_same_presentation(
    tmp_path: Path,
):
    asyncio.run(_claims_serialize_operations(tmp_path))


async def _claims_serialize_operations(tmp_path: Path):
    engine, maker = await _database(tmp_path / "serialize.db")
    try:
        _, operation_ids = await _seed_operations(maker, count=2)
        claims = await asyncio.gather(
            claim_next_workspace_operation(session_factory=maker),
            claim_next_workspace_operation(session_factory=maker),
        )
        claimed = [claim for claim in claims if claim is not None]
        assert len(claimed) == 1
        assert await claim_next_workspace_operation(session_factory=maker) is None

        assert await finalize_workspace_operation_success(
            claimed[0], WorkspaceOperationResult(), session_factory=maker
        )
        finalized = await _operation(maker, claimed[0].operation_id)
        finalized_events = await _events(maker, claimed[0].operation_id)
        assert [event.operation_version for event in finalized_events] == [1, 2]
        assert finalized.version == 2
        second = await claim_next_workspace_operation(session_factory=maker)
        assert second is not None
        assert second.operation_id in operation_ids
        assert second.operation_id != claimed[0].operation_id
    finally:
        await engine.dispose()


def test_stale_attempt_token_cannot_heartbeat_or_finalize(tmp_path: Path):
    asyncio.run(_stale_attempt_token_cannot_finalize(tmp_path))


async def _stale_attempt_token_cannot_finalize(tmp_path: Path):
    engine, maker = await _database(tmp_path / "fencing.db")
    try:
        await _seed_operations(maker)
        claim = await claim_next_workspace_operation(session_factory=maker)
        assert claim is not None
        stale = replace(claim, attempt_token="stale-token")
        assert not await heartbeat_workspace_operation(
            stale.operation_id, stale.attempt_token, session_factory=maker
        )
        assert not await finalize_workspace_operation_success(
            stale, WorkspaceOperationResult(), session_factory=maker
        )
        assert await finalize_workspace_operation_success(
            claim, WorkspaceOperationResult(), session_factory=maker
        )
    finally:
        await engine.dispose()


def test_expired_worker_lease_is_recovered_with_a_durable_error_event(
    tmp_path: Path,
):
    asyncio.run(_expired_worker_lease_is_recovered(tmp_path))


async def _expired_worker_lease_is_recovered(tmp_path: Path):
    engine, maker = await _database(tmp_path / "recovery.db")
    try:
        _, operation_ids = await _seed_operations(maker)
        operation_id = operation_ids[0]
        expired = get_current_utc_datetime() - timedelta(seconds=10)
        async with maker() as session:
            await session.execute(
                update(WorkspaceOperation)
                .where(WorkspaceOperation.id == operation_id)
                .values(
                    state="running",
                    attempt_number=1,
                    attempt_token="abandoned-attempt",
                    fencing_token=1,
                    lease_expires_at=expired,
                    heartbeat_at=expired,
                )
            )
            await session.commit()

        registry = WorkspaceOperationHandlerRegistry()
        registry.register(
            "test.operation",
            lambda _context: None,
            recovery_policy="automatic",
        )
        assert (
            await recover_expired_workspace_operations(
                session_factory=maker,
                registry=registry,
            )
            == 1
        )
        recovered = await _operation(maker, operation_id)
        assert recovered.state == "queued"
        assert recovered.attempt_token is None
        assert recovered.retry_count == 1
        assert recovered.error_code == "operation_recovery_required"
        events = await _events(maker, operation_id)
        assert [event.event_type for event in events] == ["operation.error"]
        assert [event.operation_version for event in events] == [recovered.version]
    finally:
        await engine.dispose()


def test_expired_manual_handler_requires_recovery_without_requeueing(
    tmp_path: Path,
):
    asyncio.run(_expired_manual_handler_requires_recovery(tmp_path))


async def _expired_manual_handler_requires_recovery(tmp_path: Path):
    engine, maker = await _database(tmp_path / "manual-recovery.db")
    try:
        _, operation_ids = await _seed_operations(maker)
        operation_id = operation_ids[0]
        expired = get_current_utc_datetime() - timedelta(seconds=10)
        async with maker() as session:
            await session.execute(
                update(WorkspaceOperation)
                .where(WorkspaceOperation.id == operation_id)
                .values(
                    state="running",
                    attempt_number=1,
                    attempt_token="unsafe-abandoned-attempt",
                    fencing_token=1,
                    lease_expires_at=expired,
                    heartbeat_at=expired,
                )
            )
            await session.commit()

        registry = WorkspaceOperationHandlerRegistry()
        registry.register(
            "test.operation",
            lambda _context: None,
            recovery_policy="manual",
        )
        assert (
            await recover_expired_workspace_operations(
                session_factory=maker,
                registry=registry,
            )
            == 1
        )
        recovered = await _operation(maker, operation_id)
        assert recovered.state == "failed"
        assert recovered.attempt_token is None
        assert recovered.lease_expires_at is None
        assert recovered.retry_count == 0
        assert recovered.backoff_seconds == 0
        assert recovered.error_code == "operation_recovery_required"
        assert recovered.error_detail == {
            "error_code": "operation_recovery_required",
            "retryable": False,
            "reason_code": "manual_recovery_required",
        }
        events = await _events(maker, operation_id)
        assert [event.event_type for event in events] == ["operation.error"]
        assert events[0].payload["state"] == "failed"
        assert events[0].payload["retryable"] is False
        assert events[0].payload["reason_code"] == "manual_recovery_required"
    finally:
        await engine.dispose()


def test_registry_defaults_unknown_handlers_to_manual_and_generation_to_automatic():
    registry = WorkspaceOperationHandlerRegistry()

    assert registry.recovery_policy("unknown.operation") == "manual"
    assert registry.recovery_policy("presentation.generate") == "automatic"


def test_cross_process_cancel_terminalizes_active_attempt_without_lease_wait(
    tmp_path: Path,
):
    asyncio.run(_cross_process_cancel_terminalizes_active_attempt(tmp_path))


async def _cross_process_cancel_terminalizes_active_attempt(tmp_path: Path):
    engine, maker = await _database(tmp_path / "remote-cancel.db")
    allow_handler_cleanup = asyncio.Event()
    try:
        _, operation_ids = await _seed_operations(maker)
        operation_id = operation_ids[0]
        started = asyncio.Event()
        handler_cancelled = asyncio.Event()

        async def handler(_context):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # A non-cooperative handler may swallow task cancellation and
                # delay cleanup.  The durable cancelling state must still win
                # promptly, and its later return must not publish success.
                handler_cancelled.set()
                await allow_handler_cleanup.wait()
                return WorkspaceOperationResult(payload={"status": "completed"})

        registry = WorkspaceOperationHandlerRegistry()
        registry.register("test.operation", handler, recovery_policy="manual")
        runner = WorkspaceOperationRunner(
            session_factory=maker,
            registry=registry,
            max_concurrency=1,
            lease_seconds=300,
            heartbeat_seconds=60,
            poll_seconds=0.02,
        )
        assert await runner.dispatch_once() == 1
        await asyncio.wait_for(started.wait(), timeout=1)

        # This runner is not the module-global runner, so the cancellation
        # request can reach it only through durable database state.
        assert await request_workspace_operation_cancellation(
            operation_id,
            session_factory=maker,
        )
        active = tuple(runner._active.values())
        await asyncio.wait_for(handler_cancelled.wait(), timeout=1)

        async def wait_until_cancelled():
            while True:
                operation = await _operation(maker, operation_id)
                if operation.state == "cancelled":
                    return operation
                await asyncio.sleep(0.01)

        cancelled = await asyncio.wait_for(wait_until_cancelled(), timeout=1)
        assert not all(task.done() for task in active)

        assert handler_cancelled.is_set()
        assert cancelled.state == "cancelled"
        assert cancelled.attempt_token is None
        assert cancelled.lease_expires_at is None
        assert cancelled.completed_at is not None
        events = await _events(maker, operation_id)
        assert [event.event_type for event in events] == [
            "operation.progress",
            "operation.cancelling",
            "operation.cancelled",
        ]
        assert not any(event.event_type == "operation.result" for event in events)
        allow_handler_cleanup.set()
        await asyncio.wait_for(asyncio.gather(*active), timeout=1)
        assert (await _operation(maker, operation_id)).state == "cancelled"
    finally:
        allow_handler_cleanup.set()
        await engine.dispose()


def test_runner_sets_durable_owner_context_for_handler(tmp_path: Path):
    asyncio.run(_runner_sets_durable_owner_context(tmp_path))


async def _runner_sets_durable_owner_context(tmp_path: Path):
    engine, maker = await _database(tmp_path / "owner.db")
    try:
        _, operation_ids = await _seed_operations(
            maker,
            owner_id=OWNER_ID,
            revision=3,
            expected_revision=3,
        )
        seen_owners: list[uuid.UUID | None] = []

        async def handler(_context):
            seen_owners.append(get_current_owner_id())
            return WorkspaceOperationResult(
                payload={"status": "completed"}, result_revision=4
            )

        registry = WorkspaceOperationHandlerRegistry()
        registry.register("test.operation", handler)
        runner = WorkspaceOperationRunner(
            session_factory=maker,
            registry=registry,
            max_concurrency=1,
            heartbeat_seconds=60,
        )
        assert await _drain_once(runner) == 1
        assert seen_owners == [OWNER_ID]
        assert get_current_owner_id() is None
        operation = await _operation(maker, operation_ids[0])
        assert operation.state == "succeeded"
        assert operation.owner_id == OWNER_ID
    finally:
        await engine.dispose()


def test_runner_rejects_stale_revision_immediately_before_handler(tmp_path: Path):
    asyncio.run(_runner_rejects_stale_revision(tmp_path))


async def _runner_rejects_stale_revision(tmp_path: Path):
    engine, maker = await _database(tmp_path / "revision-conflict.db")
    try:
        _, operation_ids = await _seed_operations(
            maker, revision=9, expected_revision=8
        )
        called = False

        async def handler(_context):
            nonlocal called
            called = True
            return WorkspaceOperationResult()

        registry = WorkspaceOperationHandlerRegistry()
        registry.register("test.operation", handler)
        runner = WorkspaceOperationRunner(
            session_factory=maker,
            registry=registry,
            max_concurrency=1,
            heartbeat_seconds=60,
        )
        assert await _drain_once(runner) == 1
        assert not called
        operation = await _operation(maker, operation_ids[0])
        assert operation.state == "failed"
        assert operation.error_code == "revision_conflict"
        assert operation.error_detail == {
            "error_code": "revision_conflict",
            "retryable": False,
            "retry_count": 1,
            "max_attempts": 3,
            "revision": 9,
        }
        events = await _events(maker, operation.id)
        assert [event.event_type for event in events] == [
            "operation.progress",
            "operation.error",
        ]
        assert [event.operation_version for event in events] == [1, 2]
        assert operation.version == 2
    finally:
        await engine.dispose()


def test_runner_backfills_result_event_after_publication_terminalizes_operation(
    tmp_path: Path,
):
    asyncio.run(_runner_backfills_result_event_after_publication(tmp_path))


async def _runner_backfills_result_event_after_publication(tmp_path: Path):
    engine, maker = await _database(tmp_path / "publication-result.db")
    try:
        _, operation_ids = await _seed_operations(
            maker, revision=0, expected_revision=0
        )
        publication_claims = []

        async def publishing_handler(context):
            publication_claims.append(context.claim)
            async with maker() as session:
                changed = await session.execute(
                    update(WorkspaceOperation)
                    .where(WorkspaceOperation.id == context.claim.operation_id)
                    .where(
                        WorkspaceOperation.attempt_token
                        == context.claim.attempt_token
                    )
                    .where(WorkspaceOperation.state == "running")
                    .values(
                        state="succeeded",
                        result={"status": "completed"},
                        result_revision=1,
                        attempt_token=None,
                        lease_expires_at=None,
                        completed_at=get_current_utc_datetime(),
                        version=WorkspaceOperation.version + 1,
                    )
                )
                assert changed.rowcount == 1
                await session.commit()
            # Publication owns the final commit, so a fast heartbeat may see
            # the cleared fencing token before the handler has returned.
            await asyncio.sleep(0.05)
            return WorkspaceOperationResult(
                payload={"status": "completed"}, result_revision=1
            )

        registry = WorkspaceOperationHandlerRegistry()
        registry.register("test.operation", publishing_handler)
        runner = WorkspaceOperationRunner(
            session_factory=maker,
            registry=registry,
            max_concurrency=1,
            heartbeat_seconds=0.01,
        )
        assert await _drain_once(runner) == 1
        operation = await _operation(maker, operation_ids[0])
        assert operation.state == "succeeded"
        assert operation.result_revision == 1
        events = await _events(maker, operation.id)
        result_events = [
            event for event in events if event.event_type == "operation.result"
        ]
        assert len(result_events) == 1
        assert result_events[0].payload["result_revision"] == 1
        assert result_events[0].operation_version == operation.version

        # A replay of the runner finalizer is idempotent after publication.
        assert await finalize_workspace_operation_success(
            publication_claims[0],
            WorkspaceOperationResult(
                payload={"status": "completed"}, result_revision=1
            ),
            session_factory=maker,
        )
        assert len(
            [
                event
                for event in await _events(maker, operation.id)
                if event.event_type == "operation.result"
            ]
        ) == 1
    finally:
        await engine.dispose()
