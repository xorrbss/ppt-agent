from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

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
    admit_public_operation_v2,
    activate_public_operation,
    activate_public_operation_v2,
    admit_public_operation,
    enrich_public_provider_call,
    finish_public_operation,
    finish_public_operation_v2,
    plan_provider_requests,
    public_operation_metrics,
    public_quota_metrics,
    reconcile_public_provider_call,
    requeue_public_operation_v2,
    reserve_public_provider_call,
)


def test_weighted_resource_plan_is_deterministic_and_mode_weighted() -> None:
    """Admission estimates are integer-stable and Hybrid costs more than Fidelity."""

    base = dict(
        pixel_count=1920 * 1080,
        device_pixel_ratio_milli=1000,
        dom_node_count=2500,
        font_count=8,
        svg_complexity=500,
        estimated_memory_mib=256,
    )
    fidelity = WeightedResourcePlan(**base, fidelity_mode="fidelity")
    hybrid = WeightedResourcePlan(**base, fidelity_mode="hybrid")
    assert fidelity.resource_units == WeightedResourcePlan(
        **base, fidelity_mode="fidelity"
    ).resource_units
    assert hybrid.resource_units > fidelity.resource_units
    with pytest.raises(ValueError, match="public_fidelity_mode_invalid"):
        WeightedResourcePlan(**base, fidelity_mode="unknown").resource_units


