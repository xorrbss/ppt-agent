"""add optimistic revision to Template V2

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-07-24
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c6d7e8f9a0b1"
down_revision: str | None = "b5c6d7e8f9a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "template_v2",
        sa.Column(
            "revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )


def downgrade() -> None:
    connection = op.get_bind()
    edited = connection.execute(
        sa.text("SELECT 1 FROM template_v2 WHERE revision <> 1 LIMIT 1")
    ).first()
    if edited is not None:
        raise RuntimeError(
            "Template V2 revision downgrade would discard concurrency history"
        )
    op.drop_column("template_v2", "revision")
