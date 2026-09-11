"""Add the export-ceiling public v5 policy without touching v1..v4 jobs.

The v5 row is a byte-for-byte clone of v4 that only raises the export
deadline column.  Every earlier row carries ``export_deadline_seconds = 600``
as a flat budget, while the isolated hybrid worker was granted a separate
1740 s constant: a 35-slide hybrid export (measured 894 s) therefore outlived
its own admission.  Admission now reads the column as a *ceiling* over the
slide-count formula in ``services/public_export_deadline_policy.py`` and takes
``min(ceiling, formula)``, exactly as generation does with its own column, so
the ceiling has to be the executor's hard export budget rather than the old
flat grant.

Existing v1..v4 rows are left byte for byte as they are so already pinned
operations keep their policy digest.

Revision ID: b4e6f8a0c2d5
Revises: a2c4e6f8b1d3
"""

from __future__ import annotations

from alembic import op


revision: str = "b4e6f8a0c2d5"
down_revision: str | None = "a2c4e6f8b1d3"
branch_labels: str | None = None
depends_on: str | None = None


# Mirrors ``PUBLIC_EXPORT_DEADLINE_MAX_SECONDS`` in
# ``services/public_export_deadline_policy.py`` (the trusted executor's export
# role budget) as it stood when this revision was written.  A migration must
# not import runtime code, so the literal cannot be derived here;
# ``tests/unit/test_public_export_deadline.py`` fails as soon as the two
# diverge.  Do NOT edit this number to follow a later code change -- an
# applied row is immutable; add a v6 revision instead.
_EXPORT_DEADLINE_CEILING_SECONDS = 1800


def upgrade() -> None:
    """Clone the complete current v4 policy shape into the immutable v5 row."""

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
            'public-default/v5',
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
            {_EXPORT_DEADLINE_CEILING_SECONDS},
            CURRENT_TIMESTAMP
        FROM public_policy_profiles
        WHERE version = 'public-default/v4'
          AND NOT EXISTS (
              SELECT 1
              FROM public_policy_profiles
              WHERE version = 'public-default/v5'
          )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM public_policy_profiles WHERE version = 'public-default/v5'")
