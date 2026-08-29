"""Pydantic contracts for immutable export-evidence and release receipts."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _canonical_sidecar_sha256(value: dict[str, Any]) -> str:
    return sha256(
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


class ExportEvidenceSidecarPointer(BaseModel):
    """Single atomic pointer to one immutable quality/coverage generation."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    schema_version: Literal["presenton.hybrid-evidence-pointer/v1"]
    pair_digest: str
    quality: dict[str, str]
    coverage: dict[str, str]

    @model_validator(mode="after")
    def validate_pair(self) -> "ExportEvidenceSidecarPointer":
        for member in (self.quality, self.coverage):
            if set(member) != {"file", "sha256"}:
                raise ValueError("export evidence pointer member is invalid")
            filename = member["file"]
            digest = member["sha256"].lower()
            if Path(filename).name != filename or not _is_sha256(digest):
                raise ValueError("export evidence pointer member is invalid")
            member["sha256"] = digest
        expected = sha256(
            f"{self.quality['sha256']}:{self.coverage['sha256']}".encode("ascii")
        ).hexdigest()
        if not _is_sha256(self.pair_digest) or self.pair_digest.lower() != expected:
            raise ValueError("export evidence pointer digest is inconsistent")
        self.pair_digest = expected
        return self


class ExportUnifiedReleaseReceipt(BaseModel):
    """Final export receipt separating artifact success from release readiness."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    schema_version: Literal["presenton.unified-export-release-receipt/v1"]
    requested_release_profile: str = Field(min_length=1, max_length=64)
    requested_release_policy_digest: str
    generation_receipt_digest: str
    export_binding: dict[str, Any]
    artifact_succeeded: Literal[True]
    release_ready: bool
    require_source_readback: bool
    require_visual_review: bool
    require_performance: bool
    require_native_coverage: bool
    minimum_native_coverage_ratio: float = Field(ge=0.0, le=1.0)
    source_readback_status: str = Field(min_length=1, max_length=64)
    visual_parity_status: Literal["preflight", "not-verified", "passed", "failed"]
    performance_status: str = Field(min_length=1, max_length=64)
    native_coverage_status: str = Field(min_length=1, max_length=64)
    native_coverage_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    powerpoint_qa_status: Literal["not-requested", "passed", "failed"]
    blockers: list[str] = Field(max_length=16)
    receipt_digest: str

    @model_validator(mode="after")
    def validate_final_release_formula(self) -> "ExportUnifiedReleaseReceipt":
        for digest in (
            self.requested_release_policy_digest,
            self.generation_receipt_digest,
            self.receipt_digest,
        ):
            if not digest.startswith("sha256:") or not _is_sha256(digest[7:]):
                raise ValueError("unified release receipt digest is invalid")
        expected_blockers: list[str] = []
        if self.require_source_readback and self.source_readback_status != "passed":
            expected_blockers.append("source-readback-not-passed")
        if self.require_visual_review and self.visual_parity_status != "passed":
            expected_blockers.append("visual-parity-not-passed")
        if self.require_performance and self.performance_status != "passed":
            expected_blockers.append("performance-not-passed")
        if self.require_native_coverage and (
            self.native_coverage_status != "passed"
            or self.native_coverage_ratio is None
            or self.native_coverage_ratio < self.minimum_native_coverage_ratio
        ):
            expected_blockers.append("native-coverage-not-passed")
        if any((self.require_visual_review, self.require_native_coverage)) and self.powerpoint_qa_status != "passed":
            expected_blockers.append("powerpoint-qa-not-passed")
        if self.blockers != expected_blockers:
            raise ValueError("unified release receipt blockers are inconsistent")
        if self.release_ready != (self.artifact_succeeded and not expected_blockers):
            raise ValueError("unified release readiness is inconsistent")
        payload = self.model_dump(mode="json", by_alias=True, exclude={"receipt_digest"})
        expected_digest = "sha256:" + _canonical_sidecar_sha256(payload)
        if self.receipt_digest != expected_digest:
            raise ValueError("unified release receipt digest is inconsistent")
        return self
