"""Versioned release-readiness and durable capability receipt contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
import uuid

from pydantic import Field, model_validator

from models.authored_vnext.master.base import Digest, StrictCamelModel
from models.authored_vnext.semantic.contracts import (
    ExportAttemptDigestBindingV2,
    KillSwitchProjectionV1,
)


CapabilityStateV2 = Literal["disabled", "shadow", "ready", "blocked"]


class CapabilityScopeV2(StrictCamelModel):
    platform: str = Field(min_length=1, max_length=128)
    renderer: str = Field(min_length=1, max_length=128)
    archetypes: list[str] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def canonical_archetypes(self) -> "CapabilityScopeV2":
        if self.archetypes != sorted(set(self.archetypes)):
            raise ValueError("authored_capability_scope_archetypes_not_canonical")
        return self


class CapabilityEnvironmentV2(StrictCamelModel):
    deployment_environment: Literal["development", "staging", "production"]
    os_build: str = Field(min_length=1, max_length=128)
    office_build: str = Field(min_length=1, max_length=128)
    renderer_image_digest: Digest
    font_inventory_digest: Digest
    backend_build_digest: Digest


class VisualEvidencePolicyV2(StrictCamelModel):
    policy_id: str = Field(pattern=r"^[a-z][a-z0-9._/-]{1,127}$")
    report_schema_version: Literal["authored-vnext-visual-parity-gate-report/v1"]
    receipt_ttl_seconds: int = Field(gt=0, le=31 * 24 * 60 * 60)
    required_status: Literal["passed"]
    required_visual_parity: Literal["passed"]
    required_languages: list[str] = Field(min_length=1, max_length=32)
    required_archetypes: list[str] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def canonical_sets(self) -> "VisualEvidencePolicyV2":
        if self.required_languages != sorted(set(self.required_languages)) or (
            self.required_archetypes != sorted(set(self.required_archetypes))
        ):
            raise ValueError("authored_release_visual_policy_sets_not_canonical")
        return self


class ReleaseProfileDefinitionV2(StrictCamelModel):
    schema_version: Literal["authored-release-profile-definition/v2"]
    profile_id: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,127}$")
    platform_scope: CapabilityScopeV2
    serializer_version: str = Field(min_length=1, max_length=128)
    promotion_capability_ids: list[str] = Field(min_length=1, max_length=128)
    bounded_native_capability_ids: list[str] = Field(min_length=1, max_length=128)
    renderer_attestation_capability_id: str
    visual_evidence_capability_id: str
    visual_evidence_policy: VisualEvidencePolicyV2

    @model_validator(mode="after")
    def valid_capability_sets(self) -> "ReleaseProfileDefinitionV2":
        promotion = self.promotion_capability_ids
        bounded = self.bounded_native_capability_ids
        if len(promotion) != len(set(promotion)) or len(bounded) != len(set(bounded)):
            raise ValueError("authored_release_profile_capability_duplicate")
        if not set(promotion).issubset(bounded) or {
            self.renderer_attestation_capability_id,
            self.visual_evidence_capability_id,
        }.difference(promotion):
            raise ValueError("authored_release_profile_capability_sets_invalid")
        return self


class VisualParityGateIdentityV1(StrictCamelModel):
    gate_id: str = Field(min_length=1, max_length=128)
    presentation_id: str = Field(min_length=1, max_length=128)
    presentation_revision: int = Field(ge=1)
    operation_id: str = Field(min_length=1, max_length=128)
    generation_attempt_id: str = Field(min_length=1, max_length=128)
    registry_digest: Digest
    semantic_proof: dict[str, Any]
    slide_count: int = Field(ge=1, le=500)
    modes: list[dict[str, Any]] = Field(min_length=3, max_length=3)
    diffs: dict[str, Any]
    ledger: list[dict[str, Any]] = Field(min_length=1, max_length=2048)
    release_authority: dict[str, Any]
    db_attempt_resolution: dict[str, Any]
    freshness: dict[str, Any]
    atomic_binding_digest: Digest


class VisualParityGateReportV1(StrictCamelModel):
    schema_version: Literal["authored-vnext-visual-parity-gate-report/v1"]
    status: Literal["passed", "blocked"]
    promotion_ready: Literal[False]
    visual_parity: Literal["passed", "not-verified"]
    languages: list[str] = Field(min_length=1, max_length=32)
    archetypes: list[str] = Field(min_length=1, max_length=128)
    identity: VisualParityGateIdentityV1
    evidence_digest: Digest
    trusted_issuer: Literal["authored-visual-parity-gate/v1"] = (
        "authored-visual-parity-gate/v1"
    )
    authority_signature: str | None = Field(
        default=None, pattern=r"^hmac-sha256:[0-9a-f]{64}$"
    )


class VisualEvidenceBindingV2(StrictCamelModel):
    presentation_id: uuid.UUID
    presentation_revision: int = Field(ge=1)
    manifest_digest: Digest
    generation_attempt_id: uuid.UUID
    export_attempt_id: uuid.UUID
    report_artifact_ref: str = Field(pattern=r"^artifact://sha256/[0-9a-f]{64}$")
    report_artifact_digest: Digest


class VisualEvidenceAuthorityV2(StrictCamelModel):
    schema_version: Literal["authored-visual-evidence-authority/v2"] = (
        "authored-visual-evidence-authority/v2"
    )
    binding: VisualEvidenceBindingV2


class VisualEvidenceArtifactPublishRequestV2(StrictCamelModel):
    schema_version: Literal["authored-visual-evidence-artifact-publish/v2"] = (
        "authored-visual-evidence-artifact-publish/v2"
    )
    report: VisualParityGateReportV1


class VisualEvidenceArtifactV2(StrictCamelModel):
    schema_version: Literal["authored-visual-evidence-artifact/v2"] = (
        "authored-visual-evidence-artifact/v2"
    )
    presentation_id: uuid.UUID
    report_artifact_ref: str = Field(pattern=r"^artifact://sha256/[0-9a-f]{64}$")
    report_artifact_digest: Digest
    byte_length: int = Field(ge=1)


class CapabilityReceiptIssueRequestV2(StrictCamelModel):
    schema_version: Literal["authored-capability-receipt-issue/v2"] = (
        "authored-capability-receipt-issue/v2"
    )
    capability_id: str = Field(pattern=r"^[a-z][A-Za-z0-9._-]{1,127}$")
    state: CapabilityStateV2
    scope: CapabilityScopeV2
    environment: CapabilityEnvironmentV2
    serializer_id: str = Field(min_length=1, max_length=128)
    serializer_version: str = Field(min_length=1, max_length=128)
    feature_set_digest: Digest
    evidence_policy_id: str = Field(pattern=r"^[a-z][a-z0-9._/-]{1,127}$")
    policy_digest: Digest
    evidence_bundle_id: uuid.UUID
    evidence_digest: Digest
    expires_at: datetime
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,127}$")
    supersedes_receipt_digest: Digest | None = None
    visual_evidence_authority: VisualEvidenceAuthorityV2 | None = None


class CapabilityReceiptRevokeRequestV2(StrictCamelModel):
    schema_version: Literal["authored-capability-receipt-revoke/v2"] = (
        "authored-capability-receipt-revoke/v2"
    )
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,127}$")


class CapabilityReceiptV2(StrictCamelModel):
    schema_version: Literal["authored-capability-receipt/v2"] = (
        "authored-capability-receipt/v2"
    )
    capability_id: str = Field(pattern=r"^[a-z][A-Za-z0-9._-]{1,127}$")
    state: CapabilityStateV2
    scope: CapabilityScopeV2
    environment: CapabilityEnvironmentV2
    serializer_id: str = Field(min_length=1, max_length=128)
    serializer_version: str = Field(min_length=1, max_length=128)
    feature_set_digest: Digest
    evidence_policy_id: str = Field(pattern=r"^[a-z][a-z0-9._/-]{1,127}$")
    policy_digest: Digest
    evidence_bundle_id: uuid.UUID
    evidence_digest: Digest
    issuer_principal_id: uuid.UUID
    issuer_authority: Literal["authenticated-admin", "internal-server"]
    evaluated_at: datetime
    expires_at: datetime
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,127}$")
    transition_kind: Literal["issue", "supersede", "revoke"]
    supersedes_receipt_digest: Digest | None = None
    revokes_receipt_digest: Digest | None = None
    receipt_digest: Digest | None = None
    visual_evidence_authority: VisualEvidenceAuthorityV2 | None = None

    @model_validator(mode="after")
    def valid_authority_window_and_transition(self) -> "CapabilityReceiptV2":
        if self.expires_at <= self.evaluated_at:
            raise ValueError("authored_capability_receipt_expiry_invalid")
        expected = {
            "issue": (False, False),
            "supersede": (True, False),
            "revoke": (False, True),
        }[self.transition_kind]
        actual = (
            self.supersedes_receipt_digest is not None,
            self.revokes_receipt_digest is not None,
        )
        if actual != expected or (
            self.transition_kind == "revoke" and self.state != "blocked"
        ):
            raise ValueError("authored_capability_receipt_transition_invalid")
        return self


class CapabilityProjectionEntryV2(StrictCamelModel):
    capability_id: str = Field(pattern=r"^[a-z][A-Za-z0-9._-]{1,127}$")
    state: CapabilityStateV2
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,127}$")
    receipt_digest: Digest | None = None
    scope: CapabilityScopeV2
    serializer_id: str = Field(min_length=1, max_length=128)
    serializer_version: str = Field(min_length=1, max_length=128)
    expires_at: datetime | None = None


class RendererAttestationProjectionV2(StrictCamelModel):
    state: Literal["unattested", "attested", "expired", "blocked"]
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,127}$")
    attestation_digest: Digest | None = None
    environment: CapabilityEnvironmentV2
    expires_at: datetime | None = None


class ProductReadinessProjectionV2(StrictCamelModel):
    schema_version: Literal["authored-release-profile/v2"] = (
        "authored-release-profile/v2"
    )
    profile_id: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,127}$")
    platform_scope: CapabilityScopeV2
    evaluated_at: datetime
    expires_at: datetime
    promotion_ready: bool
    bounded_native_parity_ready: bool
    independent_generation_ready: Literal[False] = False
    profile_definition_digest: Digest
    promotion_capability_ids: list[str] = Field(min_length=1, max_length=128)
    required_capability_ids: list[str] = Field(min_length=1, max_length=128)
    bounded_native_capability_ids: list[str] = Field(min_length=1, max_length=128)
    ready_receipt_digests: list[Digest] = Field(max_length=128)
    promotion_receipt_digests: list[Digest] = Field(max_length=128)
    bounded_native_receipt_digests: list[Digest] = Field(max_length=128)
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,127}$")

    @model_validator(mode="after")
    def truthful_formula(self) -> "ProductReadinessProjectionV2":
        if self.required_capability_ids != self.promotion_capability_ids:
            raise ValueError("authored_release_profile_compatibility_alias_mismatch")
        if self.bounded_native_parity_ready and not self.promotion_ready:
            raise ValueError("authored_release_profile_formula_invalid")
        if self.promotion_ready != bool(self.promotion_receipt_digests) or (
            self.bounded_native_parity_ready
            != bool(self.bounded_native_receipt_digests)
        ):
            raise ValueError("authored_release_profile_receipt_claim_invalid")
        return self


class ExportAuthorityClaimV2(StrictCamelModel):
    schema_version: Literal["authored-export-authority-claim/v2"] = (
        "authored-export-authority-claim/v2"
    )
    kind: Literal["promotion", "boundedNative"]
    profile_id: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,127}$")
    profile_definition_digest: Digest
    projection_digest: Digest
    receipt_digests: list[Digest] = Field(min_length=1, max_length=128)
    state: Literal["ready"] = "ready"


class ReleasePinnedExportAttemptDigestBindingV2(ExportAttemptDigestBindingV2):
    """Strict V2 release pins layered over the existing semantic binding payload."""

    schema_version: Literal["authored-export-attempt-binding/v2"] = (
        "authored-export-attempt-binding/v2"
    )
    release_authority_receipt_digests: list[Digest] = Field(min_length=1, max_length=128)
    release_projection_digest: Digest
    export_authority_claim: ExportAuthorityClaimV2

    @model_validator(mode="after")
    def exact_claim_pins(self) -> "ReleasePinnedExportAttemptDigestBindingV2":
        if self.release_authority_receipt_digests != self.export_authority_claim.receipt_digests:
            raise ValueError("authored_export_authority_claim_pins_mismatch")
        if self.release_projection_digest != self.export_authority_claim.projection_digest:
            raise ValueError("authored_export_authority_claim_projection_mismatch")
        return self


class WorkbenchCapabilityProjectionV2(StrictCamelModel):
    schema_version: Literal["workbench-capability-projection/v2"] = (
        "workbench-capability-projection/v2"
    )
    presentation_id: uuid.UUID
    canonical_mode: Literal["legacyHtml", "bundleGraph"]
    presentation_revision: int = Field(ge=0)
    canonical_digest: Digest | None = None
    manifest_digest: Digest | None = None
    degraded_fidelity_only: bool
    release_profile: ProductReadinessProjectionV2
    capabilities: list[CapabilityProjectionEntryV2] = Field(min_length=1, max_length=128)
    renderer_attestation: RendererAttestationProjectionV2
    kill_switches: list[KillSwitchProjectionV1] = Field(default_factory=list, max_length=128)
    release_authority_receipt_digests: list[Digest] = Field(default_factory=list, max_length=128)
    release_projection_digest: Digest | None = None
    export_authority_claim: ExportAuthorityClaimV2 | None = None
    projection_digest: Digest | None = None

    @model_validator(mode="after")
    def exact_optional_release_claim(self) -> "WorkbenchCapabilityProjectionV2":
        claim = self.export_authority_claim
        if claim is None:
            if self.release_authority_receipt_digests:
                raise ValueError("authored_projection_release_claim_required")
            return self
        if (
            self.release_authority_receipt_digests != claim.receipt_digests
            or self.release_projection_digest != claim.projection_digest
            or self.projection_digest != claim.projection_digest
        ):
            raise ValueError("authored_projection_release_claim_mismatch")
        return self


__all__ = [name for name in globals() if name.endswith("V2")]
