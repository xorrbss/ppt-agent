from __future__ import annotations

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from services.source_locked.admission import admit_source_locked_v1
from services.source_locked.analysis import analyze_source
from services.source_locked.contracts import (
    AnalysisStage,
    GenerationMode,
    PageCountPolicy,
    SourceLockedAnalysisResponseV1,
    SourceLockedGenerationJobV1,
    SourceLockedGenerationResponseV1,
    SourceLockedJobStatus,
)
from services.source_locked.digests import idempotency_payload_digest, sha256_digest
from services.source_locked.errors import SourceLockedError
from services.source_locked.generation_store import DurableSourceLockedGenerationStore
from services.source_locked.job_runner import (
    generate_source_locked_result,
    run_source_locked_job,
)
from services.source_locked.job_store import DurableSourceLockedJobStore
from utils.owner_context import get_current_owner_id
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


def _job_store() -> DurableSourceLockedJobStore:
    return DurableSourceLockedJobStore.from_app_data()


def _owner_id() -> str:
    owner_id = get_current_owner_id()
    if owner_id is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return str(owner_id)


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
    if error.detail.code in {
        "ARTIFACT_NOT_FOUND",
        "ARTIFACT_ID_INVALID",
        "GENERATION_JOB_NOT_FOUND",
    }:
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
        owner_id = _owner_id()
        contract, payload = await _admit_and_read(
            files=files,
            generation_mode=generation_mode,
            page_count_policy=page_count_policy,
            preserve_all_image_occurrences=preserve_all_image_occurrences,
        )
        store = await run_in_threadpool(_generation_store)
        result = await run_in_threadpool(
            generate_source_locked_result,
            owner_id=owner_id,
            admission_contract=contract,
            source_bytes=payload,
            idempotency_key=idempotency_key,
            expected_payload_digest=expected_payload_digest,
            generation_store=store,
            restoration=_run_restoration,
        )
        assert result is not None
        return result
    except SourceLockedError as exc:
        raise _as_http_error(exc) from exc
    except HTTPException:
        raise
    except Exception as exc:
        error = SourceLockedError(
            "SOURCE_LOCKED_PERSISTENCE_FAILED",
            AnalysisStage.MISSING_CONTENT_VALIDATION,
            "The source-locked generation record and artifact could not be persisted",
        )
        raise _as_http_error(error) from exc


