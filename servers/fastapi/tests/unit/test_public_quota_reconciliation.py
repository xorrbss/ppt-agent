"""Reconciliation of global `public_quota_counters` against admission ground truth.

Regression coverage for the hazard closed alongside migration `c6e8a0f2b4d7`:
`80b148b28` made the Phase-1 admission path (used by export operations)
account the global quota scope, which it never did before. Rows admitted by
the OLD code never reserved a global slot, but the NEW code releases one for
every admission row it finishes, raising
`RuntimeError("public_quota_counter_underflow")` from
`services/public_operation_admission.py` the first time such a row
transitions. These tests exercise `reconcile_global_quota_counters` directly
against a seeded `public_operation_admissions` table, independent of the
migration harness.
"""

from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlmodel import select

from models.sql.public_admission import PublicOperationAdmission, PublicQuotaCounter
from services.public_quota_reconciliation import reconcile_global_quota_counters
from utils.datetime_utils import get_current_utc_datetime

# Imported only so SQLAlchemy's DDL compiler can resolve the admission
# table's foreign keys (`operation_id` -> workspace_operations.id, `owner_id`
# -> user.id) against SQLModel's shared metadata. Neither table is created or
# used: this module seeds and reconciles `public_operation_admissions` and
# `public_quota_counters` directly, with no FK enforcement (SQLite leaves it
# off unless `PRAGMA foreign_keys=ON`, which this test never sets).
from models.sql.user import User  # noqa: F401
from models.sql.workspace import WorkspaceOperation  # noqa: F401


_POLICY_DIGEST = sha256(b"reconciliation-test-policy").hexdigest()


def _engine():
    # Create only the two tables this module touches, each with a single
    # `CREATE TABLE` rather than `SQLModel.metadata.create_all`: the admission
    # table's foreign keys point at `workspace_operations`/`user`, which this
    # module has no reason to import, and `metadata.create_all`'s dependency
    # sort insists on resolving every referenced table even when it is not in
    # the requested `tables=` list. SQLite does not enforce or validate FK
    # targets at DDL time (nor at all unless `PRAGMA foreign_keys=ON`, which
    # this test never sets), so the unresolved references are harmless here.
    engine = create_engine("sqlite:///:memory:")
    PublicOperationAdmission.__table__.create(engine)
    PublicQuotaCounter.__table__.create(engine)
    return engine


def _admission(
    *,
    policy_version: str,
    operation_kind: str,
    state: str,
    resource_units: int,
    owner_id: uuid.UUID | None = None,
) -> PublicOperationAdmission:
    now = get_current_utc_datetime()
    return PublicOperationAdmission(
        operation_id=uuid.uuid4(),
        policy_version=policy_version,
        owner_id=owner_id or uuid.uuid4(),
        tenant_id="tenant-a",
        trace_id_hash=sha256(uuid.uuid4().bytes).hexdigest(),
        operation_kind=operation_kind,
        resource_units=resource_units,
        policy_digest=_POLICY_DIGEST,
        deadline_at=now + timedelta(hours=1),
        state=state,
        planned_request_limit=0,
        retry_request_limit=0,
        estimated_cost_limit_microunits=0,
        actual_cost_limit_microunits=0,
    )


def _counter(
    *, policy_version: str, operation_kind: str, active_count: int, queued_count: int,
    active_resource_units: int, queued_resource_units: int,
) -> PublicQuotaCounter:
    return PublicQuotaCounter(
        policy_version=policy_version,
        scope_type="global",
        scope_key="global",
        operation_kind=operation_kind,
        active_count=active_count,
        queued_count=queued_count,
        active_resource_units=active_resource_units,
        queued_resource_units=queued_resource_units,
    )


def _load_counter(
    session: Session, *, policy_version: str, operation_kind: str
) -> PublicQuotaCounter | None:
    return session.scalars(
        select(PublicQuotaCounter).where(
            PublicQuotaCounter.scope_type == "global",
            PublicQuotaCounter.policy_version == policy_version,
            PublicQuotaCounter.operation_kind == operation_kind,
        )
    ).first()


