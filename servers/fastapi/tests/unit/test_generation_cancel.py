import asyncio
import uuid

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
        f"sqlite+aiosqlite:///{tmp_path / 'generation-cancel.db'}"
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


async def _seed_job(maker, *, owner=OWNER_A, job_state="queued", task_status="pending"):
    token = set_current_owner_id(owner)
    try:
        status = AsyncPresentationGenerationTaskModel(
            status=task_status,
            message="Queued for generation",
        )
    finally:
        reset_current_owner_id(token)
    job = PresentationGenerationJob(
        task_id=status.id,
        presentation_id=uuid.uuid4(),
        request_id="cancel-request-id",
        request_sha256="a" * 64,
        request_payload={"content": "durable"},
        state=job_state,
    )
    async with maker() as session:
        session.add(status)
        session.add(job)
        await session.commit()
    return status, job


def test_cancel_marks_queued_job_terminal_and_unclaimable(tmp_path, monkeypatch):
    async def scenario():
        engine, maker = await _job_database(tmp_path, monkeypatch)
        try:
            status, job = await _seed_job(maker)

            owner_token = set_current_owner_id(OWNER_A)
            try:
                cancelled = await jobs.cancel_generation_job(status.id)
            finally:
                reset_current_owner_id(owner_token)

            assert cancelled is not None
            assert cancelled.status == "error"
            assert cancelled.error["code"] == "generation_cancelled"

            async with maker() as session:
                stored_job = await session.get(PresentationGenerationJob, job.id)
                assert stored_job.state == "failed"
                assert stored_job.attempt_token is None

            # A cancelled job must never be claimable again.
            assert await jobs.claim_generation_job(job.id, allow_auth_resume=True) is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_cancel_is_owner_scoped(tmp_path, monkeypatch):
    async def scenario():
        engine, maker = await _job_database(tmp_path, monkeypatch)
        try:
            status, job = await _seed_job(maker)

            other_token = set_current_owner_id(OWNER_B)
            try:
                assert await jobs.cancel_generation_job(status.id) is None
            finally:
                reset_current_owner_id(other_token)

            async with maker() as session:
                stored_job = await session.get(PresentationGenerationJob, job.id)
                assert stored_job.state == "queued"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_cancel_of_terminal_task_is_a_noop(tmp_path, monkeypatch):
    async def scenario():
        engine, maker = await _job_database(tmp_path, monkeypatch)
        try:
            status, job = await _seed_job(
                maker, job_state="published", task_status="completed"
            )

            owner_token = set_current_owner_id(OWNER_A)
            try:
                result = await jobs.cancel_generation_job(status.id)
            finally:
                reset_current_owner_id(owner_token)

            assert result is not None
            assert result.status == "completed"
            async with maker() as session:
                stored_job = await session.get(PresentationGenerationJob, job.id)
                assert stored_job.state == "published"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_cancel_interrupts_registered_inflight_attempt(tmp_path, monkeypatch):
    async def scenario():
        engine, maker = await _job_database(tmp_path, monkeypatch)
        try:
            status, job = await _seed_job(maker, job_state="running")

            inflight = asyncio.create_task(asyncio.sleep(30))
            monkeypatch.setitem(jobs._active_jobs, job.id, inflight)

            owner_token = set_current_owner_id(OWNER_A)
            try:
                cancelled = await jobs.cancel_generation_job(status.id)
            finally:
                reset_current_owner_id(owner_token)

            assert cancelled is not None and cancelled.status == "error"
            await asyncio.gather(inflight, return_exceptions=True)
            assert inflight.cancelled()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
