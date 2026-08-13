"""Feature-gated DeckMasterContract vertical slice endpoints."""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, HTTPException, Path as ApiPath, status

from models.authored_vnext.master.operations import (
    CreateDeckRequest,
    CreateDraftRequest,
    MasterCasRequest,
    MasterRevisionResponse,
    PreviewRequest,
    PreviewResponse,
    RegenerateBodyRequest,
    RegenerateResponse,
    StatusResponse,
    ValidationSummary,
)
from models.authored_vnext.master.slide import DeckSnapshot
from services.authored_vnext.master.errors import (
    DeckAlreadyExistsError,
    DeckDigestConflictError,
    DeckNotFoundError,
    DeckRevisionConflictError,
    MasterDigestConflictError,
    MasterNotFoundError,
    MasterNotDraftError,
    MasterNotPublishedError,
    MasterRevisionConflictError,
    MasterServiceError,
    MasterValidationError,
    SlideNotFoundError,
)
from services.authored_vnext.master.file_repository import (
    FileAuthoredVNextMasterRepository,
)
from services.authored_vnext.master.service import AuthoredVNextMasterService
from services.authored_vnext.integration.sql_repository import (
    AuthoredVNextSqlRepository,
)
from utils.owner_context import get_current_owner_id
from .asset_router import AUTHORED_VNEXT_MASTER_ASSET_ROUTER


def authored_vnext_master_enabled() -> bool:
    return os.getenv("AUTHORED_VNEXT_MASTER_ENABLED", "false").strip().lower() == "true"


def _require_enabled() -> None:
    if not authored_vnext_master_enabled():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="authored_vnext_master_disabled",
        )


@lru_cache(maxsize=32)
def _master_service_for_scope(
    owner_scope: str, owner_id: str | None
) -> AuthoredVNextMasterService:
    mode = os.getenv("AUTHORED_VNEXT_REPOSITORY_MODE", "sql").strip().lower()
    if mode == "file":
        environment = os.getenv("ENVIRONMENT", "production").strip().lower()
        if environment not in {"dev", "development", "local", "test"}:
            raise RuntimeError("authored_vnext_file_repository_forbidden_in_production")
        app_data = Path(os.getenv("APP_DATA_DIRECTORY", "app_data"))
        repository = FileAuthoredVNextMasterRepository(
            app_data / "authored_vnext" / "master"
        )
    elif mode == "sql":
        repository = AuthoredVNextSqlRepository.from_application_database(
            owner_scope=owner_scope,
            tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
            owner_id=(uuid.UUID(owner_id) if owner_id else None),
        )
    else:
        raise RuntimeError("authored_vnext_repository_mode_invalid")
    return AuthoredVNextMasterService(repository)


def get_authored_vnext_master_service() -> AuthoredVNextMasterService:
    owner_id = get_current_owner_id()
    if owner_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authored_vnext_owner_required",
        )
    scope = str(owner_id)
    return _master_service_for_scope(scope, scope)


ServiceDependency = Annotated[
    AuthoredVNextMasterService,
    Depends(get_authored_vnext_master_service),
]


AUTHORED_VNEXT_MASTER_ROUTER = APIRouter(
    prefix="/authored-vnext/master",
    tags=["Authored vNext Master"],
    dependencies=[Depends(_require_enabled)],
)
AUTHORED_VNEXT_MASTER_ROUTER.include_router(AUTHORED_VNEXT_MASTER_ASSET_ROUTER)


def _http_error(error: MasterServiceError) -> HTTPException:
    if isinstance(error, (MasterNotFoundError, DeckNotFoundError, SlideNotFoundError)):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(
        error,
        (
            MasterRevisionConflictError,
            MasterDigestConflictError,
            DeckRevisionConflictError,
            DeckDigestConflictError,
            DeckAlreadyExistsError,
            MasterNotPublishedError,
            MasterNotDraftError,
        ),
    ):
        status_code = status.HTTP_409_CONFLICT
    elif isinstance(error, MasterValidationError):
        status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    else:
        status_code = status.HTTP_409_CONFLICT
    return HTTPException(
        status_code=status_code,
        detail={"code": error.code},
    )


