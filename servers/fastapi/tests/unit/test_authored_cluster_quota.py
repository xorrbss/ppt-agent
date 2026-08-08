import asyncio
import uuid

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
        f"sqlite+aiosqlite:///{tmp_path / 'authored-cluster-quota.db'}"
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
    monkeypatch.setattr(jobs, "AUTHORED_CLUSTER_JOB_CONCURRENCY", 1)
    return engine, maker


async def _create_job(maker, *, authored: bool):
    status = AsyncPresentationGenerationTaskModel(
        status="pending",
        message="Queued for generation",
    )
    payload = (
        {"template": "authored", "content": "cluster quota"}
        if authored
        else {"template": "general", "content": "legacy path"}
    )
    job = PresentationGenerationJob(
        task_id=status.id,
        presentation_id=uuid.uuid4(),
        request_sha256=uuid.uuid4().hex * 2,
        request_payload=payload,
    )
    async with maker() as session:
        session.add(status)
        session.add(job)
        await session.commit()
    return status, job


def test_sqlite_serializes_authored_claims_and_preserves_queued_retry(
    tmp_path, monkeypatch
):
    async def scenario():
        engine, maker = await _job_database(tmp_path, monkeypatch)
        try:
            first = await _create_job(maker, authored=True)
            second = await _create_job(maker, authored=True)

            async def claim(job_id):
                try:
                    return await jobs.claim_generation_job(
                        job_id,
                        allow_auth_resume=False,
                    )
                except jobs.GenerationCapacityUnavailable:
                    return "capacity-unavailable"

            results = await asyncio.gather(claim(first[1].id), claim(second[1].id))
            claims = [result for result in results if result != "capacity-unavailable"]
            assert len(claims) == 1
            assert results.count("capacity-unavailable") == 1
            assert claims[0] is not None

            blocked = second if results[1] == "capacity-unavailable" else first
            running = first if blocked is second else second
            async with maker() as session:
                blocked_job = await session.get(
                    PresentationGenerationJob, blocked[1].id
                )
                blocked_status = await session.get(
                    AsyncPresentationGenerationTaskModel, blocked[0].id
                )
            assert blocked_job is not None
            assert blocked_job.state == "queued"
            assert blocked_job.attempt_number == 0
            assert blocked_job.attempt_token is None
            assert blocked_status is not None
            assert blocked_status.status == "pending"

            _, running_token = claims[0]
            assert await jobs.finalize_generation_job(
                running[1].id,
                running_token,
                succeeded=True,
            )
            resumed = await jobs.claim_generation_job(
                blocked[1].id,
                allow_auth_resume=False,
            )
            assert resumed is not None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_non_authored_claims_do_not_consume_or_wait_for_authored_quota(
    tmp_path, monkeypatch
):
    async def scenario():
        engine, maker = await _job_database(tmp_path, monkeypatch)
        try:
            authored = await _create_job(maker, authored=True)
            legacy = await _create_job(maker, authored=False)

            assert await jobs.claim_generation_job(
                authored[1].id,
                allow_auth_resume=False,
            )
            assert await jobs.claim_generation_job(
                legacy[1].id,
                allow_auth_resume=False,
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_invalid_cluster_limit_falls_back_to_existing_job_limit(monkeypatch):
    monkeypatch.setenv("AUTHORED_JOB_CONCURRENCY", "3")
    monkeypatch.setenv("AUTHORED_CLUSTER_JOB_CONCURRENCY", "not-an-int")
    assert jobs._positive_int_env(
        "AUTHORED_CLUSTER_JOB_CONCURRENCY",
        jobs._positive_int_env("AUTHORED_JOB_CONCURRENCY", 1),
    ) == 3
    monkeypatch.setenv("AUTHORED_CLUSTER_JOB_CONCURRENCY", "0")
    assert jobs._positive_int_env("AUTHORED_CLUSTER_JOB_CONCURRENCY", 3) == 1


def test_capacity_wait_is_cancellable_before_an_attempt_is_claimed(monkeypatch):
    from api.v1.ppt.endpoints import presentation_generate as endpoint

    async def scenario():
        claim_attempted = asyncio.Event()

        async def no_capacity(*args, **kwargs):
            claim_attempted.set()
            raise jobs.GenerationCapacityUnavailable(
                "authored_cluster_capacity_unavailable"
            )

        monkeypatch.setattr(endpoint, "claim_generation_job", no_capacity)
        monkeypatch.setattr(endpoint, "GENERATION_JOB_CAPACITY_POLL_SECONDS", 60)

        runner = asyncio.create_task(endpoint.run_generation_job(uuid.uuid4(), None))
        await asyncio.wait_for(claim_attempted.wait(), timeout=1)
        runner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await runner

    asyncio.run(scenario())
