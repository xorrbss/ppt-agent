"""Slide-count scaled public generation deadline contract.

Covers the pure formula boundaries, the deadline actually pinned by v3
admission, the fact that retry/resume never re-grants a whole budget, and the
untouched fixed budget of the already-pinned v1/v2 policies.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import uuid

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from alembic import command
from alembic.config import Config

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
from services.public_generation_deadline_policy import (
    PUBLIC_GENERATION_DEADLINE_MAX_SECONDS,
    public_generation_deadline_seconds,
    public_generation_wave_count,
)
from services.public_operation_admission import (
    PUBLIC_DEFAULT_POLICY_VERSION,
    PUBLIC_PHASE2_DEFAULT_POLICY_VERSION,
    PrincipalContext,
    WeightedResourcePlan,
    activate_public_operation_v2,
    admit_public_operation_compatible_v2,
    plan_provider_requests,
    requeue_public_operation_v2,
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

LEGACY_FIXED_DEADLINE_SECONDS = 900


@pytest.fixture(autouse=True)
def _token_rate_profile(monkeypatch):
    """The scaled budgets below are the token profile's 3-slide, 1-then-2 shape.

    The profile now authors one slide per call by default, so the batch shape
    these budgets were derived from is asked for explicitly.
    """

    monkeypatch.setenv("AUTHORED_PROVIDER_RATE_PROFILE", "token")
    monkeypatch.setenv("AUTHORED_BATCH_SLIDES_PER_CALL", "3")


def test_public_default_policy_version_is_v3() -> None:
    assert PUBLIC_DEFAULT_POLICY_VERSION == "public-default/v3"
    assert PUBLIC_PHASE2_DEFAULT_POLICY_VERSION == "public-default/v3"


@pytest.mark.parametrize(
    ("slide_count", "expected_seconds"),
    [
        (1, 1200),
        (3, 1200),
        (4, 1500),
        (9, 1500),
        (10, 1800),
        (15, 1800),
        (35, 3000),
        (100, 6300),
    ],
)
def test_generation_deadline_scales_with_three_slide_batch_waves(
    slide_count: int, expected_seconds: int
) -> None:
    """One serial warm-up call, then two calls per wave, five minutes a wave."""

    assert public_generation_deadline_seconds(slide_count) == expected_seconds


def test_generation_deadline_wave_count_and_ceiling_are_bounded() -> None:
    assert public_generation_wave_count(1) == 1
    assert public_generation_wave_count(4) == 2
    assert public_generation_wave_count(35) == 7
    assert public_generation_wave_count(100) == 18
    # A deck far past the 100-slide policy cap still cannot exceed the ceiling.
    assert (
        public_generation_deadline_seconds(10_000)
        == PUBLIC_GENERATION_DEADLINE_MAX_SECONDS
    )
    with pytest.raises(ValueError, match="slide_count_must_be_nonnegative"):
        public_generation_deadline_seconds(-1)


def test_generation_deadline_follows_the_api_profile_wave_shape(monkeypatch) -> None:
    """Per-slide calls, five at a time: 20 slides are four waves."""

    monkeypatch.setenv("AUTHORED_PROVIDER_RATE_PROFILE", "api")
    monkeypatch.delenv("AUTHORED_BATCH_SLIDES_PER_CALL", raising=False)

    assert public_generation_wave_count(5) == 1
    assert public_generation_wave_count(20) == 4
    assert public_generation_deadline_seconds(20) == 900 + 300 * 4


def _policy(version: str, *, generation_deadline_seconds: int) -> PublicPolicyProfile:
    return PublicPolicyProfile(
        version=version,
        max_slides=100,
        global_active_generation=4,
        global_queued_generation=8,
        owner_active_generation=2,
        owner_queued_generation=4,
        tenant_active_generation=4,
        tenant_queued_generation=8,
        global_active_export=4,
        global_queued_export=8,
        owner_active_export=2,
        owner_queued_export=4,
        tenant_active_export=4,
        tenant_queued_export=8,
        global_active_resource_units=10_000_000,
        global_queued_resource_units=10_000_000,
        tenant_active_resource_units=10_000_000,
        tenant_queued_resource_units=10_000_000,
        owner_active_resource_units=10_000_000,
        owner_queued_resource_units=10_000_000,
        retry_request_limit=4,
        estimated_cost_limit_microunits=50_000_000,
        actual_cost_limit_microunits=50_000_000,
        generation_deadline_seconds=generation_deadline_seconds,
        export_deadline_seconds=600,
    )


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


async def _seed_operation(maker, owner_id: uuid.UUID, *, key: str) -> uuid.UUID:
    async with maker() as session, session.begin():
        presentation = PresentationModel(
            owner_id=owner_id,
            content="scaled deadline deck",
            n_slides=35,
            language="ko",
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
            idempotency_key_hash=key * 64,
            request_hash=key * 64,
            request_payload={},
        )
        session.add(operation)
        await session.flush()
        return operation.id


_RESOURCE_PLAN = WeightedResourcePlan(
    pixel_count=1,
    device_pixel_ratio_milli=1000,
    dom_node_count=1,
    font_count=1,
    svg_complexity=0,
    estimated_memory_mib=1,
    fidelity_mode="fidelity",
)


def _naive_utc(value):
    """SQLite hands datetimes back without a tzinfo; compare instants only."""

    return value.replace(tzinfo=None)


def _plan(slide_count: int):
    return plan_provider_requests(
        slide_count=slide_count,
        image_budget=slide_count,
        repair_budget=slide_count,
        vqa_budget=0,
    )


def test_v3_admission_pins_scaled_deadline_and_retry_never_regrants_it(
    monkeypatch, tmp_path: Path
) -> None:
    """A 35-slide job gets 50 minutes once; requeue and replay keep that instant."""

    # SQLite is used only as a deterministic contract fixture; PostgreSQL
    # authority is covered by the control-plane race gate.
    monkeypatch.setattr(
        "services.public_operation_admission._require_phase2_authority",
        lambda _session: PublicResourceLease,
    )

    async def scenario() -> None:
        engine, maker = await _database(tmp_path / "public-scaled-deadline.db")
        owner_id = uuid.uuid4()
        principal = PrincipalContext(owner_id, "tenant-deadline", "trace-deadline")
        try:
            async with maker() as session, session.begin():
                session.add(
                    User(id=owner_id, username="deadline-owner", hashed_password="test")
                )
                session.add(
                    _policy(
                        PUBLIC_PHASE2_DEFAULT_POLICY_VERSION,
                        generation_deadline_seconds=(
                            PUBLIC_GENERATION_DEADLINE_MAX_SECONDS
                        ),
                    )
                )
                session.add(
                    _policy(
                        "public-default/v2",
                        generation_deadline_seconds=LEGACY_FIXED_DEADLINE_SECONDS,
                    )
                )

            operation_id = await _seed_operation(maker, owner_id, key="a")

            async with maker() as session, session.begin():
                admitted = await admit_public_operation_compatible_v2(
                    session,
                    principal=principal,
                    operation_id=operation_id,
                    operation_kind="generation",
                    request_plan=_plan(35),
                    resource_plan=_RESOURCE_PLAN,
                )
                # ``queued_at`` and ``deadline_at`` are stamped from the same
                # admission instant, so the delta is the exact granted budget.
                granted = (
                    _naive_utc(admitted.deadline_at) - _naive_utc(admitted.queued_at)
                ).total_seconds()
                assert granted == 3000
                assert admitted.policy_version == "public-default/v3"
                operation = await session.get(WorkspaceOperation, operation_id)
                assert _naive_utc(operation.deadline_at) == _naive_utc(admitted.deadline_at)
                pinned_deadline = admitted.deadline_at
                admission_id = admitted.id

            # Resume: a second admission for the same operation replays the
            # pinned row instead of computing a new budget.
            async with maker() as session, session.begin():
                replayed = await admit_public_operation_compatible_v2(
                    session,
                    principal=principal,
                    operation_id=operation_id,
                    operation_kind="generation",
                    request_plan=_plan(35),
                    resource_plan=_RESOURCE_PLAN,
                )
                assert replayed.id == admission_id
                assert _naive_utc(replayed.deadline_at) == _naive_utc(pinned_deadline)

            # Retry: activate, requeue, re-activate.  The fence advances, the
            # deadline does not move.
            async with maker() as session, session.begin():
                active = await activate_public_operation_v2(
                    session,
                    admission_id=admission_id,
                    lease_owner_hash="c" * 64,
                    lease_seconds=60,
                )
                assert _naive_utc(active.deadline_at) == _naive_utc(pinned_deadline)
                active_fence = active.fence
                requeued = await requeue_public_operation_v2(
                    session, admission_id=admission_id, expected_fence=active_fence
                )
                assert requeued.fence == active_fence + 1
                assert _naive_utc(requeued.deadline_at) == _naive_utc(pinned_deadline)
                reactivated = await activate_public_operation_v2(
                    session,
                    admission_id=admission_id,
                    lease_owner_hash="c" * 64,
                    lease_seconds=60,
                )
                assert _naive_utc(reactivated.deadline_at) == _naive_utc(pinned_deadline)
                operation = await session.get(WorkspaceOperation, operation_id)
                assert _naive_utc(operation.deadline_at) == _naive_utc(pinned_deadline)

            # An operation pinned to the legacy v2 policy keeps its fixed
            # 15-minute budget even for the same 35-slide request.
            legacy_operation_id = await _seed_operation(maker, owner_id, key="b")
            async with maker() as session, session.begin():
                legacy = await admit_public_operation_compatible_v2(
                    session,
                    principal=principal,
                    operation_id=legacy_operation_id,
                    operation_kind="generation",
                    request_plan=_plan(35),
                    resource_plan=_RESOURCE_PLAN,
                    policy_version="public-default/v2",
                )
                assert (
                    _naive_utc(legacy.deadline_at) - _naive_utc(legacy.queued_at)
                ).total_seconds() == LEGACY_FIXED_DEADLINE_SECONDS
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_v3_policy_migration_adds_row_without_touching_v1_and_v2(tmp_path: Path) -> None:
    """The new row raises only the generation ceiling; older rows stay pinned."""

    database_url = f"sqlite:///{(tmp_path / 'public-policy-v3.db').as_posix()}"
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[2] / "alembic")
    )
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            rows = {
                row["version"]: row
                for row in connection.execute(
                    text(
                        """
                        SELECT version, max_slides, generation_deadline_seconds,
                               export_deadline_seconds, retry_request_limit
                        FROM public_policy_profiles
                        ORDER BY version
                        """
                    )
                )
                .mappings()
                .all()
            }
        assert set(rows) == {
            "public-default/v1",
            "public-default/v2",
            "public-default/v3",
        }
        for legacy in ("public-default/v1", "public-default/v2"):
            assert rows[legacy]["generation_deadline_seconds"] == (
                LEGACY_FIXED_DEADLINE_SECONDS
            )
        v3 = rows["public-default/v3"]
        assert v3["generation_deadline_seconds"] == PUBLIC_GENERATION_DEADLINE_MAX_SECONDS
        assert v3["export_deadline_seconds"] == 600
        assert v3["max_slides"] == rows["public-default/v2"]["max_slides"] == 100
        assert (
            v3["retry_request_limit"]
            == rows["public-default/v2"]["retry_request_limit"]
        )
    finally:
        engine.dispose()
