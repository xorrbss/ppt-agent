"""One-shot presentation generation pipeline (API path).

Split out of the presentation god-file. This module is the single cohesive
generation meaning-unit: request validation, the full generate handler
(outline -> layout/structure -> slides -> assets -> optional vision-QA ->
export), the background-task runner, and the three public endpoints
(`/generate`, `/generate/async`, `/status/{id}`).

NOTE: `generate_presentation_handler` is intentionally left as one linear
pipeline here; breaking that mega-function into phase helpers is a separate,
behavior-changing refactor and is tracked apart from this move-only split.
"""

import asyncio
import logging
import os
import traceback
import uuid
from datetime import datetime
from typing import List, Optional, Tuple

import dirtyjson
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Request

from constants.presentation import DEFAULT_TEMPLATES, MAX_NUMBER_OF_SLIDES
from enums.webhook_event import WebhookEvent
from models.api_error_model import APIErrorModel
from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_and_path import PresentationPathAndEditPath
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from models.slide_spec_model import archetype_to_layout_id, spec_to_blocks
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from models.sql.template import TemplateModel
from services.authored_presentation_service import (
    AUTHORED_TEMPLATE,
    generate_authored_presentation,
)
from services.concurrent_service import CONCURRENT_SERVICE
from services.database import async_session_maker, get_async_session
from services.documents_loader import DocumentsLoader
from services.generation_pipeline import build_template_structure
from services.image_generation_service import ImageGenerationService
from services.mem0_presentation_memory_service import (
    MEM0_PRESENTATION_MEMORY_SERVICE,
)
from services.webhook_service import WebhookService
from sqlalchemy.ext.asyncio import AsyncSession
from utils.asset_directory_utils import get_images_directory
from utils.export_utils import export_presentation
from utils.get_env import get_next_internal_base_url
from utils.get_layout_by_name import get_layout_by_name
from utils.llm_calls.compose_slides import compose_and_project
from utils.llm_calls.generate_content_brief import generate_content_brief
from utils.llm_calls.generate_presentation_outlines import (
    generate_ppt_outline,
    get_messages as get_outline_messages,
)
from utils.llm_calls.generate_slide_content import (
    get_slide_content_from_type_and_outline,
)
from utils.llm_utils import message_content_to_text
from utils.outline_utils import (
    get_images_for_slides_from_outline,
    get_no_of_outlines_to_generate_for_n_slides,
    get_presentation_title_from_presentation_outline,
)
from utils.process_slides import process_slide_and_fetch_assets

from api.v1.ppt.endpoints.presentation_helpers import build_export_cookie_header

logger = logging.getLogger(__name__)


PRESENTATION_GENERATE_ROUTER = APIRouter(
    prefix="/presentation", tags=["Presentation"]
)


async def check_if_api_request_is_valid(
    request: GeneratePresentationRequest,
    sql_session: AsyncSession = Depends(get_async_session),
) -> Tuple[uuid.UUID,]:
    presentation_id = uuid.uuid4()
    print(f"Presentation ID: {presentation_id}")

    # Making sure either content, slides markdown or files is provided
    if not (request.content or request.slides_markdown or request.files):
        raise HTTPException(
            status_code=400,
            detail="Either content or slides markdown or files is required to generate presentation",
        )

    if request.n_slides is not None and request.n_slides <= 0:
        raise HTTPException(
            status_code=400,
            detail="Number of slides must be greater than 0",
        )

    if request.n_slides is not None and request.n_slides > MAX_NUMBER_OF_SLIDES:
        raise HTTPException(
            status_code=400,
            detail=f"Number of slides cannot be greater than {MAX_NUMBER_OF_SLIDES}",
        )

    if (
        request.include_table_of_contents
        and request.n_slides is not None
        and request.n_slides < 3
    ):
        raise HTTPException(
            status_code=400,
            detail="Number of slides cannot be less than 3 if table of contents is included",
        )

    # Checking if template is valid. The authored mode reuses the template field as a
    # MODE selector (not a real layout template), so it bypasses layout validation.
    if request.template.lower() == AUTHORED_TEMPLATE:
        request.template = AUTHORED_TEMPLATE
    elif request.template not in DEFAULT_TEMPLATES:
        request.template = request.template.lower()
        if not request.template.startswith("custom-"):
            raise HTTPException(
                status_code=400,
                detail="Template not found. Please use a valid template.",
            )
        template_id = request.template.replace("custom-", "")
        try:
            template = await sql_session.get(TemplateModel, uuid.UUID(template_id))
            if not template:
                raise Exception()
        except Exception:
            raise HTTPException(
                status_code=400,
                detail="Template not found. Please use a valid template.",
            )

    return (presentation_id,)


