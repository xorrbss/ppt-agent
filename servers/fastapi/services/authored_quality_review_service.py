"""Post-generation quality review for saved AI-authored presentations.

The review renders the persisted authored HTML, critiques the resulting images in
parallel, and re-authors only the slides with visible defects. The live
deck is checkpointed before any replacement so the operation can be undone through
the existing presentation version history.
"""

# Private facade imports are intentionally re-exported for existing tests and
# extension monkeypatch points while their implementations live in helper modules.
# ruff: noqa: F401

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
import weakref
from collections import Counter
from collections.abc import Iterable, Mapping
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


def _merge_dom_repair_triggers(
    critiques: list[SlideCritique | None],
    local_reports: list[DomQualityReport],
) -> list[SlideCritique | None]:
    """Make required geometry defects start repair independently from VQA.

    VQA remains useful evidence, but it cannot veto deterministic clipping,
    off-canvas, overflow, or unintended-overlap telemetry.  Only medium/high
    geometry defects are promoted; unavailable telemetry stays fail-closed and
    does not fabricate a repair target.
    """

    geometry_categories = {"clipping", "off_canvas", "overflow", "overlap"}
    merged = list(critiques)
    for index, report in enumerate(local_reports):
        promoted = [
            issue
            for issue in report.issues
            if issue.category in geometry_categories
            and issue.severity in {"high", "medium"}
        ]
        if not promoted:
            continue
        critique = merged[index] if index < len(merged) else None
        existing = list(critique.issues) if critique is not None else []
        existing_keys = {(issue.type, issue.severity, issue.detail) for issue in existing}
        for issue in promoted:
            issue_type = (
                issue.category
                if issue.category in {"overflow", "overlap"}
                else "other"
            )
            detail = f"DOM_{issue.category.upper()}: {issue.detail}"[:200]
            key = (issue_type, issue.severity, detail)
            if key in existing_keys:
                continue
            existing.append(
                CritiqueIssue(
                    type=issue_type,
                    severity=issue.severity,
                    detail=detail,
                )
            )
            existing_keys.add(key)
        merged[index] = SlideCritique(needs_fix=True, issues=existing[:8])
    return merged


async def _execute_sqlalchemy(session: AsyncSession, statement: Any) -> Any:
    """Execute through SQLAlchemy even when a SQLModel session subclass is supplied.

    Production sessions are SQLAlchemy ``AsyncSession`` instances, while a few
    compatibility tests intentionally supply SQLModel's subclass.  SQLModel
    deprecates its inherited ``execute`` method and SQLAlchemy's ``scalars``
    dynamically dispatches to that deprecated override.  Calling the base method
    explicitly keeps one result contract (``Result``) in both environments without
    suppressing warnings or changing the database/session architecture.
    """
    return await AsyncSession.execute(session, statement)


def _ocr_route_signals(
    local_report: DomQualityReport | None,
    critique: SlideCritique | None,
    repair_detail: Mapping[str, Any] | None = None,
) -> list[str]:
    """Return closed, content-free reasons for selectively running slide OCR."""
    signals: set[str] = set()
    if local_report is not None:
        for issue in local_report.issues:
            if issue.category in _OCR_DOM_CATEGORIES:
                signals.add(f"dom:{issue.category}")
    if critique is not None:
        for issue in critique.issues:
            issue_type = str(issue.type)
            if issue_type in {"overflow", "placeholder"} or _OCR_DETAIL_SIGNAL.search(
                issue.detail or ""
            ):
                signals.add(f"vqa:{issue_type}")
    if repair_detail is not None:
        # Full re-authoring can alter any text even when the triggering defect was
        # purely visual.  Always compare its render against the baseline; this is
        # the highest-risk path and must not depend on VQA mentioning text loss.
        if str(repair_detail.get("path") or "") == "full_fallback":
            signals.add("repair:full_fallback")
        for change in repair_detail.get("element_changes") or []:
            if not isinstance(change, Mapping):
                continue
            action = str(change.get("action") or "").casefold().replace("_", "")
            if "text" in action:
                signals.add("repair:text_change")
                break
    return sorted(signals)


async def _inspect_ocr_for_review(*args, **kwargs):
    """Resolve the service facade at call time so tests/integrators can patch it."""
    return await inspect_authored_ocr(*args, **kwargs)


class _RequestOcrRuntime(RequestOcrRuntime):
    def __init__(self, language: str) -> None:
        super().__init__(
            language,
            inspect_ocr=_inspect_ocr_for_review,
            logger=LOGGER,
        )


async def _set_status(
    session: AsyncSession,
    task: AsyncPresentationGenerationTaskModel,
    *,
    status: str,
    message: str,
    data: dict | None = None,
    error: dict | None = None,
) -> None:
    task.status = status
    task.message = message
    task.data = data
    task.error = error
    task.updated_at = get_current_utc_datetime()
    session.add(task)
    await session.commit()


def _resolve_positions(
    request: AuthoredQualityReviewRequest, slide_count: int
) -> list[int]:
    if request.scope == "all":
        return list(range(slide_count))
    if any(index >= slide_count for index in request.slide_indices):
        raise ValueError("Slide index is outside the presentation")
    return list(request.slide_indices)


def _saved_brand(presentation: PresentationModel) -> Brand:
    theme = presentation.theme if isinstance(presentation.theme, dict) else {}
    language = str(theme.get("language") or presentation.language or "Korean")
    return Brand(
        topic=presentation.title or presentation.content or "Presentation",
        language=language,
        primary=str(theme.get("primary") or "#2563EB"),
        fonts=str(
            theme.get("fonts")
            or ("Noto Sans KR" if language.lower().startswith("ko") else "Inter")
        ),
        primary_is_explicit=True,
        fonts_are_explicit=True,
    )


def _saved_role(slide: SlideModel, fallback: str) -> str:
    if isinstance(slide.content, dict) and slide.content.get("role"):
        return str(slide.content["role"])
    layout = slide.layout or ""
    if layout.startswith("authored:") and layout.partition(":")[2]:
        return layout.partition(":")[2]
    return fallback


def _serialize_critiques(positions: Iterable[int], critiques: list) -> list[dict]:
    results: list[dict] = []
    for position, critique in zip(positions, critiques):
        if critique is None or not critique.needs_fix:
            continue
        results.append(
            {
                "slide_index": position,
                "issues": [issue.model_dump(mode="json") for issue in critique.issues],
            }
        )
    return results


async def _render_review_html(html: str) -> tuple[bytes, Mapping | None]:
    """Render with co-located telemetry while preserving legacy test doubles."""
    if render_html_to_png is not _ORIGINAL_RENDER_HTML_TO_PNG:
        png = await render_html_to_png(html)
        return png, get_cached_browser_metrics(html)
    return await render_html_to_png_with_metrics(html)


async def _hybrid_style_initial_review(
    *,
    pngs: list[bytes],
    contexts: list[str],
    positions: list[int],
    slide_count: int,
    local_reports: list[DomQualityReport],
    sampling_seed: str,
    scope: ReviewScope = "all",
) -> tuple[
    list[SlideCritique | None],
    dict,
    int,
    int,
    float,
    float,
]:
    """Delegate routing while preserving service-level monkeypatch points."""
    return await hybrid_style_initial_review(
        pngs=pngs,
        contexts=contexts,
        positions=positions,
        slide_count=slide_count,
        local_reports=local_reports,
        sampling_seed=sampling_seed,
        scope=scope,
        critique_batch=critique_authored,
        observe_cache=_observe_critique_cache,
        unusable_reason=_critique_unusable_reason,
    )


