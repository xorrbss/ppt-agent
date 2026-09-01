"""Add the 100-slide public v2 policy without changing pinned v1 jobs.

Revision ID: c8d5f3a20b71
Revises: b7c4e2d91f60
"""

from __future__ import annotations

from alembic import op


revision: str = "c8d5f3a20b71"
down_revision: str | None = "b7c4e2d91f60"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Clone the complete current policy shape into the immutable v2 row."""

    op.execute(
        """
        INSERT INTO public_policy_profiles (
            version,
            enabled,
            max_slides,
            global_active_generation,
            global_queued_generation,
            owner_active_generation,
            owner_queued_generation,
            tenant_active_generation,
            tenant_queued_generation,
            global_active_export,
            global_queued_export,
            owner_active_export,
            owner_queued_export,
            tenant_active_export,
            tenant_queued_export,
            global_active_resource_units,
            global_queued_resource_units,
            tenant_active_resource_units,
            tenant_queued_resource_units,
            owner_active_resource_units,
            owner_queued_resource_units,
            retry_request_limit,
            estimated_cost_limit_microunits,
            actual_cost_limit_microunits,
            generation_deadline_seconds,
            export_deadline_seconds,
            created_at
        )
        SELECT
            'public-default/v2',
            enabled,
            100,
            global_active_generation,
            global_queued_generation,
            owner_active_generation,
            owner_queued_generation,
            tenant_active_generation,
            tenant_queued_generation,
            global_active_export,
            global_queued_export,
            owner_active_export,
            owner_queued_export,
            tenant_active_export,
            tenant_queued_export,
            global_active_resource_units,
            global_queued_resource_units,
            tenant_active_resource_units,
            tenant_queued_resource_units,
            owner_active_resource_units,
            owner_queued_resource_units,
            retry_request_limit,
            estimated_cost_limit_microunits,
            actual_cost_limit_microunits,
            generation_deadline_seconds,
            export_deadline_seconds,
            CURRENT_TIMESTAMP
        FROM public_policy_profiles
        WHERE version = 'public-default/v1'
          AND NOT EXISTS (
              SELECT 1
              FROM public_policy_profiles
              WHERE version = 'public-default/v2'
          )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM public_policy_profiles WHERE version = 'public-default/v2'")
