"""Signed external evidence admitted by the bounded-Hybrid release profile."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal
import uuid

from pydantic import Field, field_validator, model_validator

from models.authored_vnext.master.base import Digest, StrictCamelModel
from models.authored_vnext.semantic.release_authority_v3 import EvidenceIssuerEnvelopeV3


_REQUIRED_PIPELINE_STEPS = (
    "Authored release readiness configuration",
    "Dashboard component gate",
    "Run shared Next.js CI Node suite",
    "Upstream byte-parity verification",
    "Workspace chat-first browser E2E",
)


class CiAttemptBindingV3(StrictCamelModel):
    schema_version: Literal["authored-ci-attempt-binding/v3"] = (
        "authored-ci-attempt-binding/v3"
    )
    presentation_id: uuid.UUID = Field(strict=False)
    presentation_revision: int = Field(ge=1)
    generation_attempt_id: uuid.UUID = Field(strict=False)
    fidelity_export_attempt_id: uuid.UUID = Field(strict=False)
    hybrid_export_attempt_id: uuid.UUID = Field(strict=False)
    active_manifest_digest: Digest
    source_tree_census_digest: Digest
    source_head_commit: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    evidence_policy_digest: Digest
    execution_nonce: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class BitbucketRequiredChecksReceiptV3(StrictCamelModel):
    schema_version: Literal["authored-bitbucket-required-check-receipt/v1"]
    repository: Literal["ibizsoftware/devax-ppt"]
    commit: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    pipeline_uuid: str = Field(min_length=3, max_length=128)
    protected_branches: list[str] = Field(min_length=1, max_length=16)
    required_pipeline_steps: list[str] = Field(min_length=1, max_length=64)
    successful_step_names: list[str] = Field(min_length=5, max_length=5)
    pagination_complete: Literal[True]
    latest_state_per_step_successful: Literal[True]
    successful_commit_status_keys: list[str] = Field(min_length=1, max_length=128)
    status: Literal["verified"]
    verified_at: datetime
    expires_at: datetime
    attempt_binding: CiAttemptBindingV3
    receipt_digest: Digest
    authority_signature: str = Field(pattern=r"^hmac-sha256:[0-9a-f]{64}$")

    @field_validator("verified_at", "expires_at", mode="before")
    @classmethod
    def parse_wire_datetime(cls, value: object) -> object:
        return (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            if isinstance(value, str)
            else value
        )

    @field_validator("verified_at", "expires_at")
    @classmethod
    def canonical_second_utc_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None or value.microsecond != 0:
            raise ValueError("authored_ci_timestamp_not_canonical_second")
        return value.astimezone(timezone.utc)

    @field_validator("pipeline_uuid")
    @classmethod
    def nonblank_pipeline_uuid(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("authored_ci_pipeline_uuid_invalid")
        return normalized

    @model_validator(mode="after")
    def canonical_required_checks(self) -> "BitbucketRequiredChecksReceiptV3":
        verified_at = (
            self.verified_at.astimezone(timezone.utc)
            if self.verified_at.tzinfo
            else self.verified_at.replace(tzinfo=timezone.utc)
        )
        expires_at = (
            self.expires_at.astimezone(timezone.utc)
            if self.expires_at.tzinfo
            else self.expires_at.replace(tzinfo=timezone.utc)
        )
        if (
            self.protected_branches != ["main"]
            or tuple(self.required_pipeline_steps) != tuple(sorted(_REQUIRED_PIPELINE_STEPS))
            or tuple(self.successful_step_names) != tuple(sorted(_REQUIRED_PIPELINE_STEPS))
            or self.successful_commit_status_keys
            != sorted(set(self.successful_commit_status_keys))
            or any(not item.strip() for item in self.successful_commit_status_keys)
            or self.attempt_binding.source_head_commit != self.commit
            or expires_at <= verified_at
            or expires_at - verified_at > timedelta(minutes=15)
        ):
            raise ValueError("authored_ci_required_checks_claim_invalid")
        return self


class CiEvidenceArtifactPublishRequestV3(StrictCamelModel):
    schema_version: Literal["authored-ci-evidence-artifact-publish/v3"] = (
        "authored-ci-evidence-artifact-publish/v3"
    )
    receipt: BitbucketRequiredChecksReceiptV3
    authority: EvidenceIssuerEnvelopeV3

    @model_validator(mode="after")
    def actual_ci_authority(self) -> "CiEvidenceArtifactPublishRequestV3":
        receipt_expires_at = (
            self.receipt.expires_at.astimezone(timezone.utc)
            if self.receipt.expires_at.tzinfo
            else self.receipt.expires_at.replace(tzinfo=timezone.utc)
        )
        authority_expires_at = (
            self.authority.expires_at.astimezone(timezone.utc)
            if self.authority.expires_at.tzinfo
            else self.authority.expires_at.replace(tzinfo=timezone.utc)
        )
        if (
            self.authority.evidence_class != "actual_product_ci"
            or receipt_expires_at > authority_expires_at
        ):
            raise ValueError("authored_ci_evidence_authority_class_invalid")
        return self


class CiEvidenceArtifactV3(StrictCamelModel):
    schema_version: Literal["authored-ci-evidence-artifact/v3"] = (
        "authored-ci-evidence-artifact/v3"
    )
    evidence_bundle_id: uuid.UUID
    artifact_ref: str = Field(pattern=r"^artifact://sha256/[0-9a-f]{64}$")
    artifact_digest: Digest
    receipt_digest: Digest
    authority_digest: Digest
    byte_length: int = Field(ge=1)
    repository: Literal["ibizsoftware/devax-ppt"]
    commit: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    pipeline_uuid: str = Field(min_length=3, max_length=128)
    verified_at: datetime
    state: Literal["sealed"] = "sealed"
    resolver_version: Literal["authored-ci-evidence/v3"] = "authored-ci-evidence/v3"
    publish_request_digest: Digest
    attempt_binding: CiAttemptBindingV3
    server_readback_digest: Digest | None = None
    readback_signer_key_id: str | None = None
    readback_signature_base64: str | None = None
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    execution_nonce: str | None = None


__all__ = [
    "BitbucketRequiredChecksReceiptV3",
    "CiAttemptBindingV3",
    "CiEvidenceArtifactPublishRequestV3",
    "CiEvidenceArtifactV3",
]
