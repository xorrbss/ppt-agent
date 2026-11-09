import asyncio
from datetime import timedelta
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.workspace import WorkspaceOperation
from services import presentation_generation_job_service as jobs
from utils.owner_context import (
    reset_current_owner_id,
    set_current_owner_id,
)


OWNER_A = uuid.UUID("11111111-1111-4111-8111-111111111111")
OWNER_B = uuid.UUID("22222222-2222-4222-8222-222222222222")


async def _job_database(tmp_path, monkeypatch):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'generation-jobs.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            SQLModel.metadata.create_all,
            tables=[
                AsyncPresentationGenerationTaskModel.__table__,
                PresentationGenerationJob.__table__,
                WorkspaceOperation.__table__,
            ],
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(jobs, "async_session_maker", maker)
    return engine, maker


def test_request_and_idempotency_hashes_are_canonical_and_bounded():
    assert jobs.request_sha256({"b": 2, "a": 1}) == jobs.request_sha256(
        {"a": 1, "b": 2}
    )
    assert jobs.idempotency_key_hash(" retry-key ") == jobs.idempotency_key_hash(
        "retry-key"
    )
    with pytest.raises(ValueError, match="invalid_idempotency_key"):
        jobs.idempotency_key_hash(" ")
    with pytest.raises(ValueError, match="invalid_idempotency_key"):
        jobs.idempotency_key_hash("x" * 256)


def test_idempotency_key_hash_is_scoped_to_the_current_owner():
    owner_a_token = set_current_owner_id(OWNER_A)
    try:
        owner_a_hash = jobs.idempotency_key_hash("retry-key")
    finally:
        reset_current_owner_id(owner_a_token)

    owner_b_token = set_current_owner_id(OWNER_B)
    try:
        owner_b_hash = jobs.idempotency_key_hash("retry-key")
    finally:
        reset_current_owner_id(owner_b_token)

    assert owner_a_hash != owner_b_hash


