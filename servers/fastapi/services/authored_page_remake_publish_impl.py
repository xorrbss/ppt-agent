# ruff: noqa: F401, F821
"""Detached one-page re-author preview and explicit CAS publication.

This service deliberately has no deck/subset request shape. Quality review may
surface this action, but it cannot invoke it: one explicit path-level slide index
is required for both preview and publication.
"""

from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import json
import asyncio
import logging
import time
import uuid
from collections.abc import Mapping
from typing import Any

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.presentation_from_template import AuthoredPageRemakePreviewRequest
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.authored_exact_provenance import AuthoredExactProvenance
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services import presentation_version_service as version_service
from services.authored_exact_provenance import (
    ExactProvenanceError,
    verify_exact_provenance,
)
from services.authored_exact_source_snapshot import (
    ExactSourceSnapshotError,
    materialize_exact_source_style,
)
from services.authored_quality_review_service import (
    _ANALYZE_AND_FIX_LOCKS,
    _render_review_html,
    _saved_brand,
    _saved_role,
)
from services.authored_deck_chapter_plan import load_chapter_plan
from services.authored_deck_illustration_plan import load_illustration_plan
from services.authored_deck_infographic_plan import load_infographic_plan
from services.authored_infographic_assets import (
    build_infographic_asset_context,
    deck_has_picture_source,
)
from services.authored_header_contract_service import (
    load_header_contract,
    page_number_mode_for,
)
from services.authored_presentation_generation import _fit_content_revision
from services.authored_local_fit_policy import html_digest
from services.authored_canvas import presentation_canvas_contract
from services.authored_review_asset_store import publish_reviewed_png
from services.authored_semantic_candidate import (
    compile_authored_semantic_source,
    compile_semantic_components,
)
from services.authored_vnext.semantic.query import get_active_authored_emission
from services.authored_vnext.semantic.remake import (
    republish_authored_semantic_bundle_graph_for_remake,
)
from services.image_generation_service import ImageGenerationService
from services.database import async_session_maker
from services.workspace_authored_async_admission import admit_workspace_authored_task
from services.slidespec_artifact_store import ACTIVE_POINTER_KEY, _json_cas_predicate
from utils.authored_dom_quality import inspect_authored_dom
from utils.asset_directory_utils import get_images_directory
from utils.authored_styles import (
    AuthoredExactReconstructionUnavailableError,
    resolve_authored_style,
)
from utils.datetime_utils import get_current_utc_datetime
from utils.llm_calls.author_deck import prepare_authored_deck
from utils.llm_calls.author_slide import (
    EXACT_RECONSTRUCTION,
    STYLE_REFERENCE,
    authored_semantic_dual_output_mode,
    normalize_reference_mode,
)
from utils.llm_calls.author_vision_qa import revise_authored_deck
from utils.llm_calls.critique_slide import CritiqueIssue, SlideCritique

LOGGER = logging.getLogger(__name__)
PREVIEW_KIND = "authored_page_remake_preview"


