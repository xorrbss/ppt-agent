"""Add prompt-cache token counters to public provider logical calls.

Revision ID: c5e8a1d3b7f9
Revises: e4b7c9d2a1f0
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c5e8a1d3b7f9"
down_revision: str | None = "e4b7c9d2a1f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CALLS = "public_provider_logical_calls"
COLUMNS = ("cached_input_tokens", "cache_write_input_tokens")


def upgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(CALLS)}
    for name in COLUMNS:
        if name not in existing:
            op.add_column(CALLS, sa.Column(name, sa.BigInteger(), nullable=True))


def downgrade() -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(CALLS)}
    for name in reversed(COLUMNS):
        if name in existing:
            with op.batch_alter_table(CALLS) as batch_op:
                batch_op.drop_column(name)
