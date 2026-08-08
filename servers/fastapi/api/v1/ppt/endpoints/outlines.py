import asyncio
import json
import logging
import os
import traceback
import uuid
import dirtyjson
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from models.presentation_outline_model import PresentationOutlineModel
from models.sse_response import (
    SSECompleteResponse,
    SSEErrorResponse,
    SSEResponse,
    SSEStatusResponse,
)
from services.temp_file_service import TEMP_FILE_SERVICE
from services.database import get_async_session
from services.documents_loader import DocumentsLoader
from services.mem0_presentation_memory_service import (
    MEM0_PRESENTATION_MEMORY_SERVICE,
)
from services.presentation_access_service import require_live_presentation
from services.workspace_legacy_write_gate import (
    require_outline_bootstrap_write_path,
)
from utils.document_context import (
    DocumentContextLimitError,
    build_document_context,
)
from utils.llm_utils import message_content_to_text
from utils.outline_utils import (
    get_no_of_outlines_to_generate_for_n_slides,
    get_presentation_title_from_presentation_outline,
)
from utils.llm_calls.generate_content_brief import generate_content_brief
from utils.llm_calls.generate_presentation_outlines import (
    generate_ppt_outline,
    get_messages as get_outline_messages,
)

OUTLINES_ROUTER = APIRouter(prefix="/outlines", tags=["Outlines"])
LOGGER = logging.getLogger(__name__)
CONTENT_BRIEF_TIMEOUT_SECONDS = float(
    os.getenv("CONTENT_BRIEF_TIMEOUT_SECONDS", "45")
)


