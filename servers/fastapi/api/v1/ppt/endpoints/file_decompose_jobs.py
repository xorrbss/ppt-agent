import uuid
from typing import Annotated, List, Optional

# FastAPI dependency declarations intentionally call Depends in defaults.
# ruff: noqa: B008

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from models.decompose_job import DecomposeJobSnapshot
from services.decompose_job_service import (
    DECOMPOSE_JOB_SERVICE,
    DecomposeJobCapacityError,
    DecomposeJobConflictError,
)
from services.database import get_async_session
from services.references import ReferenceProviderError, admit_reference_snapshot
from utils.document_path_security import validate_document_paths
from utils.owner_context import get_current_owner_id


FILE_DECOMPOSE_JOBS_ROUTER = APIRouter(
    prefix="/files/decompose/jobs",
    tags=["Files"],
)


def _owner_key() -> str:
    owner_id = get_current_owner_id()
    return str(owner_id) if owner_id is not None else "anonymous"


@FILE_DECOMPOSE_JOBS_ROUTER.post(
    "",
    response_model=DecomposeJobSnapshot,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_decompose_job(
    file_paths: Annotated[Optional[List[str]], Body(embed=True)] = None,
    reference_snapshot_id: Annotated[Optional[uuid.UUID], Body()] = None,
    language: Annotated[Optional[str], Body()] = None,
    idempotency_key: Annotated[
        Optional[str], Header(alias="Idempotency-Key")
    ] = None,
    session: AsyncSession = Depends(get_async_session),
):
    """Queue decomposition without holding an HTTP connection open."""

    document_count: Optional[int] = None
    if file_paths:
        file_paths = validate_document_paths(file_paths)
    if reference_snapshot_id is not None:
        try:
            admitted = await admit_reference_snapshot(
                session,
                snapshot_id=reference_snapshot_id,
            )
        except ReferenceProviderError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.code,
            ) from exc
        if admitted.provider_id != "uploaded_document":
            raise HTTPException(
                status_code=400,
                detail="uploaded_reference_snapshot_required",
            )
        documents = admitted.payload().get("documents")
        if not isinstance(documents, list) or not documents:
            raise HTTPException(
                status_code=409,
                detail="reference_snapshot_payload_invalid",
            )
        document_count = len(documents)
    try:
        snapshot, _created = await DECOMPOSE_JOB_SERVICE.create_job(
            owner_key=_owner_key(),
            file_paths=file_paths,
            reference_snapshot_id=reference_snapshot_id,
            reference_document_count=document_count,
            language=language,
            idempotency_key=idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DecomposeJobConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "IDEMPOTENCY_KEY_PAYLOAD_CONFLICT",
                "message": "Idempotency-Key was already used for a different request.",
                "retryable": False,
            },
        ) from exc
    except DecomposeJobCapacityError as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "DOCUMENT_DECOMPOSE_CAPACITY_EXCEEDED",
                "message": "Too many document processing jobs are pending. Try again shortly.",
                "retryable": True,
            },
        ) from exc
    return snapshot


@FILE_DECOMPOSE_JOBS_ROUTER.get(
    "/{job_id}",
    response_model=DecomposeJobSnapshot,
)
async def get_decompose_job(job_id: uuid.UUID):
    """Return progress, a terminal result, or a safe terminal error."""

    snapshot = await DECOMPOSE_JOB_SERVICE.get_job(
        owner_key=_owner_key(),
        job_id=job_id,
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Document processing job not found")
    return snapshot


@FILE_DECOMPOSE_JOBS_ROUTER.delete(
    "/{job_id}",
    response_model=DecomposeJobSnapshot,
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_decompose_job(job_id: uuid.UUID, response: Response):
    """Request cooperative cancellation.

    A running file conversion reports ``cancelling`` until the current
    non-interruptible converter call returns; no additional file is started.
    """

    snapshot, cancellation_requested = await DECOMPOSE_JOB_SERVICE.cancel_job(
        owner_key=_owner_key(),
        job_id=job_id,
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Document processing job not found")
    if not cancellation_requested:
        response.status_code = status.HTTP_200_OK
    return snapshot
