"""add share_token column to presentations

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-07-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'f3a4b5c6d7e8'
down_revision: Union[str, None] = 'e2f3a4b5c6d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _has_index(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return False
    return index_name in {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    if not _has_column('presentations', 'share_token'):
        op.add_column(
            'presentations',
            sa.Column('share_token', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        )
    if not _has_index('presentations', op.f('ix_presentations_share_token')):
        op.create_index(
            op.f('ix_presentations_share_token'),
            'presentations',
            ['share_token'],
            unique=True,
        )


def downgrade() -> None:
    if _has_index('presentations', op.f('ix_presentations_share_token')):
        op.drop_index(
            op.f('ix_presentations_share_token'), table_name='presentations'
        )
    if _has_column('presentations', 'share_token'):
        op.drop_column('presentations', 'share_token')
