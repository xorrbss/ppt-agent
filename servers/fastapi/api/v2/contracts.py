"""Strict public wire contracts for asynchronous presentation work.

These models deliberately contain no owner identifiers, session identifiers,
local paths, object-store keys, prompts returned by providers, or other
execution-internal state.
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel


PUBLIC_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
_PUBLIC_ID_RE = re.compile(PUBLIC_ID_PATTERN)

OperationState = Literal["queued", "running", "succeeded", "failed", "cancelled"]
OperationPhase = Literal[
    "admission",
    "authoring",
    "validating",
    "rendering",
    "packaging",
    "publishing",
]
QualityPolicy = Literal["interactive-v1", "release-v1"]
JsonScalar = str | int | float | bool


class PublicWireModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


class PresentationGenerationRequestV2(PublicWireModel):
    schema_version: Literal["presentation-generation-request/v2"] = (
        "presentation-generation-request/v2"
    )
    engine: Literal["authored"] = "authored"
    content: str = Field(min_length=1, max_length=200_000)
    slides_markdown: list[str] | None = Field(default=None, max_length=100)
    instructions: str | None = Field(default=None, max_length=20_000)
    slide_count: int | None = Field(default=None, ge=1, le=100)
    language: str | None = Field(default=None, min_length=1, max_length=64)
    authored_style: str | None = Field(default=None, min_length=1, max_length=160)
    reference_snapshot_ids: list[str] = Field(default_factory=list, max_length=16)
    quality_policy: QualityPolicy = "interactive-v1"

    @field_validator("content", "instructions", "language", "authored_style")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("text fields must not be blank")
        return normalized

    @field_validator("slides_markdown")
    @classmethod
    def validate_slides_markdown(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = [item.strip() for item in value]
        if not normalized or any(not item for item in normalized):
            raise ValueError("slidesMarkdown must contain non-blank slides")
        return normalized

    @field_validator("reference_snapshot_ids")
    @classmethod
    def validate_reference_ids(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate referenceSnapshotIds")
        if any(not _PUBLIC_ID_RE.fullmatch(item) for item in value):
            raise ValueError("referenceSnapshotIds must be opaque public identifiers")
        return value


class PresentationExportOutputV2(PublicWireModel):
    format: Literal["pptx", "pdf"]
    mode: Literal["fidelity-raster", "editable-hybrid"]
    fallback_policy: Literal[
        "reject", "per-slide-raster", "whole-deck-fidelity"
    ] | None = None
    font_embedding: Literal["none", "subset"] = "none"
    required_capabilities: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("required_capabilities")
    @classmethod
    def validate_capabilities(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("requiredCapabilities must be unique non-blank values")
        return normalized

    @model_validator(mode="after")
    def validate_mode(self) -> "PresentationExportOutputV2":
        if self.format == "pdf" and self.mode != "fidelity-raster":
            raise ValueError("capability_not_supported: pdf requires fidelity-raster")
        if self.mode == "editable-hybrid" and self.fallback_policy is None:
            raise ValueError("editable-hybrid requires fallbackPolicy")
        if self.mode == "fidelity-raster" and self.fallback_policy is not None:
            raise ValueError("fidelity-raster does not accept fallbackPolicy")
        if self.mode == "fidelity-raster" and self.font_embedding != "none":
            raise ValueError("capability_not_supported: fidelity-raster cannot embed fonts")
        return self


class PresentationExportRequestV2(PublicWireModel):
    schema_version: Literal["presentation-export-request/v2"] = (
        "presentation-export-request/v2"
    )
    source_revision: int = Field(ge=0)
    output: PresentationExportOutputV2
    quality_policy: QualityPolicy = "interactive-v1"


class OperationLinksV2(PublicWireModel):
    self: str
    events: str
    cancel: str

    @field_validator("self", "events", "cancel")
    @classmethod
    def validate_relative_api_link(cls, value: str) -> str:
        if (
            not value.startswith("/api/v2/")
            or "://" in value
            or "\\" in value
            or ".." in value
        ):
            raise ValueError("operation links must be safe relative v2 API paths")
        return value


class OperationReceiptV2(PublicWireModel):
    schema_version: Literal["operation-receipt/v2"] = "operation-receipt/v2"
    operation_id: str = Field(pattern=PUBLIC_ID_PATTERN)
    receipt_id: str = Field(pattern=PUBLIC_ID_PATTERN)
    presentation_id: str = Field(pattern=PUBLIC_ID_PATTERN)
    status: OperationState
    duplicate: bool = False
    joined_existing_work: bool = False
    accepted_at: AwareDatetime
    absolute_deadline_at: AwareDatetime
    links: OperationLinksV2

    @model_validator(mode="after")
    def validate_receipt(self) -> "OperationReceiptV2":
        if self.absolute_deadline_at <= self.accepted_at:
            raise ValueError("absoluteDeadlineAt must follow acceptedAt")
        base = f"/api/v2/operations/{self.operation_id}"
        if self.links.self != base:
            raise ValueError("links.self must identify operationId")
        if self.links.events != f"{base}/events":
            raise ValueError("links.events must identify operationId")
        if self.links.cancel != f"{base}/cancel":
            raise ValueError("links.cancel must identify operationId")
        return self


class QualityEvidenceV3(PublicWireModel):
    checker_version: str = Field(min_length=1, max_length=160)
    input_digest: str = Field(pattern=DIGEST_PATTERN)
    output_digest: str = Field(pattern=DIGEST_PATTERN)
    measurements: dict[str, JsonScalar] = Field(min_length=1, max_length=128)
    thresholds: dict[str, JsonScalar] = Field(min_length=1, max_length=128)

    @field_validator("checker_version")
    @classmethod
    def strip_checker_version(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("checkerVersion must not be blank")
        return normalized


class _QualityDomainResult(PublicWireModel):
    required: bool
    status: str
    evidence: QualityEvidenceV3 | None = None
    reason_code: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def require_truthful_evidence(self) -> "_QualityDomainResult":
        if self.status == "passed" and self.evidence is None:
            raise ValueError("passed quality status requires evidence")
        if self.status not in {"passed", "not_required"} and self.reason_code is None:
            raise ValueError(f"{self.status} quality status requires reasonCode")
        if self.required and self.status in {
            "not_required",
            "unavailable",
            "not_assessed",
            "pending",
        }:
            raise ValueError(
                f"required quality check cannot have {self.status} status"
            )
        return self


class ContentBriefQualityV3(_QualityDomainResult):
    status: Literal["passed", "degraded", "failed", "not_required"]


class AuthoringQualityV3(_QualityDomainResult):
    status: Literal["passed", "failed"]


class GeometryFontQualityV3(_QualityDomainResult):
    status: Literal["passed", "failed"]


class ImageContentIntegrityQualityV3(_QualityDomainResult):
    status: Literal["passed", "failed", "not_required"]


class VisualReviewQualityV3(_QualityDomainResult):
    status: Literal["passed", "failed", "unavailable", "not_required"]


class PerformanceQualityV3(_QualityDomainResult):
    status: Literal["passed", "failed", "not_assessed"]


class NativeCoverageOoxmlQualityV3(_QualityDomainResult):
    status: Literal["pending", "passed", "failed", "not_required"]


class QualityReceiptV3(PublicWireModel):
    schema_version: Literal["quality-receipt/v3"] = "quality-receipt/v3"
    quality_receipt_id: str = Field(pattern=PUBLIC_ID_PATTERN)
    operation_id: str = Field(pattern=PUBLIC_ID_PATTERN)
    presentation_id: str = Field(pattern=PUBLIC_ID_PATTERN)
    artifact_digest: str = Field(pattern=DIGEST_PATTERN)
    source_revision: int = Field(ge=0)
    scene_digest: str = Field(pattern=DIGEST_PATTERN)
    capability_registry_digest: str = Field(pattern=DIGEST_PATTERN)
    exporter_version: str = Field(min_length=1, max_length=160)
    evaluated_at: AwareDatetime
    content_brief: ContentBriefQualityV3
    authoring: AuthoringQualityV3
    geometry_font: GeometryFontQualityV3
    image_content_integrity: ImageContentIntegrityQualityV3
    visual_review: VisualReviewQualityV3
    performance: PerformanceQualityV3
    native_coverage_ooxml: NativeCoverageOoxmlQualityV3
    release_readiness: Literal["ready", "blocked"]

    @model_validator(mode="after")
    def fail_closed_release_readiness(self) -> "QualityReceiptV3":
        results = (
            self.content_brief,
            self.authoring,
            self.geometry_font,
            self.image_content_integrity,
            self.visual_review,
            self.performance,
            self.native_coverage_ooxml,
        )
        if self.release_readiness == "ready" and any(
            result.required and result.status != "passed" for result in results
        ):
            raise ValueError("releaseReadiness ready requires every required check to pass")
        return self


class PublicArtifactV2(PublicWireModel):
    artifact_id: str = Field(pattern=PUBLIC_ID_PATTERN)
    operation_id: str = Field(pattern=PUBLIC_ID_PATTERN)
    presentation_id: str = Field(pattern=PUBLIC_ID_PATTERN)
    kind: Literal["presentation-pptx", "presentation-pdf"]
    digest: str = Field(pattern=DIGEST_PATTERN)
    size_bytes: int = Field(ge=0)
    media_type: str = Field(min_length=1, max_length=160)
    download: str

    @field_validator("download")
    @classmethod
    def validate_download_link(cls, value: str) -> str:
        if not value.startswith("/api/v2/artifacts/") or not value.endswith("/download"):
            raise ValueError("download must be a relative v2 artifact link")
        if "://" in value or "\\" in value or ".." in value:
            raise ValueError("download must not expose a URL, local path, or storage key")
        return value


class PublicOperationErrorV2(PublicWireModel):
    reason_code: str = Field(min_length=1, max_length=160)
    retryable: bool = False


class OperationStatusV2(PublicWireModel):
    schema_version: Literal["operation-status/v2"] = "operation-status/v2"
    operation_id: str = Field(pattern=PUBLIC_ID_PATTERN)
    presentation_id: str = Field(pattern=PUBLIC_ID_PATTERN)
    kind: Literal["presentation.generate", "presentation.export"]
    status: OperationState
    phase: OperationPhase | None = None
    progress_percent: int | None = Field(default=None, ge=0, le=100)
    accepted_at: AwareDatetime
    updated_at: AwareDatetime
    absolute_deadline_at: AwareDatetime
    error: PublicOperationErrorV2 | None = None
    quality_receipt: QualityReceiptV3 | None = None
    release_readiness: Literal["ready", "blocked", "not_assessed"] = "not_assessed"
    artifacts: list[PublicArtifactV2] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status_shape(self) -> "OperationStatusV2":
        if self.updated_at < self.accepted_at:
            raise ValueError("updatedAt must not precede acceptedAt")
        if self.absolute_deadline_at <= self.accepted_at:
            raise ValueError("absoluteDeadlineAt must follow acceptedAt")
        if self.status in {"failed", "cancelled"} and self.error is None:
            raise ValueError("failed or cancelled operation requires error")
        if self.status not in {"failed", "cancelled"} and self.error is not None:
            raise ValueError("only failed or cancelled operation may expose error")
        if self.release_readiness == "ready" and (
            self.quality_receipt is None
            or self.quality_receipt.release_readiness != "ready"
        ):
            raise ValueError("ready operation requires a ready QualityReceiptV3")
        if any(
            artifact.operation_id != self.operation_id
            or artifact.presentation_id != self.presentation_id
            for artifact in self.artifacts
        ):
            raise ValueError(
                "artifacts must match the status operationId and presentationId"
            )
        if self.quality_receipt is not None and (
            self.quality_receipt.operation_id != self.operation_id
            or self.quality_receipt.presentation_id != self.presentation_id
        ):
            raise ValueError(
                "qualityReceipt must match the status operationId and presentationId"
            )
        if self.quality_receipt is not None and not self.artifacts:
            raise ValueError("qualityReceipt requires a non-empty artifacts list")
        if (
            self.quality_receipt is not None
            and not any(
                artifact.digest == self.quality_receipt.artifact_digest
                for artifact in self.artifacts
            )
        ):
            raise ValueError(
                "at least one artifact digest must match qualityReceipt.artifactDigest"
            )
        return self


class OperationEventV2(PublicWireModel):
    schema_version: Literal["operation-event/v2"] = "operation-event/v2"
    event_id: str = Field(pattern=PUBLIC_ID_PATTERN)
    operation_id: str = Field(pattern=PUBLIC_ID_PATTERN)
    sequence: int = Field(ge=1)
    occurred_at: AwareDatetime
    status: OperationState
    phase: OperationPhase | None = None
    progress_percent: int | None = Field(default=None, ge=0, le=100)
    reason_code: str | None = Field(default=None, min_length=1, max_length=160)


class OperationEventPageV2(PublicWireModel):
    schema_version: Literal["operation-event-page/v2"] = "operation-event-page/v2"
    operation_id: str = Field(pattern=PUBLIC_ID_PATTERN)
    events: list[OperationEventV2] = Field(default_factory=list, max_length=200)
    next_cursor: str | None = Field(default=None, pattern=PUBLIC_ID_PATTERN)

    @model_validator(mode="after")
    def validate_event_binding(self) -> "OperationEventPageV2":
        if any(event.operation_id != self.operation_id for event in self.events):
            raise ValueError("event operationId does not match page operationId")
        if any(
            right.sequence <= left.sequence
            for left, right in zip(self.events, self.events[1:])
        ):
            raise ValueError("events must be strictly ordered by sequence")
        return self


class CancelOperationReceiptV2(PublicWireModel):
    schema_version: Literal["operation-cancel-receipt/v2"] = (
        "operation-cancel-receipt/v2"
    )
    operation_id: str = Field(pattern=PUBLIC_ID_PATTERN)
    status: OperationState
    cancellation_requested: bool
    requested_at: AwareDatetime


class AuthorizedDownloadGrant(PublicWireModel):
    """Internal adapter result. Only the URL is emitted as a 303 Location."""

    url: str = Field(min_length=1, max_length=4_096)
    issued_at: AwareDatetime
    expires_at: AwareDatetime
    etag: str | None = Field(default=None, min_length=1, max_length=512)

    @field_validator("url")
    @classmethod
    def validate_safe_url(cls, value: str) -> str:
        if "\\" in value or "\r" in value or "\n" in value:
            raise ValueError("download grant URL is unsafe")
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower()
        if (
            parsed.scheme not in {"http", "https"}
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError("download grant must be an authorized HTTP(S) URL")
        if parsed.scheme == "http" and hostname not in {
            "localhost",
            "127.0.0.1",
            "::1",
        }:
            raise ValueError(
                "download grant must use HTTPS except for explicit loopback development"
            )
        return value

    @model_validator(mode="after")
    def validate_lifetime(self) -> "AuthorizedDownloadGrant":
        lifetime = (self.expires_at - self.issued_at).total_seconds()
        if lifetime < 120 or lifetime > 300:
            raise ValueError("download grant lifetime must be between 2 and 5 minutes")
        return self


def utc_iso(value: datetime) -> str:
    """Retained for adapters that need deterministic RFC3339 serialization."""

    return value.isoformat().replace("+00:00", "Z")
