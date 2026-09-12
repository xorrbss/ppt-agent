"""Count lease-less admissions in the global quota consistency invariant.

Revision ID: d4a7c9e1b3f6
Revises: c6e8a0f2b4d7

``fb5e9c3a7d10`` installed ``public_validate_quota_counter_consistency()`` as a
``DEFERRABLE INITIALLY DEFERRED`` constraint trigger on
``public_quota_counters`` and ``public_resource_leases``: at COMMIT it derives
the expected counters from ``public_resource_leases`` and raises
``public_quota_counter_drift`` when the persisted counter disagrees.

Because the Phase-1 admission path (``admit_public_operation``) increments
counters without ever creating a ``public_resource_leases`` row -- leases and
resource units are the v2 path's job -- that function already carries a
reconciliation branch that adds non-terminal admissions with no lease row to
the lease-derived expectation. That branch was gated
``IF target_scope_type IN ('tenant', 'owner')``, which was correct when
Phase-1 only accounted the tenant and owner scopes.

``80b148b28`` made Phase-1 account the **global** scope as well (see
``_QUOTA_SCOPES`` in ``services/public_operation_admission.py``). The global
counter is therefore raised by every Phase-1 admission while the trigger still
expects the lease-derived total of 0, so the drift check rejects the whole
transaction at COMMIT -- after the deck has already been authored, rendered
and persisted.

This revision replaces the function so the same lease-less reconciliation also
applies to ``global``: expected global counts become the lease-derived totals
for that ``(policy_version, operation_kind)`` plus the non-terminal admissions
that hold no lease row. Nothing else about the function changes -- the
lease-derived query, the resource-unit expectations (Phase-1 reserves no
units, exactly as the tenant/owner branch already assumes), the drift
exception and the ``TG_OP = 'DELETE'`` handling are untouched -- and the
triggers themselves are left exactly as installed, still deferred to COMMIT.

``c6e8a0f2b4d7`` does not address this: it reconciles counter *values* from
non-terminal admissions, while the trigger derives its expectation from
*leases*. The two only agree once the function itself counts lease-less
admissions in the global scope.

PostgreSQL-only: SQLite has no such trigger, so both directions are a no-op
there, following ``fb5e9c3a7d10``'s dialect guard.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op


revision: str = "d4a7c9e1b3f6"
down_revision: str | None = "c6e8a0f2b4d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


QUOTAS = "public_quota_counters"
ADMISSIONS = "public_operation_admissions"
LEASES = "public_resource_leases"

# The two variants of the single line that differs between this revision and
# `fb5e9c3a7d10`: which scope types reconcile lease-less admissions, and how an
# admission row is matched to the scope key being validated. Everything else in
# the function body below is copied verbatim from `fb5e9c3a7d10`.
_PREVIOUS_SCOPE_TYPES = "('tenant', 'owner')"
_PREVIOUS_SCOPE_MATCH = """AND ((target_scope_type = 'tenant' AND a.tenant_id = target_scope_key)
                   OR (target_scope_type = 'owner' AND a.owner_id::text = target_scope_key))"""

# `global` owns no admission column to join on: every admission of that
# `(policy_version, operation_kind)` counts against the single global counter,
# which is exactly what `services/public_quota_reconciliation.py` computes.
_FIXED_SCOPE_TYPES = "('global', 'tenant', 'owner')"
_FIXED_SCOPE_MATCH = """AND (target_scope_type = 'global'
                   OR (target_scope_type = 'tenant' AND a.tenant_id = target_scope_key)
                   OR (target_scope_type = 'owner' AND a.owner_id::text = target_scope_key))"""


def _consistency_function_sql(*, scope_types: str, scope_match: str) -> str:
    """Render `public_validate_quota_counter_consistency()` for one scope gate."""

    return f"""
        CREATE OR REPLACE FUNCTION public_validate_quota_counter_consistency()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
          target_policy_version text;
          target_scope_type text;
          target_scope_key text;
          target_operation_kind text;
          expected_queued_count bigint;
          expected_active_count bigint;
          expected_queued_units bigint;
          expected_active_units bigint;
          legacy_queued_count bigint := 0;
          legacy_active_count bigint := 0;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            target_policy_version := OLD.policy_version;
            target_scope_type := OLD.scope_type;
            target_scope_key := OLD.scope_key;
            target_operation_kind := OLD.operation_kind;
          ELSE
            target_policy_version := NEW.policy_version;
            target_scope_type := NEW.scope_type;
            target_scope_key := NEW.scope_key;
            target_operation_kind := NEW.operation_kind;
          END IF;

          SELECT
            count(*) FILTER (WHERE state = 'queued'),
            count(*) FILTER (WHERE state = 'leased'),
            COALESCE(sum(resource_units) FILTER (WHERE state = 'queued'), 0),
            COALESCE(sum(resource_units) FILTER (WHERE state = 'leased'), 0)
          INTO expected_queued_count, expected_active_count,
               expected_queued_units, expected_active_units
          FROM {LEASES}
          WHERE policy_version = target_policy_version
            AND scope_type = target_scope_type
            AND scope_key = target_scope_key
            AND operation_kind = target_operation_kind;

          IF target_scope_type IN {scope_types} THEN
            SELECT
              count(*) FILTER (WHERE a.state = 'queued'),
              count(*) FILTER (WHERE a.state = 'active')
            INTO legacy_queued_count, legacy_active_count
            FROM {ADMISSIONS} a
            WHERE a.policy_version = target_policy_version
              AND a.operation_kind = target_operation_kind
              {scope_match}
              AND NOT EXISTS (
                SELECT 1 FROM {LEASES} l WHERE l.admission_id = a.id
              );
          END IF;

          expected_queued_count := expected_queued_count + legacy_queued_count;
          expected_active_count := expected_active_count + legacy_active_count;

          IF NOT EXISTS (
            SELECT 1 FROM {QUOTAS} q
            WHERE q.policy_version = target_policy_version
              AND q.scope_type = target_scope_type
              AND q.scope_key = target_scope_key
              AND q.operation_kind = target_operation_kind
              AND q.queued_count = expected_queued_count
              AND q.active_count = expected_active_count
              AND q.queued_resource_units = expected_queued_units
              AND q.active_resource_units = expected_active_units
          ) THEN
            RAISE EXCEPTION 'public_quota_counter_drift';
          END IF;
          IF TG_OP = 'DELETE' THEN
            RETURN OLD;
          END IF;
          RETURN NEW;
        END;
        $$;
        """


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        _consistency_function_sql(
            scope_types=_FIXED_SCOPE_TYPES, scope_match=_FIXED_SCOPE_MATCH
        )
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        _consistency_function_sql(
            scope_types=_PREVIOUS_SCOPE_TYPES, scope_match=_PREVIOUS_SCOPE_MATCH
        )
    )
