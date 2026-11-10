"""Focused capacity-deferral acceptance coverage for authored operations.

This test deliberately drives the durable workspace runner with the real
presentation-generation operation handler.  The generation claim is stubbed
only at the provider/cluster admission boundary so the test is deterministic
and does not call a model provider or write an artifact.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import uuid

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.public_admission import PublicOperationAdmission
from models.sql.user import User
from models.sql.workspace import (
    WorkspaceActionReceipt,
    WorkspaceConversation,
    WorkspaceOperation,
    WorkspaceOperationEvent,
    WorkspaceOutbox,
)
from api.v1.ppt.endpoints.presentation_generate import (
    PRESENTATION_GENERATE_ROUTER,
)
from services import presentation_generation_operation_handler as generation_handler
from services.presentation_generation_operation_handler import (
    PresentationGenerationOperationHandler,
)
from services.workspace_operation_handler_registry import (
    WorkspaceOperationHandlerRegistry,
    WorkspaceOperationDeferred,
    WorkspaceOperationResult,
)
from services.workspace_operation_runner import (
    WorkspaceOperationRunner,
    request_workspace_operation_cancellation,
)
from utils.owner_context import reset_current_owner_id, set_current_owner_id


OWNER_IDS = tuple(
    uuid.UUID(f"{index:08d}-0000-4000-8000-000000000000")
    for index in range(1, 5)
)
GENERATION_TABLES = [
    User.__table__,
    PresentationModel.__table__,
    WorkspaceConversation.__table__,
    WorkspaceOperation.__table__,
    WorkspaceOperationEvent.__table__,
    WorkspaceOutbox.__table__,
    WorkspaceActionReceipt.__table__,
    AsyncPresentationGenerationTaskModel.__table__,
    PresentationGenerationJob.__table__,
    PublicOperationAdmission.__table__,
]


async def _database(path: Path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{path}", connect_args={"timeout": 30.0}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_safety(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")

    async with engine.begin() as connection:
        await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection, tables=GENERATION_TABLES
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_operations(maker):
    seeded: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]] = []
    base_created_at = datetime.now(timezone.utc)
    for index, owner_id in enumerate(OWNER_IDS):
        owner_token = set_current_owner_id(owner_id)
        try:
            async with maker() as session, session.begin():
                user = User(
                    id=owner_id,
                    username=f"capacity-owner-{index}",
                    hashed_password="test-only",
                )
                presentation = PresentationModel(
                    content=f"capacity acceptance {index}",
                    n_slides=1,
                    language="en",
                    layout={"name": "test"},
                    mode="svg",
                    theme={"mode": "svg"},
                    revision=0,
                )
                session.add_all((user, presentation))
                await session.flush()
                conversation = WorkspaceConversation(
                    presentation_id=presentation.id,
                    next_position=2,
                )
                session.add(conversation)
                await session.flush()
                operation_id = uuid.uuid4()
                job_id = uuid.uuid4()
                operation = WorkspaceOperation(
                    id=operation_id,
                    conversation_id=conversation.id,
                    presentation_id=presentation.id,
                    anchor_position=1,
                    kind="design.apply",
                    idempotency_key_hash=f"{index + 1:064x}",
                    request_hash=f"{index + 11:064x}",
                    request_payload={
                        "strategy": "authored",
                        "template": "authored",
                        "job_id": str(job_id),
                        "owner": str(owner_id),
                    },
                    expected_revision=0,
                    created_at=base_created_at + timedelta(milliseconds=index),
                    next_attempt_at=(
                        base_created_at + timedelta(minutes=1) if index == 3 else None
                    ),
                )
                task = AsyncPresentationGenerationTaskModel(
                    status="pending", message="Queued for generation"
                )
                session.add_all((operation, task))
                await session.flush()
                job = PresentationGenerationJob(
                    id=job_id,
                    task_id=task.id,
                    presentation_id=presentation.id,
                    workspace_operation_id=operation.id,
                    request_sha256=f"{index + 21:064x}",
                    request_payload={"strategy": "authored", "template": "authored"},
                    export_cookie_required=False,
                )
                session.add(job)
                seeded.append((owner_id, presentation.id, operation.id, job.id))
        finally:
            reset_current_owner_id(owner_token)
    return seeded


def test_authored_capacity_deferral_is_fair_durable_and_cancel_safe(
    tmp_path: Path, monkeypatch
):
    generate_async_route = next(
        route
        for route in PRESENTATION_GENERATE_ROUTER.routes
        if getattr(route, "path", None) == "/presentation/generate/async"
    )
    assert generate_async_route.status_code == 202
    assert "operation_id" in generate_async_route.response_model.model_fields
    asyncio.run(_capacity_acceptance(tmp_path, monkeypatch))


async def _capacity_acceptance(tmp_path: Path, monkeypatch):
    engine, maker = await _database(tmp_path / "capacity-deferral.db")
    seeded = await _seed_operations(maker)
    job_by_operation = {row[2]: row[3] for row in seeded}
    first_operation = seeded[0][2]
    cancelled_operation = seeded[3][2]
    first_started = asyncio.Event()
    allow_first = asyncio.Event()
    capacity_lock = asyncio.Lock()
    active_job: uuid.UUID | None = None
    claim_order: list[uuid.UUID] = []

    async def claim_linked(operation_id, _attempt_token, _attempt_number):
        nonlocal active_job
        async with capacity_lock:
            if active_job is not None or (
                not claim_order and operation_id != first_operation
            ):
                raise WorkspaceOperationDeferred(
                    "authored_cluster_capacity_unavailable",
                    retry_after_seconds=2.0,
                    detail={"queue_position": 2},
                )
            job_id = job_by_operation[operation_id]
            async with maker() as session:
                job = await session.get(PresentationGenerationJob, job_id)
                assert job is not None and job.state == "running"
                assert job.attempt_token == str(_attempt_token)
                active_job = job_id
        claim_order.append(operation_id)
        return str(job_id), str(job.task_id)

    async def pipeline(job_id, _task_id, _cookie, _context):
        nonlocal active_job
        if uuid.UUID(str(job_id)) == job_by_operation[first_operation]:
            first_started.set()
            await allow_first.wait()
        active_job = None
        return WorkspaceOperationResult(
            payload={"status": "completed", "job_id": str(job_id)}
        )

    monkeypatch.setattr(
        generation_handler,
        "claim_workspace_linked_generation_job",
        claim_linked,
    )
    handler = PresentationGenerationOperationHandler(
        session_factory=maker,
        pipeline=pipeline,
        internal_header_provider=lambda: _headers(),
        heartbeat_seconds=60,
    )

    async def linked_job_exists(_operation_id):
        # Admission seeded every operation/job pair atomically above. Avoid a
        # redundant SQLite read in four concurrent handler tasks so this test
        # measures scheduler capacity behavior instead of connection queuing.
        return SimpleNamespace(request_payload={})

    monkeypatch.setattr(handler, "_linked_job", linked_job_exists)
    registry = WorkspaceOperationHandlerRegistry()
    registry.register(
        "design.apply",
        handler,
    )
    runner = WorkspaceOperationRunner(
        session_factory=maker,
        registry=registry,
        # Match the product default: one slot can run while the second runner
        # task durably defers, freeing itself for the next owner.
        max_concurrency=2,
        poll_seconds=0.01,
        heartbeat_seconds=60,
    )
    try:
        await runner.start()
        await asyncio.wait_for(first_started.wait(), timeout=5)
        # Wait until all non-owner-A claims have durably returned to queued;
        # this avoids racing cancellation against the handler's admission
        # check and exercises the direct queued-cancel path.
        for _ in range(200):
            async with maker() as session:
                waiting_rows = {
                    row.id: row
                    for row in (
                        await session.execute(
                            select(WorkspaceOperation).where(
                                WorkspaceOperation.id.in_(
                                    [seeded[index][2] for index in range(1, 4)]
                                )
                            )
                        )
                    ).scalars()
                }
                if len(waiting_rows) == 3 and all(
                    row.state == "queued" for row in waiting_rows.values()
                ) and all(
                    isinstance(waiting_rows[seeded[index][2]].progress, dict)
                    and waiting_rows[seeded[index][2]].progress.get("stage")
                    == "waiting_for_capacity"
                    for index in (1, 2)
                ):
                    break
            await asyncio.sleep(0.01)
        assert len(waiting_rows) == 3 and all(
            row.state == "queued" for row in waiting_rows.values()
        ) and all(
            isinstance(waiting_rows[seeded[index][2]].progress, dict)
            and waiting_rows[seeded[index][2]].progress.get("stage")
            == "waiting_for_capacity"
            for index in (1, 2)
        ), {
            str(key): (row.state, row.progress, row.attempt_number, row.error_code,
                       row.next_attempt_at)
            for key, row in waiting_rows.items()
        }

        # Cancel the queued fourth owner while the first holds the sole slot.
        cancel_token = set_current_owner_id(OWNER_IDS[3])
        try:
            requested = await request_workspace_operation_cancellation(
                cancelled_operation, session_factory=maker
            )
            if not requested:
                async with maker() as session:
                    row = await session.get(WorkspaceOperation, cancelled_operation)
                    job = await session.get(PresentationGenerationJob, job_by_operation[cancelled_operation])
                assert requested, (row.state, row.owner_scope, job.state)
        finally:
            reset_current_owner_id(cancel_token)

        async with maker() as session:
            rows = (
                await session.execute(
                    select(WorkspaceOperation).order_by(WorkspaceOperation.created_at)
                )
            ).scalars().all()
            by_owner = {row.owner_id: row for row in rows}
            running = by_owner[OWNER_IDS[0]]
            assert running.state == "running"
            assert running.attempt_number == 1
            assert running.retry_count == 0
            assert by_owner[OWNER_IDS[1]].state == "queued"
            assert by_owner[OWNER_IDS[2]].state == "queued"
            for owner_id in OWNER_IDS[1:3]:
                waiting = by_owner[owner_id]
                assert waiting.attempt_number == 0
                assert waiting.retry_count == 0
                assert waiting.error_code is None
                assert waiting.next_attempt_at is not None
                assert waiting.progress is not None
                assert waiting.progress["stage"] == "waiting_for_capacity"
                assert waiting.progress["wait_reason"] == (
                    "authored_cluster_capacity_unavailable"
                )
                assert waiting.progress["capacity_waiting"] is True
                if "queue_position" in waiting.progress:
                    assert waiting.progress["queue_position"] >= 2
                waiting_job = await session.get(
                    PresentationGenerationJob, job_by_operation[waiting.id]
                )
                assert waiting_job is not None
                assert waiting_job.state == "queued"
                assert waiting_job.attempt_number == 0
                assert waiting_job.attempt_token is None
                assert waiting_job.lease_expires_at is None
            assert by_owner[OWNER_IDS[3]].state == "cancelled"
            assert by_owner[OWNER_IDS[3]].retry_count == 0
            assert all(row.state != "dead_letter" for row in rows)

            waiting_events = (
                await session.execute(
                    select(WorkspaceOperationEvent).where(
                        WorkspaceOperationEvent.operation_id.in_(
                            [by_owner[OWNER_IDS[1]].id, by_owner[OWNER_IDS[2]].id]
                        )
                    )
                )
            ).scalars().all()
            assert all(event.event_type != "operation.error" for event in waiting_events)

        allow_first.set()
        states: dict[uuid.UUID, str] = {}
        for _ in range(200):
            async with maker() as session:
                states = {
                    row.owner_id: row.state
                    for row in (
                        await session.execute(select(WorkspaceOperation))
                    ).scalars()
                }
            if all(states[owner] in {"succeeded", "cancelled"} for owner in OWNER_IDS):
                break
            await asyncio.sleep(0.05)

        assert claim_order[0] == seeded[0][2]
        assert set(claim_order[1:]) == {seeded[1][2], seeded[2][2]}
        assert len(claim_order) == 3
        async with maker() as session:
            final_rows = (
                await session.execute(select(WorkspaceOperation))
            ).scalars().all()
            assert {row.owner_id: row.state for row in final_rows} == {
                OWNER_IDS[0]: "succeeded",
                OWNER_IDS[1]: "succeeded",
                OWNER_IDS[2]: "succeeded",
                OWNER_IDS[3]: "cancelled",
            }
            assert all(row.retry_count == 0 for row in final_rows)
            assert all(row.error_code is None for row in final_rows)
            jobs = (
                await session.execute(select(PresentationGenerationJob))
            ).scalars().all()
            # The compatibility projection exposes a cancelled operation as
            # a legacy failed task while the workspace operation is
            # canonically ``cancelled``.
            assert all(job.state in {"published", "queued", "failed"} for job in jobs)
            assert all(job.attempt_number <= 1 for job in jobs)
    finally:
        await runner.stop()
        await engine.dispose()


async def _headers():
    return {}
