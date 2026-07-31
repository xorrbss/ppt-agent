"""reconcile fork schema for Presenton multi-user support

Revision ID: 4e5f6a7b8c9d
Revises: 3d4e5f6a7b8c
Create Date: 2026-07-29
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "4e5f6a7b8c9d"
down_revision: str | None = "3d4e5f6a7b8c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHAT_TEMPLATE_FK = "fk_chat_history_messages_template_v2_id_template_v2"
CHAT_TEMPLATE_INDEX = "ix_chat_history_messages_template_v2_id"


def _has_table(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def _columns(table_name: str) -> dict[str, dict]:
    if not _has_table(table_name):
        return {}
    return {
        column["name"]: column
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _has_index(table_name: str, index_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return index_name in {
        index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)
    }


def _has_foreign_key(table_name: str, constraint_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return constraint_name in {
        foreign_key.get("name")
        for foreign_key in sa.inspect(op.get_bind()).get_foreign_keys(table_name)
    }


def upgrade() -> None:
    template_columns = _columns("template_v2")
    if (
        "presentation_id" in template_columns
        and not template_columns["presentation_id"].get("nullable", True)
    ):
        with op.batch_alter_table("template_v2") as batch:
            batch.alter_column(
                "presentation_id",
                existing_type=sa.Uuid(),
                nullable=True,
            )

    chat_columns = _columns("chat_history_messages")
    if chat_columns:
        with op.batch_alter_table("chat_history_messages") as batch:
            if (
                "presentation_id" in chat_columns
                and not chat_columns["presentation_id"].get("nullable", True)
            ):
                batch.alter_column(
                    "presentation_id",
                    existing_type=sa.Uuid(),
                    nullable=True,
                )
            if "template_v2_id" not in chat_columns:
                batch.add_column(
                    sa.Column(
                        "template_v2_id",
                        sqlmodel.sql.sqltypes.AutoString(),
                        nullable=True,
                    )
                )
            if not _has_foreign_key("chat_history_messages", CHAT_TEMPLATE_FK):
                batch.create_foreign_key(
                    CHAT_TEMPLATE_FK,
                    "template_v2",
                    ["template_v2_id"],
                    ["id"],
                    ondelete="CASCADE",
                )

        if not _has_index("chat_history_messages", CHAT_TEMPLATE_INDEX):
            op.create_index(
                CHAT_TEMPLATE_INDEX,
                "chat_history_messages",
                ["template_v2_id"],
                unique=False,
            )


def downgrade() -> None:
    if _has_table("chat_history_messages"):
        if _has_index("chat_history_messages", CHAT_TEMPLATE_INDEX):
            op.drop_index(
                CHAT_TEMPLATE_INDEX,
                table_name="chat_history_messages",
            )
        with op.batch_alter_table("chat_history_messages") as batch:
            if _has_foreign_key("chat_history_messages", CHAT_TEMPLATE_FK):
                batch.drop_constraint(CHAT_TEMPLATE_FK, type_="foreignkey")
            if "template_v2_id" in _columns("chat_history_messages"):
                batch.drop_column("template_v2_id")

    template_columns = _columns("template_v2")
    if (
        "presentation_id" in template_columns
        and template_columns["presentation_id"].get("nullable", True)
    ):
        null_count = op.get_bind().execute(
            sa.text(
                "SELECT COUNT(*) FROM template_v2 "
                "WHERE presentation_id IS NULL"
            )
        ).scalar_one()
        if null_count:
            raise RuntimeError(
                "Cannot downgrade Presenton multi-user reconciliation while "
                "template_v2 rows have no presentation_id"
            )
        with op.batch_alter_table("template_v2") as batch:
            batch.alter_column(
                "presentation_id",
                existing_type=sa.Uuid(),
                nullable=False,
            )
