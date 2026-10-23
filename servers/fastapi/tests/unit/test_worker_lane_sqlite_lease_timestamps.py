"""SQLite hands lease timestamps back naive; PostgreSQL hands them back aware.

The worker lane compares persisted lease, heartbeat and deadline values against
an aware ``now`` at claim and heartbeat boundaries.  On SQLite that raised
``TypeError: can't compare offset-naive and offset-aware datetimes`` inside the
lease heartbeat.  These tests store naive timestamps directly and drive the
real claim/heartbeat paths against a SQLite database, so every boundary is
proven to go through ``utils.datetime_utils.as_utc``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.public_admission import PublicOperationAdmission, PublicResourceLease
from models.sql.workspace import WorkspaceOperation
from services import presentation_generation_job_service as jobs
from services.workspace_operation_lease import heartbeat_workspace_operation
from utils.datetime_utils import as_utc


async def _database(tmp_path, tables):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'lease.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all, tables=tables)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def test_as_utc_makes_naive_and_aware_values_comparable():
    naive = datetime(2026, 9, 1, 23, 36, 52)
    seoul = datetime(2026, 9, 2, 8, 36, 52, tzinfo=timezone(timedelta(hours=9)))

    assert as_utc(naive) == datetime(2026, 9, 1, 23, 36, 52, tzinfo=timezone.utc)
    assert as_utc(seoul) == as_utc(naive)
    assert as_utc(naive) < as_utc(seoul) + timedelta(seconds=1)


def test_generation_job_lease_takeover_and_heartbeat_with_naive_sqlite_timestamps(
    tmp_path, monkeypatch
):
    async def scenario() -> None:
        engine, maker = await _database(
            tmp_path,
            [
                AsyncPresentationGenerationTaskModel.__table__,
                PresentationGenerationJob.__table__,
                WorkspaceOperation.__table__,
            ],
        )
        monkeypatch.setattr(jobs, "async_session_maker", maker)
        try:
            expired = _naive_utc_now() - timedelta(seconds=1)
            status = AsyncPresentationGenerationTaskModel(
                status="running", message="before the worker died"
            )
            job = PresentationGenerationJob(
                task_id=status.id,
                presentation_id=uuid.uuid4(),
                request_sha256="a" * 64,
                request_payload={"template": "authored"},
                state="running",
                attempt_number=1,
                attempt_token="dead-worker",
                lease_expires_at=expired,
                heartbeat_at=expired,
            )
            async with maker() as session:
                session.add(status)
                session.add(job)
                await session.commit()
            async with maker() as session:
                stored = await session.get(PresentationGenerationJob, job.id)
                assert stored is not None
                assert stored.lease_expires_at.tzinfo is None

            claimed = await jobs.claim_generation_job(job.id, allow_auth_resume=False)
            assert claimed is not None
            task_id, token = claimed
            assert task_id == status.id
            assert token != "dead-worker"
            assert await jobs.heartbeat_generation_job(job.id, token) is True

            async with maker() as session:
                stored = await session.get(PresentationGenerationJob, job.id)
                assert stored is not None
                assert stored.state == "running"
                assert stored.attempt_number == 2
                assert as_utc(stored.lease_expires_at) > datetime.now(timezone.utc)
            # The renewed (still naive on SQLite) lease is live: no takeover.
            assert await jobs.claim_generation_job(job.id, allow_auth_resume=False) is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_workspace_heartbeat_renews_a_public_v2_lease_with_naive_sqlite_deadlines(
    tmp_path, monkeypatch,
):
    from services import workspace_operation_lease

    async def scenario() -> None:
        engine, maker = await _database(
            tmp_path,
            [
                WorkspaceOperation.__table__,
                PublicOperationAdmission.__table__,
                PublicResourceLease.__table__,
            ],
        )
        try:
            now = _naive_utc_now()
            monkeypatch.setattr(workspace_operation_lease, "get_current_utc_datetime", lambda: as_utc(now))
            deadline = now + timedelta(minutes=10)
            attempt_token = "attempt-after-restart"
            owner_id = uuid.uuid4()
            operation = WorkspaceOperation(
                conversation_id=uuid.uuid4(),
                presentation_id=uuid.uuid4(),
                anchor_position=1,
                kind="presentation.generate",
                state="running",
                idempotency_key_hash="b" * 64,
                request_hash="c" * 64,
                contract_version="public-operation/v2",
                execution_profile_version="public-generation-export/v2",
                phase="active",
                deadline_at=deadline,
                attempt_number=2,
                attempt_token=attempt_token,
                fencing_token=2,
                lease_expires_at=now + timedelta(seconds=30),
                heartbeat_at=now,
                created_at=now - timedelta(minutes=1),
                updated_at=now,
            )
            admission = PublicOperationAdmission(
                operation_id=operation.id,
                policy_version="public-v2/test",
                owner_id=owner_id,
                tenant_id="default",
                trace_id_hash="d" * 64,
                operation_kind="generation",
                resource_units=1,
                policy_digest="e" * 64,
                deadline_at=deadline,
                fence=2,
                state="active",
                slide_count=10,
                planned_request_limit=20,
                retry_request_limit=5,
                estimated_cost_limit_microunits=1_000,
                actual_cost_limit_microunits=1_000,
                queued_at=now - timedelta(minutes=1),
                activated_at=now - timedelta(seconds=30),
                updated_at=now,
            )
            leases = [
                PublicResourceLease(
                    admission_id=admission.id,
                    operation_id=operation.id,
                    policy_version="public-v2/test",
                    scope_type=scope_type,
                    scope_key=scope_key,
                    operation_kind="generation",
                    resource_units=1,
                    state="leased",
                    attempt=2,
                    fence=2,
                    lease_owner_hash=sha256(attempt_token.encode("utf-8")).hexdigest(),
                    deadline_at=deadline,
                    reserved_at=now - timedelta(minutes=1),
                    leased_at=now - timedelta(seconds=30),
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(seconds=30),
                    version=1,
                )
                for scope_type, scope_key in (
                    ("global", "global"),
                    ("tenant", "default"),
                    ("owner", str(owner_id)),
                )
            ]
            async with maker() as session:
                session.add(operation)
                session.add(admission)
                session.add_all(leases)
                await session.commit()
            async with maker() as session:
                stored = await session.get(PublicOperationAdmission, admission.id)
                assert stored is not None
                assert stored.deadline_at.tzinfo is None

            assert await heartbeat_workspace_operation(
                operation.id,
                attempt_token,
                fencing_token=2,
                session_factory=maker,
                lease_seconds=60,
            )

            async with maker() as session:
                renewed = await session.get(WorkspaceOperation, operation.id)
                assert renewed is not None
                assert as_utc(renewed.lease_expires_at) > as_utc(now + timedelta(seconds=30))
                assert as_utc(renewed.lease_expires_at) <= as_utc(deadline)
                versions = (
                    await session.execute(
                        select(PublicResourceLease.version).where(
                            PublicResourceLease.admission_id == admission.id
                        )
                    )
                ).scalars().all()
                assert versions == [2, 2, 2]

            for wide_seconds, expected in ((300, now + timedelta(seconds=300)), (1800, deadline)):
                assert await workspace_operation_lease.extend_workspace_operation_lease(
                    operation.id, attempt_token, fencing_token=2,
                    session_factory=maker, lease_seconds=wide_seconds,
                ) == wide_seconds
                assert await heartbeat_workspace_operation(
                    operation.id, attempt_token, fencing_token=2,
                    session_factory=maker, lease_seconds=60,
                )
                async with maker() as session:
                    renewed = await session.get(WorkspaceOperation, operation.id)
                    assert as_utc(renewed.lease_expires_at) == as_utc(expected)
                    expiries = (await session.execute(select(PublicResourceLease.lease_expires_at).where(
                        PublicResourceLease.admission_id == admission.id,
                    ))).scalars().all()
                    assert len(expiries) == 3
                    assert all(as_utc(expiry) == as_utc(expected) for expiry in expiries)

            # A naive deadline that has already passed refuses the renewal
            # instead of crashing the heartbeat loop.
            async with maker() as session:
                passed = _naive_utc_now() - timedelta(seconds=1)
                stored_admission = await session.get(PublicOperationAdmission, admission.id)
                stored_operation = await session.get(WorkspaceOperation, operation.id)
                assert stored_admission is not None and stored_operation is not None
                stored_admission.deadline_at = passed
                stored_operation.deadline_at = passed
                for lease in (
                    await session.execute(
                        select(PublicResourceLease).where(
                            PublicResourceLease.admission_id == admission.id
                        )
                    )
                ).scalars():
                    lease.deadline_at = passed
                    lease.lease_expires_at = None
                await session.commit()

            assert not await heartbeat_workspace_operation(
                operation.id,
                attempt_token,
                fencing_token=2,
                session_factory=maker,
                lease_seconds=60,
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
