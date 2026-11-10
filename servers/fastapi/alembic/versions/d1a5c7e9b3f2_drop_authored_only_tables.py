"""Drop the tables only the removed authored generation strategy used.

Authored removal P4 (docs/authored-removal-design-20261105.md).  The authored
strategy and its release/evidence ledgers, SlideSpec persistence, canonical
revisions, exact provenance and the semantic artifact/bundle/export-binding
tables have no reader or writer left in the product, so this revision drops
them.  Deliberately kept: authored_slide_checkpoints (svg checkpoint
rows), authored_slide_editor_events (the mounted editor-events endpoint
still writes it), authored_deck_emission_manifests and
authored_deck_emission_active (read by the svg legacy master pin) and
every authored_vnext_* table (master-pin storage and the vNext export
half kept for B5).

downgrade() recreates the exact shape the earlier revisions left behind
(columns, defaults, constraints, indexes; on PostgreSQL also the controller
grants and the owner row-level-security policy of b5a0d3e7c9f1).  The
recreated tables are always empty: upgrade() destroys the rows.

Revision ID: d1a5c7e9b3f2
Revises: c5e8a1d3b7f9
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d1a5c7e9b3f2"
down_revision: str | None = "c5e8a1d3b7f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONTROLLER_ROLE = "presenton_public_controller"
_REQUEST_POLICY = "presenton_owner_scope"
_CONTROLLER_POLICY = "presenton_owner_controller_authority"
_VISIBILITY_FUNCTION = "presenton_owner_row_visible"
_RLS_TABLE = "authored_canonical_revisions"

# Drop order: referencing tables first (slidespec_publication_attempts
# references slidespec_artifact_bundles; the export bindings reference the
# kept presentation_export_attempts).
_DROP_ORDER = (
    "authored_semantic_export_bindings",
    "slidespec_publication_attempts",
    "slidespec_artifact_bundles",
    "authored_canonical_revisions",
    "authored_capability_receipts_v2",
    "authored_editable_hybrid_mvp_completions_v1",
    "authored_exact_provenance",
    "authored_release_artifacts_v3",
    "authored_release_nonces_v3",
    "authored_ci_evidence_identities_v3",
    "authored_com_qualification_jobs_v3",
    "authored_evidence_graph_roots_v3",
    "authored_evidence_bundle_completions_v3",
    "authored_gate0_structural_traces_v3",
    "authored_gate0_structural_subjects_v3",
    "authored_gate0_structural_events_v3",
    "authored_gate0_structural_completions_v3",
    "authored_semantic_artifacts",
    "authored_emission_bundles",
)


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    for table in _DROP_ORDER:
        if _has_table(table):
            op.drop_table(table)


def _create_authored_canonical_revisions() -> None:
    op.create_table(
        'authored_canonical_revisions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('owner_id', sa.Uuid(), nullable=True),
        sa.Column('presentation_id', sa.Uuid(), nullable=False),
        sa.Column('revision', sa.BigInteger(), nullable=False),
        sa.Column('parent_revision', sa.BigInteger(), nullable=False),
        sa.Column('source_digest', sa.String(length=71), nullable=False),
        sa.Column('operation', sa.String(length=16), nullable=False),
        sa.Column('snapshot', sa.JSON(), nullable=False),
        sa.Column('history', sa.JSON(), nullable=False),
        sa.Column('history_position', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['presentation_id'], ['presentations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('presentation_id', 'revision', name='uq_authored_canonical_presentation_revision'),
        sa.CheckConstraint('history_position >= 0', name='ck_authored_canonical_history_position_nonnegative'),
        sa.CheckConstraint("operation IN ('initialize', 'patch', 'undo', 'redo')", name='ck_authored_canonical_operation'),
        sa.CheckConstraint('revision >= 0 AND parent_revision >= 0', name='ck_authored_canonical_revision_nonnegative'),
    )
    op.create_index('ix_authored_canonical_presentation_created', 'authored_canonical_revisions', ['presentation_id', 'created_at'], unique=False)
    op.create_index('ix_authored_canonical_revisions_owner_id', 'authored_canonical_revisions', ['owner_id'], unique=False)


def _create_authored_capability_receipts_v2() -> None:
    op.create_table(
        'authored_capability_receipts_v2',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.String(length=128), nullable=False),
        sa.Column('capability_id', sa.String(length=128), nullable=False),
        sa.Column('state', sa.String(length=16), nullable=False),
        sa.Column('scope_digest', sa.String(length=71), nullable=False),
        sa.Column('environment_digest', sa.String(length=71), nullable=False),
        sa.Column('serializer_id', sa.String(length=128), nullable=False),
        sa.Column('serializer_version', sa.String(length=128), nullable=False),
        sa.Column('issuer_principal_id', sa.Uuid(), nullable=False),
        sa.Column('issuer_authority', sa.String(length=32), nullable=False),
        sa.Column('evaluated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('transition_kind', sa.String(length=16), nullable=False),
        sa.Column('transition_target_digest', sa.String(length=71), nullable=True),
        sa.Column('receipt_digest', sa.String(length=71), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('authority_domain', sa.String(length=32), nullable=False, server_default=sa.text("'structural_test'")),
        sa.Column('evidence_class', sa.String(length=64), nullable=False, server_default=sa.text("'synthetic_structural_only'")),
        sa.Column('issuer_id', sa.String(length=128), nullable=True),
        sa.Column('signer_key_id', sa.String(length=128), nullable=True),
        sa.Column('environment_id', sa.String(length=128), nullable=True),
        sa.Column('rotation_epoch', sa.Integer(), nullable=True),
        sa.Column('nonce', sa.String(length=128), nullable=True),
        sa.Column('signature_algorithm', sa.String(length=32), nullable=True),
        sa.Column('export_attempt_id', sa.Uuid(), nullable=True),
        sa.Column('binding_digest', sa.String(length=71), nullable=True),
        sa.Column('signature_base64', sa.String(length=256), nullable=True),
        sa.Column('authority_digest', sa.String(length=71), nullable=True),
        sa.Column('authority_payload', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'receipt_digest', name='uq_authored_capability_receipt_v2_digest'),
        sa.UniqueConstraint('issuer_id', 'signer_key_id', 'nonce', name='uq_authored_capability_receipt_v2_signer_nonce'),
        sa.UniqueConstraint('tenant_id', 'transition_target_digest', name='uq_authored_capability_receipt_v2_transition_target'),
        sa.CheckConstraint("authority_domain IN ('structural_test', 'actual_product')", name='ck_authored_capability_receipt_v2_authority_domain'),
        sa.CheckConstraint('expires_at > evaluated_at', name='ck_authored_capability_receipt_v2_expiry'),
        sa.CheckConstraint("state IN ('disabled', 'shadow', 'ready', 'blocked')", name='ck_authored_capability_receipt_v2_state'),
        sa.CheckConstraint("transition_kind IN ('issue', 'supersede', 'revoke')", name='ck_authored_capability_receipt_v2_transition_kind'),
    )
    op.create_index('ix_authored_capability_receipt_v2_active_lookup', 'authored_capability_receipts_v2', ['tenant_id', 'capability_id', 'scope_digest', 'environment_digest', 'expires_at'], unique=False)


def _create_authored_editable_hybrid_mvp_completions_v1() -> None:
    op.create_table(
        'authored_editable_hybrid_mvp_completions_v1',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('owner_id', sa.Uuid(), nullable=False),
        sa.Column('owner_scope', sa.String(length=128), nullable=False),
        sa.Column('tenant_id', sa.String(length=128), nullable=False),
        sa.Column('deck_id', sa.String(length=256), nullable=False),
        sa.Column('deck_revision', sa.BigInteger(), nullable=False),
        sa.Column('deck_digest', sa.String(length=71), nullable=False),
        sa.Column('scene_digest', sa.String(length=71), nullable=False),
        sa.Column('authority_domain', sa.String(length=32), nullable=False),
        sa.Column('profile_id', sa.String(length=128), nullable=False),
        sa.Column('profile_digest', sa.String(length=71), nullable=False),
        sa.Column('gate_manifest_digest', sa.String(length=71), nullable=False),
        sa.Column('generation_attempt_id', sa.String(length=256), nullable=False),
        sa.Column('hybrid_export_attempt_id', sa.String(length=256), nullable=False),
        sa.Column('artifact_inventory_digest', sa.String(length=71), nullable=False),
        sa.Column('artifact_count', sa.Integer(), nullable=False),
        sa.Column('skipped_count', sa.Integer(), nullable=False),
        sa.Column('result_core_digest', sa.String(length=71), nullable=False),
        sa.Column('result_set_digest', sa.String(length=71), nullable=False),
        sa.Column('sealed_result_sha256', sa.String(length=71), nullable=False),
        sa.Column('sealed_result_bytes', sa.LargeBinary(), nullable=False),
        sa.Column('validation_receipt_bytes', sa.LargeBinary(), nullable=False),
        sa.Column('verifier_trust_bytes', sa.LargeBinary(), nullable=False),
        sa.Column('verifier_trust_sha256', sa.String(length=71), nullable=False),
        sa.Column('receipt_digest', sa.String(length=71), nullable=False),
        sa.Column('issuer_id', sa.String(length=128), nullable=False),
        sa.Column('signer_key_id', sa.String(length=128), nullable=False),
        sa.Column('receipt_nonce', sa.String(length=256), nullable=False),
        sa.Column('issued_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('cas_version', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('owner_scope', 'tenant_id', 'sealed_result_sha256', name='uq_authored_editable_mvp_completion_v1_bytes'),
        sa.UniqueConstraint('issuer_id', 'signer_key_id', 'receipt_nonce', name='uq_authored_editable_mvp_completion_v1_nonce'),
        sa.UniqueConstraint('owner_scope', 'tenant_id', 'deck_id', 'deck_revision', name='uq_authored_editable_mvp_completion_v1_subject'),
        sa.CheckConstraint('artifact_count = 35', name='ck_authored_editable_mvp_completion_v1_artifacts'),
        sa.CheckConstraint('cas_version = 1', name='ck_authored_editable_mvp_completion_v1_cas'),
        sa.CheckConstraint("authority_domain = 'actual_product'", name='ck_authored_editable_mvp_completion_v1_domain'),
        sa.CheckConstraint('skipped_count = 0', name='ck_authored_editable_mvp_completion_v1_no_skip'),
        sa.CheckConstraint("profile_id = 'authored-editable-hybrid-ko-en-basic-v1'", name='ck_authored_editable_mvp_completion_v1_profile'),
    )
    op.create_index('ix_authored_editable_mvp_completion_v1_current', 'authored_editable_hybrid_mvp_completions_v1', ['owner_scope', 'tenant_id', 'deck_id', 'deck_revision', 'expires_at'], unique=False)


def _create_authored_exact_provenance() -> None:
    op.create_table(
        'authored_exact_provenance',
        sa.Column('presentation_id', sa.Uuid(), nullable=False),
        sa.Column('provenance', sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(['presentation_id'], ['presentations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('presentation_id'),
    )


def _create_authored_release_artifacts_v3() -> None:
    op.create_table(
        'authored_release_artifacts_v3',
        sa.Column('version_id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.String(length=128), nullable=False),
        sa.Column('export_attempt_id', sa.Uuid(), nullable=False),
        sa.Column('artifact_id', sa.String(length=256), nullable=False),
        sa.Column('sha256', sa.String(length=71), nullable=False),
        sa.Column('byte_length', sa.Integer(), nullable=False),
        sa.Column('media_type', sa.String(length=128), nullable=False),
        sa.Column('encryption_key_ref', sa.String(length=256), nullable=False),
        sa.Column('storage_generation', sa.Integer(), nullable=False),
        sa.Column('state', sa.String(length=16), nullable=False),
        sa.Column('content_bytes', sa.LargeBinary(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('version_id'),
        sa.UniqueConstraint('tenant_id', 'export_attempt_id', 'artifact_id', name='uq_authored_release_artifact_v3_identity'),
        sa.CheckConstraint('storage_generation > 0', name='ck_authored_release_artifact_v3_generation'),
        sa.CheckConstraint('byte_length > 0', name='ck_authored_release_artifact_v3_length'),
        sa.CheckConstraint("state IN ('durable', 'bound')", name='ck_authored_release_artifact_v3_state'),
    )


def _create_authored_release_nonces_v3() -> None:
    op.create_table(
        'authored_release_nonces_v3',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('authority_domain', sa.String(length=32), nullable=False),
        sa.Column('evidence_class', sa.String(length=64), nullable=False),
        sa.Column('issuer_id', sa.String(length=128), nullable=False),
        sa.Column('signer_key_id', sa.String(length=128), nullable=False),
        sa.Column('nonce', sa.String(length=128), nullable=False),
        sa.Column('subject_digest', sa.String(length=71), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('issuer_id', 'signer_key_id', 'nonce', name='uq_authored_release_nonce_v3_identity'),
    )


def _create_authored_ci_evidence_identities_v3() -> None:
    op.create_table(
        'authored_ci_evidence_identities_v3',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.String(length=128), nullable=False),
        sa.Column('owner_id', sa.Uuid(), nullable=False),
        sa.Column('evidence_bundle_id', sa.Uuid(), nullable=False),
        sa.Column('receipt_digest', sa.String(length=71), nullable=False),
        sa.Column('source_head_commit', sa.String(length=64), nullable=False),
        sa.Column('pipeline_uuid', sa.String(length=128), nullable=False),
        sa.Column('attempt_subject_digest', sa.String(length=71), nullable=False),
        sa.Column('artifact_digest', sa.String(length=71), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('evidence_bundle_id', name='uq_authored_ci_evidence_v3_bundle'),
        sa.UniqueConstraint('tenant_id', 'source_head_commit', name='uq_authored_ci_evidence_v3_head'),
        sa.UniqueConstraint('tenant_id', 'pipeline_uuid', name='uq_authored_ci_evidence_v3_pipeline'),
        sa.UniqueConstraint('tenant_id', 'receipt_digest', name='uq_authored_ci_evidence_v3_receipt'),
        sa.UniqueConstraint('tenant_id', 'attempt_subject_digest', name='uq_authored_ci_evidence_v3_subject'),
    )


def _create_authored_com_qualification_jobs_v3() -> None:
    op.create_table(
        'authored_com_qualification_jobs_v3',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.String(length=128), nullable=False),
        sa.Column('export_attempt_id', sa.Uuid(), nullable=False),
        sa.Column('evidence_policy_digest', sa.String(length=71), nullable=False),
        sa.Column('authority_domain', sa.String(length=32), nullable=False),
        sa.Column('state', sa.String(length=32), nullable=False),
        sa.Column('transition_sequence', sa.Integer(), nullable=False),
        sa.Column('lease_owner', sa.String(length=128), nullable=True),
        sa.Column('lease_token', sa.String(length=71), nullable=True),
        sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('attempt_count', sa.Integer(), nullable=False),
        sa.Column('evidence', sa.JSON(), nullable=False),
        sa.Column('failure_code', sa.String(length=128), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'export_attempt_id', 'evidence_policy_digest', 'authority_domain', name='uq_authored_com_qualification_v3_subject'),
        sa.CheckConstraint("authority_domain = 'actual_product'", name='ck_authored_com_qualification_v3_actual_product'),
        sa.CheckConstraint('attempt_count >= 0 AND attempt_count <= 2', name='ck_authored_com_qualification_v3_attempts'),
        sa.CheckConstraint('transition_sequence >= 0', name='ck_authored_com_qualification_v3_sequence'),
        sa.CheckConstraint("state IN ('captured','planned','assembled','inputOoxmlVerified','firstOpenObserved','savedCopyVerified','reopenObserved','comVerified','sealed','rejected')", name='ck_authored_com_qualification_v3_state'),
    )


def _create_authored_evidence_graph_roots_v3() -> None:
    op.create_table(
        'authored_evidence_graph_roots_v3',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.String(length=128), nullable=False),
        sa.Column('owner_id', sa.Uuid(), nullable=False),
        sa.Column('presentation_id', sa.Uuid(), nullable=False),
        sa.Column('presentation_revision', sa.Integer(), nullable=False),
        sa.Column('export_attempt_id', sa.Uuid(), nullable=False),
        sa.Column('authority_domain', sa.String(length=32), nullable=False),
        sa.Column('evidence_policy_digest', sa.String(length=71), nullable=False),
        sa.Column('active_manifest_digest', sa.String(length=71), nullable=False),
        sa.Column('source_tree_census_digest', sa.String(length=71), nullable=False),
        sa.Column('source_head_commit', sa.String(length=64), nullable=False),
        sa.Column('release_profile_digest', sa.String(length=71), nullable=False),
        sa.Column('feature_set_digest', sa.String(length=71), nullable=False),
        sa.Column('gate_minus1_manifest_digest', sa.String(length=71), nullable=False),
        sa.Column('gate_minus1_result_set_digest', sa.String(length=71), nullable=False),
        sa.Column('gate_minus1_receipt_artifact_digest', sa.String(length=71), nullable=False),
        sa.Column('gate_minus1_validator_contract_digest', sa.String(length=71), nullable=False),
        sa.Column('environment_attestation_digest', sa.String(length=71), nullable=False),
        sa.Column('worker_attestation_digest', sa.String(length=71), nullable=False),
        sa.Column('com_qualification_job_id', sa.Uuid(), nullable=False),
        sa.Column('com_qualification_result_digest', sa.String(length=71), nullable=False),
        sa.Column('base_artifact_ids', sa.JSON(), nullable=False),
        sa.Column('base_artifact_ids_digest', sa.String(length=71), nullable=False),
        sa.Column('final_required_artifact_ids_digest', sa.String(length=71), nullable=False),
        sa.Column('artifact_inventory', sa.JSON(), nullable=False),
        sa.Column('artifact_inventory_digest', sa.String(length=71), nullable=False),
        sa.Column('root_digest', sa.String(length=71), nullable=False),
        sa.Column('request_digest', sa.String(length=71), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'root_digest', name='uq_authored_evidence_graph_root_v3_digest'),
        sa.UniqueConstraint('tenant_id', 'export_attempt_id', 'evidence_policy_digest', 'authority_domain', name='uq_authored_evidence_graph_root_v3_subject'),
        sa.CheckConstraint("authority_domain = 'actual_product'", name='ck_authored_evidence_graph_root_v3_actual'),
    )


def _create_authored_evidence_bundle_completions_v3() -> None:
    op.create_table(
        'authored_evidence_bundle_completions_v3',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.String(length=128), nullable=False),
        sa.Column('graph_root_id', sa.Uuid(), nullable=False),
        sa.Column('authority_domain', sa.String(length=32), nullable=False),
        sa.Column('state', sa.String(length=16), nullable=False),
        sa.Column('cas_version', sa.Integer(), nullable=False),
        sa.Column('required_capability_ids', sa.JSON(), nullable=False),
        sa.Column('receipt_digests', sa.JSON(), nullable=False),
        sa.Column('completion_digest', sa.String(length=71), nullable=False),
        sa.Column('create_request_digest', sa.String(length=71), nullable=False),
        sa.Column('seal_request_digest', sa.String(length=71), nullable=True),
        sa.Column('receipt_identities', sa.JSON(), nullable=False),
        sa.Column('authority_payload', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('sealed_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'completion_digest', name='uq_authored_evidence_completion_v3_digest'),
        sa.UniqueConstraint('graph_root_id', name='uq_authored_evidence_completion_v3_root'),
        sa.CheckConstraint("authority_domain = 'actual_product'", name='ck_authored_evidence_completion_v3_actual'),
        sa.CheckConstraint("state IN ('pending','sealed')", name='ck_authored_evidence_completion_v3_state'),
    )


def _create_authored_gate0_structural_traces_v3() -> None:
    op.create_table(
        'authored_gate0_structural_traces_v3',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('run_id', sa.Uuid(), nullable=False),
        sa.Column('case_id', sa.String(length=128), nullable=False),
        sa.Column('ordinal', sa.Integer(), nullable=False),
        sa.Column('authority_domain', sa.String(length=32), nullable=False),
        sa.Column('request_digest', sa.String(length=71), nullable=False),
        sa.Column('trace_digest', sa.String(length=71), nullable=False),
        sa.Column('http_status', sa.Integer(), nullable=False),
        sa.Column('error_code', sa.String(length=128), nullable=True),
        sa.Column('sealed_completion_count', sa.Integer(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id', 'case_id', name='uq_authored_gate0_structural_trace_v3_case'),
        sa.CheckConstraint("authority_domain = 'structural_test'", name='ck_authored_gate0_structural_trace_v3_domain'),
        sa.CheckConstraint('sealed_completion_count = 0', name='ck_authored_gate0_structural_trace_v3_no_completion'),
    )


def _create_authored_gate0_structural_subjects_v3() -> None:
    op.create_table(
        'authored_gate0_structural_subjects_v3',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('run_id', sa.Uuid(), nullable=False),
        sa.Column('case_id', sa.String(length=128), nullable=False),
        sa.Column('tenant_id', sa.String(length=128), nullable=False),
        sa.Column('authority_domain', sa.String(length=32), nullable=False),
        sa.Column('presentation_id', sa.Uuid(), nullable=False),
        sa.Column('baseline_subject_digest', sa.String(length=71), nullable=False),
        sa.Column('mutated_subject_digest', sa.String(length=71), nullable=False),
        sa.Column('subject_artifact_id', sa.String(length=256), nullable=False),
        sa.Column('subject_artifact_version_id', sa.Uuid(), nullable=False),
        sa.Column('structural_receipt_digest', sa.String(length=71), nullable=True),
        sa.Column('projection_digest', sa.String(length=71), nullable=True),
        sa.Column('baseline_payload', sa.JSON(), nullable=False),
        sa.Column('mutated_payload', sa.JSON(), nullable=False),
        sa.Column('post_state', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id', 'case_id', name='uq_authored_gate0_structural_subject_v3_case'),
        sa.CheckConstraint("authority_domain = 'structural_test'", name='ck_authored_gate0_structural_subject_v3_domain'),
    )


def _create_authored_gate0_structural_events_v3() -> None:
    op.create_table(
        'authored_gate0_structural_events_v3',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('subject_id', sa.Uuid(), nullable=False),
        sa.Column('authority_domain', sa.String(length=32), nullable=False),
        sa.Column('seam', sa.String(length=64), nullable=False),
        sa.Column('operation_id', sa.Uuid(), nullable=False),
        sa.Column('input_digest', sa.String(length=71), nullable=False),
        sa.Column('output_digest', sa.String(length=71), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('subject_id', 'seam', name='uq_authored_gate0_structural_event_v3_seam'),
        sa.CheckConstraint("authority_domain = 'structural_test'", name='ck_authored_gate0_structural_event_v3_domain'),
    )


def _create_authored_gate0_structural_completions_v3() -> None:
    op.create_table(
        'authored_gate0_structural_completions_v3',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('subject_id', sa.Uuid(), nullable=False),
        sa.Column('authority_domain', sa.String(length=32), nullable=False),
        sa.Column('state', sa.String(length=16), nullable=False),
        sa.Column('cas_version', sa.Integer(), nullable=False),
        sa.Column('receipt_digests', sa.JSON(), nullable=False),
        sa.Column('completion_digest', sa.String(length=71), nullable=False),
        sa.Column('authority_payload', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('sealed_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('subject_id', name='uq_authored_gate0_structural_completion_v3_subject'),
        sa.CheckConstraint("authority_domain = 'structural_test'", name='ck_authored_gate0_structural_completion_v3_domain'),
        sa.CheckConstraint("state IN ('pending','sealed')", name='ck_authored_gate0_structural_completion_v3_state'),
    )


def _create_authored_semantic_artifacts() -> None:
    op.create_table(
        'authored_semantic_artifacts',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('owner_id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.String(length=128), nullable=False),
        sa.Column('artifact_ref', sa.String(length=96), nullable=False),
        sa.Column('content_digest', sa.String(length=71), nullable=False),
        sa.Column('byte_length', sa.BigInteger(), nullable=False),
        sa.Column('media_type', sa.String(length=128), nullable=False),
        sa.Column('original_locator_digest', sa.String(length=71), nullable=True),
        sa.Column('resolver_version', sa.String(length=128), nullable=True),
        sa.Column('retention_class', sa.String(length=32), nullable=False),
        sa.Column('content_bytes', sa.LargeBinary(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('owner_id', 'tenant_id', 'content_digest', name='uq_authored_semantic_artifact_digest'),
        sa.CheckConstraint('byte_length >= 0', name='ck_authored_semantic_artifact_length'),
    )
    op.create_index('ix_authored_semantic_artifact_scope_created', 'authored_semantic_artifacts', ['owner_id', 'tenant_id', 'created_at'], unique=False)


def _create_authored_emission_bundles() -> None:
    op.create_table(
        'authored_emission_bundles',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('owner_id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.String(length=128), nullable=False),
        sa.Column('presentation_id', sa.Uuid(), nullable=False),
        sa.Column('slide_id', sa.Uuid(), nullable=False),
        sa.Column('slide_revision', sa.BigInteger(), nullable=False),
        sa.Column('bundle_kind', sa.String(length=16), nullable=False),
        sa.Column('operation_id', sa.Uuid(), nullable=False),
        sa.Column('origin_generation_attempt_id', sa.Uuid(), nullable=True),
        sa.Column('html_digest', sa.String(length=71), nullable=False),
        sa.Column('semantic_component_digest', sa.String(length=71), nullable=False),
        sa.Column('asset_manifest_digest', sa.String(length=71), nullable=False),
        sa.Column('font_manifest_digest', sa.String(length=71), nullable=False),
        sa.Column('master_contract_digest', sa.String(length=71), nullable=False),
        sa.Column('composition_digest', sa.String(length=71), nullable=False),
        sa.Column('identity_registry_digest', sa.String(length=71), nullable=False),
        sa.Column('quality_policy_version', sa.String(length=128), nullable=False),
        sa.Column('bundle_digest', sa.String(length=71), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('owner_id', 'tenant_id', 'bundle_digest', name='uq_authored_emission_bundle_digest'),
        sa.UniqueConstraint('owner_id', 'tenant_id', 'presentation_id', 'slide_id', 'slide_revision', 'operation_id', name='uq_authored_emission_bundle_lineage'),
        sa.CheckConstraint("bundle_kind IN ('authored', 'legacyHtml')", name='ck_authored_emission_bundle_kind'),
        sa.CheckConstraint('slide_revision >= 1', name='ck_authored_emission_bundle_revision'),
    )
    op.create_index('ix_authored_emission_bundle_slide', 'authored_emission_bundles', ['owner_id', 'tenant_id', 'presentation_id', 'slide_id', 'slide_revision'], unique=False)


def _create_authored_semantic_export_bindings() -> None:
    op.create_table(
        'authored_semantic_export_bindings',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('owner_id', sa.Uuid(), nullable=False),
        sa.Column('tenant_id', sa.String(length=128), nullable=False),
        sa.Column('export_attempt_id', sa.Uuid(), nullable=False),
        sa.Column('presentation_id', sa.Uuid(), nullable=False),
        sa.Column('presentation_revision', sa.BigInteger(), nullable=False),
        sa.Column('canonical_digest', sa.String(length=71), nullable=False),
        sa.Column('renderer_source_digest', sa.String(length=71), nullable=False),
        sa.Column('mode', sa.String(length=16), nullable=False),
        sa.Column('serializer_id', sa.String(length=128), nullable=False),
        sa.Column('serializer_version', sa.String(length=128), nullable=False),
        sa.Column('pptx_digest', sa.String(length=71), nullable=True),
        sa.Column('binding_digest', sa.String(length=71), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(['export_attempt_id'], ['presentation_export_attempts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('export_attempt_id', name='uq_authored_semantic_export_attempt'),
        sa.UniqueConstraint('owner_id', 'tenant_id', 'binding_digest', name='uq_authored_semantic_export_binding'),
        sa.CheckConstraint("mode IN ('fidelity', 'hybrid')", name='ck_authored_semantic_export_mode'),
        sa.CheckConstraint('presentation_revision >= 1', name='ck_authored_semantic_export_revision'),
    )
    op.create_index('ix_authored_semantic_export_presentation', 'authored_semantic_export_bindings', ['owner_id', 'tenant_id', 'presentation_id', 'presentation_revision'], unique=False)


def _create_slidespec_artifact_bundles() -> None:
    op.create_table(
        'slidespec_artifact_bundles',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('presentation_id', sa.Uuid(), nullable=False),
        sa.Column('slide_id', sa.Uuid(), nullable=False),
        sa.Column('slide_index', sa.Integer(), nullable=False),
        sa.Column('spec_revision', sa.Integer(), nullable=False),
        sa.Column('route', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('base_spec_hash', sa.String(length=71), nullable=True),
        sa.Column('candidate_spec_hash', sa.String(length=71), nullable=False),
        sa.Column('render_input_hash', sa.String(length=71), nullable=False),
        sa.Column('html_hash', sa.String(length=71), nullable=False),
        sa.Column('render_hash', sa.String(length=71), nullable=False),
        sa.Column('image_hash', sa.String(length=71), nullable=True),
        sa.Column('spec', sa.JSON(), nullable=False),
        sa.Column('repair_plan', sa.JSON(), nullable=True),
        sa.Column('acceptance', sa.JSON(), nullable=True),
        sa.Column('evidence', sa.JSON(), nullable=False),
        sa.Column('manifest', sa.JSON(), nullable=False),
        sa.Column('publication_receipt', sa.JSON(), nullable=True),
        sa.Column('html', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['presentation_id'], ['presentations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['slide_id'], ['slides.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slide_id', 'candidate_spec_hash', 'status', 'route', name='uq_slidespec_artifact_bundles_slide_candidate_outcome'),
        sa.CheckConstraint("route IN ('import_shadow', 'repair_shadow', 'slidespec_active')", name='ck_slidespec_artifact_bundles_route'),
        sa.CheckConstraint('slide_index >= 0', name='ck_slidespec_artifact_bundles_slide_index_nonnegative'),
        sa.CheckConstraint('spec_revision >= 1', name='ck_slidespec_artifact_bundles_spec_revision_positive'),
        sa.CheckConstraint("status IN ('imported', 'candidate', 'shadow', 'rejected', 'published')", name='ck_slidespec_artifact_bundles_status'),
    )
    op.create_index('ix_slidespec_artifact_bundles_presentation_slide_created', 'slidespec_artifact_bundles', ['presentation_id', 'slide_index', 'created_at'], unique=False)


def _create_slidespec_publication_attempts() -> None:
    op.create_table(
        'slidespec_publication_attempts',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('attempt_id', sa.Uuid(), nullable=False),
        sa.Column('artifact_bundle_id', sa.Uuid(), nullable=False),
        sa.Column('presentation_id', sa.Uuid(), nullable=False),
        sa.Column('slide_id', sa.Uuid(), nullable=False),
        sa.Column('state', sa.String(length=32), nullable=False),
        sa.Column('reason_code', sa.String(length=96), nullable=True),
        sa.Column('evidence', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['presentation_id'], ['presentations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['slide_id'], ['slides.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['artifact_bundle_id'], ['slidespec_artifact_bundles.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('attempt_id', 'state', name='uq_slidespec_publication_attempt_state'),
        sa.CheckConstraint("state IN ('staged', 'activation_committed', 'verified', 'failed', 'rolled_back', 'rollback_conflict')", name='ck_slidespec_publication_attempt_state'),
    )
    op.create_index('ix_slidespec_publication_attempts_slide_created', 'slidespec_publication_attempts', ['slide_id', 'created_at'], unique=False)


# Creation order: the artifact bundles precede the publication attempts.
_RECREATE = (
    ("authored_canonical_revisions", _create_authored_canonical_revisions),
    ("authored_capability_receipts_v2", _create_authored_capability_receipts_v2),
    ("authored_editable_hybrid_mvp_completions_v1", _create_authored_editable_hybrid_mvp_completions_v1),
    ("authored_exact_provenance", _create_authored_exact_provenance),
    ("authored_release_artifacts_v3", _create_authored_release_artifacts_v3),
    ("authored_release_nonces_v3", _create_authored_release_nonces_v3),
    ("authored_ci_evidence_identities_v3", _create_authored_ci_evidence_identities_v3),
    ("authored_com_qualification_jobs_v3", _create_authored_com_qualification_jobs_v3),
    ("authored_evidence_graph_roots_v3", _create_authored_evidence_graph_roots_v3),
    ("authored_evidence_bundle_completions_v3", _create_authored_evidence_bundle_completions_v3),
    ("authored_gate0_structural_traces_v3", _create_authored_gate0_structural_traces_v3),
    ("authored_gate0_structural_subjects_v3", _create_authored_gate0_structural_subjects_v3),
    ("authored_gate0_structural_events_v3", _create_authored_gate0_structural_events_v3),
    ("authored_gate0_structural_completions_v3", _create_authored_gate0_structural_completions_v3),
    ("authored_semantic_artifacts", _create_authored_semantic_artifacts),
    ("authored_emission_bundles", _create_authored_emission_bundles),
    ("authored_semantic_export_bindings", _create_authored_semantic_export_bindings),
    ("slidespec_artifact_bundles", _create_slidespec_artifact_bundles),
    ("slidespec_publication_attempts", _create_slidespec_publication_attempts),
)


def _restore_postgresql_authority(table: str) -> None:
    """Re-apply what the earlier revisions granted to the controller role."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    role_exists = bind.execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
        {"role": _CONTROLLER_ROLE},
    ).scalar()
    if role_exists:
        op.execute(
            sa.text(
                f'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "{table}" '
                f"TO {_CONTROLLER_ROLE}"
            )
        )
    if table != _RLS_TABLE or not role_exists:
        return
    function_exists = bind.execute(
        sa.text(
            "SELECT 1 FROM pg_proc WHERE proname = :name "
            "AND pronamespace = 'public'::regnamespace"
        ),
        {"name": _VISIBILITY_FUNCTION},
    ).scalar()
    if not function_exists:
        return
    predicate = f"public.{_VISIBILITY_FUNCTION}(owner_id, false)"
    op.execute(sa.text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY'))
    op.execute(
        sa.text(
            f'CREATE POLICY {_REQUEST_POLICY} ON "{table}" FOR ALL '
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )
    )
    op.execute(
        sa.text(
            f'CREATE POLICY {_CONTROLLER_POLICY} ON "{table}" FOR ALL '
            f"TO {_CONTROLLER_ROLE} USING (true) WITH CHECK (true)"
        )
    )


def downgrade() -> None:
    for table, create in _RECREATE:
        if _has_table(table):
            continue
        create()
        _restore_postgresql_authority(table)
