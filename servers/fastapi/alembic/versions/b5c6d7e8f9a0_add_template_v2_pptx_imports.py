"""add durable Template V2 PPTX import jobs

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-07-24
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "b5c6d7e8f9a0"
down_revision: str | None = "a4b5c6d7e8f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "template_v2_pptx_imports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("requested_template_id", sa.String(), nullable=False),
        sa.Column("draft_template_id", sa.String(), nullable=True),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("source_filename", sa.String(), nullable=False),
        sa.Column("source_media_type", sa.String(), nullable=False),
        sa.Column("source_size_bytes", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.String(), nullable=False),
        sa.Column("source_storage_key", sa.String(), nullable=False),
        sa.Column(
            "pipeline_version",
            sa.String(),
            nullable=False,
            server_default="template-v2-pptx-ooxml-v1",
        ),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["async_presentation_generation_tasks.id"],
            name="fk_template_v2_pptx_imports_task_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["draft_template_id"],
            ["template_v2.id"],
            name="fk_template_v2_pptx_imports_draft_template_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", name="uq_template_v2_pptx_imports_task_id"),
        sa.UniqueConstraint(
            "source_storage_key",
            name="uq_template_v2_pptx_imports_source_storage_key",
        ),
    )
    op.create_index(
        "ix_template_v2_pptx_imports_task_id",
        "template_v2_pptx_imports",
        ["task_id"],
        unique=True,
    )
    op.create_index(
        "ix_template_v2_pptx_imports_requested_template_id",
        "template_v2_pptx_imports",
        ["requested_template_id"],
        unique=False,
    )
    op.create_index(
        "ix_template_v2_pptx_imports_draft_template_id",
        "template_v2_pptx_imports",
        ["draft_template_id"],
        unique=False,
    )


def downgrade() -> None:
    connection = op.get_bind()
    populated = connection.execute(
        sa.text("SELECT 1 FROM template_v2_pptx_imports LIMIT 1")
    ).first()
    if populated is not None:
        raise RuntimeError(
            "Template V2 PPTX import downgrade would discard import evidence"
        )
    op.drop_index(
        "ix_template_v2_pptx_imports_draft_template_id",
        table_name="template_v2_pptx_imports",
    )
    op.drop_index(
        "ix_template_v2_pptx_imports_requested_template_id",
        table_name="template_v2_pptx_imports",
    )
    op.drop_index(
        "ix_template_v2_pptx_imports_task_id",
        table_name="template_v2_pptx_imports",
    )
    op.drop_table("template_v2_pptx_imports")
