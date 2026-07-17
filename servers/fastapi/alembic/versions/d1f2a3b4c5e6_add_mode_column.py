"""add mode column to presentations

Revision ID: d1f2a3b4c5e6
Revises: c8d1e2f3a4b5
Create Date: 2026-07-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'd1f2a3b4c5e6'
down_revision: Union[str, None] = 'c8d1e2f3a4b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column('presentations', 'mode'):
        op.add_column('presentations', sa.Column('mode', sa.String(), nullable=True))
        # Backfill existing rows from the legacy sentinels so is_authored() and the
        # editor read an explicit mode without relying on the fallback. Order
        # matters: adaptive decks carry BOTH deck_plan and layout non-null, so test
        # deck_plan first; authored decks have layout NULL; the rest are templates.
        # Guarded on the sentinel columns existing — some legacy/stamped databases
        # carry a reduced presentations table with nothing to backfill.
        if _has_column('presentations', 'layout') and _has_column(
            'presentations', 'deck_plan'
        ):
            op.execute(
                """
                UPDATE presentations
                SET mode = CASE
                    WHEN deck_plan IS NOT NULL THEN 'adaptive'
                    WHEN layout IS NULL THEN 'authored'
                    ELSE 'template'
                END
                WHERE mode IS NULL
                """
            )


def downgrade() -> None:
    if _has_column('presentations', 'mode'):
        op.drop_column('presentations', 'mode')