TABLES = [
    User.__table__,
    PresentationModel.__table__,
    WorkspaceConversation.__table__,
    WorkspaceOperation.__table__,
    PublicPolicyProfile.__table__,
    PublicQuotaCounter.__table__,
    PublicOperationAdmission.__table__,
    PublicProviderLogicalCall.__table__,
    PublicResourceLease.__table__,
]


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_safety(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection, tables=TABLES
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_public_admission_enforces_principal_quota_and_provider_hard_caps(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "public-admission.db")
        owner_a = uuid.uuid4()
        owner_b = uuid.uuid4()
        operation_ids: list[uuid.UUID] = []
        try:
            async with maker() as session, session.begin():
                session.add_all(
                    [
                        User(id=owner_a, username="owner-a", hashed_password="test"),
                        User(id=owner_b, username="owner-b", hashed_password="test"),
                        PublicPolicyProfile(
                            version="public-default/v1",
                            max_slides=30,
                            global_active_generation=4,
                            global_queued_generation=8,
                            owner_active_generation=1,
                            owner_queued_generation=1,
                            tenant_active_generation=2,
                            tenant_queued_generation=3,
                            global_active_export=4,
                            global_queued_export=8,
                            owner_active_export=1,
                            owner_queued_export=1,
                            tenant_active_export=2,
                            tenant_queued_export=3,
                            global_active_resource_units=100,
                            global_queued_resource_units=100,
                            tenant_active_resource_units=50,
                            tenant_queued_resource_units=50,
                            owner_active_resource_units=25,
                            owner_queued_resource_units=25,
                            retry_request_limit=1,
                            estimated_cost_limit_microunits=13,
                            actual_cost_limit_microunits=12,
                            generation_deadline_seconds=900,
                            export_deadline_seconds=600,
                        ),
                    ]
                )
                for index, owner_id in enumerate((owner_a, owner_a, owner_b), start=1):
                    presentation = PresentationModel(
                        owner_id=owner_id,
                        content="bounded public generation",
                        n_slides=2,
                        language="ko",
                        mode="authored",
                        theme={"mode": "authored"},
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
                        idempotency_key_hash=f"{index:064x}",
                        request_hash=f"{index + 10:064x}",
                        request_payload={},
                    )
                    session.add(operation)
                    await session.flush()
                    operation_ids.append(operation.id)

            principal_a = PrincipalContext(owner_a, "tenant-a", "trace-a")
            principal_b = PrincipalContext(owner_b, "tenant-a", "trace-b")
            plan = plan_provider_requests(
                slide_count=2,
                image_budget=1,
                repair_budget=1,
                vqa_budget=1,
            )
            assert plan.initial_request_limit == 9
            assert plan.retry_request_limit == 2
            five_slide_plan = plan_provider_requests(
                slide_count=5,
                image_budget=5,
                repair_budget=5,
                vqa_budget=0,
            )
            assert five_slide_plan.initial_request_limit == 19
            assert five_slide_plan.retry_request_limit == 5

            async with maker() as session, session.begin():
                first = await admit_public_operation(
                    session,
                    principal=principal_a,
                    operation_id=operation_ids[0],
                    operation_kind="generation",
                    request_plan=plan,
                )
                assert first.planned_request_limit == 9
                assert first.retry_request_limit == 3
                with pytest.raises(PublicAdmissionRejected) as rejected:
                    await admit_public_operation(
                        session,
                        principal=principal_a,
                        operation_id=operation_ids[1],
                        operation_kind="generation",
                        request_plan=plan,
                    )
                assert rejected.value.code == "public_owner_queue_limit_exceeded"

            async with maker() as session, session.begin():
                first = await activate_public_operation(session, first.id)
                second = await admit_public_operation(
                    session,
                    principal=principal_a,
                    operation_id=operation_ids[1],
                    operation_kind="generation",
                    request_plan=plan,
                )
                third = await admit_public_operation(
                    session,
                    principal=principal_b,
                    operation_id=operation_ids[2],
                    operation_kind="generation",
                    request_plan=plan,
                )
                with pytest.raises(PublicAdmissionRejected) as rejected:
                    await activate_public_operation(session, second.id)
                assert rejected.value.code == "public_owner_active_limit_exceeded"
                await activate_public_operation(session, third.id)

            async with maker() as session, session.begin():
                outline = await reserve_public_provider_call(
                    session,
                    admission_id=first.id,
                    stage="outline",
                    slide_index=None,
                    logical_call_ordinal=1,
                    retry_class="initial",
                    estimated_cost_microunits=10,
                )
                replay = await reserve_public_provider_call(
                    session,
                    admission_id=first.id,
                    stage="outline",
                    slide_index=None,
                    logical_call_ordinal=1,
                    retry_class="initial",
                    estimated_cost_microunits=10,
                )
                assert replay.id == outline.id
                await reconcile_public_provider_call(
                    session,
                    logical_call_id=outline.id,
                    outcome="succeeded",
                    latency_ms=100,
                    actual_cost_microunits=None,
                )
                await enrich_public_provider_call(
                    session,
                    logical_call_id=outline.id,
                    latency_ms=110,
                    actual_cost_microunits=None,
                    input_tokens=100,
                    output_tokens=50,
                )
                accounting = await session.get(PublicOperationAdmission, first.id)
                assert accounting is not None
                assert accounting.actual_consumed_microunits == 10
                assert accounting.actual_cost_unavailable_count == 1
                await enrich_public_provider_call(
                    session,
                    logical_call_id=outline.id,
                    latency_ms=125,
                    actual_cost_microunits=11,
                    input_tokens=100,
                    output_tokens=50,
                )
                await session.refresh(accounting)
                assert accounting.actual_cost_unavailable_count == 0
                conservative = await reserve_public_provider_call(
                    session,
                    admission_id=first.id,
                    stage="author",
                    slide_index=0,
                    logical_call_ordinal=2,
                    retry_class="initial",
                    estimated_cost_microunits=1,
                )
                await reconcile_public_provider_call(
                    session,
                    logical_call_id=conservative.id,
                    outcome="failed",
                    latency_ms=125,
                    actual_cost_microunits=None,
                )
                with pytest.raises(PublicAdmissionRejected) as rejected:
                    await reserve_public_provider_call(
                        session,
                        admission_id=first.id,
                        stage="author",
                        slide_index=0,
                        logical_call_ordinal=3,
                        retry_class="initial",
                        estimated_cost_microunits=2,
                    )
                assert rejected.value.code == "public_provider_actual_cost_limit_exceeded"
                operation_metrics = await public_operation_metrics(session, first.id)
                quota_metrics = await public_quota_metrics(
                    session,
                    principal=principal_a,
                    operation_kind="generation",
                )
                assert operation_metrics["initial_request_count"] == 2
                assert operation_metrics["actual_consumed_microunits"] == 12
                assert operation_metrics["actual_cost_unavailable_count"] == 1
                assert all(isinstance(value, int) for value in operation_metrics.values())
                assert all(isinstance(value, int) for value in quota_metrics.values())
                await finish_public_operation(session, first.id, state="succeeded")
                await activate_public_operation(session, second.id)

            forbidden_columns = {
                "cookie",
                "session",
                "prompt",
                "response",
                "request_payload",
                "provider_key",
            }
            persisted_columns = {
                column.name
                for table in (
                    PublicOperationAdmission.__table__,
                    PublicProviderLogicalCall.__table__,
                )
                for column in table.columns
            }
            assert forbidden_columns.isdisjoint(persisted_columns)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_phase2_sqlite_lifecycle_is_contract_only(monkeypatch, tmp_path: Path) -> None:
    """Exercise lifecycle fences; SQLite is not evidence of PostgreSQL authority."""

    # Explicitly bypass the dialect guard so this remains a deterministic unit
    # contract test. PostgreSQL locking/authority is covered only by the race gate.
    monkeypatch.setattr(
        "services.public_operation_admission._require_phase2_authority",
        lambda _session: PublicResourceLease,
    )

    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "public-phase2-lifecycle.db")
        owner_id = uuid.uuid4()
        principal = PrincipalContext(owner_id, "tenant-phase2", "trace-phase2")
        try:
            async with maker() as session, session.begin():
                session.add(User(id=owner_id, username="phase2-owner", hashed_password="test"))
                session.add(
                    PublicPolicyProfile(
                        version="public-default/v1",
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
                presentation = PresentationModel(
                    owner_id=owner_id,
                    content="phase2 lifecycle",
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
                    idempotency_key_hash="a" * 64,
                    request_hash="b" * 64,
                    request_payload={},
                )
                session.add(operation)
                await session.flush()
                operation_id = operation.id

            resource_plan = WeightedResourcePlan(
                pixel_count=1,
                device_pixel_ratio_milli=1000,
                dom_node_count=1,
                font_count=1,
                svg_complexity=0,
                estimated_memory_mib=1,
                fidelity_mode="fidelity",
            )
            fixed_deadline = datetime.now(timezone.utc) + timedelta(seconds=120)
            async with maker() as session, session.begin():
                admitted = await admit_public_operation_v2(
                    session,
                    principal=principal,
                    operation_id=operation_id,
                    operation_kind="generation",
                    request_plan=plan_provider_requests(
                        slide_count=1, image_budget=0, repair_budget=0, vqa_budget=0
                    ),
                    resource_plan=resource_plan,
                    server_hard_deadline_at=fixed_deadline,
                )
                replay = await admit_public_operation_v2(
                    session,
                    principal=principal,
                    operation_id=operation_id,
                    operation_kind="generation",
                    request_plan=plan_provider_requests(
                        slide_count=1, image_budget=0, repair_budget=0, vqa_budget=0
                    ),
                    resource_plan=resource_plan,
                    server_hard_deadline_at=fixed_deadline,
                )
                assert replay.id == admitted.id
                assert replay.deadline_at == admitted.deadline_at == fixed_deadline
                assert admitted.fence == 1

            lease_hash = "c" * 64
            async with maker() as session, session.begin():
                active = await activate_public_operation_v2(
                    session,
                    admission_id=admitted.id,
                    lease_owner_hash=lease_hash,
                    lease_seconds=60,
                )
                assert active.state == "active"
                leases = list((await session.execute(select(PublicResourceLease))).scalars())
                assert {lease.scope_type for lease in leases} == {"global", "tenant", "owner"}
                assert all(lease.state == "leased" and lease.fence == 1 for lease in leases)
                counters = list((await session.execute(select(PublicQuotaCounter))).scalars())
                assert all(row.queued_count == 0 and row.active_count == 1 for row in counters)
                assert all(row.queued_resource_units == 0 and row.active_resource_units == admitted.resource_units for row in counters)

                with pytest.raises(PublicAdmissionRejected, match="public_operation_fence_conflict"):
                    await finish_public_operation_v2(
                        session, admission_id=admitted.id, state="succeeded", expected_fence=0
                    )

                queued = await requeue_public_operation_v2(
                    session, admission_id=admitted.id, expected_fence=1
                )
                assert queued.state == "queued" and queued.fence == 2
                queued_leases = list((await session.execute(select(PublicResourceLease))).scalars())
                assert all(lease.state == "queued" and lease.fence == 2 for lease in queued_leases)
                active_again = await activate_public_operation_v2(
                    session,
                    admission_id=admitted.id,
                    lease_owner_hash=lease_hash,
                    lease_seconds=60,
                )
                assert active_again.fence == 2
                leases_again = list((await session.execute(select(PublicResourceLease))).scalars())
                assert all(lease.state == "leased" and lease.fence == 2 and lease.attempt == 2 for lease in leases_again)
                finished = await finish_public_operation_v2(
                    session,
                    admission_id=admitted.id,
                    state="succeeded",
                    expected_fence=2,
                    expected_attempt=2,
                )
                assert finished.state == "succeeded"
                counters = list((await session.execute(select(PublicQuotaCounter))).scalars())
                assert all(
                    row.queued_count == 0
                    and row.active_count == 0
                    and row.queued_resource_units == 0
                    and row.active_resource_units == 0
                    for row in counters
                )
                final_leases = list((await session.execute(select(PublicResourceLease))).scalars())
                assert all(lease.state == "released" and lease.fence == 2 for lease in final_leases)
        finally:
            await engine.dispose()

    asyncio.run(scenario())
