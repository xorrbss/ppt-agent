from __future__ import annotations

import asyncio
from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.generate_presentation_request import GeneratePresentationRequest
from models.sql.presentation import PresentationModel
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.user import User
from models.sql.workspace import (
    WorkspaceActionReceipt,
    WorkspaceConversation,
    WorkspaceOperation,
    WorkspaceOperationEvent,
    WorkspaceOutbox,
)
from models.workspace import WorkspaceOperationActionRequest
from services.authored_composition_policy import (
    AUTHORED_CAPACITY_BOUNDED_REPAIR_REVISION,
)
from services import presentation_generation_job_service as jobs
from services.presentation_generation_operation_handler import (
    PRESENTATION_GENERATION_OPERATION_KIND,
    PresentationGenerationOperationHandler,
)
from services.workspace_operation_handler_registry import (
    WorkspaceOperationCancelled,
    WorkspaceOperationClaim,
    WorkspaceOperationHandlerRegistry,
    WorkspaceOperationResult,
)
from services.workspace_operation_runner import (
    WorkspaceOperationRunner,
    recover_expired_workspace_operations,
    request_workspace_operation_cancellation,
)
from services.workspace_service import WorkspaceServiceError, apply_workspace_action
from utils.owner_context import (
    get_current_owner_id,
    reset_current_owner_id,
    set_current_owner_id,
)


OWNER_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
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
]


async def _database(path: Path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_safety(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection, tables=GENERATION_TABLES
            )
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(jobs, "async_session_maker", maker)
    return engine, maker


async def _seed_linked_generation(maker, *, owner_id: uuid.UUID | None):
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
                content="generation handler test",
                n_slides=1,
                language="en",
                layout={"name": "test"},
                theme={"mode": "authored"},
                revision=0,
            )
            session.add(presentation)
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
                kind=PRESENTATION_GENERATION_OPERATION_KIND,
                idempotency_key_hash="1" * 64,
                request_hash="2" * 64,
                request_payload={
                    "strategy": "template",
                    "job_id": str(job_id),
                },
                expected_revision=0,
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
                request_sha256="3" * 64,
                request_payload={"strategy": "template"},
                export_cookie_required=False,
            )
            session.add(job)
            await session.flush()
            return presentation.id, operation.id, task.id, job.id
    finally:
        reset_current_owner_id(owner_token)


async def _drain_once(runner: WorkspaceOperationRunner) -> int:
    dispatched = await runner.dispatch_once()
    tasks = tuple(runner._active.values())
    if tasks:
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)
    return dispatched


@pytest.mark.parametrize(
    ("owner_id", "headers", "expected_cookie"),
    [
        (OWNER_ID, {"Cookie": "session=short-lived-secret"}, "session=short-lived-secret"),
        (None, {}, None),
    ],
)
def test_generation_handler_mints_cookie_in_owner_context_without_persisting_it(
    tmp_path: Path,
    monkeypatch,
    owner_id,
    headers,
    expected_cookie,
):
    asyncio.run(
        _generation_handler_uses_ephemeral_cookie(
            tmp_path,
            monkeypatch,
            owner_id=owner_id,
            headers=headers,
            expected_cookie=expected_cookie,
        )
    )


