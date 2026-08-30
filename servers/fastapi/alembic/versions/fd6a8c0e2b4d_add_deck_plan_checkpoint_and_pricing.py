"""Add deck-wide planning checkpoints and provider pricing identity.

Revision ID: fd6a8c0e2b4d
Revises: fa4d8b2e6c90
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "fd6a8c0e2b4d"
down_revision: str | None = "fa4d8b2e6c90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CHECKPOINTS = "authored_deck_plan_checkpoints"
CALLS = "public_provider_logical_calls"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if CHECKPOINTS not in tables:
        op.create_table(
            CHECKPOINTS,
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("generation_job_id", sa.Uuid(), nullable=False),
            sa.Column("stage", sa.String(length=24), nullable=False),
            sa.Column("input_digest", sa.String(length=64), nullable=False),
            sa.Column("output_digest", sa.String(length=64), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("produced_attempt_number", sa.Integer(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.CheckConstraint(
                "stage IN ('deck_plan', 'chapter_plan', 'header', 'illustration')",
                name="ck_authored_deck_plan_checkpoint_stage",
            ),
            sa.CheckConstraint(
                "length(input_digest) = 64 AND length(output_digest) = 64",
                name="ck_authored_deck_plan_checkpoint_digests",
            ),
            sa.CheckConstraint(
                "produced_attempt_number >= 1",
                name="ck_authored_deck_plan_checkpoint_attempt",
            ),
            sa.ForeignKeyConstraint(
                ["generation_job_id"],
                ["presentation_generation_jobs.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "generation_job_id",
                "stage",
                name="uq_authored_deck_plan_checkpoint_stage",
            ),
        )
        op.create_index(
            "ix_authored_deck_plan_checkpoint_job",
            CHECKPOINTS,
            ["generation_job_id", "stage"],
            unique=False,
        )

    call_columns = {column["name"] for column in inspector.get_columns(CALLS)}
    additions = (
        ("provider_id", sa.String(length=64), "unavailable"),
        ("model_id", sa.String(length=128), "unavailable"),
        ("pricing_revision", sa.String(length=64), "unpriced-v1"),
    )
    for name, column_type, default in additions:
        if name not in call_columns:
            op.add_column(
                CALLS,
                sa.Column(name, column_type, nullable=False, server_default=default),
            )
    if "provider_idempotency_capable" not in call_columns:
        op.add_column(
            CALLS,
            sa.Column(
                "provider_idempotency_capable",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if CALLS in tables:
        columns = {
            column["name"] for column in sa.inspect(op.get_bind()).get_columns(CALLS)
        }
        for name in (
            "provider_idempotency_capable",
            "pricing_revision",
            "model_id",
            "provider_id",
        ):
            if name in columns:
                op.drop_column(CALLS, name)
    if CHECKPOINTS in tables:
        op.drop_table(CHECKPOINTS)
