"""Reconcile ``public_quota_counters`` for the global scope against ground truth.

Commit ``80b148b28`` made the Phase-1 admission path (``admit_public_operation``
/ ``activate_public_operation`` / ``finish_public_operation`` in
``services/public_operation_admission.py``) account the global quota scope for
the first time; before that commit the Phase-1 path only reserved tenant and
owner slots. Rows admitted by the OLD code therefore never reserved a global
slot, but the NEW code releases one for every admission row it finishes --
raising ``RuntimeError("public_quota_counter_underflow")`` (or the queued/
active variants of ``PublicAdmissionRejected("public_quota_counter_underflow")``)
the first time an old row transitions.

This module derives the correct ``active_count``/``queued_count`` (and their
resource-unit twins) for every global ``(policy_version, operation_kind)``
counter from the non-terminal rows in ``public_operation_admissions``, using
the exact same state -> bucket mapping the runtime uses:

    state == "active" -> active_count / active_resource_units
    state == "queued" -> queued_count / queued_resource_units

(see ``finish_public_operation_v2`` and ``activate_public_operation`` in
``services/public_operation_admission.py``, and ``_TERMINAL_STATES`` in
``services/public_operation_admission_models.py`` -- every other state is
terminal and holds no slot in any scope) -- so the backfill and the runtime
agree.

The result is always a SET computed fresh from the admission table, never an
increment, so calling this repeatedly against the same data is idempotent:
a row already at its correct value is left untouched.
"""

from __future__ import annotations

from sqlalchemy import case, func, select
from sqlalchemy.engine import Connection

from models.sql.public_admission import PublicOperationAdmission, PublicQuotaCounter


GLOBAL_SCOPE_TYPE = "global"
GLOBAL_SCOPE_KEY = "global"

# Mirrors `_TERMINAL_STATES` in `services/public_operation_admission_models.py`:
# every admission state other than these two is terminal and holds no slot.
_ACTIVE_STATE = "active"
_QUEUED_STATE = "queued"
_NON_TERMINAL_STATES = (_ACTIVE_STATE, _QUEUED_STATE)

_COUNTER_FIELDS = (
    "active_count",
    "queued_count",
    "active_resource_units",
    "queued_resource_units",
)
_ZERO_COUNTERS = {field: 0 for field in _COUNTER_FIELDS}


def compute_global_quota_truth(
    connection: Connection,
) -> dict[tuple[str, str], dict[str, int]]:
    """Return correct global counter values keyed by (policy_version, operation_kind).

    Only combinations with at least one non-terminal admission row are
    included; a caller reconciling a persisted counter table is responsible
    for zeroing any existing global row whose combination is absent here.
    """

    table = PublicOperationAdmission.__table__
    active_count = func.sum(case((table.c.state == _ACTIVE_STATE, 1), else_=0))
    queued_count = func.sum(case((table.c.state == _QUEUED_STATE, 1), else_=0))
    active_units = func.sum(
        case((table.c.state == _ACTIVE_STATE, table.c.resource_units), else_=0)
    )
    queued_units = func.sum(
        case((table.c.state == _QUEUED_STATE, table.c.resource_units), else_=0)
    )

    rows = connection.execute(
        select(
            table.c.policy_version,
            table.c.operation_kind,
            active_count.label("active_count"),
            queued_count.label("queued_count"),
            active_units.label("active_resource_units"),
            queued_units.label("queued_resource_units"),
        )
        .where(table.c.state.in_(_NON_TERMINAL_STATES))
        .group_by(table.c.policy_version, table.c.operation_kind)
    ).all()

    return {
        (row.policy_version, row.operation_kind): {
            "active_count": int(row.active_count or 0),
            "queued_count": int(row.queued_count or 0),
            "active_resource_units": int(row.active_resource_units or 0),
            "queued_resource_units": int(row.queued_resource_units or 0),
        }
        for row in rows
    }


def reconcile_global_quota_counters(connection: Connection) -> int:
    """Set every global `public_quota_counters` row to match admission ground truth.

    Creates the counter row when it is missing entirely -- the pre-``80b148b28``
    Phase-1 path never created one for export operations -- and zeroes any
    existing global counter whose ``(policy_version, operation_kind)`` has no
    non-terminal admission rows left. A row already correct is left untouched,
    so re-running this against unchanged data is a no-op. Returns the number
    of counter rows actually written.
    """

    counter_table = PublicQuotaCounter.__table__
    truth = compute_global_quota_truth(connection)

    existing_rows = connection.execute(
        select(
            counter_table.c.policy_version,
            counter_table.c.operation_kind,
            counter_table.c.active_count,
            counter_table.c.queued_count,
            counter_table.c.active_resource_units,
            counter_table.c.queued_resource_units,
        ).where(counter_table.c.scope_type == GLOBAL_SCOPE_TYPE)
    ).all()
    existing_by_scope = {
        (row.policy_version, row.operation_kind): row for row in existing_rows
    }

    scopes = set(truth) | set(existing_by_scope)
    touched = 0
    for policy_version, operation_kind in sorted(scopes):
        target = truth.get((policy_version, operation_kind), _ZERO_COUNTERS)
        current = existing_by_scope.get((policy_version, operation_kind))
        if current is not None and all(
            getattr(current, field) == target[field] for field in _COUNTER_FIELDS
        ):
            continue
        if current is None:
            connection.execute(
                counter_table.insert().values(
                    policy_version=policy_version,
                    scope_type=GLOBAL_SCOPE_TYPE,
                    scope_key=GLOBAL_SCOPE_KEY,
                    operation_kind=operation_kind,
                    updated_at=func.now(),
                    **target,
                )
            )
        else:
            connection.execute(
                counter_table.update()
                .where(
                    counter_table.c.scope_type == GLOBAL_SCOPE_TYPE,
                    counter_table.c.scope_key == GLOBAL_SCOPE_KEY,
                    counter_table.c.policy_version == policy_version,
                    counter_table.c.operation_kind == operation_kind,
                )
                .values(updated_at=func.now(), **target)
            )
        touched += 1
    return touched
