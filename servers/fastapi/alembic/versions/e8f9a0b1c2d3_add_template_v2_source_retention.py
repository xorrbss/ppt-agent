"""add private source retention audit to Template V2 PPTX imports

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
Create Date: 2026-07-24
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e8f9a0b1c2d3"
down_revision: str | None = "d7e8f9a0b1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "template_v2_pptx_imports",
        sa.Column(
            "source_retention_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "template_v2_pptx_imports",
        sa.Column("source_cleanup_token", sa.String(), nullable=True),
    )
    op.add_column(
        "template_v2_pptx_imports",
        sa.Column(
            "source_cleanup_lease_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "template_v2_pptx_imports",
        sa.Column(
            "source_cleanup_attempted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "template_v2_pptx_imports",
        sa.Column("source_deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_template_v2_pptx_imports_source_cleanup",
        "template_v2_pptx_imports",
        ["state", "source_deleted_at", "source_retention_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    connection = op.get_bind()
    irreversible = connection.execute(
        sa.text(
            """
            SELECT 1
            FROM template_v2_pptx_imports
            WHERE source_cleanup_token IS NOT NULL
               OR source_cleanup_lease_expires_at IS NOT NULL
               OR source_cleanup_attempted_at IS NOT NULL
               OR source_deleted_at IS NOT NULL
            LIMIT 1
            """
        )
    ).first()
    if irreversible is not None:
        raise RuntimeError(
            "Template V2 private-source retention downgrade would erase "
            "active or completed cleanup audit"
        )
    op.drop_index(
        "ix_template_v2_pptx_imports_source_cleanup",
        table_name="template_v2_pptx_imports",
    )
    op.drop_column("template_v2_pptx_imports", "source_deleted_at")
    op.drop_column("template_v2_pptx_imports", "source_cleanup_attempted_at")
    op.drop_column("template_v2_pptx_imports", "source_cleanup_lease_expires_at")
    op.drop_column("template_v2_pptx_imports", "source_cleanup_token")
    op.drop_column("template_v2_pptx_imports", "source_retention_expires_at")
