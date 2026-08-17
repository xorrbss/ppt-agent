"""Persist authored-vNext export worker request payloads.

Revision ID: c7e9a1b3d5f8
Revises: b6d8e0f2a4c7
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c7e9a1b3d5f8"
down_revision: str | None = "b6d8e0f2a4c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "authored_vnext_export_jobs",
        sa.Column(
            "request_payload",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("authored_vnext_export_jobs", "request_payload")