async def generate_presentation_handler(
    request: GeneratePresentationRequest,
    presentation_id: uuid.UUID,
    async_status: Optional[AsyncPresentationGenerationTaskModel],
    export_cookie_header: Optional[str] = None,
    sql_session: AsyncSession = Depends(get_async_session),
):
    try:
        using_slides_markdown = False
        language_to_use = (request.language or "").strip() or None
        additional_context = ""

        if request.slides_markdown:
            using_slides_markdown = True
            request.n_slides = len(request.slides_markdown)

        if not using_slides_markdown:
            # Updating async status
            if async_status:
                async_status.message = "Generating presentation outlines"
                async_status.updated_at = datetime.now()
                sql_session.add(async_status)
                await sql_session.commit()

            if request.files:
                documents_loader = DocumentsLoader(
                    file_paths=request.files,
                    presentation_language=request.language,
                )
                await documents_loader.load_documents()
                documents = documents_loader.documents
                if documents:
                    additional_context = "\n\n".join(documents)

            # Stage A - Knowledge Brief: research rich, grounded substance first,
            # then ground the outline in it. Falls back to documents-only on failure.
            outline_context = additional_context
            try:
                content_brief = await generate_content_brief(
                    request.content,
                    language_to_use,
                    additional_context,
                    request.tone.value,
                    request.verbosity.value,
                    request.instructions,
                )
                brief_context = content_brief.to_prompt_context()
                outline_context = (
                    f"{additional_context}\n\n{brief_context}".strip()
                    if additional_context
                    else brief_context
                )
            except Exception:
                traceback.print_exc()

            # Finding number of slides to generate by considering table of contents
            n_slides_to_generate = request.n_slides
            if request.include_table_of_contents and request.n_slides is not None:
                n_slides_to_generate = (
                    get_no_of_outlines_to_generate_for_n_slides(
                        n_slides=request.n_slides,
                        toc=True,
                        title_slide=request.include_title_slide,
                    )
                )

            outline_messages = get_outline_messages(
                request.content,
                n_slides_to_generate,
                language_to_use,
                outline_context,
                request.tone.value,
                request.verbosity.value,
                request.instructions,
                request.include_title_slide,
                request.include_table_of_contents,
            )
            await MEM0_PRESENTATION_MEMORY_SERVICE.store_generation_context(
                presentation_id=presentation_id,
                system_prompt=(
                    message_content_to_text(outline_messages[0].content)
                    if len(outline_messages) > 0
                    else None
                ),
                user_prompt=(
                    message_content_to_text(outline_messages[1].content)
                    if len(outline_messages) > 1
                    else None
                ),
                extracted_document_text=additional_context,
                source_content=request.content,
                instructions=request.instructions,
            )

            presentation_outlines_text = ""
            async for chunk in generate_ppt_outline(
                request.content,
                n_slides_to_generate,
                language_to_use,
                outline_context,
                request.tone.value,
                request.verbosity.value,
                request.instructions,
                request.include_title_slide,
                request.web_search,
                request.include_table_of_contents,
            ):

                if isinstance(chunk, HTTPException):
                    raise chunk

                presentation_outlines_text += chunk

            try:
                presentation_outlines_json = dict(
                    dirtyjson.loads(presentation_outlines_text)
                )
            except Exception:
                traceback.print_exc()
                raise HTTPException(
                    status_code=400,
                    detail="Failed to generate presentation outlines. Please try again.",
                )
            presentation_outlines = PresentationOutlineModel(
                **presentation_outlines_json
            )

            if (
                n_slides_to_generate is not None
                and len(presentation_outlines.slides) != n_slides_to_generate
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Failed to generate presentation outlines with requested "
                        "number of slides. Please try again."
                    ),
                )

            total_outlines = len(presentation_outlines.slides)

        else:
            # Setting outlines to slides markdown
            presentation_outlines = PresentationOutlineModel(
                slides=[
                    SlideOutlineModel(content=slide)
                    for slide in request.slides_markdown
                ]
            )
            total_outlines = len(request.slides_markdown)

            await MEM0_PRESENTATION_MEMORY_SERVICE.store_generation_context(
                presentation_id=presentation_id,
                system_prompt=None,
                user_prompt=None,
                extracted_document_text=None,
                source_content=request.content,
                instructions=request.instructions,
            )

        await MEM0_PRESENTATION_MEMORY_SERVICE.store_generated_outlines(
            presentation_id,
            presentation_outlines.model_dump(mode="json"),
        )

        # Authored mode (opt-in via template="authored"): the model AUTHORS bespoke
        # HTML per slide instead of filling a React archetype. Self-contained pipeline
        # (author -> render -> optional vision-QA -> image PPTX/PDF -> persist) that
        # bypasses the compose/structure/content/export path. Default path untouched.
        if request.template == AUTHORED_TEMPLATE:
            if async_status:
                async_status.message = "Authoring bespoke slides"
                async_status.updated_at = datetime.now()
                sql_session.add(async_status)
                await sql_session.commit()
            response = await generate_authored_presentation(
                request,
                presentation_id,
                presentation_outlines,
                language_to_use,
                sql_session,
            )
            if async_status:
                async_status.message = "Presentation generation completed"
                async_status.status = "completed"
                async_status.data = response.model_dump(mode="json")
                async_status.updated_at = datetime.now()
                sql_session.add(async_status)
                await sql_session.commit()
            if request.trigger_webhook:
                CONCURRENT_SERVICE.run_task(
                    None,
                    WebhookService.send_webhook,
                    WebhookEvent.PRESENTATION_GENERATION_COMPLETED,
                    response.model_dump(mode="json"),
                )
            return response

        # Updating async status
        if async_status:
            async_status.message = "Selecting layout for each slide"
            async_status.updated_at = datetime.now()
            sql_session.add(async_status)
            await sql_session.commit()

        print("-" * 40)
        print(f"Generated {total_outlines} outlines for the presentation")

        logger.info(
            "[presentation.generate] loading layout template=%r presentation_id=%s",
            request.template,
            presentation_id,
        )
        layout_model = await get_layout_by_name(request.template)
        logger.info(
            "[presentation.generate] layout ready template=%r slides=%d ordered=%s icon_weight=%s",
            request.template,
            len(layout_model.slides),
            layout_model.ordered,
            layout_model.icon_weight,
        )
        total_slide_layouts = len(layout_model.slides)

        # Adaptive path: compose one SlideSpec per outline slide and project to the
        # (outline, structure) pair the rest of the handler consumes — mirrors the
        # interactive /prepare branch. No per-slide content LLM call, no TOC/fit.
        adaptive_composition = None
        vqa_source_outline = presentation_outlines  # descriptive outline, before projection
        if layout_model.name == "adaptive":
            adaptive_composition, presentation_outlines, presentation_structure = (
                await compose_and_project(
                    presentation_outlines,
                    layout_model,
                    language=language_to_use,
                    tone=request.tone.value,
                    verbosity=request.verbosity.value,
                    instructions=request.instructions,
                )
            )
        else:
            presentation_outlines, presentation_structure = (
                await build_template_structure(
                    presentation_outlines,
                    layout_model,
                    instructions=request.instructions,
                    using_slides_markdown=using_slides_markdown,
                    include_table_of_contents=request.include_table_of_contents,
                    include_title_slide=request.include_title_slide,
                    target_n_slides=request.n_slides,
                )
            )

        final_n_slides = len(presentation_outlines.slides)

        # Create PresentationModel
        presentation = PresentationModel(
            id=presentation_id,
            content=request.content,
            n_slides=final_n_slides,
            language=language_to_use or "",
            # Use the descriptive outline (pre-projection): on the adaptive path
            # presentation_outlines was replaced with the projected SlideSpec outline
            # whose first slide's content is the composition JSON, which would leak
            # into the title. vqa_source_outline is the descriptive outline (== the
            # same object on the template path, so this is correct for both).
            title=get_presentation_title_from_presentation_outline(
                vqa_source_outline
            ),
            outlines=presentation_outlines.model_dump(),
            layout=layout_model.model_dump(),
            structure=presentation_structure.model_dump(),
            tone=request.tone.value,
            verbosity=request.verbosity.value,
            instructions=request.instructions,
            # Authored decks return before this point, so this handler only ever
            # persists adaptive (composition present) or template decks.
            mode="adaptive" if adaptive_composition else "template",
            deck_plan=(
                adaptive_composition.model_dump(mode="json")
                if adaptive_composition
                else None
            ),
        )

        # Updating async status
        if async_status:
            async_status.message = "Generating slides"
            async_status.updated_at = datetime.now()
            sql_session.add(async_status)
            await sql_session.commit()

        image_generation_service = ImageGenerationService(get_images_directory())
        async_assets_generation_tasks = []

        # 7. Build slides (adaptive: directly from the composition; legacy: batched
        # per-slide content generation), then fetch assets.
        slides: List[SlideModel] = []
        slide_layouts = [layout_model.slides[idx] for idx in presentation_structure.slides]

        if adaptive_composition is not None:
            # Adaptive: use the persisted SlideSpec directly (no per-slide LLM call).
            # spec_to_blocks → SlideModel.content, speaker_note from the spec; still
            # fetch image/icon assets per slide (markers handled depth-agnostically).
            for i, spec in enumerate(adaptive_composition.slides):
                slide = SlideModel(
                    presentation=presentation_id,
                    layout_group=layout_model.name,
                    layout=slide_layouts[i].id,
                    index=i,
                    speaker_note=spec.speaker_note or "",
                    content=spec_to_blocks(spec),
                )
                slides.append(slide)
                async_assets_generation_tasks.append(
                    asyncio.create_task(
                        process_slide_and_fetch_assets(
                            image_generation_service,
                            slide,
                            outline_image_urls=None,
                            icon_weight=layout_model.icon_weight,
                        )
                    )
                )
        else:
            # Schedule slide content generation and asset fetching in batches of 10
            batch_size = 10
            for start in range(0, len(slide_layouts), batch_size):
                end = min(start + batch_size, len(slide_layouts))

                print(f"Generating slides from {start} to {end}")

                # Generate contents for this batch concurrently
                content_tasks = [
                    get_slide_content_from_type_and_outline(
                        slide_layouts[i],
                        presentation_outlines.slides[i],
                        language_to_use,
                        request.tone.value,
                        request.verbosity.value,
                        request.instructions,
                    )
                    for i in range(start, end)
                ]
                batch_contents: List[dict] = await asyncio.gather(*content_tasks)

                # Build slides for this batch
                batch_slides: List[SlideModel] = []
                for offset, slide_content in enumerate(batch_contents):
                    i = start + offset
                    slide_layout = slide_layouts[i]
                    slide = SlideModel(
                        presentation=presentation_id,
                        layout_group=layout_model.name,
                        layout=slide_layout.id,
                        index=i,
                        speaker_note=slide_content.get("__speaker_note__"),
                        content=slide_content,
                    )
                    slides.append(slide)
                    batch_slides.append(slide)

                if using_slides_markdown:
                    image_urls_for_batch = get_images_for_slides_from_outline(
                        presentation_outlines.slides[start:end]
                    )
                else:
                    image_urls_for_batch = [[] for _ in batch_slides]

                # Start asset fetch tasks immediately so they run in parallel with next batch's LLM calls
                asset_tasks = [
                    asyncio.create_task(
                        process_slide_and_fetch_assets(
                            image_generation_service,
                            slide,
                            outline_image_urls=image_urls_for_batch[offset],
                            icon_weight=layout_model.icon_weight,
                        )
                    )
                    for offset, slide in enumerate(batch_slides)
                ]
                async_assets_generation_tasks.extend(asset_tasks)

        if async_status:
            async_status.message = "Fetching assets for slides"
            async_status.updated_at = datetime.now()
            sql_session.add(async_status)
            await sql_session.commit()

        # Run all asset tasks concurrently while batches may still be generating content
        generated_assets_list = await asyncio.gather(*async_assets_generation_tasks)
        generated_assets = []
        for assets_list in generated_assets_list:
            generated_assets.extend(assets_list)

        # 8. Save PresentationModel and Slides
        sql_session.add(presentation)
        sql_session.add_all(slides)
        sql_session.add_all(generated_assets)
        await sql_session.commit()

        # 8b. Optional vision-QA pass (opt-in via request.vision_qa, adaptive only):
        # render + critique each slide and re-compose any the model flags as broken.
        # Best-effort and bounded — wrapped so it can never break or slow the default
        # (vision_qa=false) path.
        if getattr(request, "vision_qa", False) and adaptive_composition is not None:
            try:
                from utils.llm_calls.vision_qa import run_vision_qa_pass

                # Vision-QA renders /pdf-maker to critique slides. Fall back to the
                # same NEXT_INTERNAL_URL used for template layout resolution (honours
                # the local single-origin proxy) instead of a bare :80, so the
                # opt-in QA pass doesn't silently no-op on setups without nginx.
                vqa_base = (
                    os.getenv("NEXT_PUBLIC_URL") or ""
                ).strip() or get_next_internal_base_url()
                adaptive_composition, fixed_idx = await run_vision_qa_pass(
                    str(presentation_id),
                    vqa_source_outline,
                    adaptive_composition,
                    language=language_to_use,
                    tone=request.tone.value,
                    verbosity=request.verbosity.value,
                    base_url=vqa_base,
                    instructions=request.instructions,
                )
                print(
                    f"Vision-QA pass complete: re-composed {len(fixed_idx)} slide(s): "
                    f"{fixed_idx}"
                )
                if fixed_idx:
                    by_index = {s.index: s for s in slides}
                    for i in fixed_idx:
                        spec = adaptive_composition.slides[i]
                        sm = by_index.get(i)
                        if sm is not None:
                            sm.content = spec_to_blocks(spec)
                            sm.layout = archetype_to_layout_id(spec.archetype)
                            sm.speaker_note = spec.speaker_note or ""
                    sql_session.add_all(slides)
                    await sql_session.commit()
                    print(f"Vision-QA fixed slides: {fixed_idx}")
            except Exception:
                traceback.print_exc()

        if async_status:
            async_status.message = "Exporting presentation"
            async_status.updated_at = datetime.now()
            sql_session.add(async_status)

        # 9. Export
        presentation_and_path = await export_presentation(
            presentation_id,
            presentation.title or str(uuid.uuid4()),
            request.export_as,
            cookie_header=export_cookie_header,
        )

        response = PresentationPathAndEditPath(
            **presentation_and_path.model_dump(),
            edit_path=f"/presentation?id={presentation_id}",
        )

        if async_status:
            async_status.message = "Presentation generation completed"
            async_status.status = "completed"
            async_status.data = response.model_dump(mode="json")
            async_status.updated_at = datetime.now()
            sql_session.add(async_status)
            await sql_session.commit()

        # Triggering webhook on success (opt-in via request.trigger_webhook)
        if request.trigger_webhook:
            CONCURRENT_SERVICE.run_task(
                None,
                WebhookService.send_webhook,
                WebhookEvent.PRESENTATION_GENERATION_COMPLETED,
                response.model_dump(mode="json"),
            )

        return response

    except Exception as e:
        if not isinstance(e, HTTPException):
            traceback.print_exc()
            e = HTTPException(status_code=500, detail="Presentation generation failed")

        api_error_model = APIErrorModel.from_exception(e)

        # Triggering webhook on failure (opt-in via request.trigger_webhook)
        if request.trigger_webhook:
            CONCURRENT_SERVICE.run_task(
                None,
                WebhookService.send_webhook,
                WebhookEvent.PRESENTATION_GENERATION_FAILED,
                api_error_model.model_dump(mode="json"),
            )

        if async_status:
            async_status.status = "error"
            async_status.message = "Presentation generation failed"
            async_status.updated_at = datetime.now()
            async_status.error = api_error_model.model_dump(mode="json")
            sql_session.add(async_status)
            await sql_session.commit()

        else:
            raise e


