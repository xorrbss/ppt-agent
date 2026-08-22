"""Versioned release-readiness and durable capability receipt contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
import uuid

from pydantic import Field, model_validator

from models.authored_vnext.master.base import Digest, StrictCamelModel
from models.authored_vnext.semantic.contracts import KillSwitchProjectionV1


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
    required_capability_ids: list[str] = Field(min_length=1, max_length=128)
    bounded_native_capability_ids: list[str] = Field(min_length=1, max_length=128)
    ready_receipt_digests: list[Digest] = Field(max_length=128)
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,127}$")

    @model_validator(mode="after")
    def truthful_formula(self) -> "ProductReadinessProjectionV2":
        if self.bounded_native_parity_ready and not self.promotion_ready:
            raise ValueError("authored_release_profile_formula_invalid")
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
    projection_digest: Digest | None = None


__all__ = [name for name in globals() if name.endswith("V2")]
