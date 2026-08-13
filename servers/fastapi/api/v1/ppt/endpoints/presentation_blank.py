from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from models.presentation_with_slides import PresentationWithSlides
from models.sql.presentation import PresentationModel, PresentationVersion
from models.sql.slide import SlideModel
from services.database import get_async_session
from services.compatibility_migration.admission import (
    require_legacy_creation_closed,
)

PRESENTATION_BLANK_ROUTER = APIRouter(prefix="/presentation", tags=["Presentation"])
BLANK_PRESENTATION_LAYOUT_GROUP = "blank"
BLANK_PRESENTATION_LAYOUT_ID = "__blank_slide__"
@PRESENTATION_BLANK_ROUTER.post(
    "/create/blank",
    response_model=PresentationWithSlides,
    status_code=201,
)
async def create_blank_presentation(
    sql_session: Annotated[AsyncSession, Depends(get_async_session)],
):
    require_legacy_creation_closed(operation="blank")
    presentation = PresentationModel(
        version=PresentationVersion.V2_STANDARD,
        content="",
        n_slides=1,
        language="English",
        title="Untitled Presentation",
        layout=None,
        fonts=None,
        include_title_slide=False,
    )
    slide = SlideModel(
        presentation=presentation.id,
        layout_group=BLANK_PRESENTATION_LAYOUT_GROUP,
        layout=BLANK_PRESENTATION_LAYOUT_ID,
        index=0,
        content={},
        speaker_note="",
        # Template-free blank slides intentionally have no native Template V2 UI.
        # The editor recognizes the reserved layout id and creates the white canvas.
        ui=None,
    )
    sql_session.add(presentation)
    sql_session.add(slide)
    try:
        await sql_session.commit()
    except Exception:
        await sql_session.rollback()
        raise
    await sql_session.refresh(presentation)
    await sql_session.refresh(slide)
    return PresentationWithSlides(**presentation.model_dump(), slides=[slide])
