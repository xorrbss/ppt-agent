"""Add the slide-scaled deadline public v3 policy without touching v1/v2 jobs.

The v3 row keeps every v2 quota and budget value and only raises the generation
deadline column, which the admission service reads as a ceiling for the
slide-count formula in ``services/public_generation_deadline_policy.py``.
Export keeps the inherited flat budget.  Existing v1 and v2 rows are left byte
for byte as they are so already-pinned operations keep their policy digest.

Revision ID: e1a7c9d4b2f8
Revises: d4b7f9a1c2e6
"""

from __future__ import annotations

from alembic import op


revision: str = "e1a7c9d4b2f8"
down_revision: str | None = "d4b7f9a1c2e6"
branch_labels: str | None = None
depends_on: str | None = None


# Matches PUBLIC_GENERATION_DEADLINE_MAX_SECONDS in
# services/public_generation_deadline_policy.py.
_GENERATION_DEADLINE_CEILING_SECONDS = 7200


def upgrade() -> None:
    """Clone the complete current v2 policy shape into the immutable v3 row."""

    op.execute(
        f"""
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
            'public-default/v3',
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
            {_GENERATION_DEADLINE_CEILING_SECONDS},
            export_deadline_seconds,
            CURRENT_TIMESTAMP
        FROM public_policy_profiles
        WHERE version = 'public-default/v2'
          AND NOT EXISTS (
              SELECT 1
              FROM public_policy_profiles
              WHERE version = 'public-default/v3'
          )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM public_policy_profiles WHERE version = 'public-default/v3'")
