from __future__ import annotations

from services.source_locked.contracts import (
    AdmissionContractV1,
    AnalysisStage,
    PdfSourceManifestV1,
    PptxSourceManifestV1,
    ProgressStageV1,
    SourceAnalysisSummaryV1,
    SourceLockedAnalysisResponseV1,
    SourceMediaType,
    SourceSnapshotV1,
    StageStatus,
)
from services.source_locked.digests import idempotency_payload_digest, sha256_digest
from services.source_locked.errors import SourceLockedError
from services.source_locked.pdf import analyze_pdf
from services.source_locked.pptx import analyze_pptx
from services.source_locked.seal import SOURCE_ANALYSIS_SEAL_STORE, WriteOnceSourceAnalysisSealStore


def analyze_source(
    *,
    admission_contract: AdmissionContractV1,
    data: bytes,
    idempotency_key: str,
    expected_idempotency_payload_digest: str,
    seal_store: WriteOnceSourceAnalysisSealStore = SOURCE_ANALYSIS_SEAL_STORE,
) -> SourceLockedAnalysisResponseV1:
    if not data:
        raise SourceLockedError(
            "SOURCE_EMPTY",
            AnalysisStage.SOURCE_ANALYSIS,
            "The uploaded source file is empty",
        )
    source_digest = sha256_digest(data)
    snapshot = SourceSnapshotV1(
        source_filename=admission_contract.source_filename,
        source_media_type=admission_contract.source_media_type,
        byte_length=len(data),
        sha256=source_digest,
        package_sha256=source_digest,
    )
    calculated_payload_digest = idempotency_payload_digest(admission_contract, source_digest)
    if expected_idempotency_payload_digest != calculated_payload_digest:
        raise SourceLockedError(
            "IDEMPOTENCY_PAYLOAD_DIGEST_MISMATCH",
            AnalysisStage.SOURCE_ANALYSIS,
            "The idempotency payload digest does not match the admitted source snapshot",
        )

    if admission_contract.source_media_type == SourceMediaType.PPT:
        raise SourceLockedError(
            "SOURCE_CONVERSION_REQUIRED",
            AnalysisStage.SOURCE_ANALYSIS,
            "Legacy .ppt was admitted, but source analysis requires a reviewed lossless conversion adapter",
        )
    if admission_contract.source_media_type == SourceMediaType.PPTX:
        manifest = analyze_pptx(data)
    elif admission_contract.source_media_type == SourceMediaType.PDF:
        manifest = analyze_pdf(data)
    else:  # pragma: no cover - AdmissionContractV1 makes this unreachable.
        raise SourceLockedError(
            "SOURCE_TYPE_UNSUPPORTED",
            AnalysisStage.SOURCE_ANALYSIS,
            "Unsupported source type",
        )

    if isinstance(manifest, PptxSourceManifestV1):
        summary = SourceAnalysisSummaryV1(
            source_type="pptx",
            page_count=manifest.page_count,
            slide_count=manifest.slide_count,
            media_asset_count=manifest.media_asset_count,
            image_occurrence_count=manifest.image_occurrence_count,
            inherited_occurrence_count=manifest.inherited_occurrence_count,
        )
    elif isinstance(manifest, PdfSourceManifestV1):
        summary = SourceAnalysisSummaryV1(
            source_type="pdf",
            page_count=manifest.page_count,
            slide_count=manifest.slide_count,
            media_asset_count=0,
            image_occurrence_count=0,
            inherited_occurrence_count=0,
        )

    seal = seal_store.issue_or_get(
        idempotency_key=idempotency_key,
        idempotency_payload_digest=calculated_payload_digest,
        source_snapshot_sha256=source_digest,
        manifest=manifest,
    )
    return SourceLockedAnalysisResponseV1(
        admission_contract=admission_contract,
        source_snapshot=snapshot,
        manifest=manifest,
        summary=summary,
        analysis_seal=seal,
        progress_stages=(
            ProgressStageV1(stage=AnalysisStage.SOURCE_ANALYSIS, status=StageStatus.COMPLETED),
            ProgressStageV1(stage=AnalysisStage.SLIDE_IMAGE_RESTORATION, status=StageStatus.PENDING),
            ProgressStageV1(stage=AnalysisStage.MISSING_CONTENT_VALIDATION, status=StageStatus.PENDING),
        ),
    )
