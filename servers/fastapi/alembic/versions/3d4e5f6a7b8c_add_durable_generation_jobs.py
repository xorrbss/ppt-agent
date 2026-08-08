"""add durable presentation generation jobs

Revision ID: 3d4e5f6a7b8c
Revises: 2c3d4e5f6a7b
Create Date: 2026-07-26
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "3d4e5f6a7b8c"
down_revision: str | None = "2c3d4e5f6a7b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE = "presentation_generation_jobs"


def upgrade() -> None:
    op.add_column(
        "presentations",
        sa.Column(
            "lifecycle_status",
            sa.String(),
            nullable=False,
            server_default="published",
        ),
    )
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("presentation_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("template_v2_target", sa.JSON(), nullable=True),
        sa.Column(
            "export_cookie_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column(
            "attempt_number",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("attempt_token", sa.String(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["async_presentation_generation_tasks.id"],
            name="fk_presentation_generation_jobs_task_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("presentation_id"),
        sa.UniqueConstraint("task_id"),
    )
    op.create_index(
        "ix_presentation_generation_jobs_dispatch",
        TABLE,
        ["state", "lease_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_presentation_generation_jobs_presentation_id",
        TABLE,
        ["presentation_id"],
        unique=True,
    )
    op.create_index(
        "ix_presentation_generation_jobs_task_id",
        TABLE,
        ["task_id"],
        unique=True,
    )
    op.create_index(
        "uq_presentation_generation_jobs_idempotency_key",
        TABLE,
        ["idempotency_key_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_presentation_generation_jobs_idempotency_key", table_name=TABLE
    )
    op.drop_index(
        "ix_presentation_generation_jobs_task_id", table_name=TABLE
    )
    op.drop_index(
        "ix_presentation_generation_jobs_presentation_id", table_name=TABLE
    )
    op.drop_index(
        "ix_presentation_generation_jobs_dispatch", table_name=TABLE
    )
    op.drop_table(TABLE)
    op.drop_column("presentations", "lifecycle_status")
