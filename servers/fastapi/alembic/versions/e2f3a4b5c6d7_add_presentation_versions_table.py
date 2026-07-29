"""add presentation_versions table

Revision ID: e2f3a4b5c6d7
Revises: d1f2a3b4c5e6
Create Date: 2026-07-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'e2f3a4b5c6d7'
down_revision: Union[str, None] = 'd1f2a3b4c5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def _has_index(table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return False
    return index_name in {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    if not _has_table('presentation_versions'):
        op.create_table(
            'presentation_versions',
            sa.Column('id', sa.Uuid(), nullable=False),
            sa.Column('presentation_id', sa.Uuid(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('label', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
            sa.Column('slides', sa.JSON(), nullable=True),
            sa.ForeignKeyConstraint(
                ['presentation_id'], ['presentations.id'], ondelete='CASCADE'
            ),
            sa.PrimaryKeyConstraint('id'),
        )
    if not _has_index(
        'presentation_versions', op.f('ix_presentation_versions_presentation_id')
    ):
        op.create_index(
            op.f('ix_presentation_versions_presentation_id'),
            'presentation_versions',
            ['presentation_id'],
            unique=False,
        )


def downgrade() -> None:
    if _has_index(
        'presentation_versions', op.f('ix_presentation_versions_presentation_id')
    ):
        op.drop_index(
            op.f('ix_presentation_versions_presentation_id'),
            table_name='presentation_versions',
        )
    if _has_table('presentation_versions'):
        op.drop_table('presentation_versions')