async def publish_authored_page_remake(
    *,
    session: AsyncSession,
    presentation_id: uuid.UUID,
    slide_index: int,
    preview_task_id: str,
    snapshot: bool = True,
    operation_id: uuid.UUID | None = None,
    attempt_token: str | None = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Stage one preview publication in the caller-owned transaction."""

    presentation, slides = await _load_deck(session, presentation_id, slide_index)
    if slide_index < 0 or slide_index >= len(slides):
        raise AuthoredPageRemakeError(
            "authored_page_remake_slide_out_of_range", stage="publish_validation"
        )
    slide = slides[slide_index]
    task = await session.get(AsyncPresentationGenerationTaskModel, preview_task_id)
    if task is None:
        raise AuthoredPageRemakeError(
            "authored_page_remake_preview_not_found", stage="publish_validation"
        )
    if slide.is_svg():
        from services.svg_page_remake import publish_svg_candidate

        return await publish_svg_candidate(session, presentation, slides, slide_index, task, snapshot=snapshot,
            operation_id=operation_id, attempt_token=attempt_token, expected_revision=expected_revision)
    data = _validated_preview_data(
        task,
        presentation=presentation,
        slide=slide,
        slide_index=slide_index,
    )
    candidate_html = str(data["candidate_html"])
    candidate_image = str(data["candidate_image"])

    expected_theme = presentation.theme
    policy_revision = _local_fit_policy_revision(expected_theme)
    next_theme: dict[str, Any] | None = None
    # Re-render immediately before mutation. A candidate that can no longer be
    # rendered is never published; deterministic DOM findings stay advisory so an
    # explicitly approved preview remains applicable. Policy-backed decks pass
    # through the same bounded local-fit evidence producer used by generation.
    try:
        canvas = presentation_canvas_contract(
            expected_theme if isinstance(expected_theme, dict) else {}
        )
        if policy_revision is not None:
            fitted = await _fit_content_revision(
                candidate_html,
                slide_index=slide_index,
                policy_revision=policy_revision,
                canvas=canvas,
            )
            if fitted.html != candidate_html:
                raise AuthoredPageRemakeError(
                    "authored_page_remake_candidate_changed_after_preview",
                    stage="post_verify",
                    retryable=True,
                )
            if not _png_matches_content_addressed_reference(
                candidate_image, fitted.png
            ):
                raise AuthoredPageRemakeError(
                    "authored_page_remake_candidate_capture_changed",
                    stage="post_verify",
                    retryable=True,
                )
            _png, metrics = fitted.png, fitted.metrics
            next_theme = _theme_with_remake_local_fit_receipt(
                theme=expected_theme,
                slides=slides,
                slide_index=slide_index,
                candidate_html=candidate_html,
                receipt=fitted.receipt,
            )
        else:
            _png, metrics = await _render_review_html(
                candidate_html, canvas=canvas
            )
    except AuthoredPageRemakeError:
        raise
    except Exception as error:
        raise AuthoredPageRemakeError(
            "authored_page_remake_post_verify_failed",
            stage="post_verify",
            retryable=True,
        ) from error
    plan, _style, reference_mode, _exact_sources, _canvas = await _authoring_context(
        session, presentation, slides
    )
    del plan
    post_report = inspect_authored_dom(
        candidate_html,
        metrics,
        reference_mode=reference_mode,
        viewport_width=canvas.width_px,
        viewport_height=canvas.height_px,
    )
    post_verify_status = "warning" if post_report.needs_attention else "passed"
    if post_verify_status == "warning":
        LOGGER.info(
            "Authored page remake publication carries DOM warnings "
            "(slide_index=%s, telemetry=%s, issues=%d)",
            slide_index,
            post_report.telemetry_status,
            len(post_report.issues),
        )

    expected_html = slide.html_content
    expected_content = slide.content
    expected_properties = slide.properties
    next_properties = (
        dict(expected_properties) if isinstance(expected_properties, dict) else {}
    )
    next_properties.pop(ACTIVE_POINTER_KEY, None)
    next_properties["image"] = candidate_image
    next_content = dict(expected_content) if isinstance(expected_content, dict) else {}
    next_content.update(
        {
            "__authored__": True,
            "image": candidate_image,
        }
    )

    if snapshot:
        # A batch takes one snapshot for the whole transaction instead: per-page
        # snapshots there would evict the deck's real undo history.
        await version_service.snapshot_slides(
            session,
            presentation_id,
            [item.model_dump(mode="json") for item in slides],
            label=f"페이지 다시 만들기: {slide_index + 1}",
            force=True,
        )
    result = await session.execute(
        update(SlideModel)
        .where(
            SlideModel.id == slide.id,
            SlideModel.html_content == expected_html,
            _json_cas_predicate(session, SlideModel.content, expected_content),
            _json_cas_predicate(session, SlideModel.properties, expected_properties),
        )
        .values(
            html_content=candidate_html,
            content=next_content,
            properties=next_properties,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise AuthoredPageRemakeError(
            "authored_page_remake_publish_cas_conflict", stage="publish"
        )
    if next_theme is not None:
        theme_result = await session.execute(
            update(PresentationModel)
            .where(
                PresentationModel.id == presentation.id,
                _json_cas_predicate(session, PresentationModel.theme, expected_theme),
            )
            .values(theme=next_theme)
            .execution_options(synchronize_session=False)
        )
        if theme_result.rowcount != 1:
            raise AuthoredPageRemakeError(
                "authored_page_remake_publish_cas_conflict", stage="publish"
            )
    await session.flush()
    readback = await session.execute(
        select(
            SlideModel.html_content, SlideModel.content, SlideModel.properties
        ).where(SlideModel.id == slide.id)
    )
    row = readback.one_or_none()
    if (
        row is None
        or row.html_content != candidate_html
        or row.content != next_content
        or row.properties != next_properties
    ):
        raise AuthoredPageRemakeError(
            "authored_page_remake_publish_readback_mismatch", stage="publish"
        )
    if next_theme is not None:
        theme_readback = await session.execute(
            select(PresentationModel.theme).where(
                PresentationModel.id == presentation.id
            )
        )
        if theme_readback.scalar_one_or_none() != next_theme:
            raise AuthoredPageRemakeError(
                "authored_page_remake_publish_readback_mismatch", stage="publish"
            )
        await session.refresh(presentation, attribute_names=["theme"])

    next_data = dict(data)
    next_data["phase"] = "published"
    next_data["publish"] = {
        "status": "published",
        "post_verify": post_verify_status,
        "post_verify_telemetry": post_report.telemetry_status,
        "post_verify_issues": _issue_summary(post_report),
        "rollback_available": True,
    }
    task.data = next_data
    task.message = f"{slide_index + 1}페이지의 새 버전을 적용했습니다."
    task.updated_at = get_current_utc_datetime()
    session.add(task)
    await session.flush()
    return {
        "kind": "authored_page_remake",
        "status": "published",
        "presentation_id": str(presentation_id),
        "slide_id": str(slide.id),
        "slide_index": slide_index,
        "preview_task_id": preview_task_id,
        "source_digest": data["source_digest"],
        "candidate_digest": data["candidate_digest"],
        "post_verify_status": post_verify_status,
        "rollback_available": True,
        "canvas_refresh_required": True,
        "canvas_refresh_slide_index": slide_index,
    }

def remake_http_error(error: AuthoredPageRemakeError) -> HTTPException:
    if error.code in {
        "presentation_not_found",
        "authored_page_remake_preview_not_found",
    }:
        status = 404
    elif error.code in {
        "authored_page_remake_source_changed",
        "authored_page_remake_publish_cas_conflict",
    }:
        status = 409
    else:
        status = 400
    return HTTPException(
        status_code=status,
        detail={"code": error.code, "stage": error.stage},
    )

def bind_runtime(runtime) -> None:
    for name, value in vars(runtime).items():
        if (
            name not in {"publish_authored_page_remake", "remake_http_error", "bind_runtime"}
            and not name.startswith("__")
        ):
            globals()[name] = value
