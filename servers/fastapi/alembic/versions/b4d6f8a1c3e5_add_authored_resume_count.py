"""add authored resume count

Revision ID: b4d6f8a1c3e5
Revises: a3c5e7f9b1d2
Create Date: 2026-08-01
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "b4d6f8a1c3e5"
down_revision: str | None = "a3c5e7f9b1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


JOB_TABLE = "presentation_generation_jobs"


def _has_table(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    # Same legacy-fixture guard as a3c5e7f9b1d2: repair-stamped databases may
    # lack the durable job table; runtime schema validation still rejects them.
    if not _has_table(JOB_TABLE):
        return

    op.add_column(
        JOB_TABLE,
        sa.Column(
            "resume_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    if not _has_table(JOB_TABLE):
        return

    op.drop_column(JOB_TABLE, "resume_count")