@OUTLINES_ROUTER.get("/stream/{id}")
async def stream_outlines(
    id: uuid.UUID,
    request: Request,
    sql_session: AsyncSession = Depends(get_async_session),
):
    # This GET endpoint persists its result. During the chat-first migration it
    # may write only to the owner-scoped staging deck whose generation
    # operation is still awaiting activation.
    #
    # That decision needs the deck, so it cannot be pre-empted by the
    # scope preflight: for a fully enabled cohort the preflight rejects before
    # any database access, which would make the bootstrap allowance below
    # unreachable and leave a staged deck with no way to generate its outline.
    # `require_outline_bootstrap_write_path` performs the same rollout
    # evaluation and still re-raises unsupported/unavailable runtimes.
    presentation = await require_live_presentation(sql_session, id)
    await require_outline_bootstrap_write_path(sql_session, presentation)

    temp_dir = TEMP_FILE_SERVICE.create_temp_dir()

    async def inner():
        if await request.is_disconnected():
            return

        yield SSEStatusResponse(
            status="Generating presentation outlines..."
        ).to_string()

        # EventSource reconnects automatically when a proxy/server connection is
        # interrupted. If this presentation already finished, replay the persisted
        # completion instead of starting a second expensive LLM generation. This
        # also lets a client that missed the original "complete" event recover
        # immediately from a progress screen parked at 95%.
        if presentation.outlines:
            yield SSECompleteResponse(
                key="presentation", value=presentation.model_dump(mode="json")
            ).to_string()
            return

        additional_context = ""
        if presentation.file_paths:
            try:
                documents_loader = DocumentsLoader(
                    file_paths=presentation.file_paths,
                    presentation_language=presentation.language,
                    allow_context_truncation=True,
                )
                await documents_loader.load_documents(temp_dir)
                additional_context = build_document_context(
                    documents_loader.documents,
                    presentation.file_paths,
                    overflow_strategy="truncate",
                )
            except DocumentContextLimitError as error:
                yield SSEErrorResponse(detail=error.to_sse_detail()).to_string()
                return

        # Stage A - Knowledge Brief: research rich, grounded substance first,
        # then ground the outline in it. Falls back to documents-only on failure.
        yield SSEStatusResponse(
            status="Researching and organizing content..."
        ).to_string()
        outline_context = additional_context
        try:
            content_brief = await asyncio.wait_for(
                generate_content_brief(
                    presentation.content,
                    presentation.language,
                    additional_context,
                    presentation.tone,
                    presentation.verbosity,
                    presentation.instructions,
                ),
                timeout=CONTENT_BRIEF_TIMEOUT_SECONDS,
            )
            brief_context = content_brief.to_prompt_context()
            outline_context = (
                f"{additional_context}\n\n{brief_context}".strip()
                if additional_context
                else brief_context
            )
        except HTTPException as error:
            if error.status_code == 429:
                yield SSEErrorResponse(detail=str(error.detail)).to_string()
                return
            traceback.print_exc()
        except asyncio.TimeoutError:
            LOGGER.warning(
                "Content brief generation timed out after %.1f seconds for "
                "presentation %s; continuing with the source content",
                CONTENT_BRIEF_TIMEOUT_SECONDS,
                presentation.id,
            )
        except Exception:
            traceback.print_exc()

        presentation_outlines_text = ""

        if presentation.n_slides > 0:
            n_slides_to_generate = get_no_of_outlines_to_generate_for_n_slides(
                n_slides=presentation.n_slides,
                toc=presentation.include_table_of_contents,
                title_slide=presentation.include_title_slide,
            )
        else:
            n_slides_to_generate = None

        outline_messages = get_outline_messages(
            presentation.content,
            n_slides_to_generate,
            presentation.language,
            outline_context,
            presentation.tone,
            presentation.verbosity,
            presentation.instructions,
            presentation.include_title_slide,
            presentation.include_table_of_contents,
        )
        await MEM0_PRESENTATION_MEMORY_SERVICE.store_generation_context(
            presentation_id=presentation.id,
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
            source_content=presentation.content,
            instructions=presentation.instructions,
        )

        async for chunk in generate_ppt_outline(
            presentation.content,
            n_slides_to_generate,
            presentation.language,
            outline_context,
            presentation.tone,
            presentation.verbosity,
            presentation.instructions,
            presentation.include_title_slide,
            presentation.web_search,
            presentation.include_table_of_contents,
            disconnect_checker=request.is_disconnected,
        ):
            # Give control to the event loop
            await asyncio.sleep(0)

            if isinstance(chunk, HTTPException):
                yield SSEErrorResponse(detail=chunk.detail).to_string()
                return

            yield SSEResponse(
                event="response",
                data=json.dumps({"type": "chunk", "chunk": chunk}),
            ).to_string()

            presentation_outlines_text += chunk

        try:
            presentation_outlines_json = dict(
                dirtyjson.loads(presentation_outlines_text)
            )
        except Exception as e:
            traceback.print_exc()
            yield SSEErrorResponse(
                detail=f"Failed to generate presentation outlines. Please try again. {str(e)}",
            ).to_string()
            return

        presentation_outlines = PresentationOutlineModel(**presentation_outlines_json)

        if (
            n_slides_to_generate is not None
            and len(presentation_outlines.slides) != n_slides_to_generate
        ):
            yield SSEErrorResponse(
                detail=(
                    "Failed to generate presentation outlines with requested "
                    "number of slides. Please try again."
                )
            ).to_string()
            return

        if n_slides_to_generate is not None:
            presentation_outlines.slides = presentation_outlines.slides[
                :n_slides_to_generate
            ]

        if presentation.n_slides <= 0:
            presentation.n_slides = len(presentation_outlines.slides)

        presentation.outlines = presentation_outlines.model_dump()
        presentation.title = get_presentation_title_from_presentation_outline(
            presentation_outlines
        )

        sql_session.add(presentation)
        await sql_session.commit()

        await MEM0_PRESENTATION_MEMORY_SERVICE.store_generated_outlines(
            presentation.id,
            presentation.outlines,
        )

        yield SSECompleteResponse(
            key="presentation", value=presentation.model_dump(mode="json")
        ).to_string()

    return StreamingResponse(inner(), media_type="text/event-stream")
