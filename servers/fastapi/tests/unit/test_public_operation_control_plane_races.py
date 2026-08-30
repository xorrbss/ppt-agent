"""Race-focused Phase 2 gates.

The concurrent cases in this module deliberately require a real PostgreSQL
authority.  SQLite is useful for pure unit contracts, but cannot demonstrate
row-lock ordering or cluster-wide quota authority.
"""

from __future__ import annotations

import asyncio
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
from models.sql.public_admission import (
    PublicOperationAdmission,
    PublicPolicyProfile,
    PublicProviderLogicalCall,
    PublicQuotaCounter,
    PublicResourceLease,
)
from models.sql.user import User
from models.sql.workspace import WorkspaceConversation, WorkspaceOperation
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
            assert stale_publication.value.code == "public_operation_terminal_conflict"

        async with maker() as session:
            admission = await session.get(PublicOperationAdmission, admission_id)
            assert admission is not None
            assert admission.state == "budget_exhausted"
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
                assert counter.active_count == 0
                assert counter.queued_resource_units == 0
                assert counter.active_resource_units == 0
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
