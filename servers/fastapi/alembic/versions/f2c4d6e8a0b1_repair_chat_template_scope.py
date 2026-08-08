"""repair chat template scope on databases stamped past reconciliation

Revision ID: f2c4d6e8a0b1
Revises: e1b3c5d7f9a2
Create Date: 2026-07-31
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "f2c4d6e8a0b1"
down_revision: str | None = "e1b3c5d7f9a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "chat_history_messages"
TEMPLATE_TABLE = "template_v2"
TEMPLATE_COLUMN = "template_v2_id"
TEMPLATE_FK = "fk_chat_history_messages_template_v2_id_template_v2"
TEMPLATE_INDEX = "ix_chat_history_messages_template_v2_id"


def _has_table(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def _columns(table_name: str) -> set[str]:
    if not _has_table(table_name):
        return set()
    return {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _has_template_foreign_key() -> bool:
    if not _has_table(TABLE):
        return False
    return any(
        foreign_key.get("constrained_columns") == [TEMPLATE_COLUMN]
        and foreign_key.get("referred_table") == TEMPLATE_TABLE
        and foreign_key.get("referred_columns") == ["id"]
        for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(TABLE)
    )


def _has_index() -> bool:
    if not _has_table(TABLE):
        return False
    return TEMPLATE_INDEX in {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes(TABLE)
    }


def upgrade() -> None:
    # Some fork databases were stamped beyond the earlier reconciliation while
    # retaining the upstream chat table. Repair that drift without touching rows.
    if not _has_table(TABLE):
        return

    needs_column = TEMPLATE_COLUMN not in _columns(TABLE)
    needs_foreign_key = not _has_template_foreign_key()
    if needs_column or needs_foreign_key:
        with op.batch_alter_table(TABLE) as batch:
            if needs_column:
                batch.add_column(
                    sa.Column(
                        TEMPLATE_COLUMN,
                        sqlmodel.sql.sqltypes.AutoString(),
                        nullable=True,
                    )
                )
            if needs_foreign_key:
                batch.create_foreign_key(
                    TEMPLATE_FK,
                    TEMPLATE_TABLE,
                    [TEMPLATE_COLUMN],
                    ["id"],
                    ondelete="CASCADE",
                )

    if not _has_index():
        op.create_index(TEMPLATE_INDEX, TABLE, [TEMPLATE_COLUMN], unique=False)


def downgrade() -> None:
    # e1b3c5d7f9a2 already expects this schema through 4e5f6a7b8c9d. This
    # revision only repairs drift, so downgrading the repair must preserve it.
    pass
