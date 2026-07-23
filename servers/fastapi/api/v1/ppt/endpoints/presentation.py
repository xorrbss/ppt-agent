"""Core presentation editor operations.

What remains after the presentation god-file was split into cohesive siblings
(presentation_crud / presentation_generate / presentation_helpers): the
operations that act on a prepared/existing presentation — prepare (compose
structure for the editor), stream (SSE slide-content), update (autosave), and
edit/derive (re-export with new content).
"""

import asyncio
import json
import uuid
from typing import Annotated, List, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.presentation_and_path import PresentationPathAndEditPath
from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_from_template import (
    AuthoredQualityReviewRequest,
    EditPresentationRequest,
    RetemplatePresentationRequest,
)
from models.presentation_layout import PresentationLayoutModel
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from models.presentation_with_slides import PresentationWithSlides
from models.slide_spec_model import spec_to_blocks
from models.sql.presentation import PresentationModel
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.slide import SlideModel
from models.sse_response import SSECompleteResponse, SSEErrorResponse, SSEResponse
from services import presentation_version_service as version_service
from services.authored_quality_review_service import (
    queue_authored_quality_review,
)
from services.database import get_async_session
from services.generation_pipeline import build_template_structure
from services.image_generation_service import ImageGenerationService
from services.mem0_presentation_memory_service import (
    MEM0_PRESENTATION_MEMORY_SERVICE,
)
from utils.asset_directory_utils import get_images_directory
from utils.dict_utils import deep_update
from utils.export_utils import export_presentation
from utils.llm_calls.compose_slides import compose_and_project
from utils.llm_calls.generate_slide_content import (
    get_slide_content_from_type_and_outline,
)
from utils.outline_utils import get_images_for_slides_from_outline
from utils.process_slides import (
    process_slide_add_placeholder_assets,
    process_slide_and_fetch_assets,
)
from utils.authored_styles import load_authored_styles

from api.v1.ppt.endpoints.presentation_helpers import (
    build_export_cookie_header,
    resolve_presentation_fonts,
)
from api.v1.ppt.endpoints.presentation_generate import queue_presentation_generation

PRESENTATION_ROUTER = APIRouter(prefix="/presentation", tags=["Presentation"])


def _is_authored_slide(slide: SlideModel) -> bool:
    """Recognize authored slide sentinels retained by legacy saved decks."""
    return (
        slide.layout_group == "authored"
        or (slide.layout or "").startswith("authored:")
        or (
            isinstance(slide.content, dict)
            and slide.content.get("__authored__") is True
        )
    )


@PRESENTATION_ROUTER.post("/prepare", response_model=PresentationModel)
async def prepare_presentation(
    presentation_id: Annotated[uuid.UUID, Body()],
    outlines: Annotated[List[SlideOutlineModel], Body()],
    layout: Annotated[PresentationLayoutModel, Body()],
    title: Annotated[Optional[str], Body()] = None,
    sql_session: AsyncSession = Depends(get_async_session),
):
    if not outlines:
        raise HTTPException(status_code=400, detail="Outlines are required")

    presentation = await sql_session.get(PresentationModel, presentation_id)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")

    presentation_outline_model = PresentationOutlineModel(slides=outlines)

    total_slide_layouts = len(layout.slides)
    total_outlines = len(outlines)

    if layout.name == "adaptive":
        # Adaptive path: compose one SlideSpec per outline slide (archetype chosen
        # for content + variety). Persist the composition to deck_plan and project
        # to outlines/structure so /stream + editor read it unchanged.
        composition, presentation_outline_model, presentation_structure = (
            await compose_and_project(
                presentation_outline_model,
                layout,
                language=presentation.language,
                tone=presentation.tone,
                verbosity=presentation.verbosity,
                instructions=presentation.instructions,
            )
        )
        presentation.set_deck_plan(composition)
        presentation.n_slides = len(presentation_structure.slides)
    else:
        presentation_outline_model, presentation_structure = (
            await build_template_structure(
                presentation_outline_model,
                layout,
                instructions=presentation.instructions,
                using_slides_markdown=False,
                include_table_of_contents=presentation.include_table_of_contents,
                include_title_slide=presentation.include_title_slide,
                target_n_slides=(
                    presentation.n_slides if presentation.n_slides > 0 else None
                ),
            )
        )
        presentation.n_slides = len(presentation_structure.slides)

    sql_session.add(presentation)
    presentation.mode = "adaptive" if layout.name == "adaptive" else "template"
    presentation.outlines = presentation_outline_model.model_dump(mode="json")
    presentation.title = title or presentation.title
    presentation.set_layout(layout)
    presentation.set_structure(presentation_structure)
    await sql_session.commit()

    await MEM0_PRESENTATION_MEMORY_SERVICE.store_generated_outlines(
        presentation.id,
        presentation.outlines,
    )

    return presentation


