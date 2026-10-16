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


def test_header_revision_provider_scopes_cannot_borrow_quality_review_authority(monkeypatch):
    """Reject crossed scopes before a reservation; keep the real review gate strict."""
    from contextlib import asynccontextmanager
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from services import provider_durable_admission as durable
    from services import provider_runtime_job_registration as registration
    from services.provider_runtime_primitives import CancellationRequested

    identity, owner = uuid.uuid4(), uuid.uuid4()
    row = SimpleNamespace(operation_id=identity, owner_id=owner, tenant_id="scope-test", state="active",
        planned_request_limit=1, retry_request_limit=0, initial_request_count=0, retry_request_count=0)
    operation = SimpleNamespace(id=identity, kind="quality.review", owner_id=owner, owner_scope=str(owner),
        state="running", max_attempts=1, attempt_number=1, attempt_token="live-attempt", fencing_token=3,
        cancel_requested_at=None, lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        expected_revision=4, request_payload={"context": {"mode": "analyze_only"}})

    class Session:
        def __init__(self):
            self.calls = 0

        async def scalar(self, query):
            self.calls += 1
            return row if self.calls == 1 else operation

        async def get(self, model, key, **kwargs):
            return operation

    @asynccontextmanager
    async def sessions():
        yield Session()

    monkeypatch.setattr(durable, "async_session_maker", sessions)
    monkeypatch.setattr("services.database.async_session_maker", sessions)
    monkeypatch.setattr(durable, "get_current_owner_id", lambda: owner)
    monkeypatch.setattr("utils.owner_context.get_current_owner_id", lambda: owner)
    monkeypatch.setattr("utils.owner_context.get_current_tenant_id", lambda: "scope-test")
    monkeypatch.setattr("models.sql.workspace.get_workspace_owner_scope", lambda: str(owner))
    reserve_budget = AsyncMock(side_effect=AssertionError("crossed scope reached budget reservation"))
    monkeypatch.setattr(durable, "register_durable_budget", reserve_budget)
    partial = AsyncMock(return_value=None)
    monkeypatch.setattr("services.svg_partial_recovery.runtime_admission", partial)
    phase2 = AsyncMock()
    monkeypatch.setattr("services.public_operation_admission._require_phase2_provider_contract", phase2)

    async def reserve(service, stage):
        return await durable.reserve_provider_call_detached(job_id=identity, workspace_operation_id=identity,
            operation=service, stage=stage, request_id="crossed-scope", estimated_cost=1,
            expected_attempt_token="live-attempt", expected_fencing_token=3)

    async def scenario():
        # A valid review lease cannot authorize document-header authoring.
        with pytest.raises(RuntimeError, match="svg_common_header_runtime_admission_invalid"):
            await reserve("authored_generation", "header")
        # A header operation cannot enter the legacy review-only exception.
        operation.kind = "design.apply"
        operation.request_payload = {"context": {"target": "common_header"}}
        with pytest.raises(RuntimeError, match="public_review_admission_scope_invalid"):
            await reserve("quality_review", "vqa")
        # Unsupported stages must not even enter the funded header validator.
        header_validator = AsyncMock(return_value=row)
        monkeypatch.setattr("services.workspace_svg_header_design_operation_handler.require_header_runtime_admission",
            header_validator)
        with pytest.raises(CancellationRequested, match="provider_workspace_job_scope_invalid"):
            await reserve("authored_generation", "vqa")
        header_validator.assert_not_awaited()
        partial.assert_awaited_once()
        reserve_budget.assert_not_awaited()

        operation.kind = "quality.review"
        operation.request_payload = {"context": {"mode": "analyze_only"}}
        assert await registration.require_public_review_job(Session(), operation_id=identity,
            expected_attempt_token="live-attempt", expected_fencing_token=3,
            expected_operation_kind="quality.review") is row
        phase2.assert_awaited_once()
        # Larger initial or retry quotas are not valid legacy review authority.
        for initial, retries in ((2, 0), (1, 1)):
            row.planned_request_limit, row.retry_request_limit = initial, retries
            with pytest.raises(RuntimeError, match="public_review_admission_scope_invalid"):
                await registration.require_public_review_job(Session(), operation_id=identity,
                    expected_operation_kind="quality.review")
        phase2.assert_awaited_once()

    asyncio.run(scenario())
