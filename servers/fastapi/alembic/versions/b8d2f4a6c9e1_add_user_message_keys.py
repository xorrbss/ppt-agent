"""Carry backend user-message keys next to their English fallback text.

``constants/user_messages.py`` makes the backend emit a message *key* (and
its parameters) wherever it used to emit localized prose, so the frontend can
render the text in the UI locale.  Two persisted transports need room for the
key beside the legacy text column: the legacy async task row (``message``)
and the version history label (``label``).  Both pairs are nullable; rows
written before this revision keep their text and render it verbatim.

Both tables are created by ``create_db_and_tables()`` on some upgrade paths
(the legacy task table predates its Alembic history), so each step checks the
live schema and is a no-op when the table is absent or the column present.
"""
from alembic import op
import sqlalchemy as sa

revision = "b8d2f4a6c9e1"
down_revision = "a7c9e1b3d5f8"
branch_labels = None
depends_on = None

_COLUMNS = {
    "async_presentation_generation_tasks": ("message_key", "message_params"),
    "presentation_versions": ("label_key", "label_params"),
}


def _existing_columns(table: str) -> set[str] | None:
    inspector = sa.inspect(op.get_bind())
    if table not in set(inspector.get_table_names()):
        return None
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    for table, (key_column, params_column) in _COLUMNS.items():
        existing = _existing_columns(table)
        if existing is None:
            continue
        with op.batch_alter_table(table) as batch:
            if key_column not in existing:
                batch.add_column(sa.Column(key_column, sa.String(), nullable=True))
            if params_column not in existing:
                batch.add_column(sa.Column(params_column, sa.JSON(), nullable=True))


def downgrade() -> None:
    for table, (key_column, params_column) in reversed(_COLUMNS.items()):
        existing = _existing_columns(table)
        if existing is None:
            continue
        with op.batch_alter_table(table) as batch:
            if params_column in existing:
                batch.drop_column(params_column)
            if key_column in existing:
                batch.drop_column(key_column)
