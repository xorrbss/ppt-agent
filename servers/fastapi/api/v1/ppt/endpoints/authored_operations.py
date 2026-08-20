"""Authored-only operations retained after the V1 editor surface removal."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from api.v1.ppt.endpoints.presentation_helpers import build_export_cookie_header
from models.authored_vnext.deck import DeckMetadataSnapshot
from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_from_template import (
    AuthoredPageRemakePreviewRequest,
    AuthoredPageRemakePublishRequest,
    AuthoredQualityReviewRequest,
    RetemplatePresentationRequest,
)
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.slide import SlideModel
from services.authored_exact_source_snapshot import (
    ExactSourceSnapshotError,
    create_exact_source_snapshot,
)
from services.authored_page_remake_history import list_authored_page_remake_previews
from services.authored_page_remake_service import (
    AuthoredPageRemakeError,
    publish_authored_page_remake_legacy,
    queue_authored_page_remake_preview,
    remake_http_error,
)
from services.authored_quality_ocr import get_ocr_readiness
from services.authored_quality_review_service import queue_authored_quality_review
from services.database import get_async_session
from services.presentation_access_service import require_live_presentation
from services.presentation_export_attempt_service import sha256_canonical_json
from services.workspace_legacy_write_gate import require_legacy_presentation_write_path
from utils.authored_styles import load_authored_styles, validate_exact_reconstruction_style
from utils.llm_calls.author_deck import plan_deck_roles
from utils.llm_calls.author_slide import EXACT_RECONSTRUCTION, normalize_reference_mode


AUTHORED_OPERATIONS_ROUTER = APIRouter(prefix="/presentation", tags=["Presentation"])


@AUTHORED_OPERATIONS_ROUTER.get("/quality-review/readiness")
async def authored_quality_review_readiness():
    return await get_ocr_readiness()


async def build_authored_retemplate_generation_request(
    presentation_id: uuid.UUID,
    request: RetemplatePresentationRequest,
    sql_session: AsyncSession,
) -> GeneratePresentationRequest:
    """Build a new authored request from a saved authored semantic manuscript."""

    source = await require_live_presentation(sql_session, presentation_id)
    source_slides = (
        await sql_session.execute(
            select(SlideModel)
            .where(SlideModel.presentation == presentation_id)
            .order_by(SlideModel.index)
        )
    ).scalars().all()
    if not source.is_authored() and not any(slide.is_authored() for slide in source_slides):
        raise HTTPException(400, "Only AI-authored presentations can change authored templates")

    authored_style = request.authored_style.strip()
    selected_style = {style.id: style for style in load_authored_styles()}.get(authored_style)
    if not authored_style or selected_style is None:
        raise HTTPException(400, "AI-authored template not found")
    source_outline = source.get_presentation_outline()
    if not source_outline or not source_outline.slides or any(
        not slide.content.strip() for slide in source_outline.slides
    ):
        raise HTTPException(400, "The authored presentation has no reusable semantic content")

    exact_snapshot_id = None
    if normalize_reference_mode(selected_style.reference_mode) == EXACT_RECONSTRUCTION:
        try:
            validate_exact_reconstruction_style(
                selected_style,
                slide_count=len(source_outline.slides),
                roles=plan_deck_roles(source_outline),
            )
            exact_snapshot_id = await create_exact_source_snapshot(
                selected_style.id,
                source_slides,
                expected_slide_count=len(source_outline.slides),
            )
        except (ExactSourceSnapshotError, ValueError) as error:
            raise HTTPException(422, "authored_exact_source_snapshot_invalid") from error

    payload: dict[str, object] = {
        "content": source.content or source.title or source_outline.slides[0].content,
        "slides_markdown": [slide.content for slide in source_outline.slides],
        "n_slides": len(source_outline.slides),
        "instructions": source.instructions,
        "language": source.language,
        "template": "authored",
        "include_table_of_contents": False,
        "include_title_slide": False,
        "export_as": "pptx",
        "vision_qa": request.vision_qa,
        "authored_style": authored_style,
        "master_pin": request.master_pin,
        "reference_snapshot_ids": [
            uuid.UUID(str(snapshot_id))
            for snapshot_id in (source.reference_snapshot_ids or [])
        ],
    }
    if source.tone:
        payload["tone"] = source.tone
    if source.verbosity:
        payload["verbosity"] = source.verbosity
    generation_request = GeneratePresentationRequest(**payload)
    generation_request._authored_exact_snapshot_id = exact_snapshot_id
    if len(source_slides) != len(source_outline.slides) or [
        slide.index for slide in source_slides
    ] != list(range(len(source_outline.slides))):
        raise HTTPException(409, "authored_source_semantic_snapshot_incomplete")
    semantic_snapshot = {
        "schemaVersion": "authored-source-semantic/v1",
        "sourcePresentationId": str(source.id),
        "sourceRevision": source.revision,
        "slideCount": len(source_outline.slides),
        "referenceSnapshotIds": list(source.reference_snapshot_ids or []),
        "slides": [
            {"index": index, "semanticContent": source_outline.slides[index].content,
             "values": slide.content, "ui": slide.ui, "properties": slide.properties,
             "speakerNote": slide.speaker_note}
            for index, slide in enumerate(source_slides)
        ],
    }
    semantic_snapshot["digest"] = "sha256:" + sha256_canonical_json(semantic_snapshot)
    generation_request._authored_source_semantic_snapshot = semantic_snapshot
    theme = source.theme if isinstance(source.theme, dict) else {}
    authored_projection = theme.get("authored_vnext")
    if not isinstance(authored_projection, dict):
        authored_projection = {}
    raw_metadata = authored_projection.get("deckMetadata", theme.get("deckMetadata"))
    try:
        generation_request._authored_deck_metadata = (
            DeckMetadataSnapshot.model_validate(raw_metadata)
            if isinstance(raw_metadata, dict)
            else DeckMetadataSnapshot()
        )
    except ValidationError as error:
        raise HTTPException(422, "authored_source_deck_metadata_invalid") from error
    return generation_request


@AUTHORED_OPERATIONS_ROUTER.post("/{id}/quality-review", response_model=AsyncPresentationGenerationTaskModel)
async def quality_review_authored_presentation(id: uuid.UUID, request: AuthoredQualityReviewRequest, background_tasks: BackgroundTasks, sql_session: AsyncSession = Depends(get_async_session)):
    require_legacy_presentation_write_path(id)
    await require_live_presentation(sql_session, id)
    return await queue_authored_quality_review(id, request, background_tasks, sql_session)


@AUTHORED_OPERATIONS_ROUTER.post("/{id}/slides/{slide_index}/remake/preview", response_model=AsyncPresentationGenerationTaskModel)
async def preview_authored_page_remake(id: uuid.UUID, slide_index: int, request: AuthoredPageRemakePreviewRequest, background_tasks: BackgroundTasks, sql_session: AsyncSession = Depends(get_async_session)):
    await require_live_presentation(sql_session, id)
    try:
        return await queue_authored_page_remake_preview(id, slide_index, request, background_tasks, sql_session)
    except AuthoredPageRemakeError as error:
        raise remake_http_error(error) from error


@AUTHORED_OPERATIONS_ROUTER.get("/{id}/remake/previews")
async def list_authored_page_remake_preview_history(id: uuid.UUID, sql_session: AsyncSession = Depends(get_async_session)):
    await require_live_presentation(sql_session, id)
    return await list_authored_page_remake_previews(sql_session, id)


@AUTHORED_OPERATIONS_ROUTER.post("/{id}/slides/{slide_index}/remake/publish")
async def publish_authored_page_remake_candidate(id: uuid.UUID, slide_index: int, request: AuthoredPageRemakePublishRequest, sql_session: AsyncSession = Depends(get_async_session)):
    require_legacy_presentation_write_path(id)
    await require_live_presentation(sql_session, id)
    try:
        return await publish_authored_page_remake_legacy(session=sql_session, presentation_id=id, slide_index=slide_index, preview_task_id=request.preview_task_id)
    except AuthoredPageRemakeError as error:
        raise remake_http_error(error) from error


@AUTHORED_OPERATIONS_ROUTER.post("/{id}/retemplate", response_model=AsyncPresentationGenerationTaskModel)
async def retemplate_authored_presentation(id: uuid.UUID, request: RetemplatePresentationRequest, request_http: Request, background_tasks: BackgroundTasks, sql_session: AsyncSession = Depends(get_async_session)):
    from api.v1.ppt.endpoints.presentation_generate import queue_presentation_generation

    require_legacy_presentation_write_path(id)
    generation_request = await build_authored_retemplate_generation_request(id, request, sql_session)
    return await queue_presentation_generation(generation_request, background_tasks, sql_session, export_cookie_header=build_export_cookie_header(request_http))
