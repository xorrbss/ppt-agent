from typing import Annotated, Optional
from fastapi import APIRouter, Body, HTTPException

from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.database import get_sql_session
from utils.llm_calls.edit_slide import get_edited_slide_content
from utils.llm_calls.edit_slide_html import get_edited_slide_html
from utils.llm_calls.select_slide_type_on_edit import get_slide_layout_from_prompt
from utils.process_slides import process_old_and_new_slides_and_fetch_assets
from utils.randomizers import get_random_uuid


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

    # Always assign a new unique id to the slide
    slide.id = get_random_uuid()

    with get_sql_session() as sql_session:
        sql_session.add(slide)
        slide.content = edited_slide_content
        slide.layout = slide_layout.id
        sql_session.add_all(new_assets)
        sql_session.commit()
        sql_session.refresh(slide)

    return slide


@SLIDE_ROUTER.post("/edit-html", response_model=SlideModel)
async def edit_slide_html(
    id: Annotated[str, Body()],
    prompt: Annotated[str, Body()],
    html: Annotated[Optional[str], Body()] = None,
):
    with get_sql_session() as sql_session:
        slide = sql_session.get(SlideModel, id)
        if not slide:
            raise HTTPException(status_code=404, detail="Slide not found")

    html_to_edit = html or slide.html_content
    if not html_to_edit:
        raise HTTPException(status_code=400, detail="No HTML to edit")

    edited_slide_html = await get_edited_slide_html(prompt, html_to_edit)

    # Always assign a new unique id to the slide
    # This is to ensure that the nextjs can track slide updates
    slide.id = get_random_uuid()

    with get_sql_session() as sql_session:
        sql_session.add(slide)
        slide.html_content = edited_slide_html
        sql_session.commit()
        sql_session.refresh(slide)

    return slide
