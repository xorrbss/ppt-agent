import uuid
from typing import Annotated, List, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Response, status

from models.decompose_job import DecomposeJobSnapshot
from services.decompose_job_service import (
    DECOMPOSE_JOB_SERVICE,
    DecomposeJobCapacityError,
    DecomposeJobConflictError,
)
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
    file_paths: Annotated[List[str], Body(embed=True)],
    language: Annotated[Optional[str], Body()] = None,
    idempotency_key: Annotated[
        Optional[str], Header(alias="Idempotency-Key")
    ] = None,
):
    """Queue decomposition without holding an HTTP connection open."""

    file_paths = validate_document_paths(file_paths)
    try:
        snapshot, _created = await DECOMPOSE_JOB_SERVICE.create_job(
            owner_key=_owner_key(),
            file_paths=file_paths,
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
