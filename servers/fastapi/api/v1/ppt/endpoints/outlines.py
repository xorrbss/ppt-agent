import asyncio
import json
import logging
import re
import traceback
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.content_brief_policy import (
    BRIEF_STATUS_SKIPPED,
    log_content_brief_skipped,
    merge_brief_context,
    run_content_brief_with_policy,
)
from models.sql.workspace import WorkspaceOperation, get_workspace_owner_scope
from services.public_operation_deadline_scope import remaining_public_operation_seconds
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
from services.authored_canvas import presentation_canvas_contract
from services.presentation_access_service import require_live_presentation
from services.references import ReferenceProviderError, admit_reference_snapshots
from services.workspace_legacy_write_gate import (
    require_outline_bootstrap_write_path,
)
from utils.document_context import (
    DocumentContextLimitError,
    build_document_context,
)
from utils.llm_utils import message_content_to_text
from utils.outline_utils import (
    OutlineResponseError,
    explicit_outline_title,
    get_no_of_outlines_to_generate_for_n_slides,
    get_presentation_title_from_presentation_outline,
    clean_deck_title,
    parse_outline_response,
)
from utils.llm_calls.generate_content_brief import generate_content_brief
from utils.llm_calls.generate_presentation_outlines import (
    generate_ppt_outline,
    get_messages as get_outline_messages,
)
from utils.llm_calls.outline_canvas_budget import budget_for_canvas_contract
from utils.outline_citations import strip_outline_citations
from utils.sse_heartbeat import (
    get_sse_heartbeat_interval_seconds,
    with_sse_heartbeat,
)

OUTLINES_ROUTER = APIRouter(prefix="/outlines", tags=["Outlines"])
LOGGER = logging.getLogger(__name__)


async def _outline_common_header_mode(session, presentation) -> str:
    """Read the same staged, owner-scoped generation choice as bootstrap."""
    if not presentation.is_svg():
        return "free"
    operation = await session.scalar(select(WorkspaceOperation).where(
        WorkspaceOperation.presentation_id == presentation.id,
        WorkspaceOperation.owner_scope == get_workspace_owner_scope(),
        WorkspaceOperation.kind == "presentation.generate",
        WorkspaceOperation.state == "awaiting_approval",
        WorkspaceOperation.expected_revision == presentation.revision,
    ))
    settings = (operation.request_payload or {}).get("generation_settings", {}) if operation else {}
    if not isinstance(settings, dict):
        raise ValueError("svg_common_header_mode_invalid")
    mode = settings.get("common_header_mode")
    if mode is None:
        return "free"
    if mode not in {"uniform", "free"}:
        raise ValueError("svg_common_header_mode_invalid")
    return mode


