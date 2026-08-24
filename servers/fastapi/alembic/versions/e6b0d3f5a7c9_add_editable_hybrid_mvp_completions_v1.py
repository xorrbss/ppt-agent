"""add isolated append-only editable Hybrid MVP completions v1

Revision ID: e6b0d3f5a7c9
Revises: e5a9c2d4f6b8
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "e6b0d3f5a7c9"
down_revision: str | None = "e5a9c2d4f6b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "authored_editable_hybrid_mvp_completions_v1",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("owner_scope", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("deck_id", sa.String(length=256), nullable=False),
        sa.Column("deck_revision", sa.BigInteger(), nullable=False),
        sa.Column("deck_digest", sa.String(length=71), nullable=False),
        sa.Column("scene_digest", sa.String(length=71), nullable=False),
        sa.Column("authority_domain", sa.String(length=32), nullable=False),
        sa.Column("profile_id", sa.String(length=128), nullable=False),
        sa.Column("profile_digest", sa.String(length=71), nullable=False),
        sa.Column("gate_manifest_digest", sa.String(length=71), nullable=False),
        sa.Column("generation_attempt_id", sa.String(length=256), nullable=False),
        sa.Column("hybrid_export_attempt_id", sa.String(length=256), nullable=False),
        sa.Column("artifact_inventory_digest", sa.String(length=71), nullable=False),
        sa.Column("artifact_count", sa.Integer(), nullable=False),
        sa.Column("skipped_count", sa.Integer(), nullable=False),
        sa.Column("result_core_digest", sa.String(length=71), nullable=False),
        sa.Column("result_set_digest", sa.String(length=71), nullable=False),
        sa.Column("sealed_result_sha256", sa.String(length=71), nullable=False),
        sa.Column("sealed_result_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("validation_receipt_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("verifier_trust_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("verifier_trust_sha256", sa.String(length=71), nullable=False),
        sa.Column("receipt_digest", sa.String(length=71), nullable=False),
        sa.Column("issuer_id", sa.String(length=128), nullable=False),
        sa.Column("signer_key_id", sa.String(length=128), nullable=False),
        sa.Column("receipt_nonce", sa.String(length=256), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cas_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "owner_scope",
            "tenant_id",
            "deck_id",
            "deck_revision",
            name="uq_authored_editable_mvp_completion_v1_subject",
        ),
        sa.UniqueConstraint(
            "issuer_id",
            "signer_key_id",
            "receipt_nonce",
            name="uq_authored_editable_mvp_completion_v1_nonce",
        ),
        sa.UniqueConstraint(
            "owner_scope",
            "tenant_id",
            "sealed_result_sha256",
            name="uq_authored_editable_mvp_completion_v1_bytes",
        ),
        sa.CheckConstraint("cas_version = 1", name="ck_authored_editable_mvp_completion_v1_cas"),
        sa.CheckConstraint("artifact_count = 35", name="ck_authored_editable_mvp_completion_v1_artifacts"),
        sa.CheckConstraint("skipped_count = 0", name="ck_authored_editable_mvp_completion_v1_no_skip"),
        sa.CheckConstraint("authority_domain = 'actual_product'", name="ck_authored_editable_mvp_completion_v1_domain"),
        sa.CheckConstraint(
            "profile_id = 'authored-editable-hybrid-ko-en-basic-v1'",
            name="ck_authored_editable_mvp_completion_v1_profile",
        ),
    )
    op.create_index(
        "ix_authored_editable_mvp_completion_v1_current",
        "authored_editable_hybrid_mvp_completions_v1",
        ["owner_scope", "tenant_id", "deck_id", "deck_revision", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_authored_editable_mvp_completion_v1_current",
        table_name="authored_editable_hybrid_mvp_completions_v1",
    )
    op.drop_table("authored_editable_hybrid_mvp_completions_v1")

