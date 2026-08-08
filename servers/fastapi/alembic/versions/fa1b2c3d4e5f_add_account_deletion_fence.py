"""Add durable account-deletion admission and publication fencing.

Revision ID: fa1b2c3d4e5f
Revises: f8b0d3e5a7c9
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "fa1b2c3d4e5f"
down_revision: str | None = "f8b0d3e5a7c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


USER_TABLE = "user"
DELETION_COLUMNS = {
    "deletion_state",
    "deletion_generation",
    "deletion_requested_at",
}
DELETION_CHECKS = {
    "ck_user_deletion_state",
    "ck_user_deletion_generation",
    "ck_user_deletion_marker",
}
DELETION_INDEX = "ix_user_deletion_state_requested_at"


def _schema_state() -> tuple[set[str], set[str], set[str]]:
    inspector = sa.inspect(op.get_bind())
    if USER_TABLE not in inspector.get_table_names():
        raise RuntimeError("account deletion fence requires the user table")
    columns = {column["name"] for column in inspector.get_columns(USER_TABLE)}
    checks = {
        constraint.get("name")
        for constraint in inspector.get_check_constraints(USER_TABLE)
    }
    indexes = {index.get("name") for index in inspector.get_indexes(USER_TABLE)}
    return columns, checks, indexes


def _require_complete_schema(
    columns: set[str], checks: set[str], indexes: set[str]
) -> None:
    present_columns = DELETION_COLUMNS & columns
    if present_columns != DELETION_COLUMNS:
        raise RuntimeError("account deletion fence columns are only partially applied")
    if not DELETION_CHECKS.issubset(checks):
        raise RuntimeError("account deletion fence checks are only partially applied")
    if DELETION_INDEX not in indexes:
        raise RuntimeError("account deletion fence index is missing")


def upgrade() -> None:
    columns, checks, indexes = _schema_state()
    present_columns = DELETION_COLUMNS & columns
    if present_columns:
        _require_complete_schema(columns, checks, indexes)
        return

    with op.batch_alter_table(USER_TABLE) as batch:
        batch.add_column(
            sa.Column(
                "deletion_state",
                sa.String(length=24),
                nullable=False,
                server_default=sa.text("'active'"),
            )
        )
        batch.add_column(
            sa.Column(
                "deletion_generation",
                sa.BigInteger(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch.add_column(
            sa.Column(
                "deletion_requested_at",
                sa.DateTime(timezone=True),
                nullable=True,
            )
        )
        batch.create_check_constraint(
            "ck_user_deletion_state",
            "deletion_state IN ('active', 'deleting')",
        )
        batch.create_check_constraint(
            "ck_user_deletion_generation",
            "deletion_generation >= 0",
        )
        batch.create_check_constraint(
            "ck_user_deletion_marker",
            "(deletion_state = 'active' AND deletion_requested_at IS NULL) "
            "OR (deletion_state = 'deleting' "
            "AND deletion_requested_at IS NOT NULL AND is_active = false)",
        )
        batch.create_index(
            DELETION_INDEX,
            ["deletion_state", "deletion_requested_at"],
            unique=False,
        )

    columns, checks, indexes = _schema_state()
    _require_complete_schema(columns, checks, indexes)


def downgrade() -> None:
    columns, checks, indexes = _schema_state()
    _require_complete_schema(columns, checks, indexes)

    bind = op.get_bind()
    quoted_user_table = bind.dialect.identifier_preparer.quote(USER_TABLE)
    deleting_count = bind.execute(
        sa.text(
            f"SELECT COUNT(*) FROM {quoted_user_table} "
            "WHERE deletion_state = 'deleting'"
        )
    ).scalar_one()
    if deleting_count:
        raise RuntimeError(
            "cannot downgrade account deletion fence while deletion markers exist"
        )

    with op.batch_alter_table(USER_TABLE) as batch:
        batch.drop_index(DELETION_INDEX)
        batch.drop_constraint("ck_user_deletion_marker", type_="check")
        batch.drop_constraint("ck_user_deletion_generation", type_="check")
        batch.drop_constraint("ck_user_deletion_state", type_="check")
        batch.drop_column("deletion_requested_at")
        batch.drop_column("deletion_generation")
        batch.drop_column("deletion_state")
