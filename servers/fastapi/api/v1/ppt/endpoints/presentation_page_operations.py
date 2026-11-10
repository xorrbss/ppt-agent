"""Page-level presentation operations: svg quality review and page remake.

These routes lived in ``authored_operations.py`` beside the authored canonical
editor and the retemplate route. Those were removed with the authored strategy
(2026-11-10); the paths, payloads and route names below are unchanged.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from models.presentation_from_template import (
    AuthoredPageRemakePreviewRequest,
    AuthoredPageRemakePublishRequest,
)
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from services.authored_page_remake_history import list_authored_page_remake_previews
from services.authored_page_remake_service import publish_authored_page_remake_legacy
from services.page_remake_core import (
    AuthoredPageRemakeError,
    queue_authored_page_remake_preview,
    remake_http_error,
)
from services.authored_quality_ocr import get_ocr_readiness
from models.svg_quality_review import SvgQualityReviewRequest
from services.svg_quality_review_service import queue_svg_quality_review
from services.database import get_async_session
from services.presentation_access_service import require_supported_live_presentation
from services.workspace_legacy_write_gate import require_legacy_presentation_write_path


PRESENTATION_PAGE_OPERATIONS_ROUTER = APIRouter(prefix="/presentation", tags=["Presentation"])


@PRESENTATION_PAGE_OPERATIONS_ROUTER.post("/{id}/svg-quality-review", response_model=AsyncPresentationGenerationTaskModel)
async def svg_quality_review(
    id: uuid.UUID, request: SvgQualityReviewRequest,
    request_http: Request,
    sql_session: AsyncSession = Depends(get_async_session),
):
    from services.workspace_operation_principal import local_development_export_bridge
    return await queue_svg_quality_review(id, request, sql_session,
        allow_local_development=local_development_export_bridge(request_http))


@PRESENTATION_PAGE_OPERATIONS_ROUTER.get("/quality-review/readiness")
async def authored_quality_review_readiness():
    return await get_ocr_readiness()


@PRESENTATION_PAGE_OPERATIONS_ROUTER.post("/{id}/slides/{slide_index}/remake/preview", response_model=AsyncPresentationGenerationTaskModel)
async def preview_authored_page_remake(id: uuid.UUID, slide_index: int, request: AuthoredPageRemakePreviewRequest, background_tasks: BackgroundTasks, request_http: Request, sql_session: AsyncSession = Depends(get_async_session)):
    await require_supported_live_presentation(sql_session, id)
    from services.workspace_operation_principal import local_development_export_bridge
    try:
        return await queue_authored_page_remake_preview(id, slide_index, request, background_tasks, sql_session,
            allow_local_development=local_development_export_bridge(request_http))
    except AuthoredPageRemakeError as error:
        raise remake_http_error(error) from error


@PRESENTATION_PAGE_OPERATIONS_ROUTER.get("/{id}/remake/previews")
async def list_authored_page_remake_preview_history(id: uuid.UUID, sql_session: AsyncSession = Depends(get_async_session)):
    await require_supported_live_presentation(sql_session, id)
    return await list_authored_page_remake_previews(sql_session, id)


@PRESENTATION_PAGE_OPERATIONS_ROUTER.post("/{id}/slides/{slide_index}/remake/publish")
async def publish_authored_page_remake_candidate(id: uuid.UUID, slide_index: int, request: AuthoredPageRemakePublishRequest, sql_session: AsyncSession = Depends(get_async_session)):
    require_legacy_presentation_write_path(id)
    await require_supported_live_presentation(sql_session, id)
    try:
        return await publish_authored_page_remake_legacy(session=sql_session, presentation_id=id, slide_index=slide_index, preview_task_id=request.preview_task_id)
    except AuthoredPageRemakeError as error:
        raise remake_http_error(error) from error
