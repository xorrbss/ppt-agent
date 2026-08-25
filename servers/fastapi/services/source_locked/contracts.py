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
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SourceLockedJobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


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


class PptSourceManifestV1(StrictFrozenModel):
    contract_version: Literal["source-locked-ppt-manifest/v1"] = (
        "source-locked-ppt-manifest/v1"
    )
    projection_version: Literal["source-locked-deck-projection/v1"] = (
        "source-locked-deck-projection/v1"
    )
    source_type: Literal["ppt"] = "ppt"
    package_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    original_package_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    converted_package_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    converted_manifest: PptxSourceManifestV1
    source_page_count: int = Field(gt=0)
    converted_page_count: int = Field(gt=0)
    source_visual_page_digests: tuple[
        Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")], ...
    ]
    converted_visual_page_digests: tuple[
        Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")], ...
    ]
    missing_content_count: Literal[0] = 0
    visual_readback_exact: Literal[True] = True
    isolated_libreoffice_profile: Literal[True] = True
    power_point_com_used: Literal[False] = False

    @model_validator(mode="after")
    def validate_conversion_provenance(self) -> "PptSourceManifestV1":
        if self.package_sha256 != self.original_package_sha256:
            raise ValueError("PPT manifest must remain bound to the original package")
        if self.converted_package_sha256 != self.converted_manifest.package_sha256:
            raise ValueError("converted PPTX digest does not match its structural manifest")
        if not (
            self.source_page_count
            == self.converted_page_count
            == self.converted_manifest.page_count
            == self.converted_manifest.slide_count
        ):
            raise ValueError("PPT conversion page and slide counts must remain invariant")
        if (
            len(self.source_visual_page_digests) != self.source_page_count
            or self.source_visual_page_digests != self.converted_visual_page_digests
        ):
            raise ValueError("PPT conversion visual page readback must be exact and complete")
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
    Union[PptSourceManifestV1, PptxSourceManifestV1, PdfSourceManifestV1],
    Field(discriminator="source_type"),
]


class SourceAnalysisSummaryV1(StrictFrozenModel):
    source_type: Literal["ppt", "pptx", "pdf"]
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


class SourceLockedRestorationVerificationV1(StrictFrozenModel):
    contract_version: Literal["source-locked-restoration-verification/v1"] = (
        "source-locked-restoration-verification/v1"
    )
    source_type: Literal["ppt", "pptx", "pdf"]
    source_snapshot_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    output_package_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    page_count: int = Field(gt=0)
    slide_count: int = Field(gt=0)
    direct_occurrence_count: int = Field(ge=0)
    rendered_page_count: int = Field(ge=0)
    authoritative_image_digests: tuple[
        Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")], ...
    ]
    missing_content_count: Literal[0] = 0
    byte_exact_source_package: bool
    fallback_allowed: Literal[False] = False


class SourceLockedArtifactV1(StrictFrozenModel):
    contract_version: Literal["source-locked-artifact/v1"] = "source-locked-artifact/v1"
    artifact_id: str = Field(pattern=r"^sla_[0-9a-f]{64}$")
    filename: str = Field(pattern=r"^source-locked-[0-9a-f]{16}\.pptx$")
    media_type: Literal[
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ] = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    byte_length: int = Field(gt=0)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    download_url: str = Field(pattern=r"^/api/v1/ppt/source-locked/v1/artifacts/sla_[0-9a-f]{64}$")
    created_at: datetime
    source_type: Literal["ppt", "pptx", "pdf"]
    slide_count: int = Field(gt=0)


