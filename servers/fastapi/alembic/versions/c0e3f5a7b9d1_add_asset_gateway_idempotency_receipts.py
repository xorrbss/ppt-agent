"""Add durable Asset Gateway idempotency receipts.

Revision ID: c0e3f5a7b9d1
Revises: b9d2e4f6a8c1
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "c0e3f5a7b9d1"
down_revision: str | None = "b9d2e4f6a8c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "asset_gateway_idempotency_receipts" in inspector.get_table_names():
        return
    op.create_table(
        "asset_gateway_idempotency_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("owner_scope", sa.String(length=36), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            sa.String(length=16),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("response_payload", sa.JSON(), nullable=True),
        sa.Column("asset_id", sa.Uuid(), nullable=True),
        sa.Column("snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'complete')",
            name="ck_asset_gateway_receipts_state",
        ),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["workspace_assets.id"],
            name="fk_asset_gateway_receipts_asset_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["user.id"],
            name="fk_asset_gateway_receipts_owner_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["reference_snapshots.id"],
            name="fk_asset_gateway_receipts_snapshot_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_scope",
            "operation",
            "key_hash",
            name="uq_asset_gateway_receipts_owner_operation_key",
        ),
    )
    op.create_index(
        "ix_asset_gateway_receipts_expiry",
        "asset_gateway_idempotency_receipts",
        ["expires_at", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_asset_gateway_receipts_asset",
        "asset_gateway_idempotency_receipts",
        ["owner_scope", "asset_id"],
        unique=False,
    )
    op.create_index(
        "ix_asset_gateway_receipts_snapshot",
        "asset_gateway_idempotency_receipts",
        ["owner_scope", "snapshot_id"],
        unique=False,
    )


def downgrade() -> None:
    if "asset_gateway_idempotency_receipts" in sa.inspect(
        op.get_bind()
    ).get_table_names():
        op.drop_table("asset_gateway_idempotency_receipts")
