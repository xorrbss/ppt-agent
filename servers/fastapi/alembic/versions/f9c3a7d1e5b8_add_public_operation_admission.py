"""Add principal-bound public admission and logical provider reservations.

Revision ID: f9c3a7d1e5b8
Revises: e8d2b5f7a9c1
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f9c3a7d1e5b8"
down_revision: str | None = "e8d2b5f7a9c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


POLICIES = "public_policy_profiles"
QUOTAS = "public_quota_counters"
ADMISSIONS = "public_operation_admissions"
CALLS = "public_provider_logical_calls"


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    expected = {POLICIES, QUOTAS, ADMISSIONS, CALLS}
    present = expected & existing
    if present:
        if present != expected:
            raise RuntimeError("public_admission_schema_is_partially_applied")
        return

    op.create_table(
        POLICIES,
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("max_slides", sa.Integer(), nullable=False),
        sa.Column("owner_active_generation", sa.Integer(), nullable=False),
        sa.Column("owner_queued_generation", sa.Integer(), nullable=False),
        sa.Column("tenant_active_generation", sa.Integer(), nullable=False),
        sa.Column("tenant_queued_generation", sa.Integer(), nullable=False),
        sa.Column("owner_active_export", sa.Integer(), nullable=False),
        sa.Column("owner_queued_export", sa.Integer(), nullable=False),
        sa.Column("tenant_active_export", sa.Integer(), nullable=False),
        sa.Column("tenant_queued_export", sa.Integer(), nullable=False),
        sa.Column("retry_request_limit", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_limit_microunits", sa.BigInteger(), nullable=False),
        sa.Column("actual_cost_limit_microunits", sa.BigInteger(), nullable=False),
        sa.Column("generation_deadline_seconds", sa.Integer(), nullable=False),
        sa.Column("export_deadline_seconds", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("max_slides > 0", name="ck_public_policy_max_slides"),
        sa.CheckConstraint(
            "owner_active_generation > 0 AND owner_queued_generation >= 0 "
            "AND tenant_active_generation > 0 AND tenant_queued_generation >= 0 "
            "AND owner_active_export > 0 AND owner_queued_export >= 0 "
            "AND tenant_active_export > 0 AND tenant_queued_export >= 0",
            name="ck_public_policy_quota_limits",
        ),
        sa.CheckConstraint(
            "retry_request_limit >= 0 AND estimated_cost_limit_microunits > 0 "
            "AND actual_cost_limit_microunits > 0",
            name="ck_public_policy_budget_limits",
        ),
        sa.PrimaryKeyConstraint("version"),
    )
    op.bulk_insert(
        sa.table(
            POLICIES,
            sa.column("version", sa.String),
            sa.column("enabled", sa.Boolean),
            sa.column("max_slides", sa.Integer),
            sa.column("owner_active_generation", sa.Integer),
            sa.column("owner_queued_generation", sa.Integer),
            sa.column("tenant_active_generation", sa.Integer),
            sa.column("tenant_queued_generation", sa.Integer),
            sa.column("owner_active_export", sa.Integer),
            sa.column("owner_queued_export", sa.Integer),
            sa.column("tenant_active_export", sa.Integer),
            sa.column("tenant_queued_export", sa.Integer),
            sa.column("retry_request_limit", sa.Integer),
            sa.column("estimated_cost_limit_microunits", sa.BigInteger),
            sa.column("actual_cost_limit_microunits", sa.BigInteger),
            sa.column("generation_deadline_seconds", sa.Integer),
            sa.column("export_deadline_seconds", sa.Integer),
        ),
        [
            {
                "version": "public-default/v1",
                "enabled": True,
                "max_slides": 30,
                "owner_active_generation": 1,
                "owner_queued_generation": 3,
                "tenant_active_generation": 5,
                "tenant_queued_generation": 15,
                "owner_active_export": 1,
                "owner_queued_export": 3,
                "tenant_active_export": 5,
                "tenant_queued_export": 15,
                "retry_request_limit": 4,
                "estimated_cost_limit_microunits": 50_000_000,
                "actual_cost_limit_microunits": 50_000_000,
                "generation_deadline_seconds": 900,
                "export_deadline_seconds": 600,
            }
        ],
    )

    op.create_table(
        QUOTAS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("scope_key", sa.String(length=128), nullable=False),
        sa.Column("operation_kind", sa.String(length=16), nullable=False),
        sa.Column("active_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("queued_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("scope_type IN ('owner', 'tenant')", name="ck_public_quota_scope"),
        sa.CheckConstraint("operation_kind IN ('generation', 'export')", name="ck_public_quota_operation_kind"),
        sa.CheckConstraint("active_count >= 0 AND queued_count >= 0 AND version >= 0", name="ck_public_quota_counts"),
        sa.ForeignKeyConstraint(["policy_version"], [f"{POLICIES}.version"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("policy_version", "scope_type", "scope_key", "operation_kind", name="uq_public_quota_counter_scope"),
    )

    op.create_table(
        ADMISSIONS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("trace_id_hash", sa.String(length=64), nullable=False),
        sa.Column("operation_kind", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("slide_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("planned_request_limit", sa.Integer(), nullable=False),
        sa.Column("retry_request_limit", sa.Integer(), nullable=False),
        sa.Column("estimated_cost_limit_microunits", sa.BigInteger(), nullable=False),
        sa.Column("actual_cost_limit_microunits", sa.BigInteger(), nullable=False),
        sa.Column("initial_request_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("retry_request_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("estimated_reserved_microunits", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("estimated_consumed_microunits", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("actual_reserved_microunits", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("actual_consumed_microunits", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("actual_cost_unavailable_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("operation_kind IN ('generation', 'export')", name="ck_public_operation_admission_kind"),
        sa.CheckConstraint("state IN ('queued', 'active', 'succeeded', 'failed', 'cancelled', 'budget_exhausted')", name="ck_public_operation_admission_state"),
        sa.CheckConstraint("planned_request_limit >= 0 AND retry_request_limit >= 0 AND initial_request_count >= 0 AND retry_request_count >= 0", name="ck_public_operation_admission_requests"),
        sa.CheckConstraint("estimated_cost_limit_microunits >= 0 AND actual_cost_limit_microunits >= 0 AND estimated_reserved_microunits >= 0 AND estimated_consumed_microunits >= 0 AND actual_reserved_microunits >= 0 AND actual_consumed_microunits >= 0 AND actual_cost_unavailable_count >= 0", name="ck_public_operation_admission_costs"),
        sa.ForeignKeyConstraint(["operation_id"], ["workspace_operations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["policy_version"], [f"{POLICIES}.version"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id", name="uq_public_operation_admission_operation"),
    )
    op.create_index("ix_public_operation_admission_queue", ADMISSIONS, ["policy_version", "operation_kind", "state", "queued_at"], unique=False)
    op.create_index("ix_public_operation_admission_owner", ADMISSIONS, ["tenant_id", "owner_id", "operation_kind", "state"], unique=False)

    op.create_table(
        CALLS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("admission_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("slide_index", sa.Integer(), nullable=False, server_default=sa.text("-1")),
        sa.Column("logical_call_ordinal", sa.Integer(), nullable=False),
        sa.Column("retry_class", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="reserved"),
        sa.Column("estimated_cost_microunits", sa.BigInteger(), nullable=False),
        sa.Column("actual_cost_microunits", sa.BigInteger(), nullable=True),
        sa.Column("input_tokens", sa.BigInteger(), nullable=True),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("latency_ms", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("outcome", sa.String(length=24), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("state IN ('reserved', 'reported', 'unavailable', 'failed', 'cancelled')", name="ck_public_provider_logical_call_state"),
        sa.CheckConstraint("retry_class IN ('initial', 'retry', 'resume', 'recovery')", name="ck_public_provider_logical_call_retry_class"),
        sa.CheckConstraint("slide_index >= -1 AND logical_call_ordinal >= 1", name="ck_public_provider_logical_call_identity"),
        sa.CheckConstraint("estimated_cost_microunits >= 0 AND (actual_cost_microunits IS NULL OR actual_cost_microunits >= 0) AND latency_ms >= 0", name="ck_public_provider_logical_call_costs"),
        sa.ForeignKeyConstraint(["admission_id"], [f"{ADMISSIONS}.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("admission_id", "stage", "slide_index", "logical_call_ordinal", name="uq_public_provider_logical_call"),
    )
    op.create_index("ix_public_provider_logical_call_stage", CALLS, ["admission_id", "stage", "slide_index", "logical_call_ordinal"], unique=False)


def downgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for table in (CALLS, ADMISSIONS, QUOTAS, POLICIES):
        if table in existing:
            op.drop_table(table)
