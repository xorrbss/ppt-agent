"""Add presentation-list indexes and share expiry/view metrics.

Revision ID: c4e8f0a2b6d9
Revises: b3d7f1a9c2e4

The migration is intentionally additive and marker-aware.  Some local installs
create ORM metadata before Alembic is enabled; those databases can already have
one or more of these columns/indexes while still being stamped at the previous
revision.  Upgrade therefore adds only the missing pieces on both SQLite and
PostgreSQL.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c4e8f0a2b6d9"
down_revision: str | None = "b3d7f1a9c2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PRESENTATION_LIST_INDEX = "ix_presentations_owner_lifecycle_updated_id"
SLIDE_COVER_INDEX = "ix_slides_presentation_index"


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _column_names(table: str) -> set[str]:
    return {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)
    }


def _index_names(table: str) -> set[str]:
    return {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes(table)
        if index.get("name")
    }


def upgrade() -> None:
    tables = _table_names()
    if "presentations" in tables:
        columns = _column_names("presentations")
        if "expires_at" not in columns:
            op.add_column(
                "presentations",
                sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            )
        if "view_count" not in columns:
            op.add_column(
                "presentations",
                sa.Column(
                    "view_count",
                    sa.BigInteger(),
                    nullable=False,
                    server_default=sa.text("0"),
                ),
            )
        if (
            {"owner_id", "lifecycle_status", "updated_at", "id"}.issubset(
                _column_names("presentations")
            )
            and PRESENTATION_LIST_INDEX not in _index_names("presentations")
        ):
            op.create_index(
                PRESENTATION_LIST_INDEX,
                "presentations",
                ["owner_id", "lifecycle_status", "updated_at", "id"],
            )

    if (
        "slides" in tables
        and {"presentation", "index"}.issubset(_column_names("slides"))
        and SLIDE_COVER_INDEX not in _index_names("slides")
    ):
        op.create_index(
            SLIDE_COVER_INDEX,
            "slides",
            ["presentation", "index"],
        )


def downgrade() -> None:
    tables = _table_names()
    if "slides" in tables and SLIDE_COVER_INDEX in _index_names("slides"):
        op.drop_index(SLIDE_COVER_INDEX, table_name="slides")

    if "presentations" not in tables:
        return
    if PRESENTATION_LIST_INDEX in _index_names("presentations"):
        op.drop_index(PRESENTATION_LIST_INDEX, table_name="presentations")
    columns = _column_names("presentations")
    with op.batch_alter_table("presentations") as batch:
        if "view_count" in columns:
            batch.drop_column("view_count")
        if "expires_at" in columns:
            batch.drop_column("expires_at")
