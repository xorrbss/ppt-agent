"""One export deadline: admission stores the deck size and scales the budget.

The defect these tests guard: export admission recorded ``slide_count = 0``
and a flat 600 s budget while the isolated hybrid worker was granted a
separate 1740 s constant, so a 35-slide hybrid export (measured 894 s)
outlived its own admission and was failed at completion.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import uuid

from sqlalchemy import event
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
    PUBLIC_DEFAULT_POLICY_VERSION,
    PrincipalContext,
    admit_public_operation,
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
    PublicResourceLease.__table__,
]


def policy_row(
    *, version: str = PUBLIC_DEFAULT_POLICY_VERSION, export_deadline_seconds: int
) -> PublicPolicyProfile:
    return PublicPolicyProfile(
        version=version,
        max_slides=100,
        global_active_generation=4,
        global_queued_generation=8,
        owner_active_generation=1,
        owner_queued_generation=4,
        tenant_active_generation=2,
        tenant_queued_generation=8,
        global_active_export=4,
        global_queued_export=8,
        owner_active_export=1,
        owner_queued_export=4,
        tenant_active_export=2,
        tenant_queued_export=8,
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
        export_deadline_seconds=export_deadline_seconds,
    )


async def database(path: Path):
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


async def seed_export_operation(
    session, *, owner_id: uuid.UUID, n_slides: int, ordinal: int
) -> uuid.UUID:
    presentation = PresentationModel(
        owner_id=owner_id,
        content="export deck",
        n_slides=n_slides,
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
        anchor_position=ordinal,
        kind="export.pptx",
        idempotency_key_hash=f"{ordinal:064x}",
        request_hash=f"{ordinal + 100:064x}",
        request_payload={},
    )
    session.add(operation)
    await session.flush()
    return operation.id


def granted_seconds(admission: PublicOperationAdmission) -> float:
    queued = admission.queued_at.replace(tzinfo=None)
    deadline = admission.deadline_at.replace(tzinfo=None)
    return (deadline - queued).total_seconds()


async def admit_export(
    maker, *, export_deadline_seconds: int, slide_counts: tuple[int, ...]
) -> list[PublicOperationAdmission]:
    owner_id = uuid.uuid4()
    principal = PrincipalContext(owner_id, "tenant-export", "trace-export")
    async with maker() as session, session.begin():
        session.add_all(
            [
                User(id=owner_id, username=f"owner-{owner_id}", hashed_password="x"),
                policy_row(export_deadline_seconds=export_deadline_seconds),
            ]
        )
    admissions = []
    for ordinal, n_slides in enumerate(slide_counts, start=1):
        async with maker() as session, session.begin():
            operation_id = await seed_export_operation(
                session, owner_id=owner_id, n_slides=n_slides, ordinal=ordinal
            )
            admissions.append(
                await admit_public_operation(
                    session,
                    principal=principal,
                    operation_id=operation_id,
                    operation_kind="export",
                    request_plan=None,
                    planned_slide_count=n_slides,
                )
            )
    return admissions


def test_export_admission_persists_the_planned_slide_count(tmp_path: Path) -> None:
    """The row records the deck size the deadline was derived from."""

    async def scenario() -> None:
        engine, maker = await database(tmp_path / "export-slide-count.db")
        try:
            admitted, defaulted = await admit_export(
                maker, export_deadline_seconds=600, slide_counts=(35, 0)
            )
            assert admitted.operation_kind == "export"
            assert admitted.slide_count == 35
            assert admitted.planned_request_limit == 0
            # An unknown deck size is stored as zero, never rejected.
            assert defaulted.slide_count == 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())
