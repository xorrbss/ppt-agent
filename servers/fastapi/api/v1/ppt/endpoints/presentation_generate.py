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
from datetime import datetime, timezone
from time import perf_counter
from typing import List, Optional, Tuple

import dirtyjson
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    HTTPException,
    Path,
    Request,
)

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
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_PUBLISHED,
    PRESENTATION_LIFECYCLE_STAGING,
    PresentationModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
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
from services.presentation_generation_job_service import (
    claim_generation_job,
    finalize_generation_job,
    heartbeat_generation_job_loop,
    idempotency_key_hash,
    release_generation_job,
    request_sha256,
)
from services.template_v2_generation_service import (
    TemplateV2GenerationError,
    build_template_v2_slides,
    load_template_v2_generation_target,
    preflight_template_v2_native_pptx,
    source_content_sha256,
)
from services.template_v2_generation_observability import (
    log_template_v2_generation_observation,
)
from services.webhook_service import WebhookService
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from templates.v2.constants import TEMPLATE_V2_VERSION
from templates.v2.strategies import (
    TEMPLATE_V2_STRATEGIES,
    resolve_presentation_strategies,
)
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


def _template_v2_http_exception(error: TemplateV2GenerationError) -> HTTPException:
    status_by_code = {
        "template_v2_creation_disabled": 403,
        "template_v2_allowlist_required": 403,
        "template_v2_template_not_allowed": 403,
        "template_v2_template_id_required": 400,
        "template_v2_rollout_config_invalid": 503,
        "template_v2_template_not_found": 404,
        "template_v2_revision_conflict": 409,
        "template_v2_snapshot_not_found": 409,
        "template_v2_snapshot_missing": 500,
        "template_v2_source_invalid": 422,
        "template_v2_layouts_invalid": 422,
        "template_v2_fillable_layout_required": 422,
        "template_v2_generation_invalid": 422,
    }
    return HTTPException(
        status_code=status_by_code.get(error.code, 422),
        detail=error.code,
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

    if request.strategy == "template_v2":
        template_v2_id = (request.template_v2_id or "").strip()
        if not template_v2_id:
            raise HTTPException(status_code=400, detail="template_v2_id_required")
        if request.template_v2_revision is None:
            raise HTTPException(status_code=400, detail="template_v2_revision_required")
        if request.template_v2_revision < 1:
            raise HTTPException(status_code=400, detail="template_v2_revision_invalid")
        if request.template.lower() != "adaptive":
            raise HTTPException(status_code=400, detail="generation_strategy_conflict")
        if request.files or request.web_search:
            raise HTTPException(
                status_code=400,
                detail="template_v2_source_mode_not_supported",
            )
        request.template_v2_id = template_v2_id
        try:
            # Validate admission, existence, the pinned revision and its strict
            # fillable schemas before a sync run or async job is started.
            request._template_v2_generation_target = (
                await load_template_v2_generation_target(
                    sql_session,
                    template_id=template_v2_id,
                    revision=request.template_v2_revision,
                )
            )
        except TemplateV2GenerationError as error:
            raise _template_v2_http_exception(error) from error
        return (presentation_id,)

    if request.template_v2_id is not None or request.template_v2_revision is not None:
        raise HTTPException(status_code=400, detail="generation_strategy_conflict")

    if request.strategy == "authored":
        if request.template.lower() not in {"adaptive", AUTHORED_TEMPLATE}:
            raise HTTPException(status_code=400, detail="generation_strategy_conflict")
        request.template = AUTHORED_TEMPLATE
    elif request.strategy == "adaptive":
        if request.template.lower() != "adaptive":
            raise HTTPException(status_code=400, detail="generation_strategy_conflict")
        request.template = "adaptive"
    elif request.strategy == "legacy" and request.template.lower() in {
        "adaptive",
        AUTHORED_TEMPLATE,
    }:
        raise HTTPException(status_code=400, detail="generation_strategy_conflict")

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


async def _generate_template_v2_presentation(
    *,
    request: GeneratePresentationRequest,
    presentation_id: uuid.UUID,
    presentation_outlines: PresentationOutlineModel,
    using_slides_markdown: bool,
    language_to_use: Optional[str],
    async_status: Optional[AsyncPresentationGenerationTaskModel],
    generation_job: Optional[PresentationGenerationJob],
    export_cookie_header: Optional[str],
    sql_session: AsyncSession,
) -> PresentationPathAndEditPath:
    """Execute the explicit, revision-pinned Template V2 generation branch."""

    if request.template_v2_id is None or request.template_v2_revision is None:
        raise HTTPException(status_code=400, detail="template_v2_target_required")

    generation_started_at = perf_counter()
    try:
        target = request._template_v2_generation_target
        if target is None:
            raise TemplateV2GenerationError("template_v2_snapshot_missing")
        if async_status:
            async_status.message = "Selecting Template V2 layouts"
            async_status.updated_at = datetime.now()
            sql_session.add(async_status)
            await sql_session.commit()

        presentation_outlines, presentation_structure = await build_template_structure(
            presentation_outlines,
            target.as_pipeline_layout(),
            instructions=request.instructions,
            using_slides_markdown=using_slides_markdown,
            include_table_of_contents=request.include_table_of_contents,
            include_title_slide=request.include_title_slide,
            target_n_slides=request.n_slides,
        )

        if async_status:
            async_status.message = "Generating Template V2 slides"
            async_status.updated_at = datetime.now()
            sql_session.add(async_status)
            await sql_session.commit()

        slides = await build_template_v2_slides(
            target=target,
            presentation_id=presentation_id,
            outlines=presentation_outlines,
            structure=presentation_structure,
            language=language_to_use,
            tone=request.tone.value,
            verbosity=request.verbosity.value,
            instructions=request.instructions,
        )
        native_pptx_preflight = preflight_template_v2_native_pptx(
            target=target,
            slides=slides,
        )
        created_at = datetime.now(timezone.utc)
        provenance = target.provenance(
            source_sha256=source_content_sha256(
                {
                    "content": request.content,
                    "slides_markdown": request.slides_markdown,
                    "instructions": request.instructions,
                    "language": request.language,
                    "tone": request.tone.value,
                    "verbosity": request.verbosity.value,
                    "n_slides": request.n_slides,
                    "include_table_of_contents": request.include_table_of_contents,
                    "include_title_slide": request.include_title_slide,
                }
            ),
            request_id=str(presentation_id),
            job_id=(
                str(async_status.id)
                if async_status is not None
                else f"sync:{presentation_id}"
            ),
            created_at=created_at,
            native_pptx_preflight=native_pptx_preflight,
        )
        presentation = PresentationModel(
            id=presentation_id,
            content=request.content,
            n_slides=len(slides),
            language=language_to_use or "",
            title=get_presentation_title_from_presentation_outline(
                presentation_outlines
            ),
            file_paths=request.files,
            outlines=presentation_outlines.model_dump(mode="json"),
            layout=None,
            structure=None,
            tone=request.tone.value,
            verbosity=request.verbosity.value,
            instructions=request.instructions,
            include_table_of_contents=request.include_table_of_contents,
            include_title_slide=request.include_title_slide,
            web_search=request.web_search,
            mode="template",
            version=TEMPLATE_V2_VERSION,
            lifecycle_status=PRESENTATION_LIFECYCLE_STAGING,
            theme={
                "mode": "template",
                "template_v2": provenance,
            },
        )
        if resolve_presentation_strategies(presentation, slides) != TEMPLATE_V2_STRATEGIES:
            raise TemplateV2GenerationError("template_v2_generation_invalid")

        sql_session.add(presentation)
        sql_session.add_all(slides)
        if generation_job is not None:
            generation_job.state = "staging"
            generation_job.updated_at = datetime.now(timezone.utc)
            sql_session.add(generation_job)
        await sql_session.commit()
        log_template_v2_generation_observation(
            operation="generate",
            outcome="success",
            template_id=request.template_v2_id,
            template_revision=request.template_v2_revision,
            duration_ms=(perf_counter() - generation_started_at) * 1000,
        )
    except TemplateV2GenerationError as error:
        log_template_v2_generation_observation(
            operation="generate",
            outcome="failure",
            template_id=request.template_v2_id,
            template_revision=request.template_v2_revision,
            duration_ms=(perf_counter() - generation_started_at) * 1000,
            code=error.code,
        )
        raise _template_v2_http_exception(error) from error
    except Exception:
        log_template_v2_generation_observation(
            operation="generate",
            outcome="failure",
            template_id=request.template_v2_id,
            template_revision=request.template_v2_revision,
            duration_ms=(perf_counter() - generation_started_at) * 1000,
            code="template_v2_unexpected_failure",
        )
        raise

    if async_status:
        async_status.message = "Exporting presentation"
        async_status.updated_at = datetime.now()
        sql_session.add(async_status)

    export_started_at = perf_counter()
    try:
        presentation_and_path = await export_presentation(
            presentation_id,
            presentation.title or str(uuid.uuid4()),
            request.export_as,
            cookie_header=export_cookie_header,
        )
        log_template_v2_generation_observation(
            operation="export",
            outcome="success",
            template_id=request.template_v2_id,
            template_revision=request.template_v2_revision,
            duration_ms=(perf_counter() - export_started_at) * 1000,
            export_type=request.export_as,
        )
    except Exception:
        log_template_v2_generation_observation(
            operation="export",
            outcome="failure",
            template_id=request.template_v2_id,
            template_revision=request.template_v2_revision,
            duration_ms=(perf_counter() - export_started_at) * 1000,
            export_type=request.export_as,
            code="template_v2_export_failed",
        )
        # The exporter reads through the committed database state. Compensate
        # immediately so a failed export cannot leave a queryable partial deck.
        # A future durable worker should replace this short visibility window
        # with an explicit staging -> published state transition.
        try:
            await sql_session.rollback()
            await sql_session.execute(
                delete(SlideModel).where(SlideModel.presentation == presentation_id)
            )
            await sql_session.execute(
                delete(PresentationModel).where(PresentationModel.id == presentation_id)
            )
            await sql_session.commit()
        except Exception:
            logger.exception(
                "Failed to compensate Template V2 export for presentation %s",
                presentation_id,
            )
        raise
    response = PresentationPathAndEditPath(
        **presentation_and_path.model_dump(),
        edit_path=f"/presentation?id={presentation_id}",
    )
    presentation.lifecycle_status = PRESENTATION_LIFECYCLE_PUBLISHED
    sql_session.add(presentation)

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


async def generate_presentation_handler(
    request: GeneratePresentationRequest,
    presentation_id: uuid.UUID,
    async_status: Optional[AsyncPresentationGenerationTaskModel],
    export_cookie_header: Optional[str] = None,
    generation_job: Optional[PresentationGenerationJob] = None,
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

        if request.strategy == "template_v2":
            return await _generate_template_v2_presentation(
                request=request,
                presentation_id=presentation_id,
                presentation_outlines=presentation_outlines,
                using_slides_markdown=using_slides_markdown,
                language_to_use=language_to_use,
                async_status=async_status,
                generation_job=generation_job,
                export_cookie_header=export_cookie_header,
                sql_session=sql_session,
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
        try:
            await sql_session.rollback()
        except Exception:
            logger.exception("Failed to roll back presentation generation session")
        if not isinstance(e, HTTPException):
            traceback.print_exc()
            e = HTTPException(status_code=500, detail="Presentation generation failed")

        api_error_model = APIErrorModel.from_exception(
            e, request_id=request._request_id
        )

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
    request._request_id = getattr(request_http.state, "request_id", None)
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


async def run_generation_job(
    job_id: uuid.UUID,
    export_cookie_header: Optional[str],
):
    """Claim a durable job and execute it with a fresh database session.

    The worker uses persisted request state and never persists session cookies.
    """
    claimed = await claim_generation_job(
        job_id,
        allow_auth_resume=export_cookie_header is not None,
    )
    if claimed is None:
        return
    task_id, attempt_token = claimed
    heartbeat = asyncio.create_task(
        heartbeat_generation_job_loop(job_id, attempt_token)
    )
    succeeded = False
    cancelled = False
    try:
        async with async_session_maker() as session:
            job = await session.get(PresentationGenerationJob, job_id)
            async_status = await session.get(
                AsyncPresentationGenerationTaskModel, task_id
            )
            if job is None or async_status is None:
                raise RuntimeError("presentation_generation_job_missing")
            request = GeneratePresentationRequest.model_validate(job.request_payload)
            request._request_id = job.request_id
            if job.template_v2_target is not None:
                from services.template_v2_generation_service import (
                    TemplateV2GenerationTarget,
                )

                request._template_v2_generation_target = (
                    TemplateV2GenerationTarget.from_durable_payload(
                        job.template_v2_target
                    )
                )
            await generate_presentation_handler(
                request,
                job.presentation_id,
                async_status=async_status,
                export_cookie_header=export_cookie_header,
                generation_job=job,
                sql_session=session,
            )
            await session.refresh(async_status)
            succeeded = async_status.status == "completed"
    except asyncio.CancelledError:
        cancelled = True
        raise
    except Exception as error:
        logger.exception("Durable presentation generation job %s failed", job_id)
        async with async_session_maker() as failure_session:
            status = await failure_session.get(
                AsyncPresentationGenerationTaskModel, task_id
            )
            if status is not None and status.status not in {"completed", "error"}:
                status.status = "error"
                status.message = "Presentation generation failed"
                status.error = APIErrorModel.from_exception(error).model_dump(
                    mode="json"
                )
                status.updated_at = datetime.now(timezone.utc)
                failure_session.add(status)
                await failure_session.commit()
    finally:
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)
        if cancelled:
            await release_generation_job(job_id, attempt_token)
        else:
            await finalize_generation_job(
                job_id,
                attempt_token,
                succeeded=succeeded,
            )


async def queue_presentation_generation(
    request: GeneratePresentationRequest,
    background_tasks: BackgroundTasks,
    sql_session: AsyncSession,
    export_cookie_header: Optional[str] = None,
    idempotency_key: Optional[str] = None,
) -> AsyncPresentationGenerationTaskModel:
    """Validate and atomically enqueue one durable, idempotent generation job."""
    submitted_payload = request.model_dump(mode="json")
    fingerprint = request_sha256(submitted_payload)
    try:
        key_hash = (
            idempotency_key_hash(idempotency_key)
            if idempotency_key is not None
            else None
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    if key_hash is not None:
        existing = (
            await sql_session.execute(
                select(PresentationGenerationJob).where(
                    PresentationGenerationJob.idempotency_key_hash == key_hash
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.request_sha256 != fingerprint:
                raise HTTPException(
                    status_code=409,
                    detail="idempotency_key_payload_conflict",
                )
            existing_status = await sql_session.get(
                AsyncPresentationGenerationTaskModel, existing.task_id
            )
            if existing_status is None:
                raise HTTPException(
                    status_code=409,
                    detail="idempotency_record_incomplete",
                )
            if existing.state == "awaiting_resume":
                background_tasks.add_task(
                    run_generation_job,
                    existing.id,
                    export_cookie_header,
                )
            return existing_status

    (presentation_id,) = await check_if_api_request_is_valid(request, sql_session)
    # Validation may normalize legacy template identifiers. Persist the exact
    # admitted request that the worker will execute. The fingerprint remains
    # tied to the caller's canonical submitted payload for stable replay.
    payload = request.model_dump(mode="json")

    async_status = AsyncPresentationGenerationTaskModel(
        status="pending",
        message="Queued for generation",
        data=None,
    )
    target = request._template_v2_generation_target
    job = PresentationGenerationJob(
        task_id=async_status.id,
        presentation_id=presentation_id,
        idempotency_key_hash=key_hash,
        request_id=request._request_id,
        request_sha256=fingerprint,
        request_payload=payload,
        template_v2_target=(
            target.to_durable_payload() if target is not None else None
        ),
        export_cookie_required=export_cookie_header is not None,
        state="queued",
    )
    sql_session.add(async_status)
    sql_session.add(job)
    try:
        await sql_session.commit()
    except IntegrityError:
        await sql_session.rollback()
        if key_hash is None:
            raise
        existing = (
            await sql_session.execute(
                select(PresentationGenerationJob).where(
                    PresentationGenerationJob.idempotency_key_hash == key_hash
                )
            )
        ).scalar_one_or_none()
        if existing is None or existing.request_sha256 != fingerprint:
            raise HTTPException(
                status_code=409,
                detail="idempotency_key_payload_conflict",
            )
        existing_status = await sql_session.get(
            AsyncPresentationGenerationTaskModel, existing.task_id
        )
        if existing_status is None:
            raise HTTPException(
                status_code=409,
                detail="idempotency_record_incomplete",
            )
        return existing_status

    background_tasks.add_task(
        run_generation_job,
        job.id,
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
    idempotency_key: Optional[str] = Header(
        default=None,
        alias="Idempotency-Key",
    ),
    sql_session: AsyncSession = Depends(get_async_session),
):
    request._request_id = getattr(request_http.state, "request_id", None)
    try:
        return await queue_presentation_generation(
            request,
            background_tasks,
            sql_session,
            export_cookie_header=build_export_cookie_header(request_http),
            idempotency_key=idempotency_key,
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
