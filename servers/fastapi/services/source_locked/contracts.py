from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GenerationMode(str, Enum):
    ADAPTIVE = "adaptive"
    SOURCE_LOCKED = "source_locked"


class PageCountPolicy(str, Enum):
    SOURCE = "source"
    USER_OR_AUTO = "user_or_auto"


class SourceMediaType(str, Enum):
    PPT = "ppt"
    PPTX = "pptx"
    PDF = "pdf"


class AnalysisStage(str, Enum):
    SOURCE_ANALYSIS = "source_analysis"
    SLIDE_IMAGE_RESTORATION = "slide_image_restoration"
    MISSING_CONTENT_VALIDATION = "missing_content_validation"


class StageStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class AdmissionContractV1(StrictFrozenModel):
    contract_version: Literal["source-locked-admission/v1"] = "source-locked-admission/v1"
    generation_mode: GenerationMode
    page_count_policy: PageCountPolicy
    preserve_all_image_occurrences: bool
    source_count: int = Field(ge=1)
    source_filename: str = Field(min_length=1)
    source_media_type: SourceMediaType

    @model_validator(mode="after")
    def enforce_source_locked_invariants(self) -> "AdmissionContractV1":
        if self.generation_mode == GenerationMode.SOURCE_LOCKED:
            if self.page_count_policy != PageCountPolicy.SOURCE:
                raise ValueError("source_locked requires page_count_policy=source")
            if not self.preserve_all_image_occurrences:
                raise ValueError("source_locked requires preserve_all_image_occurrences=true")
            if self.source_count != 1:
                raise ValueError("source_locked v1 requires exactly one source")
        return self


class SourceSnapshotV1(StrictFrozenModel):
    contract_version: Literal["source-locked-source-snapshot/v1"] = "source-locked-source-snapshot/v1"
    source_filename: str
    source_media_type: SourceMediaType
    byte_length: int = Field(gt=0)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    package_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class IdempotencyPayloadV1(StrictFrozenModel):
    contract_version: Literal["source-locked-idempotency-payload/v1"] = (
        "source-locked-idempotency-payload/v1"
    )
    admission_contract: AdmissionContractV1
    source_snapshot_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ImageAssetV1(StrictFrozenModel):
    asset_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    byte_length: int = Field(gt=0)
    package_parts: tuple[str, ...]


class ImageOccurrenceScope(str, Enum):
    DIRECT = "direct"
    LAYOUT = "layout"
    MASTER = "master"


class ImageOccurrenceV1(StrictFrozenModel):
    occurrence_id: str
    scope: ImageOccurrenceScope
    source_part: str
    relationship_id: str
    asset_part: str
    asset_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    slide_number: int | None = Field(default=None, ge=1)


class PptxSlideManifestV1(StrictFrozenModel):
    slide_number: int = Field(ge=1)
    slide_part: str
    layout_part: str | None
    master_part: str | None
    direct_occurrence_count: int = Field(ge=0)
    inherited_layout_occurrence_count: int = Field(ge=0)
    inherited_master_occurrence_count: int = Field(ge=0)
    effective_occurrence_count: int = Field(ge=0)


class PptxSourceManifestV1(StrictFrozenModel):
    contract_version: Literal["source-locked-pptx-manifest/v1"] = "source-locked-pptx-manifest/v1"
    projection_version: Literal["source-locked-deck-projection/v1"] = (
        "source-locked-deck-projection/v1"
    )
    source_type: Literal["pptx"] = "pptx"
    slide_count: int = Field(gt=0)
    page_count: int = Field(gt=0)
    media_assets: tuple[ImageAssetV1, ...]
    image_occurrences: tuple[ImageOccurrenceV1, ...]
    slides: tuple[PptxSlideManifestV1, ...]
    media_asset_count: int = Field(ge=0)
    image_occurrence_count: int = Field(ge=0)
    direct_occurrence_count: int = Field(ge=0)
    layout_occurrence_count: int = Field(ge=0)
    master_occurrence_count: int = Field(ge=0)
    inherited_occurrence_count: int = Field(ge=0)
    effective_occurrence_count: int = Field(ge=0)
    package_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    missing_content_count: Literal[0] = 0

    @model_validator(mode="after")
    def validate_count_readback(self) -> "PptxSourceManifestV1":
        direct = sum(o.scope == ImageOccurrenceScope.DIRECT for o in self.image_occurrences)
        layout = sum(o.scope == ImageOccurrenceScope.LAYOUT for o in self.image_occurrences)
        master = sum(o.scope == ImageOccurrenceScope.MASTER for o in self.image_occurrences)
        if (direct, layout, master) != (
            self.direct_occurrence_count,
            self.layout_occurrence_count,
            self.master_occurrence_count,
        ):
            raise ValueError("image occurrence scope counts do not match manifest")
        if self.image_occurrence_count != len(self.image_occurrences):
            raise ValueError("image_occurrence_count does not match occurrence records")
        if self.media_asset_count != len(self.media_assets):
            raise ValueError("media_asset_count does not match unique media assets")
        if self.effective_occurrence_count != self.direct_occurrence_count + self.inherited_occurrence_count:
            raise ValueError("effective occurrence count is inconsistent")
        return self


