"""add webhook subscription terminal-rejection visibility columns

Revision ID: c1a3e5f7b9d0
Revises: b7c1d4e9f2a8
Create Date: 2026-09-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'c1a3e5f7b9d0'
down_revision: Union[str, None] = 'b7c1d4e9f2a8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    if not _has_table(table_name):
        return False
    inspector = sa.inspect(op.get_bind())
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    # A sparse legacy database may not carry this table at all. "No column" then
    # means "nothing to alter", not "add one to a table that is not there".
    if not _has_table('webhook_subscriptions'):
        return
    if not _has_column('webhook_subscriptions', 'last_delivery_rejected_code'):
        op.add_column(
            'webhook_subscriptions',
            sa.Column(
                'last_delivery_rejected_code',
                sqlmodel.sql.sqltypes.AutoString(),
                nullable=True,
            ),
        )
    if not _has_column('webhook_subscriptions', 'last_delivery_rejected_at'):
        op.add_column(
            'webhook_subscriptions',
            sa.Column('last_delivery_rejected_at', sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    if not _has_table('webhook_subscriptions'):
        return
    if _has_column('webhook_subscriptions', 'last_delivery_rejected_at'):
        op.drop_column('webhook_subscriptions', 'last_delivery_rejected_at')
    if _has_column('webhook_subscriptions', 'last_delivery_rejected_code'):
        op.drop_column('webhook_subscriptions', 'last_delivery_rejected_code')
