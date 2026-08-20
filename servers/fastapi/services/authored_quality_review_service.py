"""Post-generation quality review for saved AI-authored presentations.

The review renders the persisted authored HTML, critiques the resulting images in
parallel, and re-authors only the slides with visible defects. The live
deck is checkpointed before any replacement so the operation can be undone through
the existing presentation version history.
"""

# Private facade imports are intentionally re-exported for existing tests and
# extension monkeypatch points while their implementations live in helper modules.
# ruff: noqa: E402, F401

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
import weakref
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.presentation_from_template import AuthoredQualityReviewRequest, ReviewScope
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
from services.authored_quality_acceptance import (
    acceptance_decision as _acceptance_decision,
)
from services.authored_quality_acceptance import (
    critique_score as _critique_score,
)
from services.authored_quality_acceptance import (
    critique_unusable_reason as _critique_unusable_reason,
)
from services.authored_quality_acceptance import (
    is_improved as _is_improved,
)
from services.authored_quality_acceptance import (
    local_acceptance_decision as _local_acceptance_decision,
)
from services.authored_quality_acceptance import (
    style_acceptance_decision as _style_acceptance_decision,
)
from services.authored_quality_acceptance import (
    style_comparison_has_regression as _style_comparison_has_regression,
)
from services.authored_quality_ocr import (
    RequestOcrRuntime,
)
from services.authored_quality_ocr import (
    capture_initial_ocr as _capture_initial_ocr,
)
from services.authored_quality_ocr import (
    evaluate_candidate_ocr as _evaluate_candidate_ocr,
)
from services.authored_quality_ocr import (
    normalize_ocr_check as _normalize_ocr_check,
)
from services.authored_quality_ocr import (
    ocr_language as _ocr_language,
)
from services.authored_quality_ocr import (
    ocr_omissions as _ocr_omissions,
)
from services.authored_quality_ocr import (
    ocr_unavailable_reason as _ocr_unavailable_reason,
)
from services.authored_quality_ocr import (
    safe_ocr_payload as _safe_ocr_payload,
)
from services.authored_quality_results import (
    build_slide_results as _build_slide_results,
)
from services.authored_quality_results import (
    critique_details as _critique_details,
)
from services.authored_quality_routing import (
    hybrid_style_initial_review,
)
from services.authored_quality_routing import (
    render_review_montage as _render_review_montage,
)
from services.authored_quality_telemetry import (
    build_review_event,
)
from services.authored_quality_telemetry import (
    observe_critique_cache as _observe_critique_cache,
)
from services.authored_quality_telemetry import (
    observe_style_comparison_cache as _observe_style_comparison_cache,
)
from services.authored_quality_telemetry import (
    review_observation_metadata as _review_observation_metadata,
)
from services.authored_quality_telemetry import (
    safe_review_model as _safe_review_model,
)
from services.authored_quality_telemetry import (
    style_comparison_observation_metadata as _style_comparison_observation_metadata,
)
from services.authored_review_asset_store import publish_reviewed_png
from services.database import async_session_maker
from services.image_generation_service import ImageGenerationService
from utils.asset_directory_utils import get_images_directory
from utils.authored_dom_quality import (
    DomQualityReport,
    ensure_authored_qids,
    inspect_authored_dom,
)
from utils.authored_ocr_quality import inspect_authored_ocr
from utils.authored_review_routing import plan_authored_review_routing
from utils.authored_styles import (
    AuthoredExactReconstructionUnavailableError,
    resolve_authored_style,
)
from utils.datetime_utils import get_current_utc_datetime
from utils.llm_calls import author_vision_qa
from utils.llm_calls.author_deck import prepare_authored_deck
from utils.llm_calls.author_slide import (
    EXACT_RECONSTRUCTION,
    STYLE_REFERENCE,
    Brand,
    normalize_reference_mode,
)
from utils.llm_calls.author_vision_qa import (
    critique_authored,
    critique_authored_style_comparison,
    revise_authored_deck,
)
from utils.llm_calls.critique_slide import CritiqueIssue, SlideCritique
from utils.slide_capture import (
    get_cached_browser_metrics,
    render_html_to_png,
    render_html_to_png_with_metrics,
)

LOGGER = logging.getLogger(__name__)
_ORIGINAL_RENDER_HTML_TO_PNG = render_html_to_png
#: One mutex per deck. Analyze-fix, page-remake publication, and batch
#: publication all rewrite the same slides, so they take turns. Preview
#: generation deliberately does NOT take it — see the note there.
_ANALYZE_AND_FIX_LOCKS: weakref.WeakValueDictionary[uuid.UUID, asyncio.Lock] = (
    weakref.WeakValueDictionary()
)

_OCR_DOM_CATEGORIES = {"content", "overflow", "clipping", "off_canvas"}
_OCR_DETAIL_SIGNAL = re.compile(
    r"(?:text|title|label|number|numeric|digit|symbol|character|glyph|word|"
    r"missing|omitt|truncat|clip|crop|content\s+loss|"
    r"텍스트|제목|레이블|문자|숫자|기호|누락|생략|잘림|내용)",
    re.IGNORECASE,
)



