"""Narrow the COM qualification lease token to its verifier width.

Revision ID: a4f9c2e7b1d3
Revises: f2b6d8a0c4e7
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "a4f9c2e7b1d3"
down_revision: str | None = "f2b6d8a0c4e7"
branch_labels = None
depends_on = None

TABLE = "authored_com_qualification_jobs_v3"
COLUMN = "lease_token"
# "sha256:" plus a 64-character hex digest.
VERIFIER_WIDTH = 71
BEARER_WIDTH = 128


def upgrade() -> None:
    # e8d2b5f7a9c1 replaced every stored bearer token with a prefixed SHA-256
    # verifier and narrowed the model to match, but it rewrote only the rows.
    # The column kept the bearer width, so the declared schema and the deployed
    # one disagreed and every autogenerate comparison reported the difference.
    with op.batch_alter_table(TABLE) as batch_op:
        batch_op.alter_column(
            COLUMN,
            existing_type=sa.String(length=BEARER_WIDTH),
            type_=sa.String(length=VERIFIER_WIDTH),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table(TABLE) as batch_op:
        batch_op.alter_column(
            COLUMN,
            existing_type=sa.String(length=VERIFIER_WIDTH),
            type_=sa.String(length=BEARER_WIDTH),
            existing_nullable=True,
        )
