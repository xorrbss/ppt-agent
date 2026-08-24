"""Add immutable Authored semantic bundle graph and active CAS pointer.

Revision ID: a2d5f8b1c4e7
Revises: a1c4e7b9d2f6
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "a2d5f8b1c4e7"
down_revision: str | None = "a1c4e7b9d2f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _id() -> sa.Column:
    return sa.Column("id", sa.Uuid(), nullable=False, primary_key=True)


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


def upgrade() -> None:
    op.create_table(
        "authored_semantic_artifacts",
        _id(),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("artifact_ref", sa.String(length=96), nullable=False),
        sa.Column("content_digest", sa.String(length=71), nullable=False),
        sa.Column("byte_length", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(length=128), nullable=False),
        sa.Column("original_locator_digest", sa.String(length=71), nullable=True),
        sa.Column("resolver_version", sa.String(length=128), nullable=True),
        sa.Column("retention_class", sa.String(length=32), nullable=False),
        sa.Column("content_bytes", sa.LargeBinary(), nullable=False),
        _created_at(),
        sa.UniqueConstraint(
            "owner_id", "tenant_id", "content_digest", name="uq_authored_semantic_artifact_digest"
        ),
        sa.CheckConstraint("byte_length >= 0", name="ck_authored_semantic_artifact_length"),
    )
    op.create_index(
        "ix_authored_semantic_artifact_scope_created",
        "authored_semantic_artifacts",
        ["owner_id", "tenant_id", "created_at"],
    )

    op.create_table(
        "authored_emission_bundles",
        _id(),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("presentation_id", sa.Uuid(), nullable=False),
        sa.Column("slide_id", sa.Uuid(), nullable=False),
        sa.Column("slide_revision", sa.BigInteger(), nullable=False),
        sa.Column("bundle_kind", sa.String(length=16), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("origin_generation_attempt_id", sa.Uuid(), nullable=True),
        sa.Column("html_digest", sa.String(length=71), nullable=False),
        sa.Column("semantic_component_digest", sa.String(length=71), nullable=False),
        sa.Column("asset_manifest_digest", sa.String(length=71), nullable=False),
        sa.Column("font_manifest_digest", sa.String(length=71), nullable=False),
        sa.Column("master_contract_digest", sa.String(length=71), nullable=False),
        sa.Column("composition_digest", sa.String(length=71), nullable=False),
        sa.Column("identity_registry_digest", sa.String(length=71), nullable=False),
        sa.Column("quality_policy_version", sa.String(length=128), nullable=False),
        sa.Column("bundle_digest", sa.String(length=71), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        _created_at(),
        sa.UniqueConstraint(
            "owner_id", "tenant_id", "bundle_digest", name="uq_authored_emission_bundle_digest"
        ),
        sa.UniqueConstraint(
            "owner_id", "tenant_id", "presentation_id", "slide_id", "slide_revision", "operation_id",
            name="uq_authored_emission_bundle_lineage",
        ),
        sa.CheckConstraint("bundle_kind IN ('authored', 'legacyHtml')", name="ck_authored_emission_bundle_kind"),
        sa.CheckConstraint("slide_revision >= 1", name="ck_authored_emission_bundle_revision"),
    )
    op.create_index(
        "ix_authored_emission_bundle_slide",
        "authored_emission_bundles",
        ["owner_id", "tenant_id", "presentation_id", "slide_id", "slide_revision"],
    )

    op.create_table(
        "authored_deck_emission_manifests",
        _id(),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("presentation_id", sa.Uuid(), nullable=False),
        sa.Column("presentation_revision", sa.BigInteger(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("generation_attempt_id", sa.Uuid(), nullable=True),
        sa.Column("canonical_digest", sa.String(length=71), nullable=False),
        sa.Column("master_contract_digest", sa.String(length=71), nullable=False),
        sa.Column("composition_digest", sa.String(length=71), nullable=False),
        sa.Column("asset_manifest_digest", sa.String(length=71), nullable=False),
        sa.Column("font_manifest_digest", sa.String(length=71), nullable=False),
        sa.Column("quality_policy_version", sa.String(length=128), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        _created_at(),
        sa.UniqueConstraint(
            "owner_id", "tenant_id", "canonical_digest", name="uq_authored_deck_manifest_digest"
        ),
        sa.UniqueConstraint(
            "owner_id", "tenant_id", "presentation_id", "presentation_revision", "operation_id",
            name="uq_authored_deck_manifest_lineage",
        ),
        sa.CheckConstraint("presentation_revision >= 1", name="ck_authored_deck_manifest_revision"),
    )
    op.create_index(
        "ix_authored_deck_manifest_presentation",
        "authored_deck_emission_manifests",
        ["owner_id", "tenant_id", "presentation_id", "presentation_revision"],
    )

    op.create_table(
        "authored_deck_emission_active",
        _id(),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("presentation_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_mode", sa.String(length=16), nullable=False),
        sa.Column(
            "manifest_id",
            sa.Uuid(),
            sa.ForeignKey("authored_deck_emission_manifests.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("presentation_revision", sa.BigInteger(), nullable=False),
        sa.Column("canonical_digest", sa.String(length=71), nullable=False),
        sa.Column("generation", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "owner_id", "tenant_id", "presentation_id", name="uq_authored_deck_active_pointer"
        ),
        sa.CheckConstraint("canonical_mode IN ('legacyHtml', 'bundleGraph')", name="ck_authored_deck_active_mode"),
        sa.CheckConstraint("presentation_revision >= 0 AND generation >= 1", name="ck_authored_deck_active_versions"),
        sa.CheckConstraint(
            "(canonical_mode = 'legacyHtml' AND manifest_id IS NULL) OR "
            "(canonical_mode = 'bundleGraph' AND manifest_id IS NOT NULL)",
            name="ck_authored_deck_active_manifest_mode",
        ),
    )

    op.create_table(
        "authored_semantic_export_bindings",
        _id(),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column(
            "export_attempt_id",
            sa.Uuid(),
            sa.ForeignKey("presentation_export_attempts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("presentation_id", sa.Uuid(), nullable=False),
        sa.Column("presentation_revision", sa.BigInteger(), nullable=False),
        sa.Column("canonical_digest", sa.String(length=71), nullable=False),
        sa.Column("renderer_source_digest", sa.String(length=71), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("serializer_id", sa.String(length=128), nullable=False),
        sa.Column("serializer_version", sa.String(length=128), nullable=False),
        sa.Column("pptx_digest", sa.String(length=71), nullable=True),
        sa.Column("binding_digest", sa.String(length=71), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        _created_at(),
        sa.UniqueConstraint("export_attempt_id", name="uq_authored_semantic_export_attempt"),
        sa.UniqueConstraint(
            "owner_id", "tenant_id", "binding_digest", name="uq_authored_semantic_export_binding"
        ),
        sa.CheckConstraint("mode IN ('fidelity', 'hybrid')", name="ck_authored_semantic_export_mode"),
        sa.CheckConstraint("presentation_revision >= 1", name="ck_authored_semantic_export_revision"),
    )
    op.create_index(
        "ix_authored_semantic_export_presentation",
        "authored_semantic_export_bindings",
        ["owner_id", "tenant_id", "presentation_id", "presentation_revision"],
    )


def downgrade() -> None:
    op.drop_index("ix_authored_semantic_export_presentation", table_name="authored_semantic_export_bindings")
    op.drop_table("authored_semantic_export_bindings")
    op.drop_table("authored_deck_emission_active")
    op.drop_index("ix_authored_deck_manifest_presentation", table_name="authored_deck_emission_manifests")
    op.drop_table("authored_deck_emission_manifests")
    op.drop_index("ix_authored_emission_bundle_slide", table_name="authored_emission_bundles")
    op.drop_table("authored_emission_bundles")
    op.drop_index("ix_authored_semantic_artifact_scope_created", table_name="authored_semantic_artifacts")
    op.drop_table("authored_semantic_artifacts")
