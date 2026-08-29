from __future__ import annotations

import asyncio
from pathlib import Path
import uuid

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.presentation import PresentationModel
from models.sql.public_admission import (
    PublicOperationAdmission,
    PublicPolicyProfile,
    PublicProviderLogicalCall,
    PublicQuotaCounter,
)
from models.sql.user import User
from models.sql.workspace import WorkspaceConversation, WorkspaceOperation
from services.public_operation_admission import (
    PrincipalContext,
    PublicAdmissionRejected,
    activate_public_operation,
    admit_public_operation,
    finish_public_operation,
    plan_provider_requests,
    public_operation_metrics,
    public_quota_metrics,
    reconcile_public_provider_call,
    reserve_public_provider_call,
)


TABLES = [
    User.__table__,
    PresentationModel.__table__,
    WorkspaceConversation.__table__,
    WorkspaceOperation.__table__,
    PublicPolicyProfile.__table__,
    PublicQuotaCounter.__table__,
    PublicOperationAdmission.__table__,
    PublicProviderLogicalCall.__table__,
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
                            owner_active_generation=1,
                            owner_queued_generation=1,
                            tenant_active_generation=2,
                            tenant_queued_generation=3,
                            owner_active_export=1,
                            owner_queued_export=1,
                            tenant_active_export=2,
                            tenant_queued_export=3,
                            retry_request_limit=1,
                            estimated_cost_limit_microunits=12,
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

            async with maker() as session, session.begin():
                first = await admit_public_operation(
                    session,
                    principal=principal_a,
                    operation_id=operation_ids[0],
                    operation_kind="generation",
                    request_plan=plan,
                )
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
                    latency_ms=125,
                    actual_cost_microunits=11,
                    input_tokens=100,
                    output_tokens=50,
                )
                with pytest.raises(PublicAdmissionRejected) as rejected:
                    await reserve_public_provider_call(
                        session,
                        admission_id=first.id,
                        stage="author",
                        slide_index=0,
                        logical_call_ordinal=2,
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
                assert operation_metrics["initial_request_count"] == 1
                assert operation_metrics["actual_consumed_microunits"] == 11
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
