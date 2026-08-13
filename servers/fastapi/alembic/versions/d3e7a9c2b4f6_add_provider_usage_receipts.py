"""Add privacy-safe provider usage receipts.

Revision ID: d3e7a9c2b4f6
Revises: d2b5e8f0a3c6
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d3e7a9c2b4f6"
down_revision: str | None = "d2b5e8f0a3c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE = "provider_usage_receipts"


def upgrade() -> None:
    if TABLE in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("receipt_key_hash", sa.String(length=64), nullable=False),
        sa.Column("retry_class", sa.String(length=16), nullable=False),
        sa.Column(
            "outcome", sa.String(length=16), nullable=False,
            server_default="succeeded",
        ),
        sa.Column("latency_ms", sa.BigInteger(), nullable=False),
        sa.Column("usage_state", sa.String(length=16), nullable=False),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("reported_cost_microunits", sa.BigInteger(), nullable=True),
        sa.Column("estimated_cost_microunits", sa.BigInteger(), nullable=True),
        sa.Column("pricing_version", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "usage_state IN ('reported', 'estimated', 'unavailable')",
            name="ck_provider_usage_receipts_usage_state",
        ),
        sa.CheckConstraint(
            "retry_class IN ('initial', 'retry', 'resume', 'recovery')",
            name="ck_provider_usage_receipts_retry_class",
        ),
        sa.CheckConstraint(
            "outcome IN ('succeeded', 'failed', 'cancelled')",
            name="ck_provider_usage_receipts_outcome",
        ),
        sa.CheckConstraint(
            "request_count > 0", name="ck_provider_usage_receipts_request_count"
        ),
        sa.CheckConstraint(
            "latency_ms >= 0", name="ck_provider_usage_receipts_latency"
        ),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_provider_usage_receipts_input_tokens",
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_provider_usage_receipts_output_tokens",
        ),
        sa.CheckConstraint(
            "reported_cost_microunits IS NULL OR reported_cost_microunits >= 0",
            name="ck_provider_usage_receipts_reported_cost",
        ),
        sa.CheckConstraint(
            "estimated_cost_microunits IS NULL OR estimated_cost_microunits >= 0",
            name="ck_provider_usage_receipts_estimated_cost",
        ),
        sa.CheckConstraint(
            "usage_state != 'unavailable' OR (input_tokens IS NULL AND "
            "output_tokens IS NULL AND reported_cost_microunits IS NULL AND "
            "estimated_cost_microunits IS NULL)",
            name="ck_provider_usage_receipts_unavailable_is_null",
        ),
        sa.CheckConstraint(
            "usage_state != 'reported' OR estimated_cost_microunits IS NULL",
            name="ck_provider_usage_receipts_reported_source",
        ),
        sa.CheckConstraint(
            "usage_state != 'estimated' OR reported_cost_microunits IS NULL",
            name="ck_provider_usage_receipts_estimated_source",
        ),
        sa.CheckConstraint(
            "(reported_cost_microunits IS NULL AND estimated_cost_microunits IS NULL) "
            "OR pricing_version IS NOT NULL",
            name="ck_provider_usage_receipts_priced_versioned",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["presentation_generation_jobs.id"],
            name="fk_provider_usage_receipts_job_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("receipt_key_hash", name="uq_provider_usage_receipts_key_hash"),
    )
    op.create_index("ix_provider_usage_receipts_job_id", TABLE, ["job_id"], unique=False)
    op.create_index(
        "ix_provider_usage_receipts_job_stage",
        TABLE,
        ["job_id", "operation", "stage", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    if TABLE in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table(TABLE)
