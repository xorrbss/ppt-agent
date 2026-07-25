import secrets
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from api.v1.ppt.endpoints.presentation_helpers import resolve_presentation_fonts
from models.presentation_with_slides import PresentationWithSlides
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.database import get_async_session

PRESENTATION_SHARE_ROUTER = APIRouter(
    prefix="/presentation", tags=["Presentation Sharing"]
)

# Minimum plausible length for a real share token (secrets.token_urlsafe(32) ~ 43
# chars); short/blank values are rejected up front rather than hitting the DB.
_MIN_TOKEN_LEN = 16


class ShareInfo(BaseModel):
    shared: bool
    share_token: Optional[str] = None


@PRESENTATION_SHARE_ROUTER.get("/{id}/share", response_model=ShareInfo)
async def get_share_status(
    id: uuid.UUID, sql_session: AsyncSession = Depends(get_async_session)
):
    presentation = await sql_session.get(PresentationModel, id)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")
    return ShareInfo(
        shared=bool(presentation.share_token), share_token=presentation.share_token
    )


@PRESENTATION_SHARE_ROUTER.post("/{id}/share", response_model=ShareInfo)
async def enable_share(
    id: uuid.UUID,
    regenerate: bool = False,
    sql_session: AsyncSession = Depends(get_async_session),
):
    """Enable read-only sharing (idempotent). `regenerate=true` rotates the token,
    which immediately voids the previous link."""
    presentation = await sql_session.get(PresentationModel, id)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")

    if regenerate or not presentation.share_token:
        presentation.share_token = secrets.token_urlsafe(32)
        sql_session.add(presentation)
        await sql_session.commit()
    return ShareInfo(shared=True, share_token=presentation.share_token)


@PRESENTATION_SHARE_ROUTER.delete("/{id}/share", response_model=ShareInfo)
async def disable_share(
    id: uuid.UUID, sql_session: AsyncSession = Depends(get_async_session)
):
    presentation = await sql_session.get(PresentationModel, id)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")

    if presentation.share_token is not None:
        presentation.share_token = None
        sql_session.add(presentation)
        await sql_session.commit()
    return ShareInfo(shared=False, share_token=None)


@PRESENTATION_SHARE_ROUTER.get(
    "/public/{share_token}", response_model=PresentationWithSlides
)
async def get_shared_presentation(
    share_token: str, sql_session: AsyncSession = Depends(get_async_session)
):
    """Public, read-only view of a shared deck. Reached ONLY by an unguessable
    token (never by presentation id), and exempt from the admin session in
    SessionAuthMiddleware. Returns 404 for any token that is blank, too short, or
    not currently shared — so nothing about other decks is observable."""
    if not share_token or len(share_token) < _MIN_TOKEN_LEN:
        raise HTTPException(status_code=404, detail="Shared presentation not found")

    result = await sql_session.scalars(
        select(PresentationModel).where(
            PresentationModel.share_token == share_token
        )
    )
    presentation = result.first()
    if presentation is None or not presentation.share_token:
        raise HTTPException(status_code=404, detail="Shared presentation not found")

    slides = list(
        await sql_session.scalars(
            select(SlideModel)
            .where(SlideModel.presentation == presentation.id)
            .order_by(SlideModel.index)
        )
    )
    fonts = await resolve_presentation_fonts(presentation, slides, sql_session)
    return PresentationWithSlides(
        **presentation.model_dump(), slides=slides, fonts=fonts
    )
