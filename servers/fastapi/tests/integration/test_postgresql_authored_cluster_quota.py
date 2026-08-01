"""Live PostgreSQL coverage for authored cluster-wide admission control.

The test uses an isolated schema in a dedicated test database. It is opt-in so
ordinary SQLite development remains self-contained. Set
``PPT_AGENT_POSTGRES_TEST_URL`` to a database whose name ends in ``test`` or
``tests``; set ``PPT_AGENT_REQUIRE_POSTGRES_INTEGRATION=1`` in a release gate to
turn a missing URL into a failure.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import multiprocessing
import os
import re
from typing import Any
import uuid

import pytest
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from services import presentation_generation_job_service as jobs
from utils.db_utils import to_sync_sqlalchemy_url


def _postgres_urls() -> tuple[str, str]:
    require_postgres = os.getenv("PPT_AGENT_REQUIRE_POSTGRES_INTEGRATION") == "1"
    configured_url = os.getenv("PPT_AGENT_POSTGRES_TEST_URL")
    if not configured_url:
        if require_postgres:
            pytest.fail(
                "PPT_AGENT_POSTGRES_TEST_URL is required by the PostgreSQL "
                "integration gate"
            )
        pytest.skip("set PPT_AGENT_POSTGRES_TEST_URL to run live PostgreSQL tests")

    sync_url = to_sync_sqlalchemy_url(configured_url)
    parsed = make_url(sync_url)
    if parsed.get_backend_name() != "postgresql":
        pytest.fail("PPT_AGENT_POSTGRES_TEST_URL must use PostgreSQL")
    if not re.search(r"(?:^|[_-])tests?$", parsed.database or "", re.IGNORECASE):
        pytest.fail(
            "refusing PostgreSQL integration test against a database whose "
            "name does not end in test or tests"
        )

    query = dict(parsed.query)
    if "sslmode" in query and "ssl" not in query:
        query["ssl"] = query.pop("sslmode")
    async_url = parsed.set(
        drivername="postgresql+asyncpg",
        query=query,
    ).render_as_string(hide_password=False)
    return sync_url, async_url


def _claim_from_process(
    async_url: str,
    schema: str,
    job_id: str,
    start_gate: Any,
    result_queue: Any,
) -> None:
    """Claim from a fresh interpreter and connection pool."""

    async def scenario() -> tuple[str, str | None, str | None]:
        engine = create_async_engine(
            async_url,
            poolclass=NullPool,
            connect_args={"server_settings": {"search_path": schema}},
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        jobs.async_session_maker = maker
        jobs.AUTHORED_CLUSTER_JOB_CONCURRENCY = 1
        try:
            claimed = await jobs.claim_generation_job(
                uuid.UUID(job_id),
                allow_auth_resume=False,
            )
            if claimed is None:
                return "not-claimable", None, None
            return "claimed", claimed[0], claimed[1]
        except jobs.GenerationCapacityUnavailable:
            return "capacity-unavailable", None, None
        finally:
            await engine.dispose()

    try:
        if not start_gate.wait(timeout=15):
            raise TimeoutError("claim start gate was not released")
        result_queue.put(("ok", asyncio.run(scenario())))
    except BaseException as exc:
        result_queue.put(("error", type(exc).__name__, str(exc)))
        raise


def _run_process_claims(async_url: str, schema: str, job_ids: list[uuid.UUID]):
    context = multiprocessing.get_context("spawn")
    start_gate = context.Event()
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_claim_from_process,
            args=(async_url, schema, str(job_id), start_gate, result_queue),
        )
        for job_id in job_ids
    ]
    try:
        for process in processes:
            process.start()
        start_gate.set()
        for process in processes:
            process.join(timeout=30)
        assert all(not process.is_alive() for process in processes)
        assert [process.exitcode for process in processes] == [0] * len(processes)
        return [result_queue.get(timeout=5) for _ in processes]
    finally:
        for process in processes:
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
        result_queue.close()


async def _create_authored_job(maker, label: str):
    task = AsyncPresentationGenerationTaskModel(
        id=f"postgres-authored-quota-{label}-{uuid.uuid4().hex}",
        status="pending",
        message="Queued for PostgreSQL quota integration",
    )
    job = PresentationGenerationJob(
        task_id=task.id,
        presentation_id=uuid.uuid4(),
        request_sha256=uuid.uuid4().hex * 2,
        request_payload={"template": "authored", "content": label},
    )
    async with maker() as session:
        session.add(task)
        session.add(job)
        await session.commit()
    return task, job


def test_postgresql_multiprocess_authored_quota_cancel_and_lease_recovery(
    monkeypatch,
):
    sync_url, async_url = _postgres_urls()
    schema = f"authored_quota_{uuid.uuid4().hex}"
    sync_engine = create_engine(sync_url, poolclass=NullPool)
    with sync_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    async def scenario() -> None:
        engine = create_async_engine(
            async_url,
            poolclass=NullPool,
            connect_args={"server_settings": {"search_path": schema}},
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(jobs, "async_session_maker", maker)
        monkeypatch.setattr(jobs, "AUTHORED_CLUSTER_JOB_CONCURRENCY", 1)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(
                    SQLModel.metadata.create_all,
                    tables=[
                        AsyncPresentationGenerationTaskModel.__table__,
                        PresentationGenerationJob.__table__,
                    ],
                )

            # Verify the PostgreSQL path takes a transaction-scoped advisory lock.
            async with maker() as session:
                assert await jobs._serialize_cluster_claims(session) is True
                held_locks = await session.scalar(
                    text(
                        "SELECT count(*) FROM pg_locks "
                        "WHERE locktype = 'advisory' "
                        "AND pid = pg_backend_pid() AND granted"
                    )
                )
                assert held_locks >= 1
                await session.rollback()

            first = await _create_authored_job(maker, "first")
            second = await _create_authored_job(maker, "second")
            process_results = await asyncio.to_thread(
                _run_process_claims,
                async_url,
                schema,
                [first[1].id, second[1].id],
            )
            assert all(result[0] == "ok" for result in process_results)
            outcomes = [result[1][0] for result in process_results]
            assert outcomes.count("claimed") == 1
            assert outcomes.count("capacity-unavailable") == 1

            async with maker() as session:
                rows = list(
                    await session.scalars(
                        select(PresentationGenerationJob).where(
                            PresentationGenerationJob.id.in_(
                                (first[1].id, second[1].id)
                            )
                        )
                    )
                )
            running = next(row for row in rows if row.state == "running")
            blocked = next(row for row in rows if row.state == "queued")
            assert running.attempt_number == 1
            assert running.attempt_token is not None
            assert blocked.attempt_number == 0
            assert blocked.attempt_token is None

            # A durable cancellation clears the lease and immediately frees quota.
            cancelled = await jobs.cancel_generation_job(running.task_id)
            assert cancelled is not None
            assert cancelled.status == "error"
            blocked_claim = await jobs.claim_generation_job(
                blocked.id,
                allow_auth_resume=False,
            )
            assert blocked_claim is not None
            _, blocked_token = blocked_claim

            third = await _create_authored_job(maker, "third")
            with pytest.raises(jobs.GenerationCapacityUnavailable):
                await jobs.claim_generation_job(
                    third[1].id,
                    allow_auth_resume=False,
                )

            # Simulate process death. An expired lease no longer consumes quota,
            # but reclaiming it remains fenced while the replacement is active.
            expired_at = jobs.utc_now() - timedelta(seconds=1)
            async with maker() as session:
                await session.execute(
                    update(PresentationGenerationJob)
                    .where(PresentationGenerationJob.id == blocked.id)
                    .values(lease_expires_at=expired_at)
                )
                await session.commit()

            third_claim = await jobs.claim_generation_job(
                third[1].id,
                allow_auth_resume=False,
            )
            assert third_claim is not None
            _, third_token = third_claim
            with pytest.raises(jobs.GenerationCapacityUnavailable):
                await jobs.claim_generation_job(
                    blocked.id,
                    allow_auth_resume=False,
                )

            assert await jobs.finalize_generation_job(
                third[1].id,
                third_token,
                succeeded=True,
            )
            reclaimed = await jobs.claim_generation_job(
                blocked.id,
                allow_auth_resume=False,
            )
            assert reclaimed is not None
            _, replacement_token = reclaimed
            assert replacement_token != blocked_token
            assert not await jobs.finalize_generation_job(
                blocked.id,
                blocked_token,
                succeeded=True,
            )
            assert await jobs.finalize_generation_job(
                blocked.id,
                replacement_token,
                succeeded=True,
            )

            async with maker() as session:
                cancelled_job = await session.get(
                    PresentationGenerationJob, running.id
                )
                recovered_job = await session.get(
                    PresentationGenerationJob, blocked.id
                )
            assert cancelled_job is not None
            assert cancelled_job.state == "failed"
            assert cancelled_job.attempt_token is None
            assert cancelled_job.lease_expires_at is None
            assert recovered_job is not None
            assert recovered_job.state == "published"
            assert recovered_job.attempt_number == 2
        finally:
            await engine.dispose()

    try:
        asyncio.run(scenario())
    finally:
        with sync_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        sync_engine.dispose()
