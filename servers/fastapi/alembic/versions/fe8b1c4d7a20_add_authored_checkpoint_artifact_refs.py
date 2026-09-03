"""Store authored checkpoint ArtifactRef bindings.

Revision ID: fe8b1c4d7a20
Revises: fd6a8c0e2b4d
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "fe8b1c4d7a20"
down_revision: str | None = "fd6a8c0e2b4d"
branch_labels = None
depends_on = None

CHECKPOINT_TABLE = "authored_slide_checkpoints"

# The checkpoint table's own foreign key reaches presentation_generation_jobs,
# whose key in turn reaches async_presentation_generation_tasks.  A sparse
# legacy database can carry that second table only as a dangling reference, and
# SQLite batch mode reflects the whole key closure by default, so a column edit
# here would fail over a table it never touches.  Adding and dropping a column
# needs none of that closure.
_REFLECT_KWARGS = {"resolve_fks": False}


def _has_checkpoint_table() -> bool:
    """Whether a3c5e7f9b1d2 actually created the table on this database.

    That revision returns without creating anything when the generation-job
    table is absent, so a database upgraded from a sparse legacy schema reaches
    this revision with no checkpoint table at all.  Editing it unconditionally
    ends the upgrade for exactly the databases the earlier revision chose to
    leave alone.
    """
    return CHECKPOINT_TABLE in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _has_checkpoint_table():
        return
    with op.batch_alter_table(
        CHECKPOINT_TABLE, reflect_kwargs=_REFLECT_KWARGS
    ) as batch_op:
        batch_op.add_column(sa.Column("artifact_refs", sa.JSON(), nullable=True))


def downgrade() -> None:
    if not _has_checkpoint_table():
        return
    with op.batch_alter_table(
        CHECKPOINT_TABLE, reflect_kwargs=_REFLECT_KWARGS
    ) as batch_op:
        batch_op.drop_column("artifact_refs")
