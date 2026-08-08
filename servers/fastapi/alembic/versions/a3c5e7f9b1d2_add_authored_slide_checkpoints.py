"""add authored slide checkpoints and durable progress

Revision ID: a3c5e7f9b1d2
Revises: f2c4d6e8a0b1
Create Date: 2026-08-01
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a3c5e7f9b1d2"
down_revision: str | None = "f2c4d6e8a0b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


JOB_TABLE = "presentation_generation_jobs"
CHECKPOINT_TABLE = "authored_slide_checkpoints"
CHECKPOINT_STATUS_VALUES = (
    "'pending', 'running', 'completed', 'failed', 'skipped'"
)


def _has_table(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    # A small number of legacy repair fixtures were stamped past the durable-job
    # migration without carrying that table. Preserve the repair migration's
    # no-op behavior; runtime schema validation will still reject such an
    # incomplete database instead of silently treating checkpoints as present.
    if not _has_table(JOB_TABLE):
        return

    op.add_column(
        JOB_TABLE,
        sa.Column(
            "total_slides",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        JOB_TABLE,
        sa.Column(
            "completed_slides",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        JOB_TABLE,
        sa.Column("current_stage", sa.String(length=32), nullable=True),
    )
    op.add_column(JOB_TABLE, sa.Column("last_error", sa.JSON(), nullable=True))
    op.add_column(JOB_TABLE, sa.Column("authored_outline", sa.JSON(), nullable=True))
    op.add_column(
        JOB_TABLE,
        sa.Column(
            "resume_allowed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.create_table(
        CHECKPOINT_TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("slide_index", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=True),
        sa.Column(
            "author_status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "illustrate_status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "render_status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "persist_status",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("author_html", sa.Text(), nullable=True),
        sa.Column("illustrated_html", sa.Text(), nullable=True),
        sa.Column("render_path", sa.String(length=1024), nullable=True),
        sa.Column("render_sha256", sa.String(length=64), nullable=True),
        sa.Column("failed_stage", sa.String(length=32), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("attempt_token", sa.String(length=128), nullable=True),
        sa.Column("author_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("author_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "illustrate_started_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "illustrate_completed_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("render_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("render_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("persist_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("persist_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "slide_index >= 0",
            name="ck_authored_slide_checkpoints_slide_index_nonnegative",
        ),
        *(
            sa.CheckConstraint(
                f"{stage}_status IN ({CHECKPOINT_STATUS_VALUES})",
                name=f"ck_authored_slide_checkpoints_{stage}_status",
            )
            for stage in ("author", "illustrate", "render", "persist")
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            [f"{JOB_TABLE}.id"],
            name="fk_authored_slide_checkpoints_job_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_id",
            "slide_index",
            name="uq_authored_slide_checkpoints_job_slide",
        ),
    )
    op.create_index(
        "ix_authored_slide_checkpoints_job_persist",
        CHECKPOINT_TABLE,
        ["job_id", "persist_status"],
        unique=False,
    )


def downgrade() -> None:
    if _has_table(CHECKPOINT_TABLE):
        op.drop_index(
            "ix_authored_slide_checkpoints_job_persist",
            table_name=CHECKPOINT_TABLE,
        )
        op.drop_table(CHECKPOINT_TABLE)

    if not _has_table(JOB_TABLE):
        return

    op.drop_column(JOB_TABLE, "resume_allowed")
    op.drop_column(JOB_TABLE, "last_error")
    op.drop_column(JOB_TABLE, "authored_outline")
    op.drop_column(JOB_TABLE, "current_stage")
    op.drop_column(JOB_TABLE, "completed_slides")
    op.drop_column(JOB_TABLE, "total_slides")
