"""Authored-only operations retained after the V1 editor surface removal."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from models.presentation_from_template import (
    AuthoredPageRemakePreviewRequest,
    AuthoredPageRemakePublishRequest,
    RetemplatePresentationRequest,
)
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from services.authored_canonical import (
    AuthoredCanonicalConflict,
    AuthoredCanonicalError,
    AuthoredCanonicalService,
)
from services.authored_page_remake_history import list_authored_page_remake_previews
from services.authored_page_remake_service import (
    AuthoredPageRemakeError,
    publish_authored_page_remake_legacy,
    queue_authored_page_remake_preview,
    remake_http_error,
)
from services.authored_quality_ocr import get_ocr_readiness
from models.svg_quality_review import SvgQualityReviewRequest
from services.svg_quality_review_service import queue_svg_quality_review
from services.database import get_async_session
from services.presentation_access_service import require_supported_live_presentation
from services.workspace_legacy_write_gate import require_legacy_presentation_write_path
from utils.authored_patch_engine import (
    CropAction,
    MoveAction,
    ReplaceImageAction,
    ResizeAction,
    SetStyleAction,
    SetTextAction,
)


AUTHORED_OPERATIONS_ROUTER = APIRouter(prefix="/presentation", tags=["Presentation"])
_AUTHORED_CANONICAL_SERVICE = AuthoredCanonicalService()


@AUTHORED_OPERATIONS_ROUTER.post("/{id}/svg-quality-review", response_model=AsyncPresentationGenerationTaskModel)
async def svg_quality_review(
    id: uuid.UUID, request: SvgQualityReviewRequest,
    request_http: Request,
    sql_session: AsyncSession = Depends(get_async_session),
):
    from services.workspace_operation_principal import local_development_export_bridge
    return await queue_svg_quality_review(id, request, sql_session,
        allow_local_development=local_development_export_bridge(request_http))


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class _CanonicalWireModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        alias_generator=_to_camel,
        populate_by_name=True,
    )


CanonicalPatchAction = Annotated[
    SetTextAction
    | SetStyleAction
    | MoveAction
    | ResizeAction
    | CropAction
    | ReplaceImageAction,
    Field(discriminator="action"),
]


class AuthoredCanonicalSlidePatch(_CanonicalWireModel):
    slide_id: uuid.UUID
    actions: list[CanonicalPatchAction] = Field(min_length=1, max_length=16)


class AuthoredCanonicalPatchRequest(_CanonicalWireModel):
    expected_revision: int = Field(ge=0)
    expected_digest: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )
    rebase_on_conflict: bool = False
    patches: list[AuthoredCanonicalSlidePatch] = Field(min_length=1, max_length=50)


class AuthoredCanonicalCasRequest(_CanonicalWireModel):
    expected_revision: int = Field(ge=0)
    expected_digest: str | None = Field(
        default=None, pattern=r"^sha256:[0-9a-f]{64}$"
    )


def _canonical_error_response(error: AuthoredCanonicalError):
    if isinstance(error, AuthoredCanonicalConflict):
        return JSONResponse(status_code=409, content=error.payload())
    raise HTTPException(
        status_code=422,
        detail={"code": error.code, "message": error.message},
    ) from error


@AUTHORED_OPERATIONS_ROUTER.get("/{id}/canonical")
async def get_authored_canonical_document(
    id: uuid.UUID,
    sql_session: AsyncSession = Depends(get_async_session),
):
    presentation = await require_supported_live_presentation(sql_session, id)
    try:
        return await _AUTHORED_CANONICAL_SERVICE.get_or_initialize(
            sql_session, presentation
        )
    except AuthoredCanonicalError as error:
        return _canonical_error_response(error)


@AUTHORED_OPERATIONS_ROUTER.post("/{id}/canonical/patches")
async def patch_authored_canonical_document(
    id: uuid.UUID,
    request: AuthoredCanonicalPatchRequest,
    sql_session: AsyncSession = Depends(get_async_session),
):
    presentation = await require_supported_live_presentation(sql_session, id)
    try:
        return await _AUTHORED_CANONICAL_SERVICE.apply_patches(
            sql_session,
            presentation,
            expected_revision=request.expected_revision,
            expected_digest=request.expected_digest,
            rebase_on_conflict=request.rebase_on_conflict,
            patches=[
                patch.model_dump(mode="json", by_alias=True)
                for patch in request.patches
            ],
        )
    except AuthoredCanonicalError as error:
        return _canonical_error_response(error)


@AUTHORED_OPERATIONS_ROUTER.get("/{id}/canonical/history")
async def list_authored_canonical_history(
    id: uuid.UUID,
    sql_session: AsyncSession = Depends(get_async_session),
):
    presentation = await require_supported_live_presentation(sql_session, id)
    try:
        return await _AUTHORED_CANONICAL_SERVICE.history(sql_session, presentation)
    except AuthoredCanonicalError as error:
        return _canonical_error_response(error)


@AUTHORED_OPERATIONS_ROUTER.post("/{id}/canonical/undo")
async def undo_authored_canonical_document(
    id: uuid.UUID,
    request: AuthoredCanonicalCasRequest,
    sql_session: AsyncSession = Depends(get_async_session),
):
    presentation = await require_supported_live_presentation(sql_session, id)
    try:
        return await _AUTHORED_CANONICAL_SERVICE.undo(
            sql_session,
            presentation,
            expected_revision=request.expected_revision,
            expected_digest=request.expected_digest,
        )
    except AuthoredCanonicalError as error:
        return _canonical_error_response(error)


@AUTHORED_OPERATIONS_ROUTER.post("/{id}/canonical/redo")
async def redo_authored_canonical_document(
    id: uuid.UUID,
    request: AuthoredCanonicalCasRequest,
    sql_session: AsyncSession = Depends(get_async_session),
):
    presentation = await require_supported_live_presentation(sql_session, id)
    try:
        return await _AUTHORED_CANONICAL_SERVICE.redo(
            sql_session,
            presentation,
            expected_revision=request.expected_revision,
            expected_digest=request.expected_digest,
        )
    except AuthoredCanonicalError as error:
        return _canonical_error_response(error)


@AUTHORED_OPERATIONS_ROUTER.get("/quality-review/readiness")
async def authored_quality_review_readiness():
    return await get_ocr_readiness()


@AUTHORED_OPERATIONS_ROUTER.post("/{id}/slides/{slide_index}/remake/preview", response_model=AsyncPresentationGenerationTaskModel)
async def preview_authored_page_remake(id: uuid.UUID, slide_index: int, request: AuthoredPageRemakePreviewRequest, background_tasks: BackgroundTasks, request_http: Request, sql_session: AsyncSession = Depends(get_async_session)):
    await require_supported_live_presentation(sql_session, id)
    from services.workspace_operation_principal import local_development_export_bridge
    try:
        return await queue_authored_page_remake_preview(id, slide_index, request, background_tasks, sql_session,
            allow_local_development=local_development_export_bridge(request_http))
    except AuthoredPageRemakeError as error:
        raise remake_http_error(error) from error


@AUTHORED_OPERATIONS_ROUTER.get("/{id}/remake/previews")
async def list_authored_page_remake_preview_history(id: uuid.UUID, sql_session: AsyncSession = Depends(get_async_session)):
    await require_supported_live_presentation(sql_session, id)
    return await list_authored_page_remake_previews(sql_session, id)


@AUTHORED_OPERATIONS_ROUTER.post("/{id}/slides/{slide_index}/remake/publish")
async def publish_authored_page_remake_candidate(id: uuid.UUID, slide_index: int, request: AuthoredPageRemakePublishRequest, sql_session: AsyncSession = Depends(get_async_session)):
    require_legacy_presentation_write_path(id)
    await require_supported_live_presentation(sql_session, id)
    try:
        return await publish_authored_page_remake_legacy(session=sql_session, presentation_id=id, slide_index=slide_index, preview_task_id=request.preview_task_id)
    except AuthoredPageRemakeError as error:
        raise remake_http_error(error) from error


@AUTHORED_OPERATIONS_ROUTER.post("/{id}/retemplate", response_model=AsyncPresentationGenerationTaskModel)
async def retemplate_authored_presentation(id: uuid.UUID, request: RetemplatePresentationRequest, request_http: Request, background_tasks: BackgroundTasks, sql_session: AsyncSession = Depends(get_async_session)):
    from services.generation_core.admission.strategy import AUTHORED_STRATEGY_REMOVED

    # Retemplating regenerates the deck with the authored strategy, removed on
    # 2026-11-09: refuse before the exact-source snapshot or any durable row.
    require_legacy_presentation_write_path(id)
    raise HTTPException(status_code=422, detail=AUTHORED_STRATEGY_REMOVED)