@PRESENTATION_ROUTER.post(
    "/{id}/quality-review",
    response_model=AsyncPresentationGenerationTaskModel,
    summary="Review and optionally repair an existing AI-authored presentation",
)
async def quality_review_authored_presentation(
    id: uuid.UUID,
    request: AuthoredQualityReviewRequest,
    background_tasks: BackgroundTasks,
    sql_session: AsyncSession = Depends(get_async_session),
):
    """Run high-quality visual QA against persisted authored HTML.

    The task can inspect either the whole deck or the current slide. In repair mode,
    only slides with visible problems are re-authored, and the pre-review deck is
    saved to version history before any live slide is replaced.
    """
    return await queue_authored_quality_review(
        id, request, background_tasks, sql_session
    )


@PRESENTATION_ROUTER.post(
    "/{id}/retemplate",
    response_model=AsyncPresentationGenerationTaskModel,
    summary="Re-author a presentation with another AI-authored template",
)
async def retemplate_authored_presentation(
    id: uuid.UUID,
    request: RetemplatePresentationRequest,
    request_http: Request,
    background_tasks: BackgroundTasks,
    sql_session: AsyncSession = Depends(get_async_session),
):
    """Create a new AI-authored deck from the saved semantic outline.

    The authored HTML is intentionally not used as generation input.  It is a
    rendered artifact and may contain shortened or decorative text. The stored
    LLM-authored outline remains the authoritative source, and the original
    presentation is never modified.
    """
    source = await sql_session.get(PresentationModel, id)
    if not source:
        raise HTTPException(status_code=404, detail="Presentation not found")

    # Early authored decks did not always persist presentation.mode/theme.mode.
    # Their slides still contain authored layout/content sentinels.
    is_authored_source = source.is_authored()
    if not is_authored_source:
        source_slides = (
            await sql_session.execute(
                select(SlideModel).where(SlideModel.presentation == id)
            )
        ).scalars().all()
        is_authored_source = any(_is_authored_slide(slide) for slide in source_slides)

    if not is_authored_source:
        raise HTTPException(
            status_code=400,
            detail="Only AI-authored presentations can change authored templates",
        )

    authored_style = request.authored_style.strip()
    valid_style_ids = {style.id for style in load_authored_styles()}
    if not authored_style or authored_style not in valid_style_ids:
        raise HTTPException(status_code=400, detail="AI-authored template not found")

    source_outline = source.get_presentation_outline()
    if not source_outline or not source_outline.slides:
        raise HTTPException(
            status_code=400,
            detail="The authored presentation has no reusable semantic content",
        )
    if any(not slide.content.strip() for slide in source_outline.slides):
        raise HTTPException(
            status_code=400,
            detail="Every source slide must have semantic content",
        )

    generation_payload = {
        "content": (
            source.title or source.content or source_outline.slides[0].content
        )[:500],
        "slides_markdown": [slide.content for slide in source_outline.slides],
        # The saved semantic outline is the authoritative LLM-authored manuscript.
        # Do not feed rendered html_content back into the model and do not add
        # internal conversion markers to the user's instructions.
        "instructions": source.instructions,
        "language": source.language,
        "template": "authored",
        "include_table_of_contents": False,
        "include_title_slide": False,
        "export_as": "pptx",
        "vision_qa": request.vision_qa,
        "authored_style": authored_style,
    }
    if source.tone:
        generation_payload["tone"] = source.tone
    if source.verbosity:
        generation_payload["verbosity"] = source.verbosity

    generation_request = GeneratePresentationRequest(**generation_payload)
    return await queue_presentation_generation(
        generation_request,
        background_tasks,
        sql_session,
        export_cookie_header=build_export_cookie_header(request_http),
    )


