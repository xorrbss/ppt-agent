"""Store authored checkpoint ArtifactRef bindings.

Revision ID: fe8b1c4d7a20
Revises: fd6a8c0e2b4d
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "fe8b1c4d7a20"
down_revision: str | None = "fd6a8c0e2b4d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("authored_slide_checkpoints") as batch_op:
        batch_op.add_column(sa.Column("artifact_refs", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("authored_slide_checkpoints") as batch_op:
        batch_op.drop_column("artifact_refs")
