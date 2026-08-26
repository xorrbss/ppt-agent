"""Add immutable source-content manifests, bindings, and coverage receipts.

Revision ID: e7c1a4f6b8d2
Revises: e6b0d3f5a7c9
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e7c1a4f6b8d2"
down_revision: str | None = "e6b0d3f5a7c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_content_manifests",
        sa.Column("manifest_id", sa.String(length=68), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("owner_scope", sa.String(length=128), nullable=False),
        sa.Column("reference_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("contract_version", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("source_digest", sa.String(length=71), nullable=False),
        sa.Column("manifest_digest", sa.String(length=71), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("inventory_json", sa.JSON(), nullable=False),
        sa.Column("counts_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "contract_version = 'source-content-manifest/v1'",
            name="ck_source_content_manifest_contract",
        ),
        sa.CheckConstraint(
            "source_type = 'pptx'",
            name="ck_source_content_manifest_type",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["reference_snapshot_id"],
            ["reference_snapshots.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("manifest_id"),
    )
    op.create_index(
        "ix_source_content_manifest_owner_snapshot",
        "source_content_manifests",
        ["owner_scope", "reference_snapshot_id", "created_at"],
    )

    op.create_table(
        "source_content_manifest_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("manifest_id", sa.String(length=68), nullable=False),
        sa.Column("asset_id", sa.String(length=80), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("content_digest", sa.String(length=71), nullable=False),
        sa.Column("payload", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["manifest_id"],
            ["source_content_manifests.manifest_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "manifest_id",
            "asset_id",
            name="uq_source_content_manifest_asset",
        ),
    )
    op.create_index(
        "ix_source_content_manifest_asset_digest",
        "source_content_manifest_assets",
        ["manifest_id", "content_digest"],
    )

    op.create_table(
        "source_content_generation_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("owner_scope", sa.String(length=128), nullable=False),
        sa.Column("manifest_id", sa.String(length=68), nullable=False),
        sa.Column("generation_job_id", sa.Uuid(), nullable=True),
        sa.Column("presentation_id", sa.Uuid(), nullable=True),
        sa.Column("manifest_digest", sa.String(length=71), nullable=False),
        sa.Column("composition_pin", sa.JSON(), nullable=False),
        sa.Column("content_policy_digest", sa.String(length=71), nullable=False),
        sa.Column("generation_identity_digest", sa.String(length=71), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("attempt_token", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "state IN ('admitted', 'claimed', 'cancelled', 'failed', 'published')",
            name="ck_source_content_generation_binding_state",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["manifest_id"],
            ["source_content_manifests.manifest_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["generation_job_id"],
            ["presentation_generation_jobs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["presentation_id"],
            ["presentations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("generation_job_id"),
        sa.UniqueConstraint(
            "owner_scope",
            "generation_identity_digest",
            name="uq_source_content_generation_identity",
        ),
    )
    op.create_index(
        "ix_source_content_generation_binding_manifest",
        "source_content_generation_bindings",
        ["owner_scope", "manifest_id", "created_at"],
    )

    op.create_table(
        "source_content_coverage_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("owner_scope", sa.String(length=128), nullable=False),
        sa.Column("contract_version", sa.String(length=64), nullable=False),
        sa.Column("presentation_id", sa.Uuid(), nullable=False),
        sa.Column("presentation_revision", sa.BigInteger(), nullable=False),
        sa.Column("manifest_digest", sa.String(length=71), nullable=False),
        sa.Column("plan_digest", sa.String(length=71), nullable=False),
        sa.Column("scene_digest", sa.String(length=71), nullable=False),
        sa.Column("candidate_artifact_digest", sa.String(length=71), nullable=False),
        sa.Column("export_readback_digest", sa.String(length=71), nullable=False),
        sa.Column("receipt_json", sa.JSON(), nullable=False),
        sa.Column("edit_state", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "contract_version = 'source-content-coverage-receipt/v1'",
            name="ck_source_content_coverage_contract",
        ),
        sa.CheckConstraint(
            "edit_state IN ('pristine', 'stale')",
            name="ck_source_content_coverage_edit_state",
        ),
        sa.CheckConstraint(
            "status IN ('passed', 'failed')",
            name="ck_source_content_coverage_status",
        ),
        sa.CheckConstraint(
            "presentation_revision >= 0",
            name="ck_source_content_coverage_revision",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["user.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["presentation_id"], ["presentations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_scope",
            "presentation_id",
            "presentation_revision",
            "candidate_artifact_digest",
            name="uq_source_content_coverage_artifact_revision",
        ),
    )
    op.create_index(
        "ix_source_content_coverage_publication",
        "source_content_coverage_receipts",
        [
            "owner_scope",
            "presentation_id",
            "presentation_revision",
            "edit_state",
            "status",
        ],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_source_content_coverage_publication",
        table_name="source_content_coverage_receipts",
    )
    op.drop_table("source_content_coverage_receipts")
    op.drop_index(
        "ix_source_content_generation_binding_manifest",
        table_name="source_content_generation_bindings",
    )
    op.drop_table("source_content_generation_bindings")
    op.drop_index(
        "ix_source_content_manifest_asset_digest",
        table_name="source_content_manifest_assets",
    )
    op.drop_table("source_content_manifest_assets")
    op.drop_index(
        "ix_source_content_manifest_owner_snapshot",
        table_name="source_content_manifests",
    )
    op.drop_table("source_content_manifests")
