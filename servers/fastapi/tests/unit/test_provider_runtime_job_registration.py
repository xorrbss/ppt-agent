"""Resumed attempts continue the request accounting of earlier attempts.

A worker restart gives the resumed attempt a fresh event loop and therefore a
fresh process-local ``ProviderRuntimeControl``.  Its first provider call is a
retry by design: the slide checkpoint keeps the provider attempt ordinal of
the interrupted call, so the resumed call is ordinal 2 and ``is_retry=True``.
Before the fix that call was rejected with ``retry_requires_an_initial_request``
and the job was cancelled; the initial requests of the earlier attempt are
persisted as provider usage receipts and must seed the new process.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.provider_usage_receipt import ProviderUsageReceipt
from services.provider_runtime_control import (
    ProviderRuntimeControl,
    RetryAmplificationError,
    RuntimeLimits,
)
from services.provider_usage_instrumentation import (
    provider_call_admission,
    provider_runtime_job_status,
    provider_usage_scope,
    record_local_stage,
    record_provider_call,
)
from services.provider_runtime_job_registration import persisted_request_counts
from services.provider_usage_receipt_service import (
    ProviderUsageReceiptInput,
    persisted_provider_request_counts,
    record_provider_usage_receipt,
)


async def _receipt_database(tmp_path, monkeypatch):
    """One SQLite file per test; a new engine per event loop, like a restart."""

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'receipts.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(
            SQLModel.metadata.create_all,
            tables=[
                PresentationGenerationJob.__table__,
                ProviderUsageReceipt.__table__,
            ],
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("services.database.async_session_maker", maker)
    return engine


def _receipt(job_id: uuid.UUID, key: str, **changes) -> ProviderUsageReceiptInput:
    values = {
        "job_id": job_id,
        "operation": "authored_generation",
        "stage": "author",
        "provider": "openai",
        "model": "gpt-test",
        "receipt_key_hash": key * 64,
        "retry_class": "initial",
        "outcome": "succeeded",
        "latency_ms": 10,
        "usage_state": "unavailable",
    }
    values.update(changes)
    return ProviderUsageReceiptInput(**values)


def test_persisted_request_counts_follow_admission_classes(tmp_path, monkeypatch):
    async def scenario() -> None:
        engine = await _receipt_database(tmp_path, monkeypatch)
        try:
            job_id = uuid.uuid4()
            other_job = uuid.uuid4()
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as session:
                for receipt in (
                    _receipt(job_id, "a"),
                    _receipt(job_id, "b", retry_class="resume", request_count=2),
                    _receipt(job_id, "c", retry_class="retry", outcome="failed"),
                    _receipt(job_id, "d", retry_class="recovery"),
                    _receipt(job_id, "e", provider="local", model="none"),
                    _receipt(other_job, "f"),
                ):
                    await record_provider_usage_receipt(session, receipt)
                await session.commit()
                assert await persisted_provider_request_counts(session, job_id) == (3, 2)
                assert await persisted_provider_request_counts(session, uuid.uuid4()) == (0, 0)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_resumed_attempt_in_a_new_process_is_not_a_retry_without_an_initial_request(
    tmp_path, monkeypatch
):
    job_id = uuid.uuid4()

    async def first_attempt() -> None:
        engine = await _receipt_database(tmp_path, monkeypatch)
        try:
            with provider_usage_scope(
                job_id=job_id,
                operation="generate",
                stage="author",
                attempt_number=1,
            ):
                for _ in range(2):
                    async with provider_call_admission(provider="openai", model="gpt-test"):
                        pass
                    await record_provider_call(
                        provider="openai",
                        model="gpt-test",
                        response=None,
                        latency_ms=12,
                        retry_class="initial",
                    )
                # Local stages are receipts too, but never provider admissions.
                await record_local_stage(latency_ms=1)
            status = await provider_runtime_job_status(job_id)
            assert status["initial_requests"] == 2
        finally:
            await engine.dispose()

    async def resumed_attempt_after_restart() -> None:
        # asyncio.run() gives this attempt a fresh loop and therefore a fresh
        # process-local runtime state, exactly like a restarted worker.
        engine = await _receipt_database(tmp_path, monkeypatch)
        try:
            with provider_usage_scope(
                job_id=job_id,
                operation="generate",
                stage="author",
                attempt_number=2,
            ):
                async with provider_call_admission(
                    is_retry=True, provider="openai", model="gpt-test"
                ):
                    pass
            status = await provider_runtime_job_status(job_id)
            assert status["cancelled"] is False
            assert status["initial_requests"] == 2
            assert status["retry_requests"] == 1
            assert status["total_requests"] == 3
        finally:
            await engine.dispose()

    asyncio.run(first_attempt())
    asyncio.run(resumed_attempt_after_restart())


def test_seeding_falls_open_when_the_receipt_store_cannot_be_read(tmp_path, monkeypatch):
    """A resumed attempt still starts when the receipt table is missing.

    Seeding is accounting, not admission authority. A database without the
    receipt table -- an isolated test database, a partially migrated
    deployment -- must not fail the attempt that was being recovered.
    """

    async def scenario() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'empty.db'}")
        try:
            maker = async_sessionmaker(engine, expire_on_commit=False)
            monkeypatch.setattr("services.database.async_session_maker", maker)
            assert await persisted_request_counts(uuid.uuid4()) == (0, 0)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_a_retry_with_no_persisted_initial_request_is_still_rejected(
    tmp_path, monkeypatch
):
    async def scenario() -> None:
        engine = await _receipt_database(tmp_path, monkeypatch)
        try:
            with provider_usage_scope(
                job_id=uuid.uuid4(),
                operation="generate",
                stage="author",
                attempt_number=2,
            ):
                with pytest.raises(RetryAmplificationError) as rejected:
                    async with provider_call_admission(is_retry=True):
                        pytest.fail("a retry without any initial request was admitted")
            assert str(rejected.value) == "retry_requires_an_initial_request"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_seeded_counters_continue_the_durable_request_accounting() -> None:
    async def scenario() -> None:
        control = ProviderRuntimeControl(RuntimeLimits(retry_amplification_ceiling=2.0))
        await control.register_job(
            "job",
            estimated_budget_microunits=None,
            actual_budget_microunits=None,
            initial_requests=3,
            retry_requests=1,
        )
        await control.admit("job", "r1", estimated_cost_microunits=1, is_retry=True)
        status = await control.job_status("job")
        assert status["initial_requests"] == 3
        assert status["retry_requests"] == 2
        assert status["total_requests"] == 5
        with pytest.raises(ValueError):
            await control.register_job(
                "negative",
                estimated_budget_microunits=None,
                actual_budget_microunits=None,
                initial_requests=-1,
            )

    asyncio.run(scenario())
