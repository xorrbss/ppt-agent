"""Strict bounded-Hybrid release profile and readiness contracts (v3)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
import uuid

from pydantic import Field, model_validator

from models.authored_vnext.master.base import Digest, StrictCamelModel
from models.authored_vnext.semantic.readiness import (
    CapabilityProjectionEntryV2,
    ExportAuthorityClaimV2,
    RendererAttestationProjectionV2,
    VisualEvidencePolicyV2,
)


ReadinessFieldV3 = Literal[
    "deckEvidenceReady",
    "serializerProfileReady",
    "releaseCommitReady",
]
_READINESS_FIELDS: tuple[ReadinessFieldV3, ...] = (
    "deckEvidenceReady",
    "serializerProfileReady",
    "releaseCommitReady",
)
_BOUNDED_ARCHETYPES = (
    "boundary",
    "chart",
    "diagram",
    "image-crop",
    "long-text",
    "mixed-css",
    "multi-column",
    "table",
)
_DECK_EVIDENCE_CAPABILITIES = ("evidence.visual",)
_SERIALIZER_PROFILE_CAPABILITIES = (
    "layout.flex",
    "layout.grid",
    "layout.transform",
    "receipt.degradation",
    "renderer.attestation",
    "shape.borderRadius",
    "shape.clip",
    "shape.gradient",
    "shape.opacity",
    "shape.shadow",
)
_UNSUPPORTED_NATIVE_CAPABILITIES = (
    "receipt.chart",
    "receipt.cjk",
    "receipt.imageCrop",
    "receipt.table",
    "receipt.zBand",
)
_NATIVE_SUBSET_CAPABILITIES: tuple[str, ...] = ()
_RELEASE_COMMIT_CAPABILITIES = ("ci.requiredChecks",)
_ALL_REQUIRED_CAPABILITIES = tuple(
    sorted(
        set(_DECK_EVIDENCE_CAPABILITIES)
        | set(_SERIALIZER_PROFILE_CAPABILITIES)
        | set(_NATIVE_SUBSET_CAPABILITIES)
        | set(_RELEASE_COMMIT_CAPABILITIES)
    )
)


class BoundedHybridPlatformScopeV3(StrictCamelModel):
    platform: Literal["windows-x64"]
    renderer: Literal["presentation-export"]
    serializer_profile_id: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,127}$")
    serializer_version: Literal["2"]
    archetypes: list[str] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def canonical_archetypes(self) -> "BoundedHybridPlatformScopeV3":
        if tuple(self.archetypes) != _BOUNDED_ARCHETYPES:
            raise ValueError("authored_release_v3_archetypes_invalid")
        return self


class BoundedHybridReleaseProfileDefinitionV3(StrictCamelModel):
    schema_version: Literal[
        "authored-bounded-hybrid-release-profile-definition/v3"
    ]
    profile_id: Literal["authored-windows-powerpoint-hybrid-v1"]
    release_kind: Literal["boundedHybrid"]
    platform_scope: BoundedHybridPlatformScopeV3
    promotion_readiness_fields: list[ReadinessFieldV3] = Field(
        min_length=3, max_length=3
    )
    deck_evidence_capability_ids: list[str] = Field(min_length=1, max_length=128)
    serializer_profile_capability_ids: list[str] = Field(
        min_length=1, max_length=128
    )
    native_subset_status: Literal["notApplicable"]
    native_subset_capability_ids: list[str] = Field(default_factory=list, max_length=128)
    unsupported_native_capability_ids: list[str] = Field(min_length=1, max_length=128)
    release_commit_capability_ids: list[str] = Field(min_length=1, max_length=128)
    all_required_capability_ids: list[str] = Field(min_length=1, max_length=128)
    renderer_attestation_capability_id: Literal["renderer.attestation"]
    visual_evidence_capability_id: Literal["evidence.visual"]
    visual_evidence_policy: VisualEvidencePolicyV2
    blocker_policy: Literal["requireNone"]
    kill_switch_policy: Literal["requireAllInactive"]
    deployment_authorized_aggregation: Literal["excluded"]
    full_native_parity_status: Literal["notApplicable"]
    independent_vnext_generation_status: Literal["notApplicable"]

    @property
    def serializer_version(self) -> str:
        """Compatibility seam for the stable capability-receipt v2 protocol."""

        return self.platform_scope.serializer_version

    @model_validator(mode="after")
    def exact_bounded_formula_definition(
        self,
    ) -> "BoundedHybridReleaseProfileDefinitionV3":
        if tuple(self.promotion_readiness_fields) != _READINESS_FIELDS:
            raise ValueError("authored_release_v3_readiness_fields_invalid")
        capability_sets = (
            (self.deck_evidence_capability_ids, _DECK_EVIDENCE_CAPABILITIES),
            (
                self.serializer_profile_capability_ids,
                _SERIALIZER_PROFILE_CAPABILITIES,
            ),
            (self.native_subset_capability_ids, _NATIVE_SUBSET_CAPABILITIES),
            (self.unsupported_native_capability_ids, _UNSUPPORTED_NATIVE_CAPABILITIES),
            (self.release_commit_capability_ids, _RELEASE_COMMIT_CAPABILITIES),
            (self.all_required_capability_ids, _ALL_REQUIRED_CAPABILITIES),
        )
        if any(tuple(actual) != expected for actual, expected in capability_sets):
            raise ValueError("authored_release_v3_capability_sets_invalid")
        required_union = set().union(
            self.deck_evidence_capability_ids,
            self.serializer_profile_capability_ids,
            self.native_subset_capability_ids,
            self.release_commit_capability_ids,
        )
        if (
            not set(self.native_subset_capability_ids).issubset(
                self.serializer_profile_capability_ids
            )
            or required_union != set(self.all_required_capability_ids)
            or any(actual != sorted(set(actual)) for actual, _ in capability_sets)
            or self.renderer_attestation_capability_id
            not in self.serializer_profile_capability_ids
            or set(self.unsupported_native_capability_ids)
            & set(self.all_required_capability_ids)
            or self.visual_evidence_capability_id
            not in self.deck_evidence_capability_ids
            or self.visual_evidence_policy.required_archetypes
            != self.platform_scope.archetypes
        ):
            raise ValueError("authored_release_v3_capability_invariants_invalid")
        return self


class BoundedHybridReleaseBlockerV3(StrictCamelModel):
    blocker_id: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,127}$")
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,127}$")


class BoundedHybridKillSwitchV3(StrictCamelModel):
    capability_id: str = Field(pattern=r"^[a-z][A-Za-z0-9._-]{1,127}$")
    active: bool
    reason_code: str | None = Field(
        default=None, pattern=r"^[a-z][a-z0-9._-]{1,127}$"
    )

    @model_validator(mode="after")
    def truthful_state(self) -> "BoundedHybridKillSwitchV3":
        if self.active != (self.reason_code is not None):
            raise ValueError("authored_release_v3_kill_switch_state_invalid")
        return self


class BoundedHybridReleaseReadinessV3(StrictCamelModel):
    schema_version: Literal["authored-bounded-hybrid-release-readiness/v3"] = (
        "authored-bounded-hybrid-release-readiness/v3"
    )
    profile_id: Literal["authored-windows-powerpoint-hybrid-v1"]
    platform_scope: BoundedHybridPlatformScopeV3
    deck_evidence_capability_ids: list[str] = Field(min_length=1, max_length=128)
    serializer_profile_capability_ids: list[str] = Field(
        min_length=1, max_length=128
    )
    native_subset_status: Literal["notApplicable"]
    native_subset_capability_ids: list[str] = Field(default_factory=list, max_length=128)
    unsupported_native_capability_ids: list[str] = Field(min_length=1, max_length=128)
    release_commit_capability_ids: list[str] = Field(min_length=1, max_length=128)
    all_required_capability_ids: list[str] = Field(min_length=1, max_length=128)
    profile_definition_digest: Digest
    evaluated_at: datetime
    expires_at: datetime
    full_native_parity_status: Literal["notApplicable"] = "notApplicable"
    independent_vnext_generation_status: Literal["notApplicable"] = (
        "notApplicable"
    )
    deck_evidence_ready: bool
    serializer_profile_ready: bool
    native_subset_ready: bool
    release_commit_ready: bool
    deck_evidence_receipt_digests: list[Digest] = Field(max_length=128)
    serializer_profile_receipt_digests: list[Digest] = Field(max_length=128)
    native_subset_receipt_digests: list[Digest] = Field(max_length=128)
    release_commit_receipt_digests: list[Digest] = Field(max_length=128)
    promotion_receipt_digests: list[Digest] = Field(max_length=128)
    blockers: list[BoundedHybridReleaseBlockerV3] = Field(
        default_factory=list, max_length=128
    )
    kill_switches: list[BoundedHybridKillSwitchV3] = Field(
        default_factory=list, max_length=128
    )
    deployment_authorized: bool
    promotion_ready: bool
    reason_code: Literal[
        "readiness_incomplete",
        "release_blocked",
        "release_kill_switch_active",
        "promotion_formula_satisfied",
    ]

    @model_validator(mode="after")
    def truthful_bounded_promotion(self) -> "BoundedHybridReleaseReadinessV3":
        if self.expires_at <= self.evaluated_at:
            raise ValueError("authored_release_v3_expiry_invalid")
        capability_sets = (
            (self.deck_evidence_capability_ids, _DECK_EVIDENCE_CAPABILITIES),
            (
                self.serializer_profile_capability_ids,
                _SERIALIZER_PROFILE_CAPABILITIES,
            ),
            (self.native_subset_capability_ids, _NATIVE_SUBSET_CAPABILITIES),
            (self.unsupported_native_capability_ids, _UNSUPPORTED_NATIVE_CAPABILITIES),
            (self.release_commit_capability_ids, _RELEASE_COMMIT_CAPABILITIES),
            (self.all_required_capability_ids, _ALL_REQUIRED_CAPABILITIES),
        )
        if any(tuple(actual) != expected for actual, expected in capability_sets):
            raise ValueError(
                "authored_release_v3_projection_capability_sets_invalid"
            )
        blocker_ids = [item.blocker_id for item in self.blockers]
        switch_ids = [item.capability_id for item in self.kill_switches]
        if len(blocker_ids) != len(set(blocker_ids)) or len(switch_ids) != len(
            set(switch_ids)
        ):
            raise ValueError("authored_release_v3_authority_duplicate")
        readiness_complete = all(
            (
                self.deck_evidence_ready,
                self.serializer_profile_ready,
                self.release_commit_ready,
            )
        )
        active_kill_switch = any(item.active for item in self.kill_switches)
        readiness_receipts = (
            (
                self.deck_evidence_ready,
                self.deck_evidence_receipt_digests,
                _DECK_EVIDENCE_CAPABILITIES,
            ),
            (
                self.serializer_profile_ready,
                self.serializer_profile_receipt_digests,
                _SERIALIZER_PROFILE_CAPABILITIES,
            ),
            (
                False,
                self.native_subset_receipt_digests,
                _NATIVE_SUBSET_CAPABILITIES,
            ),
            (
                self.release_commit_ready,
                self.release_commit_receipt_digests,
                _RELEASE_COMMIT_CAPABILITIES,
            ),
        )
        if any(
            (ready and len(receipts) != len(capabilities))
            or (not ready and bool(receipts))
            or len(receipts) != len(set(receipts))
            for ready, receipts, capabilities in readiness_receipts
        ):
            raise ValueError("authored_release_v3_receipt_claim_invalid")
        if self.native_subset_ready or self.native_subset_receipt_digests:
            raise ValueError("authored_release_v3_native_subset_not_applicable")
        receipts_by_capability: dict[str, str] = {}
        for ready, receipts, capabilities in readiness_receipts:
            if not ready:
                continue
            for capability_id, receipt_digest in zip(
                capabilities, receipts, strict=True
            ):
                existing = receipts_by_capability.get(capability_id)
                if existing is not None and existing != receipt_digest:
                    raise ValueError("authored_release_v3_receipt_pin_mismatch")
                receipts_by_capability[capability_id] = receipt_digest
        if len(set(receipts_by_capability.values())) != len(
            receipts_by_capability
        ):
            raise ValueError("authored_release_v3_receipt_digest_reused")
        expected_promotion = (
            readiness_complete and not self.blockers and not active_kill_switch
        )
        if self.promotion_ready != expected_promotion:
            raise ValueError("authored_release_v3_promotion_formula_invalid")
        expected_promotion_receipts = (
            [
                receipts_by_capability[capability_id]
                for capability_id in _ALL_REQUIRED_CAPABILITIES
            ]
            if expected_promotion
            else []
        )
        if (
            self.promotion_receipt_digests != expected_promotion_receipts
            or len(self.promotion_receipt_digests)
            != len(set(self.promotion_receipt_digests))
        ):
            raise ValueError("authored_release_v3_promotion_receipts_invalid")
        expected_reason = (
            "readiness_incomplete"
            if not readiness_complete
            else (
                "release_blocked"
                if self.blockers
                else (
                    "release_kill_switch_active"
                    if active_kill_switch
                    else "promotion_formula_satisfied"
                )
            )
        )
        if self.reason_code != expected_reason:
            raise ValueError("authored_release_v3_reason_code_invalid")
        return self


class WorkbenchCapabilityProjectionV3(StrictCamelModel):
    """Read-only product projection; it never acts as a runtime enable input."""

    schema_version: Literal["workbench-capability-projection/v3"] = (
        "workbench-capability-projection/v3"
    )
    presentation_id: uuid.UUID
    canonical_mode: Literal["legacyHtml", "bundleGraph"]
    presentation_revision: int = Field(ge=0)
    canonical_digest: Digest | None = None
    manifest_digest: Digest | None = None
    degraded_fidelity_only: bool
    release_profile: BoundedHybridReleaseReadinessV3
    capabilities: list[CapabilityProjectionEntryV2] = Field(
        min_length=1, max_length=128
    )
    renderer_attestation: RendererAttestationProjectionV2
    kill_switches: list[BoundedHybridKillSwitchV3] = Field(
        default_factory=list, max_length=128
    )
    release_authority_receipt_digests: list[Digest] = Field(
        default_factory=list, max_length=128
    )
    release_projection_digest: Digest | None = None
    export_authority_claim: ExportAuthorityClaimV2 | None = None
    projection_digest: Digest

    @model_validator(mode="after")
    def exact_release_claim(self) -> "WorkbenchCapabilityProjectionV3":
        claim = self.export_authority_claim
        if claim is None:
            if self.release_authority_receipt_digests or self.release_projection_digest:
                raise ValueError("authored_release_v3_unclaimed_authority_pins")
            return self
        profile = self.release_profile
        expected_receipts = (
            profile.promotion_receipt_digests
            if claim.kind == "promotion"
            else profile.native_subset_receipt_digests
        )
        if (
            claim.profile_id != profile.profile_id
            or claim.profile_definition_digest != profile.profile_definition_digest
            or claim.projection_digest != self.projection_digest
            or claim.receipt_digests != expected_receipts
            or self.release_authority_receipt_digests != expected_receipts
            or self.release_projection_digest != self.projection_digest
            or (claim.kind == "promotion" and not profile.promotion_ready)
            or (claim.kind == "boundedNative" and not profile.native_subset_ready)
        ):
            raise ValueError("authored_release_v3_export_claim_invalid")
        return self


__all__ = [name for name in globals() if name.endswith("V3")]