def _log_review_event(
    *,
    reference_mode: str,
    stage: str,
    slide_count: int,
    attempted: int = 0,
    accepted: int = 0,
    rejection_reasons: Mapping[str, int] | None = None,
    model: str | None = None,
    prompt_version: str | None = None,
    cache_version: str | None = None,
    critique_batch_count: int = 0,
    critique_slide_request_count: int = 0,
    cache_hits: int = 0,
    cache_misses: int = 0,
    render_ms: float = 0.0,
    critique_ms: float = 0.0,
    revision_ms: float = 0.0,
    indeterminate_count: int = 0,
    ocr_executed: bool = False,
    ocr_requests: int = 0,
    ocr_calls: int = 0,
    ocr_cache_hits: int = 0,
    ocr_total_ms: float = 0.0,
    local_inspection_ms: float = 0.0,
    montage_ms: float = 0.0,
    persist_ms: float = 0.0,
    total_ms: float = 0.0,
    patch_attempted: int = 0,
    patch_accepted: int = 0,
    patch_rejected: int = 0,
    fallback_attempted: int = 0,
    fallback_succeeded: int = 0,
    fallback_failed: int = 0,
    local_high_remaining: int = 0,
) -> None:
    """Emit telemetry through this module's logger for compatibility."""
    event = build_review_event(
        reference_mode=reference_mode,
        stage=stage,
        slide_count=slide_count,
        attempted=attempted,
        accepted=accepted,
        rejection_reasons=rejection_reasons,
        model=model,
        prompt_version=prompt_version,
        cache_version=cache_version,
        critique_batch_count=critique_batch_count,
        critique_slide_request_count=critique_slide_request_count,
        cache_hits=cache_hits,
        cache_misses=cache_misses,
        render_ms=render_ms,
        critique_ms=critique_ms,
        revision_ms=revision_ms,
        indeterminate_count=indeterminate_count,
        ocr_executed=ocr_executed,
        ocr_requests=ocr_requests,
        ocr_calls=ocr_calls,
        ocr_cache_hits=ocr_cache_hits,
        ocr_total_ms=ocr_total_ms,
        local_inspection_ms=local_inspection_ms,
        montage_ms=montage_ms,
        persist_ms=persist_ms,
        total_ms=total_ms,
        patch_attempted=patch_attempted,
        patch_accepted=patch_accepted,
        patch_rejected=patch_rejected,
        fallback_attempted=fallback_attempted,
        fallback_succeeded=fallback_succeeded,
        fallback_failed=fallback_failed,
        local_high_remaining=local_high_remaining,
    )
    LOGGER.info(
        "authored_quality_review %s",
        json.dumps(event, sort_keys=True, separators=(",", ":")),
    )


def _write_reviewed_png(
    presentation_id: uuid.UUID, slide_position: int, png: bytes
) -> str:
    return publish_reviewed_png(presentation_id, slide_position, png)


