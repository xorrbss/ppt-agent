"""Add PostgreSQL-authoritative public admission control-plane state.

Revision ID: fb5e9c3a7d10
Revises: fa4d8b2e6c90
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
import hashlib

from alembic import op
import sqlalchemy as sa


revision: str = "fb5e9c3a7d10"
down_revision: str | None = "fa4d8b2e6c90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OPERATIONS = "workspace_operations"
POLICIES = "public_policy_profiles"
QUOTAS = "public_quota_counters"
ADMISSIONS = "public_operation_admissions"
LEASES = "public_resource_leases"
ARTIFACTS = "public_artifacts"
EXPORT_IDENTITIES = "public_export_identities"
PUBLICATIONS = "public_artifact_publications"
CHECKPOINTS = "public_slide_checkpoints"

_LEGACY_CONTRACT_VERSION = "public-generation-export/v1"


def _backfill_legacy_export_identity_components() -> None:
    """Mark pre-v2 rows unreusable without inventing component equivalence."""

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            f"SELECT id, canonical_digest, exporter_version FROM {EXPORT_IDENTITIES}"
        )
    ).mappings().all()
    statement = sa.text(
        f"""
        UPDATE {EXPORT_IDENTITIES}
           SET scene_digest = :scene_digest,
               semantic_digest = :semantic_digest,
               quality_digest = :quality_digest,
               exporter_digest = :exporter_digest,
               render_adapter = 'legacy-unreusable-v1',
               output_mode = 'fidelity-raster',
               identity_contract_version = 'legacy-unreusable/v1',
               reuse_eligible = false
         WHERE id = :identity_id
        """
    )
    for row in rows:
        seed = f"{row['id']}:{row['canonical_digest']}:{row['exporter_version']}"
        bind.execute(
            statement,
            {
                "identity_id": row["id"],
                "scene_digest": hashlib.sha256(
                    f"legacy-unavailable:scene:{seed}".encode("utf-8")
                ).hexdigest(),
                "semantic_digest": hashlib.sha256(
                    f"legacy-unavailable:semantic:{seed}".encode("utf-8")
                ).hexdigest(),
                "quality_digest": hashlib.sha256(
                    f"legacy-unavailable:quality:{seed}".encode("utf-8")
                ).hexdigest(),
                "exporter_digest": hashlib.sha256(
                    f"legacy-unavailable:exporter:{seed}".encode("utf-8")
                ).hexdigest(),
            },
        )


def _as_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _backfill_existing_admissions() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            f"""
            SELECT a.id, a.operation_id, a.policy_version, a.operation_kind,
                   a.slide_count, a.queued_at, w.fencing_token,
                   p.generation_deadline_seconds, p.export_deadline_seconds
            FROM {ADMISSIONS} a
            JOIN {OPERATIONS} w ON w.id = a.operation_id
            JOIN {POLICIES} p ON p.version = a.policy_version
            """
        )
    ).mappings()
    for row in rows:
        deadline_seconds = (
            row["generation_deadline_seconds"]
            if row["operation_kind"] == "generation"
            else row["export_deadline_seconds"]
        )
        deadline_at = _as_datetime(row["queued_at"]) + timedelta(
            seconds=int(deadline_seconds)
        )
        policy_version = str(row["policy_version"])
        policy_digest = hashlib.sha256(policy_version.encode("utf-8")).hexdigest()
        resource_units = max(1, int(row["slide_count"] or 0))
        bind.execute(
            sa.text(
                f"""
                UPDATE {OPERATIONS}
                SET contract_version = :contract_version,
                    execution_profile_version = :profile_version,
                    phase = :phase,
                    deadline_at = :deadline_at
                WHERE id = :operation_id
                """
            ),
            {
                "contract_version": _LEGACY_CONTRACT_VERSION,
                "profile_version": policy_version,
                "phase": "legacy_admission",
                "deadline_at": deadline_at,
                "operation_id": row["operation_id"],
            },
        )
        bind.execute(
            sa.text(
                f"""
                UPDATE {ADMISSIONS}
                SET resource_units = :resource_units,
                    policy_digest = :policy_digest,
                    deadline_at = :deadline_at,
                    fence = :fence
                WHERE id = :admission_id
                """
            ),
            {
                "resource_units": resource_units,
                "policy_digest": policy_digest,
                "deadline_at": deadline_at,
                "fence": int(row["fencing_token"] or 0),
                "admission_id": row["id"],
            },
        )


def _install_postgresql_authority() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public_control_plane_authority_revision()
        RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
          SELECT '{revision}'::text
        $$;

        CREATE OR REPLACE FUNCTION public_guard_control_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_TABLE_NAME = '{OPERATIONS}' THEN
            IF NEW.id IS DISTINCT FROM OLD.id THEN
              RAISE EXCEPTION 'public_operation_id_is_immutable';
            END IF;
            IF OLD.deadline_at IS NOT NULL
               AND NEW.deadline_at IS DISTINCT FROM OLD.deadline_at THEN
              RAISE EXCEPTION 'public_absolute_deadline_is_immutable';
            END IF;
            IF OLD.execution_profile_version = 'public-generation-export/v2'
               AND (NEW.execution_profile_version IS DISTINCT FROM OLD.execution_profile_version
                    OR NEW.contract_version IS DISTINCT FROM OLD.contract_version) THEN
              RAISE EXCEPTION 'public_operation_execution_contract_is_immutable';
            END IF;
            IF (OLD.execution_profile_version = 'public-generation-export/v2'
                OR NEW.execution_profile_version = 'public-generation-export/v2')
               AND NEW.fencing_token < OLD.fencing_token THEN
              RAISE EXCEPTION 'public_operation_fence_must_be_monotonic';
            END IF;
          ELSIF TG_TABLE_NAME = '{ADMISSIONS}' THEN
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.operation_id IS DISTINCT FROM OLD.operation_id
               OR NEW.policy_version IS DISTINCT FROM OLD.policy_version
               OR NEW.owner_id IS DISTINCT FROM OLD.owner_id
               OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
               OR NEW.operation_kind IS DISTINCT FROM OLD.operation_kind
               OR NEW.resource_units IS DISTINCT FROM OLD.resource_units
               OR NEW.policy_digest IS DISTINCT FROM OLD.policy_digest THEN
              RAISE EXCEPTION 'public_admission_identity_is_immutable';
            END IF;
            IF NEW.deadline_at IS DISTINCT FROM OLD.deadline_at THEN
              RAISE EXCEPTION 'public_absolute_deadline_is_immutable';
            END IF;
            IF NEW.fence < OLD.fence THEN
              RAISE EXCEPTION 'public_admission_fence_must_be_monotonic';
            END IF;
          ELSIF TG_TABLE_NAME = '{LEASES}' THEN
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.admission_id IS DISTINCT FROM OLD.admission_id
               OR NEW.operation_id IS DISTINCT FROM OLD.operation_id
               OR NEW.policy_version IS DISTINCT FROM OLD.policy_version
               OR NEW.scope_type IS DISTINCT FROM OLD.scope_type
               OR NEW.scope_key IS DISTINCT FROM OLD.scope_key
               OR NEW.operation_kind IS DISTINCT FROM OLD.operation_kind
               OR NEW.resource_units IS DISTINCT FROM OLD.resource_units THEN
              RAISE EXCEPTION 'public_resource_lease_identity_is_immutable';
            END IF;
            IF NEW.deadline_at IS DISTINCT FROM OLD.deadline_at THEN
              RAISE EXCEPTION 'public_absolute_deadline_is_immutable';
            END IF;
            IF NEW.fence < OLD.fence OR NEW.attempt < OLD.attempt
               OR NEW.version < OLD.version THEN
              RAISE EXCEPTION 'public_resource_lease_control_must_be_monotonic';
            END IF;
          ELSE
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.policy_version IS DISTINCT FROM OLD.policy_version
               OR NEW.scope_type IS DISTINCT FROM OLD.scope_type
               OR NEW.scope_key IS DISTINCT FROM OLD.scope_key
               OR NEW.operation_kind IS DISTINCT FROM OLD.operation_kind THEN
              RAISE EXCEPTION 'public_quota_counter_identity_is_immutable';
            END IF;
            IF NEW.version < OLD.version THEN
              RAISE EXCEPTION 'public_quota_counter_version_must_be_monotonic';
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE TRIGGER trg_workspace_operation_control_guard
        BEFORE UPDATE ON {OPERATIONS}
        FOR EACH ROW EXECUTE FUNCTION public_guard_control_update();

        CREATE TRIGGER trg_public_admission_control_guard
        BEFORE UPDATE ON {ADMISSIONS}
        FOR EACH ROW EXECUTE FUNCTION public_guard_control_update();

        CREATE TRIGGER trg_public_resource_lease_control_guard
        BEFORE UPDATE ON {LEASES}
        FOR EACH ROW EXECUTE FUNCTION public_guard_control_update();

        CREATE TRIGGER trg_public_quota_counter_control_guard
        BEFORE UPDATE ON {QUOTAS}
        FOR EACH ROW EXECUTE FUNCTION public_guard_control_update();
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public_validate_admission_operation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_TABLE_NAME = '{ADMISSIONS}' THEN
            IF NOT EXISTS (
              SELECT 1
              FROM {ADMISSIONS} a
              JOIN {OPERATIONS} w ON w.id = a.operation_id
              WHERE a.id = NEW.id
                AND w.owner_id IS NOT DISTINCT FROM a.owner_id
                AND (w.execution_profile_version IS DISTINCT FROM 'public-generation-export/v2'
                     OR (a.fence >= 1
                         AND w.fencing_token IS NOT DISTINCT FROM a.fence
                         AND w.deadline_at IS NOT DISTINCT FROM a.deadline_at))
            ) THEN
              RAISE EXCEPTION 'public_admission_operation_authority_unavailable';
            END IF;
          ELSE
            IF EXISTS (
              SELECT 1 FROM {OPERATIONS} w
              WHERE w.id = NEW.id
                AND w.execution_profile_version = 'public-generation-export/v2'
            ) AND NOT EXISTS (
              SELECT 1
              FROM {OPERATIONS} w
              JOIN {ADMISSIONS} a ON a.operation_id = w.id
              WHERE w.id = NEW.id
                AND w.fencing_token >= 1
                AND a.owner_id IS NOT DISTINCT FROM w.owner_id
                AND a.fence IS NOT DISTINCT FROM w.fencing_token
                AND a.deadline_at IS NOT DISTINCT FROM w.deadline_at
            ) THEN
              RAISE EXCEPTION 'public_operation_admission_authority_unavailable';
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE CONSTRAINT TRIGGER trg_public_admission_operation_consistent
        AFTER INSERT OR UPDATE ON {ADMISSIONS}
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public_validate_admission_operation();

        CREATE CONSTRAINT TRIGGER trg_workspace_operation_admission_consistent
        AFTER INSERT OR UPDATE ON {OPERATIONS}
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public_validate_admission_operation();
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public_validate_resource_lease()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
          execution_profile text;
          required_scope_count integer;
        BEGIN
          IF TG_TABLE_NAME = '{LEASES}' THEN
            IF NOT EXISTS (
              SELECT 1 FROM {LEASES} l
              JOIN {ADMISSIONS} a ON a.id = l.admission_id
              JOIN {OPERATIONS} w ON w.id = a.operation_id
              WHERE l.id = NEW.id
                AND a.operation_id = l.operation_id
                AND a.policy_version = l.policy_version
                AND a.operation_kind = l.operation_kind
                AND a.resource_units = l.resource_units
                AND a.fence = l.fence
                AND l.fence >= 1
                AND a.deadline_at = l.deadline_at
                AND w.execution_profile_version = 'public-generation-export/v2'
                AND ((a.state = 'queued' AND l.state = 'queued')
                     OR (a.state = 'active' AND l.state = 'leased')
                     OR (a.state IN ('succeeded', 'failed', 'budget_exhausted')
                         AND l.state = 'released')
                     OR (a.state = 'cancelled' AND l.state = 'cancelled')
                     OR (a.state = 'deadline_exceeded' AND l.state = 'expired'))
                AND ((l.scope_type = 'global' AND l.scope_key = 'global')
                     OR (l.scope_type = 'tenant' AND l.scope_key = a.tenant_id)
                     OR (l.scope_type = 'owner' AND l.scope_key = a.owner_id::text))
            ) THEN
              RAISE EXCEPTION 'public_resource_lease_admission_mismatch';
            END IF;
          ELSE
            IF EXISTS (
              SELECT 1 FROM {ADMISSIONS} a
              JOIN {LEASES} l ON l.admission_id = a.id
              WHERE a.id = NEW.id
                AND (l.operation_id IS DISTINCT FROM a.operation_id
                     OR l.policy_version IS DISTINCT FROM a.policy_version
                     OR l.operation_kind IS DISTINCT FROM a.operation_kind
                     OR l.resource_units IS DISTINCT FROM a.resource_units
                     OR l.fence IS DISTINCT FROM a.fence
                     OR l.deadline_at IS DISTINCT FROM a.deadline_at
                     OR (a.state = 'queued' AND l.state <> 'queued')
                     OR (a.state = 'active' AND l.state <> 'leased')
                     OR (a.state IN ('succeeded', 'failed', 'budget_exhausted')
                         AND l.state <> 'released')
                     OR (a.state = 'cancelled' AND l.state <> 'cancelled')
                     OR (a.state = 'deadline_exceeded' AND l.state <> 'expired'))
            ) THEN
              RAISE EXCEPTION 'public_admission_resource_lease_mismatch';
            END IF;
            SELECT execution_profile_version INTO execution_profile
            FROM {OPERATIONS} w
            JOIN {ADMISSIONS} a ON a.operation_id = w.id
            WHERE a.id = NEW.id;
            IF execution_profile = 'public-generation-export/v2'
               AND EXISTS (
                 SELECT 1 FROM {ADMISSIONS} a
                 WHERE a.id = NEW.id AND a.state IN ('queued', 'active')
               ) THEN
              SELECT count(*) INTO required_scope_count
              FROM {ADMISSIONS} a
              JOIN {LEASES} l ON l.admission_id = a.id
              WHERE a.id = NEW.id
                AND ((l.scope_type = 'global' AND l.scope_key = 'global')
                     OR (l.scope_type = 'tenant' AND l.scope_key = a.tenant_id)
                     OR (l.scope_type = 'owner' AND l.scope_key = a.owner_id::text));
              IF required_scope_count <> 3 THEN
                RAISE EXCEPTION 'public_admission_resource_lease_set_incomplete';
              END IF;
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE CONSTRAINT TRIGGER trg_public_resource_lease_consistent
        AFTER INSERT OR UPDATE ON {LEASES}
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public_validate_resource_lease();

        CREATE CONSTRAINT TRIGGER trg_public_admission_resource_lease_consistent
        AFTER INSERT OR UPDATE ON {ADMISSIONS}
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public_validate_resource_lease();
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public_guard_quota_counter_capacity()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
          active_count_limit bigint;
          queued_count_limit bigint;
          active_units_limit bigint;
          queued_units_limit bigint;
          policy_enabled boolean;
        BEGIN
          SELECT
            CASE NEW.scope_type
              WHEN 'global' THEN CASE NEW.operation_kind
                WHEN 'generation' THEN p.global_active_generation
                ELSE p.global_active_export END
              WHEN 'tenant' THEN CASE NEW.operation_kind
                WHEN 'generation' THEN p.tenant_active_generation
                ELSE p.tenant_active_export END
              ELSE CASE NEW.operation_kind
                WHEN 'generation' THEN p.owner_active_generation
                ELSE p.owner_active_export END
            END,
            CASE NEW.scope_type
              WHEN 'global' THEN CASE NEW.operation_kind
                WHEN 'generation' THEN p.global_queued_generation
                ELSE p.global_queued_export END
              WHEN 'tenant' THEN CASE NEW.operation_kind
                WHEN 'generation' THEN p.tenant_queued_generation
                ELSE p.tenant_queued_export END
              ELSE CASE NEW.operation_kind
                WHEN 'generation' THEN p.owner_queued_generation
                ELSE p.owner_queued_export END
            END,
            CASE NEW.scope_type
              WHEN 'global' THEN p.global_active_resource_units
              WHEN 'tenant' THEN p.tenant_active_resource_units
              ELSE p.owner_active_resource_units
            END,
            CASE NEW.scope_type
              WHEN 'global' THEN p.global_queued_resource_units
              WHEN 'tenant' THEN p.tenant_queued_resource_units
              ELSE p.owner_queued_resource_units
            END,
            p.enabled
          INTO active_count_limit, queued_count_limit,
               active_units_limit, queued_units_limit, policy_enabled
          FROM {POLICIES} p
          WHERE p.version = NEW.policy_version;

          IF NOT FOUND THEN
            RAISE EXCEPTION 'public_policy_authority_unavailable';
          END IF;
          IF NOT policy_enabled
             AND (TG_OP = 'INSERT'
                  OR NEW.active_count > OLD.active_count
                  OR NEW.queued_count > OLD.queued_count
                  OR NEW.active_resource_units > OLD.active_resource_units
                  OR NEW.queued_resource_units > OLD.queued_resource_units) THEN
            RAISE EXCEPTION 'public_policy_is_disabled';
          END IF;
          IF NEW.active_count > active_count_limit
             OR NEW.queued_count > queued_count_limit
             OR NEW.active_resource_units > active_units_limit
             OR NEW.queued_resource_units > queued_units_limit THEN
            RAISE EXCEPTION 'public_quota_capacity_exceeded';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE TRIGGER trg_public_quota_counter_capacity
        BEFORE INSERT OR UPDATE ON {QUOTAS}
        FOR EACH ROW EXECUTE FUNCTION public_guard_quota_counter_capacity();
        """
    )
    op.execute(
        f"""
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

          IF target_scope_type IN ('tenant', 'owner') THEN
            SELECT
              count(*) FILTER (WHERE a.state = 'queued'),
              count(*) FILTER (WHERE a.state = 'active')
            INTO legacy_queued_count, legacy_active_count
            FROM {ADMISSIONS} a
            WHERE a.policy_version = target_policy_version
              AND a.operation_kind = target_operation_kind
              AND ((target_scope_type = 'tenant' AND a.tenant_id = target_scope_key)
                   OR (target_scope_type = 'owner' AND a.owner_id::text = target_scope_key))
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

        CREATE CONSTRAINT TRIGGER trg_public_resource_lease_counter_consistent
        AFTER INSERT OR UPDATE OR DELETE ON {LEASES}
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public_validate_quota_counter_consistency();

        CREATE CONSTRAINT TRIGGER trg_public_quota_counter_consistent
        AFTER INSERT OR UPDATE OR DELETE ON {QUOTAS}
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public_validate_quota_counter_consistency();
        """
    )


