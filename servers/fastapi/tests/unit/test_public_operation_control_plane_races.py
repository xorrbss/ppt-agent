"""Race-focused Phase 2 gates.

The concurrent cases in this module deliberately require a real PostgreSQL
authority.  SQLite is useful for pure unit contracts, but cannot demonstrate
row-lock ordering or cluster-wide quota authority.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
import os
import uuid
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import select

import migration_revision_contract as revision_contract
from models.sql.presentation import PresentationModel
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.public_admission import (
    PublicOperationAdmission,
    PublicPolicyProfile,
    PublicProviderLogicalCall,
    PublicQuotaCounter,
    PublicResourceLease,
)
from models.sql.user import User
from models.sql.workspace import (
    WorkspaceConversation,
    WorkspaceOperation,
    WorkspaceOperationEvent,
    WorkspaceOutbox,
)
from services.public_operation_admission import (
    PrincipalContext,
    PublicAdmissionRejected,
    WeightedResourcePlan,
    activate_public_operation_v2,
    admit_public_operation_v2,
    finish_public_operation_v2,
    plan_provider_requests,
    reconcile_public_provider_call,
    requeue_public_operation_v2,
    reserve_public_provider_call,
)
from services.provider_usage_instrumentation import (
    provider_call_admission,
    provider_usage_scope,
    public_operation_scope,
)
from services.workspace_operation_lease import heartbeat_workspace_operation
from services.workspace_operation_handler_registry import WorkspaceOperationResult
import services.workspace_operation_runner as workspace_runner
from utils.datetime_utils import get_current_utc_datetime
from utils.db_utils import to_sync_sqlalchemy_url


_FASTAPI_ROOT = Path(__file__).resolve().parents[2]
_RACE_POLICY_VERSION = "public-race/v1"


def _postgres_url() -> str:
    configured = os.getenv("PPT_AGENT_POSTGRES_TEST_URL")
    if not configured:
        pytest.skip("set PPT_AGENT_POSTGRES_TEST_URL for PostgreSQL race gates")
    if not configured.startswith(("postgresql://", "postgres://")):
        pytest.fail("PPT_AGENT_POSTGRES_TEST_URL must use PostgreSQL")
    database = urlsplit(configured).path.rsplit("/", 1)[-1].split("?", 1)[0]
    if not database.endswith(("test", "race")):
        pytest.fail("race gate requires a disposable database ending in test or race")
    return configured.replace("postgresql://", "postgresql+asyncpg://", 1).replace(
        "postgres://", "postgresql+asyncpg://", 1
    )


def _upgrade_empty_database(url: str) -> None:
    """Run the product Alembic chain, never metadata.create_all, for this gate."""

    config = Config()
    config.set_main_option("script_location", str(_FASTAPI_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", to_sync_sqlalchemy_url(url))
    command.upgrade(config, revision_contract.HEAD_REVISION)


async def _require_empty_database(engine) -> None:
    async with engine.begin() as connection:
        tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
    if tables:
        pytest.fail(
            "disposable race database is not empty; refusing to modify: "
            + ", ".join(sorted(tables))
        )


async def _assert_phase2_migration_authority(engine) -> None:
    """Prove Alembic installed the deferred cross-row authority guards."""

    expected_triggers = {
        "trg_public_admission_operation_consistent",
        "trg_workspace_operation_admission_consistent",
        "trg_public_resource_lease_consistent",
        "trg_public_admission_resource_lease_consistent",
        "trg_public_resource_lease_counter_consistent",
        "trg_public_quota_counter_consistent",
    }
    async with engine.connect() as connection:
        version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        authority_revision = await connection.scalar(
            text(revision_contract.PUBLIC_CONTROL_PLANE_AUTHORITY_PROBE_SQL)
        )
        trigger_rows = await connection.execute(
            text(
                "SELECT tgname, tgdeferrable, tginitdeferred "
                "FROM pg_trigger WHERE NOT tgisinternal AND tgname = ANY(:names)"
            ),
            {"names": list(expected_triggers)},
        )
    installed = {
        row.tgname: (row.tgdeferrable, row.tginitdeferred) for row in trigger_rows
    }
    assert version == revision_contract.HEAD_REVISION
    assert authority_revision == revision_contract.REVISION_PUBLIC_CONTROL_PLANE
    assert installed == {name: (True, True) for name in expected_triggers}


async def _drop_only_the_schema_created_by_this_gate(engine) -> None:
    """Restore a database proven empty before the gate; never touch a reused DB."""

    def drop_reflected_schema(sync_connection) -> None:
        metadata = MetaData()
        metadata.reflect(sync_connection)
        metadata.drop_all(sync_connection)

    async with engine.begin() as connection:
        await connection.run_sync(drop_reflected_schema)


async def _seed(maker: async_sessionmaker, owner_ids: tuple[uuid.UUID, ...]) -> tuple[uuid.UUID, ...]:
    operation_ids: list[uuid.UUID] = []
    async with maker() as session, session.begin():
        session.add(
            PublicPolicyProfile(
                version=_RACE_POLICY_VERSION,
                max_slides=30,
                global_active_generation=2,
                global_queued_generation=4,
                owner_active_generation=1,
                owner_queued_generation=1,
                tenant_active_generation=2,
                tenant_queued_generation=4,
                global_active_export=2,
                global_queued_export=4,
                owner_active_export=1,
                owner_queued_export=1,
                tenant_active_export=2,
                tenant_queued_export=4,
                global_active_resource_units=100,
                global_queued_resource_units=100,
                tenant_active_resource_units=50,
                tenant_queued_resource_units=50,
                owner_active_resource_units=25,
                owner_queued_resource_units=25,
                retry_request_limit=1,
                estimated_cost_limit_microunits=100,
                actual_cost_limit_microunits=100,
                generation_deadline_seconds=900,
                export_deadline_seconds=600,
            )
        )
        users: set[uuid.UUID] = set()
        for owner_id in owner_ids:
            if owner_id not in users:
                users.add(owner_id)
                session.add(User(id=owner_id, username=str(owner_id), hashed_password="test"))
        # PostgreSQL enforces the presentation owner FK at this flush boundary;
        # SQLite's looser insert ordering must not hide the real race-gate schema.
        await session.flush()
        for owner_id in owner_ids:
            presentation = PresentationModel(
                owner_id=owner_id,
                content="race gate",
                n_slides=1,
                language="en",
                mode="authored",
                theme={},
            )
            session.add(presentation)
            await session.flush()
            conversation = WorkspaceConversation(
                owner_id=owner_id,
                owner_scope=str(owner_id),
                presentation_id=presentation.id,
            )
            session.add(conversation)
            await session.flush()
            operation = WorkspaceOperation(
                owner_id=owner_id,
                owner_scope=str(owner_id),
                conversation_id=conversation.id,
                presentation_id=presentation.id,
                anchor_position=1,
                kind="presentation.generate",
                idempotency_key_hash=uuid.uuid4().hex.ljust(64, "0"),
                request_hash=uuid.uuid4().hex.ljust(64, "0"),
                request_payload={},
            )
            session.add(operation)
            await session.flush()
            operation_ids.append(operation.id)
    return tuple(operation_ids)


async def _scenario_postgresql_concurrent_admission_has_zero_cap_overrun() -> None:
    """Alembic-upgraded PostgreSQL keeps cap, cost, deadline and fence coherent."""

    url = _postgres_url()
    engine = create_async_engine(url, pool_size=4, max_overflow=0)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    upgraded = False
    try:
        await _require_empty_database(engine)
        await asyncio.to_thread(_upgrade_empty_database, url)
        upgraded = True
        await _assert_phase2_migration_authority(engine)
        owner = uuid.uuid4()
        operation_ids = await _seed(maker, (owner, owner))
        plan = plan_provider_requests(slide_count=1, image_budget=0, repair_budget=0, vqa_budget=0)
        resource_plan = WeightedResourcePlan(
            pixel_count=1920 * 1080,
            device_pixel_ratio_milli=1000,
            dom_node_count=500,
            font_count=2,
            svg_complexity=0,
            estimated_memory_mib=64,
            fidelity_mode="fidelity",
        )

        async def contender(operation_id: uuid.UUID):
            try:
                async with maker() as session, session.begin():
                    row = await admit_public_operation_v2(
                        session,
                        principal=PrincipalContext(owner, "tenant-race", "trace-race"),
                        operation_id=operation_id,
                        operation_kind="generation",
                        request_plan=plan,
                        resource_plan=resource_plan,
                        policy_version=_RACE_POLICY_VERSION,
                    )
                    return row.id
            except PublicAdmissionRejected as error:
                return error.code

        outcomes = await asyncio.gather(*(contender(item) for item in operation_ids))
        assert sum(isinstance(item, uuid.UUID) for item in outcomes) == 1
        assert any(item == "public_owner_queue_limit_exceeded" for item in outcomes)
        admission_id = next(item for item in outcomes if isinstance(item, uuid.UUID))

        async with maker() as session:
            admission = await session.get(PublicOperationAdmission, admission_id)
            assert admission is not None
            first_fence = admission.fence
            immutable_deadline = admission.deadline_at
            assert first_fence == 1

        # Active → queued recovery advances the fence without extending the absolute deadline.
        async with maker() as session, session.begin():
            active = await activate_public_operation_v2(
                session,
                admission_id=admission_id,
                lease_owner_hash="a" * 64,
                lease_seconds=60,
                fence=first_fence,
            )
            assert active.deadline_at == immutable_deadline

        async with maker() as session, session.begin():
            requeued = await requeue_public_operation_v2(
                session,
                admission_id=admission_id,
                expected_fence=first_fence,
                expected_attempt=1,
            )
            assert requeued.fence == first_fence + 1
            assert requeued.deadline_at == immutable_deadline

        # A stale worker cannot finish or publish after a newer recovery fence exists.
        async with maker() as session, session.begin():
            with pytest.raises(PublicAdmissionRejected) as stale:
                await finish_public_operation_v2(
                    session,
                    admission_id=admission_id,
                    state="succeeded",
                    expected_fence=first_fence,
                    expected_attempt=1,
                )
            assert stale.value.code == "public_operation_fence_conflict"

        # A current worker owns the workspace and all three public leases with
        # one attempt/fence. Heartbeat advances all four rows atomically.
        attempt_token = f"race-attempt-{uuid.uuid4().hex}"
        lease_owner_hash = sha256(attempt_token.encode("utf-8")).hexdigest()
        async with maker() as session, session.begin():
            active = await activate_public_operation_v2(
                session,
                admission_id=admission_id,
                lease_owner_hash=lease_owner_hash,
                lease_seconds=60,
                fence=first_fence + 1,
            )
            assert active.fence == first_fence + 1
            operation = await session.get(WorkspaceOperation, active.operation_id)
            assert operation is not None
            operation.state = "running"
            operation.attempt_number = 2
            operation.attempt_token = attempt_token

        async with maker() as session:
            leases_before = list(
                (
                    await session.execute(
                        select(PublicResourceLease).where(
                            PublicResourceLease.admission_id == admission_id
                        )
                    )
                ).scalars()
            )
            assert len(leases_before) == 3
            lease_versions = {lease.id: lease.version for lease in leases_before}

        assert await heartbeat_workspace_operation(
            active.operation_id,
            attempt_token,
            fencing_token=first_fence + 1,
            session_factory=maker,
            lease_seconds=60,
        )
        assert not await heartbeat_workspace_operation(
            active.operation_id,
            attempt_token,
            fencing_token=first_fence,
            session_factory=maker,
            lease_seconds=60,
        )

        async with maker() as session:
            operation = await session.get(WorkspaceOperation, active.operation_id)
            assert operation is not None and operation.heartbeat_at is not None
            leases_after = list(
                (
                    await session.execute(
                        select(PublicResourceLease).where(
                            PublicResourceLease.admission_id == admission_id
                        )
                    )
                ).scalars()
            )
            assert len(leases_after) == 3
            for lease in leases_after:
                assert lease.version == lease_versions[lease.id] + 1
                assert lease.heartbeat_at is not None
                assert lease.lease_expires_at is not None
                assert lease.lease_expires_at <= immutable_deadline

        # Unknown cost is charged at the reservation, and an actual overrun
        # atomically exhausts the budget.
        async with maker() as session, session.begin():
            call = await reserve_public_provider_call(
                session,
                admission_id=admission_id,
                stage="author",
                slide_index=0,
                logical_call_ordinal=1,
                retry_class="initial",
                estimated_cost_microunits=10,
            )
            receipt = await reconcile_public_provider_call(
                session,
                logical_call_id=call.id,
                outcome="succeeded",
                latency_ms=1,
                actual_cost_microunits=None,
            )
            replay = await reconcile_public_provider_call(
                session,
                logical_call_id=call.id,
                outcome="succeeded",
                latency_ms=1,
                actual_cost_microunits=None,
            )
            assert receipt.id == replay.id

        async with maker() as session, session.begin():
            overrun = await reserve_public_provider_call(
                session,
                admission_id=admission_id,
                stage="outline",
                slide_index=None,
                logical_call_ordinal=2,
                retry_class="initial",
                estimated_cost_microunits=10,
            )
            await reconcile_public_provider_call(
                session,
                logical_call_id=overrun.id,
                outcome="succeeded",
                latency_ms=1,
                actual_cost_microunits=101,
            )
            with pytest.raises(PublicAdmissionRejected) as exhausted:
                await reserve_public_provider_call(
                    session,
                    admission_id=admission_id,
                    stage="content_brief",
                    slide_index=None,
                    logical_call_ordinal=3,
                    retry_class="initial",
                    estimated_cost_microunits=1,
                )
            assert exhausted.value.code == "public_provider_actual_cost_limit_exceeded"
            with pytest.raises(PublicAdmissionRejected) as stale_publication:
                await finish_public_operation_v2(
                    session,
                    admission_id=admission_id,
                    state="succeeded",
                    expected_fence=first_fence + 1,
                    expected_attempt=2,
                )
            assert stale_publication.value.code == (
                "public_budget_terminalization_pending_canonical_operation"
            )

        async with maker() as session:
            admission = await session.get(PublicOperationAdmission, admission_id)
            assert admission is not None
            # Provider accounting is a subordinate ledger: it records the
            # exact overrun but cannot independently terminalize the operation.
            assert admission.state == "active"
            assert admission.deadline_at == immutable_deadline
            assert admission.estimated_reserved_microunits == 0
            assert admission.estimated_consumed_microunits == 20
            assert admission.actual_reserved_microunits == 0
            assert admission.actual_consumed_microunits == 111
            assert admission.actual_cost_unavailable_count == 1
            counters = list(
                (await session.execute(select(PublicQuotaCounter))).scalars()
            )
            assert len(counters) == 3
            for counter in counters:
                assert counter.queued_count == 0
                assert counter.active_count == 1
                assert counter.queued_resource_units == 0
                assert counter.active_resource_units == admission.resource_units
    finally:
        if upgraded:
            await _drop_only_the_schema_created_by_this_gate(engine)
        await engine.dispose()


async def _scenario_postgresql_workspace_claim_is_one_atomic_authority(
    monkeypatch,
) -> None:
    """One claim owns admission, leases, compatibility projection and events."""

    url = _postgres_url()
    engine = create_async_engine(url, pool_size=4, max_overflow=0)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    upgraded = False
    try:
        await _require_empty_database(engine)
        await asyncio.to_thread(_upgrade_empty_database, url)
        upgraded = True
        await _assert_phase2_migration_authority(engine)

        owner_a, owner_b, owner_c, owner_d = (uuid.uuid4() for _ in range(4))
        operation_ids = await _seed(
            maker,
            (owner_a, owner_a, owner_b, owner_c, owner_d),
        )
        blocker_id, full_candidate_id, claimed_id, budget_id, retry_id = operation_ids

        linked: dict[uuid.UUID, tuple[uuid.UUID, str]] = {}
        async with maker() as session, session.begin():
            for operation_id in (full_candidate_id, claimed_id, retry_id):
                operation = await session.get(WorkspaceOperation, operation_id)
                assert operation is not None
                task = AsyncPresentationGenerationTaskModel(
                    owner_id=operation.owner_id,
                    status="pending",
                    message="Queued for generation",
                )
                job = PresentationGenerationJob(
                    task_id=task.id,
                    presentation_id=operation.presentation_id,
                    workspace_operation_id=operation.id,
                    request_sha256="a" * 64,
                    request_payload={"generation_mode": "standard"},
                )
                session.add_all((task, job))
                await session.flush()
                operation.request_payload = {"job_id": str(job.id)}
                linked[operation_id] = (job.id, task.id)

        plan = plan_provider_requests(
            slide_count=1,
            image_budget=0,
            repair_budget=0,
            vqa_budget=0,
        )
        resource_plan = WeightedResourcePlan(
            pixel_count=1920 * 1080,
            device_pixel_ratio_milli=1000,
            dom_node_count=500,
            font_count=2,
            svg_complexity=0,
            estimated_memory_mib=64,
            fidelity_mode="fidelity",
        )

        async def admit(operation_id: uuid.UUID, owner_id: uuid.UUID):
            async with maker() as session, session.begin():
                return await admit_public_operation_v2(
                    session,
                    principal=PrincipalContext(
                        owner_id,
                        "tenant-atomic-claim",
                        f"trace-{operation_id}",
                    ),
                    operation_id=operation_id,
                    operation_kind="generation",
                    request_plan=plan,
                    resource_plan=resource_plan,
                    policy_version=_RACE_POLICY_VERSION,
                )

        blocker = await admit(blocker_id, owner_a)
        blocker_token = f"blocker-{uuid.uuid4().hex}"
        async with maker() as session, session.begin():
            await activate_public_operation_v2(
                session,
                admission_id=blocker.id,
                lease_owner_hash=sha256(blocker_token.encode("utf-8")).hexdigest(),
                lease_seconds=60,
                attempt=1,
                fence=1,
            )
            operation = await session.get(WorkspaceOperation, blocker_id)
            assert operation is not None
            operation.state = "running"
            operation.attempt_number = 1
            operation.attempt_token = blocker_token

        admissions = {
            full_candidate_id: await admit(full_candidate_id, owner_a),
            claimed_id: await admit(claimed_id, owner_b),
            budget_id: await admit(budget_id, owner_c),
            retry_id: await admit(retry_id, owner_d),
        }

        async with maker() as session:
            counters_before_claim = {
                (counter.scope_type, counter.scope_key): (
                    counter.active_count,
                    counter.queued_count,
                    counter.active_resource_units,
                    counter.queued_resource_units,
                    counter.version,
                )
                for counter in (
                    await session.execute(select(PublicQuotaCounter))
                ).scalars()
            }

        claim_outcomes = await asyncio.gather(
            *(
                workspace_runner.claim_next_workspace_operation(
                    session_factory=maker,
                    allowed_kinds={"presentation.generate"},
                    lease_seconds=60,
                )
                for _ in range(2)
            )
        )
        claims = [item for item in claim_outcomes if item is not None]
        assert len(claims) == 1
        claim = claims[0]
        assert claim.operation_id == claimed_id

        async with maker() as session:
            full_operation = await session.get(WorkspaceOperation, full_candidate_id)
            full_admission = await session.get(
                PublicOperationAdmission,
                admissions[full_candidate_id].id,
            )
            assert full_admission is not None
            full_leases = list(
                (
                    await session.execute(
                        select(PublicResourceLease).where(
                            PublicResourceLease.admission_id == full_admission.id
                        )
                    )
                ).scalars()
            )
            full_job = await session.get(
                PresentationGenerationJob,
                linked[full_candidate_id][0],
            )
            full_task = await session.get(
                AsyncPresentationGenerationTaskModel,
                linked[full_candidate_id][1],
            )
            assert full_operation is not None and full_operation.state == "queued"
            assert full_operation.attempt_number == 0
            assert full_operation.attempt_token is None
            assert full_admission.state == "queued"
            assert all(lease.state == "queued" and lease.attempt == 0 for lease in full_leases)
            assert full_job is not None and full_job.state == "queued"
            assert full_task is not None and full_task.status == "pending"
            assert await session.scalar(
                select(WorkspaceOperationEvent).where(
                    WorkspaceOperationEvent.operation_id == full_candidate_id
                ).limit(1)
            ) is None
            assert await session.scalar(
                select(WorkspaceOutbox).where(
                    WorkspaceOutbox.operation_id == full_candidate_id
                ).limit(1)
            ) is None

            counters_after_claim = {
                (counter.scope_type, counter.scope_key): (
                    counter.active_count,
                    counter.queued_count,
                    counter.active_resource_units,
                    counter.queued_resource_units,
                    counter.version,
                )
                for counter in (
                    await session.execute(select(PublicQuotaCounter))
                ).scalars()
            }
            activated_keys = {
                ("global", "global"),
                ("tenant", "tenant-atomic-claim"),
                ("owner", str(owner_b)),
            }
            claimed_units = admissions[claimed_id].resource_units
            assert counters_after_claim.keys() == counters_before_claim.keys()
            for key, before in counters_before_claim.items():
                after = counters_after_claim[key]
                if key in activated_keys:
                    assert after == (
                        before[0] + 1,
                        before[1] - 1,
                        before[2] + claimed_units,
                        before[3] - claimed_units,
                        before[4] + 1,
                    )
                else:
                    assert after == before

            claimed_operation = await session.get(WorkspaceOperation, claimed_id)
            claimed_admission = await session.get(
                PublicOperationAdmission,
                admissions[claimed_id].id,
            )
            claimed_job = await session.get(
                PresentationGenerationJob,
                linked[claimed_id][0],
            )
            claimed_task = await session.get(
                AsyncPresentationGenerationTaskModel,
                linked[claimed_id][1],
            )
            claimed_events = list(
                (
                    await session.execute(
                        select(WorkspaceOperationEvent).where(
                            WorkspaceOperationEvent.operation_id == claimed_id
                        )
                    )
                ).scalars()
            )
            claimed_outbox = list(
                (
                    await session.execute(
                        select(WorkspaceOutbox).where(
                            WorkspaceOutbox.operation_id == claimed_id
                        )
                    )
                ).scalars()
            )
            assert claimed_operation is not None and claimed_operation.state == "running"
            assert claimed_operation.attempt_token == claim.attempt_token
            assert claimed_admission is not None and claimed_admission.state == "active"
            assert claimed_job is not None and claimed_job.state == "running"
            assert claimed_job.attempt_token == claim.attempt_token
            assert claimed_task is not None and claimed_task.status == "running"
            assert len(claimed_events) == len(claimed_outbox) == 1
            assert claimed_events[0].payload["stage"] == "started"

        stale = replace(claim, attempt_token="stale-attempt-token")
        assert not await workspace_runner.finalize_workspace_operation_success(
            stale,
            WorkspaceOperationResult(result_revision=1),
            session_factory=maker,
        )

        async def reject_linked_success(*_args, **_kwargs):
            return False

        with monkeypatch.context() as patch:
            patch.setattr(
                workspace_runner,
                "_project_linked_generation_succeeded",
                reject_linked_success,
            )
            assert not await workspace_runner.finalize_workspace_operation_success(
                claim,
                WorkspaceOperationResult(result_revision=1),
                session_factory=maker,
            )

        async with maker() as session:
            operation = await session.get(WorkspaceOperation, claimed_id)
            admission = await session.get(
                PublicOperationAdmission,
                admissions[claimed_id].id,
            )
            job = await session.get(PresentationGenerationJob, linked[claimed_id][0])
            task = await session.get(
                AsyncPresentationGenerationTaskModel,
                linked[claimed_id][1],
            )
            event_count = await session.scalar(
                select(text("count(*)")).select_from(WorkspaceOperationEvent).where(
                    WorkspaceOperationEvent.operation_id == claimed_id
                )
            )
            outbox_count = await session.scalar(
                select(text("count(*)")).select_from(WorkspaceOutbox).where(
                    WorkspaceOutbox.operation_id == claimed_id
                )
            )
            assert operation is not None and operation.state == "running"
            assert admission is not None and admission.state == "active"
            assert job is not None and job.state == "running"
            assert task is not None and task.status == "running"
            assert event_count == outbox_count == 1

        assert await workspace_runner._transition_claim_failure(
            claim,
            code="atomic_test_cleanup",
            retryable=False,
            session_factory=maker,
        )

        budget_claim = await workspace_runner.claim_next_workspace_operation(
            session_factory=maker,
            allowed_kinds={"presentation.generate"},
            lease_seconds=60,
        )
        assert budget_claim is not None and budget_claim.operation_id == budget_id
        async with maker() as session, session.begin():
            budget_admission = await session.get(
                PublicOperationAdmission,
                admissions[budget_id].id,
            )
            assert budget_admission is not None
            budget_admission.actual_consumed_microunits = (
                budget_admission.actual_cost_limit_microunits
            )
        assert await workspace_runner._transition_claim_failure(
            budget_claim,
            code="transient_worker_failure",
            retryable=True,
            session_factory=maker,
        )
        async with maker() as session:
            operation = await session.get(WorkspaceOperation, budget_id)
            admission = await session.get(
                PublicOperationAdmission,
                admissions[budget_id].id,
            )
            assert operation is not None and operation.state == "failed"
            assert operation.next_attempt_at is None
            assert admission is not None and admission.state == "budget_exhausted"

        retry_claim = await workspace_runner.claim_next_workspace_operation(
            session_factory=maker,
            allowed_kinds={"presentation.generate"},
            lease_seconds=60,
        )
        assert retry_claim is not None and retry_claim.operation_id == retry_id
        assert await workspace_runner.release_workspace_operation_claim(
            retry_claim,
            session_factory=maker,
        )
        async with maker() as session:
            released_operation = await session.get(WorkspaceOperation, retry_id)
            released_job = await session.get(
                PresentationGenerationJob,
                linked[retry_id][0],
            )
            released_task = await session.get(
                AsyncPresentationGenerationTaskModel,
                linked[retry_id][1],
            )
            assert released_operation is not None and released_operation.state == "queued"
            assert released_job is not None and released_job.state == "queued"
            assert released_job.attempt_token is None
            assert released_task is not None and released_task.status == "pending"
        retry_claim = await workspace_runner.claim_next_workspace_operation(
            session_factory=maker,
            allowed_kinds={"presentation.generate"},
            lease_seconds=60,
        )
        assert retry_claim is not None and retry_claim.operation_id == retry_id
        assert await workspace_runner._transition_claim_failure(
            retry_claim,
            code="transient_worker_failure",
            retryable=True,
            session_factory=maker,
        )
        async with maker() as session, session.begin():
            operation = await session.get(WorkspaceOperation, retry_id)
            admission = await session.get(
                PublicOperationAdmission,
                admissions[retry_id].id,
            )
            retry_job = await session.get(
                PresentationGenerationJob,
                linked[retry_id][0],
            )
            retry_task = await session.get(
                AsyncPresentationGenerationTaskModel,
                linked[retry_id][1],
            )
            assert operation is not None and operation.state == "queued"
            assert operation.next_attempt_at is not None
            assert admission is not None and admission.state == "queued"
            assert retry_job is not None and retry_job.state == "queued"
            assert retry_job.attempt_token is None
            assert retry_task is not None and retry_task.status == "pending"
            operation.next_attempt_at = get_current_utc_datetime()

        second_retry_claim = await workspace_runner.claim_next_workspace_operation(
            session_factory=maker,
            allowed_kinds={"presentation.generate"},
            lease_seconds=60,
        )
        assert second_retry_claim is not None
        assert second_retry_claim.operation_id == retry_id
        async with maker() as session, session.begin():
            exhausted_requests = await session.get(
                PublicOperationAdmission,
                admissions[retry_id].id,
            )
            assert exhausted_requests is not None
            exhausted_requests.initial_request_count = (
                exhausted_requests.planned_request_limit
            )
            exhausted_requests.retry_request_count = (
                exhausted_requests.retry_request_limit
            )
        assert await workspace_runner._transition_claim_failure(
            second_retry_claim,
            code="transient_worker_failure",
            retryable=True,
            session_factory=maker,
        )
        async with maker() as session:
            operation = await session.get(WorkspaceOperation, retry_id)
            admission = await session.get(
                PublicOperationAdmission,
                admissions[retry_id].id,
            )
            assert operation is not None and operation.state == "failed"
            assert operation.next_attempt_at is None
            assert operation.error_code == "public_provider_retry_limit_exceeded"
            assert admission is not None and admission.state == "budget_exhausted"
    finally:
        if upgraded:
            await _drop_only_the_schema_created_by_this_gate(engine)
        await engine.dispose()


async def _scenario_postgresql_forced_deadline_terminal_tuple_and_projection_rollback(
    monkeypatch,
) -> None:
    """A queued deadline commits one tuple, while projection failure commits nothing."""

    url = _postgres_url()
    engine = create_async_engine(url, pool_size=2, max_overflow=0)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    upgraded = False
    try:
        await _require_empty_database(engine)
        await asyncio.to_thread(_upgrade_empty_database, url)
        upgraded = True
        await _assert_phase2_migration_authority(engine)

        deadline_owner, rollback_owner, follower_owner = (
            uuid.uuid4() for _ in range(3)
        )
        deadline_id, rollback_id, follower_id = await _seed(
            maker,
            (deadline_owner, rollback_owner, follower_owner),
        )
        linked: dict[uuid.UUID, tuple[uuid.UUID, str]] = {}
        async with maker() as session, session.begin():
            for operation_id in (deadline_id, rollback_id, follower_id):
                operation = await session.get(WorkspaceOperation, operation_id)
                assert operation is not None
                task = AsyncPresentationGenerationTaskModel(
                    owner_id=operation.owner_id,
                    status="pending",
                    message="Queued for generation",
                )
                job = PresentationGenerationJob(
                    task_id=task.id,
                    presentation_id=operation.presentation_id,
                    workspace_operation_id=operation.id,
                    request_sha256=uuid.uuid4().hex * 2,
                    request_payload={"generation_mode": "standard"},
                )
                session.add_all((task, job))
                await session.flush()
                operation.request_payload = {"job_id": str(job.id)}
                linked[operation_id] = (job.id, task.id)

        plan = plan_provider_requests(
            slide_count=1,
            image_budget=0,
            repair_budget=0,
            vqa_budget=0,
        )
        resource_plan = WeightedResourcePlan(
            pixel_count=1920 * 1080,
            device_pixel_ratio_milli=1000,
            dom_node_count=500,
            font_count=2,
            svg_complexity=0,
            estimated_memory_mib=64,
            fidelity_mode="fidelity",
        )
        base_time = get_current_utc_datetime()

        async def admit(
            operation_id: uuid.UUID,
            owner_id: uuid.UUID,
            deadline_offset_seconds: int,
        ) -> PublicOperationAdmission:
            async with maker() as session, session.begin():
                return await admit_public_operation_v2(
                    session,
                    principal=PrincipalContext(
                        owner_id,
                        "tenant-deadline-tuple",
                        f"trace-{operation_id}",
                    ),
                    operation_id=operation_id,
                    operation_kind="generation",
                    request_plan=plan,
                    resource_plan=resource_plan,
                    policy_version=_RACE_POLICY_VERSION,
                    server_hard_deadline_at=(
                        base_time + timedelta(seconds=deadline_offset_seconds)
                    ),
                )

        admissions = {
            deadline_id: await admit(deadline_id, deadline_owner, 60),
            rollback_id: await admit(rollback_id, rollback_owner, 120),
            follower_id: await admit(follower_id, follower_owner, 600),
        }

        async def counter_snapshot() -> dict[tuple[str, str], tuple[int, ...]]:
            async with maker() as session:
                return {
                    (counter.scope_type, counter.scope_key): (
                        counter.active_count,
                        counter.queued_count,
                        counter.active_resource_units,
                        counter.queued_resource_units,
                        counter.version,
                    )
                    for counter in (
                        await session.execute(select(PublicQuotaCounter))
                    ).scalars()
                }

        counters_before_deadline = await counter_snapshot()
        with monkeypatch.context() as patch:
            patch.setattr(
                workspace_runner,
                "_utc_now",
                lambda: base_time + timedelta(seconds=55),
            )
            assert await workspace_runner.claim_next_workspace_operation(
                session_factory=maker,
                allowed_kinds={"presentation.generate"},
                lease_seconds=60,
            ) is None

        async with maker() as session:
            operation = await session.get(WorkspaceOperation, deadline_id)
            admission = await session.get(
                PublicOperationAdmission,
                admissions[deadline_id].id,
            )
            leases = list(
                (
                    await session.execute(
                        select(PublicResourceLease).where(
                            PublicResourceLease.admission_id == admissions[deadline_id].id
                        )
                    )
                ).scalars()
            )
            job = await session.get(PresentationGenerationJob, linked[deadline_id][0])
            task = await session.get(
                AsyncPresentationGenerationTaskModel,
                linked[deadline_id][1],
            )
            events = list(
                (
                    await session.execute(
                        select(WorkspaceOperationEvent).where(
                            WorkspaceOperationEvent.operation_id == deadline_id
                        )
                    )
                ).scalars()
            )
            outbox = list(
                (
                    await session.execute(
                        select(WorkspaceOutbox).where(
                            WorkspaceOutbox.operation_id == deadline_id
                        )
                    )
                ).scalars()
            )
            assert operation is not None
            assert operation.state == operation.phase == "deadline_exceeded"
            assert operation.error_code == "public_deadline_exceeded"
            assert operation.attempt_token is None
            assert operation.next_attempt_at is None
            assert operation.completed_at is not None
            assert admission is not None
            assert admission.state == "deadline_exceeded"
            assert admission.terminal_at is not None
            assert len(leases) == 3
            assert all(
                lease.state == "expired"
                and lease.released_at is not None
                and lease.lease_owner_hash is None
                for lease in leases
            )
            assert job is not None
            assert job.state == "failed"
            assert job.resume_allowed is False
            assert job.attempt_token is None
            assert job.last_error == {"code": "public_deadline_exceeded"}
            assert task is not None
            assert task.status == "error"
            assert task.error is not None
            assert task.error["code"] == "public_deadline_exceeded"
            assert len(events) == len(outbox) == 1
            assert events[0].event_type == "operation.error"
            assert events[0].payload["state"] == "deadline_exceeded"
            assert events[0].payload["retryable"] is False
            assert outbox[0].payload["status"] == "deadline_exceeded"

        counters_after_deadline = await counter_snapshot()
        affected_keys = {
            ("global", "global"),
            ("tenant", "tenant-deadline-tuple"),
            ("owner", str(deadline_owner)),
        }
        released_units = admissions[deadline_id].resource_units
        assert counters_after_deadline.keys() == counters_before_deadline.keys()
        for key, before in counters_before_deadline.items():
            after = counters_after_deadline[key]
            if key in affected_keys:
                assert after == (
                    before[0],
                    before[1] - 1,
                    before[2],
                    before[3] - released_units,
                    before[4] + 1,
                )
            else:
                assert after == before

        counters_before_rollback = await counter_snapshot()

        async def reject_linked_terminal(*_args, **_kwargs):
            return False

        with monkeypatch.context() as patch:
            patch.setattr(
                workspace_runner,
                "_utc_now",
                lambda: base_time + timedelta(seconds=115),
            )
            patch.setattr(
                workspace_runner,
                "_project_linked_generation_terminal",
                reject_linked_terminal,
            )
            assert await workspace_runner.claim_next_workspace_operation(
                session_factory=maker,
                allowed_kinds={"presentation.generate"},
                lease_seconds=60,
            ) is None

        assert await counter_snapshot() == counters_before_rollback
        async with maker() as session:
            for operation_id in (rollback_id, follower_id):
                operation = await session.get(WorkspaceOperation, operation_id)
                admission = await session.get(
                    PublicOperationAdmission,
                    admissions[operation_id].id,
                )
                leases = list(
                    (
                        await session.execute(
                            select(PublicResourceLease).where(
                                PublicResourceLease.admission_id
                                == admissions[operation_id].id
                            )
                        )
                    ).scalars()
                )
                job = await session.get(
                    PresentationGenerationJob,
                    linked[operation_id][0],
                )
                task = await session.get(
                    AsyncPresentationGenerationTaskModel,
                    linked[operation_id][1],
                )
                assert operation is not None and operation.state == "queued"
                assert operation.phase == "admitted"
                assert operation.error_code is None
                assert operation.attempt_token is None
                assert admission is not None and admission.state == "queued"
                assert len(leases) == 3
                assert all(
                    lease.state == "queued"
                    and lease.attempt == 0
                    and lease.released_at is None
                    for lease in leases
                )
                assert job is not None and job.state == "queued"
                assert job.attempt_token is None
                assert task is not None and task.status == "pending"
                assert await session.scalar(
                    select(WorkspaceOperationEvent).where(
                        WorkspaceOperationEvent.operation_id == operation_id
                    ).limit(1)
                ) is None
                assert await session.scalar(
                    select(WorkspaceOutbox).where(
                        WorkspaceOutbox.operation_id == operation_id
                    ).limit(1)
                ) is None
    finally:
        if upgraded:
            await _drop_only_the_schema_created_by_this_gate(engine)
        await engine.dispose()


async def _scenario_postgresql_public_stale_lease_recovery_is_fenced() -> None:
    """Expired public leases requeue once and fence the abandoned attempt."""

    url = _postgres_url()
    engine = create_async_engine(url, pool_size=2, max_overflow=0)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    upgraded = False
    try:
        await _require_empty_database(engine)
        await asyncio.to_thread(_upgrade_empty_database, url)
        upgraded = True
        await _assert_phase2_migration_authority(engine)

        owner_id = uuid.uuid4()
        (operation_id,) = await _seed(maker, (owner_id,))
        async with maker() as session, session.begin():
            operation = await session.get(WorkspaceOperation, operation_id)
            assert operation is not None
            task = AsyncPresentationGenerationTaskModel(
                owner_id=owner_id,
                status="pending",
                message="Queued for generation",
            )
            job = PresentationGenerationJob(
                task_id=task.id,
                presentation_id=operation.presentation_id,
                workspace_operation_id=operation.id,
                request_sha256=uuid.uuid4().hex * 2,
                request_payload={"generation_mode": "standard"},
            )
            session.add_all((task, job))
            await session.flush()
            operation.request_payload = {"job_id": str(job.id)}
            job_id, task_id = job.id, task.id

        async with maker() as session, session.begin():
            admission = await admit_public_operation_v2(
                session,
                principal=PrincipalContext(
                    owner_id,
                    "tenant-stale-recovery",
                    f"trace-{operation_id}",
                ),
                operation_id=operation_id,
                operation_kind="generation",
                request_plan=plan_provider_requests(
                    slide_count=1,
                    image_budget=0,
                    repair_budget=0,
                    vqa_budget=0,
                ),
                resource_plan=WeightedResourcePlan(
                    pixel_count=1920 * 1080,
                    device_pixel_ratio_milli=1000,
                    dom_node_count=500,
                    font_count=2,
                    svg_complexity=0,
                    estimated_memory_mib=64,
                    fidelity_mode="fidelity",
                ),
                policy_version=_RACE_POLICY_VERSION,
            )
            admission_id = admission.id
            resource_units = admission.resource_units

        stale_claim = await workspace_runner.claim_next_workspace_operation(
            session_factory=maker,
            allowed_kinds={"presentation.generate"},
            lease_seconds=60,
        )
        assert stale_claim is not None
        assert stale_claim.operation_id == operation_id
        assert stale_claim.attempt_number == 1
        assert stale_claim.fencing_token == 1

        expired_at = get_current_utc_datetime() - timedelta(seconds=10)
        async with maker() as session, session.begin():
            operation = await session.get(WorkspaceOperation, operation_id)
            assert operation is not None
            operation.lease_expires_at = expired_at
            operation.heartbeat_at = expired_at
            leases = list(
                (
                    await session.execute(
                        select(PublicResourceLease).where(
                            PublicResourceLease.admission_id == admission_id
                        )
                    )
                ).scalars()
            )
            assert len(leases) == 3
            for lease in leases:
                lease.lease_expires_at = expired_at
                lease.heartbeat_at = expired_at

        assert await workspace_runner.recover_expired_workspace_operations(
            session_factory=maker
        ) == 1

        async with maker() as session, session.begin():
            operation = await session.get(WorkspaceOperation, operation_id)
            admission = await session.get(PublicOperationAdmission, admission_id)
            leases = list(
                (
                    await session.execute(
                        select(PublicResourceLease).where(
                            PublicResourceLease.admission_id == admission_id
                        )
                    )
                ).scalars()
            )
            counters = list(
                (await session.execute(select(PublicQuotaCounter))).scalars()
            )
            job = await session.get(PresentationGenerationJob, job_id)
            task = await session.get(
                AsyncPresentationGenerationTaskModel,
                task_id,
            )
            events = list(
                (
                    await session.execute(
                        select(WorkspaceOperationEvent)
                        .where(WorkspaceOperationEvent.operation_id == operation_id)
                        .order_by(WorkspaceOperationEvent.sequence)
                    )
                ).scalars()
            )
            outbox = list(
                (
                    await session.execute(
                        select(WorkspaceOutbox).where(
                            WorkspaceOutbox.operation_id == operation_id
                        )
                    )
                ).scalars()
            )
            assert operation is not None
            assert operation.state == "queued"
            assert operation.phase == "queued"
            assert operation.attempt_number == 1
            assert operation.fencing_token == 2
            assert operation.attempt_token is None
            assert operation.lease_expires_at is None
            assert operation.retry_count == 1
            assert operation.next_attempt_at is not None
            assert admission is not None
            assert admission.state == "queued"
            assert admission.fence == 2
            assert len(leases) == 3
            assert all(
                lease.state == "queued"
                and lease.attempt == 1
                and lease.fence == 2
                and lease.lease_owner_hash is None
                and lease.lease_expires_at == admission.deadline_at
                for lease in leases
            )
            assert len(counters) == 3
            assert all(
                counter.active_count == 0
                and counter.queued_count == 1
                and counter.active_resource_units == 0
                and counter.queued_resource_units == resource_units
                for counter in counters
            )
            assert job is not None
            assert job.state == "queued"
            assert job.attempt_token is None
            assert task is not None and task.status == "pending"
            assert [event.event_type for event in events] == [
                "operation.progress",
                "operation.error",
            ]
            assert len(outbox) == len(events) == 2
            operation.next_attempt_at = get_current_utc_datetime()

        fresh_claim = await workspace_runner.claim_next_workspace_operation(
            session_factory=maker,
            allowed_kinds={"presentation.generate"},
            lease_seconds=60,
        )
        assert fresh_claim is not None
        assert fresh_claim.operation_id == operation_id
        assert fresh_claim.attempt_number == 2
        assert fresh_claim.fencing_token == 2
        assert fresh_claim.attempt_token != stale_claim.attempt_token

        assert not await heartbeat_workspace_operation(
            operation_id,
            stale_claim.attempt_token,
            fencing_token=stale_claim.fencing_token,
            session_factory=maker,
            lease_seconds=60,
        )
        assert not await workspace_runner.finalize_workspace_operation_success(
            stale_claim,
            WorkspaceOperationResult(result_revision=1),
            session_factory=maker,
        )

        async with maker() as session:
            operation = await session.get(WorkspaceOperation, operation_id)
            admission = await session.get(PublicOperationAdmission, admission_id)
            leases = list(
                (
                    await session.execute(
                        select(PublicResourceLease).where(
                            PublicResourceLease.admission_id == admission_id
                        )
                    )
                ).scalars()
            )
            job = await session.get(PresentationGenerationJob, job_id)
            task = await session.get(
                AsyncPresentationGenerationTaskModel,
                task_id,
            )
            assert operation is not None and operation.state == "running"
            assert operation.attempt_number == 2
            assert operation.fencing_token == 2
            assert operation.attempt_token == fresh_claim.attempt_token
            assert admission is not None
            assert admission.state == "active"
            assert admission.fence == 2
            assert len(leases) == 3
            assert all(
                lease.state == "leased"
                and lease.attempt == 2
                and lease.fence == 2
                and lease.lease_owner_hash
                == sha256(fresh_claim.attempt_token.encode("utf-8")).hexdigest()
                for lease in leases
            )
            assert job is not None and job.state == "running"
            assert job.attempt_number == 2
            assert job.attempt_token == fresh_claim.attempt_token
            assert task is not None and task.status == "running"
    finally:
        if upgraded:
            await _drop_only_the_schema_created_by_this_gate(engine)
        await engine.dispose()


async def _scenario_postgresql_authority_failure_is_fail_closed() -> None:
    """A DB authority outage must reject admission, never start work locally."""

    # get_bind is checked before any local fallback can begin work.
    class BrokenSession:
        def get_bind(self):
            from sqlalchemy import create_engine

            return create_engine("sqlite://")

        async def scalar(self, *_args, **_kwargs):
            raise AssertionError("get_bind must fail closed before scalar")

    with pytest.raises(PublicAdmissionRejected) as rejected:
        await admit_public_operation_v2(
            BrokenSession(),
            principal=PrincipalContext(uuid.uuid4(), "tenant-race", "trace-race"),
            operation_id=uuid.uuid4(),
            operation_kind="generation",
            request_plan=plan_provider_requests(
                slide_count=1, image_budget=0, repair_budget=0, vqa_budget=0
            ),
            resource_plan=WeightedResourcePlan(
                pixel_count=1,
                device_pixel_ratio_milli=1000,
                dom_node_count=1,
                font_count=1,
                svg_complexity=0,
                estimated_memory_mib=1,
                fidelity_mode="fidelity",
            ),
        )
    assert rejected.value.code == "public_postgresql_authority_required"


def test_postgresql_concurrent_admission_has_zero_cap_overrun() -> None:
    asyncio.run(_scenario_postgresql_concurrent_admission_has_zero_cap_overrun())


def test_postgresql_workspace_claim_is_one_atomic_authority(monkeypatch) -> None:
    asyncio.run(
        _scenario_postgresql_workspace_claim_is_one_atomic_authority(monkeypatch)
    )


def test_postgresql_forced_deadline_terminal_tuple_and_projection_rollback(
    monkeypatch,
) -> None:
    asyncio.run(
        _scenario_postgresql_forced_deadline_terminal_tuple_and_projection_rollback(
            monkeypatch
        )
    )


def test_postgresql_public_stale_lease_recovery_is_fenced() -> None:
    asyncio.run(_scenario_postgresql_public_stale_lease_recovery_is_fenced())


def test_postgresql_authority_failure_is_fail_closed() -> None:
    asyncio.run(_scenario_postgresql_authority_failure_is_fail_closed())


def test_public_authority_failure_starts_no_provider_or_browser(monkeypatch) -> None:
    """The public provider gate fails before the SDK/browser context body runs."""

    async def scenario() -> None:
        starts = {"provider_sdk": 0, "browser": 0}

        async def reject_public_reservation(**_kwargs):
            raise PublicAdmissionRejected("public_postgresql_authority_required")

        monkeypatch.setattr(
            "services.public_operation_admission.reserve_public_provider_call_detached",
            reject_public_reservation,
        )
        with public_operation_scope(uuid.uuid4()):
            with provider_usage_scope(
                job_id=uuid.uuid4(),
                operation="authored_generation",
                stage="author",
            ):
                with pytest.raises(PublicAdmissionRejected) as rejected:
                    async with provider_call_admission(estimated_cost_microunits=1):
                        starts["provider_sdk"] += 1
                        starts["browser"] += 1
                assert rejected.value.code == "public_postgresql_authority_required"
        assert starts == {"provider_sdk": 0, "browser": 0}

    asyncio.run(scenario())
