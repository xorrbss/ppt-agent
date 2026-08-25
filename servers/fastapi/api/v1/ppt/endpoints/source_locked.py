from __future__ import annotations

from fastapi import APIRouter, File, Form, Header, HTTPException, UploadFile

from services.source_locked.admission import admit_source_locked_v1
from services.source_locked.analysis import analyze_source
from services.source_locked.contracts import (
    AnalysisStage,
    GenerationMode,
    PageCountPolicy,
    SourceLockedAnalysisResponseV1,
)
from services.source_locked.errors import SourceLockedError
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
        # Count and extension admission intentionally happen before reading any
        # file bytes or invoking format analyzers.
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
        payload = await _read_bounded(upload, limit_bytes)
        return analyze_source(
            admission_contract=contract,
            data=payload,
            idempotency_key=idempotency_key,
            expected_idempotency_payload_digest=expected_payload_digest,
        )
    except SourceLockedError as exc:
        if exc.detail.code == "SOURCE_TOO_LARGE":
            status_code = 413
        else:
            status_code = 409 if exc.detail.code.startswith("IDEMPOTENCY_") else 422
        raise HTTPException(
            status_code=status_code,
            detail=exc.detail.model_dump(mode="json"),
        ) from exc
