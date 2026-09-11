"""One export deadline: admission stores the deck size and scales the budget.

The defect these tests guard: export admission recorded ``slide_count = 0``
and a flat 600 s budget while the isolated hybrid worker was granted a
separate 1740 s constant, so a 35-slide hybrid export (measured 894 s)
outlived its own admission and was failed at completion.
"""

from __future__ import annotations

import asyncio
import importlib.util
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
    PublicResourceLease,
)
from models.sql.user import User
from models.sql.workspace import WorkspaceConversation, WorkspaceOperation
import migration_revision_contract as revision_contract
from services.public_export_deadline_policy import (
    PUBLIC_EXPORT_DEADLINE_MAX_SECONDS,
    PUBLIC_EXPORT_DEADLINE_MIN_SECONDS,
    public_export_deadline_seconds,
)
from services.public_operation_admission import (
    PUBLIC_DEFAULT_POLICY_VERSION,
    PrincipalContext,
    admit_public_operation,
)
from services.trusted_executor.worker_dispatcher import _ROLE_RESOURCE_BUDGETS


VERSIONS_DIRECTORY = Path(__file__).resolve().parents[2] / "alembic" / "versions"
V4_REVISION = "a2c4e6f8b1d3"
V5_REVISION = "b4e6f8a0c2d5"


def _revision_module(revision: str):
    path = next(VERSIONS_DIRECTORY.glob(f"{revision}_*.py"))
    spec = importlib.util.spec_from_file_location(f"_revision_{revision}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


# --- Slide-count scaled export budget -----------------------------------

_EXPORT_KNOBS = (
    "PUBLIC_EXPORT_DEADLINE_PER_SLIDE_SECONDS",
    "PUBLIC_EXPORT_DEADLINE_BASE_SECONDS",
    "PUBLIC_EXPORT_DEADLINE_MIN_SECONDS",
)

# The measured wall of the 35-slide editable-hybrid export (W22 report).
MEASURED_35_SLIDE_HYBRID_SECONDS = 894


@pytest.fixture(autouse=True)
def _shipped_export_terms(monkeypatch):
    for knob in _EXPORT_KNOBS:
        monkeypatch.delenv(knob, raising=False)


def test_thirty_five_slide_export_budget_covers_the_measured_wall() -> None:
    """THE defect: 894 s of hybrid export against a 600 s flat budget."""

    granted = public_export_deadline_seconds(35)
    assert granted > MEASURED_35_SLIDE_HYBRID_SECONDS
    assert granted == 120 + 26 * 35 == 1030
    assert public_export_deadline_seconds(5) < granted
    assert public_export_deadline_seconds(10) < granted


def test_small_and_unknown_decks_keep_the_historical_floor() -> None:
    assert public_export_deadline_seconds(0) == PUBLIC_EXPORT_DEADLINE_MIN_SECONDS
    assert public_export_deadline_seconds(5) == PUBLIC_EXPORT_DEADLINE_MIN_SECONDS
    assert public_export_deadline_seconds(10) == PUBLIC_EXPORT_DEADLINE_MIN_SECONDS
    assert PUBLIC_EXPORT_DEADLINE_MIN_SECONDS == 600


@pytest.mark.parametrize("slide_count", [0, 1, 35, 64, 65, 100, 250])
def test_no_export_budget_exceeds_the_ceiling(slide_count: int) -> None:
    granted = public_export_deadline_seconds(slide_count)
    assert 1 <= granted <= PUBLIC_EXPORT_DEADLINE_MAX_SECONDS
    assert granted == min(PUBLIC_EXPORT_DEADLINE_MAX_SECONDS, max(600, 120 + 26 * slide_count))


def test_the_ceiling_mirrors_the_executor_cap_and_the_v5_row() -> None:
    """The migration literal and the executor budget are hand-copied mirrors."""

    v5 = _revision_module(V5_REVISION)
    assert v5._EXPORT_DEADLINE_CEILING_SECONDS == PUBLIC_EXPORT_DEADLINE_MAX_SECONDS
    assert v5.down_revision == V4_REVISION
    assert revision_contract.HEAD_REVISION == V5_REVISION
    assert revision_contract.REVISION_PUBLIC_EXPORT_DEADLINE_CEILING_V5 == V5_REVISION
    assert PUBLIC_EXPORT_DEADLINE_MAX_SECONDS <= _ROLE_RESOURCE_BUDGETS["export"][2]


def test_runtime_terms_are_overridable_but_never_lift_the_ceiling(monkeypatch) -> None:
    monkeypatch.setenv("PUBLIC_EXPORT_DEADLINE_PER_SLIDE_SECONDS", "40")
    monkeypatch.setenv("PUBLIC_EXPORT_DEADLINE_BASE_SECONDS", "200")
    assert public_export_deadline_seconds(35) == 200 + 40 * 35
    assert public_export_deadline_seconds(100) == PUBLIC_EXPORT_DEADLINE_MAX_SECONDS
    monkeypatch.setenv("PUBLIC_EXPORT_DEADLINE_MIN_SECONDS", "900")
    assert public_export_deadline_seconds(0) == 900
    monkeypatch.delenv("PUBLIC_EXPORT_DEADLINE_MIN_SECONDS")
    assert public_export_deadline_seconds(0) == PUBLIC_EXPORT_DEADLINE_MIN_SECONDS
    with pytest.raises(ValueError):
        public_export_deadline_seconds(-1)


def test_admission_pins_the_scaled_budget_under_the_row_ceiling(tmp_path: Path) -> None:
    """v5 (ceiling 1800) lets the formula decide; a legacy 600 row still caps."""

    async def scenario() -> None:
        engine, maker = await database(tmp_path / "export-scaled.db")
        try:
            thirty_five, five, hundred = await admit_export(
                maker, export_deadline_seconds=1800, slide_counts=(35, 5, 100)
            )
            assert granted_seconds(thirty_five) == 1030
            assert granted_seconds(five) == 600
            assert granted_seconds(hundred) == PUBLIC_EXPORT_DEADLINE_MAX_SECONDS
        finally:
            await engine.dispose()

        engine, maker = await database(tmp_path / "export-legacy.db")
        try:
            (legacy,) = await admit_export(
                maker, export_deadline_seconds=600, slide_counts=(35,)
            )
            assert legacy.slide_count == 35
            assert granted_seconds(legacy) == 600
        finally:
            await engine.dispose()

    asyncio.run(scenario())
