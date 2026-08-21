"""Add append-only canonical Authored document revisions.

Revision ID: a1c4e7b9d2f6
Revises: c7e9a1b3d5f8
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a1c4e7b9d2f6"
down_revision: str | None = "c7e9a1b3d5f8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "authored_canonical_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("presentation_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("parent_revision", sa.BigInteger(), nullable=False),
        sa.Column("source_digest", sa.String(length=71), nullable=False),
        sa.Column("operation", sa.String(length=16), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("history", sa.JSON(), nullable=False),
        sa.Column("history_position", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "history_position >= 0",
            name="ck_authored_canonical_history_position_nonnegative",
        ),
        sa.CheckConstraint(
            "operation IN ('initialize', 'patch', 'undo', 'redo')",
            name="ck_authored_canonical_operation",
        ),
        sa.CheckConstraint(
            "revision >= 0 AND parent_revision >= 0",
            name="ck_authored_canonical_revision_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["presentation_id"], ["presentations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "presentation_id",
            "revision",
            name="uq_authored_canonical_presentation_revision",
        ),
    )
    op.create_index(
        "ix_authored_canonical_revisions_owner_id",
        "authored_canonical_revisions",
        ["owner_id"],
        unique=False,
    )
    op.create_index(
        "ix_authored_canonical_presentation_created",
        "authored_canonical_revisions",
        ["presentation_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_authored_canonical_presentation_created",
        table_name="authored_canonical_revisions",
    )
    op.drop_index(
        "ix_authored_canonical_revisions_owner_id",
        table_name="authored_canonical_revisions",
    )
    op.drop_table("authored_canonical_revisions")
