"""Add nullable document-owned SVG header candidate checkpoints."""

from alembic import op
import sqlalchemy as sa

revision = "f5b7d9e1a3c6"
down_revision = "f4a6c8e0b2d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("presentation_generation_jobs", "workspace_operations"):
        op.add_column(table, sa.Column("svg_header_design_checkpoint", sa.JSON(), nullable=True))


def downgrade() -> None:
    for table in ("workspace_operations", "presentation_generation_jobs"):
        op.drop_column(table, "svg_header_design_checkpoint")