class SourceLockedGenerationResponseV1(StrictFrozenModel):
    contract_version: Literal["source-locked-generation-response/v1"] = (
        "source-locked-generation-response/v1"
    )
    generation_record_id: str = Field(pattern=r"^slgen_[0-9a-f]{64}$")
    admission_contract: AdmissionContractV1
    source_snapshot: SourceSnapshotV1
    manifest: SourceManifestV1
    summary: SourceAnalysisSummaryV1
    analysis_seal: SourceAnalysisSealV1
    restoration_verification: SourceLockedRestorationVerificationV1
    artifact: SourceLockedArtifactV1
    progress_stages: tuple[ProgressStageV1, ...]
    idempotent_replay: bool = False
    fallback_allowed: Literal[False] = False

    @model_validator(mode="after")
    def require_completed_generation(self) -> "SourceLockedGenerationResponseV1":
        expected = [
            AnalysisStage.SOURCE_ANALYSIS,
            AnalysisStage.SLIDE_IMAGE_RESTORATION,
            AnalysisStage.MISSING_CONTENT_VALIDATION,
        ]
        if [item.stage for item in self.progress_stages] != expected:
            raise ValueError("generation progress stages must be complete and ordered")
        if any(item.status != StageStatus.COMPLETED for item in self.progress_stages):
            raise ValueError("generation response requires all stages completed")
        if self.artifact.sha256 != self.restoration_verification.output_package_sha256:
            raise ValueError("artifact digest does not match restoration verification")
        if self.artifact.slide_count != self.restoration_verification.slide_count:
            raise ValueError("artifact slide count does not match restoration verification")
        verification = self.restoration_verification
        if verification.source_snapshot_sha256 != self.source_snapshot.sha256:
            raise ValueError("restoration verification does not match the source snapshot")
        if (
            self.summary.source_type != verification.source_type
            or self.summary.page_count != verification.page_count
            or self.summary.slide_count != verification.slide_count
            or self.artifact.source_type != verification.source_type
        ):
            raise ValueError("summary and artifact metadata do not match restoration readback")
        if verification.page_count != verification.slide_count:
            raise ValueError("source-locked output requires one slide per source page")
        if verification.source_type == "pptx":
            if not verification.byte_exact_source_package:
                raise ValueError("PPTX source-locked generation must preserve the source package bytes")
            if verification.output_package_sha256 != self.source_snapshot.sha256:
                raise ValueError("PPTX source-locked artifact must be byte-exact")
        elif verification.source_type == "ppt":
            if not isinstance(self.manifest, PptSourceManifestV1):
                raise ValueError("PPT generation requires sealed conversion provenance")
            if self.source_snapshot.source_media_type != SourceMediaType.PPT:
                raise ValueError("PPT generation must remain bound to the original media type")
            if self.manifest.package_sha256 != self.source_snapshot.sha256:
                raise ValueError("PPT conversion manifest does not match the original snapshot")
            if verification.byte_exact_source_package:
                raise ValueError("legacy PPT conversion cannot claim byte-exact source packaging")
            if verification.rendered_page_count != 0:
                raise ValueError("PPT conversion verification does not use PDF raster restoration")
            if (
                verification.direct_occurrence_count
                != self.manifest.converted_manifest.direct_occurrence_count
                or verification.authoritative_image_digests
                != tuple(
                    item.asset_digest
                    for item in self.manifest.converted_manifest.image_occurrences
                )
            ):
                raise ValueError("PPT converted image occurrence readback is incomplete")
        elif verification.rendered_page_count != verification.page_count:
            raise ValueError("PDF source-locked generation must render every page")
        return self


class SourceLockedErrorV1(StrictFrozenModel):
    contract_version: Literal["source-locked-error/v1"] = "source-locked-error/v1"
    code: str
    stage: AnalysisStage
    message: str
    fallback_allowed: Literal[False] = False


class SourceLockedGenerationJobV1(StrictFrozenModel):
    contract_version: Literal["source-locked-job/v1"] = "source-locked-job/v1"
    job_id: str = Field(pattern=r"^sljob_[0-9a-f]{64}$")
    status: SourceLockedJobStatus
    source_filename: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime
    progress_stages: tuple[ProgressStageV1, ...]
    cancel_requested: bool = False
    idempotent_replay: bool = False
    result: SourceLockedGenerationResponseV1 | None = None
    error: SourceLockedErrorV1 | None = None
    fallback_allowed: Literal[False] = False

    @model_validator(mode="after")
    def validate_job_state(self) -> "SourceLockedGenerationJobV1":
        expected = [
            AnalysisStage.SOURCE_ANALYSIS,
            AnalysisStage.SLIDE_IMAGE_RESTORATION,
            AnalysisStage.MISSING_CONTENT_VALIDATION,
        ]
        if [item.stage for item in self.progress_stages] != expected:
            raise ValueError("job progress stages must be ordered and complete")
        if self.status == SourceLockedJobStatus.SUCCEEDED:
            if self.result is None or self.error is not None or self.cancel_requested:
                raise ValueError("successful jobs require an immutable result")
            if any(item.status != StageStatus.COMPLETED for item in self.progress_stages):
                raise ValueError("successful jobs require all stages completed")
        elif self.status == SourceLockedJobStatus.FAILED:
            if self.error is None or self.result is not None:
                raise ValueError("failed jobs require one terminal error")
        elif self.status == SourceLockedJobStatus.CANCELLED:
            if self.result is not None:
                raise ValueError("cancelled jobs cannot expose a generation result")
            if not self.cancel_requested:
                raise ValueError("cancelled jobs require an accepted cancel request")
        elif self.result is not None or self.error is not None:
            raise ValueError("non-terminal jobs cannot expose terminal state")
        return self
