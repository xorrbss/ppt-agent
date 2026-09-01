"""Allow compatible export admissions to bind fenced artifact pointers.

Revision ID: b7c4e2d91f60
Revises: a1e5f8c2d7b0
"""

from __future__ import annotations

from alembic import op


revision: str = "b7c4e2d91f60"
down_revision: str | None = "a1e5f8c2d7b0"
branch_labels: str | None = None
depends_on: str | None = None


def _install(*, compatible: bool) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    admission_fence = (
        "((operation_contract = 'public-operation/v2' AND a.fence = NEW.fencing_token) "
        "OR (operation_contract IS DISTINCT FROM 'public-operation/v2' AND a.fence = 0))"
        if compatible
        else "a.fence = NEW.fencing_token"
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION public_validate_artifact_pointer()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
          artifact_state text;
          artifact_refs bigint;
          operation_fence bigint;
          operation_contract text;
        BEGIN
          SELECT lifecycle_state, refcount
            INTO artifact_state, artifact_refs
            FROM public_artifacts
           WHERE id = NEW.artifact_id;
          IF artifact_state IS DISTINCT FROM 'committed' OR artifact_refs < 1 THEN
            RAISE EXCEPTION 'public_artifact_pointer_requires_committed_reference';
          END IF;

          SELECT fencing_token, contract_version
            INTO operation_fence, operation_contract
            FROM workspace_operations
           WHERE id = NEW.operation_id;
          IF operation_fence IS NULL OR NEW.fencing_token IS DISTINCT FROM operation_fence THEN
            RAISE EXCEPTION 'public_artifact_pointer_fence_stale';
          END IF;
          IF NOT EXISTS (
            SELECT 1
              FROM public_operation_admissions a
              JOIN public_artifacts artifact ON artifact.id = NEW.artifact_id
             WHERE a.operation_id = NEW.operation_id
               AND a.owner_id = artifact.owner_id
               AND a.tenant_id = artifact.tenant_id
               AND (
                 (TG_TABLE_NAME = 'public_artifact_publications' AND a.state IN ('active', 'succeeded'))
                 OR (TG_TABLE_NAME = 'public_slide_checkpoints' AND a.state = 'active')
               )
               AND {admission_fence}
          ) THEN
            RAISE EXCEPTION 'public_artifact_pointer_admission_binding_invalid';
          END IF;
          IF TG_OP = 'UPDATE' AND NEW.fencing_token < OLD.fencing_token THEN
            RAISE EXCEPTION 'public_artifact_pointer_fence_must_be_monotonic';
          END IF;
          RETURN NEW;
        END;
        $$;
        """
    )


def upgrade() -> None:
    _install(compatible=True)


def downgrade() -> None:
    _install(compatible=False)