async def _generation_handler_uses_ephemeral_cookie(
    tmp_path: Path,
    monkeypatch,
    *,
    owner_id: uuid.UUID | None,
    headers: dict[str, str],
    expected_cookie: str | None,
):
    engine, maker = await _database(tmp_path / "generation-handler.db", monkeypatch)
    try:
        _, operation_id, task_id, job_id = await _seed_linked_generation(
            maker, owner_id=owner_id
        )
        observed_owner_ids: list[uuid.UUID | None] = []
        observed_cookies: list[str | None] = []

        async def internal_header_provider():
            observed_owner_ids.append(get_current_owner_id())
            return headers

        async def pipeline(
            linked_job_id,
            linked_task_id,
            cookie_header,
            _context,
        ):
            assert linked_job_id == job_id
            assert linked_task_id == task_id
            observed_cookies.append(cookie_header)
            async with maker() as session:
                status = await session.get(
                    AsyncPresentationGenerationTaskModel, task_id
                )
                assert status is not None
                status.status = "completed"
                session.add(status)
                await session.commit()
            return WorkspaceOperationResult(
                payload={"artifact_id": "artifact-safe"}, result_revision=0
            )

        handler = PresentationGenerationOperationHandler(
            session_factory=maker,
            pipeline=pipeline,
            internal_header_provider=internal_header_provider,
            heartbeat_seconds=60,
        )
        registry = WorkspaceOperationHandlerRegistry()
        registry.register(PRESENTATION_GENERATION_OPERATION_KIND, handler)
        runner = WorkspaceOperationRunner(
            session_factory=maker,
            registry=registry,
            max_concurrency=1,
            heartbeat_seconds=60,
        )

        # The legacy dispatcher must not claim a workspace-linked projection.
        assert (
            await jobs.claim_generation_job(job_id, allow_auth_resume=False)
            is None
        )
        assert await _drain_once(runner) == 1
        assert observed_owner_ids == [owner_id]
        assert observed_cookies == [expected_cookie]
        assert get_current_owner_id() is None

        async with maker() as session:
            operation = (
                await session.execute(
                    select(WorkspaceOperation)
                    .where(WorkspaceOperation.id == operation_id)
                    .execution_options(skip_owner_scope=True)
                )
            ).scalar_one()
            events = list(
                (
                    await session.execute(
                        select(WorkspaceOperationEvent)
                        .where(
                            WorkspaceOperationEvent.operation_id
                            == operation_id
                        )
                        .execution_options(skip_owner_scope=True)
                    )
                )
                .scalars()
                .all()
            )
            outbox = list(
                (
                    await session.execute(
                        select(WorkspaceOutbox)
                        .where(WorkspaceOutbox.operation_id == operation_id)
                        .execution_options(skip_owner_scope=True)
                    )
                )
                .scalars()
                .all()
            )
            job = await session.get(PresentationGenerationJob, job_id)
        assert operation.state == "succeeded"
        assert job is not None and job.state == "published"
        durable_json = json.dumps(
            {
                "operation_request": operation.request_payload,
                "operation_result": operation.result,
                "job_request": job.request_payload,
                "events": [event.payload for event in events],
                "outbox": [row.payload for row in outbox],
            },
            sort_keys=True,
        )
        assert "Cookie" not in durable_json
        assert "short-lived-secret" not in durable_json
    finally:
        await engine.dispose()


def test_generation_handler_terminalizes_linked_job_when_cancelled(
    tmp_path: Path, monkeypatch
):
    asyncio.run(_generation_handler_terminalizes_cancelled_job(tmp_path, monkeypatch))


async def _generation_handler_terminalizes_cancelled_job(tmp_path: Path, monkeypatch):
    engine, maker = await _database(tmp_path / "generation-cancel.db", monkeypatch)
    try:
        _, operation_id, task_id, job_id = await _seed_linked_generation(
            maker, owner_id=None
        )

        async def internal_header_provider():
            return {}

        async def cancelled_pipeline(*_args):
            raise WorkspaceOperationCancelled("workspace_operation_cancelled")

        handler = PresentationGenerationOperationHandler(
            session_factory=maker,
            pipeline=cancelled_pipeline,
            internal_header_provider=internal_header_provider,
            heartbeat_seconds=60,
        )
        registry = WorkspaceOperationHandlerRegistry()
        registry.register(PRESENTATION_GENERATION_OPERATION_KIND, handler)
        runner = WorkspaceOperationRunner(
            session_factory=maker,
            registry=registry,
            max_concurrency=1,
            heartbeat_seconds=60,
        )

        assert await _drain_once(runner) == 1
        async with maker() as session:
            operation = await session.get(WorkspaceOperation, operation_id)
            job = await session.get(PresentationGenerationJob, job_id)
            task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
        assert operation is not None and operation.state == "cancelled"
        assert job is not None and job.state == "failed"
        assert job.resume_allowed is False
        assert job.last_error == {"code": "generation_cancelled"}
        assert job.attempt_token is None
        assert job.lease_expires_at is None
        assert task is not None and task.status == "error"
        assert task.error is not None
        assert task.error["status_code"] == 499
        assert task.error["code"] == "generation_cancelled"
        assert task.error["detail"] == "Generation cancelled by user"
    finally:
        await engine.dispose()


def test_non_retryable_generation_failure_disables_resume(tmp_path: Path, monkeypatch):
    asyncio.run(_non_retryable_generation_failure_disables_resume(tmp_path, monkeypatch))


