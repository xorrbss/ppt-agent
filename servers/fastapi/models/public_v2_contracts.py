"""Strict public wire contracts for asynchronous presentation work.

These models deliberately contain no owner identifiers, session identifiers,
local paths, object-store keys, prompts returned by providers, or other
execution-internal state.
"""

from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import re
from uuid import UUID
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
    model_serializer,
)
from pydantic.alias_generators import to_camel

from models.svg_canvas import SvgCanvasInput

from models.generate_presentation_request import (
    AuthoredMasterPinInput,
    SourceContentManifestPinInput,
    SourceContentPolicy,
    RasterApprovalPinInput,
)
from models.authored_vnext.narrative_ir_contracts import (
    ContentIntegrityReceiptV2,
)


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
GenerationMode = Literal[
    "auto",
    "registered-template",
    "brand-style",
    "free-design",
    "fidelity-first",
    "editable-first",
]
JsonScalar = str | int | float | bool


class PublicWireModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )


class DesignPackPinV1(PublicWireModel):
    design_pack_id: str = Field(pattern=PUBLIC_ID_PATTERN)
    version: int = Field(ge=1)
    digest: str = Field(pattern=DIGEST_PATTERN)


class GenerationRouteReceiptV1(PublicWireModel):
    schema_version: Literal["generation-route-receipt/v1"] = "generation-route-receipt/v1"
    requested_mode: GenerationMode
    selected_route: Literal["source-native", "authored-fallback", "svg-native"]
    design_pack_pin: DesignPackPinV1 | None = None
    narrative_ir_digest: str | None = Field(default=None, pattern=DIGEST_PATTERN)
    fallback_reason: str | None = Field(default=None, min_length=1, max_length=160)
    output_capability: Literal["fidelity"] = "fidelity"
    receipt_digest: str = Field(pattern=DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_route_receipt(self) -> "GenerationRouteReceiptV1":
        if self.selected_route == "source-native" and self.design_pack_pin is None:
            raise ValueError("source-native requires designPackPin")
        if self.selected_route == "source-native" and self.fallback_reason is not None:
            raise ValueError("source-native cannot expose fallbackReason")
        sealed = self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"receipt_digest"},
        )
        expected_digest = (
            "sha256:"
            + sha256(
                json.dumps(
                    sealed,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        )
        if self.receipt_digest != expected_digest:
            raise ValueError("receiptDigest does not match generation route payload")
        return self


class PresentationGenerationRequestV2(PublicWireModel):
    schema_version: Literal["presentation-generation-request/v2"] = "presentation-generation-request/v2"
    engine: Literal["authored"] = "authored"
    # Omit for the server default (svg) -- see
    # services.generation_core.admission.resolve_generation_strategy. Never
    # read this field directly; every decision goes through the resolver.
    strategy: Literal["authored", "svg"] | None = None
    generation_mode: GenerationMode = "auto"
    design_pack_pin: DesignPackPinV1 | None = None
    generation_engine: Literal["authored", "deck-ir"] | None = "authored"
    content: str = Field(min_length=1, max_length=200_000)
    slides_markdown: list[str] | None = Field(default=None, max_length=100)
    instructions: str | None = Field(default=None, max_length=20_000)
    slide_count: int | None = Field(default=None, ge=1, le=100)
    language: str | None = Field(default=None, min_length=1, max_length=64)
    authored_style: str | None = Field(default=None, min_length=1, max_length=160)
    aspect_ratio: Literal["16:9", "4:3", "9:16", "9:13", "1:1", "21:9"] = "16:9"
    svg_canvas: SvgCanvasInput | None = Field(default=None, exclude_if=lambda value: value is None)
    svg_design_reference_snapshot_id: UUID | None = Field(default=None, exclude_if=lambda value: value is None)
    svg_design_reference_pin: DesignPackPinV1 | None = Field(default=None, exclude_if=lambda value: value is None)
    page_size: Literal["A4"] | None = None
    orientation: Literal["landscape", "portrait"] | None = None

    @model_validator(mode="before")
    @classmethod
    def validate_canvas_selectors(cls, value: Any) -> Any:
        from models.generate_presentation_request import GeneratePresentationRequest

        if isinstance(value, dict):
            selectors = dict(value)
            for alias, name in (("pageSize", "page_size"), ("aspectRatio", "aspect_ratio"), ("svgCanvas", "svg_canvas")):
                if alias in selectors:
                    selectors[name] = selectors[alias]
            GeneratePresentationRequest.validate_canvas_selectors(selectors)
        return value

    @model_serializer(mode="wrap")
    def serialize_canvas_selectors(self, handler):
        payload = handler(self)
        for key in (
            ("aspectRatio", "aspect_ratio") if self.page_size is not None else ("pageSize", "page_size", "orientation")
        ):
            payload.pop(key, None)
        if self.svg_canvas is not None:
            for key in ("aspectRatio", "aspect_ratio", "pageSize", "page_size", "orientation"):
                payload.pop(key, None)
        if self.source_mode == "source_content_restyle" and "aspect_ratio" not in self.model_fields_set:
            payload.pop("aspect_ratio", None)
            payload.pop("aspectRatio", None)
        return payload

    master_pin: AuthoredMasterPinInput | None = None
    reference_snapshot_ids: list[str] = Field(default_factory=list, max_length=16)
    source_mode: Literal["source_content_restyle", "raster_reconstruction"] | None = None
    source_content_manifest_pin: SourceContentManifestPinInput | None = None
    raster_approval_pin: RasterApprovalPinInput | None = None
    content_policy: SourceContentPolicy | None = None
    quality_policy: QualityPolicy = "interactive-v1"

    @model_validator(mode="after")
    def reject_retired_deck_ir_engine_selection(self) -> "PresentationGenerationRequestV2":
        # "deck-ir" is retired as an explicit client choice on the public wire
        # contract. The server's internal auto-routing to that engine
        # (services.public_generation_route.effective_generation_engine) is a
        # separate, server-owned decision that never reads this field back.
        if self.generation_engine == "deck-ir":
            raise ValueError("generation_engine_deck_ir_retired")
        return self

    @model_validator(mode="after")
    def validate_generation_mode(self) -> "PresentationGenerationRequestV2":
        references = (self.master_pin, self.svg_design_reference_pin, self.svg_design_reference_snapshot_id)
        if (self.svg_design_reference_pin is not None or self.svg_design_reference_snapshot_id is not None) and sum(value is not None for value in references) > 1:
            raise ValueError("svg_design_reference_conflict")
        if (self.svg_design_reference_pin is not None or self.svg_design_reference_snapshot_id is not None) and (self.strategy != "svg" or self.design_pack_pin is not None or self.generation_mode not in {None, "auto", "free-design"}):
            raise ValueError("svg_design_reference_strategy_required")
        if self.strategy == "authored" and (self.svg_canvas is not None or self.aspect_ratio == "4:3"):
            raise ValueError("svg_canvas_strategy_required")
        if self.generation_mode == "editable-first":
            raise ValueError("capability_not_supported: editable-first")
        if self.generation_mode == "registered-template" and self.design_pack_pin is None and self.source_mode is None:
            raise ValueError("design_pack_pin_required")
        if self.design_pack_pin is not None and self.generation_mode != "registered-template":
            raise ValueError("design_pack_pin_requires_registered_template")
        if self.source_mode is None:
            if (
                self.source_content_manifest_pin is not None
                or self.content_policy is not None
                or self.raster_approval_pin is not None
            ):
                raise ValueError("source_content_mode_required")
            return self
        if self.source_mode == "raster_reconstruction":
            if self.raster_approval_pin is None:
                raise ValueError("raster_approval_pin_required")
            if self.source_content_manifest_pin is not None or len(self.reference_snapshot_ids) != 1:
                raise ValueError("raster_single_source_snapshot_required")
            from services.generation_core.admission.strategy import (
                resolve_generation_strategy,
            )

            if resolve_generation_strategy(self) != "svg":
                raise ValueError("raster_a4_svg_required")
            from utils.svg_template_rules import resolve_source_template_style

            self.authored_style = resolve_source_template_style(self.authored_style or "screenshot-blue").id
            if self.slides_markdown is not None or self.quality_policy == "release-v1":
                raise ValueError("source_content_generation_option_conflict")
            return self
        if self.raster_approval_pin is not None:
            raise ValueError("raster_source_mode_required")
        if self.source_content_manifest_pin is None:
            raise ValueError("source_content_manifest_pin_required")
        if self.content_policy is None:
            raise ValueError("source_content_policy_required")
        if not self.authored_style:
            raise ValueError("source_content_template_required")
        if self.slides_markdown is not None or self.quality_policy == "release-v1":
            raise ValueError("source_content_generation_option_conflict")
        return self

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
    fallback_policy: Literal["reject", "per-slide-raster", "whole-deck-fidelity"] | None = None
    font_embedding: Literal["none", "subset"] = "none"
    required_capabilities: list[str] = Field(default_factory=list, max_length=64)

    @field_validator("required_capabilities")
    @classmethod
    def validate_capabilities(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item for item in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("requiredCapabilities must be unique non-blank values")
        return normalized

    @model_validator(mode="before")
    @classmethod
    def default_editable_font_embedding(cls, value: Any) -> Any:
        if (
            isinstance(value, dict)
            and value.get("mode") == "editable-hybrid"
            and "fontEmbedding" not in value
            and "font_embedding" not in value
        ):
            return {**value, "fontEmbedding": "subset"}
        return value

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
    schema_version: Literal["presentation-export-request/v2"] = "presentation-export-request/v2"
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
        if not value.startswith("/api/v2/") or "://" in value or "\\" in value or ".." in value:
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
            raise ValueError(f"required quality check cannot have {self.status} status")
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


class PublicRenderRuntimeIdentityV1(PublicWireModel):
    schema_version: Literal["public-render-runtime-binding/v1"] = "public-render-runtime-binding/v1"
    mode: Literal[
        "owned-local-controller-observed",
        "external-deployment-pinned",
    ]
    checkpoint_reuse_eligible: Literal[True]
    release_eligible: Literal[False]
    release_eligibility_reason: Literal["runtime-binding-is-not-release-authorization"]
    worker_image_digest: str | None = Field(default=None, pattern=DIGEST_PATTERN)
    font_inventory_digest: str | None = Field(default=None, pattern=DIGEST_PATTERN)
    limitations: list[
        Literal[
            "controller-worker-colocation-required",
            "system-font-inventory-unsealed",
        ]
    ] = Field(max_length=2)
    binding_digest: str = Field(pattern=DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_runtime_binding(self) -> "PublicRenderRuntimeIdentityV1":
        if self.mode == "owned-local-controller-observed":
            if self.worker_image_digest is not None or self.font_inventory_digest is not None:
                raise ValueError("owned-local runtime must not claim deployment pins")
            if self.limitations != [
                "controller-worker-colocation-required",
                "system-font-inventory-unsealed",
            ]:
                raise ValueError("owned-local runtime limitations are incomplete")
        elif (
            self.worker_image_digest is None
            or self.font_inventory_digest is None
            or self.limitations
            or self.worker_image_digest == "sha256:" + ("0" * 64)
            or self.font_inventory_digest == "sha256:" + ("0" * 64)
        ):
            raise ValueError("external runtime requires non-zero deployment pins")

        sealed = self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"binding_digest"},
        )
        expected_digest = (
            "sha256:"
            + sha256(
                json.dumps(
                    sealed,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        )
        if self.binding_digest != expected_digest:
            raise ValueError("render runtime bindingDigest does not match its payload")
        return self


class PublicRenderRuntimeIdentityV2(PublicWireModel):
    schema_version: Literal["public-render-runtime-binding/v2"] = "public-render-runtime-binding/v2"
    mode: Literal[
        "owned-local-controller-observed",
        "external-deployment-pinned",
    ]
    checkpoint_reuse_eligible: bool
    release_eligible: Literal[False]
    release_eligibility_reason: Literal["runtime-binding-is-not-release-authorization"]
    worker_image_digest: str | None = Field(default=None, pattern=DIGEST_PATTERN)
    chrome_runtime_digest: str | None = Field(default=None, pattern=DIGEST_PATTERN)
    font_inventory_digest: str = Field(pattern=DIGEST_PATTERN)
    limitations: list[
        Literal[
            "controller-worker-colocation-required",
            "worker-result-attestation-unavailable",
        ]
    ] = Field(max_length=1)
    binding_digest: str = Field(pattern=DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_runtime_binding(self) -> "PublicRenderRuntimeIdentityV2":
        zero_digest = "sha256:" + ("0" * 64)
        if self.mode == "owned-local-controller-observed":
            if (
                self.worker_image_digest is not None
                or self.chrome_runtime_digest in {None, zero_digest}
                or self.font_inventory_digest == zero_digest
                or self.checkpoint_reuse_eligible is not True
                or self.limitations != ["controller-worker-colocation-required"]
            ):
                raise ValueError("owned-local runtime identity is incomplete")
        elif (
            self.worker_image_digest in {None, zero_digest}
            or self.chrome_runtime_digest is not None
            or self.font_inventory_digest == zero_digest
            or self.checkpoint_reuse_eligible is not False
            or self.limitations != ["worker-result-attestation-unavailable"]
        ):
            raise ValueError("external runtime requires pins and disables checkpoint reuse")

        sealed = self.model_dump(
            mode="json",
            by_alias=True,
            exclude={"binding_digest"},
        )
        expected_digest = (
            "sha256:"
            + sha256(
                json.dumps(
                    sealed,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        )
        if self.binding_digest != expected_digest:
            raise ValueError("render runtime bindingDigest does not match its payload")
        return self


class PublicExportExecutionReceiptV1(PublicWireModel):
    schema_version: Literal["public-export-execution-receipt/v1"] = "public-export-execution-receipt/v1"
    slide_count: int = Field(ge=1, le=100)
    render_concurrency_limit: int = Field(ge=1, le=8)
    render_concurrency_peak: int = Field(ge=0, le=8)
    render_gate_wait_millis: int = Field(ge=0)
    render_scheduler_queue_wait_millis: int = Field(ge=0)
    render_worker_cumulative_millis: int = Field(ge=0)
    render_wall_millis: int = Field(ge=0)
    artifact_stage_millis: int = Field(ge=0)
    html_persist_millis: int = Field(ge=0)
    export_worker_millis: int = Field(ge=0)
    export_scheduler_queue_wait_millis: int = Field(ge=0)
    operation_wall_millis: int = Field(ge=0)
    render_execution_count: int = Field(ge=0, le=100)
    render_batch_execution_count: int = Field(ge=0, le=100)
    chromium_process_launch_count: int = Field(ge=0, le=100)
    browser_reuse_count: int = Field(ge=0, le=100)
    render_checkpoint_reuse_count: int = Field(ge=0, le=100)
    render_singleflight_join_count: int = Field(ge=0, le=100)
    render_runtime_identity: PublicRenderRuntimeIdentityV1 | PublicRenderRuntimeIdentityV2
    deck_render_identity_digest: str = Field(pattern=DIGEST_PATTERN)

    @model_validator(mode="after")
    def validate_render_counts(self) -> "PublicExportExecutionReceiptV1":
        if self.render_concurrency_peak > self.render_concurrency_limit:
            raise ValueError("renderConcurrencyPeak cannot exceed its limit")
        if (
            self.render_execution_count + self.render_checkpoint_reuse_count + self.render_singleflight_join_count
            > self.slide_count * 2
        ):
            raise ValueError("render accounting exceeds the bounded slide count")
        if self.chromium_process_launch_count != self.render_batch_execution_count:
            raise ValueError("each render batch must own exactly one Chromium process")
        if self.browser_reuse_count != self.render_execution_count - self.render_batch_execution_count:
            raise ValueError("browser reuse count must match batched fresh slides")
        return self


class PublicGenerationStageDurationV1(PublicWireModel):
    duration_millis: int = Field(ge=0)


PublicGenerationStageValueV1 = PublicGenerationStageDurationV1 | Literal["not_started"]


class PublicGenerationStageTimingsV1(PublicWireModel):
    outline: PublicGenerationStageValueV1
    provider: PublicGenerationStageValueV1
    authoring: PublicGenerationStageValueV1
    repair: PublicGenerationStageValueV1
    chromium_review: PublicGenerationStageValueV1
    publication: PublicGenerationStageValueV1


class PublicGenerationUsageAvailabilityV1(PublicWireModel):
    tokens: Literal["available", "partial", "unavailable", "not_started"]
    cost: Literal["available", "partial", "unavailable", "not_started"]


class PublicGenerationWorkerTimingV1(PublicWireModel):
    status: Literal["observed", "unavailable", "not_started"]
    worker: str | None = Field(default=None, min_length=1, max_length=160)
    peak_memory: int | None = Field(default=None, ge=0)
    peak_handles: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_worker_observation(self) -> "PublicGenerationWorkerTimingV1":
        observed_values = self.worker is not None or self.peak_memory is not None or self.peak_handles is not None
        if self.status == "observed" and not observed_values:
            raise ValueError("observed worker timing requires worker evidence")
        if self.status != "observed" and observed_values:
            raise ValueError("unobserved worker timing cannot carry worker evidence")
        return self


class PublicGenerationTimingV1(PublicWireModel):
    """Terminal public projection of one durable generation attempt."""

    schema_version: Literal["public-generation-timing/v1"] = "public-generation-timing/v1"
    attempt_number: int = Field(ge=0)
    outcome: Literal[
        "succeeded",
        "failed",
        "budget_exhausted",
        "cancelled",
        "dead_letter",
        "worker_crash",
    ]
    stages: PublicGenerationStageTimingsV1
    queue_wait_millis: int | Literal["not_started"]
    wall_millis: int | Literal["not_started"]
    logical_calls: int | Literal["not_started"]
    retry_reason: str | None = Field(default=None, min_length=1, max_length=160)
    usage_availability: PublicGenerationUsageAvailabilityV1
    worker: PublicGenerationWorkerTimingV1
    terminal_error: str | None = Field(default=None, min_length=1, max_length=160)
    source_receipt_digest: str | None = Field(default=None, pattern=DIGEST_PATTERN)

    @field_validator("queue_wait_millis", "wall_millis", "logical_calls")
    @classmethod
    def validate_non_negative_or_not_started(cls, value: int | str) -> int | str:
        if value == "not_started":
            return value
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("timing counters must be non-negative or not_started")
        return value

    @model_validator(mode="after")
    def validate_terminal_error(self) -> "PublicGenerationTimingV1":
        if self.outcome == "succeeded" and self.terminal_error is not None:
            raise ValueError("succeeded generation timing cannot carry terminalError")
        if self.outcome != "succeeded" and self.terminal_error is None:
            raise ValueError("failed generation timing requires terminalError")
        return self


class OperationExecutionReceiptV2(PublicWireModel):
    schema_version: Literal["operation-execution-receipt/v2"] = "operation-execution-receipt/v2"
    attempt_number: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    queue_position: int | None = Field(default=None, ge=1)
    stage: OperationPhase | None = None
    publication: Literal["pending", "published", "unavailable"]
    export: Literal["not_requested", "pending", "running", "completed", "failed"]
    timing: PublicExportExecutionReceiptV1 | None = None

    @model_validator(mode="after")
    def validate_attempts_and_timing(self) -> "OperationExecutionReceiptV2":
        if self.export == "not_requested" and self.timing is not None:
            raise ValueError("generation operations cannot carry export timing")
        return self


class OperationStatusV2(PublicWireModel):
    schema_version: Literal["operation-status/v2"] = "operation-status/v2"
    operation_id: str = Field(pattern=PUBLIC_ID_PATTERN)
    presentation_id: str = Field(pattern=PUBLIC_ID_PATTERN)
    kind: Literal["presentation.generate", "presentation.export"]
    status: OperationState
    phase: OperationPhase | None = None
    progress_percent: int | None = Field(default=None, ge=0, le=100)
    source_revision: int | None = Field(default=None, ge=0)
    accepted_at: AwareDatetime
    updated_at: AwareDatetime
    absolute_deadline_at: AwareDatetime
    error: PublicOperationErrorV2 | None = None
    generation_route_receipt: GenerationRouteReceiptV1 | None = None
    generation_timing_receipt: dict[str, Any] | None = None
    generation_timing: PublicGenerationTimingV1 | None = None
    content_integrity_receipt: ContentIntegrityReceiptV2 | None = None
    execution_receipt: OperationExecutionReceiptV2 | None = None
    quality_receipt: QualityReceiptV3 | None = None
    release_readiness: Literal["ready", "blocked", "not_assessed"] = "not_assessed"
    readiness_scope: Literal["artifact:fidelity"] = "artifact:fidelity"
    fidelity_artifact_ready: bool = False
    native_editability_ready: Literal[False] = False
    promotion_ready: Literal[False] = False
    deployment_authorized: Literal[False] = False
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
        if self.status == "succeeded" and self.source_revision is None:
            raise ValueError("succeeded operation requires sourceRevision")
        if (
            self.kind == "presentation.generate"
            and self.status == "succeeded"
            and self.generation_route_receipt is not None
            and self.generation_route_receipt.selected_route == "source-native"
            and self.content_integrity_receipt is None
        ):
            raise ValueError("registered-template success requires contentIntegrityReceipt")
        if self.content_integrity_receipt is not None:
            receipt = self.content_integrity_receipt
            if (
                self.kind != "presentation.generate"
                or self.status != "succeeded"
                or self.generation_route_receipt is None
                or self.source_revision is None
            ):
                raise ValueError("contentIntegrityReceipt requires succeeded generation lineage")
            if str(receipt.operation_id) != self.operation_id:
                raise ValueError("contentIntegrityReceipt must match the status operationId")
            if receipt.result_revision != self.source_revision:
                raise ValueError("contentIntegrityReceipt resultRevision must match sourceRevision")
            if receipt.route_strategy_receipt_digest != self.generation_route_receipt.receipt_digest:
                raise ValueError("contentIntegrityReceipt route receipt binding mismatch")
        if self.release_readiness == "ready" and (
            self.quality_receipt is None or self.quality_receipt.release_readiness != "ready"
        ):
            raise ValueError("ready operation requires a ready QualityReceiptV3")
        if self.release_readiness == "ready" and not self.fidelity_artifact_ready:
            raise ValueError("ready artifact:fidelity status requires fidelityArtifactReady")
        if self.fidelity_artifact_ready and (
            self.status != "succeeded" or self.release_readiness != "ready" or not self.artifacts
        ):
            raise ValueError("fidelityArtifactReady requires one succeeded ready artifact result")
        if any(
            artifact.operation_id != self.operation_id or artifact.presentation_id != self.presentation_id
            for artifact in self.artifacts
        ):
            raise ValueError("artifacts must match the status operationId and presentationId")
        if self.quality_receipt is not None and (
            self.quality_receipt.operation_id != self.operation_id
            or self.quality_receipt.presentation_id != self.presentation_id
        ):
            raise ValueError("qualityReceipt must match the status operationId and presentationId")
        if self.quality_receipt is not None and not self.artifacts:
            raise ValueError("qualityReceipt requires a non-empty artifacts list")
        if self.quality_receipt is not None and not any(
            artifact.digest == self.quality_receipt.artifact_digest for artifact in self.artifacts
        ):
            raise ValueError("at least one artifact digest must match qualityReceipt.artifactDigest")
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
        if any(right.sequence <= left.sequence for left, right in zip(self.events, self.events[1:], strict=False)):
            raise ValueError("events must be strictly ordered by sequence")
        return self


class CancelOperationReceiptV2(PublicWireModel):
    schema_version: Literal["operation-cancel-receipt/v2"] = "operation-cancel-receipt/v2"
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
            raise ValueError("download grant must use HTTPS except for explicit loopback development")
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