def _validate_existing_postgresql_counters() -> None:
    """Fail closed on pre-Phase-2 count drift; never rewrite authoritative history."""

    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(
        f"""
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM {QUOTAS} q
            LEFT JOIN LATERAL (
              SELECT
                count(*) FILTER (WHERE a.state = 'queued') AS queued_count,
                count(*) FILTER (WHERE a.state = 'active') AS active_count
              FROM {ADMISSIONS} a
              WHERE a.policy_version = q.policy_version
                AND a.operation_kind = q.operation_kind
                AND ((q.scope_type = 'tenant' AND a.tenant_id = q.scope_key)
                     OR (q.scope_type = 'owner' AND a.owner_id::text = q.scope_key))
                AND NOT EXISTS (
                  SELECT 1 FROM {LEASES} l WHERE l.admission_id = a.id
                )
            ) expected ON TRUE
            WHERE q.scope_type IN ('tenant', 'owner')
              AND (q.queued_count <> expected.queued_count
                   OR q.active_count <> expected.active_count
                   OR q.queued_resource_units <> 0
                   OR q.active_resource_units <> 0)
          ) OR EXISTS (
            SELECT 1
            FROM {ADMISSIONS} a
            WHERE a.state IN ('queued', 'active')
              AND NOT EXISTS (
                SELECT 1 FROM {LEASES} l WHERE l.admission_id = a.id
              )
              AND (
                NOT EXISTS (
                  SELECT 1 FROM {QUOTAS} q
                  WHERE q.policy_version = a.policy_version
                    AND q.operation_kind = a.operation_kind
                    AND q.scope_type = 'tenant'
                    AND q.scope_key = a.tenant_id
                )
                OR NOT EXISTS (
                  SELECT 1 FROM {QUOTAS} q
                  WHERE q.policy_version = a.policy_version
                    AND q.operation_kind = a.operation_kind
                    AND q.scope_type = 'owner'
                    AND q.scope_key = a.owner_id::text
                )
              )
          ) THEN
            RAISE EXCEPTION 'public_legacy_quota_counter_drift';
          END IF;
        END;
        $$;
        """
    )


