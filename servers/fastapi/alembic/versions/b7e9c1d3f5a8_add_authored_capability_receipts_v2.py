"""add append-only authored capability receipts v2

Revision ID: b7e9c1d3f5a8
Revises: a2d5f8b1c4e7
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "b7e9c1d3f5a8"
down_revision: str | None = "a2d5f8b1c4e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "authored_capability_receipts_v2",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("capability_id", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("scope_digest", sa.String(length=71), nullable=False),
        sa.Column("environment_digest", sa.String(length=71), nullable=False),
        sa.Column("serializer_id", sa.String(length=128), nullable=False),
        sa.Column("serializer_version", sa.String(length=128), nullable=False),
        sa.Column("issuer_principal_id", sa.Uuid(), nullable=False),
        sa.Column("issuer_authority", sa.String(length=32), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("transition_kind", sa.String(length=16), nullable=False),
        sa.Column("transition_target_digest", sa.String(length=71), nullable=True),
        sa.Column("receipt_digest", sa.String(length=71), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "state IN ('disabled', 'shadow', 'ready', 'blocked')",
            name="ck_authored_capability_receipt_v2_state",
        ),
        sa.CheckConstraint(
            "transition_kind IN ('issue', 'supersede', 'revoke')",
            name="ck_authored_capability_receipt_v2_transition_kind",
        ),
        sa.CheckConstraint(
            "expires_at > evaluated_at", name="ck_authored_capability_receipt_v2_expiry"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "receipt_digest", name="uq_authored_capability_receipt_v2_digest"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "transition_target_digest",
            name="uq_authored_capability_receipt_v2_transition_target",
        ),
    )
    op.create_index(
        "ix_authored_capability_receipt_v2_active_lookup",
        "authored_capability_receipts_v2",
        ["tenant_id", "capability_id", "scope_digest", "environment_digest", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_authored_capability_receipt_v2_active_lookup",
        table_name="authored_capability_receipts_v2",
    )
    op.drop_table("authored_capability_receipts_v2")
