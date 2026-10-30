"""Add a gated, owner-indexed generation-attempt parent projection.

Historical owner-scoped backfill and global scope certification are separate,
explicitly authorized maintenance steps. This revision starts incomplete.
"""

from alembic import op
import sqlalchemy as sa


revision = "e4b7c9d2a1f0"
down_revision = "d8f0a2b4c6e9"
branch_labels = None
depends_on = None


def upgrade():
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
    op.create_table(
        "workspace_generation_lineage_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("id = 1", name="ck_workspace_generation_lineage_state_singleton"),
    )
    op.execute(
        sa.text(
            "INSERT INTO workspace_generation_lineage_state (id, complete) VALUES (1, false)"
        )
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            sa.text(
                "GRANT SELECT ON TABLE workspace_generation_lineage_state TO presenton_public_api"
            )
        )


def downgrade():
    op.drop_table("workspace_generation_lineage_state")
    op.drop_index(
        "ix_workspace_operations_generation_parent_owner",
        table_name="workspace_operations",
    )
    op.drop_column("workspace_operations", "previous_presentation_id")