async def _run_legacy_authored_quality_review_unlocked(
    presentation_id: uuid.UUID,
    request: AuthoredQualityReviewRequest,
    task: AsyncPresentationGenerationTaskModel,
    session: AsyncSession,
) -> None:
    review_started_at = time.perf_counter()
    presentation = await session.get(PresentationModel, presentation_id)
    if presentation is None:
        raise ValueError("Presentation not found")
    outline = presentation.get_presentation_outline()
    if outline is None or not outline.slides:
        raise ValueError("The saved AI-authored outline is missing")

    slide_rows = await _execute_sqlalchemy(
        session,
        select(SlideModel)
        .where(SlideModel.presentation == presentation_id)
        .order_by(SlideModel.index),
    )
    slides = list(slide_rows.scalars().all())
    if (
        not slides
        or not presentation.is_authored()
        or not all(slide.is_authored() for slide in slides)
    ):
        raise ValueError("High-quality review supports AI-authored presentations only")
    if len(outline.slides) != len(slides):
        raise ValueError("Saved outline and slide counts do not match")

    positions = _resolve_positions(request, len(slides))
    targets = [slides[position] for position in positions]
    if any(not slide.html_content for slide in targets):
        raise ValueError("One or more slides are missing their editable authored HTML")

    await _set_status(
        session,
        task,
        status="processing",
        message="슬라이드 렌더링을 준비하고 있습니다.",
    )

    theme = presentation.theme if isinstance(presentation.theme, dict) else {}
    saved_style_id = str(theme.get("style") or "default")
    provenance_row = await session.get(AuthoredExactProvenance, presentation_id)
    exact_source_images: tuple[bytes, ...] | None = None
    if provenance_row is not None:
        private_style_id = (
            provenance_row.provenance.get("style_id")
            if isinstance(provenance_row.provenance, dict)
            else None
        )
        if not isinstance(private_style_id, str) or private_style_id != saved_style_id:
            raise ExactProvenanceError(
                "Saved exact reconstruction style identity changed"
            )
        style = resolve_authored_style(private_style_id)
        snapshot_id = provenance_row.provenance.get("source_snapshot_id")
        if snapshot_id is not None:
            if not isinstance(snapshot_id, str):
                raise ExactProvenanceError(
                    "Saved exact reconstruction source snapshot is invalid"
                )
            try:
                style = await materialize_exact_source_style(
                    style,
                    snapshot_id,
                    expected_slide_count=len(slides),
                )
            except ExactSourceSnapshotError as error:
                raise ExactProvenanceError(
                    "Saved exact reconstruction source snapshot is unavailable"
                ) from error
        exact_source_images = verify_exact_provenance(
            provenance_row.provenance,
            style,
            expected_slide_count=len(slides),
        )
        saved_reference_mode = EXACT_RECONSTRUCTION
    else:
        legacy_reference_mode = theme.get("reference_mode")
        if (
            isinstance(legacy_reference_mode, str)
            and normalize_reference_mode(legacy_reference_mode) == EXACT_RECONSTRUCTION
        ):
            raise ExactProvenanceError(
                "Saved exact reconstruction provenance is missing"
            )
        style = resolve_authored_style(saved_style_id)
        if (
            normalize_reference_mode(getattr(style, "reference_mode", STYLE_REFERENCE))
            == EXACT_RECONSTRUCTION
        ):
            raise ExactProvenanceError(
                "Saved exact reconstruction provenance is missing"
            )
        saved_reference_mode = STYLE_REFERENCE
    plan = prepare_authored_deck(outline, _saved_brand(presentation), style)
    plan_reference_mode = normalize_reference_mode(
        getattr(plan, "reference_mode", saved_reference_mode)
    )
    if plan_reference_mode != saved_reference_mode:
        raise ValueError(
            "Saved authored reference mode does not match its source style"
        )
    contents = [outline.slides[position].content for position in positions]
    roles = [
        _saved_role(target, plan.roles[position])
        for position, target in zip(positions, targets)
    ]
    persisted_htmls = [str(target.html_content) for target in targets]
    # Existing decks are migrated in memory; accepted fixes persist the stable ids.
    # The original values remain available for the compare-and-swap publish guard.
    htmls = [ensure_authored_qids(html) for html in persisted_htmls]
    exact_reconstruction = plan_reference_mode == EXACT_RECONSTRUCTION
    final_review_batch_count = 0
    if exact_reconstruction:
        planned_references = getattr(plan, "reference_images", None)
        if planned_references is None or len(planned_references) != len(slides):
            raise ValueError(
                "Exact reconstruction review requires a complete source mapping"
            )
        if any(len(references) != 1 for references in planned_references):
            raise ValueError(
                "Exact reconstruction review requires exactly one source per slide"
            )
        target_references = [planned_references[position] for position in positions]
    source_images = (
        [exact_source_images[position] for position in positions]
        if exact_reconstruction and exact_source_images is not None
        else None
    )
    review_metadata = _review_observation_metadata()
    ocr_runtime = _RequestOcrRuntime(_ocr_language(plan.brand.language))

    await _set_status(
        session,
        task,
        status="processing",
        message=f"{len(targets)}개 슬라이드를 렌더링하고 있습니다.",
    )
    render_started_at = time.perf_counter()
    rendered = list(
        await asyncio.gather(*(_render_review_html(html) for html in htmls))
    )
    pngs = [png for png, _metrics in rendered]
    browser_metrics = [metrics for _png, metrics in rendered]
    render_ms = (time.perf_counter() - render_started_at) * 1000

    local_started_at = time.perf_counter()
    local_reports = [
        inspect_authored_dom(html, metrics, reference_mode=plan_reference_mode)
        for html, metrics in zip(htmls, browser_metrics)
    ]
    local_inspection_ms = (time.perf_counter() - local_started_at) * 1000

    await _set_status(
        session,
        task,
        status="processing",
        message="잘림, 겹침, 정렬, 대비를 병렬로 검사하고 있습니다.",
    )
    critique_kwargs = {}
    if exact_reconstruction:
        critique_kwargs = {
            "source_images": source_images,
            "reference_mode": plan_reference_mode,
        }
    critique_contexts = [content[:240] for content in contents]
    routing_metadata: dict = {}
    montage_ms = 0.0
    use_hybrid_deck_routing = (
        request.scope in {"all", "subset"}
        and not exact_reconstruction
        and len(targets) >= 6
    )
    if use_hybrid_deck_routing:
        (
            initial_critiques,
            routing_metadata,
            initial_cache_hits,
            initial_cache_misses,
            montage_ms,
            initial_critique_ms,
        ) = await _hybrid_style_initial_review(
            pngs=pngs,
            contexts=critique_contexts,
            positions=positions,
            slide_count=len(slides),
            local_reports=local_reports,
            sampling_seed=str(presentation_id),
            scope=request.scope,
        )
    else:
        routing_plan = plan_authored_review_routing(
            scope=request.scope,
            slide_count=len(slides),
            local_issue_indices=[
                positions[index]
                for index, report in enumerate(local_reports)
                if report.needs_attention
            ],
            current_slide_index=(positions[0] if request.scope == "current" else None),
            target_indices=positions,
            sampling_seed=str(presentation_id),
        )
        routing_metadata = {
            "plan": routing_plan.as_dict(),
            "escalation": None,
            "montage_promoted_batches": [],
            "montage_vqa_count": 0,
            "detail_vqa_count": len(targets),
            "montage_vqa_batch_count": 0,
            "detail_vqa_batch_count": 1 if targets else 0,
            "vqa_batch_count": 1 if targets else 0,
        }
        initial_cache_hits, initial_cache_misses = _observe_critique_cache(
            pngs,
            critique_contexts,
            source_images=source_images,
            reference_mode=plan_reference_mode,
        )
        initial_critique_started_at = time.perf_counter()
        initial_critiques = list(
            await critique_authored(
                pngs,
                contexts=critique_contexts,
                **critique_kwargs,
            )
        )
        if len(initial_critiques) != len(targets):
            initial_critiques = (initial_critiques + [None] * len(targets))[
                : len(targets)
            ]
        initial_critique_ms = (time.perf_counter() - initial_critique_started_at) * 1000
    initial_critiques = _merge_dom_repair_triggers(
        initial_critiques,
        local_reports,
    )
    initial_indeterminate_count = sum(
        _critique_unusable_reason(critique) == "indeterminate"
        for critique in initial_critiques
    )
    ocr_checks: list[dict[str, Any]] = []
    for local_index, (position, local_report, initial_critique) in enumerate(
        zip(positions, local_reports, initial_critiques)
    ):
        ocr_checks.append(
            await _capture_initial_ocr(
                runtime=ocr_runtime,
                slide_index=position,
                reference_mode=plan_reference_mode,
                source_image=(
                    source_images[local_index] if source_images is not None else None
                ),
                baseline_image=pngs[local_index],
                baseline_metrics=browser_metrics[local_index],
                signals=_ocr_route_signals(local_report, initial_critique),
            )
        )
    ocr_summary = ocr_runtime.summary()
    _log_review_event(
        reference_mode=plan_reference_mode,
        stage="initial_review",
        slide_count=len(pngs),
        model=review_metadata["model"],
        prompt_version=review_metadata["prompt_version"],
        cache_version=review_metadata["cache_version"],
        critique_batch_count=routing_metadata["vqa_batch_count"],
        critique_slide_request_count=initial_cache_misses,
        cache_hits=initial_cache_hits,
        cache_misses=initial_cache_misses,
        render_ms=render_ms,
        critique_ms=initial_critique_ms,
        indeterminate_count=initial_indeterminate_count,
        **ocr_summary,
    )
    if exact_reconstruction and any(
        _critique_score(critique) is None for critique in initial_critiques
    ):
        raise RuntimeError(
            "Exact reconstruction comparison failed; no slide was treated as clean"
        )
    initial_issues = _serialize_critiques(positions, initial_critiques)

    if not initial_issues:
        message = (
            "검수가 완료되었습니다. 수정이 필요한 슬라이드가 없습니다."
            if not initial_issues
            else f"검수가 완료되었습니다. {len(initial_issues)}개 슬라이드에서 문제를 확인했습니다."
        )
        total_ms = (time.perf_counter() - review_started_at) * 1000
        local_high_remaining = sum(
            any(issue.severity == "high" for issue in report.issues)
            for report in local_reports
        )
        _log_review_event(
            reference_mode=plan_reference_mode,
            stage="completed",
            slide_count=len(targets),
            model=review_metadata["model"],
            prompt_version=review_metadata["prompt_version"],
            cache_version=review_metadata["cache_version"],
            critique_batch_count=routing_metadata["vqa_batch_count"],
            critique_slide_request_count=initial_cache_misses,
            cache_hits=initial_cache_hits,
            cache_misses=initial_cache_misses,
            render_ms=render_ms,
            local_inspection_ms=local_inspection_ms,
            montage_ms=montage_ms,
            critique_ms=initial_critique_ms,
            total_ms=total_ms,
            local_high_remaining=local_high_remaining,
            indeterminate_count=initial_indeterminate_count,
            **ocr_summary,
        )
        await _set_status(
            session,
            task,
            status="completed",
            message=message,
            data={
                "presentation_id": str(presentation_id),
                "mode": request.mode,
                "scope": request.scope,
                "reviewed_count": len(targets),
                "issue_slide_count": len(initial_issues),
                "issues": initial_issues,
                "fixed_count": 0,
                "fixed_slide_indices": [],
                "remaining_issue_count": len(initial_issues),
                "remaining_slide_indices": [
                    issue["slide_index"] for issue in initial_issues
                ],
                "remaining_issues": initial_issues,
                "version_saved": False,
                "slide_results": _build_slide_results(
                    positions=positions,
                    initial_critiques=initial_critiques,
                    final_critiques=initial_critiques,
                    repair_details=[],
                ),
                "local_checks": [
                    {
                        "slide_index": position,
                        "telemetry_status": report.telemetry_status,
                        "inspected_elements": report.inspected_elements,
                        "issues": [issue.to_dict() for issue in report.issues],
                    }
                    for position, report in zip(positions, local_reports)
                ],
                "ocr_checks": ocr_checks,
                "routing": routing_metadata,
                "metrics": {
                    "stage_timings_ms": {
                        "render": round(render_ms, 3),
                        "local_inspection": round(local_inspection_ms, 3),
                        "montage_vqa": round(montage_ms, 3),
                        "detail_vqa": round(initial_critique_ms, 3),
                        "ocr": ocr_summary["ocr_total_ms"],
                    },
                    "total_ms": round(total_ms, 3),
                    "cache_hits": initial_cache_hits,
                    "vqa_calls": initial_cache_misses,
                    "patch_count": 0,
                    "fallback_count": 0,
                    "patch_attempts": 0,
                    "patch_successes": 0,
                    "patch_failures": 0,
                    "patch_attempted": 0,
                    "patch_succeeded": 0,
                    "patch_failed": 0,
                    "fallback_attempts": 0,
                    "fallback_successes": 0,
                    "fallback_failures": 0,
                    "fallback_attempted": 0,
                    "fallback_succeeded": 0,
                    "fallback_failed": 0,
                    "candidate_proposed": 0,
                    "candidate_rendered": 0,
                    "validation_passed": 0,
                    "validation_rejected": 0,
                    "validation_inconclusive": 0,
                    "apply_committed": 0,
                    "publish_active": 0,
                    "postverify_passed": 0,
                    "applied": 0,
                    "rolled_back": 0,
                    "legacy_preserved": len(targets),
                    **ocr_summary,
                },
            },
        )
        return

    await _set_status(
        session,
        task,
        status="processing",
        message=f"문제가 발견된 {len(initial_issues)}개 슬라이드를 자동 수정하고 있습니다.",
    )
    revise_kwargs = {
        "initial_critiques": initial_critiques,
        "return_details": True,
    }
    candidate_decisions: list[dict] = []
    if exact_reconstruction:
        revise_kwargs["reference_images_by_slide"] = target_references
    revision_started_at = time.perf_counter()
    revision_result = await revise_authored_deck(
        htmls,
        pngs,
        contents,
        roles,
        plan.brand,
        plan.design_system,
        max_cycles=1,
        style=style,
        slide_indices=positions,
        total_slides=len(slides),
        **revise_kwargs,
    )
    if len(revision_result) == 4:
        (
            revised_htmls,
            revised_pngs,
            candidate_local_indices,
            repair_details,
        ) = revision_result
    else:  # Backward-compatible for alternate/custom revision implementations.
        revised_htmls, revised_pngs, candidate_local_indices = revision_result
        repair_details = []
    candidate_local_indices = list(dict.fromkeys(candidate_local_indices))
    if any(
        type(index) is not int or index < 0 or index >= len(targets)
        for index in candidate_local_indices
    ):
        raise RuntimeError(
            "Authored quality review returned an invalid candidate index"
        )
    revision_ms = (time.perf_counter() - revision_started_at) * 1000
    candidate_browser_metrics = {
        index: get_cached_browser_metrics(revised_htmls[index])
        for index in candidate_local_indices
    }
    candidate_local_reports = {
        index: inspect_authored_dom(
            revised_htmls[index],
            candidate_browser_metrics[index],
            reference_mode=plan_reference_mode,
        )
        for index in candidate_local_indices
    }
    candidate_local_decisions = {
        index: _local_acceptance_decision(local_reports[index], report)
        for index, report in candidate_local_reports.items()
    }
    fallback_local_reports: dict[int, DomQualityReport] = {}
    _log_review_event(
        reference_mode=plan_reference_mode,
        stage="correction",
        slide_count=len(targets),
        attempted=len(initial_issues),
        accepted=len(candidate_local_indices),
        rejection_reasons=(
            {"no_candidate": len(initial_issues) - len(candidate_local_indices)}
            if len(candidate_local_indices) < len(initial_issues)
            else None
        ),
        model=review_metadata["model"],
        prompt_version=review_metadata["prompt_version"],
        cache_version=review_metadata["cache_version"],
        render_ms=render_ms,
        revision_ms=revision_ms,
        **ocr_runtime.summary(),
    )

    await _set_status(
        session,
        task,
        status="processing",
        message="수정 결과를 다시 검증하고 있습니다.",
    )
    if exact_reconstruction:
        accepted_local_indices: list[int] = []
        chosen_critiques = list(initial_critiques)
        rejection_reasons: Counter[str] = Counter()
        final_cache_hits = 0
        final_cache_misses = 0
        final_critique_ms = 0.0
        if candidate_local_indices:
            final_review_batch_count = 1
            candidate_pngs = [revised_pngs[index] for index in candidate_local_indices]
            candidate_contexts = [
                contents[index][:240] for index in candidate_local_indices
            ]
            candidate_sources = [
                source_images[index] for index in candidate_local_indices
            ]
            final_cache_hits, final_cache_misses = _observe_critique_cache(
                candidate_pngs,
                candidate_contexts,
                source_images=candidate_sources,
                reference_mode=plan_reference_mode,
            )
            final_critique_started_at = time.perf_counter()
            candidate_critiques = await critique_authored(
                candidate_pngs,
                contexts=candidate_contexts,
                source_images=candidate_sources,
                reference_mode=plan_reference_mode,
            )
            final_critique_ms = (time.perf_counter() - final_critique_started_at) * 1000
            for offset, local_index in enumerate(candidate_local_indices):
                final_critique = (
                    candidate_critiques[offset]
                    if offset < len(candidate_critiques)
                    else None
                )
                accepted, reason = _acceptance_decision(
                    initial_critiques[local_index], final_critique
                )
                local_accepted, local_reason = candidate_local_decisions[local_index]
                if accepted and not local_accepted:
                    accepted, reason = False, local_reason
                repair_detail = next(
                    (
                        detail
                        for detail in reversed(repair_details)
                        if detail.get("local_index") == local_index
                    ),
                    {},
                )
                path = str(repair_detail.get("path") or "none")
                ocr_required, ocr_accepted, ocr_reason = await _evaluate_candidate_ocr(
                    runtime=ocr_runtime,
                    entry=ocr_checks[local_index],
                    reference_mode=plan_reference_mode,
                    source_image=source_images[local_index],
                    baseline_image=pngs[local_index],
                    candidate_image=revised_pngs[local_index],
                    baseline_metrics=browser_metrics[local_index],
                    candidate_metrics=candidate_browser_metrics.get(local_index),
                    signals=_ocr_route_signals(
                        candidate_local_reports.get(local_index),
                        final_critique,
                        repair_detail,
                    ),
                    path=path,
                )
                if accepted and ocr_required and not ocr_accepted:
                    accepted, reason = False, str(ocr_reason or "ocr_unavailable")
                candidate_decisions.append(
                    {
                        "slide_index": positions[local_index],
                        "accepted": accepted,
                        "reason": reason,
                        "attempted": True,
                        "candidate_proposed": True,
                        "candidate_rendered": True,
                        "validation_status": "passed" if accepted else "rejected",
                        "apply_status": "not_started",
                        "publish_status": "not_started",
                        "post_verify_status": "not_started",
                        "applied": False,
                        "rolled_back": False,
                        "rejection_reason": None if accepted else reason,
                        "path": path,
                    }
                )
                if accepted:
                    accepted_local_indices.append(local_index)
                    chosen_critiques[local_index] = final_critique
                else:
                    rejection_reasons[reason] += 1
            _log_review_event(
                reference_mode=plan_reference_mode,
                stage="final_review",
                slide_count=len(candidate_local_indices),
                attempted=len(candidate_local_indices),
                model=review_metadata["model"],
                prompt_version=review_metadata["prompt_version"],
                cache_version=review_metadata["cache_version"],
                critique_batch_count=1,
                critique_slide_request_count=final_cache_misses,
                cache_hits=final_cache_hits,
                cache_misses=final_cache_misses,
                render_ms=render_ms,
                critique_ms=final_critique_ms,
                revision_ms=revision_ms,
                indeterminate_count=sum(
                    _critique_unusable_reason(critique) == "indeterminate"
                    for critique in candidate_critiques
                ),
                **ocr_runtime.summary(),
            )
        fixed_local_indices = accepted_local_indices
        final_critiques = chosen_critiques
        _log_review_event(
            reference_mode=plan_reference_mode,
            stage="acceptance",
            slide_count=len(targets),
            attempted=len(candidate_local_indices),
            accepted=len(accepted_local_indices),
            rejection_reasons=rejection_reasons,
            model=review_metadata["model"],
            prompt_version=review_metadata["prompt_version"],
            cache_version=review_metadata["cache_version"],
            render_ms=render_ms,
            revision_ms=revision_ms,
            indeterminate_count=sum(
                _critique_unusable_reason(critique) == "indeterminate"
                for critique in final_critiques
            ),
            **ocr_runtime.summary(),
        )
    else:
        accepted_local_indices = []
        chosen_critiques = list(initial_critiques)
        comparison_metadata = _style_comparison_observation_metadata()
        final_critique_ms = 0.0
        candidate_critiques: list[SlideCritique | None] = []
        final_cache_hits = 0
        final_cache_misses = 0
        fallback_comparison_batch_count = 0
        repair_by_index = {
            detail.get("local_index"): detail
            for detail in repair_details
            if isinstance(detail, Mapping)
            and isinstance(detail.get("local_index"), int)
        }
        if candidate_local_indices:
            baseline_candidate_pngs = [pngs[index] for index in candidate_local_indices]
            candidate_pngs = [revised_pngs[index] for index in candidate_local_indices]
            candidate_contexts = [
                contents[index][:240] for index in candidate_local_indices
            ]
            final_cache_hits, final_cache_misses = _observe_style_comparison_cache(
                baseline_candidate_pngs,
                candidate_pngs,
                candidate_contexts,
            )
            final_critique_started_at = time.perf_counter()
            candidate_critiques = list(
                await critique_authored_style_comparison(
                    baseline_candidate_pngs,
                    candidate_pngs,
                    contexts=candidate_contexts,
                )
            )
            final_critique_ms = (time.perf_counter() - final_critique_started_at) * 1000
            for offset, local_index in enumerate(candidate_local_indices):
                final_critique = (
                    candidate_critiques[offset]
                    if offset < len(candidate_critiques)
                    else None
                )
                accepted, reason = _style_acceptance_decision(
                    initial_critiques[local_index], final_critique
                )
                local_accepted, local_reason = candidate_local_decisions[local_index]
                if accepted and not local_accepted:
                    accepted, reason = False, local_reason
                path = str(repair_by_index.get(local_index, {}).get("path") or "none")
                repair_detail = repair_by_index.get(local_index, {})
                ocr_required, ocr_accepted, ocr_reason = await _evaluate_candidate_ocr(
                    runtime=ocr_runtime,
                    entry=ocr_checks[local_index],
                    reference_mode=plan_reference_mode,
                    source_image=None,
                    baseline_image=pngs[local_index],
                    candidate_image=revised_pngs[local_index],
                    baseline_metrics=browser_metrics[local_index],
                    candidate_metrics=candidate_browser_metrics.get(local_index),
                    signals=_ocr_route_signals(
                        candidate_local_reports.get(local_index),
                        final_critique,
                        repair_detail,
                    ),
                    path=path,
                )
                if accepted and ocr_required and not ocr_accepted:
                    accepted, reason = False, str(ocr_reason or "ocr_unavailable")
                candidate_decisions.append(
                    {
                        "slide_index": positions[local_index],
                        "accepted": accepted,
                        "reason": reason,
                        "attempted": True,
                        "candidate_proposed": True,
                        "candidate_rendered": True,
                        "validation_status": "passed" if accepted else "rejected",
                        "apply_status": "not_started",
                        "publish_status": "not_started",
                        "post_verify_status": "not_started",
                        "applied": False,
                        "rolled_back": False,
                        "rejection_reason": None if accepted else reason,
                        "path": path,
                    }
                )
                if accepted:
                    accepted_local_indices.append(local_index)
                    chosen_critiques[local_index] = final_critique

        # A local patch that is determinate but fails the final quality gate gets one
        # high-reasoning structural retry. Provider/unavailable/indeterminate results
        # deliberately stop here: accepting a second candidate would not be safe.
        fallback_eligible_reasons = {
            "unchanged",
            "worsened",
            "regression",
            "local_regression",
            "local_baseline_unavailable",
            "local_candidate_unavailable",
            "local_high_remaining",
            "local_not_improved",
            "ocr_not_improved",
            "ocr_regression",
        }
        decisions_by_position = {
            decision["slide_index"]: decision for decision in candidate_decisions
        }
        fallback_local_indices = [
            local_index
            for local_index in candidate_local_indices
            if (
                decisions_by_position[positions[local_index]]["reason"]
                in fallback_eligible_reasons
                and decisions_by_position[positions[local_index]]["path"]
                == "local_patch"
            )
        ]
        if fallback_local_indices:
            clean_critique = SlideCritique(needs_fix=False, issues=[])
            fallback_initial_critiques = [
                initial_critiques[index]
                if index in fallback_local_indices
                else clean_critique
                for index in range(len(targets))
            ]
            fallback_started_at = time.perf_counter()
            fallback_result = await revise_authored_deck(
                htmls,
                pngs,
                contents,
                roles,
                plan.brand,
                plan.design_system,
                max_cycles=1,
                style=style,
                slide_indices=positions,
                total_slides=len(slides),
                initial_critiques=fallback_initial_critiques,
                return_details=True,
                force_full_reauthor_indices=fallback_local_indices,
            )
            revision_ms += (time.perf_counter() - fallback_started_at) * 1000
            if len(fallback_result) == 4:
                legacy_fallback_result = False
                (
                    fallback_htmls,
                    fallback_pngs,
                    fallback_candidate_indices,
                    fallback_repair_details,
                ) = fallback_result
            else:  # Backward-compatible custom implementation.
                legacy_fallback_result = True
                (
                    fallback_htmls,
                    fallback_pngs,
                    fallback_candidate_indices,
                ) = fallback_result
                fallback_repair_details = []
            fallback_candidate_indices = list(dict.fromkeys(fallback_candidate_indices))
            if any(
                type(index) is not int or index not in fallback_local_indices
                for index in fallback_candidate_indices
            ):
                raise RuntimeError(
                    "Authored quality review returned an invalid fallback candidate index"
                )
            if legacy_fallback_result:
                fallback_candidate_set = set(fallback_candidate_indices)
                fallback_repair_details = []
                for index in fallback_local_indices:
                    succeeded = index in fallback_candidate_set
                    failure = None
                    if not succeeded:
                        failure = {
                            "stage": "full_reauthor",
                            "code": "fallback_failed",
                            "error_type": "UnknownFallbackFailure",
                        }
                    fallback_repair_details.append(
                        {
                            "local_index": index,
                            "slide_index": positions[index],
                            "path": "full_fallback",
                            "attempted": True,
                            "succeeded": succeeded,
                            "failure_detail": failure,
                            "patch": {
                                "attempted": False,
                                "succeeded": False,
                                "failure_detail": None,
                            },
                            "fallback": {
                                "attempted": True,
                                "succeeded": succeeded,
                                "failure_detail": failure,
                            },
                            "element_changes": [],
                        }
                    )
            repair_details.extend(fallback_repair_details)
            fallback_detail_by_index = {
                detail.get("local_index"): detail
                for detail in fallback_repair_details
                if isinstance(detail, Mapping)
                and isinstance(detail.get("local_index"), int)
            }
            fallback_browser_metrics = {
                index: get_cached_browser_metrics(fallback_htmls[index])
                for index in fallback_candidate_indices
            }
            fallback_local_reports = {
                index: inspect_authored_dom(
                    fallback_htmls[index],
                    fallback_browser_metrics[index],
                    reference_mode=plan_reference_mode,
                )
                for index in fallback_candidate_indices
            }
            fallback_local_decisions = {
                index: _local_acceptance_decision(local_reports[index], report)
                for index, report in fallback_local_reports.items()
            }
            fallback_critiques: list[SlideCritique | None] = []
            if fallback_candidate_indices:
                fallback_comparison_batch_count = 1
                fallback_baseline_pngs = [
                    pngs[index] for index in fallback_candidate_indices
                ]
                fallback_candidate_pngs = [
                    fallback_pngs[index] for index in fallback_candidate_indices
                ]
                fallback_contexts = [
                    contents[index][:240] for index in fallback_candidate_indices
                ]
                cache_hits, cache_misses = _observe_style_comparison_cache(
                    fallback_baseline_pngs,
                    fallback_candidate_pngs,
                    fallback_contexts,
                )
                final_cache_hits += cache_hits
                final_cache_misses += cache_misses
                fallback_review_started_at = time.perf_counter()
                fallback_critiques = list(
                    await critique_authored_style_comparison(
                        fallback_baseline_pngs,
                        fallback_candidate_pngs,
                        contexts=fallback_contexts,
                    )
                )
                final_critique_ms += (
                    time.perf_counter() - fallback_review_started_at
                ) * 1000
                candidate_critiques.extend(fallback_critiques)

            fallback_critique_by_index = {
                local_index: (
                    fallback_critiques[offset]
                    if offset < len(fallback_critiques)
                    else None
                )
                for offset, local_index in enumerate(fallback_candidate_indices)
            }
            for local_index in fallback_local_indices:
                decision = decisions_by_position[positions[local_index]]
                initial_rejection_reason = decision["reason"]
                final_critique = fallback_critique_by_index.get(local_index)
                if local_index not in fallback_candidate_indices:
                    failure = (
                        fallback_detail_by_index.get(local_index, {}).get(
                            "failure_detail"
                        )
                        or {}
                    )
                    accepted, reason = (
                        False,
                        str(failure.get("code") or "fallback_failed"),
                    )
                else:
                    accepted, reason = _style_acceptance_decision(
                        initial_critiques[local_index], final_critique
                    )
                    local_accepted, local_reason = fallback_local_decisions[local_index]
                    if accepted and not local_accepted:
                        accepted, reason = False, local_reason
                    fallback_detail = fallback_detail_by_index.get(local_index, {})
                    (
                        ocr_required,
                        ocr_accepted,
                        ocr_reason,
                    ) = await _evaluate_candidate_ocr(
                        runtime=ocr_runtime,
                        entry=ocr_checks[local_index],
                        reference_mode=plan_reference_mode,
                        source_image=None,
                        baseline_image=pngs[local_index],
                        candidate_image=fallback_pngs[local_index],
                        baseline_metrics=browser_metrics[local_index],
                        candidate_metrics=fallback_browser_metrics.get(local_index),
                        signals=_ocr_route_signals(
                            fallback_local_reports.get(local_index),
                            final_critique,
                            fallback_detail,
                        ),
                        path="full_fallback",
                    )
                    if accepted and ocr_required and not ocr_accepted:
                        accepted, reason = False, str(ocr_reason or "ocr_unavailable")
                decision.update(
                    {
                        "accepted": accepted,
                        "reason": reason,
                        "attempted": True,
                        "candidate_proposed": True,
                        "candidate_rendered": local_index in fallback_candidate_indices,
                        "validation_status": "passed" if accepted else "rejected",
                        "apply_status": "not_started",
                        "publish_status": "not_started",
                        "post_verify_status": "not_started",
                        "applied": False,
                        "rolled_back": False,
                        "rejection_reason": None if accepted else reason,
                        "path": "full_fallback",
                        "initial_rejection_reason": initial_rejection_reason,
                    }
                )
                if accepted:
                    revised_htmls[local_index] = fallback_htmls[local_index]
                    revised_pngs[local_index] = fallback_pngs[local_index]
                    accepted_local_indices.append(local_index)
                    chosen_critiques[local_index] = final_critique

        rejection_reasons = Counter(
            decision["reason"]
            for decision in candidate_decisions
            if not decision["accepted"]
        )
        fixed_local_indices = accepted_local_indices
        final_critiques = chosen_critiques
        final_review_batch_count = (
            (1 if candidate_local_indices else 0) + fallback_comparison_batch_count
        )
        _log_review_event(
            reference_mode=plan_reference_mode,
            stage="final_review",
            slide_count=len(candidate_critiques),
            attempted=len(candidate_critiques),
            model=comparison_metadata["model"],
            prompt_version=comparison_metadata["prompt_version"],
            cache_version=comparison_metadata["cache_version"],
            critique_batch_count=(1 if candidate_local_indices else 0)
            + fallback_comparison_batch_count,
            critique_slide_request_count=final_cache_misses,
            cache_hits=final_cache_hits,
            cache_misses=final_cache_misses,
            render_ms=render_ms,
            critique_ms=final_critique_ms,
            revision_ms=revision_ms,
            indeterminate_count=sum(
                _critique_unusable_reason(critique) == "indeterminate"
                for critique in candidate_critiques
            ),
            **ocr_runtime.summary(),
        )
        _log_review_event(
            reference_mode=plan_reference_mode,
            stage="acceptance",
            slide_count=len(targets),
            attempted=len(candidate_decisions),
            accepted=len(accepted_local_indices),
            rejection_reasons=rejection_reasons,
            model=comparison_metadata["model"],
            prompt_version=comparison_metadata["prompt_version"],
            cache_version=comparison_metadata["cache_version"],
            render_ms=render_ms,
            revision_ms=revision_ms,
            indeterminate_count=sum(
                _critique_unusable_reason(critique) == "indeterminate"
                for critique in candidate_critiques
            ),
            **ocr_runtime.summary(),
        )
    remaining_issues = _serialize_critiques(positions, final_critiques)

    fixed_positions = [positions[index] for index in fixed_local_indices]
    persist_ms = 0.0
    if fixed_local_indices:
        persist_started_at = time.perf_counter()
        # Publish every immutable candidate before staging any database change.
        # A failed asset write therefore leaves both the live deck and its snapshot
        # history untouched (an unreferenced immutable file is safe to retain).
        pending_updates: list[tuple[int, str, dict, dict]] = []
        for local_index in fixed_local_indices:
            position = positions[local_index]
            slide = targets[local_index]
            image_reference = _write_reviewed_png(
                presentation_id, position, revised_pngs[local_index]
            )
            pending_updates.append(
                (
                    local_index,
                    revised_htmls[local_index],
                    {
                        **(slide.content if isinstance(slide.content, dict) else {}),
                        "__authored__": True,
                        "image": image_reference,
                        "role": roles[local_index],
                    },
                    {
                        **(
                            slide.properties
                            if isinstance(slide.properties, dict)
                            else {}
                        ),
                        "image": image_reference,
                    },
                )
            )
        await version_service.snapshot_slides(
            session,
            presentation_id,
            [slide.model_dump(mode="json") for slide in slides],
            label="고품질 검수 전",
            force=True,
        )
        for (
            local_index,
            revised_html,
            revised_content,
            revised_properties,
        ) in pending_updates:
            slide = targets[local_index]
            result = await _execute_sqlalchemy(
                session,
                update(SlideModel)
                .where(
                    SlideModel.id == slide.id,
                    SlideModel.html_content == persisted_htmls[local_index],
                    SlideModel.content == slide.content,
                    SlideModel.properties == slide.properties,
                )
                .values(
                    html_content=revised_html,
                    content=revised_content,
                    properties=revised_properties,
                )
                .execution_options(synchronize_session=False),
            )
            if result.rowcount != 1:
                await session.rollback()
                raise RuntimeError(
                    "Presentation changed during quality review; no fixes were applied"
                )
        # Keep accepted slide mutations and the final completed task payload in
        # one transaction. The operation handler uses that task as its durable
        # resume marker, so a crash cannot leave applied fixes without the data
        # needed to publish the revision exactly once.
        await session.flush()
        expected_by_id = {
            targets[local_index].id: (revised_html, revised_content, revised_properties)
            for local_index, revised_html, revised_content, revised_properties in pending_updates
        }
        readback = await _execute_sqlalchemy(
            session,
            select(
                SlideModel.id,
                SlideModel.html_content,
                SlideModel.content,
                SlideModel.properties,
            ).where(SlideModel.id.in_(tuple(expected_by_id))),
        )
        readback_by_id = {
            row.id: (row.html_content, row.content, row.properties)
            for row in readback.all()
        }
        if readback_by_id != expected_by_id:
            await session.rollback()
            raise RuntimeError("Published authored slide read-back did not match")
        fixed_position_set = set(fixed_positions)
        for decision in candidate_decisions:
            if decision.get("slide_index") not in fixed_position_set:
                continue
            html = revised_htmls[positions.index(decision["slide_index"])]
            decision.update(
                {
                    "apply_status": "committed",
                    "publish_status": "active",
                    "post_verify_status": "passed",
                    "html_hash": "sha256:"
                    + hashlib.sha256(html.encode("utf-8")).hexdigest(),
                    "applied": True,
                }
            )
        persist_ms = (time.perf_counter() - persist_started_at) * 1000

    task_rejection_reasons: Counter[str] = Counter(
        decision["reason"]
        for decision in candidate_decisions
        if not decision["accepted"]
    )
    no_candidate_count = max(0, len(initial_issues) - len(candidate_local_indices))
    if no_candidate_count:
        task_rejection_reasons["no_candidate"] += no_candidate_count

    fallback_count = sum(
        detail.get("path") == "full_fallback" and detail.get("succeeded", True)
        for detail in repair_details
        if isinstance(detail, Mapping)
    )
    patch_count = sum(
        detail.get("path") == "local_patch" and detail.get("succeeded", True)
        for detail in repair_details
        if isinstance(detail, Mapping)
    )
    patch_attempts = sum(
        bool((detail.get("patch") or {}).get("attempted"))
        for detail in repair_details
        if isinstance(detail, Mapping)
    )
    patch_successes = sum(
        bool((detail.get("patch") or {}).get("succeeded"))
        for detail in repair_details
        if isinstance(detail, Mapping)
    )
    fallback_attempts = sum(
        bool((detail.get("fallback") or {}).get("attempted"))
        for detail in repair_details
        if isinstance(detail, Mapping)
    )
    fallback_successes = sum(
        bool((detail.get("fallback") or {}).get("succeeded"))
        for detail in repair_details
        if isinstance(detail, Mapping)
    )
    ocr_summary = ocr_runtime.summary()
    funnel_metrics = {
        "candidate_proposed": sum(
            bool(decision.get("candidate_proposed"))
            for decision in candidate_decisions
        ),
        "candidate_rendered": sum(
            bool(decision.get("candidate_rendered"))
            for decision in candidate_decisions
        ),
        "validation_passed": sum(
            decision.get("validation_status") == "passed"
            for decision in candidate_decisions
        ),
        "validation_rejected": sum(
            decision.get("validation_status") == "rejected"
            for decision in candidate_decisions
        ),
        "validation_inconclusive": sum(
            decision.get("validation_status") == "inconclusive"
            for decision in candidate_decisions
        ),
        "apply_committed": sum(
            decision.get("apply_status") == "committed"
            for decision in candidate_decisions
        ),
        "publish_active": sum(
            decision.get("publish_status") == "active"
            for decision in candidate_decisions
        ),
        "postverify_passed": sum(
            decision.get("post_verify_status") == "passed"
            for decision in candidate_decisions
        ),
        "applied": sum(bool(decision.get("applied")) for decision in candidate_decisions),
        "rolled_back": sum(
            bool(decision.get("rolled_back")) for decision in candidate_decisions
        ),
        "legacy_preserved": len(targets)
        - sum(bool(decision.get("applied")) for decision in candidate_decisions),
    }
    total_ms = (time.perf_counter() - review_started_at) * 1000
    final_local_reports = list(local_reports)
    for local_index in fixed_local_indices:
        accepted_report = fallback_local_reports.get(
            local_index
        ) or candidate_local_reports.get(local_index)
        if accepted_report is not None:
            final_local_reports[local_index] = accepted_report
    local_high_remaining = sum(
        any(issue.severity == "high" for issue in report.issues)
        for report in final_local_reports
    )
    _log_review_event(
        reference_mode=plan_reference_mode,
        stage="completed",
        slide_count=len(targets),
        attempted=len(candidate_decisions),
        accepted=len(fixed_local_indices),
        rejection_reasons=task_rejection_reasons,
        model=review_metadata["model"],
        prompt_version=review_metadata["prompt_version"],
        cache_version=review_metadata["cache_version"],
        critique_batch_count=(
            routing_metadata["vqa_batch_count"] + final_review_batch_count
        ),
        critique_slide_request_count=initial_cache_misses + final_cache_misses,
        cache_hits=initial_cache_hits + final_cache_hits,
        cache_misses=initial_cache_misses + final_cache_misses,
        render_ms=render_ms,
        local_inspection_ms=local_inspection_ms,
        montage_ms=montage_ms,
        critique_ms=initial_critique_ms + final_critique_ms,
        revision_ms=revision_ms,
        persist_ms=persist_ms,
        total_ms=total_ms,
        patch_attempted=patch_attempts,
        patch_accepted=patch_successes,
        patch_rejected=max(0, patch_attempts - patch_successes),
        fallback_attempted=fallback_attempts,
        fallback_succeeded=fallback_successes,
        fallback_failed=max(0, fallback_attempts - fallback_successes),
        local_high_remaining=local_high_remaining,
        indeterminate_count=sum(
            _critique_unusable_reason(critique) == "indeterminate"
            for critique in final_critiques
        ),
        **ocr_summary,
    )
    await _set_status(
        session,
        task,
        status="completed",
        message=(
            f"검수가 완료되었습니다. {len(fixed_positions)}개 슬라이드를 수정했습니다."
            if fixed_positions
            else "검수는 완료되었지만 자동 수정할 수 있는 변경은 생성되지 않았습니다."
        ),
        data={
            "presentation_id": str(presentation_id),
            "mode": request.mode,
            "scope": request.scope,
            "reviewed_count": len(targets),
            "issue_slide_count": len(initial_issues),
            "issues": initial_issues,
            "fixed_count": len(fixed_positions),
            "fixed_slide_indices": fixed_positions,
            "candidate_count": len(candidate_local_indices),
            "accepted_candidate_count": len(fixed_positions),
            "rejected_candidate_count": (
                len(candidate_local_indices) - len(fixed_positions)
            ),
            "rejection_reasons": dict(sorted(task_rejection_reasons.items())),
            "candidate_decisions": candidate_decisions,
            "remaining_issue_count": len(remaining_issues),
            "remaining_slide_indices": [
                issue["slide_index"] for issue in remaining_issues
            ],
            "remaining_issues": remaining_issues,
            "version_saved": bool(fixed_positions),
            "slide_results": _build_slide_results(
                positions=positions,
                initial_critiques=initial_critiques,
                final_critiques=final_critiques,
                repair_details=repair_details,
                candidate_decisions=candidate_decisions,
            ),
            "local_checks": [
                {
                    "slide_index": position,
                    "telemetry_status": report.telemetry_status,
                    "inspected_elements": report.inspected_elements,
                    "issues": [issue.to_dict() for issue in report.issues],
                }
                for position, report in zip(positions, local_reports)
            ],
            "ocr_checks": ocr_checks,
            "routing": routing_metadata,
            "metrics": {
                "stage_timings_ms": {
                    "render": round(render_ms, 3),
                    "local_inspection": round(local_inspection_ms, 3),
                    "montage_vqa": round(montage_ms, 3),
                    "detail_vqa": round(initial_critique_ms, 3),
                    "repair": round(revision_ms, 3),
                    "final_vqa": round(final_critique_ms, 3),
                    "ocr": ocr_summary["ocr_total_ms"],
                    "persist": round(persist_ms, 3),
                },
                "total_ms": round(total_ms, 3),
                "cache_hits": initial_cache_hits + final_cache_hits,
                "vqa_calls": initial_cache_misses + final_cache_misses,
                "patch_count": patch_count,
                "fallback_count": fallback_count,
                "patch_attempts": patch_attempts,
                "patch_successes": patch_successes,
                "patch_failures": max(0, patch_attempts - patch_successes),
                "patch_attempted": patch_attempts,
                "patch_succeeded": patch_successes,
                "patch_failed": max(0, patch_attempts - patch_successes),
                "fallback_attempts": fallback_attempts,
                "fallback_successes": fallback_successes,
                "fallback_failures": max(0, fallback_attempts - fallback_successes),
                "fallback_attempted": fallback_attempts,
                "fallback_succeeded": fallback_successes,
                "fallback_failed": max(0, fallback_attempts - fallback_successes),
                **funnel_metrics,
                **ocr_summary,
            },
        },
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


def _canonical_metrics_with_flat_aliases(metrics: Mapping[str, Any]) -> dict[str, Any]:
    """Keep canonical telemetry queryable by existing summary consumers.

    The nested shape is the source of truth, while the flat aliases let mixed
    legacy/canonical review runs add the same counter at the deck-summary seam.
    A producer-provided flat value always wins so a future producer cannot be
    silently double-counted.
    """

    normalized = dict(metrics)
    for group, aliases in _CANONICAL_METRIC_FLAT_ALIASES.items():
        values = metrics.get(group)
        if not isinstance(values, Mapping):
            continue
        for nested_key, flat_key in aliases.items():
            if nested_key in values:
                normalized.setdefault(flat_key, values[nested_key])
    return normalized


def _merge_canonical_metric_group(
    previous: Any, value: Mapping[str, Any], *, group: str
) -> dict[str, Any]:
    """Aggregate canonical counters without weakening bounded/fail-closed flags."""

    merged = dict(previous) if isinstance(previous, Mapping) else {}
    for key, item in value.items():
        prior = merged.get(key)
        if (
            isinstance(prior, (int, float))
            and not isinstance(prior, bool)
            and isinstance(item, (int, float))
            and not isinstance(item, bool)
        ):
            merged[str(key)] = prior + item
        elif group == "solver" and key == "search_complete" and isinstance(item, bool):
            # One bounded solver result makes the deck-level search bounded.
            merged[str(key)] = bool(prior) and item if prior is not None else item
        elif group == "rollout" and key == "kill_switch_enabled" and isinstance(item, bool):
            # A kill switch on any owned slide must remain visible to operators.
            merged[str(key)] = bool(prior) or item
        else:
            merged[str(key)] = item
    return merged


def _merge_slidespec_review_data(
    *,
    presentation_id: uuid.UUID,
    request: AuthoredQualityReviewRequest,
    legacy: Mapping[str, Any] | None,
    canonical: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge disjoint per-slide owners without changing the legacy contract."""

    result = dict(legacy or {})
    result.setdefault("presentation_id", str(presentation_id))
    result.setdefault("mode", request.mode)
    result.setdefault("scope", request.scope)
    additive = (
        "reviewed_count",
        "issue_slide_count",
        "fixed_count",
        "candidate_count",
        "accepted_candidate_count",
        "rejected_candidate_count",
        "remaining_issue_count",
    )
    for key in additive:
        result[key] = int(result.get(key, 0)) + int(canonical.get(key, 0))
    for key in ("issues", "remaining_issues", "slide_results"):
        result[key] = list(result.get(key, [])) + list(canonical.get(key, []))
    result["candidate_decisions"] = list(result.get("candidate_decisions", [])) + list(
        canonical.get("candidate_decisions", [])
    )
    rejection_reasons = dict(result.get("rejection_reasons") or {})
    for code, count in (canonical.get("rejection_reasons") or {}).items():
        rejection_reasons[str(code)] = rejection_reasons.get(str(code), 0) + int(count)
    result["rejection_reasons"] = rejection_reasons
    for key in ("fixed_slide_indices", "remaining_slide_indices"):
        canonical_key = (
            "remaining_issue_indices" if key == "remaining_slide_indices" else key
        )
        result[key] = sorted(
            set(result.get(key, [])) | set(canonical.get(canonical_key, []))
        )
    result["version_saved"] = bool(
        result.get("version_saved") or canonical.get("version_saved")
    )
    routing = dict(result.get("routing") or {})
    routing.update(canonical.get("routing") or {})
    result["routing"] = routing
    metrics = dict(result.get("metrics") or {})
    canonical_metrics = _canonical_metrics_with_flat_aliases(
        canonical.get("metrics") or {}
    )
    for key, value in canonical_metrics.items():
        previous = metrics.get(key)
        if key == "stage_timings_ms" and isinstance(value, Mapping):
            merged_timings = dict(previous) if isinstance(previous, Mapping) else {}
            for stage, duration in value.items():
                prior_duration = merged_timings.get(stage, 0)
                if isinstance(duration, (int, float)) and not isinstance(
                    duration, bool
                ):
                    merged_timings[str(stage)] = float(prior_duration) + float(duration)
            metrics[key] = merged_timings
            continue
        if key in _CANONICAL_METRIC_FLAT_ALIASES and isinstance(value, Mapping):
            metrics[key] = _merge_canonical_metric_group(previous, value, group=key)
            continue
        if (
            isinstance(previous, (int, float))
            and not isinstance(previous, bool)
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        ):
            metrics[key] = previous + value
        else:
            metrics[key] = value
    result["metrics"] = metrics
    result["slide_results"] = sorted(
        result["slide_results"], key=lambda item: int(item.get("slide_index", 0))
    )
    return result


async def _run_authored_quality_review_unlocked(
    presentation_id: uuid.UUID,
    request: AuthoredQualityReviewRequest,
    task: AsyncPresentationGenerationTaskModel,
    session: AsyncSession,
) -> None:
    """Import eligible authored slides, then route canonical owners to SlideSpec."""

    presentation = await session.get(PresentationModel, presentation_id)
    if presentation is None:
        raise ValueError("Presentation not found")
    outline = presentation.get_presentation_outline()
    if outline is None or not outline.slides:
        raise ValueError("The saved AI-authored outline is missing")
    slide_rows = await _execute_sqlalchemy(
        session,
        select(SlideModel)
        .where(SlideModel.presentation == presentation_id)
        .order_by(SlideModel.index),
    )
    slides = list(slide_rows.scalars().all())
    if (
        not slides
        or not presentation.is_authored()
        or not all(slide.is_authored() for slide in slides)
    ):
        raise ValueError("High-quality review supports AI-authored presentations only")
    if len(outline.slides) != len(slides):
        raise ValueError("Saved outline and slide counts do not match")
    positions = _resolve_positions(request, len(slides))
    if any(not slides[position].html_content for position in positions):
        raise ValueError("One or more slides are missing their editable authored HTML")

    from services.slidespec_artifact_store import ACTIVE_POINTER_KEY
    from services.slidespec_authored_importer import ensure_authored_slidespec_imports

    imported = await ensure_authored_slidespec_imports(
        presentation_id=presentation_id,
        slides=slides,
        positions=positions,
        opt_in=request.slidespec_opt_in,
        owner_id=getattr(presentation, "owner_id", None),
        session=session,
    )
    slides = list(imported.slides)

    claimed_hint = {
        position
        for position in positions
        if isinstance(slides[position].properties, Mapping)
        and ACTIVE_POINTER_KEY in slides[position].properties
    }
    if not claimed_hint:
        await _run_legacy_authored_quality_review_unlocked(
            presentation_id, request, task, session
        )
        return

    # Run legacy owners first.  It may commit; canonical publication then takes a
    # fresh snapshot and CASes only its disjoint pointer-backed slides.  This avoids
    # reporting a failed mixed-deck task after a canonical mutation already landed.
    legacy_positions = [position for position in positions if position not in claimed_hint]
    legacy_data: Mapping[str, Any] | None = None
    if legacy_positions:
        legacy_request = AuthoredQualityReviewRequest(
            scope="subset",
            slide_indices=legacy_positions,
            mode=request.mode,
            slidespec_opt_in=False,
        )
        await _run_legacy_authored_quality_review_unlocked(
            presentation_id, legacy_request, task, session
        )
        legacy_data = task.data if isinstance(task.data, Mapping) else None

        refreshed_rows = await _execute_sqlalchemy(
            session,
            select(SlideModel)
            .where(SlideModel.presentation == presentation_id)
            .order_by(SlideModel.index)
            .execution_options(populate_existing=True),
        )
        slides = list(refreshed_rows.scalars().all())

    from services.slidespec_authored_executor import execute_slidespec_authored_review

    canonical = await execute_slidespec_authored_review(
        presentation_id=presentation_id,
        slides=slides,
        positions=sorted(claimed_hint),
        opt_in=request.slidespec_opt_in,
        owner_id=getattr(presentation, "owner_id", None),
        session=session,
    )
    claimed = set(canonical.claimed_positions)
    if claimed != claimed_hint:
        await session.rollback()
        raise RuntimeError("SlideSpec ownership changed during authored review")

    routing = canonical.data.setdefault("routing", {})
    routing["imported_positions"] = list(imported.imported_positions)
    routing["import_failures"] = {
        str(position): code for position, code in imported.failures.items()
    }

    data = _merge_slidespec_review_data(
        presentation_id=presentation_id,
        request=request,
        legacy=legacy_data,
        canonical=canonical.data,
    )
    await _set_status(
        session,
        task,
        status="completed",
        message=(
            f"검수가 완료되었습니다. {data['fixed_count']}개 슬라이드를 수정했습니다."
            if data["fixed_count"]
            else "검수는 완료되었지만 자동 수정으로 확정된 변경은 없습니다."
        ),
        data=data,
    )


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
                message="고품질 검수 중 오류가 발생했습니다.",
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
        message="고품질 검수 작업을 대기열에 추가했습니다.",
        data=None,
    )
    session.add(task)
    await session.commit()
    background_tasks.add_task(_run_quality_review, presentation_id, request, task.id)
    return task
