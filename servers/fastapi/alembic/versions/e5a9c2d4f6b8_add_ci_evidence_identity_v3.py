"""add replay-safe CI evidence identity v3

Revision ID: e5a9c2d4f6b8
Revises: e4f8a1c3d5b7
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "e5a9c2d4f6b8"
down_revision: str | None = "e4f8a1c3d5b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "authored_ci_evidence_identities_v3",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_bundle_id", sa.Uuid(), nullable=False),
        sa.Column("receipt_digest", sa.String(length=71), nullable=False),
        sa.Column("source_head_commit", sa.String(length=64), nullable=False),
        sa.Column("pipeline_uuid", sa.String(length=128), nullable=False),
        sa.Column("attempt_subject_digest", sa.String(length=71), nullable=False),
        sa.Column("artifact_digest", sa.String(length=71), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "receipt_digest", name="uq_authored_ci_evidence_v3_receipt"
        ),
        sa.UniqueConstraint(
            "tenant_id", "source_head_commit", name="uq_authored_ci_evidence_v3_head"
        ),
        sa.UniqueConstraint(
            "tenant_id", "pipeline_uuid", name="uq_authored_ci_evidence_v3_pipeline"
        ),
        sa.UniqueConstraint(
            "tenant_id", "attempt_subject_digest", name="uq_authored_ci_evidence_v3_subject"
        ),
        sa.UniqueConstraint("evidence_bundle_id", name="uq_authored_ci_evidence_v3_bundle"),
    )


def downgrade() -> None:
    op.drop_table("authored_ci_evidence_identities_v3")
