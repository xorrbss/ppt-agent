"""HTTP boundary for durable authored vNext PPTX export jobs."""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, HTTPException, Path as ApiPath, status
from fastapi.responses import FileResponse

from services.authored_vnext.integration.artifact_store import BufferedArtifactStore
from services.authored_vnext.integration.export_service import (
    AuthoredVNextExportService,
    CreateJobCommand,
    ExportServiceError,
)
from services.authored_vnext.integration.sql_repository import (
    AuthoredVNextSqlRepository,
    ExportJobConflictError,
    OperationFenceError,
    PublicationConflictError,
    SourceDigestMismatchError,
)
from services.authored_vnext.integration.worker import HttpOoxmlWorkerTransport
from services.authored_vnext.master.assets import OwnerMasterAssetStore
from utils.owner_context import get_current_owner_id

from .models import (
    CancelResponse,
    CreateExportJobRequest,
    ExportArtifactReceipt,
    ExportJobResponse,
    OperationFenceRequest,
    PublicationCasRequest,
)


def _enabled() -> bool:
    return os.getenv("AUTHORED_VNEXT_EXPORT_ENABLED", "false").lower() == "true"


def _require_enabled() -> None:
    if not _enabled():
        raise HTTPException(status_code=404, detail="authored_vnext_export_disabled")


@lru_cache(maxsize=32)
def _service_for_scope(owner_scope: str, owner_id: str | None) -> AuthoredVNextExportService:
    repository = AuthoredVNextSqlRepository.from_application_database(
        owner_scope=owner_scope,
        tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        owner_id=uuid.UUID(owner_id) if owner_id else None,
    )
    store = BufferedArtifactStore(
        Path(os.getenv("APP_DATA_DIRECTORY", "app_data"))
        / "exports"
        / "users"
        / owner_scope
        / "authored-vnext"
    )
    asset_store = OwnerMasterAssetStore(
        Path(os.getenv("APP_DATA_DIRECTORY", "app_data"))
        / "authored_vnext"
        / "master-assets"
        / "users"
        / owner_scope
    )
    return AuthoredVNextExportService(
        repository,
        HttpOoxmlWorkerTransport.from_environment(),
        store,
        asset_store,
    )


def get_authored_vnext_export_service() -> AuthoredVNextExportService:
    owner_id = get_current_owner_id()
    if owner_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authored_vnext_owner_required",
        )
    scope = str(owner_id)
    return _service_for_scope(scope, scope)


Service = Annotated[
    AuthoredVNextExportService, Depends(get_authored_vnext_export_service)
]


AUTHORED_VNEXT_EXPORT_ROUTER = APIRouter(
    prefix="/authored-vnext/export",
    tags=["Authored vNext Export"],
    dependencies=[Depends(_require_enabled)],
)


def _artifact(service: AuthoredVNextExportService, deck_id: str):
    active = service.repository.get_active_artifact(deck_id)
    return active


def _job_response(service: AuthoredVNextExportService, job) -> ExportJobResponse:
    active = _artifact(service, job.deck_id) if job.state == "succeeded" else None
    artifact = None
    if active is not None and active[1].job_id == job.id:
        pointer, receipt = active
        artifact = ExportArtifactReceipt(
            artifact_id=str(receipt.id),
            publication_revision=pointer.revision,
            digest=receipt.artifact_digest,
            byte_length=receipt.size_bytes,
            package_digest=receipt.package_digest,
            master_digest=receipt.master_digest,
            deck_digest=job.deck_digest,
            read_back_verified=True,
        )
    state = "running" if job.state == "cancel_requested" else job.state
    return ExportJobResponse(
        job_id=str(job.id),
        deck_id=job.deck_id,
        state=state,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        operation_fence=job.operation_fence,
        cancel_requested=job.cancel_requested,
        resumable=job.state in {"failed", "cancelled"}
        and job.attempt_count < job.max_attempts,
        error_code=job.failure_code,
        artifact=artifact,
    )


def _http_error(error: Exception) -> HTTPException:
    code = getattr(error, "code", str(error))
    if isinstance(error, KeyError):
        return HTTPException(status_code=404, detail={"code": code.strip("'")})
    if code == "authored_vnext_deck_not_found":
        return HTTPException(status_code=404, detail={"code": code})
    if isinstance(
        error,
        (
            ExportJobConflictError,
            OperationFenceError,
            PublicationConflictError,
            SourceDigestMismatchError,
        ),
    ) or (isinstance(error, ExportServiceError) and "mismatch" in error.code):
        return HTTPException(status_code=409, detail={"code": code})
    return HTTPException(status_code=422, detail={"code": code})


@AUTHORED_VNEXT_EXPORT_ROUTER.post(
    "/jobs", response_model=ExportJobResponse, status_code=status.HTTP_202_ACCEPTED
)
def create_export_job(
    request: CreateExportJobRequest, service: Service
) -> ExportJobResponse:
    try:
        payload = request.model_dump(mode="json", by_alias=True)
        job = service.create_job(
            CreateJobCommand(
                deck_id=request.deck_id,
                expected_deck_revision=request.expected_deck_revision,
                expected_deck_digest=request.expected_deck_digest,
                expected_master_revision=request.expected_master_revision,
                expected_master_digest=request.expected_master_digest,
                expected_active_artifact_revision=request.expected_active_artifact_revision,
                expected_active_artifact_digest=request.expected_active_artifact_digest,
                expected_source_package_digest=request.expected_source_package_digest,
                expected_asset_digests=request.expected_asset_digests,
                expected_font_digest=request.expected_font_digest,
                idempotency_key=request.idempotency_key,
                timeout_ms=request.timeout_ms,
                max_attempts=request.max_attempts,
                operation_fence=request.operation_fence,
                request_payload=payload,
            )
        )
        return _job_response(service, job)
    except Exception as error:
        raise _http_error(error) from error


