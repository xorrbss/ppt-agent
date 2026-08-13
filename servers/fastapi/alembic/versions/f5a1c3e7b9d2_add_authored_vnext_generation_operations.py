"""Add durable authored-vNext generation operations.

Revision ID: f5a1c3e7b9d2
Revises: d4f8a0b2c6e1
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f5a1c3e7b9d2"
down_revision: str | None = "d4f8a0b2c6e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "authored_vnext_generation_operations"


def upgrade() -> None:
    if TABLE in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("owner_scope", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("operation_id", sa.String(length=256), nullable=False),
        sa.Column("deck_id", sa.String(length=256), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False, server_default="queued"),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("checkpoint_payload", sa.JSON(), nullable=True),
        sa.Column("result_payload", sa.JSON(), nullable=True),
        sa.Column("expected_revision", sa.BigInteger(), nullable=True),
        sa.Column("expected_digest", sa.String(length=71), nullable=True),
        sa.Column("result_revision", sa.BigInteger(), nullable=True),
        sa.Column("result_digest", sa.String(length=71), nullable=True),
        sa.Column(
            "account_deletion_generation",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("worker_id", sa.String(length=256), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_avnext_generation_operation_state",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["user.id"],
            name="fk_avnext_generation_operations_owner_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND fencing_token >= 0 AND version >= 0 "
            "AND account_deletion_generation >= 0",
            name="ck_avnext_generation_operation_counters",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_scope", "tenant_id", "operation_id",
            name="uq_avnext_generation_operation_id",
        ),
        sa.UniqueConstraint(
            "owner_scope", "tenant_id", "kind", "idempotency_key_hash",
            name="uq_avnext_generation_operation_idempotency",
        ),
    )
    op.create_index(
        "ix_avnext_generation_operation_dispatch",
        TABLE,
        ["state", "lease_expires_at", "created_at"],
    )


def downgrade() -> None:
    if TABLE not in sa.inspect(op.get_bind()).get_table_names():
        return
    op.drop_index("ix_avnext_generation_operation_dispatch", table_name=TABLE)
    op.drop_table(TABLE)
