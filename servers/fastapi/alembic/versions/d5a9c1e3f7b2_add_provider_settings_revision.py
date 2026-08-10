"""add monotonic provider settings revision

Revision ID: d5a9c1e3f7b2
Revises: c4e8f0a2b6d9
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "d5a9c1e3f7b2"
down_revision: str | None = "c4e8f0a2b6d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns() -> dict[str, dict]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "provider_settings" not in inspector.get_table_names():
        return {}
    return {
        column["name"]: column
        for column in inspector.get_columns("provider_settings")
    }


def upgrade() -> None:
    columns = _columns()
    if not columns:
        # Some historically stamped databases skipped the idempotent d0 table
        # creation. Repair that known absence while retaining strict validation
        # for any table that already claims to implement this contract.
        op.create_table(
            "provider_settings",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("config", sa.JSON(), nullable=False),
            sa.Column(
                "revision",
                sa.BigInteger(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        return
    revision_column = columns.get("revision")
    if revision_column is None:
        op.add_column(
            "provider_settings",
            sa.Column(
                "revision",
                sa.BigInteger(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )
        return

    default = (
        str(revision_column.get("default"))
        .lower()
        .replace("::bigint", "")
        .strip("'\"() ")
    )
    if (
        not isinstance(revision_column.get("type"), sa.BigInteger)
        or revision_column.get("nullable", True)
        or default != "0"
    ):
        raise RuntimeError("provider_settings revision column is incompatible")


def downgrade() -> None:
    if "revision" in _columns():
        with op.batch_alter_table("provider_settings") as batch:
            batch.drop_column("revision")
