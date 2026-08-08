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


def test_worker_restores_correlation_request_id_from_durable_job():
    from api.v1.ppt.endpoints import presentation_generate as endpoint

    status = AsyncPresentationGenerationTaskModel(status="running")
    job = PresentationGenerationJob(
        task_id=status.id,
        presentation_id=uuid.uuid4(),
        request_id="request-from-ingress",
        request_sha256="c" * 64,
        request_payload={"content": "durable request"},
        state="running",
    )
    captured_request_ids: list[str | None] = []

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
            new=AsyncMock(return_value=True),
        ),
        patch.object(
            endpoint,
            "async_session_maker",
            new=lambda: WorkerSession(),
        ),
    ):
        asyncio.run(endpoint.run_generation_job(job.id, None))

    assert captured_request_ids == ["request-from-ingress"]
