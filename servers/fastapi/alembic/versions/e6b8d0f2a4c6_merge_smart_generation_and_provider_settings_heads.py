"""merge presentation-runtime migration heads and repair font metadata

Revision ID: e6b8d0f2a4c6
Revises: d5a9c1e3f7b2, f3a7c1d9e5b2
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "e6b8d0f2a4c6"
down_revision: tuple[str, str] = (
    "d5a9c1e3f7b2",
    "f3a7c1d9e5b2",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Join both branches and reconcile the live Presentation model."""

    inspector = sa.inspect(op.get_bind())
    if "presentations" not in inspector.get_table_names():
        return
    columns = {
        column["name"] for column in inspector.get_columns("presentations")
    }
    if "fonts" not in columns:
        with op.batch_alter_table("presentations") as batch:
            batch.add_column(sa.Column("fonts", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Restore the two independent branch heads."""

    inspector = sa.inspect(op.get_bind())
    if "presentations" not in inspector.get_table_names():
        return
    columns = {
        column["name"] for column in inspector.get_columns("presentations")
    }
    if "fonts" in columns:
        with op.batch_alter_table("presentations") as batch:
            batch.drop_column("fonts")