@AUTHORED_VNEXT_MASTER_ROUTER.get("/status", response_model=StatusResponse)
def master_status() -> StatusResponse:
    return StatusResponse(
        enabled=True,
        contract_version="deckmaster-contract/v1",
        slide_spec_version="slidespec/v2",
        compiler_port="OoxmlMasterCompilerPort",
    )


@AUTHORED_VNEXT_MASTER_ROUTER.post(
    "/drafts",
    response_model=MasterRevisionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_draft(
    request: CreateDraftRequest,
    service: ServiceDependency,
) -> MasterRevisionResponse:
    try:
        return service.create_draft(request)
    except MasterServiceError as error:
        raise _http_error(error) from error


@AUTHORED_VNEXT_MASTER_ROUTER.get(
    "/masters/{master_id}/revisions/{revision}",
    response_model=MasterRevisionResponse,
)
def get_master_revision(
    service: ServiceDependency,
    master_id: str = ApiPath(pattern=r"^master:[a-z0-9][a-z0-9._-]*$"),
    revision: int = ApiPath(ge=1),
) -> MasterRevisionResponse:
    try:
        return service.get_revision(master_id, revision)
    except MasterServiceError as error:
        raise _http_error(error) from error


@AUTHORED_VNEXT_MASTER_ROUTER.post(
    "/drafts/{master_id}/validate",
    response_model=ValidationSummary,
)
def validate_draft(
    request: MasterCasRequest,
    service: ServiceDependency,
    master_id: str = ApiPath(pattern=r"^master:[a-z0-9][a-z0-9._-]*$"),
) -> ValidationSummary:
    try:
        return service.validate_draft(master_id, request)
    except MasterServiceError as error:
        raise _http_error(error) from error


@AUTHORED_VNEXT_MASTER_ROUTER.post(
    "/drafts/{master_id}/publish",
    response_model=MasterRevisionResponse,
)
def publish_draft(
    request: MasterCasRequest,
    service: ServiceDependency,
    master_id: str = ApiPath(pattern=r"^master:[a-z0-9][a-z0-9._-]*$"),
) -> MasterRevisionResponse:
    try:
        return service.publish(master_id, request)
    except MasterServiceError as error:
        raise _http_error(error) from error


@AUTHORED_VNEXT_MASTER_ROUTER.post("/preview", response_model=PreviewResponse)
def preview(
    request: PreviewRequest,
    service: ServiceDependency,
) -> PreviewResponse:
    try:
        return service.preview(request)
    except (MasterServiceError, ValueError) as error:
        if isinstance(error, MasterServiceError):
            raise _http_error(error) from error
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "authored_vnext_master_preview_invalid"},
        ) from error


@AUTHORED_VNEXT_MASTER_ROUTER.post(
    "/decks",
    response_model=DeckSnapshot,
    status_code=status.HTTP_201_CREATED,
)
def create_deck(
    request: CreateDeckRequest,
    service: ServiceDependency,
) -> DeckSnapshot:
    try:
        return service.create_deck(request)
    except (MasterServiceError, ValueError) as error:
        if isinstance(error, MasterServiceError):
            raise _http_error(error) from error
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "authored_vnext_deck_invalid"},
        ) from error


@AUTHORED_VNEXT_MASTER_ROUTER.post(
    "/decks/{deck_id}/slides/{slide_id}/regenerate-body",
    response_model=RegenerateResponse,
)
def regenerate_body(
    request: RegenerateBodyRequest,
    service: ServiceDependency,
    deck_id: str = ApiPath(pattern=r"^deck:[a-z0-9][a-z0-9._-]*$"),
    slide_id: str = ApiPath(pattern=r"^slide:[a-z0-9][a-z0-9._-]*$"),
) -> RegenerateResponse:
    try:
        return service.regenerate_body(deck_id, slide_id, request)
    except MasterServiceError as error:
        raise _http_error(error) from error
