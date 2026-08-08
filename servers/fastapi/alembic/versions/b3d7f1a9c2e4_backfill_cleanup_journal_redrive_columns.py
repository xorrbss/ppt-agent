"""Backfill cleanup-journal redrive columns onto a pre-existing table.

Revision ID: b3d7f1a9c2e4
Revises: fc3d5e7f9a1b

`f8b0d3e5a7c9` creates `workspace_cleanup_journal` only when the table is
absent:

    if not _table_exists(CLEANUP_JOURNAL):
        _create_cleanup_journal()

An environment whose table already existed - created by an older model through
`create_all` before the redrive columns were added - therefore ran that
revision to completion without gaining `redrive_count`, `last_redriven_at` or
`last_redriven_by_snapshot`.  Alembic reports such a database as being at head
while every presentation deletion fails on the journal insert with
"table workspace_cleanup_journal has no column named redrive_count", which the
handler reports only as `presentation_delete_database_unavailable`.

This revision adds whichever of those columns is missing.  It is a no-op on a
database that got the table from `f8b0d3e5a7c9`.

Note on the check constraint: a freshly created table also carries
`ck_workspace_cleanup_journal_redrive_count`.  Reproducing it here would mean
rebuilding the table under SQLite batch mode, and the repair is not worth
risking the existing indexes and unique constraint for a guard on a column the
application only ever increments.  A repaired database is therefore missing
that one constraint relative to a fresh one; the columns and their defaults
match.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b3d7f1a9c2e4"
down_revision: str | None = "fc3d5e7f9a1b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


CLEANUP_JOURNAL = "workspace_cleanup_journal"


def _existing_columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if CLEANUP_JOURNAL not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(CLEANUP_JOURNAL)}


def upgrade() -> None:
    present = _existing_columns()
    if not present:
        # No table at all means `f8b0d3e5a7c9` will create it complete.
        return

    if "redrive_count" not in present:
        op.add_column(
            CLEANUP_JOURNAL,
            sa.Column(
                "redrive_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )
    if "last_redriven_at" not in present:
        op.add_column(
            CLEANUP_JOURNAL,
            sa.Column("last_redriven_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "last_redriven_by_snapshot" not in present:
        op.add_column(
            CLEANUP_JOURNAL,
            sa.Column("last_redriven_by_snapshot", sa.Uuid(), nullable=True),
        )


def downgrade() -> None:
    # Only drop what this revision could have added, and only where the table
    # exists: a database that already had the columns before this revision ran
    # would otherwise lose them on the way down.
    present = _existing_columns()
    if not present:
        return
    for column in (
        "last_redriven_by_snapshot",
        "last_redriven_at",
        "redrive_count",
    ):
        if column in present:
            op.drop_column(CLEANUP_JOURNAL, column)