@AUTHORED_VNEXT_EXPORT_ROUTER.get(
    "/jobs/{job_id}", response_model=ExportJobResponse
)
def get_export_job(job_id: uuid.UUID, service: Service) -> ExportJobResponse:
    try:
        return _job_response(service, service.repository.get_export_job(job_id))
    except Exception as error:
        raise _http_error(error) from error


@AUTHORED_VNEXT_EXPORT_ROUTER.post(
    "/jobs/{job_id}/run", response_model=ExportJobResponse
)
async def run_export_job(job_id: uuid.UUID, service: Service) -> ExportJobResponse:
    try:
        return _job_response(service, await service.run_job(job_id))
    except Exception as error:
        raise _http_error(error) from error


@AUTHORED_VNEXT_EXPORT_ROUTER.post(
    "/jobs/{job_id}/cancel", response_model=CancelResponse
)
def cancel_export_job(
    request: OperationFenceRequest,
    job_id: uuid.UUID,
    service: Service,
) -> CancelResponse:
    try:
        job = service.repository.request_cancel(
            job_id, expected_operation_fence=request.operation_fence
        )
        return CancelResponse(
            job_id=str(job.id), state=job.state, cancel_requested=job.cancel_requested
        )
    except Exception as error:
        raise _http_error(error) from error


@AUTHORED_VNEXT_EXPORT_ROUTER.post(
    "/jobs/{job_id}/retry",
    response_model=ExportJobResponse,
    name="retry_authored_vnext_export_job",
)
@AUTHORED_VNEXT_EXPORT_ROUTER.post(
    "/jobs/{job_id}/resume",
    response_model=ExportJobResponse,
    name="resume_authored_vnext_export_job",
)
def retry_export_job(
    request: OperationFenceRequest,
    job_id: uuid.UUID,
    service: Service,
) -> ExportJobResponse:
    try:
        return _job_response(
            service,
            service.repository.retry_export_job(
                job_id, expected_operation_fence=request.operation_fence
            ),
        )
    except Exception as error:
        raise _http_error(error) from error


@AUTHORED_VNEXT_EXPORT_ROUTER.get(
    "/decks/{deck_id}/artifact", response_class=FileResponse
)
def download_active_artifact(
    service: Service,
    deck_id: str = ApiPath(pattern=r"^deck:[a-z0-9][a-z0-9._-]*$"),
) -> FileResponse:
    try:
        active = service.repository.get_active_artifact(deck_id)
        if active is None:
            raise KeyError("authored_vnext_artifact_not_found")
        _, receipt = active
        path = service.artifact_store.open_verified(
            receipt.storage_key, receipt.artifact_digest, receipt.size_bytes
        )
        return FileResponse(
            path,
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            filename=f"{deck_id.removeprefix('deck:')}.pptx",
            headers={"ETag": f'"{receipt.artifact_digest}"'},
        )
    except Exception as error:
        raise _http_error(error) from error


@AUTHORED_VNEXT_EXPORT_ROUTER.post(
    "/decks/{deck_id}/artifacts/{artifact_id}/rollback",
    response_model=ExportArtifactReceipt,
)
def rollback_artifact(
    request: PublicationCasRequest,
    service: Service,
    deck_id: str,
    artifact_id: uuid.UUID,
) -> ExportArtifactReceipt:
    try:
        active = service.repository.get_active_artifact(deck_id)
        if active is None:
            raise KeyError("authored_vnext_artifact_not_found")
        pointer, _ = active
        if pointer.revision != request.expected_publication_revision:
            raise PublicationConflictError()
        target_receipt = service.repository.get_artifact_receipt(
            deck_id=deck_id,
            artifact_id=artifact_id,
        )
        # A durable receipt does not prove its external bytes still exist.
        # Preserve the downloadable LKG unless the rollback target reopens and
        # verifies before the pointer CAS.
        service.artifact_store.open_verified(
            target_receipt.storage_key,
            target_receipt.artifact_digest,
            target_receipt.size_bytes,
        )
        updated = service.repository.rollback_publication(
            deck_id=deck_id,
            target_artifact_id=artifact_id,
            expected_generation=pointer.generation,
            expected_digest=request.expected_artifact_digest,
        )
        _, receipt = service.repository.get_active_artifact(deck_id)
        return ExportArtifactReceipt(
            artifact_id=str(receipt.id),
            publication_revision=updated.revision,
            digest=receipt.artifact_digest,
            byte_length=receipt.size_bytes,
            package_digest=receipt.package_digest,
            master_digest=receipt.master_digest,
            deck_digest=service.repository.get_deck(
                deck_id, receipt.deck_revision
            ).digest,
            read_back_verified=True,
        )
    except Exception as error:
        raise _http_error(error) from error
