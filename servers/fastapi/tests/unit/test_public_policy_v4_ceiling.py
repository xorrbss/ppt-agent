"""The ``public-default/v4`` policy row and the mirror that must not drift.

The defect this file guards against: revision ``e1a7c9d4b2f8`` wrote the v3
row's ``generation_deadline_seconds`` as a hand-copied mirror of
``PUBLIC_GENERATION_DEADLINE_MAX_SECONDS``.  The code constant later moved from
7200 s to 14400 s, the row could not, and admission takes ``min(policy ceiling,
formula)`` -- so the stale mirror silently capped every deck from about 40
slides up while the code looked correct.

A migration must not import runtime code (no revision in this repository does),
so the v4 row's literal is still a mirror.  What changes is that the mirror is
now machine-checked here, against both the code constant and a database built
from base to head.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import uuid

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import migration_revision_contract as revision_contract
from models.sql.presentation import PresentationModel
from models.sql.public_admission import PublicPolicyProfile, PublicResourceLease
from models.sql.user import User
from models.sql.workspace import WorkspaceConversation, WorkspaceOperation
from services.public_generation_deadline_policy import (
    PUBLIC_GENERATION_DEADLINE_MAX_SECONDS,
    public_generation_deadline_seconds,
)
from services.public_operation_admission import (
    PUBLIC_DEFAULT_POLICY_VERSION,
    PUBLIC_PHASE2_DEFAULT_POLICY_VERSION,
    PrincipalContext,
    PublicAdmissionRejected,
    WeightedResourcePlan,
    admit_public_operation_compatible_v2,
    load_public_policy,
    plan_provider_requests,
)
from services.public_policy_versions import (
    PUBLIC_PREVIOUS_DEFAULT_POLICY_VERSION,
    resolve_public_policy_row,
)


ALEMBIC_DIRECTORY = Path(__file__).resolve().parents[2] / "alembic"
VERSIONS_DIRECTORY = ALEMBIC_DIRECTORY / "versions"

V3_REVISION = "e1a7c9d4b2f8"
V4_REVISION = "a2c4e6f8b1d3"
REVISION_BEFORE_V4 = "e8b3d1f6a4c2"

# Frozen: the v3 row is applied in production and can never move again.
V3_ROW_CEILING_SECONDS = 7200

# The formula values for the deck sizes production actually generates, under
# the shipped token rate profile.  They are pinned literals so a change to the
# formula has to be acknowledged here too.
NINETY_SLIDE_BUDGET_SECONDS = 11770
HUNDRED_SLIDE_BUDGET_SECONDS = 12550

# Every column the two rows must agree on.  ``generation_deadline_seconds`` is
# the single deliberate difference and ``created_at`` is a migration timestamp.
CLONED_POLICY_COLUMNS = (
    "enabled",
    "max_slides",
    "global_active_generation",
    "global_queued_generation",
    "owner_active_generation",
    "owner_queued_generation",
    "tenant_active_generation",
    "tenant_queued_generation",
    "global_active_export",
    "global_queued_export",
    "owner_active_export",
    "owner_queued_export",
    "tenant_active_export",
    "tenant_queued_export",
    "global_active_resource_units",
    "global_queued_resource_units",
    "tenant_active_resource_units",
    "tenant_queued_resource_units",
    "owner_active_resource_units",
    "owner_queued_resource_units",
    "retry_request_limit",
    "estimated_cost_limit_microunits",
    "actual_cost_limit_microunits",
    "export_deadline_seconds",
)

_PROFILE_KNOBS = (
    "AUTHORED_BATCH_SLIDES_PER_CALL",
    "AUTHORED_BATCH_WAVE_CONCURRENCY",
    "AUTHORED_AUTHOR_CONCURRENCY",
    "CONTENT_BRIEF_TIMEOUT_MAX_SECONDS",
    "CONTENT_BRIEF_TOTAL_BUDGET_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_PER_SLIDE_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_CALL_OVERHEAD_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_BASE_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_OUTLINE_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_HEADER_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_REMEDIATION_WAVE_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_REVIEW_PER_SLIDE_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_REVIEW_PER_REMAKE_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_BUDGETED_WAVE_CONCURRENCY",
    "PUBLIC_GENERATION_DEADLINE_MAX_SECONDS",
)


@pytest.fixture(autouse=True)
def _shipped_token_rate_profile(monkeypatch):
    monkeypatch.setenv("AUTHORED_PROVIDER_RATE_PROFILE", "token")
    for knob in _PROFILE_KNOBS:
        monkeypatch.delenv(knob, raising=False)


def _revision_module(revision: str):
    path = next(VERSIONS_DIRECTORY.glob(f"{revision}_*.py"))
    spec = importlib.util.spec_from_file_location(f"_revision_{revision}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(ALEMBIC_DIRECTORY))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _policy_rows(engine) -> dict[str, dict]:
    with engine.connect() as connection:
        return {
            row["version"]: dict(row)
            for row in connection.execute(
                text("SELECT * FROM public_policy_profiles")
            )
            .mappings()
            .all()
        }


def test_v4_migration_mirror_matches_the_code_ceiling() -> None:
    """THE regression gate: the literal in the migration is not free to drift.

    If ``PUBLIC_GENERATION_DEADLINE_MAX_SECONDS`` is raised again, this fails
    and the only correct answer is a new ``public-default/v5`` row -- editing
    the applied v4 literal would change a row other operations are pinned to.
    """

    v4 = _revision_module(V4_REVISION)
    assert v4._GENERATION_DEADLINE_CEILING_SECONDS == (
        PUBLIC_GENERATION_DEADLINE_MAX_SECONDS
    )
    assert v4.revision == V4_REVISION
    assert v4.down_revision == REVISION_BEFORE_V4
    assert revision_contract.REVISION_PUBLIC_DEADLINE_CEILING_V4 == V4_REVISION


def test_v3_migration_mirror_stays_frozen_below_the_code_ceiling() -> None:
    """The v3 literal must never be edited to follow the code constant."""

    v3 = _revision_module(V3_REVISION)
    assert v3._GENERATION_DEADLINE_CEILING_SECONDS == V3_ROW_CEILING_SECONDS
    assert V3_ROW_CEILING_SECONDS < PUBLIC_GENERATION_DEADLINE_MAX_SECONDS


def test_v4_row_clones_v3_and_only_raises_the_generation_ceiling(tmp_path) -> None:
    """Base to head on a fresh database, then downgrade removes only v4."""

    database_url = f"sqlite:///{(tmp_path / 'public-policy-v4.db').as_posix()}"
    config = _alembic_config(database_url)
    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        rows = _policy_rows(engine)
        assert set(rows) == {
            "public-default/v1",
            "public-default/v2",
            "public-default/v3",
            "public-default/v4",
        }
        v3 = rows["public-default/v3"]
        v4 = rows["public-default/v4"]
        assert v3["generation_deadline_seconds"] == V3_ROW_CEILING_SECONDS
        assert v4["generation_deadline_seconds"] == (
            PUBLIC_GENERATION_DEADLINE_MAX_SECONDS
        )
        for column in CLONED_POLICY_COLUMNS:
            assert v4[column] == v3[column], column
        assert set(v4) - set(CLONED_POLICY_COLUMNS) == {
            "version",
            "generation_deadline_seconds",
            "created_at",
        }
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one() == revision_contract.HEAD_REVISION

        command.downgrade(config, REVISION_BEFORE_V4)
        remaining = _policy_rows(engine)
        assert set(remaining) == {
            "public-default/v1",
            "public-default/v2",
            "public-default/v3",
        }
        for version, row in remaining.items():
            assert row == rows[version]
    finally:
        engine.dispose()


async def _async_database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_safety(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_operation(maker, owner_id: uuid.UUID, *, key: str) -> uuid.UUID:
    async with maker() as session, session.begin():
        presentation = PresentationModel(
            owner_id=owner_id,
            content="ceiling deck",
            n_slides=90,
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


def _plan(slide_count: int):
    return plan_provider_requests(
        slide_count=slide_count,
        image_budget=slide_count,
        repair_budget=slide_count,
        vqa_budget=0,
    )


def _granted_seconds(admission) -> float:
    queued = admission.queued_at.replace(tzinfo=None)
    deadline = admission.deadline_at.replace(tzinfo=None)
    return (deadline - queued).total_seconds()


@pytest.mark.parametrize(
    ("slide_count", "expected_seconds"),
    [(90, NINETY_SLIDE_BUDGET_SECONDS), (100, HUNDRED_SLIDE_BUDGET_SECONDS)],
)
def test_large_decks_receive_the_formula_not_a_clamp(
    monkeypatch, tmp_path, slide_count: int, expected_seconds: int
) -> None:
    """A 90- and a 100-slide deck admitted against the real migrated v4 row.

    Under v3 both were cut to 7200 s by the policy column.  The whole point of
    the new row is that ``min(ceiling, formula)`` now returns the formula.
    """

    monkeypatch.setattr(
        "services.public_operation_admission._require_phase2_authority",
        lambda _session: PublicResourceLease,
    )
    database_path = tmp_path / f"public-admission-v4-{slide_count}.db"
    command.upgrade(
        _alembic_config(f"sqlite:///{database_path.as_posix()}"), "head"
    )

    async def scenario() -> None:
        engine, maker = await _async_database(database_path)
        owner_id = uuid.uuid4()
        principal = PrincipalContext(owner_id, "tenant-ceiling", "trace-ceiling")
        try:
            async with maker() as session, session.begin():
                session.add(
                    User(id=owner_id, username="ceiling-owner", hashed_password="test")
                )
            operation_id = await _seed_operation(maker, owner_id, key="a")
            async with maker() as session, session.begin():
                admitted = await admit_public_operation_compatible_v2(
                    session,
                    principal=principal,
                    operation_id=operation_id,
                    operation_kind="generation",
                    request_plan=_plan(slide_count),
                    resource_plan=_RESOURCE_PLAN,
                )
                assert admitted.policy_version == "public-default/v4"
                assert _granted_seconds(admitted) == expected_seconds
                assert (
                    public_generation_deadline_seconds(slide_count)
                    == expected_seconds
                )
                assert expected_seconds > V3_ROW_CEILING_SECONDS
                assert expected_seconds < PUBLIC_GENERATION_DEADLINE_MAX_SECONDS
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_default_version_falls_back_to_the_previous_row_before_migration(
    tmp_path,
) -> None:
    """Deployment ordering: code ahead of the database degrades, never fails.

    Admission fails closed with ``public_policy_unavailable`` when the row for
    the configured version is missing, so a process that expects v4 in front of
    an unmigrated database would reject every deck.  The default version -- and
    only the default version -- therefore falls back one step, with a warning.
    """

    database_path = tmp_path / "public-policy-fallback.db"
    command.upgrade(
        _alembic_config(f"sqlite:///{database_path.as_posix()}"),
        REVISION_BEFORE_V4,
    )

    async def scenario() -> None:
        engine, maker = await _async_database(database_path)
        try:
            async with maker() as session:
                assert (
                    await session.get(
                        PublicPolicyProfile, PUBLIC_DEFAULT_POLICY_VERSION
                    )
                ) is None

                resolved = await resolve_public_policy_row(
                    session, PUBLIC_DEFAULT_POLICY_VERSION
                )
                assert resolved is not None
                assert resolved.version == PUBLIC_PREVIOUS_DEFAULT_POLICY_VERSION
                assert resolved.generation_deadline_seconds == V3_ROW_CEILING_SECONDS

                policy = await load_public_policy(session)
                assert policy.version == PUBLIC_PREVIOUS_DEFAULT_POLICY_VERSION

                # An explicitly requested version is never redirected: a pinned
                # operation must keep the policy it was admitted under.
                assert (
                    await resolve_public_policy_row(session, "public-default/v9")
                ) is None
                with pytest.raises(PublicAdmissionRejected) as rejected:
                    await load_public_policy(session, "public-default/v9")
                assert rejected.value.code == "public_policy_unavailable"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_admission_records_the_version_of_the_row_it_actually_applied(
    monkeypatch, tmp_path
) -> None:
    """The fallback has to move the identity, not only the limits.

    ``load_public_policy`` answered the missing v4 default with the v3 row, but
    admission kept writing the *requested* version onto the admission row and
    the quota counters. Both carry a foreign key onto ``public_policy_profiles``,
    so PostgreSQL rejected the insert with ``public_policy_authority_unavailable``
    and every generation returned 500 -- the seam that exists to keep decks
    working while the code is ahead of its migration instead broke all of them.
    """

    monkeypatch.setattr(
        "services.public_operation_admission._require_phase2_authority",
        lambda _session: PublicResourceLease,
    )
    database_path = tmp_path / "public-admission-before-v4.db"
    command.upgrade(
        _alembic_config(f"sqlite:///{database_path.as_posix()}"),
        REVISION_BEFORE_V4,
    )

    async def scenario() -> None:
        engine, maker = await _async_database(database_path)
        owner_id = uuid.uuid4()
        principal = PrincipalContext(owner_id, "tenant-fallback", "trace-fallback")
        try:
            async with maker() as session, session.begin():
                session.add(
                    User(id=owner_id, username="fallback-owner", hashed_password="test")
                )
            operation_id = await _seed_operation(maker, owner_id, key="fallback")
            async with maker() as session, session.begin():
                admitted = await admit_public_operation_compatible_v2(
                    session,
                    principal=principal,
                    operation_id=operation_id,
                    operation_kind="generation",
                    request_plan=_plan(18),
                    resource_plan=_RESOURCE_PLAN,
                )
                assert admitted.policy_version == PUBLIC_PREVIOUS_DEFAULT_POLICY_VERSION

            # The quota counters have to agree, or the same foreign key fails on
            # the next admission against this database.
            async with maker() as session:
                versions = (
                    await session.execute(
                        text("SELECT DISTINCT policy_version FROM public_quota_counters")
                    )
                ).scalars().all()
            assert versions == [PUBLIC_PREVIOUS_DEFAULT_POLICY_VERSION]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_both_default_version_constants_move_together() -> None:
    assert PUBLIC_DEFAULT_POLICY_VERSION == "public-default/v4"
    assert PUBLIC_PHASE2_DEFAULT_POLICY_VERSION == PUBLIC_DEFAULT_POLICY_VERSION
    assert PUBLIC_PREVIOUS_DEFAULT_POLICY_VERSION == "public-default/v3"
