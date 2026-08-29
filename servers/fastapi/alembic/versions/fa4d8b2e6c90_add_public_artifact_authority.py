"""add public content-addressed artifact authority and export single-flight

Revision ID: fa4d8b2e6c90
Revises: f9c3a7d1e5b8
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "fa4d8b2e6c90"
down_revision: str | None = "f9c3a7d1e5b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "public_artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "schema_version",
            sa.String(length=32),
            server_default="artifact-ref/v1",
            nullable=False,
        ),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(length=160), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("created_by_operation_id", sa.Uuid(), nullable=False),
        sa.Column("retention_class", sa.String(length=24), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "schema_version = 'artifact-ref/v1'",
            name="ck_public_artifact_schema_version",
        ),
        sa.CheckConstraint(
            "kind IN ('checkpoint', 'slide-html', 'slide-png', 'export-bundle', "
            "'presentation-pptx', 'presentation-pdf')",
            name="ck_public_artifact_kind",
        ),
        sa.CheckConstraint(
            "retention_class IN ('checkpoint', 'ephemeral', 'published')",
            name="ck_public_artifact_retention",
        ),
        sa.CheckConstraint("size_bytes >= 0", name="ck_public_artifact_size"),
        sa.CheckConstraint("length(sha256) = 64", name="ck_public_artifact_digest_length"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id",
            "tenant_id",
            "kind",
            "sha256",
            "created_by_operation_id",
            "retention_class",
            name="uq_public_artifact_operation_digest",
        ),
    )
    op.create_index(
        "ix_public_artifact_owner_created",
        "public_artifacts",
        ["owner_id", "tenant_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_public_artifact_digest",
        "public_artifacts",
        ["sha256"],
        unique=False,
    )
    with op.batch_alter_table("workspace_artifacts") as batch_op:
        batch_op.add_column(
            sa.Column("public_artifact_id", sa.Uuid(), nullable=True),
        )
        batch_op.create_foreign_key(
            "fk_workspace_artifacts_public_artifact_id",
            "public_artifacts",
            ["public_artifact_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index(
            "ix_workspace_artifacts_public_artifact_id",
            ["public_artifact_id"],
            unique=False,
        )

    op.create_table(
        "public_export_identities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("presentation_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("canonical_digest", sa.String(length=64), nullable=False),
        sa.Column("export_mode", sa.String(length=8), nullable=False),
        sa.Column("font_manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("exporter_version", sa.String(length=128), nullable=False),
        sa.Column("source_artifact_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=16), server_default="running", nullable=False),
        sa.Column("claimed_by_operation_id", sa.Uuid(), nullable=False),
        sa.Column("worker_id", sa.String(length=160), nullable=False),
        sa.Column("attempt_token_hash", sa.String(length=71), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("revision >= 0", name="ck_public_export_identity_revision"),
        sa.CheckConstraint(
            "export_mode IN ('pptx', 'pdf')",
            name="ck_public_export_identity_mode",
        ),
        sa.CheckConstraint(
            "state IN ('running', 'succeeded', 'failed')",
            name="ck_public_export_identity_state",
        ),
        sa.CheckConstraint(
            "attempt_count >= 1 AND fencing_token >= 1",
            name="ck_public_export_identity_counters",
        ),
        sa.CheckConstraint(
            "length(canonical_digest) = 64 AND length(font_manifest_digest) = 64",
            name="ck_public_export_identity_digests",
        ),
        sa.ForeignKeyConstraint(
            ["source_artifact_id"],
            ["public_artifacts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["result_artifact_id"],
            ["public_artifacts.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_id",
            "presentation_id",
            "revision",
            "canonical_digest",
            "export_mode",
            "font_manifest_digest",
            "exporter_version",
            name="uq_public_export_identity",
        ),
    )
    op.create_index(
        "ix_public_export_identity_claim",
        "public_export_identities",
        ["state", "lease_expires_at", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_public_export_identity_owner",
        "public_export_identities",
        ["owner_id", "tenant_id", "presentation_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_public_export_identity_owner",
        table_name="public_export_identities",
    )
    op.drop_index(
        "ix_public_export_identity_claim",
        table_name="public_export_identities",
    )
    op.drop_table("public_export_identities")
    with op.batch_alter_table("workspace_artifacts") as batch_op:
        batch_op.drop_index("ix_workspace_artifacts_public_artifact_id")
        batch_op.drop_constraint(
            "fk_workspace_artifacts_public_artifact_id",
            type_="foreignkey",
        )
        batch_op.drop_column("public_artifact_id")
    op.drop_index("ix_public_artifact_digest", table_name="public_artifacts")
    op.drop_index("ix_public_artifact_owner_created", table_name="public_artifacts")
    op.drop_table("public_artifacts")
