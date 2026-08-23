"""Signed external evidence admitted by the bounded-Hybrid release profile."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
import uuid

from pydantic import Field, field_validator, model_validator

from models.authored_vnext.master.base import Digest, StrictCamelModel


_REQUIRED_PIPELINE_STEPS = (
    "Authored release readiness configuration",
    "Run shared Next.js CI Node suite",
    "Workspace chat-first browser E2E",
)


class BitbucketRequiredChecksReceiptV3(StrictCamelModel):
    schema_version: Literal["authored-bitbucket-required-check-receipt/v1"]
    repository: Literal["ibizsoftware/devax-ppt"]
    commit: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    pipeline_uuid: str = Field(min_length=3, max_length=128)
    protected_branches: list[str] = Field(min_length=1, max_length=16)
    required_pipeline_steps: list[str] = Field(min_length=1, max_length=64)
    successful_commit_status_keys: list[str] = Field(min_length=1, max_length=128)
    verified_at: datetime
    receipt_digest: Digest
    authority_signature: str = Field(pattern=r"^hmac-sha256:[0-9a-f]{64}$")

    @field_validator("verified_at", mode="before")
    @classmethod
    def parse_wire_datetime(cls, value: object) -> object:
        return (
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            if isinstance(value, str)
            else value
        )

    @field_validator("pipeline_uuid")
    @classmethod
    def nonblank_pipeline_uuid(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("authored_ci_pipeline_uuid_invalid")
        return normalized

    @model_validator(mode="after")
    def canonical_required_checks(self) -> "BitbucketRequiredChecksReceiptV3":
        if (
            self.protected_branches != ["main"]
            or tuple(self.required_pipeline_steps) != tuple(sorted(_REQUIRED_PIPELINE_STEPS))
            or self.successful_commit_status_keys
            != sorted(set(self.successful_commit_status_keys))
            or any(not item.strip() for item in self.successful_commit_status_keys)
        ):
            raise ValueError("authored_ci_required_checks_claim_invalid")
        return self


class CiEvidenceArtifactPublishRequestV3(StrictCamelModel):
    schema_version: Literal["authored-ci-evidence-artifact-publish/v3"] = (
        "authored-ci-evidence-artifact-publish/v3"
    )
    receipt: BitbucketRequiredChecksReceiptV3


class CiEvidenceArtifactV3(StrictCamelModel):
    schema_version: Literal["authored-ci-evidence-artifact/v3"] = (
        "authored-ci-evidence-artifact/v3"
    )
    evidence_bundle_id: uuid.UUID
    artifact_ref: str = Field(pattern=r"^artifact://sha256/[0-9a-f]{64}$")
    artifact_digest: Digest
    receipt_digest: Digest
    byte_length: int = Field(ge=1)


__all__ = [
    "BitbucketRequiredChecksReceiptV3",
    "CiEvidenceArtifactPublishRequestV3",
    "CiEvidenceArtifactV3",
]
