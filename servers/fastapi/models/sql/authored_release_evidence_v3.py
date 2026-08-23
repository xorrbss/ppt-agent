"""Durable CAS artifacts and COM qualification state for release evidence v3."""

from __future__ import annotations

from datetime import datetime
from typing import Any
import uuid

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Integer,
    JSON,
    LargeBinary,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime


class AuthoredReleaseArtifactV3(SQLModel, table=True):
    __tablename__ = "authored_release_artifacts_v3"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "export_attempt_id",
            "artifact_id",
            name="uq_authored_release_artifact_v3_identity",
        ),
        CheckConstraint("state IN ('durable', 'bound')", name="ck_authored_release_artifact_v3_state"),
    )

    version_id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    export_attempt_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    artifact_id: str = Field(sa_column=Column(String(256), nullable=False))
    sha256: str = Field(sa_column=Column(String(71), nullable=False))
    byte_length: int = Field(ge=1, sa_column=Column(Integer, nullable=False))
    media_type: str = Field(sa_column=Column(String(128), nullable=False))
    encryption_key_ref: str = Field(sa_column=Column(String(256), nullable=False))
    storage_generation: int = Field(ge=1, sa_column=Column(Integer, nullable=False))
    state: str = Field(default="durable", sa_column=Column(String(16), nullable=False))
    content_bytes: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    created_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class AuthoredReleaseNonceV3(SQLModel, table=True):
    __tablename__ = "authored_release_nonces_v3"
    __table_args__ = (
        UniqueConstraint(
            "issuer_id",
            "signer_key_id",
            "nonce",
            name="uq_authored_release_nonce_v3_identity",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    authority_domain: str = Field(sa_column=Column(String(32), nullable=False))
    evidence_class: str = Field(sa_column=Column(String(64), nullable=False))
    issuer_id: str = Field(sa_column=Column(String(128), nullable=False))
    signer_key_id: str = Field(sa_column=Column(String(128), nullable=False))
    nonce: str = Field(sa_column=Column(String(128), nullable=False))
    subject_digest: str = Field(sa_column=Column(String(71), nullable=False))
    consumed_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class AuthoredCiEvidenceIdentityV3(SQLModel, table=True):
    __tablename__ = "authored_ci_evidence_identities_v3"
    __table_args__ = (
        UniqueConstraint("tenant_id", "receipt_digest", name="uq_authored_ci_evidence_v3_receipt"),
        UniqueConstraint("tenant_id", "source_head_commit", name="uq_authored_ci_evidence_v3_head"),
        UniqueConstraint("tenant_id", "pipeline_uuid", name="uq_authored_ci_evidence_v3_pipeline"),
        UniqueConstraint("tenant_id", "attempt_subject_digest", name="uq_authored_ci_evidence_v3_subject"),
        UniqueConstraint("evidence_bundle_id", name="uq_authored_ci_evidence_v3_bundle"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    owner_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    evidence_bundle_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    receipt_digest: str = Field(sa_column=Column(String(71), nullable=False))
    source_head_commit: str = Field(sa_column=Column(String(64), nullable=False))
    pipeline_uuid: str = Field(sa_column=Column(String(128), nullable=False))
    attempt_subject_digest: str = Field(sa_column=Column(String(71), nullable=False))
    artifact_digest: str = Field(sa_column=Column(String(71), nullable=False))
    created_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class AuthoredComQualificationJobV3(SQLModel, table=True):
    __tablename__ = "authored_com_qualification_jobs_v3"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "export_attempt_id",
            "evidence_policy_digest",
            "authority_domain",
            name="uq_authored_com_qualification_v3_subject",
        ),
        CheckConstraint(
            "state IN ('captured','planned','assembled','inputOoxmlVerified',"
            "'firstOpenObserved','savedCopyVerified','reopenObserved',"
            "'comVerified','sealed','rejected')",
            name="ck_authored_com_qualification_v3_state",
        ),
        CheckConstraint("authority_domain = 'actual_product'", name="ck_authored_com_qualification_v3_actual_product"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    export_attempt_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    evidence_policy_digest: str = Field(sa_column=Column(String(71), nullable=False))
    authority_domain: str = Field(default="actual_product", sa_column=Column(String(32), nullable=False))
    state: str = Field(default="captured", sa_column=Column(String(32), nullable=False))
    transition_sequence: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    lease_owner: str | None = Field(default=None, sa_column=Column(String(128), nullable=True))
    lease_token: str | None = Field(default=None, sa_column=Column(String(128), nullable=True))
    lease_expires_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    heartbeat_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    attempt_count: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    evidence: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    failure_code: str | None = Field(default=None, sa_column=Column(String(128), nullable=True))
    updated_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class AuthoredEvidenceGraphRootV3(SQLModel, table=True):
    __tablename__ = "authored_evidence_graph_roots_v3"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "export_attempt_id",
            "evidence_policy_digest",
            "authority_domain",
            name="uq_authored_evidence_graph_root_v3_subject",
        ),
        UniqueConstraint("tenant_id", "root_digest", name="uq_authored_evidence_graph_root_v3_digest"),
        CheckConstraint("authority_domain = 'actual_product'", name="ck_authored_evidence_graph_root_v3_actual"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    owner_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    presentation_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    presentation_revision: int = Field(sa_column=Column(Integer, nullable=False))
    export_attempt_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    authority_domain: str = Field(default="actual_product", sa_column=Column(String(32), nullable=False))
    evidence_policy_digest: str = Field(sa_column=Column(String(71), nullable=False))
    active_manifest_digest: str = Field(sa_column=Column(String(71), nullable=False))
    source_tree_census_digest: str = Field(sa_column=Column(String(71), nullable=False))
    source_head_commit: str = Field(sa_column=Column(String(64), nullable=False))
    release_profile_digest: str = Field(sa_column=Column(String(71), nullable=False))
    feature_set_digest: str = Field(sa_column=Column(String(71), nullable=False))
    gate_minus1_manifest_digest: str = Field(sa_column=Column(String(71), nullable=False))
    gate_minus1_result_set_digest: str = Field(sa_column=Column(String(71), nullable=False))
    gate_minus1_receipt_artifact_digest: str = Field(sa_column=Column(String(71), nullable=False))
    gate_minus1_validator_contract_digest: str = Field(sa_column=Column(String(71), nullable=False))
    environment_attestation_digest: str = Field(sa_column=Column(String(71), nullable=False))
    worker_attestation_digest: str = Field(sa_column=Column(String(71), nullable=False))
    com_qualification_job_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    com_qualification_result_digest: str = Field(sa_column=Column(String(71), nullable=False))
    base_artifact_ids: list[str] = Field(sa_column=Column(JSON, nullable=False))
    base_artifact_ids_digest: str = Field(sa_column=Column(String(71), nullable=False))
    final_required_artifact_ids_digest: str = Field(sa_column=Column(String(71), nullable=False))
    artifact_inventory: list[dict[str, Any]] = Field(sa_column=Column(JSON, nullable=False))
    artifact_inventory_digest: str = Field(sa_column=Column(String(71), nullable=False))
    root_digest: str = Field(sa_column=Column(String(71), nullable=False))
    request_digest: str = Field(sa_column=Column(String(71), nullable=False))
    created_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class AuthoredEvidenceBundleCompletionV3(SQLModel, table=True):
    __tablename__ = "authored_evidence_bundle_completions_v3"
    __table_args__ = (
        UniqueConstraint("graph_root_id", name="uq_authored_evidence_completion_v3_root"),
        UniqueConstraint("tenant_id", "completion_digest", name="uq_authored_evidence_completion_v3_digest"),
        CheckConstraint("state IN ('pending','sealed')", name="ck_authored_evidence_completion_v3_state"),
        CheckConstraint("authority_domain = 'actual_product'", name="ck_authored_evidence_completion_v3_actual"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    graph_root_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    authority_domain: str = Field(default="actual_product", sa_column=Column(String(32), nullable=False))
    state: str = Field(default="pending", sa_column=Column(String(16), nullable=False))
    cas_version: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    required_capability_ids: list[str] = Field(sa_column=Column(JSON, nullable=False))
    receipt_digests: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    completion_digest: str = Field(sa_column=Column(String(71), nullable=False))
    create_request_digest: str = Field(sa_column=Column(String(71), nullable=False))
    seal_request_digest: str | None = Field(default=None, sa_column=Column(String(71), nullable=True))
    receipt_identities: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    authority_payload: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    created_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    sealed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


class AuthoredGate0StructuralTraceV3(SQLModel, table=True):
    __tablename__ = "authored_gate0_structural_traces_v3"
    __table_args__ = (
        UniqueConstraint("run_id", "case_id", name="uq_authored_gate0_structural_trace_v3_case"),
        CheckConstraint("authority_domain = 'structural_test'", name="ck_authored_gate0_structural_trace_v3_domain"),
        CheckConstraint("sealed_completion_count = 0", name="ck_authored_gate0_structural_trace_v3_no_completion"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    run_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    case_id: str = Field(sa_column=Column(String(128), nullable=False))
    ordinal: int = Field(sa_column=Column(Integer, nullable=False))
    authority_domain: str = Field(default="structural_test", sa_column=Column(String(32), nullable=False))
    request_digest: str = Field(sa_column=Column(String(71), nullable=False))
    trace_digest: str = Field(sa_column=Column(String(71), nullable=False))
    http_status: int = Field(sa_column=Column(Integer, nullable=False))
    error_code: str | None = Field(default=None, sa_column=Column(String(128), nullable=True))
    sealed_completion_count: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    payload: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class AuthoredGate0StructuralSubjectV3(SQLModel, table=True):
    __tablename__ = "authored_gate0_structural_subjects_v3"
    __table_args__ = (
        UniqueConstraint("run_id", "case_id", name="uq_authored_gate0_structural_subject_v3_case"),
        CheckConstraint("authority_domain = 'structural_test'", name="ck_authored_gate0_structural_subject_v3_domain"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    run_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    case_id: str = Field(sa_column=Column(String(128), nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    authority_domain: str = Field(default="structural_test", sa_column=Column(String(32), nullable=False))
    presentation_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    baseline_subject_digest: str = Field(sa_column=Column(String(71), nullable=False))
    mutated_subject_digest: str = Field(sa_column=Column(String(71), nullable=False))
    subject_artifact_id: str = Field(sa_column=Column(String(256), nullable=False))
    subject_artifact_version_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    structural_receipt_digest: str | None = Field(default=None, sa_column=Column(String(71), nullable=True))
    projection_digest: str | None = Field(default=None, sa_column=Column(String(71), nullable=True))
    baseline_payload: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    mutated_payload: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    post_state: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class AuthoredGate0StructuralEventV3(SQLModel, table=True):
    __tablename__ = "authored_gate0_structural_events_v3"
    __table_args__ = (
        UniqueConstraint("subject_id", "seam", name="uq_authored_gate0_structural_event_v3_seam"),
        CheckConstraint("authority_domain = 'structural_test'", name="ck_authored_gate0_structural_event_v3_domain"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    subject_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    authority_domain: str = Field(default="structural_test", sa_column=Column(String(32), nullable=False))
    seam: str = Field(sa_column=Column(String(64), nullable=False))
    operation_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    input_digest: str = Field(sa_column=Column(String(71), nullable=False))
    output_digest: str = Field(sa_column=Column(String(71), nullable=False))
    payload: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class AuthoredGate0StructuralCompletionV3(SQLModel, table=True):
    __tablename__ = "authored_gate0_structural_completions_v3"
    __table_args__ = (
        UniqueConstraint("subject_id", name="uq_authored_gate0_structural_completion_v3_subject"),
        CheckConstraint(
            "authority_domain = 'structural_test'", name="ck_authored_gate0_structural_completion_v3_domain"
        ),
        CheckConstraint("state IN ('pending','sealed')", name="ck_authored_gate0_structural_completion_v3_state"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    subject_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    authority_domain: str = Field(default="structural_test", sa_column=Column(String(32), nullable=False))
    state: str = Field(default="pending", sa_column=Column(String(16), nullable=False))
    cas_version: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    receipt_digests: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    completion_digest: str = Field(sa_column=Column(String(71), nullable=False))
    authority_payload: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    created_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    sealed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


__all__ = [
    "AuthoredComQualificationJobV3",
    "AuthoredReleaseArtifactV3",
    "AuthoredReleaseNonceV3",
    "AuthoredCiEvidenceIdentityV3",
    "AuthoredEvidenceGraphRootV3",
    "AuthoredEvidenceBundleCompletionV3",
    "AuthoredGate0StructuralTraceV3",
    "AuthoredGate0StructuralSubjectV3",
    "AuthoredGate0StructuralEventV3",
    "AuthoredGate0StructuralCompletionV3",
]
