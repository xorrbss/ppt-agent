"""add durable ownership and leases to Template V2 PPTX imports

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-07-24
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d7e8f9a0b1c2"
down_revision: str | None = "c6d7e8f9a0b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "template_v2_pptx_imports",
        sa.Column(
            "attempt_number",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "template_v2_pptx_imports",
        sa.Column("attempt_token", sa.String(), nullable=True),
    )
    op.add_column(
        "template_v2_pptx_imports",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "template_v2_pptx_imports",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "template_v2_pptx_imports",
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_template_v2_pptx_imports_dispatch",
        "template_v2_pptx_imports",
        ["state", "lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    connection = op.get_bind()
    active = connection.execute(
        sa.text(
            """
            SELECT 1
            FROM template_v2_pptx_imports
            WHERE state IN ('processing', 'finalizing')
               OR attempt_token IS NOT NULL
            LIMIT 1
            """
        )
    ).first()
    if active is not None:
        raise RuntimeError(
            "Template V2 PPTX import lease downgrade would orphan active attempts"
        )
    op.drop_index(
        "ix_template_v2_pptx_imports_dispatch",
        table_name="template_v2_pptx_imports",
    )
    op.drop_column("template_v2_pptx_imports", "last_started_at")
    op.drop_column("template_v2_pptx_imports", "heartbeat_at")
    op.drop_column("template_v2_pptx_imports", "lease_expires_at")
    op.drop_column("template_v2_pptx_imports", "attempt_token")
    op.drop_column("template_v2_pptx_imports", "attempt_number")
