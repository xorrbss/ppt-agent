"""Evaluate the quota invariant outside the request principal's RLS view.

The invariant compares shared counters with all matching leases/admissions,
including other owners and tenants. An invoker trigger sees only the caller's
RLS rows. Keep the d4a7c9e1b3f6 arithmetic and installed deferred triggers;
replace only their function, preserving its existing privileged owner.

No role, policy, table privilege, counter or admission is changed. Execution is
restricted to the two existing trigger bindings. Trigger firing does not need
the caller's EXECUTE privilege (checked when the trigger was installed).
"""

from alembic import op

revision = "f6c8e0a2b4d7"
down_revision = "f5b7d9e1a3c6"
branch_labels = None
depends_on = None

_FUNCTION = "public.public_validate_quota_counter_consistency()"

# CREATE OR REPLACE preserves the function owner. FORCE RLS also affects table
# owners, so require the actual function owner to bypass RLS before installing
# SECURITY DEFINER. No migration-login assumption or role escalation is used.
_OWNER_PREFLIGHT_SQL = """
DO $quota_invariant_owner$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_catalog.pg_proc AS p
    JOIN pg_catalog.pg_roles AS r ON r.oid = p.proowner
    WHERE p.oid = 'public.public_validate_quota_counter_consistency()'::regprocedure
      AND (r.rolsuper OR r.rolbypassrls)
  ) THEN
    RAISE EXCEPTION 'public_quota_invariant_owner_authority_required'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
END;
$quota_invariant_owner$;
"""

# All relation references are qualified, pg_temp is last, and the function has
# no caller-supplied SQL. row_security=off fails closed if owner authority is
# subsequently removed rather than silently comparing a partial RLS view.
_CONSISTENCY_FUNCTION_SQL = """
        CREATE OR REPLACE FUNCTION public.public_validate_quota_counter_consistency()
        RETURNS trigger LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        SET row_security = off
        AS $$
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
          IF TG_WHEN <> 'AFTER' OR TG_LEVEL <> 'ROW' OR TG_NARGS <> 0
             OR TG_OP NOT IN ('INSERT', 'UPDATE', 'DELETE')
             OR NOT (
               (TG_RELID = 'public.public_resource_leases'::regclass
                AND TG_NAME = 'trg_public_resource_lease_counter_consistent')
               OR (TG_RELID = 'public.public_quota_counters'::regclass
                   AND TG_NAME = 'trg_public_quota_counter_consistent')
             ) THEN
            RAISE EXCEPTION 'public_quota_invariant_trigger_required'
              USING ERRCODE = 'insufficient_privilege';
          END IF;
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
          FROM public.public_resource_leases
          WHERE policy_version = target_policy_version
            AND scope_type = target_scope_type
            AND scope_key = target_scope_key
            AND operation_kind = target_operation_kind;

          IF target_scope_type IN ('global', 'tenant', 'owner') THEN
            SELECT
              count(*) FILTER (WHERE a.state = 'queued'),
              count(*) FILTER (WHERE a.state = 'active')
            INTO legacy_queued_count, legacy_active_count
            FROM public.public_operation_admissions a
            WHERE a.policy_version = target_policy_version
              AND a.operation_kind = target_operation_kind
              AND (target_scope_type = 'global'
                   OR (target_scope_type = 'tenant' AND a.tenant_id = target_scope_key)
                   OR (target_scope_type = 'owner' AND a.owner_id::text = target_scope_key))
              AND NOT EXISTS (
                SELECT 1 FROM public.public_resource_leases l WHERE l.admission_id = a.id
              );
          END IF;

          expected_queued_count := expected_queued_count + legacy_queued_count;
          expected_active_count := expected_active_count + legacy_active_count;

          IF NOT EXISTS (
            SELECT 1 FROM public.public_quota_counters q
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
    op.execute(_OWNER_PREFLIGHT_SQL)
    op.execute(_CONSISTENCY_FUNCTION_SQL)
    # PostgreSQL functions default to PUBLIC EXECUTE; runtime roles need none
    # for these already-installed triggers. Their table/RLS grants stay intact.
    op.execute(
        f"REVOKE ALL ON FUNCTION {_FUNCTION} "
        "FROM PUBLIC, presenton_public_api, presenton_public_controller"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    # Restore the former invoker/config/default-EXECUTE contract while retaining
    # the exact arithmetic, qualified relations and harmless trigger binding.
    op.execute(f"ALTER FUNCTION {_FUNCTION} SECURITY INVOKER")
    op.execute(f"ALTER FUNCTION {_FUNCTION} RESET ALL")
    op.execute(f"GRANT EXECUTE ON FUNCTION {_FUNCTION} TO PUBLIC")