async def _non_retryable_generation_failure_disables_resume(
    tmp_path: Path, monkeypatch
):
    engine, maker = await _database(tmp_path / "generation-non-retryable.db", monkeypatch)
    try:
        _, operation_id, task_id, job_id = await _seed_linked_generation(
            maker, owner_id=None
        )

        async def internal_header_provider():
            return {}

        async def non_retryable_pipeline(*_args):
            error = RuntimeError("authored_slide_capacity_exceeded")
            error.code = "authored_slide_capacity_exceeded"
            error.retryable = False
            raise error

        handler = PresentationGenerationOperationHandler(
            session_factory=maker,
            pipeline=non_retryable_pipeline,
            internal_header_provider=internal_header_provider,
            heartbeat_seconds=60,
        )
        registry = WorkspaceOperationHandlerRegistry()
        registry.register(PRESENTATION_GENERATION_OPERATION_KIND, handler)
        runner = WorkspaceOperationRunner(
            session_factory=maker,
            registry=registry,
            max_concurrency=1,
            heartbeat_seconds=60,
        )

        assert await _drain_once(runner) == 1
        async with maker() as session:
            operation = await session.get(WorkspaceOperation, operation_id)
            job = await session.get(PresentationGenerationJob, job_id)
            task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
        assert operation is not None and operation.state == "failed"
        assert operation.error_detail is not None
        assert operation.error_detail["retryable"] is False
        assert job is not None and job.state == "failed"
        assert job.resume_allowed is False
        assert task is not None and task.status == "error"
        assert task.error is not None
        assert task.error["code"] == "authored_slide_capacity_exceeded"
    finally:
        await engine.dispose()


def test_queued_generation_cancel_atomically_terminalizes_linked_status(
    tmp_path: Path, monkeypatch
):
    asyncio.run(_queued_generation_cancel_terminalizes_linked_status(tmp_path, monkeypatch))


async def _queued_generation_cancel_terminalizes_linked_status(
    tmp_path: Path, monkeypatch
):
    engine, maker = await _database(tmp_path / "generation-queued-cancel.db", monkeypatch)
    try:
        _, operation_id, task_id, job_id = await _seed_linked_generation(
            maker, owner_id=None
        )

        assert await request_workspace_operation_cancellation(
            operation_id,
            session_factory=maker,
        )
        async with maker() as session:
            operation = await session.get(WorkspaceOperation, operation_id)
            job = await session.get(PresentationGenerationJob, job_id)
            task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
        assert operation is not None and operation.state == "cancelled"
        assert job is not None and job.state == "failed"
        assert job.resume_allowed is False
        assert job.attempt_token is None
        assert task is not None and task.status == "error"
        assert task.error is not None and task.error["status_code"] == 499
        assert task.error["code"] == "generation_cancelled"
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("cancel_requested", "expected_operation_state", "expected_job_state"),
    [
        (False, "queued", "queued"),
        (True, "cancelled", "failed"),
    ],
)
def test_expired_generation_lease_projects_linked_status_atomically(
    tmp_path: Path,
    monkeypatch,
    cancel_requested: bool,
    expected_operation_state: str,
    expected_job_state: str,
):
    asyncio.run(
        _expired_generation_lease_projects_linked_status(
            tmp_path,
            monkeypatch,
            cancel_requested=cancel_requested,
            expected_operation_state=expected_operation_state,
            expected_job_state=expected_job_state,
        )
    )


async def _expired_generation_lease_projects_linked_status(
    tmp_path: Path,
    monkeypatch,
    *,
    cancel_requested: bool,
    expected_operation_state: str,
    expected_job_state: str,
):
    engine, maker = await _database(
        tmp_path / f"generation-expired-{cancel_requested}.db",
        monkeypatch,
    )
    try:
        _, operation_id, task_id, job_id = await _seed_linked_generation(
            maker, owner_id=None
        )
        expired = jobs.utc_now() - timedelta(seconds=10)
        async with maker() as session:
            operation = await session.get(WorkspaceOperation, operation_id)
            job = await session.get(PresentationGenerationJob, job_id)
            task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
            assert operation is not None and job is not None and task is not None
            operation.state = "cancelling" if cancel_requested else "running"
            operation.attempt_number = 1
            operation.attempt_token = "expired-linked-attempt"
            operation.fencing_token = 1
            operation.lease_expires_at = expired
            operation.heartbeat_at = expired
            if cancel_requested:
                operation.cancel_requested_at = expired
            job.state = "running"
            job.attempt_number = 1
            job.attempt_token = "expired-linked-attempt"
            job.lease_expires_at = expired
            job.heartbeat_at = expired
            task.status = "running"
            session.add_all((operation, job, task))
            await session.commit()

        assert await recover_expired_workspace_operations(session_factory=maker) == 1
        async with maker() as session:
            operation = await session.get(WorkspaceOperation, operation_id)
            job = await session.get(PresentationGenerationJob, job_id)
            task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
        assert operation is not None and operation.state == expected_operation_state
        assert operation.attempt_token is None
        assert job is not None and job.state == expected_job_state
        assert job.attempt_token is None
        assert job.lease_expires_at is None
        assert task is not None
        if cancel_requested:
            assert task.status == "error"
            assert task.error is not None and task.error["status_code"] == 499
            assert task.error["code"] == "generation_cancelled"
        else:
            assert task.status == "pending"
            assert task.error is None
    finally:
        await engine.dispose()