from services.authored_quality_review_support import (
    _RequestOcrRuntime,
    _execute_sqlalchemy,
    _hybrid_style_initial_review,
    _inspect_ocr_for_review,
    _log_review_event,
    _merge_dom_repair_triggers,
    _ocr_route_signals,
    _render_review_html,
    _resolve_positions,
    _saved_brand,
    _saved_role,
    _serialize_critiques,
    _set_status,
    _write_reviewed_png,
)
from services.authored_quality_review_prepare import prepare_legacy_review
from services.authored_quality_review_clean import finish_clean_legacy_review
from services.authored_quality_review_revise import revise_legacy_review
from services.authored_quality_review_exact import review_exact_candidates
from services.authored_quality_review_style import review_style_candidates
from services.authored_quality_review_finalize import finalize_legacy_review
from services.authored_quality_review_canonical import (
    _canonical_metrics_with_flat_aliases,
    _indexed_canonical_issues,
    _merge_canonical_metric_group,
    _merge_slidespec_review_data,
    _run_authored_quality_review_unlocked,
    _unclaimed_slidespec_review_data,
)

_CANONICAL_METRIC_FLAT_ALIASES: dict[str, dict[str, str]] = {
    "solver": {
        "elapsed_ms": "solver_elapsed_ms",
        "branches": "solver_branches",
        "unknown": "solver_unknown",
        "unsat_core_size": "solver_unsat_core_size",
        "search_complete": "solver_search_complete",
    },
    "candidates": {
        "proposed": "candidate_proposed",
        "rendered": "candidate_rendered",
        "accepted": "candidate_accepted",
        "rejected": "candidate_rejected",
    },
    "transaction": {"rollback_count": "rollback_count"},
    "rollout": {"kill_switch_enabled": "kill_switch_enabled"},
}


class _LegacyReviewContext:
    pass


async def _run_legacy_authored_quality_review_unlocked(
    presentation_id: uuid.UUID,
    request: AuthoredQualityReviewRequest,
    task: AsyncPresentationGenerationTaskModel,
    session: AsyncSession,
) -> None:
    ctx = _LegacyReviewContext()
    ctx.presentation_id = presentation_id
    ctx.request = request
    ctx.task = task
    ctx.session = session
    await prepare_legacy_review(ctx)
    if await finish_clean_legacy_review(ctx):
        return
    await revise_legacy_review(ctx)
    if ctx.exact_reconstruction:
        await review_exact_candidates(ctx)
    else:
        await review_style_candidates(ctx)
    await finalize_legacy_review(ctx)


async def run_authored_quality_review(
    presentation_id: uuid.UUID,
    request: AuthoredQualityReviewRequest,
    task: AsyncPresentationGenerationTaskModel,
    session: AsyncSession,
) -> None:
    """Serialize mutating reviews per deck and CAS each accepted slide at publish."""
    lock = _ANALYZE_AND_FIX_LOCKS.setdefault(presentation_id, asyncio.Lock())
    async with lock:
        await _run_authored_quality_review_unlocked(
            presentation_id, request, task, session
        )


def _public_review_error_detail(exc: Exception) -> str:
    """Return a stable task error without exposing private review metadata."""
    if isinstance(exc, AuthoredExactReconstructionUnavailableError):
        return "authored_exact_reconstruction_unavailable"
    if isinstance(exc, ExactProvenanceError):
        return "authored_exact_reconstruction_provenance_mismatch"
    return "authored_quality_review_failed"


async def _run_quality_review(
    presentation_id: uuid.UUID,
    request: AuthoredQualityReviewRequest,
    task_id: str,
) -> None:
    async with async_session_maker() as session:
        task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
        if task is None:
            return
        try:
            await run_authored_quality_review(presentation_id, request, task, session)
        except Exception as exc:  # noqa: BLE001 - background task boundary
            exact_failure = isinstance(
                exc,
                (
                    AuthoredExactReconstructionUnavailableError,
                    ExactProvenanceError,
                ),
            )
            if exact_failure:
                LOGGER.warning(
                    "Exact reconstruction quality review failed closed (%s)",
                    type(exc).__name__,
                )
            else:
                LOGGER.warning(
                    "Authored quality review failed (%s)",
                    type(exc).__name__,
                )
            await session.rollback()
            await _set_status(
                session,
                task,
                status="error",
                message="怨좏뭹吏?寃??以??ㅻ쪟媛 諛쒖깮?덉뒿?덈떎.",
                error={"detail": _public_review_error_detail(exc)},
            )


async def queue_authored_quality_review(
    presentation_id: uuid.UUID,
    request: AuthoredQualityReviewRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession,
) -> AsyncPresentationGenerationTaskModel:
    presentation = await session.get(PresentationModel, presentation_id)
    if presentation is None:
        raise HTTPException(status_code=404, detail="Presentation not found")

    slide_rows = await _execute_sqlalchemy(
        session,
        select(SlideModel)
        .where(SlideModel.presentation == presentation_id)
        .order_by(SlideModel.index),
    )
    slides = list(slide_rows.scalars().all())
    if (
        not presentation.is_authored()
        or not slides
        or not all(slide.is_authored() for slide in slides)
    ):
        raise HTTPException(
            status_code=400,
            detail="High-quality review supports AI-authored presentations only",
        )
    try:
        _resolve_positions(request, len(slides))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    task = AsyncPresentationGenerationTaskModel(
        status="pending",
        message="怨좏뭹吏?寃???묒뾽???湲곗뿴??異붽??덉뒿?덈떎.",
        data=None,
    )
    session.add(task)
    await session.commit()
    background_tasks.add_task(_run_quality_review, presentation_id, request, task.id)
    return task
