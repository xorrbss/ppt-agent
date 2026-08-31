"""Keep local storage unique while public journals share immutable CAS bytes.

Revision ID: a1e5f8c2d7b0
Revises: a0d4f7c9e2b6
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "a1e5f8c2d7b0"
down_revision: str | None = "a0d4f7c9e2b6"
branch_labels = None
depends_on = None

TABLE = "workspace_artifacts"
OLD_CONSTRAINT = "uq_workspace_artifacts_storage_key"
LOCAL_INDEX = "uq_workspace_artifacts_local_storage_key"


def upgrade() -> None:
    with op.batch_alter_table(TABLE) as batch_op:
        batch_op.drop_constraint(OLD_CONSTRAINT, type_="unique")
        batch_op.create_index(
            LOCAL_INDEX,
            ["storage_key"],
            unique=True,
            postgresql_where=sa.text("public_artifact_id IS NULL"),
            sqlite_where=sa.text("public_artifact_id IS NULL"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Serialize the duplicate check and constraint restoration. Never
        # discard an immutable public journal to make a downgrade fit.
        op.execute(sa.text(f"LOCK TABLE {TABLE} IN ACCESS EXCLUSIVE MODE"))
    duplicate = bind.execute(
        sa.text(
            f"SELECT storage_key FROM {TABLE} "
            "GROUP BY storage_key HAVING COUNT(*) > 1 LIMIT 1"
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError("workspace_artifact_shared_keys_prevent_downgrade")
    with op.batch_alter_table(TABLE) as batch_op:
        batch_op.drop_index(LOCAL_INDEX)
        batch_op.create_unique_constraint(OLD_CONSTRAINT, ["storage_key"])
