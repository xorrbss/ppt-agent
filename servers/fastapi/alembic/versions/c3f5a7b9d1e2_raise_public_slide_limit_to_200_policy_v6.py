"""Add the 200-slide public v6 policy without touching v1..v5 jobs.

The v6 row is a byte-for-byte clone of v5 that only raises ``max_slides``
from 100 to 200, so a source-content deck up to 200 pages is admitted.
Every other ceiling is unchanged: generation and export deadlines are
``min(column, slide-count formula)`` and the weighted resource units of a
200-slide deck (~520) stay under the v5 owner ceiling (1000).

Existing v1..v5 rows are left byte for byte as they are so already pinned
operations keep their policy digest.

Revision ID: c3f5a7b9d1e2
Revises: f6c8e0a2b4d7
"""

from __future__ import annotations

from alembic import op


revision: str = "c3f5a7b9d1e2"
down_revision: str | None = "f6c8e0a2b4d7"
branch_labels: str | None = None
depends_on: str | None = None


# Mirrors ``PUBLIC_DEFAULT_MAX_SLIDES`` in
# ``services/public_operation_admission.py`` as it stood when this revision was
# written.  A migration must not import runtime code, so the literal cannot be
# derived here; admission refuses the default row when the two diverge.  Do
# NOT edit this number to follow a later code change -- an applied row is
# immutable; add a v7 revision instead.
_MAX_SLIDES = 200


def upgrade() -> None:
    """Clone the complete current v5 policy shape into the immutable v6 row."""

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
            'public-default/v6',
            enabled,
            {_MAX_SLIDES},
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
        WHERE version = 'public-default/v5'
          AND NOT EXISTS (
              SELECT 1
              FROM public_policy_profiles
              WHERE version = 'public-default/v6'
          )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM public_policy_profiles WHERE version = 'public-default/v6'")
