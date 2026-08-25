from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from services.source_locked.admission import admit_source_locked_v1
from services.source_locked.analysis import analyze_source
from services.source_locked.contracts import (
    AnalysisStage,
    GenerationMode,
    PageCountPolicy,
    ProgressStageV1,
    SourceLockedArtifactV1,
    SourceLockedAnalysisResponseV1,
    SourceLockedGenerationResponseV1,
    SourceLockedRestorationVerificationV1,
    StageStatus,
)
from services.source_locked.digests import idempotency_payload_digest, sha256_digest
from services.source_locked.errors import SourceLockedError
from services.source_locked.generation_store import (
    DurableSourceLockedGenerationStore,
    generation_identity,
)
from utils.upload_limits import get_single_upload_limit_bytes, reject_if_declared_too_large


SOURCE_LOCKED_ROUTER = APIRouter(prefix="/source-locked/v1", tags=["Source Locked"])


async def _read_bounded(upload: UploadFile, limit_bytes: int) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while chunk := await upload.read(min(1024 * 1024, limit_bytes - size + 1)):
        size += len(chunk)
        if size > limit_bytes:
            raise SourceLockedError(
                "SOURCE_TOO_LARGE",
                AnalysisStage.SOURCE_ANALYSIS,
                "The source file exceeds the configured upload limit",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _generation_store() -> DurableSourceLockedGenerationStore:
    return DurableSourceLockedGenerationStore.from_app_data()


def _run_restoration(*, admission_contract, source_snapshot, manifest, source_bytes):
    # Kept as a narrow adapter so the restoration worker remains independently
    # testable and this endpoint owns only admission/persistence concerns.
    from services.source_locked.restoration import restore_source_locked_v1

    return restore_source_locked_v1(
        admission_contract=admission_contract,
        source_snapshot=source_snapshot,
        manifest=manifest,
        source_bytes=source_bytes,
    )


def _http_status(error: SourceLockedError) -> int:
    if error.detail.code == "SOURCE_TOO_LARGE":
        return 413
    if error.detail.code in {"ARTIFACT_NOT_FOUND", "ARTIFACT_ID_INVALID"}:
        return 404
    if error.detail.code.startswith("IDEMPOTENCY_"):
        return 409
    return 422


def _as_http_error(error: SourceLockedError) -> HTTPException:
    return HTTPException(
        status_code=_http_status(error),
        detail=error.detail.model_dump(mode="json"),
    )


async def _admit_and_read(
    *,
    files: list[UploadFile] | None,
    generation_mode: GenerationMode,
    page_count_policy: PageCountPolicy,
    preserve_all_image_occurrences: bool,
):
    contract = admit_source_locked_v1(
        [upload.filename or "" for upload in (files or [])],
        generation_mode=generation_mode,
        page_count_policy=page_count_policy,
        preserve_all_image_occurrences=preserve_all_image_occurrences,
    )
    assert files is not None
    upload = files[0]
    limit_bytes = get_single_upload_limit_bytes()
    try:
        reject_if_declared_too_large(upload, limit_bytes=limit_bytes, label="Source file")
    except HTTPException as exc:
        raise SourceLockedError(
            "SOURCE_TOO_LARGE",
            AnalysisStage.SOURCE_ANALYSIS,
            "The source file exceeds the configured upload limit",
        ) from exc
    return contract, await _read_bounded(upload, limit_bytes)


@SOURCE_LOCKED_ROUTER.post("/analyze", response_model=SourceLockedAnalysisResponseV1)
async def analyze_source_locked_v1(
    files: list[UploadFile] | None = File(default=None),
    generation_mode: GenerationMode = Form(...),
    page_count_policy: PageCountPolicy = Form(...),
    preserve_all_image_occurrences: bool = Form(...),
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=200),
    expected_payload_digest: str = Header(
        ...,
        alias="X-Idempotency-Payload-Digest",
        pattern=r"^sha256:[0-9a-f]{64}$",
    ),
) -> SourceLockedAnalysisResponseV1:
    try:
        contract, payload = await _admit_and_read(
            files=files,
            generation_mode=generation_mode,
            page_count_policy=page_count_policy,
            preserve_all_image_occurrences=preserve_all_image_occurrences,
        )
        return analyze_source(
            admission_contract=contract,
            data=payload,
            idempotency_key=idempotency_key,
            expected_idempotency_payload_digest=expected_payload_digest,
        )
    except SourceLockedError as exc:
        raise _as_http_error(exc) from exc