def test_reconcile_creates_missing_counter_from_mixed_admission_states() -> None:
    """A never-created global export counter is backfilled from admission rows.

    Mirrors the pre-`80b148b28` Phase-1 export path: two active rows, three
    queued rows, and one terminal row that must not count.
    """

    engine = _engine()
    with Session(engine) as session:
        session.add_all(
            [
                _admission(
                    policy_version="public-default/v5",
                    operation_kind="export",
                    state="active",
                    resource_units=3,
                ),
                _admission(
                    policy_version="public-default/v5",
                    operation_kind="export",
                    state="active",
                    resource_units=4,
                ),
                _admission(
                    policy_version="public-default/v5",
                    operation_kind="export",
                    state="queued",
                    resource_units=1,
                ),
                _admission(
                    policy_version="public-default/v5",
                    operation_kind="export",
                    state="queued",
                    resource_units=1,
                ),
                _admission(
                    policy_version="public-default/v5",
                    operation_kind="export",
                    state="queued",
                    resource_units=2,
                ),
                _admission(
                    policy_version="public-default/v5",
                    operation_kind="export",
                    state="succeeded",
                    resource_units=99,
                ),
            ]
        )
        session.commit()

    with engine.begin() as connection:
        touched = reconcile_global_quota_counters(connection)
    assert touched == 1

    with Session(engine) as session:
        counter = _load_counter(
            session, policy_version="public-default/v5", operation_kind="export"
        )
        assert counter is not None
        assert counter.active_count == 2
        assert counter.queued_count == 3
        assert counter.active_resource_units == 7
        assert counter.queued_resource_units == 4


def test_reconcile_corrects_stale_counter_and_zeroes_an_all_terminal_scope() -> None:
    """An existing wrong counter is corrected; a now-all-terminal scope is zeroed."""

    engine = _engine()
    with Session(engine) as session:
        # Stale: counter says nothing is queued/active, but one active and one
        # queued row exist (the exact shape that later underflows on finish).
        session.add(
            _counter(
                policy_version="public-default/v4",
                operation_kind="generation",
                active_count=0,
                queued_count=0,
                active_resource_units=0,
                queued_resource_units=0,
            )
        )
        session.add_all(
            [
                _admission(
                    policy_version="public-default/v4",
                    operation_kind="generation",
                    state="active",
                    resource_units=5,
                ),
                _admission(
                    policy_version="public-default/v4",
                    operation_kind="generation",
                    state="queued",
                    resource_units=2,
                ),
            ]
        )
        # A scope whose counter still carries a slot for a row that has since
        # terminated -- must be reconciled down to zero, not left positive.
        session.add(
            _counter(
                policy_version="public-default/v5",
                operation_kind="export",
                active_count=1,
                queued_count=1,
                active_resource_units=9,
                queued_resource_units=9,
            )
        )
        session.add(
            _admission(
                policy_version="public-default/v5",
                operation_kind="export",
                state="failed",
                resource_units=9,
            )
        )
        session.commit()

    with engine.begin() as connection:
        touched = reconcile_global_quota_counters(connection)
    assert touched == 2

    with Session(engine) as session:
        generation_counter = _load_counter(
            session, policy_version="public-default/v4", operation_kind="generation"
        )
        assert generation_counter is not None
        assert generation_counter.active_count == 1
        assert generation_counter.queued_count == 1
        assert generation_counter.active_resource_units == 5
        assert generation_counter.queued_resource_units == 2

        export_counter = _load_counter(
            session, policy_version="public-default/v5", operation_kind="export"
        )
        assert export_counter is not None
        assert export_counter.active_count == 0
        assert export_counter.queued_count == 0
        assert export_counter.active_resource_units == 0
        assert export_counter.queued_resource_units == 0


def test_reconcile_is_idempotent() -> None:
    """Re-running against unchanged data writes nothing and changes nothing."""

    engine = _engine()
    with Session(engine) as session:
        session.add_all(
            [
                _admission(
                    policy_version="public-default/v5",
                    operation_kind="export",
                    state="active",
                    resource_units=3,
                ),
                _admission(
                    policy_version="public-default/v5",
                    operation_kind="export",
                    state="queued",
                    resource_units=6,
                ),
            ]
        )
        session.commit()

    with engine.begin() as connection:
        first_touched = reconcile_global_quota_counters(connection)
    assert first_touched == 1

    with Session(engine) as session:
        before = _load_counter(
            session, policy_version="public-default/v5", operation_kind="export"
        )
        assert before is not None
        snapshot = (
            before.active_count,
            before.queued_count,
            before.active_resource_units,
            before.queued_resource_units,
        )

    with engine.begin() as connection:
        second_touched = reconcile_global_quota_counters(connection)
    assert second_touched == 0

    with Session(engine) as session:
        after = _load_counter(
            session, policy_version="public-default/v5", operation_kind="export"
        )
        assert after is not None
        assert (
            after.active_count,
            after.queued_count,
            after.active_resource_units,
            after.queued_resource_units,
        ) == snapshot