@PRESENTATION_ROUTER.get("/stream/{id}", response_model=PresentationWithSlides)
async def stream_presentation(
    id: uuid.UUID,
    request: Request,
    regenerate: bool = False,
    sql_session: AsyncSession = Depends(get_async_session),
):
    presentation = await sql_session.get(PresentationModel, id)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")
    if presentation.is_authored():
        raise HTTPException(
            status_code=400,
            detail=(
                "AI 저작(authored) 프레젠테이션은 스트리밍 편집기를 사용하지 않습니다. "
                "뷰어로 열거나 내보낸 PPTX를 사용하세요."
            ),
        )
    if not presentation.structure:
        raise HTTPException(
            status_code=400,
            detail="Presentation not prepared for stream",
        )
    if not presentation.outlines:
        raise HTTPException(
            status_code=400,
            detail="Outlines can not be empty",
        )

    image_generation_service = ImageGenerationService(get_images_directory())

    async def inner():
        if await request.is_disconnected():
            return

        # Idempotent stream: if this deck was already generated, REPLAY the
        # persisted slides through the SSE envelope instead of re-running the LLM
        # and delete-replacing slides. A browser refresh / link prefetch of the
        # stream URL must not silently wipe edits made since generation. The
        # explicit "재생성" (regenerate) action passes regenerate=true to force a
        # fresh generation.
        existing_slides = (
            []
            if regenerate
            else
            (
                await sql_session.execute(
                    select(SlideModel)
                    .where(SlideModel.presentation == id)
                    .order_by(SlideModel.index)
                )
            )
            .scalars()
            .all()
        )
        if existing_slides:
            yield SSEResponse(
                event="response",
                data=json.dumps({"type": "chunk", "chunk": '{ "slides": [ '}),
            ).to_string()
            for existing_slide in existing_slides:
                yield SSEResponse(
                    event="response",
                    data=json.dumps(
                        {"type": "chunk", "chunk": existing_slide.model_dump_json()}
                    ),
                ).to_string()
            yield SSEResponse(
                event="response",
                data=json.dumps({"type": "chunk", "chunk": " ] }"}),
            ).to_string()
            return

        structure = presentation.get_structure()
        layout = presentation.get_layout()
        icon_weight = layout.icon_weight
        outline = presentation.get_presentation_outline()
        deck_plan = presentation.get_deck_plan()
        image_urls_for_slides = get_images_for_slides_from_outline(outline.slides)

        async_assets_generation_tasks: List[asyncio.Task] = []
        asset_events: asyncio.Queue = asyncio.Queue()

        async def notify_slide_assets_ready(slide_index: int, asset_task: asyncio.Task):
            await asset_task
            await asset_events.put(slide_index)

        slides: List[SlideModel] = []
        yield SSEResponse(
            event="response",
            data=json.dumps({"type": "chunk", "chunk": '{ "slides": [ '}),
        ).to_string()
        yielded_slide_asset_sse_count = 0

        for i, slide_layout_index in enumerate(structure.slides):
            if await request.is_disconnected():
                raise asyncio.CancelledError

            slide_layout = layout.slides[slide_layout_index]

            try:
                if layout.name == "adaptive" and deck_plan and i < len(deck_plan.slides):
                    # Adaptive: use the persisted SlideSpec directly (no LLM call).
                    spec = deck_plan.slides[i]
                    slide_content = spec_to_blocks(spec)
                    slide_speaker_note = spec.speaker_note or ""
                else:
                    slide_content = await get_slide_content_from_type_and_outline(
                        slide_layout,
                        outline.slides[i],
                        presentation.language,
                        presentation.tone,
                        presentation.verbosity,
                        presentation.instructions,
                        disconnect_checker=request.is_disconnected,
                    )
                    slide_speaker_note = slide_content.get("__speaker_note__", "")
            except HTTPException as e:
                yield SSEErrorResponse(detail=e.detail).to_string()
                return

            slide = SlideModel(
                presentation=id,
                layout_group=layout.name,
                layout=slide_layout.id,
                index=i,
                speaker_note=slide_speaker_note,
                content=slide_content,
            )
            slides.append(slide)

            # This will mutate slide and add placeholder assets
            process_slide_add_placeholder_assets(slide)

            # This will mutate slide - start task immediately so it runs in parallel with next slide LLM generation
            asset_task = asyncio.create_task(
                process_slide_and_fetch_assets(
                    image_generation_service,
                    slide,
                    outline_image_urls=(
                        image_urls_for_slides[i]
                        if i < len(image_urls_for_slides)
                        else None
                    ),
                    icon_weight=icon_weight,
                )
            )
            async_assets_generation_tasks.append(asset_task)
            asyncio.create_task(notify_slide_assets_ready(i, asset_task))

            yield SSEResponse(
                event="response",
                data=json.dumps({"type": "chunk", "chunk": slide.model_dump_json()}),
            ).to_string()

            while True:
                try:
                    done_idx = asset_events.get_nowait()
                except asyncio.QueueEmpty:
                    break
                yielded_slide_asset_sse_count += 1
                yield SSEResponse(
                    event="response",
                    data=json.dumps(
                        {
                            "type": "slide_assets",
                            "slide_index": done_idx,
                            "slide": slides[done_idx].model_dump(mode="json"),
                        }
                    ),
                ).to_string()

        yield SSEResponse(
            event="response",
            data=json.dumps({"type": "chunk", "chunk": " ] }"}),
        ).to_string()

        while yielded_slide_asset_sse_count < len(slides):
            done_idx = await asset_events.get()
            yielded_slide_asset_sse_count += 1
            yield SSEResponse(
                event="response",
                data=json.dumps(
                    {
                        "type": "slide_assets",
                        "slide_index": done_idx,
                        "slide": slides[done_idx].model_dump(mode="json"),
                    }
                ),
            ).to_string()

        generated_assets_lists = await asyncio.gather(*async_assets_generation_tasks)
        generated_assets = []
        for assets_list in generated_assets_lists:
            generated_assets.extend(assets_list)

        # Moved this here to make sure new slides are generated before deleting the old ones
        await sql_session.execute(
            delete(SlideModel).where(SlideModel.presentation == id)
        )
        await sql_session.commit()

        sql_session.add(presentation)
        sql_session.add_all(slides)
        sql_session.add_all(generated_assets)
        await sql_session.commit()

        response = PresentationWithSlides(
            **presentation.model_dump(),
            slides=slides,
            fonts=await resolve_presentation_fonts(presentation, slides, sql_session),
        )

        yield SSECompleteResponse(
            key="presentation",
            value=response.model_dump(mode="json"),
        ).to_string()

    return StreamingResponse(inner(), media_type="text/event-stream")


