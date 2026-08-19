"""Detached multi-page re-author previews for an explicit page selection."""

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from models.authored_batch_remake import AuthoredBatchRemakePreviewRequest
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from services.authored_batch_remake_service import (
    queue_authored_batch_remake_preview,
)
from services.database import get_async_session
from services.presentation_access_service import require_live_presentation

AUTHORED_BATCH_REMAKE_ROUTER = APIRouter(prefix="/presentation", tags=["Presentation"])


@AUTHORED_BATCH_REMAKE_ROUTER.post(
    "/{id}/slides/remake/preview-batch",
    response_model=AsyncPresentationGenerationTaskModel,
    summary="Create detached AI-authored replacements for several pages",
)
async def preview_authored_batch_remake(
    id: uuid.UUID,
    request: AuthoredBatchRemakePreviewRequest,
    background_tasks: BackgroundTasks,
    sql_session: Annotated[AsyncSession, Depends(get_async_session)],
):
    """Fan out one candidate per selected page without mutating the deck.

    Preview is non-mutating, so the legacy write-path guard deliberately does
    not apply here; publication is a separate, approved workspace command.
    """

    await require_live_presentation(sql_session, id)
    return await queue_authored_batch_remake_preview(
        id, request, background_tasks, sql_session
    )
