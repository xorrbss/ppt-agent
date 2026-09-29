from typing import Annotated, Optional
from fastapi import APIRouter, Body, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession
import uuid

from constants.user_messages import UserMessageKey, user_message
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.database import get_async_session
from services.image_generation_service import ImageGenerationService
from services.mem0_presentation_memory_service import (
    MEM0_PRESENTATION_MEMORY_SERVICE,
)
from services.workspace_legacy_write_gate import (
    require_legacy_presentation_write_path,
)
from utils.asset_directory_utils import get_images_directory
from utils.llm_calls.edit_slide import get_edited_slide_content
from utils.llm_calls.edit_slide_html import get_edited_slide_html
from utils.llm_calls.select_slide_type_on_edit import get_slide_layout_from_prompt
from utils.process_slides import process_old_and_new_slides_and_fetch_assets


SLIDE_ROUTER = APIRouter(prefix="/slide", tags=["Slide"])


@SLIDE_ROUTER.get("/{id}/svg-preview")
async def get_svg_preview(id: uuid.UUID,
                          sql_session: AsyncSession = Depends(get_async_session)):
    from services.artifact_store import ArtifactStoreError
    from services.authored_canvas import presentation_canvas_contract
    from services.presentation_access_service import require_live_presentation
    from services.svg_asset_resolver import resolve_slide_svg_preview
    from utils.owner_context import get_current_owner_id, get_current_tenant_id
    owner_id, tenant_id = get_current_owner_id(), get_current_tenant_id()
    if owner_id is None or not tenant_id:
        raise HTTPException(status_code=401, detail="authentication_required")
    slide = await sql_session.get(SlideModel, id)
    if slide is None or slide.owner_id != owner_id or not slide.is_svg():
        raise HTTPException(status_code=404, detail="Slide not found")
    presentation = await require_live_presentation(sql_session, slide.presentation)
    if presentation.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="Presentation not found")
    canvas = presentation_canvas_contract(presentation.theme)
    try:
        svg = await resolve_slide_svg_preview(slide, session=sql_session,
            owner_id=owner_id, tenant_id=tenant_id, width=canvas.width_px, height=canvas.height_px)
    except (ValueError, KeyError, ArtifactStoreError) as error:
        raise HTTPException(status_code=409, detail="svg_preview_unavailable") from error
    return Response(svg, media_type="image/svg+xml", headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'",
    })

# Authored decks are model-authored images (no React layout), so per-slide LLM editing
# does not apply — the deck is view-only in-app and edited in PowerPoint via the PPTX.
_AUTHORED_NOT_EDITABLE = {
    "code": "authored_slide_not_editable",
    **user_message(UserMessageKey.SLIDE_AUTHORED_NOT_EDITABLE).fields("message"),
}


@SLIDE_ROUTER.post("/edit")
async def edit_slide(
    id: Annotated[uuid.UUID, Body()],
    prompt: Annotated[str, Body()],
    sql_session: AsyncSession = Depends(get_async_session),
):
    require_legacy_presentation_write_path()
    slide = await sql_session.get(SlideModel, id)
    if not slide:
        raise HTTPException(status_code=404, detail="Slide not found")
    presentation = await sql_session.get(PresentationModel, slide.presentation)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")
    if presentation.is_svg() or slide.is_svg():
        raise HTTPException(status_code=400, detail="svg_dom_edit_not_supported")
    if presentation.is_authored():
        raise HTTPException(
            status_code=400,
            detail=_AUTHORED_NOT_EDITABLE,
        )

    memory_context = await MEM0_PRESENTATION_MEMORY_SERVICE.retrieve_context(
        presentation.id,
        prompt,
    )

    presentation_layout = presentation.get_layout()
    slide_layout = await get_slide_layout_from_prompt(
        prompt,
        presentation_layout,
        slide,
        memory_context,
    )

    edited_slide_content = await get_edited_slide_content(
        prompt,
        slide,
        presentation.language,
        slide_layout,
        presentation.tone,
        presentation.verbosity,
        presentation.instructions,
        memory_context,
    )

    image_generation_service = ImageGenerationService(get_images_directory())

    # This will mutate edited_slide_content
    new_assets = await process_old_and_new_slides_and_fetch_assets(
        image_generation_service,
        slide.content,
        edited_slide_content,
        icon_weight=presentation.get_layout().icon_weight,
    )

    # Always assign a new unique id to the slide
    slide.id = uuid.uuid4()

    sql_session.add(slide)
    slide.content = edited_slide_content
    slide.layout = slide_layout.id
    slide.speaker_note = edited_slide_content.get("__speaker_note__", "")
    sql_session.add_all(new_assets)
    await sql_session.commit()

    await MEM0_PRESENTATION_MEMORY_SERVICE.store_slide_edit(
        presentation_id=presentation.id,
        slide_index=slide.index,
        edit_prompt=prompt,
        edited_slide_content=edited_slide_content,
    )

    return slide


@SLIDE_ROUTER.post("/edit-html", response_model=SlideModel)
async def edit_slide_html(
    id: Annotated[uuid.UUID, Body()],
    prompt: Annotated[str, Body()],
    html: Annotated[Optional[str], Body()] = None,
    sql_session: AsyncSession = Depends(get_async_session),
):
    require_legacy_presentation_write_path()
    slide = await sql_session.get(SlideModel, id)
    if not slide:
        raise HTTPException(status_code=404, detail="Slide not found")

    presentation = await sql_session.get(PresentationModel, slide.presentation)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")
    if presentation.is_svg() or slide.is_svg():
        raise HTTPException(status_code=400, detail="svg_dom_edit_not_supported")
    if presentation.is_authored():
        raise HTTPException(
            status_code=400,
            detail=_AUTHORED_NOT_EDITABLE,
        )

    html_to_edit = html or slide.html_content
    if not html_to_edit:
        raise HTTPException(status_code=400, detail="No HTML to edit")

    memory_context = await MEM0_PRESENTATION_MEMORY_SERVICE.retrieve_context(
        presentation.id,
        prompt,
    )

    edited_slide_html = await get_edited_slide_html(
        prompt,
        html_to_edit,
        memory_context,
    )

    # Always assign a new unique id to the slide
    # This is to ensure that the nextjs can track slide updates
    slide.id = uuid.uuid4()

    sql_session.add(slide)
    slide.html_content = edited_slide_html
    await sql_session.commit()

    await MEM0_PRESENTATION_MEMORY_SERVICE.store_slide_edit(
        presentation_id=presentation.id,
        slide_index=slide.index,
        edit_prompt=prompt,
        edited_slide_content=edited_slide_html,
    )

    return slide
