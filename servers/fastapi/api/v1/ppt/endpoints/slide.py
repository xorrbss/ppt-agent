from typing import Annotated
from fastapi import APIRouter, Body, HTTPException

from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.database import get_sql_session
from utils.llm_calls.edit_slide import get_edited_slide_content
from utils.llm_calls.select_slide_type_on_edit import get_slide_layout_from_prompt
from utils.process_slides import process_old_and_new_slides_and_fetch_assets


SLIDE_ROUTER = APIRouter(prefix="/slide", tags=["Slide"])


@SLIDE_ROUTER.post("/edit")
async def edit_slide(id: Annotated[str, Body()], prompt: Annotated[str, Body()]):

    with get_sql_session() as sql_session:
        slide = sql_session.get(SlideModel, id)
        if not slide:
            raise HTTPException(status_code=404, detail="Slide not found")
        presentation = sql_session.get(PresentationModel, slide.presentation)
        if not presentation:
            raise HTTPException(status_code=404, detail="Presentation not found")

    presentation_layout = presentation.get_layout()

    slide_layout = await get_slide_layout_from_prompt(
        prompt, presentation_layout, slide
    )
    edited_slide_content = await get_edited_slide_content(
        prompt, slide_layout, slide, presentation.language
    )

    # This will mutate edited_slide_content
    new_assets = await process_old_and_new_slides_and_fetch_assets(
        slide.content, edited_slide_content
    )

    with get_sql_session() as sql_session:
        sql_session.add(slide)
        slide.content = edited_slide_content
        slide.layout = slide_layout.id
        sql_session.add_all(new_assets)
        sql_session.commit()
        sql_session.refresh(slide)

    return slide