def test_terminal_retry_rebinds_and_runs_the_linked_generation_job(
    tmp_path: Path,
    monkeypatch,
):
    asyncio.run(_terminal_retry_rebinds_linked_job(tmp_path, monkeypatch))


async def _terminal_retry_rebinds_linked_job(tmp_path: Path, monkeypatch):
    engine, maker = await _database(tmp_path / "generation-retry.db", monkeypatch)
    try:
        _, source_operation_id, task_id, job_id = await _seed_linked_generation(
            maker,
            owner_id=None,
        )
        async with maker() as session:
            source = await session.get(WorkspaceOperation, source_operation_id)
            job = await session.get(PresentationGenerationJob, job_id)
            task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
            assert source is not None and job is not None and task is not None
            source.state = "failed"
            source.version = 4
            source.error_code = "generation_failed"
            job.state = "failed"
            job.resume_allowed = False
            job.last_error = {"code": "generation_failed"}
            task.status = "error"
            task.error = {"code": "generation_failed"}
            session.add_all((source, job, task))
            await session.commit()

        async with maker() as session:
            receipt, retry, duplicate = await apply_workspace_action(
                session,
                operation_id=source_operation_id,
                request=WorkspaceOperationActionRequest(
                    action="retry",
                    idempotency_key="retry-linked-generation",
                    expected_operation_version=4,
                    expected_revision=0,
                ),
            )
        assert duplicate is False
        assert receipt.result_operation_id == retry.id
        assert retry.retry_of_operation_id == source_operation_id

        async with maker() as session:
            rebound_job = await session.get(PresentationGenerationJob, job_id)
            rebound_task = await session.get(
                AsyncPresentationGenerationTaskModel,
                task_id,
            )
        assert rebound_job is not None
        assert rebound_job.workspace_operation_id == retry.id
        assert rebound_job.state == "queued"
        assert rebound_job.resume_allowed is False
        assert rebound_job.resume_count == 1
        assert rebound_job.last_error is None
        assert rebound_task is not None and rebound_task.status == "pending"
        assert rebound_task.error is None

        async def successful_retry_pipeline(*_args):
            async with maker() as session:
                status = await session.get(
                    AsyncPresentationGenerationTaskModel,
                    task_id,
                )
                assert status is not None
                status.status = "completed"
                status.error = None
                session.add(status)
                await session.commit()
            return WorkspaceOperationResult(
                payload={"artifact_id": "retried-artifact"},
                result_revision=0,
            )

        async def internal_header_provider():
            return {}

        handler = PresentationGenerationOperationHandler(
            session_factory=maker,
            pipeline=successful_retry_pipeline,
            internal_header_provider=internal_header_provider,
            heartbeat_seconds=60,
        )
        registry = WorkspaceOperationHandlerRegistry()
        registry.register(PRESENTATION_GENERATION_OPERATION_KIND, handler)
        runner = WorkspaceOperationRunner(
            session_factory=maker,
            registry=registry,
            max_concurrency=1,
            heartbeat_seconds=60,
        )
        assert await _drain_once(runner) == 1

        async with maker() as session:
            source = await session.get(WorkspaceOperation, source_operation_id)
            completed_retry = await session.get(WorkspaceOperation, retry.id)
            completed_job = await session.get(PresentationGenerationJob, job_id)
        assert source is not None and source.state == "failed"
        assert completed_retry is not None and completed_retry.state == "succeeded"
        assert completed_job is not None and completed_job.state == "published"
    finally:
        await engine.dispose()