def _install_postgresql_artifact_authority() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public_validate_artifact_pointer()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
          artifact_state text;
          artifact_refs bigint;
          operation_fence bigint;
        BEGIN
          SELECT lifecycle_state, refcount
            INTO artifact_state, artifact_refs
            FROM {ARTIFACTS}
           WHERE id = NEW.artifact_id;
          IF artifact_state IS DISTINCT FROM 'committed' OR artifact_refs < 1 THEN
            RAISE EXCEPTION 'public_artifact_pointer_requires_committed_reference';
          END IF;

          SELECT fencing_token
            INTO operation_fence
            FROM {OPERATIONS}
           WHERE id = NEW.operation_id;
          IF operation_fence IS NULL OR NEW.fencing_token IS DISTINCT FROM operation_fence THEN
            RAISE EXCEPTION 'public_artifact_pointer_fence_stale';
          END IF;
          IF NOT EXISTS (
            SELECT 1
              FROM {ADMISSIONS} a
              JOIN {ARTIFACTS} artifact ON artifact.id = NEW.artifact_id
             WHERE a.operation_id = NEW.operation_id
               AND a.owner_id = artifact.owner_id
               AND a.tenant_id = artifact.tenant_id
               AND (
                 (TG_TABLE_NAME = '{PUBLICATIONS}' AND a.state IN ('active', 'succeeded'))
                 OR (TG_TABLE_NAME = '{CHECKPOINTS}' AND a.state = 'active')
               )
               AND a.fence = NEW.fencing_token
          ) THEN
            RAISE EXCEPTION 'public_artifact_pointer_admission_binding_invalid';
          END IF;
          IF TG_OP = 'UPDATE' AND NEW.fencing_token < OLD.fencing_token THEN
            RAISE EXCEPTION 'public_artifact_pointer_fence_must_be_monotonic';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE CONSTRAINT TRIGGER trg_public_artifact_publication_valid
        AFTER INSERT OR UPDATE ON {PUBLICATIONS}
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public_validate_artifact_pointer();

        CREATE CONSTRAINT TRIGGER trg_public_slide_checkpoint_valid
        AFTER INSERT OR UPDATE ON {CHECKPOINTS}
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public_validate_artifact_pointer();
        """
    )


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    required = {
        OPERATIONS,
        POLICIES,
        QUOTAS,
        ADMISSIONS,
        ARTIFACTS,
        EXPORT_IDENTITIES,
    }
    if not required.issubset(set(inspector.get_table_names())):
        raise RuntimeError("public_control_plane_prerequisite_schema_unavailable")

    with op.batch_alter_table(OPERATIONS) as batch:
        batch.add_column(sa.Column("contract_version", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column("execution_profile_version", sa.String(length=64), nullable=True)
        )
        batch.add_column(sa.Column("phase", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True))
        batch.drop_constraint("ck_workspace_operations_state", type_="check")
        batch.create_check_constraint(
            "ck_workspace_operations_state",
            "state IN ('queued', 'waiting_for_assets', 'blocked', 'running', "
            "'approval_required', 'awaiting_approval', 'cancelling', 'succeeded', "
            "'failed', 'cancelled', 'deadline_exceeded', 'dead_letter')",
        )
        batch.create_check_constraint(
            "ck_workspace_operations_execution_contract",
            "(contract_version IS NULL AND execution_profile_version IS NULL "
            "AND phase IS NULL AND deadline_at IS NULL) OR "
            "(contract_version IS NOT NULL AND execution_profile_version IS NOT NULL "
            "AND phase IS NOT NULL AND deadline_at IS NOT NULL)",
        )
        batch.create_check_constraint(
            "ck_workspace_operations_deadline",
            "deadline_at IS NULL OR deadline_at > created_at",
        )

    with op.batch_alter_table(POLICIES) as batch:
        batch.add_column(sa.Column("global_active_generation", sa.Integer(), nullable=False, server_default="20"))
        batch.add_column(sa.Column("global_queued_generation", sa.Integer(), nullable=False, server_default="60"))
        batch.add_column(sa.Column("global_active_export", sa.Integer(), nullable=False, server_default="20"))
        batch.add_column(sa.Column("global_queued_export", sa.Integer(), nullable=False, server_default="60"))
        batch.add_column(sa.Column("global_active_resource_units", sa.BigInteger(), nullable=False, server_default="20000"))
        batch.add_column(sa.Column("global_queued_resource_units", sa.BigInteger(), nullable=False, server_default="60000"))
        batch.add_column(sa.Column("tenant_active_resource_units", sa.BigInteger(), nullable=False, server_default="5000"))
        batch.add_column(sa.Column("tenant_queued_resource_units", sa.BigInteger(), nullable=False, server_default="15000"))
        batch.add_column(sa.Column("owner_active_resource_units", sa.BigInteger(), nullable=False, server_default="1000"))
        batch.add_column(sa.Column("owner_queued_resource_units", sa.BigInteger(), nullable=False, server_default="3000"))
        batch.drop_constraint("ck_public_policy_quota_limits", type_="check")
        batch.create_check_constraint(
            "ck_public_policy_quota_limits",
            "global_active_generation > 0 AND global_queued_generation >= 0 "
            "AND owner_active_generation > 0 AND owner_queued_generation >= 0 "
            "AND tenant_active_generation > 0 AND tenant_queued_generation >= 0 "
            "AND global_active_export > 0 AND global_queued_export >= 0 "
            "AND owner_active_export > 0 AND owner_queued_export >= 0 "
            "AND tenant_active_export > 0 AND tenant_queued_export >= 0",
        )
        batch.create_check_constraint(
            "ck_public_policy_resource_limits",
            "global_active_resource_units > 0 AND global_queued_resource_units >= 0 "
            "AND tenant_active_resource_units > 0 AND tenant_queued_resource_units >= 0 "
            "AND owner_active_resource_units > 0 AND owner_queued_resource_units >= 0",
        )

    with op.batch_alter_table(QUOTAS) as batch:
        batch.add_column(sa.Column("active_resource_units", sa.BigInteger(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("queued_resource_units", sa.BigInteger(), nullable=False, server_default="0"))
        batch.drop_constraint("ck_public_quota_scope", type_="check")
        batch.drop_constraint("ck_public_quota_counts", type_="check")
        batch.create_check_constraint(
            "ck_public_quota_scope",
            "scope_type IN ('global', 'tenant', 'owner')",
        )
        batch.create_check_constraint(
            "ck_public_quota_scope_key",
            "(scope_type = 'global' AND scope_key = 'global') OR "
            "(scope_type <> 'global' AND length(scope_key) > 0)",
        )
        batch.create_check_constraint(
            "ck_public_quota_counts",
            "active_count >= 0 AND queued_count >= 0 "
            "AND active_resource_units >= 0 AND queued_resource_units >= 0 "
            "AND version >= 0",
        )

    with op.batch_alter_table(ADMISSIONS) as batch:
        batch.add_column(sa.Column("resource_units", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("policy_digest", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("fence", sa.BigInteger(), nullable=False, server_default="0"))
        batch.drop_constraint("ck_public_operation_admission_state", type_="check")
        batch.create_check_constraint(
            "ck_public_operation_admission_state",
            "state IN ('queued', 'active', 'succeeded', 'failed', 'cancelled', "
            "'deadline_exceeded', 'budget_exhausted')",
        )

    _backfill_existing_admissions()

    with op.batch_alter_table(ADMISSIONS) as batch:
        batch.alter_column("resource_units", existing_type=sa.BigInteger(), nullable=False)
        batch.alter_column("policy_digest", existing_type=sa.String(length=64), nullable=False)
        batch.alter_column("deadline_at", existing_type=sa.DateTime(timezone=True), nullable=False)
        batch.create_check_constraint(
            "ck_public_operation_admission_control",
            "resource_units > 0 AND fence >= 0 AND length(policy_digest) = 64",
        )

    op.create_table(
        LEASES,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("admission_id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("scope_key", sa.String(length=128), nullable=False),
        sa.Column("operation_kind", sa.String(length=16), nullable=False),
        sa.Column("resource_units", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("lease_owner_hash", sa.String(length=64), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("leased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="0"),
        sa.CheckConstraint("scope_type IN ('global', 'tenant', 'owner')", name="ck_public_resource_lease_scope"),
        sa.CheckConstraint("(scope_type = 'global' AND scope_key = 'global') OR (scope_type <> 'global' AND length(scope_key) > 0)", name="ck_public_resource_lease_scope_key"),
        sa.CheckConstraint("operation_kind IN ('generation', 'export')", name="ck_public_resource_lease_kind"),
        sa.CheckConstraint("state IN ('queued', 'leased', 'released', 'cancelled', 'expired')", name="ck_public_resource_lease_state"),
        sa.CheckConstraint("resource_units > 0 AND attempt >= 0 AND fence >= 0 AND version >= 0", name="ck_public_resource_lease_control"),
        sa.CheckConstraint("lease_expires_at IS NULL OR lease_expires_at <= deadline_at", name="ck_public_resource_lease_deadline"),
        sa.ForeignKeyConstraint(["admission_id"], [f"{ADMISSIONS}.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["operation_id"], [f"{OPERATIONS}.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["policy_version"], [f"{POLICIES}.version"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("admission_id", "scope_type", "scope_key", name="uq_public_resource_lease_admission_scope"),
    )
    op.create_index("ix_public_resource_lease_claim", LEASES, ["policy_version", "operation_kind", "state", "reserved_at"], unique=False)
    op.create_index("ix_public_resource_lease_operation_fence", LEASES, ["operation_id", "fence", "state"], unique=False)

    with op.batch_alter_table(ARTIFACTS) as batch:
        batch.add_column(
            sa.Column(
                "lifecycle_state",
                sa.String(length=16),
                nullable=False,
                server_default="committed",
            )
        )
        batch.add_column(sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column("tombstoned_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(
            sa.Column("refcount", sa.BigInteger(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column(
                "lifecycle_revision",
                sa.BigInteger(),
                nullable=False,
                server_default="1",
            )
        )
        batch.add_column(sa.Column("last_gc_error", sa.String(length=512), nullable=True))
        batch.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )
        )
        batch.create_check_constraint(
            "ck_public_artifact_lifecycle_state",
            "lifecycle_state IN ('provisional', 'committed', 'tombstoned', 'deleted')",
        )
        batch.create_check_constraint(
            "ck_public_artifact_refcount",
            "refcount >= 0",
        )
        batch.create_check_constraint(
            "ck_public_artifact_lifecycle_revision",
            "lifecycle_revision >= 1",
        )
        batch.create_check_constraint(
            "ck_public_artifact_deleted_refcount",
            "lifecycle_state <> 'deleted' OR refcount = 0",
        )
        batch.create_index(
            "ix_public_artifact_gc",
            ["lifecycle_state", "expires_at", "refcount"],
            unique=False,
        )

    with op.batch_alter_table(EXPORT_IDENTITIES) as batch:
        batch.drop_constraint("uq_public_export_identity", type_="unique")
        for column in (
            "scene_digest",
            "semantic_digest",
            "quality_digest",
            "exporter_digest",
        ):
            batch.add_column(sa.Column(column, sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("render_adapter", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("output_mode", sa.String(length=24), nullable=True))
        batch.add_column(
            sa.Column("identity_contract_version", sa.String(length=32), nullable=True)
        )
        batch.add_column(sa.Column("reuse_eligible", sa.Boolean(), nullable=True))

    _backfill_legacy_export_identity_components()
    with op.batch_alter_table(EXPORT_IDENTITIES) as batch:
        for column in (
            "scene_digest",
            "semantic_digest",
            "quality_digest",
            "exporter_digest",
        ):
            batch.alter_column(
                column,
                existing_type=sa.String(length=64),
                nullable=False,
            )
        batch.alter_column(
            "render_adapter",
            existing_type=sa.String(length=128),
            nullable=False,
        )
        batch.alter_column(
            "output_mode",
            existing_type=sa.String(length=24),
            nullable=False,
        )
        batch.alter_column(
            "identity_contract_version",
            existing_type=sa.String(length=32),
            nullable=False,
            server_default="digest-bound/v2",
        )
        batch.alter_column(
            "reuse_eligible",
            existing_type=sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        )
        batch.create_check_constraint(
            "ck_public_export_identity_output_mode",
            "output_mode IN ('fidelity-raster', 'editable-hybrid')",
        )
        batch.create_check_constraint(
            "ck_public_export_identity_reuse_contract",
            "(identity_contract_version = 'digest-bound/v2' AND reuse_eligible = true) "
            "OR (identity_contract_version = 'legacy-unreusable/v1' AND reuse_eligible = false)",
        )
        batch.drop_constraint("ck_public_export_identity_digests", type_="check")
        batch.create_check_constraint(
            "ck_public_export_identity_digests",
            "length(canonical_digest) = 64 AND length(scene_digest) = 64 "
            "AND length(semantic_digest) = 64 AND length(quality_digest) = 64 "
            "AND length(exporter_digest) = 64 AND length(font_manifest_digest) = 64",
        )
        batch.create_unique_constraint(
            "uq_public_export_identity",
            [
                "owner_id",
                "tenant_id",
                "presentation_id",
                "revision",
                "scene_digest",
                "semantic_digest",
                "quality_digest",
                "exporter_digest",
                "export_mode",
                "output_mode",
                "font_manifest_digest",
                "render_adapter",
                "identity_contract_version",
            ],
        )

    op.create_table(
        PUBLICATIONS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("publication_name", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.Column("quality_digest", sa.String(length=64), nullable=False),
        sa.Column("attempt_token_hash", sa.String(length=71), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "revision >= 0",
            name="ck_public_artifact_publication_revision",
        ),
        sa.CheckConstraint(
            "fencing_token >= 1 AND length(attempt_token_hash) = 71 "
            "AND length(quality_digest) = 64",
            name="ck_public_artifact_publication_fence",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"], [f"{OPERATIONS}.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"], [f"{ARTIFACTS}.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "operation_id",
            "publication_name",
            name="uq_public_artifact_publication_operation_name",
        ),
    )
    op.create_index(
        "ix_public_artifact_publication_artifact",
        PUBLICATIONS,
        ["artifact_id", "published_at"],
        unique=False,
    )

    op.create_table(
        CHECKPOINTS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("slide_index", sa.Integer(), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("stage_version", sa.String(length=64), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_token_hash", sa.String(length=71), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column(
            "committed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "revision >= 0 AND slide_index >= 0",
            name="ck_public_slide_checkpoint_position",
        ),
        sa.CheckConstraint(
            "length(input_digest) = 64 AND length(attempt_token_hash) = 71 "
            "AND fencing_token >= 1",
            name="ck_public_slide_checkpoint_fence",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"], [f"{OPERATIONS}.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"], [f"{ARTIFACTS}.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "operation_id",
            "revision",
            "slide_index",
            "input_digest",
            "stage_version",
            name="uq_public_slide_checkpoint_identity",
        ),
    )
    op.create_index(
        "ix_public_slide_checkpoint_artifact",
        CHECKPOINTS,
        ["artifact_id", "committed_at"],
        unique=False,
    )

    op.execute(
        sa.text(
            f"""
            UPDATE {ARTIFACTS}
               SET refcount =
                   (SELECT COUNT(*) FROM {EXPORT_IDENTITIES} identity_source
                     WHERE identity_source.source_artifact_id = {ARTIFACTS}.id
                       AND identity_source.state IN ('running', 'succeeded'))
                 + (SELECT COUNT(*) FROM {EXPORT_IDENTITIES} identity_result
                     WHERE identity_result.result_artifact_id = {ARTIFACTS}.id
                       AND identity_result.state = 'succeeded')
                 + (SELECT COUNT(*) FROM {PUBLICATIONS} publication
                     WHERE publication.artifact_id = {ARTIFACTS}.id)
                 + (SELECT COUNT(*) FROM {CHECKPOINTS} checkpoint
                     WHERE checkpoint.artifact_id = {ARTIFACTS}.id),
                   lifecycle_revision = lifecycle_revision + 1,
                   updated_at = CURRENT_TIMESTAMP
            """
        )
    )

    _validate_existing_postgresql_counters()
    _install_postgresql_authority()
    _install_postgresql_artifact_authority()


def downgrade() -> None:
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    for pointer_table in (PUBLICATIONS, CHECKPOINTS):
        if pointer_table in tables:
            pointer_count = bind.execute(
                sa.text(f"SELECT COUNT(*) FROM {pointer_table}")
            ).scalar_one()
            if pointer_count:
                raise RuntimeError(
                    "cannot_downgrade_public_control_plane_with_artifact_pointers"
                )
    if LEASES in set(sa.inspect(bind).get_table_names()):
        lease_count = bind.execute(sa.text(f"SELECT COUNT(*) FROM {LEASES}")).scalar_one()
        if lease_count:
            raise RuntimeError("cannot_downgrade_public_control_plane_with_resource_leases")

    if bind.dialect.name == "postgresql":
        for trigger, table in (
            ("trg_public_slide_checkpoint_valid", CHECKPOINTS),
            ("trg_public_artifact_publication_valid", PUBLICATIONS),
        ):
            if table in tables:
                op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        op.execute("DROP FUNCTION IF EXISTS public_validate_artifact_pointer()")
        for trigger, table in (
            ("trg_public_quota_counter_consistent", QUOTAS),
            ("trg_public_resource_lease_counter_consistent", LEASES),
            ("trg_public_quota_counter_capacity", QUOTAS),
            ("trg_public_admission_resource_lease_consistent", ADMISSIONS),
            ("trg_public_resource_lease_consistent", LEASES),
            ("trg_workspace_operation_admission_consistent", OPERATIONS),
            ("trg_public_admission_operation_consistent", ADMISSIONS),
            ("trg_public_quota_counter_control_guard", QUOTAS),
            ("trg_public_resource_lease_control_guard", LEASES),
            ("trg_public_admission_control_guard", ADMISSIONS),
            ("trg_workspace_operation_control_guard", OPERATIONS),
        ):
            op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        for function in (
            "public_validate_quota_counter_consistency",
            "public_guard_quota_counter_capacity",
            "public_validate_resource_lease",
            "public_validate_admission_operation",
            "public_guard_control_update",
            "public_control_plane_authority_revision",
        ):
            op.execute(f"DROP FUNCTION IF EXISTS {function}()")

    if CHECKPOINTS in tables:
        op.drop_index(
            "ix_public_slide_checkpoint_artifact",
            table_name=CHECKPOINTS,
        )
        op.drop_table(CHECKPOINTS)
    if PUBLICATIONS in tables:
        op.drop_index(
            "ix_public_artifact_publication_artifact",
            table_name=PUBLICATIONS,
        )
        op.drop_table(PUBLICATIONS)

    with op.batch_alter_table(EXPORT_IDENTITIES) as batch:
        batch.drop_constraint("uq_public_export_identity", type_="unique")
        batch.drop_constraint("ck_public_export_identity_reuse_contract", type_="check")
        batch.drop_constraint("ck_public_export_identity_output_mode", type_="check")
        batch.drop_constraint("ck_public_export_identity_digests", type_="check")
        batch.create_check_constraint(
            "ck_public_export_identity_digests",
            "length(canonical_digest) = 64 AND length(font_manifest_digest) = 64",
        )
        batch.create_unique_constraint(
            "uq_public_export_identity",
            [
                "owner_id",
                "presentation_id",
                "revision",
                "canonical_digest",
                "export_mode",
                "font_manifest_digest",
                "exporter_version",
            ],
        )
        for column in (
            "reuse_eligible",
            "identity_contract_version",
            "output_mode",
            "render_adapter",
            "exporter_digest",
            "quality_digest",
            "semantic_digest",
            "scene_digest",
        ):
            batch.drop_column(column)

    with op.batch_alter_table(ARTIFACTS) as batch:
        batch.drop_index("ix_public_artifact_gc")
        batch.drop_constraint("ck_public_artifact_deleted_refcount", type_="check")
        batch.drop_constraint("ck_public_artifact_lifecycle_revision", type_="check")
        batch.drop_constraint("ck_public_artifact_refcount", type_="check")
        batch.drop_constraint("ck_public_artifact_lifecycle_state", type_="check")
        for column in (
            "last_gc_error",
            "updated_at",
            "lifecycle_revision",
            "refcount",
            "deleted_at",
            "tombstoned_at",
            "expires_at",
            "lifecycle_state",
        ):
            batch.drop_column(column)

    op.drop_table(LEASES)

    with op.batch_alter_table(ADMISSIONS) as batch:
        batch.drop_constraint("ck_public_operation_admission_control", type_="check")
        batch.drop_constraint("ck_public_operation_admission_state", type_="check")
        batch.create_check_constraint(
            "ck_public_operation_admission_state",
            "state IN ('queued', 'active', 'succeeded', 'failed', 'cancelled', 'budget_exhausted')",
        )
        for column in ("fence", "deadline_at", "policy_digest", "resource_units"):
            batch.drop_column(column)

    with op.batch_alter_table(QUOTAS) as batch:
        batch.drop_constraint("ck_public_quota_scope_key", type_="check")
        batch.drop_constraint("ck_public_quota_scope", type_="check")
        batch.drop_constraint("ck_public_quota_counts", type_="check")
        batch.create_check_constraint("ck_public_quota_scope", "scope_type IN ('owner', 'tenant')")
        batch.create_check_constraint("ck_public_quota_counts", "active_count >= 0 AND queued_count >= 0 AND version >= 0")
        batch.drop_column("queued_resource_units")
        batch.drop_column("active_resource_units")

    with op.batch_alter_table(POLICIES) as batch:
        batch.drop_constraint("ck_public_policy_resource_limits", type_="check")
        batch.drop_constraint("ck_public_policy_quota_limits", type_="check")
        batch.create_check_constraint(
            "ck_public_policy_quota_limits",
            "owner_active_generation > 0 AND owner_queued_generation >= 0 "
            "AND tenant_active_generation > 0 AND tenant_queued_generation >= 0 "
            "AND owner_active_export > 0 AND owner_queued_export >= 0 "
            "AND tenant_active_export > 0 AND tenant_queued_export >= 0",
        )
        for column in (
            "owner_queued_resource_units", "owner_active_resource_units",
            "tenant_queued_resource_units", "tenant_active_resource_units",
            "global_queued_resource_units", "global_active_resource_units",
            "global_queued_export", "global_active_export",
            "global_queued_generation", "global_active_generation",
        ):
            batch.drop_column(column)

    with op.batch_alter_table(OPERATIONS) as batch:
        batch.drop_constraint("ck_workspace_operations_deadline", type_="check")
        batch.drop_constraint("ck_workspace_operations_execution_contract", type_="check")
        batch.drop_constraint("ck_workspace_operations_state", type_="check")
        batch.create_check_constraint(
            "ck_workspace_operations_state",
            "state IN ('queued', 'waiting_for_assets', 'blocked', 'running', "
            "'approval_required', 'awaiting_approval', 'cancelling', 'succeeded', "
            "'failed', 'cancelled', 'dead_letter')",
        )
        for column in (
            "deadline_at", "phase", "execution_profile_version", "contract_version"
        ):
            batch.drop_column(column)