@PRESENTATION_ROUTER.patch("/update", response_model=PresentationWithSlides)
async def update_presentation(
    id: Annotated[uuid.UUID, Body()],
    n_slides: Annotated[Optional[int], Body()] = None,
    title: Annotated[Optional[str], Body()] = None,
    theme: Annotated[Optional[dict], Body()] = None,
    slides: Annotated[Optional[List[SlideModel]], Body()] = None,
    sql_session: AsyncSession = Depends(get_async_session),
):
    presentation = await sql_session.get(PresentationModel, id)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")

    presentation_update_dict = {}
    if n_slides is not None:
        presentation_update_dict["n_slides"] = n_slides
    if title:
        presentation_update_dict["title"] = title
    # Only overwrite the theme when a theme object is actually sent. The previous
    # `if theme or theme is None` was ALWAYS true, so any partial update that
    # omitted theme (e.g. a title-only call from an MCP tool) silently nulled the
    # stored theme. Send an empty object to intentionally clear it.
    if theme is not None:
        presentation_update_dict["theme"] = theme

    if presentation_update_dict:
        presentation.sqlmodel_update(presentation_update_dict)
    # NOTE: `slides` is a FULL REPLACE of the deck's slides (the editor autosave
    # always sends the complete set, and expresses deletions by omission). Callers
    # that only want to change one slide must use /slide/edit, not a partial list
    # here, or the omitted slides are dropped.
    if slides:
        # Just to make sure id is UUID
        for slide in slides:
            slide.presentation = uuid.UUID(slide.presentation)
            slide.id = uuid.UUID(slide.id)

        # Snapshot this saved state into durable history (throttled + capped) so the
        # editor's undo survives reloads. Runs before the full-replace below.
        await version_service.snapshot_slides(
            sql_session,
            presentation.id,
            [slide.model_dump(mode="json") for slide in slides],
        )

        await sql_session.execute(
            delete(SlideModel).where(SlideModel.presentation == presentation.id)
        )
        sql_session.add_all(slides)

    await sql_session.commit()

    response_slides = slides or []
    fonts = await resolve_presentation_fonts(
        presentation,
        response_slides,
        sql_session,
    )

    return PresentationWithSlides(
        **presentation.model_dump(),
        slides=response_slides,
        fonts=fonts,
    )