def test_explicit_capacity_recovery_rebinds_only_supported_authored_revision(
    tmp_path: Path,
    monkeypatch,
):
    asyncio.run(
        _explicit_capacity_recovery_rebinds_only_supported_authored_revision(
            tmp_path,
            monkeypatch,
        )
    )


async def _explicit_capacity_recovery_rebinds_only_supported_authored_revision(
    tmp_path: Path,
    monkeypatch,
):
    engine, maker = await _database(
        tmp_path / "generation-capacity-recovery.db",
        monkeypatch,
    )
    try:
        _, source_operation_id, task_id, job_id = await _seed_linked_generation(
            maker,
            owner_id=None,
        )
        async with maker() as session:
            source = await session.get(WorkspaceOperation, source_operation_id)
            job = await session.get(PresentationGenerationJob, job_id)
            task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
            assert source is not None and job is not None and task is not None
            generation_request = {"strategy": "authored", "content": "test deck"}
            source.state = "failed"
            source.version = 4
            source.error_code = "authored_slide_capacity_exceeded"
            source.error_detail = {"retryable": False}
            source.request_payload = {
                "generation_request": generation_request,
                "job_id": str(job.id),
            }
            job.state = "failed"
            job.request_payload = generation_request
            job.resume_allowed = False
            job.last_error = {"code": "authored_slide_capacity_exceeded"}
            task.status = "error"
            task.error = {"code": "authored_slide_capacity_exceeded"}
            session.add_all((source, job, task))
            await session.commit()

        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as generic_retry:
                await apply_workspace_action(
                    session,
                    operation_id=source_operation_id,
                    request=WorkspaceOperationActionRequest(
                        action="retry",
                        idempotency_key="generic-capacity-retry",
                        expected_operation_version=4,
                        expected_revision=0,
                    ),
                )
        assert generic_retry.value.code == "operation_not_retryable"

        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as stale_revision:
                await apply_workspace_action(
                    session,
                    operation_id=source_operation_id,
                    request=WorkspaceOperationActionRequest(
                        action="retry",
                        idempotency_key="stale-capacity-recovery",
                        expected_operation_version=4,
                        expected_revision=0,
                        recovery_revision="authored-capacity-bounded-repair/v0",
                    ),
                )
        assert stale_revision.value.code == "operation_not_retryable"

        async with maker() as session:
            receipt, recovery, duplicate = await apply_workspace_action(
                session,
                operation_id=source_operation_id,
                request=WorkspaceOperationActionRequest(
                    action="retry",
                    idempotency_key="bounded-capacity-recovery",
                    expected_operation_version=4,
                    expected_revision=0,
                    recovery_revision=AUTHORED_CAPACITY_BOUNDED_REPAIR_REVISION,
                ),
            )
        assert duplicate is False
        assert receipt.result_operation_id == recovery.id
        assert recovery.retry_of_operation_id == source_operation_id
        assert recovery.request_payload["recovery_revision"] == (
            AUTHORED_CAPACITY_BOUNDED_REPAIR_REVISION
        )

        async with maker() as session:
            source = await session.get(WorkspaceOperation, source_operation_id)
            rebound_job = await session.get(PresentationGenerationJob, job_id)
            rebound_task = await session.get(
                AsyncPresentationGenerationTaskModel,
                task_id,
            )
        assert source is not None
        assert source.state == "failed"
        assert source.error_detail == {"retryable": False}
        assert rebound_job is not None
        assert rebound_job.workspace_operation_id == recovery.id
        assert rebound_job.state == "queued"
        assert rebound_job.resume_allowed is False
        assert rebound_job.resume_count == 1
        assert rebound_task is not None and rebound_task.status == "pending"
        assert rebound_task.error is None
    finally:
        await engine.dispose()


def test_runner_shutdown_requeues_linked_generation_without_terminal_failure(
    tmp_path: Path,
    monkeypatch,
):
    asyncio.run(_runner_shutdown_requeues_linked_generation(tmp_path, monkeypatch))