class PdfPageRenderV1(StrictFrozenModel):
    page_number: int = Field(ge=1)
    target_filename: str
    image_format: Literal["png"] = "png"
    lossless_encoding: Literal[True] = True
    authoritative_visual: Literal[True] = True
    source_object_editability: Literal["not_promised"] = "not_promised"


class OcrSidecarPolicyV1(StrictFrozenModel):
    enabled: bool = False
    authoritative: Literal[False] = False
    purpose: Literal["search_and_accessibility_only"] = "search_and_accessibility_only"


class PdfSourceManifestV1(StrictFrozenModel):
    contract_version: Literal["source-locked-pdf-manifest/v1"] = "source-locked-pdf-manifest/v1"
    projection_version: Literal["source-locked-deck-projection/v1"] = (
        "source-locked-deck-projection/v1"
    )
    source_type: Literal["pdf"] = "pdf"
    page_count: int = Field(gt=0)
    slide_count: int = Field(gt=0)
    page_render_bundle: tuple[PdfPageRenderV1, ...]
    ocr_sidecar: OcrSidecarPolicyV1
    package_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    missing_content_count: Literal[0] = 0

    @model_validator(mode="after")
    def validate_page_mapping(self) -> "PdfSourceManifestV1":
        if self.page_count != self.slide_count or self.page_count != len(self.page_render_bundle):
            raise ValueError("PDF source_locked requires exactly one slide per page")
        expected_pages = list(range(1, self.page_count + 1))
        if [page.page_number for page in self.page_render_bundle] != expected_pages:
            raise ValueError("PDF page render bundle must be ordered and complete")
        return self


SourceManifestV1 = Annotated[
    Union[PptxSourceManifestV1, PdfSourceManifestV1],
    Field(discriminator="source_type"),
]


class SourceAnalysisSummaryV1(StrictFrozenModel):
    source_type: Literal["pptx", "pdf"]
    page_count: int = Field(gt=0)
    slide_count: int = Field(gt=0)
    media_asset_count: int = Field(ge=0)
    image_occurrence_count: int = Field(ge=0)
    inherited_occurrence_count: int = Field(ge=0)


class ProgressStageV1(StrictFrozenModel):
    stage: AnalysisStage
    status: StageStatus


class SourceAnalysisSealV1(StrictFrozenModel):
    contract_version: Literal["source-analysis-seal/v1"] = "source-analysis-seal/v1"
    seal_id: str
    seal_scope: Literal["source_analysis_only"] = "source_analysis_only"
    idempotency_key: str
    idempotency_payload_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_snapshot_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    manifest_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    issued_at: datetime


class SourceLockedAnalysisResponseV1(StrictFrozenModel):
    contract_version: Literal["source-locked-analysis-response/v1"] = (
        "source-locked-analysis-response/v1"
    )
    admission_contract: AdmissionContractV1
    source_snapshot: SourceSnapshotV1
    manifest: SourceManifestV1
    summary: SourceAnalysisSummaryV1
    analysis_seal: SourceAnalysisSealV1
    progress_stages: tuple[ProgressStageV1, ...]
    fallback_allowed: Literal[False] = False


class SourceLockedErrorV1(StrictFrozenModel):
    contract_version: Literal["source-locked-error/v1"] = "source-locked-error/v1"
    code: str
    stage: AnalysisStage
    message: str
    fallback_allowed: Literal[False] = False
