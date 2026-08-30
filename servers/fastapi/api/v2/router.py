"""Thin FastAPI adapters for the canonical public v2 contract."""

from __future__ import annotations

from typing import Annotated, Awaitable, TypeVar
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, status
from fastapi.responses import RedirectResponse, StreamingResponse

from api.v2.contracts import (
    CancelOperationReceiptV2,
    OperationEventPageV2,
    OperationReceiptV2,
    OperationStatusV2,
    PresentationExportRequestV2,
    PresentationGenerationRequestV2,
    PUBLIC_ID_PATTERN,
)
from api.v2.orchestration import (
    PublicRequestActor,
    PublicV2OrchestrationError,
    PublicV2Orchestrator,
    get_public_request_actor,
    get_public_v2_orchestrator,
)
from services.artifact_store import (
    ArtifactBindingError,
    ArtifactByteRange,
    ArtifactNotFound,
    ArtifactStoreError,
    configured_local_development_artifact_store,
)


API_V2_PUBLIC_ROUTER = APIRouter(prefix="/api/v2", tags=["Public v2"])
_T = TypeVar("_T")

ActorDependency = Annotated[PublicRequestActor, Depends(get_public_request_actor)]
OrchestratorDependency = Annotated[
    PublicV2Orchestrator, Depends(get_public_v2_orchestrator)
]
IdempotencyKeyHeader = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=256,
        description="Opaque owner-scoped idempotency key",
    ),
]
PublicIdPath = Annotated[str, Path(pattern=PUBLIC_ID_PATTERN)]


def _idempotency_key(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"reasonCode": "idempotency_key_required"},
        )
    return normalized


async def _delegate(call: Awaitable[_T]) -> _T:
    try:
        return await call
    except PublicV2OrchestrationError as error:
        headers = None
        if error.retry_after_seconds is not None:
            headers = {"Retry-After": str(error.retry_after_seconds)}
        raise HTTPException(
            status_code=error.status_code,
            detail={"reasonCode": error.reason_code},
            headers=headers,
        ) from error


def _single_byte_range(value: str | None) -> ArtifactByteRange | None:
    """Accept the bounded byte-range form supported by the artifact stream."""

    if value is None:
        return None
    try:
        unit, bounds = value.strip().split("=", 1)
        start_text, end_text = bounds.split("-", 1)
        if unit.lower() != "bytes" or "," in bounds or not start_text.isdecimal():
            raise ValueError
        end = int(end_text) if end_text else None
        return ArtifactByteRange(start=int(start_text), end_inclusive=end)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE,
            detail={"reasonCode": "artifact_range_not_satisfiable"},
        ) from error


@API_V2_PUBLIC_ROUTER.post(
    "/presentations/generations",
    response_model=OperationReceiptV2,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_presentation_generation(
    request: PresentationGenerationRequestV2,
    idempotency_key: IdempotencyKeyHeader,
    actor: ActorDependency,
    orchestrator: OrchestratorDependency,
) -> OperationReceiptV2:
    return await _delegate(
        orchestrator.submit_generation(
            actor=actor,
            idempotency_key=_idempotency_key(idempotency_key),
            request=request,
        )
    )


@API_V2_PUBLIC_ROUTER.post(
    "/presentations/{presentation_id}/exports",
    response_model=OperationReceiptV2,
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_presentation_export(
    presentation_id: PublicIdPath,
    request: PresentationExportRequestV2,
    idempotency_key: IdempotencyKeyHeader,
    actor: ActorDependency,
    orchestrator: OrchestratorDependency,
) -> OperationReceiptV2:
    return await _delegate(
        orchestrator.submit_export(
            actor=actor,
            presentation_id=presentation_id,
            idempotency_key=_idempotency_key(idempotency_key),
            request=request,
        )
    )


@API_V2_PUBLIC_ROUTER.get(
    "/operations/{operation_id}",
    response_model=OperationStatusV2,
)
async def get_operation_status(
    operation_id: PublicIdPath,
    actor: ActorDependency,
    orchestrator: OrchestratorDependency,
) -> OperationStatusV2:
    return await _delegate(
        orchestrator.get_operation(actor=actor, operation_id=operation_id)
    )


@API_V2_PUBLIC_ROUTER.get(
    "/operations/{operation_id}/events",
    response_model=OperationEventPageV2,
)
async def list_operation_events(
    operation_id: PublicIdPath,
    actor: ActorDependency,
    orchestrator: OrchestratorDependency,
    after: str | None = Query(default=None, pattern=PUBLIC_ID_PATTERN),
    limit: int = Query(default=100, ge=1, le=200),
) -> OperationEventPageV2:
    return await _delegate(
        orchestrator.list_operation_events(
            actor=actor,
            operation_id=operation_id,
            after=after,
            limit=limit,
        )
    )


@API_V2_PUBLIC_ROUTER.post(
    "/operations/{operation_id}/cancel",
    response_model=CancelOperationReceiptV2,
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_operation(
    operation_id: PublicIdPath,
    actor: ActorDependency,
    orchestrator: OrchestratorDependency,
) -> CancelOperationReceiptV2:
    return await _delegate(
        orchestrator.cancel_operation(actor=actor, operation_id=operation_id)
    )


@API_V2_PUBLIC_ROUTER.get(
    "/artifacts/{artifact_id}/download",
    response_class=RedirectResponse,
    status_code=status.HTTP_303_SEE_OTHER,
    responses={
        status.HTTP_303_SEE_OTHER: {
            "description": "Authorized short-lived artifact download redirect"
        }
    },
)
async def download_artifact(
    artifact_id: PublicIdPath,
    actor: ActorDependency,
    orchestrator: OrchestratorDependency,
) -> RedirectResponse:
    grant = await _delegate(
        orchestrator.authorize_download(actor=actor, artifact_id=artifact_id)
    )
    headers = {"Cache-Control": "no-store"}
    if grant.etag is not None:
        headers["ETag"] = grant.etag
    return RedirectResponse(
        url=grant.url,
        status_code=status.HTTP_303_SEE_OTHER,
        headers=headers,
    )


@API_V2_PUBLIC_ROUTER.get(
    "/artifacts/{artifact_id}/local-download",
    include_in_schema=False,
)
async def download_local_development_artifact(
    artifact_id: PublicIdPath,
    token: str = Query(min_length=1, max_length=8_192),
    range_header: str | None = Header(default=None, alias="Range"),
) -> StreamingResponse:
    """Redeem one short-lived local-only signed grant without buffering bytes."""

    try:
        stream = await configured_local_development_artifact_store().open_signed_download(
            artifact_id=uuid.UUID(artifact_id),
            token=token,
            byte_range=_single_byte_range(range_header),
        )
    except ArtifactBindingError as error:
        status_code = (
            status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE
            if str(error) == "artifact.range_not_satisfiable"
            else status.HTTP_404_NOT_FOUND
        )
        raise HTTPException(
            status_code=status_code,
            detail={"reasonCode": "artifact_not_found"},
        ) from error
    except (ArtifactNotFound, ArtifactStoreError, RuntimeError) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"reasonCode": "artifact_not_found"},
        ) from error
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
        "Content-Length": str(stream.content_length),
    }
    if stream.content_range is not None:
        headers["Content-Range"] = stream.content_range
    if stream.etag is not None:
        headers["ETag"] = stream.etag
    return StreamingResponse(
        stream.iter_chunks(),
        status_code=(status.HTTP_206_PARTIAL_CONTENT if stream.content_range else status.HTTP_200_OK),
        media_type=stream.media_type,
        headers=headers,
    )