async def _runner_shutdown_requeues_linked_generation(tmp_path: Path, monkeypatch):
    engine, maker = await _database(tmp_path / "generation-shutdown.db", monkeypatch)
    try:
        _, operation_id, task_id, job_id = await _seed_linked_generation(
            maker,
            owner_id=None,
        )
        started = asyncio.Event()
        never_complete = asyncio.Event()

        async def internal_header_provider():
            return {}

        async def blocking_pipeline(*_args):
            started.set()
            await never_complete.wait()
            raise AssertionError("unreachable")

        handler = PresentationGenerationOperationHandler(
            session_factory=maker,
            pipeline=blocking_pipeline,
            internal_header_provider=internal_header_provider,
            heartbeat_seconds=60,
        )
        registry = WorkspaceOperationHandlerRegistry()
        registry.register(PRESENTATION_GENERATION_OPERATION_KIND, handler)
        runner = WorkspaceOperationRunner(
            session_factory=maker,
            registry=registry,
            max_concurrency=1,
            heartbeat_seconds=60,
        )
        assert await runner.dispatch_once() == 1
        await asyncio.wait_for(started.wait(), timeout=5)
        await runner.stop()

        async with maker() as session:
            operation = await session.get(WorkspaceOperation, operation_id)
            job = await session.get(PresentationGenerationJob, job_id)
            task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
        assert operation is not None and operation.state == "queued"
        assert operation.cancel_requested_at is None
        assert operation.attempt_token is None
        assert operation.lease_expires_at is None
        assert job is not None and job.state == "queued"
        assert job.attempt_token is None
        assert job.lease_expires_at is None
        assert task is not None and task.status == "pending"
        assert task.error is None
    finally:
        await engine.dispose()


class _PipelineContext:
    def __init__(self, claim: WorkspaceOperationClaim) -> None:
        self.claim = claim

    async def progress(self, **_kwargs) -> bool:
        return True

    async def cancellation_requested(self) -> bool:
        return False

    async def raise_if_cancelled(self) -> None:
        return None


def test_authored_master_admission_allows_unpinned_full_canvas():
    from api.v1.ppt.endpoints import presentation_generate as endpoint

    request = GeneratePresentationRequest(
        content="Masterless authored deck",
        strategy="authored",
        template="authored",
        authored_style="default",
    )

    asyncio.run(endpoint._validate_authored_master_pin_admission(request))

    assert request.master_pin is None
    assert request._authored_master_pin is None
    assert request._authored_master_resolution is None
    assert request._authored_composition_pin is None


def test_default_pipeline_merges_published_artifact_metadata(
    tmp_path: Path, monkeypatch
):
    asyncio.run(_default_pipeline_merges_publication_result(tmp_path, monkeypatch))


async def _default_pipeline_merges_publication_result(tmp_path: Path, monkeypatch):
    engine, maker = await _database(tmp_path / "publication-result.db", monkeypatch)
    try:
        presentation_id, operation_id, task_id, job_id = (
            await _seed_linked_generation(maker, owner_id=OWNER_ID)
        )
        from api.v1.ppt.endpoints import presentation_generate as endpoint

        monkeypatch.setattr(
            endpoint,
            "_request_from_durable_payload",
            lambda _payload: SimpleNamespace(),
        )
        monkeypatch.setattr(
            endpoint, "_validate_authored_exact_admission", lambda _request: None
        )

        async def preserve_unpinned_request(_request):
            return None

        monkeypatch.setattr(
            endpoint,
            "_validate_authored_master_pin_admission",
            preserve_unpinned_request,
        )

        async def fake_generation_handler(
            _request,
            _presentation_id,
            *,
            async_status,
            export_cookie_header,
            generation_job,
            progress_reporter,
            sql_session,
        ):
            assert export_cookie_header is None
            assert generation_job.id == job_id
            assert progress_reporter == context.progress
            async_status.status = "completed"
            presentation = await sql_session.get(
                PresentationModel, presentation_id
            )
            operation = await sql_session.get(WorkspaceOperation, operation_id)
            assert presentation is not None and operation is not None
            presentation.revision = 1
            operation.result_revision = 1
            operation.result = {
                "artifact_id": "immutable-artifact-id",
                "status": "completed",
            }
            sql_session.add_all((async_status, presentation, operation))
            await sql_session.flush()

        monkeypatch.setattr(
            endpoint, "generate_presentation_handler", fake_generation_handler
        )
        claim = WorkspaceOperationClaim(
            operation_id=operation_id,
            owner_id=OWNER_ID,
            owner_scope=str(OWNER_ID),
            conversation_id=uuid.uuid4(),
            presentation_id=presentation_id,
            kind=PRESENTATION_GENERATION_OPERATION_KIND,
            request_payload={},
            expected_revision=0,
            attempt_number=1,
            attempt_token="attempt-token",
            fencing_token=1,
        )
        context = _PipelineContext(claim)
        handler = PresentationGenerationOperationHandler(
            session_factory=maker,
            internal_header_provider=lambda: {},  # not used by this direct call
        )
        owner_token = set_current_owner_id(OWNER_ID)
        try:
            result = await handler._run_existing_pipeline(
                job_id, task_id, None, context
            )
        finally:
            reset_current_owner_id(owner_token)
        assert result.result_revision == 1
        assert result.payload == {
            "status": "completed",
            "task_id": task_id,
            "job_id": str(job_id),
            "artifact_id": "immutable-artifact-id",
        }
    finally:
        await engine.dispose()


