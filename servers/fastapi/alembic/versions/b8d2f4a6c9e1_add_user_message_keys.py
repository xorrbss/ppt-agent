"""Carry backend user-message keys next to their English fallback text.

``constants/user_messages.py`` makes the backend emit a message *key* (and
its parameters) wherever it used to emit localized prose, so the frontend can
render the text in the UI locale.  Two persisted transports need room for the
key beside the legacy text column: the legacy async task row (``message``)
and the version history label (``label``).  Both pairs are nullable; rows
written before this revision keep their text and render it verbatim.
"""
from alembic import op
import sqlalchemy as sa

revision = "b8d2f4a6c9e1"
down_revision = "a7c9e1b3d5f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("async_presentation_generation_tasks") as batch:
        batch.add_column(sa.Column("message_key", sa.String(), nullable=True))
        batch.add_column(sa.Column("message_params", sa.JSON(), nullable=True))
    with op.batch_alter_table("presentation_versions") as batch:
        batch.add_column(sa.Column("label_key", sa.String(), nullable=True))
        batch.add_column(sa.Column("label_params", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("presentation_versions") as batch:
        batch.drop_column("label_params")
        batch.drop_column("label_key")
    with op.batch_alter_table("async_presentation_generation_tasks") as batch:
        batch.drop_column("message_params")
        batch.drop_column("message_key")