@PRESENTATION_ROUTER.post("/edit", response_model=PresentationPathAndEditPath)
async def edit_presentation_with_new_content(
    request_http: Request,
    data: Annotated[EditPresentationRequest, Body()],
    sql_session: AsyncSession = Depends(get_async_session),
):
    presentation = await sql_session.get(PresentationModel, data.presentation_id)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")

    slides = await sql_session.scalars(
        select(SlideModel).where(SlideModel.presentation == data.presentation_id)
    )

    new_slides = []
    slides_to_delete = []
    for each_slide in slides:
        updated_content = None
        new_slide_data = list(
            filter(lambda x: x.index == each_slide.index, data.slides)
        )
        if new_slide_data:
            updated_content = deep_update(each_slide.content, new_slide_data[0].content)
            new_slides.append(
                each_slide.get_new_slide(presentation.id, updated_content)
            )
            slides_to_delete.append(each_slide.id)

    await sql_session.execute(
        delete(SlideModel).where(SlideModel.id.in_(slides_to_delete))
    )

    sql_session.add_all(new_slides)
    await sql_session.commit()

    presentation_and_path = await export_presentation(
        presentation.id,
        presentation.title or str(uuid.uuid4()),
        data.export_as,
        cookie_header=build_export_cookie_header(request_http),
    )

    return PresentationPathAndEditPath(
        **presentation_and_path.model_dump(),
        edit_path=f"/presentation?id={presentation.id}",
    )


@PRESENTATION_ROUTER.post("/derive", response_model=PresentationPathAndEditPath)
async def derive_presentation_from_existing_one(
    request_http: Request,
    data: Annotated[EditPresentationRequest, Body()],
    sql_session: AsyncSession = Depends(get_async_session),
):
    presentation = await sql_session.get(PresentationModel, data.presentation_id)
    if not presentation:
        raise HTTPException(status_code=404, detail="Presentation not found")

    slides = await sql_session.scalars(
        select(SlideModel).where(SlideModel.presentation == data.presentation_id)
    )

    new_presentation = presentation.get_new_presentation()
    new_slides = []
    for each_slide in slides:
        updated_content = None
        new_slide_data = list(
            filter(lambda x: x.index == each_slide.index, data.slides)
        )
        if new_slide_data:
            updated_content = deep_update(each_slide.content, new_slide_data[0].content)
        new_slides.append(
            each_slide.get_new_slide(new_presentation.id, updated_content)
        )

    sql_session.add(new_presentation)
    sql_session.add_all(new_slides)
    await sql_session.commit()

    presentation_and_path = await export_presentation(
        new_presentation.id,
        new_presentation.title or str(uuid.uuid4()),
        data.export_as,
        cookie_header=build_export_cookie_header(request_http),
    )

    return PresentationPathAndEditPath(
        **presentation_and_path.model_dump(),
        edit_path=f"/presentation?id={new_presentation.id}",
    )
