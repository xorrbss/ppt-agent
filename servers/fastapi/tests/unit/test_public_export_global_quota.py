"""The global quota scope is authoritative for export, and counted once.

Export commands are admitted by the compatibility (Phase-1) entry point
``admit_public_operation``.  That entry point used to consult only the owner
and tenant counters, so ``public_policy_profiles.global_active_export`` was
enforced on no product path at all.  These tests pin the corrected contract:

* the global scope rejects with the same shape the owner/tenant scopes use,
* a refused admission mutates nothing,
* every terminal state -- success, failure, cancellation, deadline -- returns
  the global slot,
* and one operation consumes exactly one global slot even when it is offered
  to both admission layers.
"""

from __future__ import annotations

import asyncio
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
    PUBLIC_DEFAULT_MAX_SLIDES,
    PUBLIC_DEFAULT_POLICY_VERSION,
    PrincipalContext,
    PublicAdmissionRejected,
    WeightedResourcePlan,
    activate_public_operation,
    admit_public_operation,
    admit_public_operation_compatible_v2,
    finish_public_operation,
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

GLOBAL_ACTIVE_EXPORT = 2
GLOBAL_QUEUED_EXPORT = 3


def _policy() -> PublicPolicyProfile:
    """A row whose export ceilings bind globally before owner or tenant."""

    return PublicPolicyProfile(
        version=PUBLIC_DEFAULT_POLICY_VERSION,
        max_slides=PUBLIC_DEFAULT_MAX_SLIDES,
        global_active_generation=8,
        global_queued_generation=8,
        owner_active_generation=8,
        owner_queued_generation=8,
        tenant_active_generation=8,
        tenant_queued_generation=8,
        global_active_export=GLOBAL_ACTIVE_EXPORT,
        global_queued_export=GLOBAL_QUEUED_EXPORT,
        # Deliberately wider than the global columns: only the global scope
        # may be the reason any assertion below fails.
        owner_active_export=8,
        owner_queued_export=8,
        tenant_active_export=8,
        tenant_queued_export=8,
        global_active_resource_units=100,
        global_queued_resource_units=100,
        tenant_active_resource_units=100,
        tenant_queued_resource_units=100,
        owner_active_resource_units=100,
        owner_queued_resource_units=100,
        retry_request_limit=1,
        estimated_cost_limit_microunits=10,
        actual_cost_limit_microunits=10,
        generation_deadline_seconds=900,
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


async def _seed(maker, *, owner_id: uuid.UUID, operations: int) -> list[uuid.UUID]:
    operation_ids: list[uuid.UUID] = []
    async with maker() as session, session.begin():
        session.add_all(
            [
                User(id=owner_id, username=f"owner-{owner_id}", hashed_password="x"),
                _policy(),
            ]
        )
        for index in range(operations):
            presentation = PresentationModel(
                owner_id=owner_id,
                content="bounded public export",
                n_slides=3,
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
                anchor_position=index + 1,
                kind="export.pptx",
                idempotency_key_hash=f"{index:064x}",
                request_hash=f"{index + 100:064x}",
                request_payload={},
            )
            session.add(operation)
            await session.flush()
            operation_ids.append(operation.id)
    return operation_ids


async def _counter(session, *, scope_type: str, scope_key: str) -> PublicQuotaCounter | None:
    return await session.scalar(
        select(PublicQuotaCounter).where(
            PublicQuotaCounter.policy_version == PUBLIC_DEFAULT_POLICY_VERSION,
            PublicQuotaCounter.scope_type == scope_type,
            PublicQuotaCounter.scope_key == scope_key,
            PublicQuotaCounter.operation_kind == "export",
        )
    )


async def _global_counts(maker) -> tuple[int, int]:
    async with maker() as session:
        row = await _counter(session, scope_type="global", scope_key="global")
        assert row is not None
        return row.queued_count, row.active_count


def _widen_the_export_knob(monkeypatch) -> None:
    """The global active limit is min(policy row, scheduler cap knob).

    The knob's ceiling is owned by ``test_public_runtime_quota_clamp``; here
    the policy row must be the binding term.
    """

    monkeypatch.setenv("PUBLIC_EXPORT_SCHEDULER_GLOBAL_CAP", "64")


def test_global_export_cap_admits_n_and_refuses_the_next(
    tmp_path: Path, monkeypatch
) -> None:
    """N concurrent exports run; the N+1 is refused in the global scope."""

    _widen_the_export_knob(monkeypatch)

    async def scenario() -> None:
        owner_id = uuid.uuid4()
        engine, maker = await _database(tmp_path / "global-active.db")
        try:
            operation_ids = await _seed(
                maker, owner_id=owner_id, operations=GLOBAL_ACTIVE_EXPORT + 1
            )
            principal = PrincipalContext(owner_id, "tenant-a", "trace-a")
            admissions: list[uuid.UUID] = []
            async with maker() as session, session.begin():
                for operation_id in operation_ids:
                    admission = await admit_public_operation(
                        session,
                        principal=principal,
                        operation_id=operation_id,
                        operation_kind="export",
                        request_plan=None,
                        planned_slide_count=3,
                    )
                    admissions.append(admission.id)
            async with maker() as session, session.begin():
                for admission_id in admissions[:GLOBAL_ACTIVE_EXPORT]:
                    await activate_public_operation(session, admission_id)
                with pytest.raises(PublicAdmissionRejected) as rejected:
                    await activate_public_operation(session, admissions[-1])
            # Identical shape to the owner/tenant refusals: a
            # ``PublicAdmissionRejected`` whose code names the scope and the
            # axis it exceeded.
            assert rejected.value.code == "public_global_active_limit_exceeded"
            queued, active = await _global_counts(maker)
            assert (queued, active) == (1, GLOBAL_ACTIVE_EXPORT)
            async with maker() as session:
                refused = await session.get(PublicOperationAdmission, admissions[-1])
                assert refused is not None
                # The refused operation is still queued, holding exactly the
                # one queued slot it was admitted with -- it leaked no active
                # slot in any scope.
                assert refused.state == "queued"
                owner_counter = await _counter(
                    session, scope_type="owner", scope_key=str(owner_id)
                )
                tenant_counter = await _counter(
                    session, scope_type="tenant", scope_key="tenant-a"
                )
                assert owner_counter is not None and tenant_counter is not None
                assert owner_counter.active_count == GLOBAL_ACTIVE_EXPORT
                assert tenant_counter.active_count == GLOBAL_ACTIVE_EXPORT
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_global_export_queue_cap_refusal_leaks_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    """A refused admission writes no row and consumes no slot."""

    _widen_the_export_knob(monkeypatch)

    async def scenario() -> None:
        owner_id = uuid.uuid4()
        engine, maker = await _database(tmp_path / "global-queued.db")
        try:
            operation_ids = await _seed(
                maker, owner_id=owner_id, operations=GLOBAL_QUEUED_EXPORT + 1
            )
            principal = PrincipalContext(owner_id, "tenant-a", "trace-a")
            async with maker() as session, session.begin():
                for operation_id in operation_ids[:GLOBAL_QUEUED_EXPORT]:
                    await admit_public_operation(
                        session,
                        principal=principal,
                        operation_id=operation_id,
                        operation_kind="export",
                        request_plan=None,
                        planned_slide_count=3,
                    )
            before = await _global_counts(maker)
            async with maker() as session, session.begin():
                with pytest.raises(PublicAdmissionRejected) as rejected:
                    await admit_public_operation(
                        session,
                        principal=principal,
                        operation_id=operation_ids[-1],
                        operation_kind="export",
                        request_plan=None,
                        planned_slide_count=3,
                    )
            assert rejected.value.code == "public_global_queue_limit_exceeded"
            # The owner/tenant refusals carry the same retry hint.
            assert rejected.value.retry_after_seconds == 15
            assert await _global_counts(maker) == before
            async with maker() as session:
                leaked = await session.scalar(
                    select(PublicOperationAdmission).where(
                        PublicOperationAdmission.operation_id == operation_ids[-1]
                    )
                )
                assert leaked is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "terminal_state",
    ["succeeded", "failed", "cancelled", "deadline_exceeded"],
)
def test_every_terminal_state_returns_the_global_slot(
    tmp_path: Path, monkeypatch, terminal_state: str
) -> None:
    """Completion, failure, cancellation and deadline all release the slot.

    Lease expiry has no separate release: the runner's recovery sweep
    terminalizes the workspace operation, and that projection funnels into
    this same ``finish_public_operation`` call.
    """

    _widen_the_export_knob(monkeypatch)

    async def scenario() -> None:
        owner_id = uuid.uuid4()
        engine, maker = await _database(tmp_path / f"release-{terminal_state}.db")
        try:
            operation_ids = await _seed(maker, owner_id=owner_id, operations=1)
            principal = PrincipalContext(owner_id, "tenant-a", "trace-a")
            async with maker() as session, session.begin():
                admission = await admit_public_operation(
                    session,
                    principal=principal,
                    operation_id=operation_ids[0],
                    operation_kind="export",
                    request_plan=None,
                    planned_slide_count=3,
                )
                admission_id = admission.id
            assert await _global_counts(maker) == (1, 0)
            # ``cancelled`` from the queue never activates; the other states
            # release an active slot.
            if terminal_state != "cancelled":
                async with maker() as session, session.begin():
                    await activate_public_operation(session, admission_id)
                assert await _global_counts(maker) == (0, 1)
            async with maker() as session, session.begin():
                await finish_public_operation(
                    session, admission_id, state=terminal_state
                )
            assert await _global_counts(maker) == (0, 0)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_one_operation_consumes_one_global_slot_across_both_layers(
    tmp_path: Path, monkeypatch
) -> None:
    """Neither a replayed admission nor the v2 layer double-counts an export."""

    _widen_the_export_knob(monkeypatch)

    async def scenario() -> None:
        owner_id = uuid.uuid4()
        engine, maker = await _database(tmp_path / "counted-once.db")
        try:
            operation_ids = await _seed(maker, owner_id=owner_id, operations=1)
            principal = PrincipalContext(owner_id, "tenant-a", "trace-a")
            async with maker() as session, session.begin():
                first = await admit_public_operation(
                    session,
                    principal=principal,
                    operation_id=operation_ids[0],
                    operation_kind="export",
                    request_plan=None,
                    planned_slide_count=3,
                )
                # An idempotent resubmission of the same command returns the
                # same row without reserving a second slot.
                replay = await admit_public_operation(
                    session,
                    principal=principal,
                    operation_id=operation_ids[0],
                    operation_kind="export",
                    request_plan=None,
                    planned_slide_count=3,
                )
                assert replay.id == first.id
            assert await _global_counts(maker) == (1, 0)
            # Offering the very same operation to the strict layer must not
            # add a second reservation either: it refuses on identity.
            async with maker() as session, session.begin():
                with pytest.raises(PublicAdmissionRejected) as rejected:
                    await admit_public_operation_compatible_v2(
                        session,
                        principal=principal,
                        operation_id=operation_ids[0],
                        operation_kind="export",
                        request_plan=None,
                        resource_plan=WeightedResourcePlan(
                            pixel_count=1920 * 1080,
                            device_pixel_ratio_milli=1000,
                            dom_node_count=2500,
                            font_count=8,
                            svg_complexity=500,
                            estimated_memory_mib=256,
                            fidelity_mode="fidelity",
                        ),
                        policy_version=PUBLIC_DEFAULT_POLICY_VERSION,
                        planned_slide_count=3,
                    )
            assert rejected.value.code == "public_operation_identity_conflict"
            assert await _global_counts(maker) == (1, 0)
            async with maker() as session:
                admissions = list(
                    (
                        await session.execute(
                            select(PublicOperationAdmission).where(
                                PublicOperationAdmission.operation_id
                                == operation_ids[0]
                            )
                        )
                    ).scalars()
                )
                assert len(admissions) == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())
