"""Add immutable reference snapshots.

Revision ID: f7c9e1a3b5d8
Revises: e6b8d0f2a4c6
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "f7c9e1a3b5d8"
down_revision: str | None = "e6b8d0f2a4c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TABLE = "reference_snapshots"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if TABLE in inspector.get_table_names():
        return
    required = {"user", "workspace_conversations"}
    missing = required - set(inspector.get_table_names())
    if missing:
        raise RuntimeError(
            "reference snapshots require existing owner and conversation tables: "
            + ", ".join(sorted(missing))
        )

    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), nullable=False, primary_key=True),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("provider_id", sa.String(length=32), nullable=False),
        sa.Column("source_revision", sa.String(length=256), nullable=False),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("normalized_payload", sa.JSON(), nullable=False),
        sa.Column("asset_bindings", sa.JSON(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "schema_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "provider_id IN ('community', 'uploaded_document', 'template')",
            name="ck_reference_snapshots_provider",
        ),
        sa.CheckConstraint(
            "schema_version = 1",
            name="ck_reference_snapshots_schema_version",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["user.id"],
            name="fk_reference_snapshots_owner_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["workspace_conversations.id"],
            name="fk_reference_snapshots_conversation_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_reference_snapshots_owner_conversation_created",
        TABLE,
        ["owner_id", "conversation_id", "created_at"],
    )
    op.create_index(
        "ix_reference_snapshots_expiry",
        TABLE,
        ["expires_at"],
    )


def downgrade() -> None:
    if TABLE in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table(TABLE)