def _retain_outline_header_titles(outline) -> None:
    """Keep titles in Markdown as the UI forwards content-only outlines."""
    for slide in outline.slides:
        title = slide.title
        if not isinstance(title, str) or not title.strip() or "\n" in title or "\r" in title:
            raise ValueError("svg_common_header_title_unresolved")
        headline = explicit_outline_title(slide.content)
        if headline == title:
            continue
        if re.search(r"^\s{0,3}#{1,6}\s+", slide.content, re.MULTILINE):
            raise ValueError("svg_common_header_title_unresolved")
        # Add only the separate display title; every original body character
        # survives the content-only UI handoff unchanged.
        slide.content = f"## {title}\n\n{slide.content}"


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
    common_header_mode = await _outline_common_header_mode(sql_session, presentation)

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
        if presentation.reference_snapshot_ids:
            try:
                snapshot_ids = [
                    uuid.UUID(str(snapshot_id))
                    for snapshot_id in presentation.reference_snapshot_ids
                ]
                snapshots = await admit_reference_snapshots(
                    sql_session,
                    snapshot_ids=snapshot_ids,
                )
                documents: list[str] = []
                document_names: list[str] = []
                for snapshot in snapshots:
                    if snapshot.provider_id != "uploaded_document":
                        raise ReferenceProviderError(
                            "uploaded_reference_snapshot_required",
                            status_code=400,
                        )
                    values = snapshot.payload().get("documents")
                    if not isinstance(values, list):
                        raise ReferenceProviderError(
                            "reference_snapshot_payload_invalid",
                            status_code=409,
                        )
                    for index, value in enumerate(values):
                        if not isinstance(value, dict) or not isinstance(
                            value.get("text"), str
                        ):
                            raise ReferenceProviderError(
                                "reference_snapshot_payload_invalid",
                                status_code=409,
                            )
                        documents.append(value["text"])
                        document_names.append(
                            str(value.get("name") or f"document-{index + 1}")
                        )
                additional_context = build_document_context(
                    documents,
                    document_names,
                    overflow_strategy="truncate",
                )
            except (ReferenceProviderError, ValueError) as error:
                detail = getattr(error, "code", "reference_snapshot_invalid")
                yield SSEErrorResponse(detail=str(detail)).to_string()
                return
            except DocumentContextLimitError as error:
                yield SSEErrorResponse(detail=error.to_sse_detail()).to_string()
                return
        elif presentation.file_paths:
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
        # The deadline scales with the input and times out into one reduced
        # retry (content_brief_policy); `brief_status` travels on the
        # complete event as `briefStatus`.
        outline_context = additional_context
        brief_status = BRIEF_STATUS_SKIPPED
        try:
            brief_outcome = await run_content_brief_with_policy(
                generate_content_brief,
                content=presentation.content,
                language=presentation.language,
                additional_context=additional_context,
                tone=presentation.tone,
                verbosity=presentation.verbosity,
                instructions=presentation.instructions,
                presentation_id=presentation.id,
                # ``None`` outside a public operation, which is the normal case
                # for this stream; under one, the brief takes only its share.
                remaining_operation_seconds=remaining_public_operation_seconds(),
            )
            brief_status = brief_outcome.status
            if brief_outcome.brief is not None:
                outline_context = merge_brief_context(
                    additional_context, brief_outcome.brief
                )
        except HTTPException as error:
            detail = str(error.detail)
            if error.status_code == 429 or (
                "codex oauth access token" in detail.lower()
                and "not set" in detail.lower()
            ):
                yield SSEErrorResponse(detail=detail).to_string()
                return
            log_content_brief_skipped(presentation.id, error)
            traceback.print_exc()
        except Exception as error:
            log_content_brief_skipped(presentation.id, error)
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

        canvas_budget = budget_for_canvas_contract(
            presentation_canvas_contract(presentation.theme)
        )
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
            canvas_budget,
            common_header_mode=common_header_mode,
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
            canvas_budget=canvas_budget,
            common_header_mode=common_header_mode,
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
            presentation_outlines = parse_outline_response(
                presentation_outlines_text, n_slides_to_generate
            )
        except OutlineResponseError as error:
            if error.cause is not None:
                traceback.print_exc()
            detail = error.detail if error.cause is None else f"{error.detail} {error.cause}"
            yield SSEErrorResponse(detail=detail).to_string()
            return

        # With web search on the model cites its sources inline. The URL is what
        # the slide author turns into an `<a href>` the renderer refuses, and it
        # also eats the slide's text budget, so it goes before the outline is
        # stored or shown. The label stays, so the citation still reads as one.
        presentation_outlines = strip_outline_citations(presentation_outlines)
        if common_header_mode == "uniform":
            try:
                _retain_outline_header_titles(presentation_outlines)
            except ValueError as error:
                yield SSEErrorResponse(detail=str(error)).to_string()
                return

        if presentation.n_slides <= 0:
            presentation.n_slides = len(presentation_outlines.slides)

        presentation.outlines = presentation_outlines.model_dump()
        presentation.title = clean_deck_title(
            presentation_outlines.title
        ) or get_presentation_title_from_presentation_outline(presentation_outlines)

        sql_session.add(presentation)
        await sql_session.commit()

        await MEM0_PRESENTATION_MEMORY_SERVICE.store_generated_outlines(
            presentation.id,
            presentation.outlines,
        )

        yield SSEResponse(
            event="response",
            data=json.dumps(
                {
                    "type": "complete",
                    "presentation": presentation.model_dump(mode="json"),
                    "briefStatus": brief_status,
                }
            ),
        ).to_string()

    # The content brief and the wait for the outline call's first token both
    # routinely outlast the 300s idle timeout of the proxies in front of this
    # response, so the whole generator is heartbeated with SSE comment frames.
    return StreamingResponse(
        with_sse_heartbeat(
            inner(), interval_seconds=get_sse_heartbeat_interval_seconds()
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
