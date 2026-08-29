"""Durable export-attempt identity and its schema-versioned provenance."""

from __future__ import annotations

import re
from hashlib import sha256
import json
from datetime import datetime
from typing import Any, Literal, Optional
import uuid

from pydantic import (
    BaseModel,
    ConfigDict,
    Field as PydanticField,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel
from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlmodel import Field, SQLModel

from models.sql.workspace import get_workspace_owner_scope
from utils.datetime_utils import get_current_utc_datetime
from utils.owner_context import get_current_owner_id


PRESENTATION_EXPORT_FORMATS = ("pptx", "pdf")
PRESENTATION_EXPORT_ATTEMPT_STATES = (
    "pending",
    "running",
    "succeeded",
    "failed",
)
PRESENTATION_EXPORT_ADAPTERS = (
    "native_clone",
    "native_reconstructed",
    "general",
)
PRESENTATION_EXPORT_REQUESTED_ADAPTERS = ("auto", *PRESENTATION_EXPORT_ADAPTERS)
_SENSITIVE_PROVENANCE_KEYS = {
    "access_key",
    "access_token",
    "api_key",
    "authorization",
    "cookie",
    "credentials",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "secret_key",
    "session_cookie",
    "token",
}
_SENSITIVE_PROVENANCE_KEYS_COMPACT = {
    re.sub(r"[^a-z0-9]", "", key) for key in _SENSITIVE_PROVENANCE_KEYS
}


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _sha256_sql(column: str) -> str:
    remainder = column
    for character in "0123456789abcdef":
        remainder = f"replace({remainder}, '{character}', '')"
    return (
        f"length({column}) = 64 AND lower({column}) = {column} "
        f"AND length({remainder}) = 0"
    )


def _reject_sensitive_provenance_keys(value: Any) -> Any:
    if isinstance(value, dict):
        for key, child in value.items():
            raw_key = str(key).strip()
            camel_split = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", raw_key)
            camel_split = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", camel_split)
            normalized = re.sub(r"[^a-z0-9]+", "_", camel_split.lower()).strip(
                "_"
            )
            compact = re.sub(r"[^a-z0-9]", "", raw_key.lower())
            if normalized in _SENSITIVE_PROVENANCE_KEYS or any(
                normalized.endswith(f"_{suffix}")
                for suffix in _SENSITIVE_PROVENANCE_KEYS
            ) or compact in _SENSITIVE_PROVENANCE_KEYS_COMPACT or any(
                compact.endswith(suffix)
                for suffix in _SENSITIVE_PROVENANCE_KEYS_COMPACT
            ):
                raise ValueError("export provenance must not contain credentials")
            _reject_sensitive_provenance_keys(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_sensitive_provenance_keys(child)
    return value


class ExportCompilerRuntimeProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    compiler: str = PydanticField(min_length=1, max_length=128)
    compiler_version: str = PydanticField(min_length=1, max_length=128)
    runtime: str = PydanticField(min_length=1, max_length=128)
    runtime_version: str = PydanticField(min_length=1, max_length=128)


class ExportFreshnessProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_revision: int = PydanticField(ge=0)
    source_snapshot_sha256: str
    html_source_sha256: str | None = None
    composition_sha256: str | None = None
    asset_manifest_sha256: str | None = None
    renderer_source_sha256: str | None = None
    package_sha256: str | None = None
    asset_digests_match: bool
    package_digest_match: bool

    @field_validator(
        "source_snapshot_sha256",
        "html_source_sha256",
        "composition_sha256",
        "asset_manifest_sha256",
        "renderer_source_sha256",
        "package_sha256",
    )
    @classmethod
    def validate_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.lower()
        if not _is_sha256(normalized):
            raise ValueError("export freshness digests must be SHA-256 values")
        return normalized


class ExportAtomicBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Optional only for replaying pre-atomic provenance. Trusted parity requires it.
    presentation_id: uuid.UUID | None = None
    export_attempt_id: uuid.UUID
    presentation_revision: int = PydanticField(ge=0)
    canonical_source_sha256: str
    renderer_source_sha256: str
    pptx_sha256: str

    @field_validator(
        "canonical_source_sha256", "renderer_source_sha256", "pptx_sha256"
    )
    @classmethod
    def validate_binding_digest(cls, value: str) -> str:
        normalized = value.lower()
        if not _is_sha256(normalized):
            raise ValueError("export atomic binding digest must be SHA-256")
        return normalized


class ExportPowerPointChartReadback(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    slide_number: int = PydanticField(ge=1, le=100)
    element_id: str = PydanticField(min_length=1, max_length=128)
    chart: dict[str, Any]


class ExportPowerPointQaEvidence(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    status: Literal["preflight", "not-verified", "passed", "failed"]
    platform: Literal["windows", "unsupported", "unknown"]
    supported: bool = False
    automation: Literal["PowerPoint.COM"] | None = None
    reopened: bool = False
    rendered: bool = False
    readback: bool = False
    verification_scope: Literal["powerpoint-first-open-save-reopen-stability"] | None = None
    semantic_payload_binding: Literal["not-verified", "passed", "failed"] = "not-verified"
    repair_dialogs: int | None = PydanticField(default=None, ge=0)
    repair_observation: Literal[
        "not-observable-via-noninteractive-com", "interactive-observation"
    ] | None = None
    rendered_png_sha256: list[str] = PydanticField(default_factory=list, max_length=100)
    first_open_rendered_png_sha256: list[str] = PydanticField(
        default_factory=list, max_length=100
    )
    reopen_rendered_png_sha256: list[str] = PydanticField(
        default_factory=list, max_length=100
    )
    render_hashes_match: bool | None = None
    first_open_readback_sha256: str | None = None
    reopen_readback_sha256: str | None = None
    first_open_readback_artifact: str | None = PydanticField(default=None, max_length=128)
    reopen_readback_artifact: str | None = PydanticField(default=None, max_length=128)
    shape_count_changes: int = PydanticField(default=0, ge=0)
    object_identity_changes: int = PydanticField(default=0, ge=0)
    geometry_changes_gt_5pt: int = PydanticField(default=0, ge=0)
    geometry_changes_gt_10pt: int = PydanticField(default=0, ge=0)
    text_changes: int = PydanticField(default=0, ge=0)
    line_break_changes: int = PydanticField(default=0, ge=0)
    typeface_changes: int = PydanticField(default=0, ge=0)
    table_content_changes: int = PydanticField(default=0, ge=0)
    chart_content_changes: int = PydanticField(default=0, ge=0)
    crop_changes: int = PydanticField(default=0, ge=0)
    readback_match: bool | None = None
    powerpoint_version: str | None = PydanticField(default=None, max_length=64)
    slide_count: int | None = PydanticField(default=None, ge=1, le=100)
    typography_issues_gt_5pt: int = PydanticField(default=0, ge=0)
    typography_issues_gt_10pt: int = PydanticField(default=0, ge=0)
    inspection_failures: int = PydanticField(default=0, ge=0)
    native_text: int | None = PydanticField(default=None, ge=0)
    native_image: int | None = PydanticField(default=None, ge=0)
    native_shape: int | None = PydanticField(default=None, ge=0)
    native_table: int | None = PydanticField(default=None, ge=0)
    native_chart: int | None = PydanticField(default=None, ge=0)
    native_image_crop: int | None = PydanticField(default=None, ge=0)
    native_qid_missing: int = PydanticField(default=0, ge=0)
    native_qid_ghosting: int = PydanticField(default=0, ge=0)
    backplate_collisions: int = PydanticField(default=0, ge=0)
    first_open_chart_readback: list[ExportPowerPointChartReadback] = PydanticField(
        default_factory=list, max_length=1024
    )
    reopen_chart_readback: list[ExportPowerPointChartReadback] = PydanticField(
        default_factory=list, max_length=1024
    )

    @field_validator(
        "rendered_png_sha256",
        "first_open_rendered_png_sha256",
        "reopen_rendered_png_sha256",
    )
    @classmethod
    def validate_rendered_digests(cls, value: list[str]) -> list[str]:
        normalized = [digest.lower() for digest in value]
        if len(normalized) != len(set(normalized)) or any(
            not _is_sha256(digest) for digest in normalized
        ):
            raise ValueError("PowerPoint rendered PNG digests are invalid")
        return normalized

    @field_validator("first_open_readback_sha256", "reopen_readback_sha256")
    @classmethod
    def validate_readback_digests(cls, value: str | None) -> str | None:
        if value is not None and not _is_sha256(value):
            raise ValueError("PowerPoint readback digest is invalid")
        return value.lower() if value is not None else None

    @model_validator(mode="after")
    def validate_passed_gate(self) -> "ExportPowerPointQaEvidence":
        if self.status == "passed" and not (
            self.platform == "windows"
            and self.supported
            and self.automation == "PowerPoint.COM"
            and self.reopened
            and self.rendered
            and self.readback
            and (
                self.repair_dialogs == 0
                or (
                    self.repair_dialogs is None
                    and self.repair_observation
                    == "not-observable-via-noninteractive-com"
                )
            )
            and self.rendered_png_sha256
            and self.render_hashes_match is not False
            and self.readback_match is not False
            and self.typography_issues_gt_5pt == 0
            and self.typography_issues_gt_10pt == 0
        ):
            raise ValueError("PowerPoint visual parity requires COM reopen/render/readback evidence")
        if self.platform == "unsupported" and self.status not in {
            "preflight",
            "not-verified",
        }:
            raise ValueError("unsupported platforms cannot claim PowerPoint QA")
        return self


class ExportCoverageSource(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    export_attempt_id: uuid.UUID | None = None
    presentation_revision: int = PydanticField(ge=0)
    canonical_source_digest: str | None = None
    source_digest: str

    @field_validator("source_digest", "canonical_source_digest")
    @classmethod
    def validate_source_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.lower()
        if not _is_sha256(normalized):
            raise ValueError("export coverage source digest must be SHA-256")
        return normalized


class ExportCoverageCounts(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    native_text: int = PydanticField(ge=0)
    native_image: int = PydanticField(ge=0)
    native_shape: int = PydanticField(ge=0)
    native_table: int = PydanticField(ge=0)
    native_chart: int = PydanticField(ge=0)
    native_image_crop: int = PydanticField(default=0, ge=0)
    raster_remaining: int = PydanticField(ge=0)


class ExportOoxmlReadback(ExportCoverageCounts):
    status: Literal["not-run", "passed", "failed"]


class ExportNativeTextPolicy(BaseModel):
    """Exact bounded CJK native-text policy emitted by the Hybrid serializer."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    korean_latin: Literal["bounded-native"]
    japanese: Literal["raster-required"]
    chinese: Literal["raster-required"]


class ExportTypographyIssue(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    code: str = PydanticField(min_length=1, max_length=128)
    element_id: str | None = PydanticField(default=None, max_length=512)


class ExportSemanticReceipts(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    observation_receipt_digest: str | None = None
    native_admission_receipt_digest: str | None = None
    hybrid_plan_digest: str | None = None
    raster_degradation_receipt_digest: str | None = None

    @field_validator(
        "observation_receipt_digest",
        "native_admission_receipt_digest",
        "hybrid_plan_digest",
        "raster_degradation_receipt_digest",
    )
    @classmethod
    def validate_receipt_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.lower()
        if not normalized.startswith("sha256:") or not _is_sha256(normalized[7:]):
            raise ValueError("semantic receipt digest must be prefixed SHA-256")
        return normalized

    @model_validator(mode="after")
    def require_one_receipt(self) -> "ExportSemanticReceipts":
        if not any(
            (
                self.observation_receipt_digest,
                self.native_admission_receipt_digest,
                self.hybrid_plan_digest,
                self.raster_degradation_receipt_digest,
            )
        ):
            raise ValueError("semantic receipt set must not be empty")
        return self


class ExportSlideCoverage(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    slide_number: int = PydanticField(ge=1)
    native_text: int = PydanticField(ge=0)
    native_image: int = PydanticField(ge=0)
    native_shape: int = PydanticField(ge=0)
    native_table: int = PydanticField(ge=0)
    native_chart: int = PydanticField(ge=0)
    native_image_crop: int = PydanticField(default=0, ge=0)
    logical_coverage: ExportCoverageCounts | None = None
    ooxml_readback: ExportOoxmlReadback | None = None
    backplate_count: int = PydanticField(default=0, ge=0)
    typography_issues: list[ExportTypographyIssue] = PydanticField(
        default_factory=list, max_length=1024
    )
    semantic_receipts: ExportSemanticReceipts | None = PydanticField(
        default=None,
        exclude_if=lambda value: value is None,
    )
    raster_remaining: int = PydanticField(ge=0)
    fallback_reasons: list[str] = PydanticField(max_length=256)
    visual_parity: Literal[
        "fidelity-backplate-retained",
        "preflight",
        "not-verified",
        "passed",
        "failed",
    ]
    native_backplate_de_duplication: Literal["applied", "not-applicable"]

    @field_validator("fallback_reasons")
    @classmethod
    def validate_fallback_reasons(cls, value: list[str]) -> list[str]:
        normalized = sorted({reason.strip() for reason in value if reason.strip()})
        if any(len(reason) > 128 for reason in normalized):
            raise ValueError("export coverage fallback reason is too long")
        return normalized

    @model_validator(mode="after")
    def validate_declared_support(self) -> "ExportSlideCoverage":
        # Native table/chart claims are accepted only when the semantic plan
        # and exact OOXML package readback agree.  A serializer declaration
        # alone never creates release authority.
        for field, label in (("native_table", "table"), ("native_chart", "chart")):
            value = getattr(self, field)
            if not value:
                continue
            if (
                self.logical_coverage is None
                or self.ooxml_readback is None
                or self.ooxml_readback.status != "passed"
                or getattr(self.logical_coverage, field) != value
                or getattr(self.ooxml_readback, field) != value
            ):
                raise ValueError(
                    f"native {label} coverage requires matching semantic and OOXML readback"
                )
        if self.native_image_crop and (
            self.native_image_crop > self.native_image
            or self.logical_coverage is None
            or self.ooxml_readback is None
            or self.ooxml_readback.status != "passed"
            or self.logical_coverage.native_image_crop != self.native_image_crop
            or self.ooxml_readback.native_image_crop != self.native_image_crop
        ):
            raise ValueError(
                "native image crop coverage requires matching semantic and OOXML readback"
            )
        native_count = (
            self.native_text
            + self.native_image
            + self.native_shape
            + self.native_table
            + self.native_chart
        )
        expected = "applied" if native_count > 0 else "not-applicable"
        if self.native_backplate_de_duplication != expected:
            raise ValueError("native/backplate de-duplication evidence is inconsistent")
        return self


class PresentationExportCoverageManifest(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    schema_version: Literal[
        "presenton.authored-hybrid-coverage/v1",
        "presenton.authored-hybrid-coverage/v2",
    ]
    mode: Literal["hybrid"]
    cjk_coverage_profile_id: Literal["ja-ko-zh-bounded-fixture-v1"] | None = (
        PydanticField(default=None, exclude_if=lambda value: value is None)
    )
    native_text_policy: ExportNativeTextPolicy | None = PydanticField(
        default=None,
        exclude_if=lambda value: value is None,
    )
    source: ExportCoverageSource
    total_slides: int = PydanticField(ge=1)
    native_text: int = PydanticField(ge=0)
    native_image: int = PydanticField(ge=0)
    native_shape: int = PydanticField(ge=0)
    native_table: int = PydanticField(ge=0)
    native_chart: int = PydanticField(ge=0)
    native_image_crop: int = PydanticField(default=0, ge=0)
    logical_coverage: ExportCoverageCounts | None = None
    ooxml_readback: ExportOoxmlReadback | None = None
    backplate_count: int = PydanticField(default=0, ge=0)
    typography_issues: list[dict[str, Any]] = PydanticField(
        default_factory=list, max_length=4096
    )
    raster_remaining: int = PydanticField(ge=0)
    fully_editable: bool
    visual_parity: Literal["preflight", "not-verified", "passed", "failed"] = (
        "not-verified"
    )
    slides: list[ExportSlideCoverage] = PydanticField(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_totals(self) -> "PresentationExportCoverageManifest":
        if self.total_slides != len(self.slides):
            raise ValueError("export coverage slide total is inconsistent")
        if [slide.slide_number for slide in self.slides] != list(
            range(1, self.total_slides + 1)
        ):
            raise ValueError("export coverage slide numbers must be contiguous")
        totals = {
            "native_text": sum(slide.native_text for slide in self.slides),
            "native_image": sum(slide.native_image for slide in self.slides),
            "native_shape": sum(slide.native_shape for slide in self.slides),
            "native_table": sum(slide.native_table for slide in self.slides),
            "native_chart": sum(slide.native_chart for slide in self.slides),
            "native_image_crop": sum(
                slide.native_image_crop for slide in self.slides
            ),
            "raster_remaining": sum(
                slide.raster_remaining for slide in self.slides
            ),
        }
        if any(getattr(self, field) != value for field, value in totals.items()):
            raise ValueError("export coverage native/raster totals are inconsistent")
        if self.fully_editable != (self.raster_remaining == 0):
            raise ValueError("export coverage editability claim is inconsistent")
        if self.schema_version.endswith("/v2"):
            if (
                self.cjk_coverage_profile_id is None
                or self.native_text_policy is None
                or self.source.export_attempt_id is None
                or self.source.canonical_source_digest is None
                or self.logical_coverage is None
                or self.ooxml_readback is None
                or any(
                    slide.logical_coverage is None or slide.ooxml_readback is None
                    for slide in self.slides
                )
            ):
                raise ValueError("export coverage v2 atomic evidence is incomplete")
            if self.logical_coverage.raster_remaining != self.raster_remaining:
                raise ValueError("logical raster coverage is inconsistent")
            if self.backplate_count != sum(
                slide.backplate_count for slide in self.slides
            ):
                raise ValueError("export coverage backplate total is inconsistent")
            if any(
                slide.visual_parity != self.visual_parity for slide in self.slides
            ):
                raise ValueError(
                    "export coverage v2 visual parity status is inconsistent"
                )
        return self


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


def _without_default_native_crop(value: Any) -> Any:
    """Canonical legacy-v2 view from before nativeImageCrop was first-class."""

    if isinstance(value, dict):
        return {
            key: _without_default_native_crop(item)
            for key, item in value.items()
            if not (key == "nativeImageCrop" and item == 0)
        }
    if isinstance(value, list):
        return [_without_default_native_crop(item) for item in value]
    return value


class ExportVisualParityEvidence(BaseModel):
    """One immutable identity shared by both sidecars and trusted desktop QA."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
    )

    status: Literal["preflight", "not-verified", "passed", "failed"]
    binding: ExportAtomicBinding
    quality_sidecar_sha256: str
    coverage_sidecar_sha256: str
    powerpoint_qa: ExportPowerPointQaEvidence

    @field_validator("quality_sidecar_sha256", "coverage_sidecar_sha256")
    @classmethod
    def validate_sidecar_digest(cls, value: str) -> str:
        normalized = value.lower()
        if not _is_sha256(normalized):
            raise ValueError("visual parity sidecar digest must be SHA-256")
        return normalized

    @model_validator(mode="after")
    def validate_qa_status(self) -> "ExportVisualParityEvidence":
        # COM package/readback QA is necessary but is not itself visual-parity
        # evidence.  A passed visual claim requires passed COM QA, while a
        # passed COM observation may truthfully remain not-verified until a
        # separate bound visual comparison is supplied.
        if self.status == "passed" and self.powerpoint_qa.status != "passed":
            raise ValueError("passed visual parity requires passed PowerPoint QA")
        if self.status == "not-verified" and self.powerpoint_qa.status == "failed":
            raise ValueError("failed PowerPoint QA cannot be visually not-verified")
        if self.status == "passed" and (
            self.binding.presentation_id is None
            or self.powerpoint_qa.native_text is None
            or self.powerpoint_qa.native_image is None
            or self.powerpoint_qa.native_shape is None
            or self.powerpoint_qa.native_table is None
            or self.powerpoint_qa.native_chart is None
            or self.powerpoint_qa.native_image_crop is None
        ):
            raise ValueError("passed visual parity requires complete native readback")
        return self


class PresentationExportAttemptProvenance(BaseModel):
    """Closed JSON contract stored with one deterministic adapter decision."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    adapter_chain: tuple[
        Literal["native_clone", "native_reconstructed", "general"], ...
    ] = PydanticField(min_length=1, max_length=3)
    capability_evidence: dict[str, Any] = PydanticField(min_length=1)
    fallback_reason: str | None = PydanticField(default=None, max_length=256)
    asset_digests: dict[str, str] = PydanticField(default_factory=dict)
    compiler_runtime: ExportCompilerRuntimeProvenance
    freshness: ExportFreshnessProvenance
    coverage_manifest: PresentationExportCoverageManifest | None = None
    quality_sidecar: dict[str, Any] | None = None
    atomic_binding: ExportAtomicBinding | None = None
    powerpoint_qa: ExportPowerPointQaEvidence | None = None
    visual_parity: ExportVisualParityEvidence | None = None

    @field_validator("adapter_chain")
    @classmethod
    def validate_adapter_chain(
        cls,
        value: tuple[
            Literal["native_clone", "native_reconstructed", "general"], ...
        ],
    ) -> tuple[Literal["native_clone", "native_reconstructed", "general"], ...]:
        if len(value) != len(set(value)):
            raise ValueError("export adapter chain entries must be unique")
        return value

    @field_validator("capability_evidence")
    @classmethod
    def reject_sensitive_capability_evidence(
        cls,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        return _reject_sensitive_provenance_keys(value)

    @field_validator("asset_digests")
    @classmethod
    def validate_asset_digests(cls, value: dict[str, str]) -> dict[str, str]:
        normalized: dict[str, str] = {}
        for asset_id, digest in value.items():
            key = asset_id.strip()
            candidate = digest.lower()
            if not key or not _is_sha256(candidate):
                raise ValueError("export asset digests must use nonblank keys and SHA-256 values")
            normalized[key] = candidate
        return normalized

    @model_validator(mode="after")
    def validate_fallback_evidence(self) -> "PresentationExportAttemptProvenance":
        if len(self.adapter_chain) > 1 and not (self.fallback_reason or "").strip():
            raise ValueError("export adapter fallback requires a stable reason")
        if self.fallback_reason is not None:
            self.fallback_reason = self.fallback_reason.strip()
        if self.coverage_manifest is not None:
            if self.compiler_runtime.compiler != "authored-hybrid":
                raise ValueError("Hybrid coverage requires the authored Hybrid compiler")
            if (
                self.coverage_manifest.source.presentation_revision
                != self.freshness.source_revision
            ):
                raise ValueError("export coverage revision does not match freshness")
            if (
                self.freshness.renderer_source_sha256
                != self.coverage_manifest.source.source_digest
            ):
                raise ValueError("export coverage source digest does not match renderer")
            if self.coverage_manifest.schema_version.endswith("/v2"):
                binding = self.atomic_binding
                source = self.coverage_manifest.source
                if binding is None:
                    raise ValueError("export coverage v2 requires atomic binding")
                if (
                    source.export_attempt_id != binding.export_attempt_id
                    or source.presentation_revision != binding.presentation_revision
                    or source.canonical_source_digest
                    != binding.canonical_source_sha256
                    or self.freshness.source_snapshot_sha256
                    != binding.canonical_source_sha256
                    or self.freshness.renderer_source_sha256
                    != binding.renderer_source_sha256
                ):
                    raise ValueError("export coverage atomic binding is inconsistent")
        if self.powerpoint_qa is not None and self.powerpoint_qa.status == "passed":
            if self.atomic_binding is None:
                raise ValueError("PowerPoint QA requires atomic artifact binding")
        if self.visual_parity is not None:
            binding = self.atomic_binding
            quality = self.quality_sidecar
            coverage = self.coverage_manifest
            evidence = self.visual_parity
            if (
                binding is None
                or quality is None
                or coverage is None
                or self.powerpoint_qa is None
                or binding.presentation_id is None
            ):
                raise ValueError("visual parity requires both bound sidecars and QA")
            if evidence.binding != binding or evidence.powerpoint_qa != self.powerpoint_qa:
                raise ValueError("visual parity evidence binding is inconsistent")
            if (
                binding.presentation_revision != self.freshness.source_revision
                or binding.canonical_source_sha256
                != self.freshness.source_snapshot_sha256
                or binding.renderer_source_sha256
                != self.freshness.renderer_source_sha256
                or binding.pptx_sha256 != self.freshness.package_sha256
            ):
                raise ValueError("visual parity freshness binding is inconsistent")
            quality_binding = quality.get("atomicBinding")
            try:
                quality_powerpoint_qa = ExportPowerPointQaEvidence.model_validate(
                    quality.get("powerPointQa")
                )
            except ValueError:
                quality_powerpoint_qa = None
            if not isinstance(quality_binding, dict) or (
                quality.get("exportAttemptId") != str(binding.export_attempt_id)
                or quality_binding.get("presentationId") != str(binding.presentation_id)
                or quality_binding.get("presentationRevision")
                != binding.presentation_revision
                or quality_binding.get("canonicalSourceDigest")
                != binding.canonical_source_sha256
                or quality_binding.get("rendererSourceDigest")
                != binding.renderer_source_sha256
                or quality_binding.get("pptxSha256") != binding.pptx_sha256
                or quality_powerpoint_qa != self.powerpoint_qa
            ):
                raise ValueError("quality sidecar visual parity binding is inconsistent")
            if (
                coverage.source.export_attempt_id != binding.export_attempt_id
                or coverage.source.presentation_revision != binding.presentation_revision
                or coverage.source.canonical_source_digest
                != binding.canonical_source_sha256
                or coverage.source.source_digest != binding.renderer_source_sha256
                or coverage.visual_parity != evidence.status
            ):
                raise ValueError("coverage sidecar visual parity binding is inconsistent")
            quality_json = dict(quality)
            coverage_json = coverage.model_dump(mode="json", by_alias=True)
            coverage_digests = {
                _canonical_sidecar_sha256(coverage_json),
                _canonical_sidecar_sha256(_without_default_native_crop(coverage_json)),
            }
            if (
                _canonical_sidecar_sha256(quality_json)
                != evidence.quality_sidecar_sha256
                or evidence.coverage_sidecar_sha256 not in coverage_digests
            ):
                raise ValueError("visual parity sidecar digest is inconsistent")
        return self


def validate_export_attempt_provenance(value: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical JSON object accepted by the persistence boundary."""

    return PresentationExportAttemptProvenance.model_validate(value).model_dump(
        mode="json"
    )


class PresentationExportAttempt(SQLModel, table=True):
    __tablename__ = "presentation_export_attempts"
    __table_args__ = (
        UniqueConstraint(
            "owner_scope",
            "presentation_id",
            "format",
            "idempotency_key_hash",
            name="uq_export_attempts_scope_presentation_format_key",
        ),
        CheckConstraint(
            "format IN ('pptx', 'pdf')",
            name="ck_export_attempts_format",
        ),
        CheckConstraint(
            "state IN ('pending', 'running', 'succeeded', 'failed')",
            name="ck_export_attempts_state",
        ),
        CheckConstraint(
            "source_revision >= 0",
            name="ck_export_attempts_source_revision",
        ),
        CheckConstraint(
            f"({_sha256_sql('source_snapshot_sha256')}) AND "
            f"({_sha256_sql('request_hash')}) AND "
            f"({_sha256_sql('idempotency_key_hash')})",
            name="ck_export_attempts_hashes",
        ),
        CheckConstraint(
            "artifact_sha256 IS NULL OR "
            f"({_sha256_sql('artifact_sha256')})",
            name="ck_export_attempts_artifact_digest",
        ),
        CheckConstraint(
            "artifact_size_bytes IS NULL OR artifact_size_bytes >= 0",
            name="ck_export_attempts_artifact_size",
        ),
        CheckConstraint(
            "provenance_schema_version >= 1",
            name="ck_export_attempts_provenance_schema",
        ),
        CheckConstraint(
            "requested_adapter IN "
            "('auto', 'native_clone', 'native_reconstructed', 'general')",
            name="ck_export_attempts_requested_adapter",
        ),
        CheckConstraint(
            "selected_adapter IS NULL OR selected_adapter IN "
            "('native_clone', 'native_reconstructed', 'general')",
            name="ck_export_attempts_selected_adapter",
        ),
        CheckConstraint(
            "(state = 'pending' AND started_at IS NULL AND completed_at IS NULL "
            "AND artifact_sha256 IS NULL AND artifact_size_bytes IS NULL "
            "AND failure_code IS NULL) OR "
            "(state = 'running' AND started_at IS NOT NULL AND completed_at IS NULL "
            "AND artifact_sha256 IS NULL AND artifact_size_bytes IS NULL "
            "AND failure_code IS NULL) OR "
            "(state = 'succeeded' AND started_at IS NOT NULL "
            "AND completed_at IS NOT NULL AND selected_adapter IS NOT NULL "
            "AND artifact_sha256 IS NOT NULL AND artifact_size_bytes IS NOT NULL "
            "AND failure_code IS NULL AND failure_path IS NULL) OR "
            "(state = 'failed' AND started_at IS NOT NULL "
            "AND completed_at IS NOT NULL AND artifact_sha256 IS NULL "
            "AND artifact_size_bytes IS NULL AND failure_code IS NOT NULL)",
            name="ck_export_attempts_terminal_state",
        ),
        Index(
            "ix_export_attempts_presentation_created",
            "presentation_id",
            "created_at",
        ),
        Index("ix_export_attempts_state_created", "state", "created_at"),
        Index("ix_export_attempts_operation", "workspace_operation_id"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: Optional[uuid.UUID] = Field(
        default_factory=get_current_owner_id,
        exclude=True,
        sa_column=Column(
            Uuid,
            ForeignKey(
                "user.id",
                name="fk_export_attempts_owner_id",
                ondelete="CASCADE",
            ),
            nullable=True,
            default=get_current_owner_id,
        ),
    )
    owner_scope: str = Field(
        default_factory=get_workspace_owner_scope,
        sa_column=Column(
            String(36),
            nullable=False,
            default=get_workspace_owner_scope,
            server_default="anonymous",
        ),
    )
    presentation_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey(
                "presentations.id",
                name="fk_export_attempts_presentation_id",
                ondelete="CASCADE",
            ),
            nullable=False,
        )
    )
    workspace_operation_id: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(
            Uuid,
            ForeignKey(
                "workspace_operations.id",
                name="fk_export_attempts_workspace_operation_id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
    )
    format: str = Field(sa_column=Column(String(8), nullable=False))
    state: str = Field(
        default="pending",
        sa_column=Column(
            String(16),
            nullable=False,
            default="pending",
            server_default="pending",
        ),
    )
    source_revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    source_snapshot_sha256: str = Field(
        sa_column=Column(String(64), nullable=False)
    )
    request_hash: str = Field(sa_column=Column(String(64), nullable=False))
    idempotency_key_hash: str = Field(
        sa_column=Column(String(64), nullable=False)
    )
    requested_adapter: str = Field(sa_column=Column(String(64), nullable=False))
    selected_adapter: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
    )
    artifact_sha256: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
    )
    artifact_size_bytes: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    failure_code: Optional[str] = Field(
        default=None,
        sa_column=Column(String(128), nullable=True),
    )
    failure_path: Optional[str] = Field(
        default=None,
        sa_column=Column(String(512), nullable=True),
    )
    provenance_schema_version: int = Field(
        default=1,
        sa_column=Column(
            Integer,
            nullable=False,
            default=1,
            server_default=text("1"),
        ),
    )
    provenance: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    started_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    completed_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            default=get_current_utc_datetime,
            server_default=func.now(),
        ),
    )
    updated_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            default=get_current_utc_datetime,
            onupdate=get_current_utc_datetime,
            server_default=func.now(),
        ),
    )
