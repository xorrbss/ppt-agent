"""Cryptographic authority envelope for bounded-Hybrid product evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
import uuid

from pydantic import Field, field_validator, model_validator

from models.authored_vnext.master.base import StrictCamelModel
from models.authored_vnext.semantic.readiness import CapabilityReceiptIssueRequestV2


MAX_DURABLE_ARTIFACT_BYTES_V3 = 16 * 1024 * 1024
MAX_DURABLE_ARTIFACT_BASE64_CHARS_V3 = ((MAX_DURABLE_ARTIFACT_BYTES_V3 + 2) // 3) * 4


GATE1_REQUIRED_ARTIFACT_IDS_V3: tuple[str, ...] = (
    "semantic-source-json",
    "active-manifest-json",
    "fidelity-input-pptx",
    "fidelity-saved-pptx",
    "hybrid-input-pptx",
    "hybrid-saved-pptx",
    *(f"fidelity-browser-slide-{index:02d}-png" for index in range(1, 9)),
    *(f"hybrid-browser-slide-{index:02d}-png" for index in range(1, 9)),
    *(f"fidelity-first-open-slide-{index:02d}-png" for index in range(1, 9)),
    *(f"fidelity-reopen-slide-{index:02d}-png" for index in range(1, 9)),
    *(f"hybrid-first-open-slide-{index:02d}-png" for index in range(1, 9)),
    *(f"hybrid-reopen-slide-{index:02d}-png" for index in range(1, 9)),
    "fidelity-input-ooxml-json",
    "fidelity-saved-ooxml-json",
    "hybrid-input-ooxml-json",
    "hybrid-saved-ooxml-json",
    "fidelity-first-open-com-json",
    "fidelity-reopen-com-json",
    "hybrid-first-open-com-json",
    "hybrid-reopen-com-json",
    "independent-visual-metrics-json",
    "manual-ledger-json",
    "environment-attestation-json",
    "worker-attestation-json",
    "ci-seal-json",
    "phase-result-set-json",
    "evidence-graph-root-json",
    "completion-seal-json",
)
GATE1_REQUIRED_ARTIFACT_IDS_DIGEST_V3 = "sha256:36326d7447a867e06cbc00730ad51e0172dfb3c6d5e363db098bb704d71bbb5f"
GATE1_GRAPH_BASE_ARTIFACT_IDS_V3: tuple[str, ...] = tuple(
    artifact_id
    for artifact_id in GATE1_REQUIRED_ARTIFACT_IDS_V3
    if artifact_id
    not in {
        "phase-result-set-json",
        "evidence-graph-root-json",
        "completion-seal-json",
    }
)
GATE1_GRAPH_BASE_ARTIFACT_IDS_DIGEST_V3 = "sha256:3ae6393a6db1a97ea8e05116ed37eda08244dd41619b5dbfc1bd0ae0e6ea7f5c"


class EvidenceIssuerEnvelopeV3(StrictCamelModel):
    schema_version: Literal["authored-evidence-issuer-envelope/v3"] = "authored-evidence-issuer-envelope/v3"
    authority_domain: Literal["actual_product"]
    evidence_class: Literal[
        "actual_product_capability",
        "actual_product_ci",
        "actual_product_com_qualification",
        "actual_product_completion",
    ]
    issuer_id: str = Field(pattern=r"^[a-z][a-z0-9._/-]{2,127}$")
    signer_key_id: str = Field(pattern=r"^[a-z][a-z0-9._/-]{2,127}$")
    environment_id: str = Field(pattern=r"^[a-z][a-z0-9._/-]{2,127}$")
    key_purpose: Literal[
        "actual-product-capability",
        "actual-product-ci",
        "actual-product-com-qualification",
        "actual-product-completion",
    ]
    rotation_epoch: int = Field(ge=1)
    issued_at: datetime
    expires_at: datetime
    nonce: str = Field(pattern=r"^[A-Za-z0-9_-]{16,128}$")
    signature_algorithm: Literal["Ed25519"]
    signature_base64: str = Field(pattern=r"^[A-Za-z0-9+/]{86}==$")

    @field_validator("issued_at", "expires_at", mode="before")
    @classmethod
    def parse_wire_datetime(cls, value: object) -> object:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value

    @model_validator(mode="after")
    def valid_window_and_purpose(self) -> "EvidenceIssuerEnvelopeV3":
        if self.expires_at <= self.issued_at:
            raise ValueError("authored_evidence_issuer_expiry_invalid")
        expected_purpose = {
            "actual_product_capability": "actual-product-capability",
            "actual_product_ci": "actual-product-ci",
            "actual_product_com_qualification": "actual-product-com-qualification",
            "actual_product_completion": "actual-product-completion",
        }[self.evidence_class]
        if self.key_purpose != expected_purpose:
            raise ValueError("authored_evidence_signer_key_purpose_mismatch")
        return self


class CapabilityReceiptIssueRequestV3(StrictCamelModel):
    schema_version: Literal["authored-capability-receipt-issue/v3"] = "authored-capability-receipt-issue/v3"
    receipt: CapabilityReceiptIssueRequestV2
    export_attempt_id: uuid.UUID
    binding_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    authority: EvidenceIssuerEnvelopeV3

    @field_validator("export_attempt_id", mode="before")
    @classmethod
    def parse_wire_uuid(cls, value: object) -> object:
        return uuid.UUID(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def capability_signer_only(self) -> "CapabilityReceiptIssueRequestV3":
        if (
            self.authority.evidence_class != "actual_product_capability"
            or self.authority.key_purpose != "actual-product-capability"
        ):
            raise ValueError("authored_capability_receipt_authority_class_invalid")
        return self


class DurableArtifactPutRequestV3(StrictCamelModel):
    schema_version: Literal["authored-durable-artifact-put/v3"] = "authored-durable-artifact-put/v3"
    export_attempt_id: uuid.UUID
    artifact_id: str = Field(pattern=r"^[A-Za-z0-9._/-]{3,256}$")
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    media_type: str = Field(min_length=1, max_length=128)
    encryption_key_ref: str = Field(pattern=r"^[A-Za-z0-9._:/-]{3,256}$")
    storage_generation: int = Field(ge=1)
    content_base64: str = Field(
        min_length=4,
        max_length=MAX_DURABLE_ARTIFACT_BASE64_CHARS_V3,
    )

    @field_validator("export_attempt_id", mode="before")
    @classmethod
    def parse_wire_uuid(cls, value: object) -> object:
        return uuid.UUID(value) if isinstance(value, str) else value


class DurableArtifactRefV3(StrictCamelModel):
    schema_version: Literal["authored-durable-artifact-ref/v3"] = "authored-durable-artifact-ref/v3"
    export_attempt_id: uuid.UUID
    artifact_id: str
    version_id: uuid.UUID
    sha256: str
    byte_length: int
    media_type: str
    encryption_key_ref: str
    storage_generation: int
    state: Literal["durable", "bound"]

    @field_validator("export_attempt_id", "version_id", mode="before")
    @classmethod
    def parse_wire_uuid(cls, value: object) -> object:
        return uuid.UUID(value) if isinstance(value, str) else value


class DurableArtifactBindRequestV3(StrictCamelModel):
    schema_version: Literal["authored-durable-artifact-bind/v3"] = "authored-durable-artifact-bind/v3"
    export_attempt_id: uuid.UUID
    expected_version_id: uuid.UUID
    expected_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @field_validator("export_attempt_id", "expected_version_id", mode="before")
    @classmethod
    def parse_wire_uuid(cls, value: object) -> object:
        return uuid.UUID(value) if isinstance(value, str) else value


class ComQualificationCreateRequestV3(StrictCamelModel):
    schema_version: Literal["authored-com-qualification-create/v3"] = "authored-com-qualification-create/v3"
    export_attempt_id: uuid.UUID
    evidence_policy_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @field_validator("export_attempt_id", mode="before")
    @classmethod
    def parse_wire_uuid(cls, value: object) -> object:
        return uuid.UUID(value) if isinstance(value, str) else value


class ComQualificationClaimRequestV3(StrictCamelModel):
    schema_version: Literal["authored-com-qualification-claim/v3"] = "authored-com-qualification-claim/v3"
    worker_id: str = Field(pattern=r"^[A-Za-z0-9._/-]{3,128}$")


class ComQualificationTransitionRequestV3(StrictCamelModel):
    schema_version: Literal["authored-com-qualification-transition/v3"] = "authored-com-qualification-transition/v3"
    worker_id: str = Field(pattern=r"^[A-Za-z0-9._/-]{3,128}$")
    lease_token: str = Field(pattern=r"^[A-Za-z0-9_-]{32,128}$")
    expected_state: Literal[
        "captured",
        "planned",
        "assembled",
        "inputOoxmlVerified",
        "firstOpenObserved",
        "savedCopyVerified",
        "reopenObserved",
        "comVerified",
    ]
    expected_transition_sequence: int = Field(ge=0)
    next_state: Literal[
        "planned",
        "assembled",
        "inputOoxmlVerified",
        "firstOpenObserved",
        "savedCopyVerified",
        "reopenObserved",
        "comVerified",
        "sealed",
        "rejected",
    ]
    evidence: dict[str, object] = Field(default_factory=dict)
    failure_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9._-]{1,127}$")
    authority: EvidenceIssuerEnvelopeV3

    @model_validator(mode="after")
    def qualification_worker_authority(self) -> "ComQualificationTransitionRequestV3":
        if (
            self.authority.evidence_class != "actual_product_com_qualification"
            or self.authority.issuer_id != self.worker_id
        ):
            raise ValueError("authored_com_qualification_worker_authority_invalid")
        return self


class ComQualificationJobV3(StrictCamelModel):
    schema_version: Literal["authored-com-qualification-job/v3"] = "authored-com-qualification-job/v3"
    job_id: uuid.UUID
    export_attempt_id: uuid.UUID
    evidence_policy_digest: str
    authority_domain: Literal["actual_product"]
    state: str
    transition_sequence: int
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    attempt_count: int
    evidence: dict[str, object]
    result_digest: str | None = None
    failure_code: str | None = None
    server_readback_digest: str | None = None
    readback_signer_key_id: str | None = None
    readback_signature_base64: str | None = None
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    execution_nonce: str | None = None


class EvidenceGraphRootCreateRequestV3(StrictCamelModel):
    schema_version: Literal["authored-evidence-graph-root-create/v3"] = "authored-evidence-graph-root-create/v3"
    presentation_id: uuid.UUID
    presentation_revision: int = Field(ge=1)
    export_attempt_id: uuid.UUID
    evidence_policy_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    active_manifest_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_tree_census_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_head_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    release_profile_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    feature_set_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    gate_minus1_manifest_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    gate_minus1_result_set_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    gate_minus1_receipt_artifact_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    gate_minus1_validator_contract_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    environment_attestation_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    worker_attestation_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    com_qualification_job_id: uuid.UUID
    com_qualification_result_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    base_artifact_ids: list[str] = Field(min_length=67, max_length=67)
    base_artifact_ids_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    final_required_artifact_ids_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    artifact_inventory: list[DurableArtifactRefV3] = Field(min_length=67, max_length=67)
    artifact_inventory_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    root_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    authority: EvidenceIssuerEnvelopeV3

    @field_validator("presentation_id", "export_attempt_id", "com_qualification_job_id", mode="before")
    @classmethod
    def parse_wire_uuid(cls, value: object) -> object:
        return uuid.UUID(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def graph_authority_and_bound_inventory(self) -> "EvidenceGraphRootCreateRequestV3":
        if self.authority.evidence_class != "actual_product_completion":
            raise ValueError("authored_evidence_graph_authority_invalid")
        if any(item.state != "bound" for item in self.artifact_inventory):
            raise ValueError("authored_evidence_graph_artifact_not_bound")
        required = list(GATE1_GRAPH_BASE_ARTIFACT_IDS_V3)
        inventory_ids = [item.artifact_id for item in self.artifact_inventory]
        if (
            self.base_artifact_ids != required
            or self.base_artifact_ids_digest != GATE1_GRAPH_BASE_ARTIFACT_IDS_DIGEST_V3
            or self.final_required_artifact_ids_digest != GATE1_REQUIRED_ARTIFACT_IDS_DIGEST_V3
            or len(set(inventory_ids)) != len(inventory_ids)
            or inventory_ids != required
        ):
            raise ValueError("authored_evidence_graph_required_artifacts_invalid")
        return self


class EvidenceGraphRootV3(StrictCamelModel):
    schema_version: Literal["authored-evidence-graph-root/v3"] = "authored-evidence-graph-root/v3"
    graph_root_id: uuid.UUID
    persisted_record_id: uuid.UUID
    presentation_id: uuid.UUID
    presentation_revision: int
    export_attempt_id: uuid.UUID
    request_digest: str
    evidence_policy_digest: str
    active_manifest_digest: str
    required_pin_digest: str
    base_artifact_ids: list[str]
    base_artifact_ids_digest: str
    final_required_artifact_ids_digest: str
    artifact_inventory_digest: str
    root_digest: str
    com_qualification_job_id: uuid.UUID
    com_qualification_state: Literal["sealed"]
    com_qualification_result_digest: str
    server_readback_digest: str | None = None
    readback_signer_key_id: str | None = None
    readback_signature_base64: str | None = None
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    execution_nonce: str | None = None


class EvidenceBundleCompletionCreateRequestV3(StrictCamelModel):
    schema_version: Literal["authored-evidence-bundle-completion-create/v3"] = (
        "authored-evidence-bundle-completion-create/v3"
    )
    graph_root_id: uuid.UUID
    required_capability_ids: list[str] = Field(min_length=1, max_length=128)

    @field_validator("graph_root_id", mode="before")
    @classmethod
    def parse_wire_uuid(cls, value: object) -> object:
        return uuid.UUID(value) if isinstance(value, str) else value


class EvidenceBundleCompletionSealRequestV3(StrictCamelModel):
    schema_version: Literal["authored-evidence-bundle-completion-seal/v3"] = (
        "authored-evidence-bundle-completion-seal/v3"
    )
    expected_cas_version: int = Field(ge=0)
    receipt_digests: list[str] = Field(min_length=1, max_length=128)
    completion_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    authority: EvidenceIssuerEnvelopeV3

    @model_validator(mode="after")
    def completion_authority(self) -> "EvidenceBundleCompletionSealRequestV3":
        if self.authority.evidence_class != "actual_product_completion":
            raise ValueError("authored_evidence_completion_authority_invalid")
        return self


class EvidenceBundleCompletionV3(StrictCamelModel):
    schema_version: Literal["authored-evidence-bundle-completion/v3"] = "authored-evidence-bundle-completion/v3"
    completion_id: uuid.UUID
    persisted_record_id: uuid.UUID
    graph_root_id: uuid.UUID
    graph_root_digest: str
    export_attempt_id: uuid.UUID
    state: Literal["pending", "sealed"]
    cas_version: int
    required_capability_ids: list[str]
    receipt_digests: list[str]
    completion_digest: str
    create_request_digest: str
    seal_request_digest: str | None = None
    receipt_identities: list[dict[str, object]]
    sealed_at: datetime | None = None
    server_readback_digest: str | None = None
    readback_signer_key_id: str | None = None
    readback_signature_base64: str | None = None
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    execution_nonce: str | None = None


class Gate0StructuralMutationV1(StrictCamelModel):
    json_paths: list[str] = Field(min_length=1, max_length=16)
    operator: str = Field(min_length=1, max_length=128)
    phase: str | None = Field(default=None, max_length=128)


class Gate0StructuralCaseExecutionV1(StrictCamelModel):
    schema_version: Literal["authored-gate0-structural-case-execution/v1"] = (
        "authored-gate0-structural-case-execution/v1"
    )
    run_id: uuid.UUID
    manifest_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    case_id: str = Field(pattern=r"^g0\.[a-z0-9.-]+$")
    ordinal: int = Field(ge=1, le=34)
    mutation: Gate0StructuralMutationV1 | None = None

    @field_validator("run_id", mode="before")
    @classmethod
    def parse_wire_uuid(cls, value: object) -> object:
        return uuid.UUID(value) if isinstance(value, str) else value


__all__ = [
    "CapabilityReceiptIssueRequestV3",
    "ComQualificationClaimRequestV3",
    "ComQualificationCreateRequestV3",
    "ComQualificationJobV3",
    "ComQualificationTransitionRequestV3",
    "DurableArtifactPutRequestV3",
    "DurableArtifactBindRequestV3",
    "DurableArtifactRefV3",
    "EvidenceIssuerEnvelopeV3",
    "EvidenceGraphRootCreateRequestV3",
    "EvidenceGraphRootV3",
    "EvidenceBundleCompletionCreateRequestV3",
    "EvidenceBundleCompletionSealRequestV3",
    "EvidenceBundleCompletionV3",
    "Gate0StructuralCaseExecutionV1",
    "GATE1_REQUIRED_ARTIFACT_IDS_DIGEST_V3",
    "GATE1_REQUIRED_ARTIFACT_IDS_V3",
    "GATE1_GRAPH_BASE_ARTIFACT_IDS_DIGEST_V3",
    "GATE1_GRAPH_BASE_ARTIFACT_IDS_V3",
]
