"""Add cached_tokens to provider_usage_receipts.

Revision ID: b7c1d4e9f2a8
Revises: b5a0d3e7c9f1
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "b7c1d4e9f2a8"
down_revision: str | None = "b5a0d3e7c9f1"
branch_labels = None
depends_on = None

TABLE = "provider_usage_receipts"
COLUMN = "cached_tokens"
CONSTRAINT = "ck_provider_usage_receipts_cached_tokens"

# SQLite batch mode reflects the table before recreating it. The default
# reflection resolves foreign keys, which walks from this table to
# ``presentation_generation_jobs`` and on to
# ``async_presentation_generation_tasks`` — a table no Alembic revision
# creates (``create_db_and_tables`` tops it up), so a database upgraded from
# the legacy baseline stamp raises ``NoSuchTableError`` mid-migration.
# Alembic stubs the referent itself when it re-emits the foreign key, so
# reflecting shallowly is both sufficient and safe.
_SHALLOW_REFLECTION = {"resolve_fks": False}


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(TABLE)}
    if COLUMN in columns:
        return
    with op.batch_alter_table(TABLE, reflect_kwargs=_SHALLOW_REFLECTION) as batch_op:
        batch_op.add_column(sa.Column(COLUMN, sa.BigInteger(), nullable=True))
        batch_op.create_check_constraint(
            CONSTRAINT, f"{COLUMN} IS NULL OR {COLUMN} >= 0"
        )


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(TABLE)}
    if COLUMN not in columns:
        return
    with op.batch_alter_table(TABLE, reflect_kwargs=_SHALLOW_REFLECTION) as batch_op:
        batch_op.drop_constraint(CONSTRAINT, type_="check")
        batch_op.drop_column(COLUMN)