@PRESENTATION_GENERATE_ROUTER.post(
    "/generate", response_model=PresentationPathAndEditPath
)
async def generate_presentation_sync(
    request_http: Request,
    request: GeneratePresentationRequest,
    sql_session: AsyncSession = Depends(get_async_session),
):
    try:
        (presentation_id,) = await check_if_api_request_is_valid(request, sql_session)
        return await generate_presentation_handler(
            request,
            presentation_id,
            None,
            export_cookie_header=build_export_cookie_header(request_http),
            sql_session=sql_session,
        )
    except HTTPException:
        raise
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail="Presentation generation failed")


async def _run_async_generation(
    request: GeneratePresentationRequest,
    presentation_id: uuid.UUID,
    async_status_id: str,
    export_cookie_header: Optional[str],
):
    """Background entry for /generate/async. A FastAPI BackgroundTask runs AFTER
    the request's yield-dependency session is torn down, so generation must not
    borrow that session — open a fresh one and re-fetch the status row by id."""
    async with async_session_maker() as session:
        async_status = await session.get(
            AsyncPresentationGenerationTaskModel, async_status_id
        )
        await generate_presentation_handler(
            request,
            presentation_id,
            async_status=async_status,
            export_cookie_header=export_cookie_header,
            sql_session=session,
        )