def test_default_pipeline_resolves_durable_authored_master_pin_before_generation_handler(
    tmp_path: Path, monkeypatch
):
    asyncio.run(
        _default_pipeline_resolves_durable_authored_master_pin_before_generation_handler(
            tmp_path, monkeypatch
        )
    )


async def _default_pipeline_resolves_durable_authored_master_pin_before_generation_handler(
    tmp_path: Path, monkeypatch
):
    engine, maker = await _database(tmp_path / "durable-master-pin.db", monkeypatch)
    try:
        presentation_id, operation_id, task_id, job_id = (
            await _seed_linked_generation(maker, owner_id=OWNER_ID)
        )
        digest = f"sha256:{'a' * 64}"
        durable_request = GeneratePresentationRequest(
            content="Pinned authored deck",
            strategy="authored",
            template="authored",
            master_pin={
                "masterId": "master:company",
                "revision": 7,
                "digest": digest,
            },
        )
        async with maker() as session, session.begin():
            job = await session.get(PresentationGenerationJob, job_id)
            assert job is not None
            job.request_payload = durable_request.model_dump(mode="json", by_alias=True)
            session.add(job)

        from api.v1.ppt.endpoints import presentation_generate as endpoint

        calls: list[str] = []
        resolved_master = SimpleNamespace(master_id="master:company", revision=7)
        validate_exact = endpoint._validate_authored_exact_admission

        def observed_exact_validation(request):
            calls.append("exact")
            assert request._authored_master_resolution is None
            validate_exact(request)

        async def resolve_master_pin(request):
            calls.append("master")
            assert request.master_pin is not None
            assert request.master_pin.master_id == "master:company"
            assert request.master_pin.revision == 7
            assert request.master_pin.digest == digest
            request._authored_master_resolution = resolved_master

        monkeypatch.setattr(
            endpoint, "_validate_authored_exact_admission", observed_exact_validation
        )
        monkeypatch.setattr(
            endpoint, "_validate_authored_master_pin_admission", resolve_master_pin
        )

        async def fake_generation_handler(
            request,
            _presentation_id,
            *,
            async_status,
            export_cookie_header,
            generation_job,
            progress_reporter,
            sql_session,
        ):
            calls.append("generate")
            assert calls == ["exact", "master", "generate"]
            assert request._authored_master_resolution is resolved_master
            assert export_cookie_header is None
            assert generation_job.id == job_id
            assert progress_reporter == context.progress
            async_status.status = "completed"
            presentation = await sql_session.get(
                PresentationModel, presentation_id
            )
            assert presentation is not None
            presentation.revision = 1
            sql_session.add_all((async_status, presentation))
            await sql_session.flush()

        monkeypatch.setattr(
            endpoint, "generate_presentation_handler", fake_generation_handler
        )
        claim = WorkspaceOperationClaim(
            operation_id=operation_id,
            owner_id=OWNER_ID,
            owner_scope=str(OWNER_ID),
            conversation_id=uuid.uuid4(),
            presentation_id=presentation_id,
            kind=PRESENTATION_GENERATION_OPERATION_KIND,
            request_payload={},
            expected_revision=0,
            attempt_number=1,
            attempt_token="attempt-token",
            fencing_token=1,
        )
        context = _PipelineContext(claim)
        handler = PresentationGenerationOperationHandler(
            session_factory=maker,
            internal_header_provider=lambda: {},  # not used by this direct call
        )
        owner_token = set_current_owner_id(OWNER_ID)
        try:
            result = await handler._run_existing_pipeline(
                job_id, task_id, None, context
            )
        finally:
            reset_current_owner_id(owner_token)

        assert calls == ["exact", "master", "generate"]
        assert result.result_revision == 1
    finally:
        await engine.dispose()
