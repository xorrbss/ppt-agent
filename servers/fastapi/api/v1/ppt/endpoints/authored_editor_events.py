"""Record one authored-slide editor event for the measurement loop."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from models.authored_slide_editor_event import (
    AuthoredSlideEditorEventRequest,
    AuthoredSlideEditorEventResponse,
)
from services.authored_slide_editor_event_service import (
    record_authored_slide_editor_event,
)
from services.database import get_async_session
from services.presentation_access_service import require_live_presentation

AUTHORED_EDITOR_EVENTS_ROUTER = APIRouter(
    prefix="/presentation", tags=["Presentation"]
)


@AUTHORED_EDITOR_EVENTS_ROUTER.post(
    "/{id}/editor-events",
    response_model=AuthoredSlideEditorEventResponse,
    summary="Record one per-slide editor event",
)
async def record_editor_event(
    id: uuid.UUID,
    request: AuthoredSlideEditorEventRequest,
    sql_session: Annotated[AsyncSession, Depends(get_async_session)],
) -> AuthoredSlideEditorEventResponse:
    """Append an editor action to the measurement ledger.

    This is observability, not a deck mutation: it never edits a slide, so the
    legacy presentation write-path guard deliberately does not apply.  The deck
    is still resolved through the owner-scoped live lookup, so an event cannot
    be recorded against a deck the caller may not read.
    """

    await require_live_presentation(sql_session, id)
    return await record_authored_slide_editor_event(sql_session, id, request)
