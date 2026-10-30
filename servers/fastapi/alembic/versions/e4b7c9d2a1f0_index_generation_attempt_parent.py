"""Atomically backfill indexed generation lineage under the migration owner.

The affected writers must be drained before this revision. PostgreSQL DDL,
global inventory, projection, writer gate, and marker commit in one transaction.
"""

from alembic import op
import sqlalchemy as sa

from migration_generation_lineage import (
    assert_global_migration_visibility,
    backfill_global_generation_lineage,
    grant_readiness_only,
    install_writer_gate,
)


revision = "e4b7c9d2a1f0"
down_revision = "d8f0a2b4c6e9"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    postgres = connection.dialect.name == "postgresql"
    if postgres:
        connection.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
        connection.execute(sa.text("SET LOCAL statement_timeout = '120s'"))
        connection.execute(sa.text("LOCK TABLE workspace_operations IN ACCESS EXCLUSIVE MODE"))
        assert_global_migration_visibility(connection)
    op.add_column(
        "workspace_operations",
        sa.Column("previous_presentation_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "ix_workspace_operations_generation_parent_owner",
        "workspace_operations",
        ["owner_scope", "owner_id", "previous_presentation_id", "created_at", "id"],
        postgresql_where=sa.text(
            "kind = 'presentation.generate' AND previous_presentation_id IS NOT NULL"
        ),
        sqlite_where=sa.text(
            "kind = 'presentation.generate' AND previous_presentation_id IS NOT NULL"
        ),
    )
    total, projected, scopes, anonymous = backfill_global_generation_lineage(connection)
    op.create_table(
        "workspace_generation_lineage_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("generation_rows", sa.BigInteger(), nullable=False),
        sa.Column("projected_rows", sa.BigInteger(), nullable=False),
        sa.Column("owner_scope_count", sa.BigInteger(), nullable=False),
        sa.Column("anonymous_rows", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_workspace_generation_lineage_state_singleton"),
    )
    op.execute(
        sa.text(
            "INSERT INTO workspace_generation_lineage_state "
            "(id, complete, completed_at, generation_rows, projected_rows, "
            "owner_scope_count, anonymous_rows) "
            f"VALUES (1, true, CURRENT_TIMESTAMP, {total}, {projected}, {scopes}, {anonymous})"
        )
    )
    if postgres:
        install_writer_gate(connection)
        grant_readiness_only(connection)


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text(
            "DROP TRIGGER trg_workspace_generation_lineage_writer_gate ON workspace_operations"
        ))
        op.execute(sa.text("DROP FUNCTION presenton_generation_lineage_writer_gate()"))
    op.drop_table("workspace_generation_lineage_state")
    op.drop_index(
        "ix_workspace_operations_generation_parent_owner",
        table_name="workspace_operations",
    )
    op.drop_column("workspace_operations", "previous_presentation_id")
