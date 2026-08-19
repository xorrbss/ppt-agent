"""Public facade for the authored generation pipeline."""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_and_path import PresentationPathAndEditPath
from models.presentation_outline_model import PresentationOutlineModel
from models.sql.presentation_generation_job import PresentationGenerationJob

from services import authored_presentation_generation as _generation
from services import authored_presentation_artifacts as _artifacts
from services.authored_header_contract_service import resolve_header_contract
from services import authored_presentation_pipeline as _pipeline
from services import authored_presentation_publication as _publication
from services.authored_presentation_artifacts import (
    _authored_export_path,
    _authored_image_set_digest,
    _authored_image_set_relative_directory,
    _build_authored_export,
    _build_image_pdf,
    _cleanup_published_checkpoints,
    _complete_stage_after_external_success,
    _remove_partial_slide_pngs,
    _save_slide_pngs,
    authored_title,
    resolve_brand,
)
from services.authored_presentation_contracts import (
    AUTHORED_TEMPLATE,
    AuthoredGenerationPipelineResult,
)
from services.authored_checkpoint_service import complete_stage
from services.authored_exact_source_snapshot import materialize_exact_source_style
from utils.authored_styles import (
    resolve_authored_style,
    validate_exact_reconstruction_style,
)
from utils.authored_dom_quality import ensure_authored_qids
from utils.llm_calls.author_deck import author_planned_slide, prepare_authored_deck
from utils.llm_calls.author_slide import is_fallback_slide_html
from utils.llm_calls.author_vision_qa import revise_authored_deck
from utils.slide_capture import find_chrome, render_html_to_png


_AUTHORED_JOB_SEMAPHORE = asyncio.Semaphore(
    max(1, int(os.getenv("AUTHORED_JOB_CONCURRENCY", "1")))
)
_AUTHORED_SLIDE_WORKERS = max(
    1, int(os.getenv("AUTHORED_SLIDE_PIPELINE_CONCURRENCY", "8"))
)


def _sync_runtime_dependencies() -> None:
    _pipeline._AUTHORED_SLIDE_WORKERS = _AUTHORED_SLIDE_WORKERS
    _pipeline.author_planned_slide = author_planned_slide
    _pipeline.is_fallback_slide_html = is_fallback_slide_html
    _pipeline.prepare_authored_deck = prepare_authored_deck
    _pipeline.render_html_to_png = render_html_to_png
    _generation._author_illustrate_render_pipeline = _author_illustrate_render_pipeline
    _generation._build_authored_export = _build_authored_export
    _generation._cleanup_published_checkpoints = _cleanup_published_checkpoints
    _generation.find_chrome = find_chrome
    _generation.materialize_exact_source_style = materialize_exact_source_style
    _generation.revise_authored_deck = revise_authored_deck
    _generation.resolve_authored_style = resolve_authored_style
    _generation.resolve_header_contract = resolve_header_contract
    _generation.validate_exact_reconstruction_style = (
        validate_exact_reconstruction_style
    )
    _publication._save_slide_pngs = _save_slide_pngs
    _publication._remove_partial_slide_pngs = _remove_partial_slide_pngs
    _artifacts.complete_stage = complete_stage


async def _author_illustrate_render_pipeline(*args, **kwargs):
    _sync_runtime_dependencies()
    return await _pipeline._author_illustrate_render_pipeline(*args, **kwargs)


async def _generate_authored_presentation(*args, **kwargs):
    _sync_runtime_dependencies()
    return await _generation._generate_authored_presentation(*args, **kwargs)


async def generate_authored_presentation(
    request: GeneratePresentationRequest,
    presentation_id: uuid.UUID,
    outline: PresentationOutlineModel,
    language: Optional[str],
    sql_session: AsyncSession,
    generation_job: PresentationGenerationJob | None = None,
) -> PresentationPathAndEditPath:
    async with _AUTHORED_JOB_SEMAPHORE:
        return await _generate_authored_presentation(
            request,
            presentation_id,
            outline,
            language,
            sql_session,
            generation_job,
        )


__all__ = [
    "AUTHORED_TEMPLATE",
    "AuthoredGenerationPipelineResult",
    "_authored_export_path",
    "_authored_image_set_digest",
    "_authored_image_set_relative_directory",
    "_author_illustrate_render_pipeline",
    "_build_authored_export",
    "_build_image_pdf",
    "_cleanup_published_checkpoints",
    "_complete_stage_after_external_success",
    "_generate_authored_presentation",
    "_remove_partial_slide_pngs",
    "_save_slide_pngs",
    "authored_title",
    "generate_authored_presentation",
    "ensure_authored_qids",
    "complete_stage",
    "is_fallback_slide_html",
    "resolve_brand",
    "resolve_authored_style",
    "resolve_header_contract",
    "validate_exact_reconstruction_style",
    "prepare_authored_deck",
    "materialize_exact_source_style",
]
