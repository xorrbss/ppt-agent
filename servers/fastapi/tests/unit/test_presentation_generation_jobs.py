import asyncio
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
