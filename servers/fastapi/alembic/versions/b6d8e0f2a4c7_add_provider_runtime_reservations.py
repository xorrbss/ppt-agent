"""Add durable provider budget, reservation, RPM, and circuit state.

Revision ID: b6d8e0f2a4c7
Revises: a6b2c4d8e0f3

The physical contract is frozen here instead of importing live ORM metadata.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "b6d8e0f2a4c7"
down_revision: str | None = "a6b2c4d8e0f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


BUDGETS = "provider_runtime_budgets"
RESERVATIONS = "provider_runtime_reservations"
CIRCUITS = "provider_runtime_circuits"


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    expected = {BUDGETS, RESERVATIONS, CIRCUITS}
    present = expected & existing
    if present:
        if present != expected:
            raise RuntimeError("provider_runtime_schema_is_partially_applied")
        return

    op.create_table(
        BUDGETS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column(
            "state",
            sa.String(length=16),
            nullable=False,
            server_default="active",
        ),
        sa.Column("cancellation_reason", sa.String(length=64), nullable=True),
        sa.Column("estimated_limit", sa.BigInteger(), nullable=True),
        sa.Column("actual_limit", sa.BigInteger(), nullable=True),
        sa.Column(
            "estimated_consumed",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "estimated_reserved",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "actual_consumed",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "request_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "version",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
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
            "state IN ('active', 'cancelled', 'completed')",
            name="ck_provider_runtime_budget_state",
        ),
        sa.CheckConstraint(
            "estimated_limit IS NULL OR estimated_limit >= 0",
            name="ck_provider_runtime_budget_estimated_limit",
        ),
        sa.CheckConstraint(
            "actual_limit IS NULL OR actual_limit >= 0",
            name="ck_provider_runtime_budget_actual_limit",
        ),
        sa.CheckConstraint(
            "estimated_consumed >= 0 AND estimated_reserved >= 0 "
            "AND actual_consumed >= 0 AND request_count >= 0 AND version >= 0",
            name="ck_provider_runtime_budget_counters",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["user.id"],
            name="fk_provider_runtime_budgets_owner_id_user",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["presentation_generation_jobs.id"],
            name="fk_provider_runtime_budgets_job_id_generation_job",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "owner_id",
            "job_id",
            name="uq_provider_runtime_budget_job",
        ),
    )
    op.create_index(
        "ix_provider_runtime_budget_owner",
        BUDGETS,
        ["tenant_id", "owner_id", "state"],
        unique=False,
    )

    op.create_table(
        RESERVATIONS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("budget_id", sa.Uuid(), nullable=False),
        sa.Column("reservation_key_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            sa.String(length=16),
            nullable=False,
            server_default="reserved",
        ),
        sa.Column("estimated_cost", sa.BigInteger(), nullable=False),
        sa.Column("actual_cost", sa.BigInteger(), nullable=True),
        sa.Column("outcome", sa.String(length=24), nullable=True),
        sa.Column("lease_token_hash", sa.String(length=64), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
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
            "state IN ('reserved', 'reconciled', 'cancelled', 'uncertain')",
            name="ck_provider_runtime_reservation_state",
        ),
        sa.CheckConstraint(
            "estimated_cost >= 0 AND (actual_cost IS NULL OR actual_cost >= 0)",
            name="ck_provider_runtime_reservation_cost",
        ),
        sa.ForeignKeyConstraint(
            ["budget_id"],
            [f"{BUDGETS}.id"],
            name="fk_provider_runtime_reservations_budget_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "budget_id",
            "reservation_key_hash",
            name="uq_provider_runtime_reservation",
        ),
    )
    op.create_index(
        "ix_provider_runtime_reservation_state",
        RESERVATIONS,
        ["state", "lease_expires_at"],
        unique=False,
    )

    op.create_table(
        CIRCUITS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("service_key", sa.String(length=128), nullable=False),
        sa.Column(
            "state",
            sa.String(length=16),
            nullable=False,
            server_default="closed",
        ),
        sa.Column(
            "rpm_window_started_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "rpm_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("open_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("half_open_lease_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "version",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "state IN ('closed', 'open', 'half_open')",
            name="ck_provider_runtime_circuit_state",
        ),
        sa.CheckConstraint(
            "rpm_count >= 0 AND consecutive_failures >= 0 AND version >= 0",
            name="ck_provider_runtime_circuit_counters",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "service_key",
            name="uq_provider_runtime_circuit_service",
        ),
    )


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for table in (CIRCUITS, RESERVATIONS, BUDGETS):
        if table in existing:
            op.drop_table(table)
