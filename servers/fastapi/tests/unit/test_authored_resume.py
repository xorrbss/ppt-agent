import asyncio
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob


async def _resume_database(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'authored-resume.db'}"
    )
    async with engine.begin() as connection:
        # WAL permits the two retry requests to retain their pre-update reads
        # while the winner commits its guarded failed -> queued transition.
        await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        await connection.run_sync(
            SQLModel.metadata.create_all,
            tables=[
                AsyncPresentationGenerationTaskModel.__table__,
                PresentationGenerationJob.__table__,
            ],
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_retryable_failed_job_has_exactly_one_atomic_resume_winner(tmp_path):
    from api.v1.ppt.endpoints import presentation_generate as endpoint

    async def scenario():
        engine, maker = await _resume_database(tmp_path)
        try:
            status = AsyncPresentationGenerationTaskModel(
                status="error",
                message="Presentation generation failed",
                error={
                    "code": "authored_slide_render_failed",
                    "slide_number": 42,
                    "stage": "render",
                },
            )
            key_hash = "b" * 64
            job = PresentationGenerationJob(
                task_id=status.id,
                presentation_id=uuid.uuid4(),
                idempotency_key_hash=key_hash,
                request_sha256="c" * 64,
                request_payload={"template": "authored"},
                state="failed",
                attempt_number=1,
                attempt_token="expired-attempt",
                resume_allowed=True,
                lease_expires_at=None,
                last_error=status.error,
            )
            async with maker() as setup:
                setup.add_all((status, job))
                await setup.commit()

            # Model two same-key retries that both read the failed row before
            # either attempts the guarded state transition.
            first = maker()
            second = maker()
            try:
                first_job = await first.get(PresentationGenerationJob, job.id)
                first_status = await first.get(
                    AsyncPresentationGenerationTaskModel, status.id
                )
                second_job = await second.get(PresentationGenerationJob, job.id)
                second_status = await second.get(
                    AsyncPresentationGenerationTaskModel, status.id
                )

                assert first_job.state == second_job.state == "failed"
                assert await endpoint._resume_failed_authored_job(
                    first_job, first_status, first
                )
                assert not await endpoint._resume_failed_authored_job(
                    second_job, second_status, second
                )
            finally:
                await first.close()
                await second.close()

            async with maker() as verification:
                resumed = await verification.get(PresentationGenerationJob, job.id)
                resumed_status = await verification.get(
                    AsyncPresentationGenerationTaskModel, status.id
                )

            assert resumed.state == "queued"
            assert resumed.resume_allowed is False
            assert resumed.attempt_token is None
            assert resumed.idempotency_key_hash == key_hash
            assert resumed.attempt_number == 1
            # Concurrent same-key retries consume exactly one resume credit.
            assert resumed.resume_count == 1
            # Checkpoint diagnostic history remains on the durable job while the
            # task-facing transient error is cleared for the resumed execution.
            assert resumed.last_error["slide_number"] == 42
            assert resumed_status.status == "pending"
            assert resumed_status.error is None
            assert resumed_status.message == (
                "Queued to resume from the last completed slide"
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_resume_is_refused_after_resume_cap_without_flipping_state(
    tmp_path, monkeypatch
):
    from api.v1.ppt.endpoints import presentation_generate as endpoint

    async def scenario():
        engine, maker = await _resume_database(tmp_path)
        try:
            monkeypatch.setenv("AUTHORED_RESUME_MAX_ATTEMPTS", "3")
            status = AsyncPresentationGenerationTaskModel(
                status="error",
                message="Presentation generation failed",
                error={"code": "authored_slide_render_failed", "retryable": True},
            )
            job = PresentationGenerationJob(
                task_id=status.id,
                presentation_id=uuid.uuid4(),
                idempotency_key_hash="f" * 64,
                request_sha256="a" * 64,
                request_payload={"template": "authored"},
                state="failed",
                attempt_number=3,
                resume_count=3,
                resume_allowed=True,
                last_error=status.error,
            )
            async with maker() as setup:
                setup.add_all((status, job))
                await setup.commit()

            async with maker() as session:
                exhausted_job = await session.get(PresentationGenerationJob, job.id)
                exhausted_status = await session.get(
                    AsyncPresentationGenerationTaskModel, status.id
                )
                with pytest.raises(HTTPException) as refused:
                    await endpoint._resume_failed_authored_job(
                        exhausted_job, exhausted_status, session
                    )
            assert refused.value.status_code == 409
            assert refused.value.detail == "resume_attempts_exhausted"

            async with maker() as verification:
                stored_job = await verification.get(PresentationGenerationJob, job.id)
                stored_status = await verification.get(
                    AsyncPresentationGenerationTaskModel, status.id
                )
            assert stored_job.state == "failed"
            assert stored_job.resume_allowed is False
            assert stored_status.status == "error"

            # Raising the cap does not revive the job: refusal is durable.
            monkeypatch.setenv("AUTHORED_RESUME_MAX_ATTEMPTS", "10")
            async with maker() as session:
                stored_job = await session.get(PresentationGenerationJob, job.id)
                stored_status = await session.get(
                    AsyncPresentationGenerationTaskModel, status.id
                )
                assert not await endpoint._resume_failed_authored_job(
                    stored_job, stored_status, session
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_resume_below_resume_cap_is_still_allowed(tmp_path, monkeypatch):
    from api.v1.ppt.endpoints import presentation_generate as endpoint

    async def scenario():
        engine, maker = await _resume_database(tmp_path)
        try:
            monkeypatch.setenv("AUTHORED_RESUME_MAX_ATTEMPTS", "3")
            status = AsyncPresentationGenerationTaskModel(
                status="error",
                message="Presentation generation failed",
                error={"code": "authored_slide_render_failed", "retryable": True},
            )
            job = PresentationGenerationJob(
                task_id=status.id,
                presentation_id=uuid.uuid4(),
                idempotency_key_hash="a" * 64,
                request_sha256="a" * 64,
                request_payload={"template": "authored"},
                state="failed",
                attempt_number=2,
                resume_count=2,
                resume_allowed=True,
                last_error=status.error,
            )
            async with maker() as setup:
                setup.add_all((status, job))
                await setup.commit()

            async with maker() as session:
                stored_job = await session.get(PresentationGenerationJob, job.id)
                stored_status = await session.get(
                    AsyncPresentationGenerationTaskModel, status.id
                )
                assert await endpoint._resume_failed_authored_job(
                    stored_job, stored_status, session
                )

            async with maker() as verification:
                resumed = await verification.get(PresentationGenerationJob, job.id)
            assert resumed.state == "queued"
            assert resumed.resume_count == 3
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_crash_recovery_reclaims_do_not_consume_the_resume_cap(
    tmp_path, monkeypatch
):
    """Regression: attempt_number inflation from worker restarts must not
    exhaust the failure-resume cap (design doc §6: restarts are free)."""
    from api.v1.ppt.endpoints import presentation_generate as endpoint
    from services import presentation_generation_job_service as jobs

    async def scenario():
        engine, maker = await _resume_database(tmp_path)
        try:
            monkeypatch.setattr(jobs, "async_session_maker", maker)
            monkeypatch.setenv("AUTHORED_RESUME_MAX_ATTEMPTS", "3")
            status = AsyncPresentationGenerationTaskModel(
                status="pending",
                message="Queued for generation",
            )
            job = PresentationGenerationJob(
                task_id=status.id,
                presentation_id=uuid.uuid4(),
                idempotency_key_hash="e" * 64,
                request_sha256="d" * 64,
                request_payload={"template": "authored"},
            )
            async with maker() as setup:
                setup.add_all((status, job))
                await setup.commit()

            # Four shutdown-requeue cycles: each claim increments
            # attempt_number past the cap with zero failure resumes.
            for _ in range(4):
                claim = await jobs.claim_generation_job(
                    job.id, allow_auth_resume=False
                )
                assert claim is not None
                assert await jobs.release_generation_job(job.id, claim[1])

            async with maker() as failure:
                survived = await failure.get(PresentationGenerationJob, job.id)
                assert survived.attempt_number >= 3
                assert survived.resume_count == 0
                # First genuine retryable failure after the restarts.
                survived.state = "failed"
                survived.resume_allowed = True
                survived.last_error = {
                    "code": "authored_slide_render_failed",
                    "retryable": True,
                }
                failure_status = await failure.get(
                    AsyncPresentationGenerationTaskModel, status.id
                )
                failure_status.status = "error"
                failure_status.error = survived.last_error
                failure.add_all((survived, failure_status))
                await failure.commit()

            async with maker() as session:
                stored_job = await session.get(PresentationGenerationJob, job.id)
                stored_status = await session.get(
                    AsyncPresentationGenerationTaskModel, status.id
                )
                assert await endpoint._resume_failed_authored_job(
                    stored_job, stored_status, session
                )

            async with maker() as verification:
                resumed = await verification.get(PresentationGenerationJob, job.id)
            assert resumed.state == "queued"
            assert resumed.resume_count == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_non_retryable_failed_job_cannot_be_resumed(tmp_path):
    from api.v1.ppt.endpoints import presentation_generate as endpoint

    async def scenario():
        engine, maker = await _resume_database(tmp_path)
        try:
            status = AsyncPresentationGenerationTaskModel(
                status="error",
                message="Permanent failure",
                error={"code": "authored_invalid_input", "retryable": False},
            )
            job = PresentationGenerationJob(
                task_id=status.id,
                presentation_id=uuid.uuid4(),
                idempotency_key_hash="d" * 64,
                request_sha256="e" * 64,
                request_payload={"template": "authored"},
                state="failed",
                resume_allowed=False,
                last_error=status.error,
            )
            async with maker() as session:
                session.add_all((status, job))
                await session.commit()

            async with maker() as session:
                stored_job = await session.get(PresentationGenerationJob, job.id)
                stored_status = await session.get(
                    AsyncPresentationGenerationTaskModel, status.id
                )
                assert not await endpoint._resume_failed_authored_job(
                    stored_job, stored_status, session
                )

            async with maker() as verification:
                unchanged_job = await verification.get(
                    PresentationGenerationJob, job.id
                )
                unchanged_status = await verification.get(
                    AsyncPresentationGenerationTaskModel, status.id
                )
            assert unchanged_job.state == "failed"
            assert unchanged_job.resume_allowed is False
            assert unchanged_status.status == "error"
            assert unchanged_status.error["retryable"] is False
        finally:
            await engine.dispose()

    asyncio.run(scenario())
