"""Drop the Template V2 studio tables and the last of its sidecar columns.

Template V2 (structured templates, the template-preview studio, PPTX
import/generation) was removed entirely on 2026-09-25. This migration only
drops storage; it does not touch any earlier migration's history.
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "f432fc854225"
down_revision = "f1c3e5a7b9d2"
branch_labels = None
depends_on = None

_CHAT_TEMPLATE_FK = "fk_chat_history_messages_template_v2_id_template_v2"
_CHAT_TEMPLATE_INDEX = "ix_chat_history_messages_template_v2_id"


def _has_table(name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return name in inspector.get_table_names()


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return column in {row["name"] for row in inspector.get_columns(table)}


def upgrade() -> None:
    if _has_column("chat_history_messages", "template_v2_id"):
        with op.batch_alter_table("chat_history_messages") as batch:
            batch.drop_constraint(_CHAT_TEMPLATE_FK, type_="foreignkey")
            batch.drop_index(_CHAT_TEMPLATE_INDEX)
            batch.drop_column("template_v2_id")
    if _has_column("presentation_generation_jobs", "template_v2_target"):
        with op.batch_alter_table("presentation_generation_jobs") as batch:
            batch.drop_column("template_v2_target")
    for table in (
        "template_v2_revisions",
        "template_v2_pptx_imports",
        "template_v2_local_state",
        "template_v2",
    ):
        if _has_table(table):
            op.drop_table(table)


def downgrade() -> None:
    raise RuntimeError(
        "template_v2 removal cannot be downgraded: the studio tables and "
        "columns are dropped, not archived"
    )
