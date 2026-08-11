"""Core presentation editor operations.

What remains after the presentation god-file was split into cohesive siblings
(presentation_crud / presentation_generate / presentation_helpers): the
operations that act on a prepared/existing presentation — prepare (compose
structure for the editor), stream (SSE slide-content), update (autosave), and
edit/derive (re-export with new content).
"""

import asyncio
import json
import logging
import uuid
from typing import Annotated, List, Literal, Optional, cast

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.presentation_and_path import PresentationPathAndEditPath
from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_from_template import (
    AuthoredPageRemakePreviewRequest,
    AuthoredPageRemakePublishRequest,
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
from services.authored_exact_source_snapshot import (
    ExactSourceSnapshotError,
    create_exact_source_snapshot,
)
from services.authored_quality_ocr import get_ocr_readiness
from services.authored_quality_review_service import (
    queue_authored_quality_review,
)
from services.authored_page_remake_history import (
    list_authored_page_remake_previews,
)
from services.authored_page_remake_service import (
    AuthoredPageRemakeError,
    publish_authored_page_remake_legacy,
    queue_authored_page_remake_preview,
    remake_http_error,
)
from services.database import get_async_session
from services.generation_pipeline import build_template_structure
from services.image_generation_service import ImageGenerationService
from services.mem0_presentation_memory_service import (
    MEM0_PRESENTATION_MEMORY_SERVICE,
)
from services.presentation_access_service import require_live_presentation
from services.presentation_export_attempt_service import sha256_canonical_json
from services.presentation_export_router import (
    execute_presentation_export,
    terminalize_standalone_presentation_export,
)
from services.workspace_legacy_write_gate import (
    require_legacy_presentation_write_path,
)
from utils.asset_directory_utils import get_images_directory
from utils.dict_utils import deep_update
from utils.llm_calls.compose_slides import compose_and_project
from utils.llm_calls.generate_slide_content import (
    get_slide_content_from_type_and_outline,
)
from utils.outline_utils import get_images_for_slides_from_outline
from utils.process_slides import (
    process_slide_add_placeholder_assets,
    process_slide_and_fetch_assets,
)
from utils.authored_styles import (
    load_authored_styles,
    validate_exact_reconstruction_style,
)
from utils.llm_calls.author_deck import plan_deck_roles
from utils.llm_calls.author_slide import EXACT_RECONSTRUCTION, normalize_reference_mode

from utils.owner_context import get_current_owner_id
from models.sql.workspace import get_workspace_owner_scope
from api.v1.ppt.endpoints.presentation_helpers import (
    build_export_cookie_header,
    resolve_presentation_fonts,
)
from api.v1.ppt.endpoints.presentation_generate import queue_presentation_generation

PRESENTATION_ROUTER = APIRouter(prefix="/presentation", tags=["Presentation"])
_build_export_cookie_header = build_export_cookie_header
logger = logging.getLogger(__name__)

async def _notify_slide_assets_ready(
    slide_index: int,
    asset_task: asyncio.Task,
    asset_events: asyncio.Queue,
) -> None:
    """Always release the stream waiter, even when one asset provider fails.

    Slide content already contains local placeholder assets at this point, so a
    transient image/icon provider failure must degrade to those placeholders
    instead of leaving the SSE stream waiting forever.
    """
    try:
        generated_assets = await asset_task
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception(
            "Asset generation failed for slide %s; keeping placeholders",
            slide_index,
        )
        generated_assets = []

    await asset_events.put((slide_index, generated_assets))


async def apply_presentation_prepare(
    presentation_id: uuid.UUID,
    outlines: List[SlideOutlineModel],
    layout: PresentationLayoutModel,
    title: Optional[str],
    sql_session: AsyncSession,
) -> PresentationModel:
    """Compose an outline into a deck's persisted structure.

    This boundary intentionally performs no route/write-gate check, mirroring
    `apply_presentation_update`: the HTTP route applies the runner-only gate,
    while an already-authorized caller reaches the same composition and
    persistence directly.
    """
    if not outlines:
        raise HTTPException(status_code=400, detail="Outlines are required")

    presentation = await require_live_presentation(sql_session, presentation_id)

    presentation_outline_model = PresentationOutlineModel(slides=outlines)

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


@PRESENTATION_ROUTER.post("/prepare", response_model=PresentationModel)
async def prepare_presentation(
    presentation_id: Annotated[uuid.UUID, Body()],
    outlines: Annotated[List[SlideOutlineModel], Body()],
    layout: Annotated[PresentationLayoutModel, Body()],
    title: Annotated[Optional[str], Body()] = None,
    sql_session: AsyncSession = Depends(get_async_session),
):
    require_legacy_presentation_write_path(presentation_id)
    return await apply_presentation_prepare(
        presentation_id,
        outlines,
        layout,
        title,
        sql_session,
    )


@PRESENTATION_ROUTER.get(
    "/quality-review/readiness",
    summary="Check the OCR dependency used by authored quality review",
)
async def authored_quality_review_readiness():
    """Return a content-free OCR readiness probe suitable for operations."""
    return await get_ocr_readiness()


async def build_authored_retemplate_generation_request(
    id: uuid.UUID,
    request: RetemplatePresentationRequest,
    sql_session: AsyncSession,
) -> GeneratePresentationRequest:
    """Build a new-deck request from a saved authored manuscript.

    This boundary intentionally performs no route/write-gate check.  The HTTP
    route applies the legacy gate, while the chat-first workspace handler calls
    it from its already-authorized durable operation.
    """
    source = await require_live_presentation(sql_session, id)

    is_authored_source = source.is_authored()
    if not is_authored_source:
        source_slides = (
            await sql_session.execute(
                select(SlideModel).where(SlideModel.presentation == id)
            )
        ).scalars().all()
        is_authored_source = any(slide.is_authored() for slide in source_slides)

    if not is_authored_source:
        raise HTTPException(
            status_code=400,
            detail="Only AI-authored presentations can change authored templates",
        )

    authored_style = request.authored_style.strip()
    styles_by_id = {style.id: style for style in load_authored_styles()}
    selected_style = styles_by_id.get(authored_style)
    if not authored_style or selected_style is None:
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

    exact_snapshot_id = None
    if normalize_reference_mode(selected_style.reference_mode) == EXACT_RECONSTRUCTION:
        try:
            validate_exact_reconstruction_style(
                selected_style,
                slide_count=len(source_outline.slides),
                roles=plan_deck_roles(source_outline),
            )
            source_slides = (
                await sql_session.execute(
                    select(SlideModel)
                    .where(SlideModel.presentation == id)
                    .order_by(SlideModel.index)
                )
            ).scalars().all()
            exact_snapshot_id = await create_exact_source_snapshot(
                selected_style.id,
                source_slides,
                expected_slide_count=len(source_outline.slides),
            )
        except (ExactSourceSnapshotError, ValueError) as error:
            logger.warning(
                "Exact authored retemplate admission rejected (%s)",
                type(error).__name__,
            )
            raise HTTPException(
                status_code=422,
                detail="authored_exact_source_snapshot_invalid",
            ) from error

    generation_payload = {
        "content": (
            source.title or source.content or source_outline.slides[0].content
        )[:500],
        "slides_markdown": [slide.content for slide in source_outline.slides],
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
    generation_request._authored_exact_snapshot_id = exact_snapshot_id
    return generation_request


@PRESENTATION_ROUTER.post(
    "/{id}/quality-review",
    response_model=AsyncPresentationGenerationTaskModel,
    summary="Review and automatically repair an existing AI-authored presentation",
)
async def quality_review_authored_presentation(
    id: uuid.UUID,
    request: AuthoredQualityReviewRequest,
    background_tasks: BackgroundTasks,
    sql_session: AsyncSession = Depends(get_async_session),
):
    """Run high-quality visual QA against persisted authored HTML.

    The task can inspect the whole deck, the current slide, or an explicit page
    subset. Slides with visible problems are automatically re-authored, and the
    pre-review deck is saved to version history before any live slide is replaced.
    """
    require_legacy_presentation_write_path(id)
    await require_live_presentation(sql_session, id)
    return await queue_authored_quality_review(
        id, request, background_tasks, sql_session
    )


@PRESENTATION_ROUTER.post(
    "/{id}/slides/{slide_index}/remake/preview",
    response_model=AsyncPresentationGenerationTaskModel,
    summary="Create a detached AI-authored replacement for one page",
)
async def preview_authored_page_remake(
    id: uuid.UUID,
    slide_index: int,
    request: AuthoredPageRemakePreviewRequest,
    background_tasks: BackgroundTasks,
    sql_session: AsyncSession = Depends(get_async_session),
):
    """Generate exactly one page candidate without mutating the presentation."""

    await require_live_presentation(sql_session, id)
    try:
        return await queue_authored_page_remake_preview(
            id, slide_index, request, background_tasks, sql_session
        )
    except AuthoredPageRemakeError as error:
        raise remake_http_error(error) from error


@PRESENTATION_ROUTER.get(
    "/{id}/remake/previews",
    summary="List recent one-page remake previews for this presentation",
)
async def list_authored_page_remake_preview_history(
    id: uuid.UUID,
    sql_session: AsyncSession = Depends(get_async_session),
):
    """Replay the remake activity a reloaded client would otherwise lose."""

    await require_live_presentation(sql_session, id)
    return await list_authored_page_remake_previews(sql_session, id)


@PRESENTATION_ROUTER.post(
    "/{id}/slides/{slide_index}/remake/publish",
    summary="Publish an approved one-page remake candidate",
)
async def publish_authored_page_remake_candidate(
    id: uuid.UUID,
    slide_index: int,
    request: AuthoredPageRemakePublishRequest,
    sql_session: AsyncSession = Depends(get_async_session),
):
    """Publish only the exact detached candidate approved by the user."""

    require_legacy_presentation_write_path(id)
    await require_live_presentation(sql_session, id)
    try:
        return await publish_authored_page_remake_legacy(
            session=sql_session,
            presentation_id=id,
            slide_index=slide_index,
            preview_task_id=request.preview_task_id,
        )
    except AuthoredPageRemakeError as error:
        raise remake_http_error(error) from error


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
    require_legacy_presentation_write_path(id)
    generation_request = await build_authored_retemplate_generation_request(
        id, request, sql_session
    )
    return await queue_presentation_generation(
        generation_request,
        background_tasks,
        sql_session,
        export_cookie_header=build_export_cookie_header(request_http),
    )


# Smart generation enters through the canonical queued generation driver. The
# former direct SSE implementation was unreachable and bypassed revision,
# reference-snapshot, and render-artifact boundaries.


@PRESENTATION_ROUTER.get("/stream/{id}", response_model=PresentationWithSlides)
async def stream_presentation(
    id: uuid.UUID,
    request: Request,
    regenerate: bool = False,
    sql_session: AsyncSession = Depends(get_async_session),
):
    # Despite being a legacy GET route, the stream replaces persisted slides.
    require_legacy_presentation_write_path(id)
    presentation = await require_live_presentation(sql_session, id)
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

        asset_events: asyncio.Queue = asyncio.Queue()

        slides: List[SlideModel] = []
        generated_assets_by_slide: List[list] = []
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
            generated_assets_by_slide.append([])
            asyncio.create_task(
                _notify_slide_assets_ready(i, asset_task, asset_events)
            )

            yield SSEResponse(
                event="response",
                data=json.dumps({"type": "chunk", "chunk": slide.model_dump_json()}),
            ).to_string()

            while True:
                try:
                    done_idx, assets = asset_events.get_nowait()
                except asyncio.QueueEmpty:
                    break
                generated_assets_by_slide[done_idx] = assets
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
            done_idx, assets = await asset_events.get()
            generated_assets_by_slide[done_idx] = assets
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

        generated_assets = []
        for assets_list in generated_assets_by_slide:
            generated_assets.extend(assets_list)

        # Moved this here to make sure new slides are generated before deleting the old ones
        await sql_session.execute(
            delete(SlideModel).where(
                SlideModel.presentation == id,
                SlideModel.owner_id == get_current_owner_id(),
            )
        )
        await sql_session.commit()

        sql_session.add(presentation)
        sql_session.add_all(slides)
        sql_session.add_all(generated_assets)
        await sql_session.commit()

        response = PresentationWithSlides(
            **presentation.model_dump(exclude={"fonts"}),
            slides=slides,
            fonts=await resolve_presentation_fonts(presentation, slides, sql_session),
        )

        yield SSECompleteResponse(
            key="presentation",
            value=response.model_dump(mode="json"),
        ).to_string()

    return StreamingResponse(inner(), media_type="text/event-stream")


async def apply_presentation_update(
    id: uuid.UUID,
    sql_session: AsyncSession,
    *,
    n_slides: Optional[int] = None,
    title: Optional[str] = None,
    theme: Optional[dict] = None,
    slides: Optional[List[SlideModel]] = None,
) -> PresentationWithSlides:
    """Validate and persist a full presentation update.

    This boundary intentionally performs no route/write-gate check, mirroring
    `build_authored_retemplate_generation_request`: the HTTP route applies the
    runner-only gate, while an already-authorized caller reaches the same
    validation and persistence directly.
    """
    presentation = await require_live_presentation(sql_session, id)

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
        # SQLModel table classes are permissive when FastAPI constructs them as
        # request parameters. Revalidate their raw trees explicitly at this
        # persistence boundary so malformed native UI cannot surface later as
        # an unhandled snapshot/SQLAlchemy error.
        validated_slides: list[SlideModel] = []
        for slide_index, slide in enumerate(slides):
            try:
                raw_slide = {
                    field_name: getattr(slide, field_name)
                    for field_name in SlideModel.model_fields
                }
                validated_slides.append(
                    SlideModel.model_validate(raw_slide)
                )
            except ValidationError as error:
                details = []
                for validation_error in error.errors(include_url=False):
                    validation_error = dict(validation_error)
                    validation_error.pop("ctx", None)
                    validation_error["loc"] = [
                        "body",
                        "slides",
                        slide_index,
                        *validation_error.get("loc", ()),
                    ]
                    details.append(validation_error)
                raise HTTPException(status_code=422, detail=details) from error
        slides = validated_slides

        # Snapshot this saved state into durable history (throttled + capped) so the
        # editor's undo survives reloads. Runs before the full-replace below.
        await version_service.snapshot_slides(
            sql_session,
            presentation.id,
            [slide.model_dump(mode="json") for slide in slides],
        )

        await sql_session.execute(
            delete(SlideModel).where(
                SlideModel.presentation == presentation.id,
                SlideModel.owner_id == get_current_owner_id(),
            )
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
        **presentation.model_dump(exclude={"fonts"}),
        slides=response_slides,
        fonts=fonts,
    )


@PRESENTATION_ROUTER.patch("/update", response_model=PresentationWithSlides)
async def update_presentation(
    id: Annotated[uuid.UUID, Body()],
    n_slides: Annotated[Optional[int], Body()] = None,
    title: Annotated[Optional[str], Body()] = None,
    theme: Annotated[Optional[dict], Body()] = None,
    slides: Annotated[Optional[List[SlideModel]], Body()] = None,
    sql_session: AsyncSession = Depends(get_async_session),
):
    require_legacy_presentation_write_path(id)
    return await apply_presentation_update(
        id,
        sql_session,
        n_slides=n_slides,
        title=title,
        theme=theme,
        slides=slides,
    )


@PRESENTATION_ROUTER.post("/edit", response_model=PresentationPathAndEditPath)
async def edit_presentation_with_new_content(
    request_http: Request,
    data: Annotated[EditPresentationRequest, Body()],
    sql_session: AsyncSession = Depends(get_async_session),
):
    require_legacy_presentation_write_path(data.presentation_id)
    presentation = await require_live_presentation(
        sql_session, data.presentation_id
    )

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
        delete(SlideModel).where(
            SlideModel.id.in_(slides_to_delete),
            SlideModel.owner_id == get_current_owner_id(),
        )
    )

    sql_session.add_all(new_slides)
    await sql_session.commit()

    request_hash = sha256_canonical_json(
        {"operation": "edit", "request": data.model_dump(mode="json")}
    )
    execution = await execute_presentation_export(
        sql_session,
        render_presentation_id=presentation.id,
        attempt_presentation_id=presentation.id,
        export_format=cast(Literal["pptx", "pdf"], data.export_as),
        expected_render_revision=presentation.revision,
        source_revision=presentation.revision,
        title=presentation.title or str(uuid.uuid4()),
        cookie_header=build_export_cookie_header(request_http),
        owner_id=presentation.owner_id,
        owner_scope=get_workspace_owner_scope(),
        workspace_operation_id=None,
        request_hash=request_hash,
        idempotency_key_hash=sha256_canonical_json(
            {
                "operation": "edit-export",
                "presentation_id": str(presentation.id),
                "revision": presentation.revision,
                "format": data.export_as,
                "request_hash": request_hash,
            }
        ),
    )
    await terminalize_standalone_presentation_export(sql_session, execution)
    presentation_and_path = execution.presentation

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
    require_legacy_presentation_write_path(data.presentation_id)
    presentation = await require_live_presentation(
        sql_session, data.presentation_id
    )

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

    request_hash = sha256_canonical_json(
        {"operation": "derive", "request": data.model_dump(mode="json")}
    )
    execution = await execute_presentation_export(
        sql_session,
        render_presentation_id=new_presentation.id,
        attempt_presentation_id=new_presentation.id,
        export_format=cast(Literal["pptx", "pdf"], data.export_as),
        expected_render_revision=new_presentation.revision,
        source_revision=new_presentation.revision,
        title=new_presentation.title or str(uuid.uuid4()),
        cookie_header=build_export_cookie_header(request_http),
        owner_id=new_presentation.owner_id,
        owner_scope=get_workspace_owner_scope(),
        workspace_operation_id=None,
        request_hash=request_hash,
        idempotency_key_hash=sha256_canonical_json(
            {
                "operation": "derive-export",
                "presentation_id": str(new_presentation.id),
                "revision": new_presentation.revision,
                "format": data.export_as,
                "request_hash": request_hash,
            }
        ),
    )
    await terminalize_standalone_presentation_export(sql_session, execution)
    presentation_and_path = execution.presentation

    return PresentationPathAndEditPath(
        **presentation_and_path.model_dump(),
        edit_path=f"/presentation?id={new_presentation.id}",
    )