@SOURCE_LOCKED_ROUTER.post(
    "/jobs",
    response_model=SourceLockedGenerationJobV1,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_source_locked_job_v1(
    background_tasks: BackgroundTasks,
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
) -> SourceLockedGenerationJobV1:
    try:
        owner_id = _owner_id()
        contract, payload = await _admit_and_read(
            files=files,
            generation_mode=generation_mode,
            page_count_policy=page_count_policy,
            preserve_all_image_occurrences=preserve_all_image_occurrences,
        )
        calculated_payload_digest = idempotency_payload_digest(
            contract,
            sha256_digest(payload),
        )
        if calculated_payload_digest != expected_payload_digest:
            raise SourceLockedError(
                "IDEMPOTENCY_PAYLOAD_DIGEST_MISMATCH",
                AnalysisStage.SOURCE_ANALYSIS,
                "The idempotency payload digest does not match the admitted source snapshot",
            )
        job_store = await run_in_threadpool(_job_store)
        generation_store = await run_in_threadpool(_generation_store)
        snapshot, created = await run_in_threadpool(
            job_store.create,
            owner_id=owner_id,
            idempotency_key=idempotency_key,
            idempotency_payload_digest=calculated_payload_digest,
            admission_contract=contract,
            source_bytes=payload,
        )
        if snapshot.status == SourceLockedJobStatus.RUNNING:
            replay = snapshot.idempotent_replay
            snapshot, _ = await run_in_threadpool(
                job_store.recover_stale,
                owner_id=owner_id,
                job_id=snapshot.job_id,
            )
            assert snapshot is not None
            if replay:
                snapshot = snapshot.model_copy(update={"idempotent_replay": True})
        if created or snapshot.status == SourceLockedJobStatus.QUEUED:
            background_tasks.add_task(
                run_source_locked_job,
                owner_id=owner_id,
                job_id=snapshot.job_id,
                job_store=job_store,
                generation_store=generation_store,
            )
        return snapshot
    except SourceLockedError as exc:
        raise _as_http_error(exc) from exc


@SOURCE_LOCKED_ROUTER.get(
    "/jobs",
    response_model=list[SourceLockedGenerationJobV1],
)
async def list_source_locked_jobs_v1(
    background_tasks: BackgroundTasks,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[SourceLockedGenerationJobV1]:
    owner_id = _owner_id()
    job_store = await run_in_threadpool(_job_store)
    snapshots = await run_in_threadpool(
        job_store.list_for_owner,
        owner_id=owner_id,
        limit=limit,
    )
    result: list[SourceLockedGenerationJobV1] = []
    generation_store = None
    for snapshot in snapshots:
        recovered, _ = await run_in_threadpool(
            job_store.recover_stale,
            owner_id=owner_id,
            job_id=snapshot.job_id,
        )
        assert recovered is not None
        result.append(recovered)
        if recovered.status == SourceLockedJobStatus.QUEUED:
            if generation_store is None:
                generation_store = await run_in_threadpool(_generation_store)
            background_tasks.add_task(
                run_source_locked_job,
                owner_id=owner_id,
                job_id=recovered.job_id,
                job_store=job_store,
                generation_store=generation_store,
            )
    return result


@SOURCE_LOCKED_ROUTER.get(
    "/jobs/{job_id}",
    response_model=SourceLockedGenerationJobV1,
)
async def get_source_locked_job_v1(
    job_id: str,
    background_tasks: BackgroundTasks,
) -> SourceLockedGenerationJobV1:
    try:
        owner_id = _owner_id()
        job_store = await run_in_threadpool(_job_store)
        snapshot = await run_in_threadpool(job_store.get, owner_id=owner_id, job_id=job_id)
        if snapshot is None:
            raise SourceLockedError(
                "GENERATION_JOB_NOT_FOUND",
                AnalysisStage.SOURCE_ANALYSIS,
                "Source-locked generation job not found",
            )
        snapshot, _ = await run_in_threadpool(
            job_store.recover_stale,
            owner_id=owner_id,
            job_id=job_id,
        )
        assert snapshot is not None
        if snapshot.status == SourceLockedJobStatus.QUEUED:
            generation_store = await run_in_threadpool(_generation_store)
            background_tasks.add_task(
                run_source_locked_job,
                owner_id=owner_id,
                job_id=job_id,
                job_store=job_store,
                generation_store=generation_store,
            )
        return snapshot
    except SourceLockedError as exc:
        raise _as_http_error(exc) from exc


@SOURCE_LOCKED_ROUTER.post(
    "/jobs/{job_id}/cancel",
    response_model=SourceLockedGenerationJobV1,
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_source_locked_job_v1(
    job_id: str,
    response: Response,
) -> SourceLockedGenerationJobV1:
    owner_id = _owner_id()
    job_store = await run_in_threadpool(_job_store)
    snapshot, accepted = await run_in_threadpool(
        job_store.cancel,
        owner_id=owner_id,
        job_id=job_id,
    )
    if snapshot is None:
        raise _as_http_error(
            SourceLockedError(
                "GENERATION_JOB_NOT_FOUND",
                AnalysisStage.SOURCE_ANALYSIS,
                "Source-locked generation job not found",
            )
        )
    if not accepted:
        response.status_code = status.HTTP_200_OK
    return snapshot


@SOURCE_LOCKED_ROUTER.get("/artifacts/{artifact_id}", response_class=FileResponse)
async def download_source_locked_artifact_v1(artifact_id: str) -> FileResponse:
    try:
        owner_id = _owner_id()
        store = await run_in_threadpool(_generation_store)
        path, response = await run_in_threadpool(
            store.artifact_path,
            artifact_id,
            owner_id=owner_id,
        )
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
    except HTTPException:
        raise
    except Exception as exc:
        error = SourceLockedError(
            "SOURCE_LOCKED_STORAGE_FAILED",
            AnalysisStage.MISSING_CONTENT_VALIDATION,
            "The source-locked artifact store is unavailable",
        )
        raise _as_http_error(error) from exc