def test_job_claim_is_atomic_and_success_finalization_is_token_guarded(
    tmp_path, monkeypatch
):
    async def scenario():
        engine, maker = await _job_database(tmp_path, monkeypatch)
        try:
            status = AsyncPresentationGenerationTaskModel(
                status="pending",
                message="Queued for generation",
            )
            job = PresentationGenerationJob(
                task_id=status.id,
                presentation_id=uuid.uuid4(),
                request_id="request-correlation-id",
                request_sha256="a" * 64,
                request_payload={"content": "durable"},
            )
            async with maker() as session:
                session.add(status)
                session.add(job)
                await session.commit()

            claim = await jobs.claim_generation_job(
                job.id,
                allow_auth_resume=False,
            )
            assert claim is not None
            task_id, token = claim
            assert task_id == status.id
            assert (
                await jobs.claim_generation_job(
                    job.id,
                    allow_auth_resume=False,
                )
                is None
            )
            assert not await jobs.finalize_generation_job(
                job.id,
                "wrong-token",
                succeeded=True,
            )
            assert await jobs.finalize_generation_job(
                job.id,
                token,
                succeeded=True,
            )

            async with maker() as session:
                stored_job = await session.get(PresentationGenerationJob, job.id)
                stored_status = await session.get(
                    AsyncPresentationGenerationTaskModel, status.id
                )
            assert stored_job is not None
            assert stored_job.request_id == "request-correlation-id"
            assert stored_job.state == "published"
            assert stored_job.published_at is not None
            assert stored_status is not None
            assert stored_status.status == "running"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_linked_claim_is_operation_projection_and_does_not_consume_legacy_quota(
    tmp_path, monkeypatch
):
    async def scenario():
        engine, maker = await _job_database(tmp_path, monkeypatch)
        try:
            now = jobs.utc_now()
            operation_id = uuid.uuid4()
            attempt_token = "operation-attempt"
            presentation_id = uuid.uuid4()
            status = AsyncPresentationGenerationTaskModel(status="pending")
            linked = PresentationGenerationJob(
                task_id=status.id,
                presentation_id=presentation_id,
                workspace_operation_id=operation_id,
                request_sha256="d" * 64,
                request_payload={"strategy": "authored"},
            )
            operation = WorkspaceOperation(
                id=operation_id,
                conversation_id=uuid.uuid4(),
                presentation_id=presentation_id,
                anchor_position=1,
                kind="presentation.generate",
                state="running",
                idempotency_key_hash="e" * 64,
                request_hash="f" * 64,
                attempt_number=2,
                attempt_token=attempt_token,
                fencing_token=9,
                heartbeat_at=now,
                lease_expires_at=now + timedelta(minutes=5),
                started_at=now,
            )
            legacy_status = AsyncPresentationGenerationTaskModel(status="pending")
            legacy = PresentationGenerationJob(
                task_id=legacy_status.id,
                presentation_id=uuid.uuid4(),
                request_sha256="1" * 64,
                request_payload={"strategy": "authored"},
            )
            async with maker() as session:
                session.add_all((operation, status, linked, legacy_status, legacy))
                await session.commit()

            monkeypatch.setattr(jobs, "AUTHORED_CLUSTER_JOB_CONCURRENCY", 1)
            async with maker() as session:
                projected = await jobs.project_workspace_linked_generation_claim(
                    session,
                    operation_id,
                    expected_job_id=linked.id,
                    attempt_token=attempt_token,
                    attempt_number=2,
                    fencing_token=9,
                )
                assert projected == (linked.id, status.id)
                assert linked.workspace_operation_id is not None
                assert await jobs._authored_capacity_available(
                    session,
                    target_job_id=legacy.id,
                    now=now,
                )
                await session.rollback()

            async with maker() as session:
                stored_linked = await session.get(
                    PresentationGenerationJob, linked.id
                )
                assert stored_linked is not None
                assert stored_linked.state == "queued"
                assert stored_linked.attempt_token is None

            async with maker() as session:
                assert await jobs.project_workspace_linked_generation_claim(
                    session,
                    operation_id,
                    expected_job_id=linked.id,
                    attempt_token=attempt_token,
                    attempt_number=2,
                    fencing_token=9,
                ) == (linked.id, status.id)
                await session.commit()

            assert await jobs.claim_workspace_linked_generation_job(
                operation_id,
                attempt_token,
                2,
            ) == (linked.id, status.id)

            async with maker() as session:
                assert await jobs.project_workspace_linked_generation_succeeded(
                    session,
                    operation_id,
                    expected_job_id=linked.id,
                    expected_attempt_token=attempt_token,
                )
                await session.commit()
            async with maker() as session:
                stored_linked = await session.get(
                    PresentationGenerationJob, linked.id
                )
                stored_status = await session.get(
                    AsyncPresentationGenerationTaskModel, status.id
                )
                assert stored_linked is not None
                assert stored_linked.state == "published"
                assert stored_linked.attempt_token is None
                assert stored_status is not None
                assert stored_status.status == "completed"

            poison_operation_id = uuid.uuid4()
            poison_presentation_id = uuid.uuid4()
            poison_status = AsyncPresentationGenerationTaskModel(status="pending")
            poison_job = PresentationGenerationJob(
                task_id=poison_status.id,
                presentation_id=poison_presentation_id,
                workspace_operation_id=poison_operation_id,
                request_sha256="2" * 64,
                request_payload={"strategy": "authored"},
                last_error={
                    "code": "authored_slide_capacity_exceeded",
                    "retryable": False,
                },
            )
            poison_operation = WorkspaceOperation(
                id=poison_operation_id,
                conversation_id=uuid.uuid4(),
                presentation_id=poison_presentation_id,
                anchor_position=1,
                kind="presentation.generate",
                state="running",
                idempotency_key_hash="3" * 64,
                request_hash="4" * 64,
                attempt_number=1,
                attempt_token="poison-attempt",
                fencing_token=1,
                heartbeat_at=now,
                lease_expires_at=now + timedelta(minutes=5),
                started_at=now,
            )
            async with maker() as session:
                session.add_all((poison_operation, poison_status, poison_job))
                await session.commit()
            async with maker() as session:
                with pytest.raises(
                    jobs.WorkspaceLinkedGenerationProjectionPoisoned,
                    match="authored_slide_capacity_exceeded",
                ):
                    await jobs.project_workspace_linked_generation_claim(
                        session,
                        poison_operation_id,
                        expected_job_id=poison_job.id,
                        attempt_token="poison-attempt",
                        attempt_number=1,
                        fencing_token=1,
                    )
                await session.rollback()
            assert await jobs.claim_generation_job(
                legacy.id,
                allow_auth_resume=False,
            ) is not None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("cookie_required", "expected_state"),
    [(False, "queued"), (True, "awaiting_resume")],
)
def test_interrupted_attempt_is_safely_requeued(
    tmp_path,
    monkeypatch,
    cookie_required,
    expected_state,
):
    async def scenario():
        engine, maker = await _job_database(tmp_path, monkeypatch)
        try:
            status = AsyncPresentationGenerationTaskModel(status="pending")
            job = PresentationGenerationJob(
                task_id=status.id,
                presentation_id=uuid.uuid4(),
                request_sha256="b" * 64,
                request_payload={"content": "recoverable"},
                export_cookie_required=cookie_required,
            )
            async with maker() as session:
                session.add(status)
                session.add(job)
                await session.commit()

            claim = await jobs.claim_generation_job(
                job.id,
                allow_auth_resume=False,
            )
            assert claim is not None
            _, token = claim
            assert await jobs.release_generation_job(job.id, token)

            async with maker() as session:
                stored_job = await session.get(PresentationGenerationJob, job.id)
                stored_status = await session.get(
                    AsyncPresentationGenerationTaskModel, status.id
                )
            assert stored_job is not None
            assert stored_job.state == expected_state
            assert stored_job.attempt_token is None
            assert stored_status is not None
            assert stored_status.status == "pending"
            if cookie_required:
                assert "Idempotency-Key" in (stored_status.message or "")
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_design_retry_repairs_a_job_link_by_target_presentation(
    tmp_path, monkeypatch
):
    async def scenario():
        engine, maker = await _job_database(tmp_path, monkeypatch)
        try:
            source_operation_id = uuid.uuid4()
            retry_operation_id = uuid.uuid4()
            target_presentation_id = uuid.uuid4()
            status = AsyncPresentationGenerationTaskModel(
                status="error",
                message="Previous design attempt failed",
                error="authored_slide_persist_failed",
            )
            job = PresentationGenerationJob(
                task_id=status.id,
                presentation_id=target_presentation_id,
                workspace_operation_id=source_operation_id,
                request_sha256="c" * 64,
                request_payload={"content": "pinned design replay"},
                state="failed",
                attempt_number=3,
                last_error={"code": "authored_slide_persist_failed"},
            )
            async with maker() as session:
                session.add_all((status, job))
                await session.commit()

            async with maker() as session:
                rebound = await jobs.rebind_workspace_linked_generation_job_for_retry(
                    session,
                    uuid.uuid4(),
                    retry_operation_id,
                    expected_job_id=job.id,
                    expected_presentation_id=target_presentation_id,
                )
                await session.commit()

            assert rebound is True
            async with maker() as session:
                stored_job = await session.get(PresentationGenerationJob, job.id)
                stored_status = await session.get(
                    AsyncPresentationGenerationTaskModel, status.id
                )
            assert stored_job is not None
            assert stored_job.workspace_operation_id == retry_operation_id
            assert stored_job.state == "queued"
            assert stored_job.attempt_number == 0
            assert stored_job.last_error is None
            assert stored_status is not None
            assert stored_status.status == "pending"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_restart_reconciliation_recovers_only_abandoned_attempts(
    tmp_path, monkeypatch
):
    async def scenario():
        engine, maker = await _job_database(tmp_path, monkeypatch)
        try:
            now = jobs.utc_now()

            def make_job(
                *,
                state: str,
                cookie_required: bool = False,
                lease_delta: timedelta | None = None,
            ):
                status = AsyncPresentationGenerationTaskModel(
                    status="running" if state != "queued" else "pending",
                    message="before restart",
                )
                job = PresentationGenerationJob(
                    task_id=status.id,
                    presentation_id=uuid.uuid4(),
                    request_sha256=uuid.uuid4().hex * 2,
                    request_payload={"content": state},
                    export_cookie_required=cookie_required,
                    state=state,
                    attempt_token=f"token-{status.id}",
                    lease_expires_at=(
                        now + lease_delta if lease_delta is not None else None
                    ),
                )
                return status, job

            queued = make_job(state="queued")
            stale_running = make_job(
                state="running", lease_delta=timedelta(seconds=-1)
            )
            stale_auth_staging = make_job(
                state="staging", cookie_required=True, lease_delta=None
            )
            healthy_running = make_job(
                state="running", lease_delta=timedelta(minutes=10)
            )
            async with maker() as session:
                for status, job in (
                    queued,
                    stale_running,
                    stale_auth_staging,
                    healthy_running,
                ):
                    session.add(status)
                    session.add(job)
                await session.commit()

            assert await jobs.recover_generation_jobs() == 3

            async with maker() as session:
                recovered_queued = await session.get(
                    PresentationGenerationJob, queued[1].id
                )
                recovered_running = await session.get(
                    PresentationGenerationJob, stale_running[1].id
                )
                recovered_auth = await session.get(
                    PresentationGenerationJob, stale_auth_staging[1].id
                )
                untouched_running = await session.get(
                    PresentationGenerationJob, healthy_running[1].id
                )
                queued_status = await session.get(
                    AsyncPresentationGenerationTaskModel, queued[0].id
                )
                running_status = await session.get(
                    AsyncPresentationGenerationTaskModel, stale_running[0].id
                )
                auth_status = await session.get(
                    AsyncPresentationGenerationTaskModel,
                    stale_auth_staging[0].id,
                )
                healthy_status = await session.get(
                    AsyncPresentationGenerationTaskModel, healthy_running[0].id
                )

            for recovered in (recovered_queued, recovered_running):
                assert recovered is not None
                assert recovered.state == "queued"
                assert recovered.attempt_token is None
                assert recovered.lease_expires_at is None
            assert recovered_auth is not None
            assert recovered_auth.state == "awaiting_resume"
            assert recovered_auth.attempt_token is None
            assert recovered_auth.lease_expires_at is None
            assert untouched_running is not None
            assert untouched_running.state == "running"
            assert (
                untouched_running.attempt_token
                == healthy_running[1].attempt_token
            )
            assert untouched_running.lease_expires_at is not None

            assert queued_status is not None
            assert queued_status.status == "pending"
            assert queued_status.message == "Recovered after worker restart"
            assert running_status is not None
            assert running_status.status == "pending"
            assert running_status.message == "Recovered after worker restart"
            assert auth_status is not None
            assert auth_status.status == "pending"
            assert "Idempotency-Key" in (auth_status.message or "")
            assert healthy_status is not None
            assert healthy_status.status == "running"
            assert healthy_status.message == "before restart"

            assert (
                await jobs.claim_generation_job(
                    stale_auth_staging[1].id,
                    allow_auth_resume=False,
                )
                is None
            )
            assert (
                await jobs.claim_generation_job(
                    stale_auth_staging[1].id,
                    allow_auth_resume=True,
                )
                is not None
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def _run_worker_on_payload(request_payload: dict):
    """Run ``run_generation_job`` on one durable payload with a fake session."""
    from services import presentation_generation_pipeline as endpoint

    status = AsyncPresentationGenerationTaskModel(status="running")
    job = PresentationGenerationJob(
        task_id=status.id,
        presentation_id=uuid.uuid4(),
        request_id="request-from-ingress",
        request_sha256="c" * 64,
        request_payload=request_payload,
        state="running",
    )
    captured_request_ids: list[str | None] = []
    finalized: list[dict] = []

    class FailureSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, model, identifier):
            return status if model is AsyncPresentationGenerationTaskModel else None

        def add(self, _row):
            return None

        async def commit(self):
            return None

    sessions = []

    class WorkerSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, model, identifier):
            if model is PresentationGenerationJob and identifier == job.id:
                return job
            if (
                model is AsyncPresentationGenerationTaskModel
                and identifier == status.id
            ):
                return status
            return None

        async def refresh(self, _):
            return None

    async def generate(request, *_args, **_kwargs):
        captured_request_ids.append(request._request_id)
        status.status = "completed"

    async def finalize(*_args, **kwargs):
        finalized.append(kwargs)
        return True

    def session_maker():
        sessions.append(None)
        return WorkerSession() if len(sessions) == 1 else FailureSession()

    with (
        patch.object(
            endpoint,
            "claim_generation_job",
            new=AsyncMock(return_value=(status.id, "attempt-token")),
        ),
        patch.object(
            endpoint,
            "heartbeat_generation_job_loop",
            new=AsyncMock(),
        ),
        patch.object(
            endpoint,
            "generate_presentation_handler",
            new=generate,
        ),
        patch.object(
            endpoint,
            "finalize_generation_job",
            new=finalize,
        ),
        patch.object(
            endpoint,
            "async_session_maker",
            new=session_maker,
        ),
    ):
        asyncio.run(endpoint.run_generation_job(job.id, None))
    return status, captured_request_ids, finalized


def test_worker_restores_correlation_request_id_from_durable_job():
    _status, captured_request_ids, finalized = _run_worker_on_payload(
        {"content": "durable request", "strategy": "svg"}
    )

    assert captured_request_ids == ["request-from-ingress"]
    assert finalized == [{"succeeded": True}]


@pytest.mark.parametrize(
    "request_payload",
    [
        {"content": "durable authored request", "strategy": "authored"},
        # Saved before the strategy resolver existed: omitted meant authored.
        {"content": "durable pre-resolver request"},
        {"content": "durable deck-ir request", "strategy": "authored", "generation_engine": "deck-ir"},
    ],
)
def test_worker_fails_a_job_staged_with_authored_without_generating(request_payload):
    status, captured_request_ids, finalized = _run_worker_on_payload(request_payload)

    assert captured_request_ids == []
    assert finalized == [{"succeeded": False}]
    assert status.status == "error"
    assert status.error["code"] == "authored_strategy_removed"
    assert status.error["status_code"] == 422
