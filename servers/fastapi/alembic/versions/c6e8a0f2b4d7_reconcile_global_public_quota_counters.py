"""Reconcile public_quota_counters for the global scope before the new accounting runs.

Revision ID: c6e8a0f2b4d7
Revises: b4e6f8a0c2d5

``80b148b28`` made the Phase-1 admission path (used by export operations)
account the global quota scope, which it never did before -- Phase-1 admit,
activate and finish now walk the shared ``global, tenant, owner`` hierarchy
instead of only ``tenant, owner``. Rows admitted by the OLD code never
reserved a global slot, but the NEW code releases one for every admission row
it finishes, raising ``RuntimeError("public_quota_counter_underflow")`` (or
the ``PublicAdmissionRejected`` variant of the same message) from
``services/public_operation_admission.py`` the first time such a row
transitions.

This is a one-time data migration, not a schema change: it recomputes every
global ``(policy_version, operation_kind)`` counter from the non-terminal
rows already in ``public_operation_admissions`` -- ground truth the runtime
itself trusts -- using the exact state -> bucket mapping the runtime uses
(``services/public_quota_reconciliation.py``, mirrored from
``finish_public_operation_v2`` / ``activate_public_operation`` in
``services/public_operation_admission.py``). It is a pure SET computed fresh
each run, so it is idempotent and safe to re-run.

The reconciliation logic lives in ``services/public_quota_reconciliation.py``
(not inline here) so it has a normal unit-test surface; this revision only
supplies the transactional connection.
"""

from __future__ import annotations

from alembic import op

from services.public_quota_reconciliation import reconcile_global_quota_counters


revision: str = "c6e8a0f2b4d7"
down_revision: str | None = "b4e6f8a0c2d5"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    reconcile_global_quota_counters(op.get_bind())


def downgrade() -> None:
    """No-op: reverting a counter reconciliation is meaningless.

    The counters this revision writes are a derived snapshot of
    ``public_operation_admissions`` at the moment it ran, not new state that
    can be "undone" -- by the time anyone downgrades, the admission rows have
    moved on and any prior counter values are stale by construction. Doing
    nothing leaves the counters correct; restoring old values would corrupt
    them.
    """
