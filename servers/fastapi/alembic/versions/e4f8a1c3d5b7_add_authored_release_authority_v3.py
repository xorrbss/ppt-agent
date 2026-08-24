"""add fail-closed bounded Hybrid release authority v3

Revision ID: e4f8a1c3d5b7
Revises: b7e9c1d3f5a8
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision: str = "e4f8a1c3d5b7"
down_revision: str | None = "b7e9c1d3f5a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("authored_capability_receipts_v2") as batch:
        batch.add_column(
            sa.Column(
                "authority_domain",
                sa.String(length=32),
                nullable=False,
                server_default="structural_test",
            )
        )
        batch.add_column(
            sa.Column(
                "evidence_class",
                sa.String(length=64),
                nullable=False,
                server_default="synthetic_structural_only",
            )
        )
        batch.add_column(sa.Column("issuer_id", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("signer_key_id", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("environment_id", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("rotation_epoch", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("nonce", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("signature_algorithm", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("export_attempt_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("binding_digest", sa.String(length=71), nullable=True))
        batch.add_column(sa.Column("signature_base64", sa.String(length=256), nullable=True))
        batch.add_column(sa.Column("authority_digest", sa.String(length=71), nullable=True))
        batch.add_column(sa.Column("authority_payload", sa.JSON(), nullable=True))
        batch.create_check_constraint(
            "ck_authored_capability_receipt_v2_authority_domain",
            "authority_domain IN ('structural_test', 'actual_product')",
        )
        batch.create_unique_constraint(
            "uq_authored_capability_receipt_v2_signer_nonce",
            ["issuer_id", "signer_key_id", "nonce"],
        )
    op.create_table(
        "authored_release_artifacts_v3",
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("export_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_id", sa.String(length=256), nullable=False),
        sa.Column("sha256", sa.String(length=71), nullable=False),
        sa.Column("byte_length", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(length=128), nullable=False),
        sa.Column("encryption_key_ref", sa.String(length=256), nullable=False),
        sa.Column("storage_generation", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("content_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("byte_length > 0", name="ck_authored_release_artifact_v3_length"),
        sa.CheckConstraint("storage_generation > 0", name="ck_authored_release_artifact_v3_generation"),
        sa.CheckConstraint("state IN ('durable', 'bound')", name="ck_authored_release_artifact_v3_state"),
        sa.PrimaryKeyConstraint("version_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "export_attempt_id",
            "artifact_id",
            name="uq_authored_release_artifact_v3_identity",
        ),
    )
    op.create_table(
        "authored_release_nonces_v3",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("authority_domain", sa.String(length=32), nullable=False),
        sa.Column("evidence_class", sa.String(length=64), nullable=False),
        sa.Column("issuer_id", sa.String(length=128), nullable=False),
        sa.Column("signer_key_id", sa.String(length=128), nullable=False),
        sa.Column("nonce", sa.String(length=128), nullable=False),
        sa.Column("subject_digest", sa.String(length=71), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "issuer_id",
            "signer_key_id",
            "nonce",
            name="uq_authored_release_nonce_v3_identity",
        ),
    )
    op.create_table(
        "authored_com_qualification_jobs_v3",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("export_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_policy_digest", sa.String(length=71), nullable=False),
        sa.Column("authority_domain", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("transition_sequence", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_token", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("failure_code", sa.String(length=128), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "authority_domain = 'actual_product'", name="ck_authored_com_qualification_v3_actual_product"
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 2", name="ck_authored_com_qualification_v3_attempts"
        ),
        sa.CheckConstraint("transition_sequence >= 0", name="ck_authored_com_qualification_v3_sequence"),
        sa.CheckConstraint(
            "state IN ('captured','planned','assembled','inputOoxmlVerified',"
            "'firstOpenObserved','savedCopyVerified','reopenObserved',"
            "'comVerified','sealed','rejected')",
            name="ck_authored_com_qualification_v3_state",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "export_attempt_id",
            "evidence_policy_digest",
            "authority_domain",
            name="uq_authored_com_qualification_v3_subject",
        ),
    )
    op.create_table(
        "authored_evidence_graph_roots_v3",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("presentation_id", sa.Uuid(), nullable=False),
        sa.Column("presentation_revision", sa.Integer(), nullable=False),
        sa.Column("export_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("authority_domain", sa.String(length=32), nullable=False),
        sa.Column("evidence_policy_digest", sa.String(length=71), nullable=False),
        sa.Column("active_manifest_digest", sa.String(length=71), nullable=False),
        sa.Column("source_tree_census_digest", sa.String(length=71), nullable=False),
        sa.Column("source_head_commit", sa.String(length=64), nullable=False),
        sa.Column("release_profile_digest", sa.String(length=71), nullable=False),
        sa.Column("feature_set_digest", sa.String(length=71), nullable=False),
        sa.Column("gate_minus1_manifest_digest", sa.String(length=71), nullable=False),
        sa.Column("gate_minus1_result_set_digest", sa.String(length=71), nullable=False),
        sa.Column("gate_minus1_receipt_artifact_digest", sa.String(length=71), nullable=False),
        sa.Column("gate_minus1_validator_contract_digest", sa.String(length=71), nullable=False),
        sa.Column("environment_attestation_digest", sa.String(length=71), nullable=False),
        sa.Column("worker_attestation_digest", sa.String(length=71), nullable=False),
        sa.Column("com_qualification_job_id", sa.Uuid(), nullable=False),
        sa.Column("com_qualification_result_digest", sa.String(length=71), nullable=False),
        sa.Column("base_artifact_ids", sa.JSON(), nullable=False),
        sa.Column("base_artifact_ids_digest", sa.String(length=71), nullable=False),
        sa.Column("final_required_artifact_ids_digest", sa.String(length=71), nullable=False),
        sa.Column("artifact_inventory", sa.JSON(), nullable=False),
        sa.Column("artifact_inventory_digest", sa.String(length=71), nullable=False),
        sa.Column("root_digest", sa.String(length=71), nullable=False),
        sa.Column("request_digest", sa.String(length=71), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("authority_domain = 'actual_product'", name="ck_authored_evidence_graph_root_v3_actual"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "export_attempt_id",
            "evidence_policy_digest",
            "authority_domain",
            name="uq_authored_evidence_graph_root_v3_subject",
        ),
        sa.UniqueConstraint("tenant_id", "root_digest", name="uq_authored_evidence_graph_root_v3_digest"),
    )
    op.create_table(
        "authored_evidence_bundle_completions_v3",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("graph_root_id", sa.Uuid(), nullable=False),
        sa.Column("authority_domain", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("cas_version", sa.Integer(), nullable=False),
        sa.Column("required_capability_ids", sa.JSON(), nullable=False),
        sa.Column("receipt_digests", sa.JSON(), nullable=False),
        sa.Column("completion_digest", sa.String(length=71), nullable=False),
        sa.Column("create_request_digest", sa.String(length=71), nullable=False),
        sa.Column("seal_request_digest", sa.String(length=71), nullable=True),
        sa.Column("receipt_identities", sa.JSON(), nullable=False),
        sa.Column("authority_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("state IN ('pending','sealed')", name="ck_authored_evidence_completion_v3_state"),
        sa.CheckConstraint("authority_domain = 'actual_product'", name="ck_authored_evidence_completion_v3_actual"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("graph_root_id", name="uq_authored_evidence_completion_v3_root"),
        sa.UniqueConstraint("tenant_id", "completion_digest", name="uq_authored_evidence_completion_v3_digest"),
    )
    op.create_table(
        "authored_gate0_structural_traces_v3",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.String(length=128), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("authority_domain", sa.String(length=32), nullable=False),
        sa.Column("request_digest", sa.String(length=71), nullable=False),
        sa.Column("trace_digest", sa.String(length=71), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("sealed_completion_count", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("authority_domain = 'structural_test'", name="ck_authored_gate0_structural_trace_v3_domain"),
        sa.CheckConstraint("sealed_completion_count = 0", name="ck_authored_gate0_structural_trace_v3_no_completion"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "case_id", name="uq_authored_gate0_structural_trace_v3_case"),
    )
    op.create_table(
        "authored_gate0_structural_subjects_v3",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=False),
        sa.Column("authority_domain", sa.String(length=32), nullable=False),
        sa.Column("presentation_id", sa.Uuid(), nullable=False),
        sa.Column("baseline_subject_digest", sa.String(length=71), nullable=False),
        sa.Column("mutated_subject_digest", sa.String(length=71), nullable=False),
        sa.Column("subject_artifact_id", sa.String(length=256), nullable=False),
        sa.Column("subject_artifact_version_id", sa.Uuid(), nullable=False),
        sa.Column("structural_receipt_digest", sa.String(length=71), nullable=True),
        sa.Column("projection_digest", sa.String(length=71), nullable=True),
        sa.Column("baseline_payload", sa.JSON(), nullable=False),
        sa.Column("mutated_payload", sa.JSON(), nullable=False),
        sa.Column("post_state", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "authority_domain = 'structural_test'", name="ck_authored_gate0_structural_subject_v3_domain"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "case_id", name="uq_authored_gate0_structural_subject_v3_case"),
    )
    op.create_table(
        "authored_gate0_structural_events_v3",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("authority_domain", sa.String(length=32), nullable=False),
        sa.Column("seam", sa.String(length=64), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("input_digest", sa.String(length=71), nullable=False),
        sa.Column("output_digest", sa.String(length=71), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("authority_domain = 'structural_test'", name="ck_authored_gate0_structural_event_v3_domain"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("subject_id", "seam", name="uq_authored_gate0_structural_event_v3_seam"),
    )
    op.create_table(
        "authored_gate0_structural_completions_v3",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("authority_domain", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("cas_version", sa.Integer(), nullable=False),
        sa.Column("receipt_digests", sa.JSON(), nullable=False),
        sa.Column("completion_digest", sa.String(length=71), nullable=False),
        sa.Column("authority_payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("sealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "authority_domain = 'structural_test'", name="ck_authored_gate0_structural_completion_v3_domain"
        ),
        sa.CheckConstraint("state IN ('pending','sealed')", name="ck_authored_gate0_structural_completion_v3_state"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("subject_id", name="uq_authored_gate0_structural_completion_v3_subject"),
    )


def downgrade() -> None:
    op.drop_table("authored_gate0_structural_completions_v3")
    op.drop_table("authored_gate0_structural_events_v3")
    op.drop_table("authored_gate0_structural_subjects_v3")
    op.drop_table("authored_gate0_structural_traces_v3")
    op.drop_table("authored_evidence_bundle_completions_v3")
    op.drop_table("authored_evidence_graph_roots_v3")
    op.drop_table("authored_com_qualification_jobs_v3")
    op.drop_table("authored_release_nonces_v3")
    op.drop_table("authored_release_artifacts_v3")
    with op.batch_alter_table("authored_capability_receipts_v2") as batch:
        batch.drop_constraint("uq_authored_capability_receipt_v2_signer_nonce", type_="unique")
        batch.drop_constraint("ck_authored_capability_receipt_v2_authority_domain", type_="check")
        for column in (
            "signature_base64",
            "signature_algorithm",
            "binding_digest",
            "export_attempt_id",
            "authority_payload",
            "authority_digest",
            "nonce",
            "rotation_epoch",
            "environment_id",
            "signer_key_id",
            "issuer_id",
            "evidence_class",
            "authority_domain",
        ):
            batch.drop_column(column)
