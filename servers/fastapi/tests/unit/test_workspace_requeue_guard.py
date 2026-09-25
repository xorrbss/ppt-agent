"""A rejected requeue terminalizes the attempt; nothing escapes a runner task.

The capacity trigger that rejects a requeue in production
(``public_guard_quota_counter_capacity``, PostgreSQL only) is reproduced here
by running the real requeue and then raising the trigger's error at the point
the trigger raises it, after the counter rows were changed in the session.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import uuid

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError
from sqlmodel import SQLModel

import test_public_generation_deadline_policy as policy_test
import test_workspace_operation_runner as runner_test
from models.sql.public_admission import (
    PublicOperationAdmission,
    PublicQuotaCounter,
    PublicResourceLease,
)
from models.sql.workspace import WorkspaceOperation
from services import workspace_operation_runner as runner
from services.public_operation_admission import (
    PUBLIC_PHASE2_DEFAULT_POLICY_VERSION,
    PrincipalContext,
    activate_public_operation_v2,
    admit_public_operation_compatible_v2,
    requeue_public_operation_v2,
)
from services.workspace_operation_handler_registry import (
    WorkspaceOperationClaim,
    WorkspaceOperationHandlerRegistry,
)
from services.workspace_requeue_guard import requeue_rejection_code

TENANT = "tenant-requeue"
CAPACITY_CODE = "public_quota_capacity_exceeded"


@pytest.fixture(autouse=True)
def _sqlite_phase2_authority(monkeypatch):
    # SQLite is a deterministic contract fixture; PostgreSQL authority is
    # covered by the control-plane race gate.
    monkeypatch.setattr(
        "services.public_operation_admission._require_phase2_authority",
        lambda _session: PublicResourceLease,
    )


async def _database(path: Path):
    engine, maker = await policy_test._database(path)
    tables = [table for table in runner_test.RUNNER_TABLES if table not in policy_test.TABLES]
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: SQLModel.metadata.create_all(sync, tables=tables))
    return engine, maker


async def _active_export(maker) -> tuple[WorkspaceOperationClaim, uuid.UUID]:
    """An export whose admission is active and whose attempt is running."""

    owner_id = uuid.uuid4()
    async with maker() as session, session.begin():
        session.add(policy_test.User(id=owner_id, username=f"requeue-{owner_id}", hashed_password="x"))
        session.add(policy_test._policy(PUBLIC_PHASE2_DEFAULT_POLICY_VERSION, generation_deadline_seconds=3600))
    operation_id = await policy_test._seed_operation(maker, owner_id, key="e")
    async with maker() as session, session.begin():
        operation = await session.get(WorkspaceOperation, operation_id)
        operation.kind = "export.pptx"
        admitted = await admit_public_operation_compatible_v2(
            session, principal=PrincipalContext(owner_id, TENANT, "trace-requeue"),
            operation_id=operation_id, operation_kind="export",
            request_plan=None,
            resource_plan=policy_test._RESOURCE_PLAN,
        )
        active = await activate_public_operation_v2(
            session, admission_id=admitted.id, lease_owner_hash="c" * 64, lease_seconds=600)
        operation = await session.get(WorkspaceOperation, operation_id)
        operation.state = "running"
        operation.attempt_token = "attempt-1"
        operation.attempt_number = 1
        operation.fencing_token = active.fence
        claim = WorkspaceOperationClaim(
            operation_id=operation_id, owner_id=owner_id, owner_scope=str(owner_id),
            conversation_id=operation.conversation_id, presentation_id=operation.presentation_id,
            kind="export.pptx", request_payload={}, expected_revision=None, attempt_number=1,
            attempt_token="attempt-1", fencing_token=active.fence,
        )
    return claim, admitted.id


async def _counters(maker, owner_id) -> dict[str, tuple[int, int, int, int]]:
    async with maker() as session:
        rows = (await session.scalars(select(PublicQuotaCounter))).all()
    return {
        row.scope_type: (row.active_count, row.queued_count, row.active_resource_units, row.queued_resource_units)
        for row in rows
        if row.scope_type != "owner" or row.scope_key == str(owner_id)
    }


def _trigger_rejects_after_the_real_requeue(monkeypatch) -> list[int]:
    calls: list[int] = []

    async def requeue_then_trigger(session, **kwargs):
        calls.append(1)
        await requeue_public_operation_v2(session, **kwargs)
        raise DBAPIError("UPDATE public_quota_counters", {}, Exception(CAPACITY_CODE))

    monkeypatch.setattr(runner, "requeue_public_operation_v2", requeue_then_trigger)
    return calls


def test_requeue_moves_the_reservation_between_buckets_without_charging_twice(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "move.db")
        try:
            claim, admission_id = await _active_export(maker)
            before = await _counters(maker, claim.owner_id)
            async with maker() as session, session.begin():
                await requeue_public_operation_v2(
                    session, admission_id=admission_id, expected_fence=claim.fencing_token)
            after = await _counters(maker, claim.owner_id)
            for scope, (active, queued, active_units, queued_units) in before.items():
                moved_active, moved_queued, moved_active_units, moved_queued_units = after[scope]
                assert (moved_active, moved_queued) == (active - 1, queued + 1)
                assert moved_active_units + moved_queued_units == active_units + queued_units
            # The queue bucket is what a full owner queue rejects: the move
            # takes a queued slot that newer admissions may already hold.
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_rejected_requeue_terminalizes_and_releases_the_reservation(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "rejected.db")
        try:
            claim, admission_id = await _active_export(maker)
            before = await _counters(maker, claim.owner_id)
            calls = _trigger_rejects_after_the_real_requeue(monkeypatch)
            assert await runner._transition_claim_failure(
                claim, code="public_export_worker_failed", retryable=True, session_factory=maker)
            assert calls == [1]
            async with maker() as session:
                operation = await session.get(WorkspaceOperation, claim.operation_id)
                admission = await session.get(PublicOperationAdmission, admission_id)
            assert operation.state == "failed"
            assert operation.error_code == "public_export_worker_failed"  # the real failure
            assert operation.error_detail["requeue_rejected_code"] == CAPACITY_CODE
            assert operation.error_detail["retryable"] is False
            assert operation.attempt_token is None and operation.next_attempt_at is None
            assert admission.state == "failed"
            after = await _counters(maker, claim.owner_id)
            for scope, (active, queued, active_units, queued_units) in before.items():
                assert after[scope] == (active - 1, queued, active_units - admission.resource_units, queued_units)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_lease_recovery_terminalizes_when_the_requeue_is_rejected(tmp_path: Path, monkeypatch) -> None:
    """The M0 shape: the worker died holding the active slot; recovery used to
    raise on every sweep, so the owner's queued operations never started."""

    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "recovery.db")
        try:
            claim, admission_id = await _active_export(maker)
            async with maker() as session, session.begin():
                await session.execute(
                    update(WorkspaceOperation).where(WorkspaceOperation.id == claim.operation_id)
                    .values(lease_expires_at=runner._utc_now() - runner.timedelta(seconds=5))
                    .execution_options(skip_owner_scope=True))
            calls = _trigger_rejects_after_the_real_requeue(monkeypatch)
            registry = WorkspaceOperationHandlerRegistry()
            registry.register("export.pptx", lambda _context: None, recovery_policy="automatic")
            assert await runner.recover_expired_workspace_operations(session_factory=maker, registry=registry) == 1
            assert calls == [1]
            async with maker() as session:
                operation = await session.get(WorkspaceOperation, claim.operation_id)
                admission = await session.get(PublicOperationAdmission, admission_id)
            assert (operation.state, operation.error_code) == ("failed", CAPACITY_CODE)
            assert admission.state == "failed"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_a_failing_transition_never_escapes_the_runner_task(tmp_path: Path, monkeypatch, caplog) -> None:
    async def scenario() -> None:
        engine, maker = await runner_test._database(tmp_path / "guard.db")
        try:
            _, (operation_id,) = await runner_test._seed_operations(maker, owner_id=runner_test.OWNER_ID)
            original = runner._transition_claim_failure
            seen: list[str] = []

            async def broken_once(claim, *, code, **kwargs):
                seen.append(code)
                if len(seen) == 1:
                    raise RuntimeError("transition exploded")
                return await original(claim, code=code, **kwargs)

            monkeypatch.setattr(runner, "_transition_claim_failure", broken_once)

            async def handler(_context):
                raise RuntimeError("handler failed")

            registry = WorkspaceOperationHandlerRegistry()
            registry.register("test.operation", handler)
            worker = runner.WorkspaceOperationRunner(
                session_factory=maker, registry=registry, max_concurrency=1, heartbeat_seconds=60)
            assert await worker.dispatch_once() == 1
            tasks = tuple(worker._active.values())
            results = await asyncio.gather(*tasks, return_exceptions=True)
            assert results == [None]  # the task itself did not die
            operation = await runner_test._operation(maker, operation_id)
            assert seen == ["workspace_operation_internal_error", "workspace_operation_transition_failed"]
            assert operation.state == "failed"
            assert operation.error_code == "workspace_operation_transition_failed"
        finally:
            await engine.dispose()

    asyncio.run(scenario())
    assert any(
        "workspace_operation_transition_failed" in record.getMessage() for record in caplog.records
    )


def test_rejection_codes_come_from_admission_errors_and_trigger_messages() -> None:
    from services.public_operation_admission_models import PublicAdmissionRejected

    assert requeue_rejection_code(PublicAdmissionRejected("public_operation_fence_conflict")) == (
        "public_operation_fence_conflict")
    assert requeue_rejection_code(DBAPIError("UPDATE", {}, Exception(CAPACITY_CODE))) == CAPACITY_CODE
    assert requeue_rejection_code(DBAPIError("UPDATE", {}, Exception("connection reset"))) is None
    assert requeue_rejection_code(RuntimeError(CAPACITY_CODE)) is None
