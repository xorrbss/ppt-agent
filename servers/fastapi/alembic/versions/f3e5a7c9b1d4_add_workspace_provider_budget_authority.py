"""Bind provider budgets to real workspace operations without generation jobs."""
from alembic import op
import sqlalchemy as sa

revision = "f3e5a7c9b1d4"
down_revision = "b8d2f4a6c9e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("provider_runtime_budgets") as batch:
        batch.alter_column("job_id", existing_type=sa.Uuid(), nullable=True)
        batch.add_column(sa.Column("workspace_operation_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key("fk_provider_runtime_budget_operation", "workspace_operations",
                                 ["workspace_operation_id"], ["id"], ondelete="CASCADE")
        batch.create_unique_constraint("uq_provider_runtime_budget_operation",
                                       ["tenant_id", "owner_id", "workspace_operation_id"])
        batch.create_check_constraint("ck_provider_runtime_budget_authority",
            "(job_id IS NOT NULL AND workspace_operation_id IS NULL) OR "
            "(job_id IS NULL AND workspace_operation_id IS NOT NULL)")


def downgrade() -> None:
    if op.get_bind().scalar(sa.text(
        "SELECT count(*) FROM provider_runtime_budgets WHERE workspace_operation_id IS NOT NULL"
    )):
        raise RuntimeError("Workspace provider budgets must be retained before downgrade")
    with op.batch_alter_table("provider_runtime_budgets") as batch:
        batch.drop_constraint("ck_provider_runtime_budget_authority", type_="check")
        batch.drop_constraint("uq_provider_runtime_budget_operation", type_="unique")
        batch.drop_constraint("fk_provider_runtime_budget_operation", type_="foreignkey")
        batch.drop_column("workspace_operation_id")
        batch.alter_column("job_id", existing_type=sa.Uuid(), nullable=False)
