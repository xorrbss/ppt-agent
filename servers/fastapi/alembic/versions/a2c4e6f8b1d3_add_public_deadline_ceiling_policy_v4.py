"""Add the raised-ceiling public v4 policy without touching v1/v2/v3 jobs.

The v4 row is a byte-for-byte clone of v3 that only raises the generation
deadline column.  Admission reads that column as a ceiling over the slide-count
formula in ``services/public_generation_deadline_policy.py`` and takes
``min(ceiling, formula)``, so the v3 row's 7200 s -- which was itself only a
mirror of the code ceiling of the day -- silently capped every deck from about
40 slides up even after the code ceiling was raised to 14400 s.  Production
routinely generates 90-slide decks, whose formula value is 11170 s.

Existing v1, v2 and v3 rows are left byte for byte as they are so already
pinned operations keep their policy digest.

Revision ID: a2c4e6f8b1d3
Revises: e8b3d1f6a4c2
"""

from __future__ import annotations

from alembic import op


revision: str = "a2c4e6f8b1d3"
down_revision: str | None = "e8b3d1f6a4c2"
branch_labels: str | None = None
depends_on: str | None = None


# Mirrors ``PUBLIC_GENERATION_DEADLINE_MAX_SECONDS`` in
# ``services/public_generation_deadline_policy.py`` as it stood when this
# revision was written.  A migration must not import runtime code, so the
# literal cannot be derived here; ``tests/unit/test_public_policy_v4_ceiling.py``
# fails as soon as the two diverge, which is exactly the silent drift that made
# the v3 row cap large decks.  Do NOT edit this number to follow a later code
# change -- an applied row is immutable; add a v5 revision instead.
_GENERATION_DEADLINE_CEILING_SECONDS = 14400


def upgrade() -> None:
    """Clone the complete current v3 policy shape into the immutable v4 row."""

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
            'public-default/v4',
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
        WHERE version = 'public-default/v3'
          AND NOT EXISTS (
              SELECT 1
              FROM public_policy_profiles
              WHERE version = 'public-default/v4'
          )
        """
    )


def downgrade() -> None:
    op.execute("DELETE FROM public_policy_profiles WHERE version = 'public-default/v4'")
