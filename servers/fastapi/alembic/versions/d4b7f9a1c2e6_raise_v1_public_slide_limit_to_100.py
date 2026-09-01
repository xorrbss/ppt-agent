"""Keep the compatibility public v1 policy aligned with the 100-slide cap.

Revision ID: d4b7f9a1c2e6
Revises: c8d5f3a20b71
"""

from __future__ import annotations

from alembic import op


revision: str = "d4b7f9a1c2e6"
down_revision: str | None = "c8d5f3a20b71"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE public_policy_profiles
        SET max_slides = 100
        WHERE version = 'public-default/v1'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE public_policy_profiles
        SET max_slides = 30
        WHERE version = 'public-default/v1'
        """
    )
