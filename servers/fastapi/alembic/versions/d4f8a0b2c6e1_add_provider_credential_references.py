"""Add external provider credential references.

Revision ID: d4f8a0b2c6e1
Revises: e4f8a2c6d0b3
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d4f8a0b2c6e1"
down_revision: str | None = "e4f8a2c6d0b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "provider_credential_references"


def upgrade() -> None:
    if TABLE in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("backend", sa.String(length=32), nullable=False),
        sa.Column("credential_ref", sa.String(length=512), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "backend IN ('desktop_vault', 'server_kms')",
            name="ck_provider_credential_references_backend",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"],
            name="fk_provider_credential_references_user_id", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("credential_ref", name="uq_provider_credential_references_credential_ref"),
    )
    op.create_index(
        "ix_provider_credential_references_user_id", TABLE, ["user_id"], unique=False
    )
    op.create_index(
        "uq_provider_credential_references_scope_provider",
        TABLE,
        ["tenant_id", "user_id", "provider"],
        unique=True,
    )


def downgrade() -> None:
    if TABLE in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table(TABLE)
