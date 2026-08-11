"""Persist canonical reference snapshot identities on presentations.

Revision ID: b9d2e4f6a8c1
Revises: a8c1e4f7b2d9
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "b9d2e4f6a8c1"
down_revision: str | None = "a8c1e4f7b2d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("presentations")
    }
    if "reference_snapshot_ids" not in columns:
        op.add_column(
            "presentations",
            sa.Column("reference_snapshot_ids", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("presentations")
    }
    if "reference_snapshot_ids" in columns:
        op.drop_column("presentations", "reference_snapshot_ids")
