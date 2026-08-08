"""add Template V2 revision journal

Revision ID: 2c3d4e5f6a7b
Revises: 1b2c3d4e5f6a
Create Date: 2026-07-25
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "2c3d4e5f6a7b"
down_revision: str | None = "1b2c3d4e5f6a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE = "template_v2_revisions"
TEMPLATE_FK = "fk_template_v2_revisions_template_id_template_v2"
TEMPLATE_CREATED_INDEX = "ix_template_v2_revisions_template_created"
REVISION_CHECK = "ck_template_v2_revisions_revision_positive"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("template_id", sa.String(length=128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("merged_components", sa.JSON(), nullable=True),
        sa.Column("layouts", sa.JSON(), nullable=True),
        sa.Column("assets", sa.JSON(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("revision >= 1", name=REVISION_CHECK),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["template_v2.id"],
            name=TEMPLATE_FK,
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("template_id", "revision"),
    )
    op.create_index(
        TEMPLATE_CREATED_INDEX,
        TABLE,
        ["template_id", "created_at"],
        unique=False,
    )
    op.execute(
        sa.text(
            """
            INSERT INTO template_v2_revisions (
                template_id,
                revision,
                reason,
                name,
                description,
                merged_components,
                layouts,
                assets,
                is_default,
                created_at
            )
            SELECT
                template.id,
                local_state.revision,
                'baseline',
                template.name,
                template.description,
                template.merged_components,
                template.layouts,
                template.assets,
                template.is_default,
                template.updated_at
            FROM template_v2 AS template
            JOIN template_v2_local_state AS local_state
              ON local_state.template_id = template.id
            """
        )
    )


def downgrade() -> None:
    op.drop_index(TEMPLATE_CREATED_INDEX, table_name=TABLE)
    op.drop_table(TABLE)