@SOURCE_LOCKED_ROUTER.post("/generate", response_model=SourceLockedGenerationResponseV1)
async def generate_source_locked_v1(
    files: list[UploadFile] | None = File(default=None),
    generation_mode: GenerationMode = Form(...),
    page_count_policy: PageCountPolicy = Form(...),
    preserve_all_image_occurrences: bool = Form(...),
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=200),
    expected_payload_digest: str = Header(
        ...,
        alias="X-Idempotency-Payload-Digest",
        pattern=r"^sha256:[0-9a-f]{64}$",
    ),
) -> SourceLockedGenerationResponseV1:
    try:
        contract, payload = await _admit_and_read(
            files=files,
            generation_mode=generation_mode,
            page_count_policy=page_count_policy,
            preserve_all_image_occurrences=preserve_all_image_occurrences,
        )
        source_digest = sha256_digest(payload)
        calculated_payload_digest = idempotency_payload_digest(contract, source_digest)
        if calculated_payload_digest != expected_payload_digest:
            raise SourceLockedError(
                "IDEMPOTENCY_PAYLOAD_DIGEST_MISMATCH",
                AnalysisStage.SOURCE_ANALYSIS,
                "The idempotency payload digest does not match the admitted source snapshot",
            )

        store = await run_in_threadpool(_generation_store)
        existing = await run_in_threadpool(
            store.lookup,
            idempotency_key=idempotency_key,
            idempotency_payload_digest=calculated_payload_digest,
        )
        if existing is not None:
            return existing

        analysis = analyze_source(
            admission_contract=contract,
            data=payload,
            idempotency_key=idempotency_key,
            expected_idempotency_payload_digest=expected_payload_digest,
        )
        try:
            restoration = await run_in_threadpool(
                _run_restoration,
                admission_contract=contract,
                source_snapshot=analysis.source_snapshot,
                manifest=analysis.manifest,
                source_bytes=payload,
            )
        except SourceLockedError:
            raise
        except Exception as exc:
            raise SourceLockedError(
                "SOURCE_RESTORATION_FAILED",
                AnalysisStage.SLIDE_IMAGE_RESTORATION,
                "Source-locked slide and image restoration failed",
            ) from exc

        artifact_bytes = restoration.artifact_bytes
        verification_in = restoration.verification
        verification = SourceLockedRestorationVerificationV1(
            source_type=verification_in.source_type,
            source_snapshot_sha256=verification_in.source_snapshot_sha256,
            output_package_sha256=verification_in.output_package_sha256,
            page_count=verification_in.page_count,
            slide_count=verification_in.slide_count,
            direct_occurrence_count=verification_in.direct_occurrence_count,
            rendered_page_count=verification_in.rendered_page_count,
            authoritative_image_digests=tuple(verification_in.authoritative_image_digests),
            missing_content_count=verification_in.missing_content_count,
            byte_exact_source_package=verification_in.byte_exact_source_package,
            fallback_allowed=verification_in.fallback_allowed,
        )
        if restoration.artifact_media_type != SourceLockedArtifactV1.model_fields["media_type"].default:
            raise SourceLockedError(
                "ARTIFACT_MEDIA_TYPE_INVALID",
                AnalysisStage.MISSING_CONTENT_VALIDATION,
                "The restoration worker did not produce a PPTX artifact",
            )

        generation_record_id, artifact_id = generation_identity(
            idempotency_key,
            calculated_payload_digest,
        )
        artifact_digest = sha256_digest(artifact_bytes)
        artifact = SourceLockedArtifactV1(
            artifact_id=artifact_id,
            filename=f"source-locked-{artifact_id.removeprefix('sla_')[:16]}.pptx",
            byte_length=len(artifact_bytes),
            sha256=artifact_digest,
            download_url=f"/api/v1/ppt/source-locked/v1/artifacts/{artifact_id}",
            created_at=datetime.now(timezone.utc),
            source_type=analysis.summary.source_type,
            slide_count=analysis.summary.slide_count,
        )
        response = SourceLockedGenerationResponseV1(
            generation_record_id=generation_record_id,
            admission_contract=analysis.admission_contract,
            source_snapshot=analysis.source_snapshot,
            manifest=analysis.manifest,
            summary=analysis.summary,
            analysis_seal=analysis.analysis_seal,
            restoration_verification=verification,
            artifact=artifact,
            progress_stages=(
                ProgressStageV1(stage=AnalysisStage.SOURCE_ANALYSIS, status=StageStatus.COMPLETED),
                ProgressStageV1(
                    stage=AnalysisStage.SLIDE_IMAGE_RESTORATION,
                    status=StageStatus.COMPLETED,
                ),
                ProgressStageV1(
                    stage=AnalysisStage.MISSING_CONTENT_VALIDATION,
                    status=StageStatus.COMPLETED,
                ),
            ),
        )
        return await run_in_threadpool(
            store.persist,
            response=response,
            artifact_bytes=artifact_bytes,
        )
    except SourceLockedError as exc:
        raise _as_http_error(exc) from exc
    except ValueError as exc:
        error = SourceLockedError(
            "RESTORATION_READBACK_INVALID",
            AnalysisStage.MISSING_CONTENT_VALIDATION,
            "Restoration readback did not satisfy the source-locked contract",
        )
        raise _as_http_error(error) from exc
    except Exception as exc:
        error = SourceLockedError(
            "SOURCE_LOCKED_PERSISTENCE_FAILED",
            AnalysisStage.MISSING_CONTENT_VALIDATION,
            "The source-locked generation record and artifact could not be persisted",
        )
        raise _as_http_error(error) from exc


@SOURCE_LOCKED_ROUTER.get("/artifacts/{artifact_id}", response_class=FileResponse)
async def download_source_locked_artifact_v1(artifact_id: str) -> FileResponse:
    try:
        store = await run_in_threadpool(_generation_store)
        path, response = await run_in_threadpool(store.artifact_path, artifact_id)
        return FileResponse(
            path,
            media_type=response.artifact.media_type,
            filename=response.artifact.filename,
            headers={
                "ETag": f'"{response.artifact.sha256}"',
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
    except SourceLockedError as exc:
        raise _as_http_error(exc) from exc
    except Exception as exc:
        error = SourceLockedError(
            "SOURCE_LOCKED_STORAGE_FAILED",
            AnalysisStage.MISSING_CONTENT_VALIDATION,
            "The source-locked artifact store is unavailable",
        )
        raise _as_http_error(error) from exc
