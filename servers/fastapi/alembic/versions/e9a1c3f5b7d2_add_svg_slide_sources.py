"""Add isolated SVG sources and durable SVG generation checkpoints.

Revision ID: e9a1c3f5b7d2
Revises: d4a7c9e1b3f6
"""

from alembic import op
import sqlalchemy as sa

revision = "e9a1c3f5b7d2"
down_revision = "d4a7c9e1b3f6"
branch_labels = None
depends_on = None

CHECKS = {
    "ck_slides_svg_source_exclusive": (
        "svg_content IS NULL OR svg_content = '' OR ((html_content IS NULL OR html_content = '') AN"
        'D ui IS NULL)'
                                      ),
    "ck_slides_source_format": (
        "source_format IS NULL OR (source_format = 'svg' AND (html_content IS NULL OR html_content "
        "= '') AND ui IS NULL) OR (source_format = 'html' AND (svg_content IS NULL OR svg_content ="
        " '') AND ui IS NULL) OR (source_format = 'native' AND (svg_content IS NULL OR svg_content "
        "= '') AND (html_content IS NULL OR html_content = ''))"
                               ),
}


def upgrade():
    with op.batch_alter_table("slides") as batch:
        batch.add_column(sa.Column("svg_content", sa.Text(), nullable=True))
        batch.add_column(sa.Column("source_format", sa.String(), nullable=True))
        for name, expression in CHECKS.items():
            batch.create_check_constraint(name, expression)
    op.add_column("authored_slide_checkpoints", sa.Column("svg_checkpoint", sa.JSON(), nullable=True))
    op.add_column("presentation_generation_jobs", sa.Column("svg_style_snapshot", sa.JSON(), nullable=True))


def downgrade():
    # Readers from older releases cannot safely interpret SVG identities. Never
    # discard their sources or checkpoints as part of a rollback.
    bind = op.get_bind()
    for table, predicate in (
        ("slides", "svg_content IS NOT NULL OR source_format IS NOT NULL"),
        ("authored_slide_checkpoints", "svg_checkpoint IS NOT NULL"),
        ("presentation_generation_jobs", "svg_style_snapshot IS NOT NULL"),
        ("presentations", "mode = 'svg' OR profile_driver_id = 'svg-v1'"),
    ):
        if bind.execute(sa.text(f"SELECT 1 FROM {table} WHERE {predicate} LIMIT 1")).first():
            raise RuntimeError("svg_source_rollback_requires_data_retention")
    op.drop_column("presentation_generation_jobs", "svg_style_snapshot")
    op.drop_column("authored_slide_checkpoints", "svg_checkpoint")
    with op.batch_alter_table("slides") as batch:
        for name in CHECKS:
            batch.drop_constraint(name, type_="check")
        batch.drop_column("source_format")
        batch.drop_column("svg_content")