async def queue_presentation_generation(
    request: GeneratePresentationRequest,
    background_tasks: BackgroundTasks,
    sql_session: AsyncSession,
    export_cookie_header: Optional[str] = None,
) -> AsyncPresentationGenerationTaskModel:
    """Validate and enqueue one presentation generation job."""
    (presentation_id,) = await check_if_api_request_is_valid(request, sql_session)

    async_status = AsyncPresentationGenerationTaskModel(
        status="pending",
        message="Queued for generation",
        data=None,
    )
    sql_session.add(async_status)
    await sql_session.commit()

    background_tasks.add_task(
        _run_async_generation,
        request,
        presentation_id,
        async_status.id,
        export_cookie_header,
    )
    return async_status


@PRESENTATION_GENERATE_ROUTER.post(
    "/generate/async", response_model=AsyncPresentationGenerationTaskModel
)
async def generate_presentation_async(
    request_http: Request,
    request: GeneratePresentationRequest,
    background_tasks: BackgroundTasks,
    sql_session: AsyncSession = Depends(get_async_session),
):
    try:
        return await queue_presentation_generation(
            request,
            background_tasks,
            sql_session,
            export_cookie_header=build_export_cookie_header(request_http),
        )

    except Exception as e:
        if not isinstance(e, HTTPException):
            print(e)
            e = HTTPException(status_code=500, detail="Presentation generation failed")

        raise e


@PRESENTATION_GENERATE_ROUTER.get(
    "/status/{id}", response_model=AsyncPresentationGenerationTaskModel
)
async def check_async_presentation_generation_status(
    id: str = Path(description="ID of the presentation generation task"),
    sql_session: AsyncSession = Depends(get_async_session),
):
    status = await sql_session.get(AsyncPresentationGenerationTaskModel, id)
    if not status:
        raise HTTPException(
            status_code=404, detail="No presentation generation task found"
        )
    return status
