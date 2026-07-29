"""add Template V2 PPTX import review boundary

Revision ID: 1b2c3d4e5f6a
Revises: 0a1b2c3d4e5f
Create Date: 2026-07-25
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "1b2c3d4e5f6a"
down_revision: str | None = "0a1b2c3d4e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE = "template_v2_pptx_imports"
OWNER_REQUEST_KEY_INDEX = (
    "uq_template_v2_pptx_imports_owner_request_key"
)


def upgrade() -> None:
    op.add_column(
        TABLE,
        sa.Column(
            "owner_scope",
            sa.String(length=64),
            nullable=False,
            server_default="local-disabled-auth-scope-v1",
        ),
    )
    op.add_column(
        TABLE,
        sa.Column("request_key_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        TABLE,
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        TABLE,
        sa.Column(
            "revision",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.add_column(
        TABLE,
        sa.Column("analysis_result", sa.JSON(), nullable=True),
    )
    op.add_column(
        TABLE,
        sa.Column("repeat_suggestions", sa.JSON(), nullable=True),
    )
    op.add_column(
        TABLE,
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        TABLE,
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        OWNER_REQUEST_KEY_INDEX,
        TABLE,
        ["owner_scope", "request_key_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(OWNER_REQUEST_KEY_INDEX, table_name=TABLE)
    op.drop_column(TABLE, "cancelled_at")
    op.drop_column(TABLE, "confirmed_at")
    op.drop_column(TABLE, "repeat_suggestions")
    op.drop_column(TABLE, "analysis_result")
    op.drop_column(TABLE, "revision")
    op.drop_column(TABLE, "request_fingerprint")
    op.drop_column(TABLE, "request_key_hash")
    op.drop_column(TABLE, "owner_scope")
