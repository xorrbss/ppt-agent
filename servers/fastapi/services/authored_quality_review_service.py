"""Post-generation quality review for saved AI-authored presentations.

The review renders the persisted authored HTML, critiques the resulting images in
parallel, and re-authors only the slides with visible defects. The live
deck is checkpointed before any replacement so the operation can be undone through
the existing presentation version history.
"""

# Several imports are re-exported only as monkeypatch points for existing tests
# and extensions (for example ``author_vision_qa`` and ``async_session_maker``).
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
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import inspect, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.presentation_from_template import AuthoredQualityReviewRequest, ReviewScope
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.authored_exact_provenance import AuthoredExactProvenance
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services import presentation_version_service as version_service
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
from services.authored_canvas import presentation_canvas_contract
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
from services.slidespec_artifact_store import ACTIVE_POINTER_KEY
from services.database import async_session_maker
from services.workspace_authored_async_admission import admit_workspace_authored_task
from services.image_generation_service import ImageGenerationService
from utils.asset_directory_utils import get_images_directory
from utils.authored_dom_categories import (
    BLOCKING_SAFETY_CATEGORIES,
    critique_type_for_category,
)
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
from services.authored_canvas import canvas_or_default, canvas_render_kwargs
from utils.hashing import sha256_prefixed

LOGGER = logging.getLogger(__name__)
_ORIGINAL_RENDER_HTML_TO_PNG = render_html_to_png
#: One mutex per deck. Analyze-fix, page-remake publication, and batch
#: publication all rewrite the same slides, so they take turns. Preview
#: generation deliberately does NOT take it — see the note there.
_ANALYZE_AND_FIX_LOCKS: weakref.WeakValueDictionary[uuid.UUID, asyncio.Lock] = (
    weakref.WeakValueDictionary()
)

# Categories that justify paying for slide OCR: each one can mean the reader
# lost text. Deliberately not every blocking category -- ``overlap``,
# ``text_escapes_card`` and ``text_intrudes_footer_zone`` misplace copy that OCR
# would still read back intact, so OCR adds cost and no evidence there.
_OCR_DOM_CATEGORIES = {"content", "overflow", "clipping", "off_canvas"}
_OCR_DETAIL_SIGNAL = re.compile(
    r"(?:text|title|label|number|numeric|digit|symbol|character|glyph|word|"
    r"missing|omitt|truncat|clip|crop|content\s+loss|"
    r"텍스트|제목|레이블|문자|숫자|기호|누락|생략|잘림|내용)",
    re.IGNORECASE,
)


# ---- Folded back from authored_quality_review_support.py (module-alias split) ----

def _merge_dom_repair_triggers(
    critiques: list[SlideCritique | None], local_reports: list[DomQualityReport]
) -> list[SlideCritique | None]:
    """Make required geometry defects start repair independently from VQA.

    VQA remains useful evidence, but it cannot veto deterministic clipping,
    off-canvas, overflow, card-escaping-text or unintended-overlap telemetry
    -- the categories the composition policy blocks on.  Only medium/high
    geometry defects are promoted; unavailable telemetry stays fail-closed and
    does not fabricate a repair target.
    """
    geometry_categories = BLOCKING_SAFETY_CATEGORIES
    merged = list(critiques)
    for index, report in enumerate(local_reports):
        promoted = [
            issue
            for issue in report.issues
            if issue.category in geometry_categories and issue.severity in {"high", "medium"}
        ]
        if not promoted:
            continue
        critique = merged[index] if index < len(merged) else None
        existing = list(critique.issues) if critique is not None else []
        existing_keys = {(issue.type, issue.severity, issue.detail) for issue in existing}
        for issue in promoted:
            # One registry decides this (``utils.authored_dom_categories``), so
            # a category a detector adds is projected here too instead of being
            # silently filed as "other" -- or, as in bench run 36, raising.
            issue_type = critique_type_for_category(issue.category)
            detail = f"DOM_{issue.category.upper()}: {issue.detail}"[:200]
            key = (issue_type, issue.severity, detail)
            if key in existing_keys:
                continue
            existing.append(CritiqueIssue(type=issue_type, severity=issue.severity, detail=detail))
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
            if issue_type in {"overflow", "placeholder"} or _OCR_DETAIL_SIGNAL.search(issue.detail or ""):
                signals.add(f"vqa:{issue_type}")
    if repair_detail is not None:
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
    task_state = inspect(task)
    if task_state.persistent and "data" in task_state.expired_attributes:
        # An importer rollback expires this task too, even with
        # expire_on_commit=False. Reload its durable checkpoint explicitly.
        await session.refresh(task, attribute_names=["data"])
    # The workspace release-review handler seeds a private, digest-only
    # pre-review checkpoint in task.data. Quality review status updates commit
    # independently and historically replaced the entire mapping, which made a
    # crash after slide fixes unable to prove which original HTML the V2 receipt
    # covered. Preserve only this explicitly namespaced checkpoint.
    release_review_base = (
        task.data.get("_workspace_release_review_base")
        if isinstance(task.data, dict)
        else None
    )
    task.status = status
    task.message = message
    if release_review_base is not None:
        task.data = {
            **(data if isinstance(data, dict) else {}),
            "_workspace_release_review_base": release_review_base,
        }
    else:
        task.data = data
    task.error = error
    task.updated_at = get_current_utc_datetime()
    session.add(task)
    await session.commit()


def _resolve_positions(request: AuthoredQualityReviewRequest, slide_count: int) -> list[int]:
    if request.scope == "all":
        return list(range(slide_count))
    if any((index >= slide_count for index in request.slide_indices)):
        raise ValueError("Slide index is outside the presentation")
    return list(request.slide_indices)


def _saved_brand(presentation: PresentationModel) -> Brand:
    theme = presentation.theme if isinstance(presentation.theme, dict) else {}
    language = str(theme.get("language") or presentation.language or "Korean")
    return Brand(
        topic=presentation.title or presentation.content or "Presentation",
        language=language,
        primary=str(theme.get("primary") or "#2563EB"),
        fonts=str(theme.get("fonts") or ("Noto Sans KR" if language.lower().startswith("ko") else "Inter")),
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
            {"slide_index": position, "issues": [issue.model_dump(mode="json") for issue in critique.issues]}
        )
    return results


async def _render_review_html(
    html: str, *, canvas: object | None = None
) -> tuple[bytes, Mapping | None]:
    """Render with co-located telemetry while preserving legacy test doubles."""
    render_kwargs = canvas_render_kwargs(*canvas_or_default(canvas))
    if render_html_to_png is not _ORIGINAL_RENDER_HTML_TO_PNG:
        png = await render_html_to_png(html, **render_kwargs)
        return (
            png,
            get_cached_browser_metrics(html, **render_kwargs),
        )
    return await render_html_to_png_with_metrics(html, **render_kwargs)


async def _hybrid_style_initial_review(
    *,
    pngs: list[bytes],
    contexts: list[str],
    positions: list[int],
    slide_count: int,
    local_reports: list[DomQualityReport],
    sampling_seed: str,
    scope: ReviewScope = "all",
) -> tuple[list[SlideCritique | None], dict, int, int, float, float]:
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
        "authored_quality_review %s", json.dumps(event, sort_keys=True, separators=(",", ":"))
    )


def _write_reviewed_png(presentation_id: uuid.UUID, slide_position: int, png: bytes) -> str:
    return publish_reviewed_png(presentation_id, slide_position, png)


# ---- Folded back from authored_quality_review_prepare.py (module-alias split) ----

async def prepare_legacy_review(ctx):
    ctx.review_started_at = time.perf_counter()
    ctx.presentation = await ctx.session.get(PresentationModel, ctx.presentation_id)
    if ctx.presentation is None:
        raise ValueError("Presentation not found")
    ctx.outline = ctx.presentation.get_presentation_outline()
    if ctx.outline is None or not ctx.outline.slides:
        raise ValueError("The saved AI-authored outline is missing")
    ctx.slide_rows = await _execute_sqlalchemy(
        ctx.session,
        select(SlideModel)
        .where(SlideModel.presentation == ctx.presentation_id)
        .order_by(SlideModel.index),
    )
    ctx.slides = list(ctx.slide_rows.scalars().all())
    if not ctx.slides or not ctx.presentation.is_authored() or (not all((slide.is_authored() for slide in ctx.slides))):
        raise ValueError("High-quality review supports AI-authored presentations only")
    if len(ctx.outline.slides) != len(ctx.slides):
        raise ValueError("Saved outline and slide counts do not match")
    ctx.positions = _resolve_positions(ctx.request, len(ctx.slides))
    ctx.targets = [ctx.slides[position] for position in ctx.positions]
    if any((not slide.html_content for slide in ctx.targets)):
        raise ValueError("One or more slides are missing their editable authored HTML")
    await _set_status(
        ctx.session, ctx.task, status="processing", message="슬라이드 렌더링을 준비하고 있습니다."
    )
    ctx.theme = ctx.presentation.theme if isinstance(ctx.presentation.theme, dict) else {}
    ctx.canvas = presentation_canvas_contract(ctx.theme)
    ctx.saved_style_id = str(ctx.theme.get("style") or "default")
    ctx.provenance_row = await ctx.session.get(AuthoredExactProvenance, ctx.presentation_id)
    ctx.exact_source_images: tuple[bytes, ...] | None = None
    if ctx.provenance_row is not None:
        ctx.private_style_id = (
            ctx.provenance_row.provenance.get("style_id") if isinstance(ctx.provenance_row.provenance, dict) else None
        )
        if not isinstance(ctx.private_style_id, str) or ctx.private_style_id != ctx.saved_style_id:
            raise ExactProvenanceError("Saved exact reconstruction style identity changed")
        ctx.style = resolve_authored_style(ctx.private_style_id)
        ctx.snapshot_id = ctx.provenance_row.provenance.get("source_snapshot_id")
        if ctx.snapshot_id is not None:
            if not isinstance(ctx.snapshot_id, str):
                raise ExactProvenanceError("Saved exact reconstruction source snapshot is invalid")
            try:
                ctx.style = await materialize_exact_source_style(
                    ctx.style, ctx.snapshot_id, expected_slide_count=len(ctx.slides)
                )
            except ExactSourceSnapshotError as error:
                raise ExactProvenanceError(
                    "Saved exact reconstruction source snapshot is unavailable"
                ) from error
        ctx.exact_source_images = verify_exact_provenance(
            ctx.provenance_row.provenance, ctx.style, expected_slide_count=len(ctx.slides)
        )
        ctx.saved_reference_mode = EXACT_RECONSTRUCTION
    else:
        ctx.legacy_reference_mode = ctx.theme.get("reference_mode")
        if (
            isinstance(ctx.legacy_reference_mode, str)
            and normalize_reference_mode(ctx.legacy_reference_mode) == EXACT_RECONSTRUCTION
        ):
            raise ExactProvenanceError("Saved exact reconstruction provenance is missing")
        ctx.style = resolve_authored_style(ctx.saved_style_id)
        if (
            normalize_reference_mode(getattr(ctx.style, "reference_mode", STYLE_REFERENCE))
            == EXACT_RECONSTRUCTION
        ):
            raise ExactProvenanceError("Saved exact reconstruction provenance is missing")
        ctx.saved_reference_mode = STYLE_REFERENCE
    # Reviewing re-authors slides against this plan, so it has to carry the
    # deck's own frozen chrome rather than the built-in defaults.
    header_contract = load_header_contract(ctx.presentation.theme)
    ctx.plan = prepare_authored_deck(
        ctx.outline,
        _saved_brand(ctx.presentation),
        ctx.style,
        page_number_mode=page_number_mode_for(header_contract),
        header_contract=header_contract,
        # Reviewing a page re-authors it, so it needs the same chapter prefix
        # and the same forced role the first pass gave it.
        chapter_plan=load_chapter_plan(ctx.presentation.theme),
        # The persisted plan is the original deck decision.  Recomputing it
        # from a review subset would give an edited page a different right to
        # use a generated picture than its untouched siblings.
        illustration_plan=load_illustration_plan(ctx.presentation.theme),
        infographic_plan=load_infographic_plan(ctx.presentation.theme),
        canvas=ctx.canvas,
    )
    ctx.plan_reference_mode = normalize_reference_mode(
        getattr(ctx.plan, "reference_mode", ctx.saved_reference_mode)
    )
    if ctx.plan_reference_mode != ctx.saved_reference_mode:
        raise ValueError("Saved authored reference mode does not match its source style")
    ctx.contents = [ctx.outline.slides[position].content for position in ctx.positions]
    ctx.roles = [
        _saved_role(target, ctx.plan.roles[position]) for position, target in zip(ctx.positions, ctx.targets)
    ]
    ctx.persisted_htmls = [str(target.html_content) for target in ctx.targets]
    ctx.htmls = [ensure_authored_qids(html) for html in ctx.persisted_htmls]
    ctx.exact_reconstruction = ctx.plan_reference_mode == EXACT_RECONSTRUCTION
    ctx.illustration_reauthor_kwargs = {}
    illustration_slots = getattr(ctx.plan, "illustration_slots", ())
    if not ctx.exact_reconstruction:
        # Slots must be aligned to ``ctx.htmls`` / ``ctx.contents`` rather than
        # the persisted outline: a subset review is addressed by local indices.
        if illustration_slots:
            ctx.illustration_reauthor_kwargs["illustration_slots"] = [
                illustration_slots[position] for position in ctx.positions
            ]
        infographic_briefs = getattr(ctx.plan, "infographic_briefs", ())
        if infographic_briefs:
            ctx.illustration_reauthor_kwargs["infographic_briefs"] = [
                infographic_briefs[position] for position in ctx.positions
            ]
            ctx.illustration_reauthor_kwargs["infographic_assets"] = (
                build_infographic_asset_context(style=ctx.style, brand=ctx.plan.brand)
            )
        if deck_has_picture_source(ctx.style):
            ctx.illustration_reauthor_kwargs["image_service"] = (
                ImageGenerationService(get_images_directory())
            )
    ctx.final_review_batch_count = 0
    if ctx.exact_reconstruction:
        ctx.planned_references = getattr(ctx.plan, "reference_images", None)
        if ctx.planned_references is None or len(ctx.planned_references) != len(ctx.slides):
            raise ValueError("Exact reconstruction review requires a complete source mapping")
        if any((len(references) != 1 for references in ctx.planned_references)):
            raise ValueError("Exact reconstruction review requires exactly one source per slide")
        ctx.target_references = [ctx.planned_references[position] for position in ctx.positions]
    ctx.source_images = (
        [ctx.exact_source_images[position] for position in ctx.positions]
        if ctx.exact_reconstruction and ctx.exact_source_images is not None
        else None
    )
    ctx.review_metadata = _review_observation_metadata()
    ctx.ocr_runtime = _RequestOcrRuntime(_ocr_language(ctx.plan.brand.language))
    await _set_status(
        ctx.session, ctx.task, status="processing", message=f"{len(ctx.targets)}개 슬라이드를 렌더링하고 있습니다."
    )
    ctx.render_started_at = time.perf_counter()
    ctx.rendered = list(await asyncio.gather(*(
        _render_review_html(html, canvas=ctx.canvas) for html in ctx.htmls
    )))
    ctx.pngs = [png for png, _metrics in ctx.rendered]
    ctx.browser_metrics = [metrics for _png, metrics in ctx.rendered]
    ctx.render_ms = (time.perf_counter() - ctx.render_started_at) * 1000
    ctx.local_started_at = time.perf_counter()
    ctx.local_reports = [
        inspect_authored_dom(html, metrics, reference_mode=ctx.plan_reference_mode)
        for html, metrics in zip(ctx.htmls, ctx.browser_metrics)
    ]
    ctx.local_inspection_ms = (time.perf_counter() - ctx.local_started_at) * 1000
    await _set_status(
        ctx.session, ctx.task, status="processing", message="잘림, 겹침, 정렬, 대비를 병렬로 검사하고 있습니다."
    )
    ctx.critique_kwargs = {}
    if ctx.exact_reconstruction:
        ctx.critique_kwargs = {"source_images": ctx.source_images, "reference_mode": ctx.plan_reference_mode}
    ctx.critique_contexts = [content[:240] for content in ctx.contents]
    ctx.routing_metadata: dict = {}
    ctx.montage_ms = 0.0
    ctx.use_hybrid_deck_routing = (
        ctx.request.scope in {"all", "subset"} and (not ctx.exact_reconstruction) and (len(ctx.targets) >= 6)
    )
    if ctx.use_hybrid_deck_routing:
        (
            ctx.initial_critiques,
            ctx.routing_metadata,
            ctx.initial_cache_hits,
            ctx.initial_cache_misses,
            ctx.montage_ms,
            ctx.initial_critique_ms,
        ) = await _hybrid_style_initial_review(
            pngs=ctx.pngs,
            contexts=ctx.critique_contexts,
            positions=ctx.positions,
            slide_count=len(ctx.slides),
            local_reports=ctx.local_reports,
            sampling_seed=str(ctx.presentation_id),
            scope=ctx.request.scope,
        )
    else:
        ctx.routing_plan = plan_authored_review_routing(
            scope=ctx.request.scope,
            slide_count=len(ctx.slides),
            local_issue_indices=[
                ctx.positions[index] for index, report in enumerate(ctx.local_reports) if report.needs_attention
            ],
            current_slide_index=ctx.positions[0] if ctx.request.scope == "current" else None,
            target_indices=ctx.positions,
            sampling_seed=str(ctx.presentation_id),
        )
        ctx.routing_metadata = {
            "plan": ctx.routing_plan.as_dict(),
            "escalation": None,
            "montage_promoted_batches": [],
            "montage_vqa_count": 0,
            "detail_vqa_count": len(ctx.targets),
            "montage_vqa_batch_count": 0,
            "detail_vqa_batch_count": 1 if ctx.targets else 0,
            "vqa_batch_count": 1 if ctx.targets else 0,
        }
        ctx.initial_cache_hits, ctx.initial_cache_misses = _observe_critique_cache(
            ctx.pngs, ctx.critique_contexts, source_images=ctx.source_images, reference_mode=ctx.plan_reference_mode
        )
        ctx.initial_critique_started_at = time.perf_counter()
        ctx.initial_critiques = list(
            await critique_authored(
                ctx.pngs,
                contexts=ctx.critique_contexts,
                # One deck-level `vqa` scope covers every slide, so each needs
                # its own provider-admission identity.
                slide_indices=ctx.positions,
                **ctx.critique_kwargs,
            )
        )
        if len(ctx.initial_critiques) != len(ctx.targets):
            ctx.initial_critiques = (ctx.initial_critiques + [None] * len(ctx.targets))[: len(ctx.targets)]
        ctx.initial_critique_ms = (time.perf_counter() - ctx.initial_critique_started_at) * 1000
    ctx.initial_critiques = _merge_dom_repair_triggers(ctx.initial_critiques, ctx.local_reports)
    ctx.initial_indeterminate_count = sum(
        (_critique_unusable_reason(critique) == "indeterminate" for critique in ctx.initial_critiques)
    )
    ctx.ocr_checks: list[dict[str, Any]] = []
    for ctx.local_index, (ctx.position, ctx.local_report, ctx.initial_critique) in enumerate(  # noqa: B020
        zip(ctx.positions, ctx.local_reports, ctx.initial_critiques)
    ):
        ctx.ocr_checks.append(
            await _capture_initial_ocr(
                runtime=ctx.ocr_runtime,
                slide_index=ctx.position,
                reference_mode=ctx.plan_reference_mode,
                source_image=ctx.source_images[ctx.local_index] if ctx.source_images is not None else None,
                baseline_image=ctx.pngs[ctx.local_index],
                baseline_metrics=ctx.browser_metrics[ctx.local_index],
                signals=_ocr_route_signals(ctx.local_report, ctx.initial_critique),
            )
        )
    ctx.ocr_summary = ctx.ocr_runtime.summary()
    _log_review_event(
        reference_mode=ctx.plan_reference_mode,
        stage="initial_review",
        slide_count=len(ctx.pngs),
        model=ctx.review_metadata["model"],
        prompt_version=ctx.review_metadata["prompt_version"],
        cache_version=ctx.review_metadata["cache_version"],
        critique_batch_count=ctx.routing_metadata["vqa_batch_count"],
        critique_slide_request_count=ctx.initial_cache_misses,
        cache_hits=ctx.initial_cache_hits,
        cache_misses=ctx.initial_cache_misses,
        render_ms=ctx.render_ms,
        critique_ms=ctx.initial_critique_ms,
        indeterminate_count=ctx.initial_indeterminate_count,
        **ctx.ocr_summary,
    )
    if ctx.exact_reconstruction and any(
        (_critique_score(critique) is None for critique in ctx.initial_critiques)
    ):
        raise RuntimeError("Exact reconstruction comparison failed; no slide was treated as clean")
    ctx.initial_issues = _serialize_critiques(ctx.positions, ctx.initial_critiques)


# ---- Folded back from authored_quality_review_clean.py (module-alias split) ----

async def finish_clean_legacy_review(ctx):
    if not ctx.initial_issues:
        ctx.message = (
            "검수가 완료되었습니다. 수정이 필요한 슬라이드가 없습니다."
            if not ctx.initial_issues
            else f"검수가 완료되었습니다. {len(ctx.initial_issues)}개 슬라이드에서 문제를 확인했습니다."
        )
        ctx.total_ms = (time.perf_counter() - ctx.review_started_at) * 1000
        ctx.local_high_remaining = sum(
            (any((issue.severity == "high" for issue in report.issues)) for report in ctx.local_reports)
        )
        _log_review_event(
            reference_mode=ctx.plan_reference_mode,
            stage="completed",
            slide_count=len(ctx.targets),
            model=ctx.review_metadata["model"],
            prompt_version=ctx.review_metadata["prompt_version"],
            cache_version=ctx.review_metadata["cache_version"],
            critique_batch_count=ctx.routing_metadata["vqa_batch_count"],
            critique_slide_request_count=ctx.initial_cache_misses,
            cache_hits=ctx.initial_cache_hits,
            cache_misses=ctx.initial_cache_misses,
            render_ms=ctx.render_ms,
            local_inspection_ms=ctx.local_inspection_ms,
            montage_ms=ctx.montage_ms,
            critique_ms=ctx.initial_critique_ms,
            total_ms=ctx.total_ms,
            local_high_remaining=ctx.local_high_remaining,
            indeterminate_count=ctx.initial_indeterminate_count,
            **ctx.ocr_summary,
        )
        await _set_status(
            ctx.session,
            ctx.task,
            status="completed",
            message=ctx.message,
            data={
                "presentation_id": str(ctx.presentation_id),
                "mode": ctx.request.mode,
                "scope": ctx.request.scope,
                "review_policy": dict(ctx.review_metadata),
                "reviewed_count": len(ctx.targets),
                "issue_slide_count": len(ctx.initial_issues),
                "issues": ctx.initial_issues,
                "fixed_count": 0,
                "fixed_slide_indices": [],
                "remaining_issue_count": len(ctx.initial_issues),
                "remaining_slide_indices": [issue["slide_index"] for issue in ctx.initial_issues],
                "remaining_issues": ctx.initial_issues,
                "version_saved": False,
                "slide_results": _build_slide_results(
                    positions=ctx.positions,
                    initial_critiques=ctx.initial_critiques,
                    final_critiques=ctx.initial_critiques,
                    repair_details=[],
                ),
                "local_checks": [
                    {
                        "slide_index": position,
                        "telemetry_status": report.telemetry_status,
                        "inspected_elements": report.inspected_elements,
                        "issues": [issue.to_dict() for issue in report.issues],
                    }
                    for position, report in zip(ctx.positions, ctx.local_reports)
                ],
                "ocr_checks": ctx.ocr_checks,
                "routing": ctx.routing_metadata,
                "metrics": {
                    "stage_timings_ms": {
                        "render": round(ctx.render_ms, 3),
                        "local_inspection": round(ctx.local_inspection_ms, 3),
                        "montage_vqa": round(ctx.montage_ms, 3),
                        "detail_vqa": round(ctx.initial_critique_ms, 3),
                        "ocr": ctx.ocr_summary["ocr_total_ms"],
                    },
                    "total_ms": round(ctx.total_ms, 3),
                    "cache_hits": ctx.initial_cache_hits,
                    "vqa_calls": ctx.initial_cache_misses,
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
                    "legacy_preserved": len(ctx.targets),
                    **ctx.ocr_summary,
                },
            },
        )
        return True
    return False


# ---- Folded back from authored_quality_review_revise.py (module-alias split) ----

async def revise_legacy_review(ctx):
    canvas = getattr(ctx, "canvas", None)
    render_kwargs = canvas_render_kwargs(*canvas_or_default(canvas))
    await _set_status(
        ctx.session,
        ctx.task,
        status="processing",
        message=f"문제가 발견된 {len(ctx.initial_issues)}개 슬라이드를 자동 수정하고 있습니다.",
    )
    ctx.revise_kwargs = {
        "initial_critiques": ctx.initial_critiques,
        "return_details": True,
        "canvas": canvas,
    }
    # Test doubles substitute a lightweight plan object, and the deck-plan
    # dataclass only carries prefixes for a chaptered deck, so both absences
    # mean the same thing: this review re-authors without a breadcrumb.
    plan_prefixes = getattr(ctx.plan, "chapter_prefixes", ())
    if plan_prefixes:
        ctx.revise_kwargs["chapter_prefixes"] = [
            plan_prefixes[position] for position in ctx.positions
        ]
    ctx.candidate_decisions: list[dict] = []
    if ctx.exact_reconstruction:
        ctx.revise_kwargs["reference_images_by_slide"] = ctx.target_references
    ctx.revise_kwargs.update(getattr(ctx, "illustration_reauthor_kwargs", {}))
    ctx.revision_started_at = time.perf_counter()
    ctx.revision_result = await revise_authored_deck(
        ctx.htmls,
        ctx.pngs,
        ctx.contents,
        ctx.roles,
        ctx.plan.brand,
        ctx.plan.design_system,
        max_cycles=1,
        style=ctx.style,
        slide_indices=ctx.positions,
        total_slides=len(ctx.slides),
        **ctx.revise_kwargs,
    )
    if len(ctx.revision_result) == 4:
        ctx.revised_htmls, ctx.revised_pngs, ctx.candidate_local_indices, ctx.repair_details = ctx.revision_result
    else:
        ctx.revised_htmls, ctx.revised_pngs, ctx.candidate_local_indices = ctx.revision_result
        ctx.repair_details = []
    ctx.candidate_local_indices = list(dict.fromkeys(ctx.candidate_local_indices))
    if any((type(index) is not int or index < 0 or index >= len(ctx.targets) for index in ctx.candidate_local_indices)):
        raise RuntimeError("Authored quality review returned an invalid candidate index")
    ctx.revision_ms = (time.perf_counter() - ctx.revision_started_at) * 1000
    ctx.candidate_browser_metrics = {
        index: get_cached_browser_metrics(
            ctx.revised_htmls[index],
            **render_kwargs,
        ) for index in ctx.candidate_local_indices
    }
    ctx.candidate_local_reports = {
        index: inspect_authored_dom(
            ctx.revised_htmls[index], ctx.candidate_browser_metrics[index], reference_mode=ctx.plan_reference_mode
        )
        for index in ctx.candidate_local_indices
    }
    ctx.candidate_local_decisions = {
        index: _local_acceptance_decision(ctx.local_reports[index], report)
        for index, report in ctx.candidate_local_reports.items()
    }
    ctx.fallback_local_reports: dict[int, DomQualityReport] = {}
    _log_review_event(
        reference_mode=ctx.plan_reference_mode,
        stage="correction",
        slide_count=len(ctx.targets),
        attempted=len(ctx.initial_issues),
        accepted=len(ctx.candidate_local_indices),
        rejection_reasons={"no_candidate": len(ctx.initial_issues) - len(ctx.candidate_local_indices)}
        if len(ctx.candidate_local_indices) < len(ctx.initial_issues)
        else None,
        model=ctx.review_metadata["model"],
        prompt_version=ctx.review_metadata["prompt_version"],
        cache_version=ctx.review_metadata["cache_version"],
        render_ms=ctx.render_ms,
        revision_ms=ctx.revision_ms,
        **ctx.ocr_runtime.summary(),
    )
    await _set_status(
        ctx.session, ctx.task, status="processing", message="수정 결과를 다시 검증하고 있습니다."
    )


# ---- Folded back from authored_quality_review_exact.py (module-alias split) ----

async def review_exact_candidates(ctx):
    ctx.accepted_local_indices: list[int] = []
    ctx.chosen_critiques = list(ctx.initial_critiques)
    ctx.rejection_reasons: Counter[str] = Counter()
    ctx.final_cache_hits = 0
    ctx.final_cache_misses = 0
    ctx.final_critique_ms = 0.0
    if ctx.candidate_local_indices:
        ctx.final_review_batch_count = 1
        ctx.candidate_pngs = [ctx.revised_pngs[index] for index in ctx.candidate_local_indices]
        ctx.candidate_contexts = [ctx.contents[index][:240] for index in ctx.candidate_local_indices]
        ctx.candidate_sources = [ctx.source_images[index] for index in ctx.candidate_local_indices]
        ctx.final_cache_hits, ctx.final_cache_misses = _observe_critique_cache(
            ctx.candidate_pngs,
            ctx.candidate_contexts,
            source_images=ctx.candidate_sources,
            reference_mode=ctx.plan_reference_mode,
        )
        ctx.final_critique_started_at = time.perf_counter()
        ctx.candidate_critiques = await critique_authored(
            ctx.candidate_pngs,
            contexts=ctx.candidate_contexts,
            source_images=ctx.candidate_sources,
            reference_mode=ctx.plan_reference_mode,
            slide_indices=[
                ctx.positions[index] for index in ctx.candidate_local_indices
            ],
            # The second review of the same slides inside the same scope.
            critique_round="final",
        )
        ctx.final_critique_ms = (time.perf_counter() - ctx.final_critique_started_at) * 1000
        for ctx.offset, ctx.local_index in enumerate(ctx.candidate_local_indices):  # noqa: B020
            ctx.final_critique = (
                ctx.candidate_critiques[ctx.offset] if ctx.offset < len(ctx.candidate_critiques) else None
            )
            ctx.accepted, ctx.reason = _acceptance_decision(
                ctx.initial_critiques[ctx.local_index], ctx.final_critique
            )
            ctx.local_accepted, ctx.local_reason = ctx.candidate_local_decisions[ctx.local_index]
            if ctx.accepted and (not ctx.local_accepted):
                ctx.accepted, ctx.reason = (False, ctx.local_reason)
            ctx.repair_detail = next(
                (detail for detail in reversed(ctx.repair_details) if detail.get("local_index") == ctx.local_index), {}
            )
            ctx.path = str(ctx.repair_detail.get("path") or "none")
            ctx.ocr_required, ctx.ocr_accepted, ctx.ocr_reason = await _evaluate_candidate_ocr(
                runtime=ctx.ocr_runtime,
                entry=ctx.ocr_checks[ctx.local_index],
                reference_mode=ctx.plan_reference_mode,
                source_image=ctx.source_images[ctx.local_index],
                baseline_image=ctx.pngs[ctx.local_index],
                candidate_image=ctx.revised_pngs[ctx.local_index],
                baseline_metrics=ctx.browser_metrics[ctx.local_index],
                candidate_metrics=ctx.candidate_browser_metrics.get(ctx.local_index),
                signals=_ocr_route_signals(
                    ctx.candidate_local_reports.get(ctx.local_index), ctx.final_critique, ctx.repair_detail
                ),
                path=ctx.path,
            )
            if ctx.accepted and ctx.ocr_required and (not ctx.ocr_accepted):
                ctx.accepted, ctx.reason = (False, str(ctx.ocr_reason or "ocr_unavailable"))
            ctx.candidate_decisions.append(
                {
                    "slide_index": ctx.positions[ctx.local_index],
                    "accepted": ctx.accepted,
                    "reason": ctx.reason,
                    "attempted": True,
                    "candidate_proposed": True,
                    "candidate_rendered": True,
                    "validation_status": "passed" if ctx.accepted else "rejected",
                    "apply_status": "not_started",
                    "publish_status": "not_started",
                    "post_verify_status": "not_started",
                    "applied": False,
                    "rolled_back": False,
                    "rejection_reason": None if ctx.accepted else ctx.reason,
                    "path": ctx.path,
                }
            )
            if ctx.accepted:
                ctx.accepted_local_indices.append(ctx.local_index)
                ctx.chosen_critiques[ctx.local_index] = ctx.final_critique
            else:
                ctx.rejection_reasons[ctx.reason] += 1
        _log_review_event(
            reference_mode=ctx.plan_reference_mode,
            stage="final_review",
            slide_count=len(ctx.candidate_local_indices),
            attempted=len(ctx.candidate_local_indices),
            model=ctx.review_metadata["model"],
            prompt_version=ctx.review_metadata["prompt_version"],
            cache_version=ctx.review_metadata["cache_version"],
            critique_batch_count=1,
            critique_slide_request_count=ctx.final_cache_misses,
            cache_hits=ctx.final_cache_hits,
            cache_misses=ctx.final_cache_misses,
            render_ms=ctx.render_ms,
            critique_ms=ctx.final_critique_ms,
            revision_ms=ctx.revision_ms,
            indeterminate_count=sum(
                (
                    _critique_unusable_reason(critique) == "indeterminate"
                    for critique in ctx.candidate_critiques
                )
            ),
            **ctx.ocr_runtime.summary(),
        )
    ctx.fixed_local_indices = ctx.accepted_local_indices
    ctx.final_critiques = ctx.chosen_critiques
    _log_review_event(
        reference_mode=ctx.plan_reference_mode,
        stage="acceptance",
        slide_count=len(ctx.targets),
        attempted=len(ctx.candidate_local_indices),
        accepted=len(ctx.accepted_local_indices),
        rejection_reasons=ctx.rejection_reasons,
        model=ctx.review_metadata["model"],
        prompt_version=ctx.review_metadata["prompt_version"],
        cache_version=ctx.review_metadata["cache_version"],
        render_ms=ctx.render_ms,
        revision_ms=ctx.revision_ms,
        indeterminate_count=sum(
            (_critique_unusable_reason(critique) == "indeterminate" for critique in ctx.final_critiques)
        ),
        **ctx.ocr_runtime.summary(),
    )


# ---- Folded back from authored_quality_review_style.py (module-alias split) ----

async def review_style_candidates(ctx):
    canvas = getattr(ctx, "canvas", None)
    render_kwargs = canvas_render_kwargs(*canvas_or_default(canvas))
    ctx.accepted_local_indices = []
    ctx.chosen_critiques = list(ctx.initial_critiques)
    ctx.comparison_metadata = _style_comparison_observation_metadata()
    ctx.final_critique_ms = 0.0
    ctx.candidate_critiques: list[SlideCritique | None] = []
    ctx.final_cache_hits = 0
    ctx.final_cache_misses = 0
    ctx.fallback_comparison_batch_count = 0
    ctx.repair_by_index = {
        detail.get("local_index"): detail
        for detail in ctx.repair_details
        if isinstance(detail, Mapping) and isinstance(detail.get("local_index"), int)
    }
    if ctx.candidate_local_indices:
        ctx.baseline_candidate_pngs = [ctx.pngs[index] for index in ctx.candidate_local_indices]
        ctx.candidate_pngs = [ctx.revised_pngs[index] for index in ctx.candidate_local_indices]
        ctx.candidate_contexts = [ctx.contents[index][:240] for index in ctx.candidate_local_indices]
        ctx.final_cache_hits, ctx.final_cache_misses = _observe_style_comparison_cache(
            ctx.baseline_candidate_pngs, ctx.candidate_pngs, ctx.candidate_contexts
        )
        ctx.final_critique_started_at = time.perf_counter()
        ctx.candidate_critiques = list(
            await critique_authored_style_comparison(
                ctx.baseline_candidate_pngs, ctx.candidate_pngs, contexts=ctx.candidate_contexts
            )
        )
        ctx.final_critique_ms = (time.perf_counter() - ctx.final_critique_started_at) * 1000
        for ctx.offset, ctx.local_index in enumerate(ctx.candidate_local_indices):  # noqa: B020
            ctx.final_critique = (
                ctx.candidate_critiques[ctx.offset] if ctx.offset < len(ctx.candidate_critiques) else None
            )
            ctx.accepted, ctx.reason = _style_acceptance_decision(
                ctx.initial_critiques[ctx.local_index], ctx.final_critique
            )
            ctx.local_accepted, ctx.local_reason = ctx.candidate_local_decisions[ctx.local_index]
            if ctx.accepted and (not ctx.local_accepted):
                ctx.accepted, ctx.reason = (False, ctx.local_reason)
            ctx.path = str(ctx.repair_by_index.get(ctx.local_index, {}).get("path") or "none")
            ctx.repair_detail = ctx.repair_by_index.get(ctx.local_index, {})
            ctx.ocr_required, ctx.ocr_accepted, ctx.ocr_reason = await _evaluate_candidate_ocr(
                runtime=ctx.ocr_runtime,
                entry=ctx.ocr_checks[ctx.local_index],
                reference_mode=ctx.plan_reference_mode,
                source_image=None,
                baseline_image=ctx.pngs[ctx.local_index],
                candidate_image=ctx.revised_pngs[ctx.local_index],
                baseline_metrics=ctx.browser_metrics[ctx.local_index],
                candidate_metrics=ctx.candidate_browser_metrics.get(ctx.local_index),
                signals=_ocr_route_signals(
                    ctx.candidate_local_reports.get(ctx.local_index), ctx.final_critique, ctx.repair_detail
                ),
                path=ctx.path,
            )
            if ctx.accepted and ctx.ocr_required and (not ctx.ocr_accepted):
                ctx.accepted, ctx.reason = (False, str(ctx.ocr_reason or "ocr_unavailable"))
            ctx.candidate_decisions.append(
                {
                    "slide_index": ctx.positions[ctx.local_index],
                    "accepted": ctx.accepted,
                    "reason": ctx.reason,
                    "attempted": True,
                    "candidate_proposed": True,
                    "candidate_rendered": True,
                    "validation_status": "passed" if ctx.accepted else "rejected",
                    "apply_status": "not_started",
                    "publish_status": "not_started",
                    "post_verify_status": "not_started",
                    "applied": False,
                    "rolled_back": False,
                    "rejection_reason": None if ctx.accepted else ctx.reason,
                    "path": ctx.path,
                }
            )
            if ctx.accepted:
                ctx.accepted_local_indices.append(ctx.local_index)
                ctx.chosen_critiques[ctx.local_index] = ctx.final_critique
    ctx.fallback_eligible_reasons = {
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
    ctx.decisions_by_position = {decision["slide_index"]: decision for decision in ctx.candidate_decisions}
    ctx.fallback_local_indices = [
        local_index
        for local_index in ctx.candidate_local_indices
        if ctx.decisions_by_position[ctx.positions[local_index]]["reason"] in ctx.fallback_eligible_reasons
        and ctx.decisions_by_position[ctx.positions[local_index]]["path"] == "local_patch"
    ]
    if ctx.fallback_local_indices:
        ctx.clean_critique = SlideCritique(needs_fix=False, issues=[])
        ctx.fallback_initial_critiques = [
            ctx.initial_critiques[index] if index in ctx.fallback_local_indices else ctx.clean_critique
            for index in range(len(ctx.targets))
        ]
        ctx.fallback_started_at = time.perf_counter()
        # A test double substitutes a lightweight plan, and the deck-plan
        # dataclass carries prefixes only for a chaptered deck; both absences
        # mean this fallback re-authors without a breadcrumb.
        fallback_prefixes = getattr(ctx.plan, "chapter_prefixes", ())
        ctx.fallback_result = await revise_authored_deck(
            ctx.htmls,
            ctx.pngs,
            ctx.contents,
            ctx.roles,
            ctx.plan.brand,
            ctx.plan.design_system,
            max_cycles=1,
            style=ctx.style,
            slide_indices=ctx.positions,
            total_slides=len(ctx.slides),
            initial_critiques=ctx.fallback_initial_critiques,
            return_details=True,
            force_full_reauthor_indices=ctx.fallback_local_indices,
            canvas=canvas,
            **getattr(ctx, "illustration_reauthor_kwargs", {}),
            **(
                {
                    "chapter_prefixes": [
                        fallback_prefixes[position] for position in ctx.positions
                    ]
                }
                if fallback_prefixes
                else {}
            ),
        )
        ctx.revision_ms += (time.perf_counter() - ctx.fallback_started_at) * 1000
        if len(ctx.fallback_result) == 4:
            ctx.legacy_fallback_result = False
            ctx.fallback_htmls, ctx.fallback_pngs, ctx.fallback_candidate_indices, ctx.fallback_repair_details = (
                ctx.fallback_result
            )
        else:
            ctx.legacy_fallback_result = True
            ctx.fallback_htmls, ctx.fallback_pngs, ctx.fallback_candidate_indices = ctx.fallback_result
            ctx.fallback_repair_details = []
        ctx.fallback_candidate_indices = list(dict.fromkeys(ctx.fallback_candidate_indices))
        if any(
            (
                type(index) is not int or index not in ctx.fallback_local_indices
                for index in ctx.fallback_candidate_indices
            )
        ):
            raise RuntimeError("Authored quality review returned an invalid fallback candidate index")
        if ctx.legacy_fallback_result:
            ctx.fallback_candidate_set = set(ctx.fallback_candidate_indices)
            ctx.fallback_repair_details = []
            for ctx.index in ctx.fallback_local_indices:  # noqa: B020
                ctx.succeeded = ctx.index in ctx.fallback_candidate_set
                ctx.failure = None
                if not ctx.succeeded:
                    ctx.failure = {
                        "stage": "full_reauthor",
                        "code": "fallback_failed",
                        "error_type": "UnknownFallbackFailure",
                    }
                ctx.fallback_repair_details.append(
                    {
                        "local_index": ctx.index,
                        "slide_index": ctx.positions[ctx.index],
                        "path": "full_fallback",
                        "attempted": True,
                        "succeeded": ctx.succeeded,
                        "failure_detail": ctx.failure,
                        "patch": {"attempted": False, "succeeded": False, "failure_detail": None},
                        "fallback": {"attempted": True, "succeeded": ctx.succeeded, "failure_detail": ctx.failure},
                        "element_changes": [],
                    }
                )
        ctx.repair_details.extend(ctx.fallback_repair_details)
        ctx.fallback_detail_by_index = {
            detail.get("local_index"): detail
            for detail in ctx.fallback_repair_details
            if isinstance(detail, Mapping) and isinstance(detail.get("local_index"), int)
        }
        ctx.fallback_browser_metrics = {
            index: get_cached_browser_metrics(
                ctx.fallback_htmls[index],
                **render_kwargs,
            )
            for index in ctx.fallback_candidate_indices
        }
        ctx.fallback_local_reports = {
            index: inspect_authored_dom(
                ctx.fallback_htmls[index], ctx.fallback_browser_metrics[index], reference_mode=ctx.plan_reference_mode
            )
            for index in ctx.fallback_candidate_indices
        }
        ctx.fallback_local_decisions = {
            index: _local_acceptance_decision(ctx.local_reports[index], report)
            for index, report in ctx.fallback_local_reports.items()
        }
        ctx.fallback_critiques: list[SlideCritique | None] = []
        if ctx.fallback_candidate_indices:
            ctx.fallback_comparison_batch_count = 1
            ctx.fallback_baseline_pngs = [ctx.pngs[index] for index in ctx.fallback_candidate_indices]
            ctx.fallback_candidate_pngs = [ctx.fallback_pngs[index] for index in ctx.fallback_candidate_indices]
            ctx.fallback_contexts = [ctx.contents[index][:240] for index in ctx.fallback_candidate_indices]
            ctx.cache_hits, ctx.cache_misses = _observe_style_comparison_cache(
                ctx.fallback_baseline_pngs, ctx.fallback_candidate_pngs, ctx.fallback_contexts
            )
            ctx.final_cache_hits += ctx.cache_hits
            ctx.final_cache_misses += ctx.cache_misses
            ctx.fallback_review_started_at = time.perf_counter()
            ctx.fallback_critiques = list(
                await critique_authored_style_comparison(
                    ctx.fallback_baseline_pngs, ctx.fallback_candidate_pngs, contexts=ctx.fallback_contexts
                )
            )
            ctx.final_critique_ms += (time.perf_counter() - ctx.fallback_review_started_at) * 1000
            ctx.candidate_critiques.extend(ctx.fallback_critiques)
        ctx.fallback_critique_by_index = {
            local_index: ctx.fallback_critiques[offset] if offset < len(ctx.fallback_critiques) else None
            for offset, local_index in enumerate(ctx.fallback_candidate_indices)
        }
        for ctx.local_index in ctx.fallback_local_indices:  # noqa: B020
            ctx.decision = ctx.decisions_by_position[ctx.positions[ctx.local_index]]
            ctx.initial_rejection_reason = ctx.decision["reason"]
            ctx.final_critique = ctx.fallback_critique_by_index.get(ctx.local_index)
            if ctx.local_index not in ctx.fallback_candidate_indices:
                ctx.failure = ctx.fallback_detail_by_index.get(ctx.local_index, {}).get("failure_detail") or {}
                ctx.accepted, ctx.reason = (False, str(ctx.failure.get("code") or "fallback_failed"))
            else:
                ctx.accepted, ctx.reason = _style_acceptance_decision(
                    ctx.initial_critiques[ctx.local_index], ctx.final_critique
                )
                ctx.local_accepted, ctx.local_reason = ctx.fallback_local_decisions[ctx.local_index]
                if ctx.accepted and (not ctx.local_accepted):
                    ctx.accepted, ctx.reason = (False, ctx.local_reason)
                ctx.fallback_detail = ctx.fallback_detail_by_index.get(ctx.local_index, {})
                ctx.ocr_required, ctx.ocr_accepted, ctx.ocr_reason = await _evaluate_candidate_ocr(
                    runtime=ctx.ocr_runtime,
                    entry=ctx.ocr_checks[ctx.local_index],
                    reference_mode=ctx.plan_reference_mode,
                    source_image=None,
                    baseline_image=ctx.pngs[ctx.local_index],
                    candidate_image=ctx.fallback_pngs[ctx.local_index],
                    baseline_metrics=ctx.browser_metrics[ctx.local_index],
                    candidate_metrics=ctx.fallback_browser_metrics.get(ctx.local_index),
                    signals=_ocr_route_signals(
                        ctx.fallback_local_reports.get(ctx.local_index), ctx.final_critique, ctx.fallback_detail
                    ),
                    path="full_fallback",
                )
                if ctx.accepted and ctx.ocr_required and (not ctx.ocr_accepted):
                    ctx.accepted, ctx.reason = (False, str(ctx.ocr_reason or "ocr_unavailable"))
            ctx.decision.update(
                {
                    "accepted": ctx.accepted,
                    "reason": ctx.reason,
                    "attempted": True,
                    "candidate_proposed": True,
                    "candidate_rendered": ctx.local_index in ctx.fallback_candidate_indices,
                    "validation_status": "passed" if ctx.accepted else "rejected",
                    "apply_status": "not_started",
                    "publish_status": "not_started",
                    "post_verify_status": "not_started",
                    "applied": False,
                    "rolled_back": False,
                    "rejection_reason": None if ctx.accepted else ctx.reason,
                    "path": "full_fallback",
                    "initial_rejection_reason": ctx.initial_rejection_reason,
                }
            )
            if ctx.accepted:
                ctx.revised_htmls[ctx.local_index] = ctx.fallback_htmls[ctx.local_index]
                ctx.revised_pngs[ctx.local_index] = ctx.fallback_pngs[ctx.local_index]
                ctx.accepted_local_indices.append(ctx.local_index)
                ctx.chosen_critiques[ctx.local_index] = ctx.final_critique
    ctx.rejection_reasons = Counter(
        (decision["reason"] for decision in ctx.candidate_decisions if not decision["accepted"])
    )
    ctx.fixed_local_indices = ctx.accepted_local_indices
    ctx.final_critiques = ctx.chosen_critiques
    ctx.final_review_batch_count = (1 if ctx.candidate_local_indices else 0) + ctx.fallback_comparison_batch_count
    _log_review_event(
        reference_mode=ctx.plan_reference_mode,
        stage="final_review",
        slide_count=len(ctx.candidate_critiques),
        attempted=len(ctx.candidate_critiques),
        model=ctx.comparison_metadata["model"],
        prompt_version=ctx.comparison_metadata["prompt_version"],
        cache_version=ctx.comparison_metadata["cache_version"],
        critique_batch_count=(1 if ctx.candidate_local_indices else 0) + ctx.fallback_comparison_batch_count,
        critique_slide_request_count=ctx.final_cache_misses,
        cache_hits=ctx.final_cache_hits,
        cache_misses=ctx.final_cache_misses,
        render_ms=ctx.render_ms,
        critique_ms=ctx.final_critique_ms,
        revision_ms=ctx.revision_ms,
        indeterminate_count=sum(
            (_critique_unusable_reason(critique) == "indeterminate" for critique in ctx.candidate_critiques)
        ),
        **ctx.ocr_runtime.summary(),
    )
    _log_review_event(
        reference_mode=ctx.plan_reference_mode,
        stage="acceptance",
        slide_count=len(ctx.targets),
        attempted=len(ctx.candidate_decisions),
        accepted=len(ctx.accepted_local_indices),
        rejection_reasons=ctx.rejection_reasons,
        model=ctx.comparison_metadata["model"],
        prompt_version=ctx.comparison_metadata["prompt_version"],
        cache_version=ctx.comparison_metadata["cache_version"],
        render_ms=ctx.render_ms,
        revision_ms=ctx.revision_ms,
        indeterminate_count=sum(
            (_critique_unusable_reason(critique) == "indeterminate" for critique in ctx.candidate_critiques)
        ),
        **ctx.ocr_runtime.summary(),
    )


# ---- Folded back from authored_quality_review_finalize.py (module-alias split) ----

async def finalize_legacy_review(ctx):
    ctx.remaining_issues = _serialize_critiques(ctx.positions, ctx.final_critiques)
    ctx.fixed_positions = [ctx.positions[index] for index in ctx.fixed_local_indices]
    ctx.persist_ms = 0.0
    if ctx.fixed_local_indices:
        ctx.persist_started_at = time.perf_counter()
        ctx.pending_updates: list[tuple[int, str, dict, dict]] = []
        for ctx.local_index in ctx.fixed_local_indices:  # noqa: B020
            ctx.position = ctx.positions[ctx.local_index]
            ctx.slide = ctx.targets[ctx.local_index]
            ctx.image_reference = _write_reviewed_png(
                ctx.presentation_id, ctx.position, ctx.revised_pngs[ctx.local_index]
            )
            ctx.pending_updates.append(
                (
                    ctx.local_index,
                    ctx.revised_htmls[ctx.local_index],
                    {
                        **(ctx.slide.content if isinstance(ctx.slide.content, dict) else {}),
                        "__authored__": True,
                        "image": ctx.image_reference,
                        "role": ctx.roles[ctx.local_index],
                    },
                    {
                        **(ctx.slide.properties if isinstance(ctx.slide.properties, dict) else {}),
                        "image": ctx.image_reference,
                    },
                )
            )
        await version_service.snapshot_slides(
            ctx.session,
            ctx.presentation_id,
            [slide.model_dump(mode="json") for slide in ctx.slides],
            label="고품질 검수 전",
            force=True,
        )
        for ctx.local_index, ctx.revised_html, ctx.revised_content, ctx.revised_properties in ctx.pending_updates:  # noqa: B020
            ctx.slide = ctx.targets[ctx.local_index]
            ctx.result = await _execute_sqlalchemy(
                ctx.session,
                update(SlideModel)
                .where(
                    SlideModel.id == ctx.slide.id,
                    SlideModel.html_content == ctx.persisted_htmls[ctx.local_index],
                    SlideModel.content == ctx.slide.content,
                    SlideModel.properties == ctx.slide.properties,
                )
                .values(html_content=ctx.revised_html, content=ctx.revised_content, properties=ctx.revised_properties)
                .execution_options(synchronize_session=False),
            )
            if ctx.result.rowcount != 1:
                await ctx.session.rollback()
                raise RuntimeError("Presentation changed during quality review; no fixes were applied")
        await ctx.session.flush()
        ctx.expected_by_id = {
            ctx.targets[local_index].id: (revised_html, revised_content, revised_properties)
            for local_index, revised_html, revised_content, revised_properties in ctx.pending_updates
        }
        ctx.readback = await _execute_sqlalchemy(
            ctx.session,
            select(
                SlideModel.id,
                SlideModel.html_content,
                SlideModel.content,
                SlideModel.properties,
            ).where(SlideModel.id.in_(tuple(ctx.expected_by_id))),
        )
        ctx.readback_by_id = {row.id: (row.html_content, row.content, row.properties) for row in ctx.readback.all()}
        if ctx.readback_by_id != ctx.expected_by_id:
            await ctx.session.rollback()
            raise RuntimeError("Published authored slide read-back did not match")
        ctx.fixed_position_set = set(ctx.fixed_positions)
        for ctx.decision in ctx.candidate_decisions:  # noqa: B020
            if ctx.decision.get("slide_index") not in ctx.fixed_position_set:
                continue
            ctx.html = ctx.revised_htmls[ctx.positions.index(ctx.decision["slide_index"])]
            ctx.decision.update(
                {
                    "apply_status": "committed",
                    "publish_status": "active",
                    "post_verify_status": "passed",
                    "html_hash": sha256_prefixed(ctx.html.encode("utf-8")),
                    "applied": True,
                }
            )
        ctx.persist_ms = (time.perf_counter() - ctx.persist_started_at) * 1000
    ctx.task_rejection_reasons: Counter[str] = Counter(
        (decision["reason"] for decision in ctx.candidate_decisions if not decision["accepted"])
    )
    ctx.no_candidate_count = max(0, len(ctx.initial_issues) - len(ctx.candidate_local_indices))
    if ctx.no_candidate_count:
        ctx.task_rejection_reasons["no_candidate"] += ctx.no_candidate_count
    ctx.fallback_count = sum(
        (
            detail.get("path") == "full_fallback" and detail.get("succeeded", True)
            for detail in ctx.repair_details
            if isinstance(detail, Mapping)
        )
    )
    ctx.patch_count = sum(
        (
            detail.get("path") == "local_patch" and detail.get("succeeded", True)
            for detail in ctx.repair_details
            if isinstance(detail, Mapping)
        )
    )
    ctx.patch_attempts = sum(
        (
            bool((detail.get("patch") or {}).get("attempted"))
            for detail in ctx.repair_details
            if isinstance(detail, Mapping)
        )
    )
    ctx.patch_successes = sum(
        (
            bool((detail.get("patch") or {}).get("succeeded"))
            for detail in ctx.repair_details
            if isinstance(detail, Mapping)
        )
    )
    ctx.fallback_attempts = sum(
        (
            bool((detail.get("fallback") or {}).get("attempted"))
            for detail in ctx.repair_details
            if isinstance(detail, Mapping)
        )
    )
    ctx.fallback_successes = sum(
        (
            bool((detail.get("fallback") or {}).get("succeeded"))
            for detail in ctx.repair_details
            if isinstance(detail, Mapping)
        )
    )
    ctx.ocr_summary = ctx.ocr_runtime.summary()
    ctx.funnel_metrics = {
        "candidate_proposed": sum((bool(decision.get("candidate_proposed")) for decision in ctx.candidate_decisions)),
        "candidate_rendered": sum((bool(decision.get("candidate_rendered")) for decision in ctx.candidate_decisions)),
        "validation_passed": sum(
            (decision.get("validation_status") == "passed" for decision in ctx.candidate_decisions)
        ),
        "validation_rejected": sum(
            (decision.get("validation_status") == "rejected" for decision in ctx.candidate_decisions)
        ),
        "validation_inconclusive": sum(
            (decision.get("validation_status") == "inconclusive" for decision in ctx.candidate_decisions)
        ),
        "apply_committed": sum((decision.get("apply_status") == "committed" for decision in ctx.candidate_decisions)),
        "publish_active": sum((decision.get("publish_status") == "active" for decision in ctx.candidate_decisions)),
        "postverify_passed": sum(
            (decision.get("post_verify_status") == "passed" for decision in ctx.candidate_decisions)
        ),
        "applied": sum((bool(decision.get("applied")) for decision in ctx.candidate_decisions)),
        "rolled_back": sum((bool(decision.get("rolled_back")) for decision in ctx.candidate_decisions)),
        "legacy_preserved": len(ctx.targets)
        - sum((bool(decision.get("applied")) for decision in ctx.candidate_decisions)),
    }
    ctx.total_ms = (time.perf_counter() - ctx.review_started_at) * 1000
    ctx.final_local_reports = list(ctx.local_reports)
    for ctx.local_index in ctx.fixed_local_indices:  # noqa: B020
        ctx.accepted_report = ctx.fallback_local_reports.get(ctx.local_index) or ctx.candidate_local_reports.get(
            ctx.local_index
        )
        if ctx.accepted_report is not None:
            ctx.final_local_reports[ctx.local_index] = ctx.accepted_report
    ctx.local_high_remaining = sum(
        (any((issue.severity == "high" for issue in report.issues)) for report in ctx.final_local_reports)
    )
    _log_review_event(
        reference_mode=ctx.plan_reference_mode,
        stage="completed",
        slide_count=len(ctx.targets),
        attempted=len(ctx.candidate_decisions),
        accepted=len(ctx.fixed_local_indices),
        rejection_reasons=ctx.task_rejection_reasons,
        model=ctx.review_metadata["model"],
        prompt_version=ctx.review_metadata["prompt_version"],
        cache_version=ctx.review_metadata["cache_version"],
        critique_batch_count=ctx.routing_metadata["vqa_batch_count"] + ctx.final_review_batch_count,
        critique_slide_request_count=ctx.initial_cache_misses + ctx.final_cache_misses,
        cache_hits=ctx.initial_cache_hits + ctx.final_cache_hits,
        cache_misses=ctx.initial_cache_misses + ctx.final_cache_misses,
        render_ms=ctx.render_ms,
        local_inspection_ms=ctx.local_inspection_ms,
        montage_ms=ctx.montage_ms,
        critique_ms=ctx.initial_critique_ms + ctx.final_critique_ms,
        revision_ms=ctx.revision_ms,
        persist_ms=ctx.persist_ms,
        total_ms=ctx.total_ms,
        patch_attempted=ctx.patch_attempts,
        patch_accepted=ctx.patch_successes,
        patch_rejected=max(0, ctx.patch_attempts - ctx.patch_successes),
        fallback_attempted=ctx.fallback_attempts,
        fallback_succeeded=ctx.fallback_successes,
        fallback_failed=max(0, ctx.fallback_attempts - ctx.fallback_successes),
        local_high_remaining=ctx.local_high_remaining,
        indeterminate_count=sum(
            (_critique_unusable_reason(critique) == "indeterminate" for critique in ctx.final_critiques)
        ),
        **ctx.ocr_summary,
    )
    await _set_status(
        ctx.session,
        ctx.task,
        status="completed",
        message=(
            f"검수가 완료되었습니다. {len(ctx.fixed_positions)}개 슬라이드를 수정했습니다."
            + (" 잔여 이슈가 있습니다." if ctx.remaining_issues else "")
        )
        if ctx.fixed_positions
        else (
            "검수는 완료되었지만 자동 수정할 수 있는 변경은 생성되지 않았습니다."
            + (" 잔여 이슈가 있습니다." if ctx.remaining_issues else "")
        ),
        data={
            "presentation_id": str(ctx.presentation_id),
            "mode": ctx.request.mode,
            "scope": ctx.request.scope,
            "review_policy": dict(ctx.review_metadata),
            "reviewed_count": len(ctx.targets),
            "issue_slide_count": len(ctx.initial_issues),
            "issues": ctx.initial_issues,
            "fixed_count": len(ctx.fixed_positions),
            "fixed_slide_indices": ctx.fixed_positions,
            "candidate_count": len(ctx.candidate_local_indices),
            "accepted_candidate_count": len(ctx.fixed_positions),
            "rejected_candidate_count": len(ctx.candidate_local_indices) - len(ctx.fixed_positions),
            "rejection_reasons": dict(sorted(ctx.task_rejection_reasons.items())),
            "candidate_decisions": ctx.candidate_decisions,
            "remaining_issue_count": len(ctx.remaining_issues),
            "remaining_slide_indices": [issue["slide_index"] for issue in ctx.remaining_issues],
            "remaining_issues": ctx.remaining_issues,
            # ``status=completed`` is the workflow/task state. Quality outcome
            # is deliberately separate so residual findings cannot masquerade
            # as a clean quality pass while keeping existing task consumers
            # backwards compatible.
            "quality_gate_status": (
                "residual_issues" if ctx.remaining_issues else "passed"
            ),
            "version_saved": bool(ctx.fixed_positions),
            "slide_results": _build_slide_results(
                positions=ctx.positions,
                initial_critiques=ctx.initial_critiques,
                final_critiques=ctx.final_critiques,
                repair_details=ctx.repair_details,
                candidate_decisions=ctx.candidate_decisions,
            ),
            "local_checks": [
                {
                    "slide_index": position,
                    "telemetry_status": report.telemetry_status,
                    "inspected_elements": report.inspected_elements,
                    "issues": [issue.to_dict() for issue in report.issues],
                }
                for position, report in zip(ctx.positions, ctx.local_reports)
            ],
            "ocr_checks": ctx.ocr_checks,
            "routing": ctx.routing_metadata,
            "metrics": {
                "stage_timings_ms": {
                    "render": round(ctx.render_ms, 3),
                    "local_inspection": round(ctx.local_inspection_ms, 3),
                    "montage_vqa": round(ctx.montage_ms, 3),
                    "detail_vqa": round(ctx.initial_critique_ms, 3),
                    "repair": round(ctx.revision_ms, 3),
                    "final_vqa": round(ctx.final_critique_ms, 3),
                    "ocr": ctx.ocr_summary["ocr_total_ms"],
                    "persist": round(ctx.persist_ms, 3),
                },
                "total_ms": round(ctx.total_ms, 3),
                "cache_hits": ctx.initial_cache_hits + ctx.final_cache_hits,
                "vqa_calls": ctx.initial_cache_misses + ctx.final_cache_misses,
                "patch_count": ctx.patch_count,
                "fallback_count": ctx.fallback_count,
                "patch_attempts": ctx.patch_attempts,
                "patch_successes": ctx.patch_successes,
                "patch_failures": max(0, ctx.patch_attempts - ctx.patch_successes),
                "patch_attempted": ctx.patch_attempts,
                "patch_succeeded": ctx.patch_successes,
                "patch_failed": max(0, ctx.patch_attempts - ctx.patch_successes),
                "fallback_attempts": ctx.fallback_attempts,
                "fallback_successes": ctx.fallback_successes,
                "fallback_failures": max(0, ctx.fallback_attempts - ctx.fallback_successes),
                "fallback_attempted": ctx.fallback_attempts,
                "fallback_succeeded": ctx.fallback_successes,
                "fallback_failed": max(0, ctx.fallback_attempts - ctx.fallback_successes),
                **ctx.funnel_metrics,
                **ctx.ocr_summary,
            },
        },
    )


# ---- Folded back from authored_quality_review_canonical.py (module-alias split) ----

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
            and (not isinstance(prior, bool))
            and isinstance(item, (int, float))
            and (not isinstance(item, bool))
        ):
            merged[str(key)] = prior + item
        elif group == "solver" and key == "search_complete" and isinstance(item, bool):
            merged[str(key)] = bool(prior) and item if prior is not None else item
        elif group == "rollout" and key == "kill_switch_enabled" and isinstance(item, bool):
            merged[str(key)] = bool(prior) or item
        else:
            merged[str(key)] = item
    return merged


def _indexed_canonical_issues(
    canonical: Mapping[str, Any], *, remaining_only: bool
) -> list[dict[str, Any]]:
    """Flatten slide findings without discarding their navigation index."""
    indexed: list[dict[str, Any]] = []
    raw_slides = canonical.get("slide_results")
    if isinstance(raw_slides, Sequence) and (not isinstance(raw_slides, (str, bytes, bytearray))):
        for raw_slide in raw_slides:
            if not isinstance(raw_slide, Mapping):
                continue
            slide_index = raw_slide.get("slide_index")
            if not isinstance(slide_index, int) or isinstance(slide_index, bool):
                continue
            if remaining_only and int(raw_slide.get("unresolved_confirmed_issue_count", 0)) <= 0:
                continue
            raw_issues = raw_slide.get("confirmed_issues")
            if not isinstance(raw_issues, Sequence) or isinstance(raw_issues, (str, bytes, bytearray)):
                continue
            indexed.extend(
                (
                    {**dict(issue), "slide_index": slide_index}
                    for issue in raw_issues
                    if isinstance(issue, Mapping)
                )
            )
    if indexed:
        return indexed
    fallback_key = "remaining_issues" if remaining_only else "issues"
    fallback = canonical.get(fallback_key)
    return [dict(issue) for issue in fallback or () if isinstance(issue, Mapping)]


def _merge_slidespec_review_data(
    *,
    presentation_id: uuid.UUID,
    request: AuthoredQualityReviewRequest,
    legacy: Mapping[str, Any] | None,
    canonical: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge disjoint per-slide owners without changing the legacy contract."""
    result = dict(legacy or {})
    canonical_policy = canonical.get("review_policy")
    if canonical_policy is not None:
        if (not isinstance(canonical_policy, Mapping)
            or ("review_policy" in result and result["review_policy"] != canonical_policy)):
            raise ValueError("authored_review_checker_policy_conflict")
        result["review_policy"] = dict(canonical_policy)
    result.setdefault("presentation_id", str(presentation_id))
    result.setdefault("mode", request.mode)
    result.setdefault("scope", request.scope)
    additive = (
        "reviewed_count",
        "issue_slide_count",
        "detailed_issue_count",
        "confirmed_issue_count",
        "incomplete_measurement_count",
        "unresolved_confirmed_issue_count",
        "clean_slide_count",
        "fixed_count",
        "resolved_issue_count",
        "candidate_count",
        "accepted_candidate_count",
        "rejected_candidate_count",
        "remaining_slide_count",
        "remaining_issue_count",
    )
    for key in additive:
        result[key] = int(result.get(key, 0)) + int(canonical.get(key, 0))
    result["issues"] = list(result.get("issues", [])) + _indexed_canonical_issues(
        canonical, remaining_only=False
    )
    result["remaining_issues"] = list(result.get("remaining_issues", [])) + _indexed_canonical_issues(
        canonical, remaining_only=True
    )
    result["slide_results"] = list(result.get("slide_results", [])) + list(canonical.get("slide_results", []))
    result["candidate_decisions"] = list(result.get("candidate_decisions", [])) + list(
        canonical.get("candidate_decisions", [])
    )
    rejection_reasons = dict(result.get("rejection_reasons") or {})
    for code, count in (canonical.get("rejection_reasons") or {}).items():
        rejection_reasons[str(code)] = rejection_reasons.get(str(code), 0) + int(count)
    result["rejection_reasons"] = rejection_reasons
    for key in ("fixed_slide_indices", "remaining_slide_indices"):
        canonical_key = "remaining_issue_indices" if key == "remaining_slide_indices" else key
        result[key] = sorted(set(result.get(key, [])) | set(canonical.get(canonical_key, [])))
    result["version_saved"] = bool(result.get("version_saved") or canonical.get("version_saved"))
    routing = dict(result.get("routing") or {})
    routing.update(canonical.get("routing") or {})
    result["routing"] = routing
    metrics = dict(result.get("metrics") or {})
    canonical_metrics = _canonical_metrics_with_flat_aliases(canonical.get("metrics") or {})
    for key, value in canonical_metrics.items():
        previous = metrics.get(key)
        if key == "stage_timings_ms" and isinstance(value, Mapping):
            merged_timings = dict(previous) if isinstance(previous, Mapping) else {}
            for stage, duration in value.items():
                prior_duration = merged_timings.get(stage, 0)
                if isinstance(duration, (int, float)) and (not isinstance(duration, bool)):
                    merged_timings[str(stage)] = float(prior_duration) + float(duration)
            metrics[key] = merged_timings
            continue
        if key in _CANONICAL_METRIC_FLAT_ALIASES and isinstance(value, Mapping):
            metrics[key] = _merge_canonical_metric_group(previous, value, group=key)
            continue
        if (
            isinstance(previous, (int, float))
            and (not isinstance(previous, bool))
            and isinstance(value, (int, float))
            and (not isinstance(value, bool))
        ):
            metrics[key] = previous + value
        else:
            metrics[key] = value
    result["metrics"] = metrics
    result["slide_results"] = sorted(result["slide_results"], key=lambda item: int(item.get("slide_index", 0)))
    return result


def _bind_canonical_review_policy(data, observed_policy):
    """Keep the pre-execution checker identity, never a post-hoc PASS."""
    existing = data.get("review_policy")
    if existing is not None and existing != observed_policy:
        raise ValueError("authored_review_checker_policy_conflict")
    return {**data, "review_policy": dict(observed_policy)}


def _unclaimed_slidespec_review_data(
    positions: Iterable[int], failures: Mapping[int, str]
) -> dict[str, Any]:
    """Describe slides that could not enter the typed repair pipeline.

    Quality review must never turn an import/rollout failure into an automatic
    full-page rewrite.  These entries therefore remain revision-neutral and
    carry a concrete reason that the UI can surface next to the existing,
    explicitly user-invoked single-page remake action.
    """
    ordered = tuple(sorted(set((int(position) for position in positions))))
    slide_results: list[dict[str, Any]] = []
    remaining_issues: list[dict[str, Any]] = []
    for position in ordered:
        reason = str(failures.get(position) or "repair.typed_pipeline_unavailable")
        issue = {
            "slide_index": position,
            "type": "quality_measurement_incomplete",
            "severity": "high",
            "detail": reason,
            "reason_code": reason,
            "applied": False,
        }
        remaining_issues.append(issue)
        slide_results.append(
            {
                "slide_index": position,
                "path": "none",
                "attempted": False,
                "applied": False,
                "reason_codes": (reason, "quality_review.automatic_page_regeneration_disabled"),
                "confirmed_issues": [],
                "remaining_issues": [reason],
                "confirmed_issue_count": 0,
                "incomplete_measurement_count": 1,
                "unresolved_confirmed_issue_count": 0,
                "clean_slide_count": 0,
            }
        )
    return {
        "reviewed_count": len(ordered),
        "issue_slide_count": 0,
        "detailed_issue_count": 0,
        "confirmed_issue_count": 0,
        "incomplete_measurement_count": len(ordered),
        "unresolved_confirmed_issue_count": 0,
        "clean_slide_count": 0,
        "issues": [],
        "fixed_count": 0,
        "resolved_issue_count": 0,
        "fixed_slide_indices": [],
        "remaining_slide_count": len(ordered),
        "remaining_issue_count": len(ordered),
        "remaining_slide_indices": list(ordered),
        "remaining_issues": remaining_issues,
        "candidate_count": 0,
        "accepted_candidate_count": 0,
        "rejected_candidate_count": 0,
        "candidate_decisions": [],
        "rejection_reasons": dict(
            Counter(
                (str(failures.get(position) or "repair.typed_pipeline_unavailable") for position in ordered)
            )
        ),
        "version_saved": False,
        "slide_results": slide_results,
        "routing": {"slidespec": True, "claimed_positions": [], "automatic_page_regeneration": False},
        "metrics": {"individual_fallback_count": len(ordered), "automatic_page_regeneration_calls": 0},
    }


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
    if not slides or not presentation.is_authored() or (not all((slide.is_authored() for slide in slides))):
        raise ValueError("High-quality review supports AI-authored presentations only")
    if len(outline.slides) != len(slides):
        raise ValueError("Saved outline and slide counts do not match")
    positions = _resolve_positions(request, len(slides))
    if any((not slides[position].html_content for position in positions)):
        raise ValueError("One or more slides are missing their editable authored HTML")
    from services.slidespec_authored_importer import ensure_authored_slidespec_imports

    # Canonical repair acceptance uses paired style comparison, not the legacy
    # standalone critique. Capture its configuration before any importer or
    # executor work; the release consumer still rejects missing/stale identity.
    review_policy = dict(_style_comparison_observation_metadata())

    # Import publication can roll back and expire the presentation in this
    # session. Keep the already-inspected owner identity for both stages.
    review_owner_id = getattr(presentation, "owner_id", None)
    imported = await ensure_authored_slidespec_imports(
        presentation_id=presentation_id,
        slides=slides,
        positions=positions,
        opt_in=True,
        owner_id=review_owner_id,
        session=session,
    )
    slides = list(imported.slides)
    claimed_hint = {
        position
        for position in positions
        if isinstance(slides[position].properties, Mapping)
        and ACTIVE_POINTER_KEY in slides[position].properties
    }
    unclaimed_positions = [position for position in positions if position not in claimed_hint]
    unclaimed_data: Mapping[str, Any] | None = (
        _unclaimed_slidespec_review_data(unclaimed_positions, imported.failures)
        if unclaimed_positions
        else None
    )
    if not claimed_hint:
        data = _merge_slidespec_review_data(
            presentation_id=presentation_id, request=request, legacy=unclaimed_data, canonical={}
        )
        await _set_status(
            session,
            task,
            status="completed",
            message="검수는 완료되었지만 자동 수정으로 확정된 변경은 없습니다.",
            data=_bind_canonical_review_policy(data, review_policy),
        )
        return
    from services.slidespec_authored_executor import execute_slidespec_authored_review

    canonical = await execute_slidespec_authored_review(
        presentation_id=presentation_id,
        slides=slides,
        positions=sorted(claimed_hint),
        opt_in=True,
        owner_id=review_owner_id,
        session=session,
    )
    claimed = set(canonical.claimed_positions)
    if claimed != claimed_hint:
        await session.rollback()
        raise RuntimeError("SlideSpec ownership changed during authored review")
    routing = canonical.data.setdefault("routing", {})
    routing["imported_positions"] = list(imported.imported_positions)
    routing["import_failures"] = {str(position): code for position, code in imported.failures.items()}
    data = _merge_slidespec_review_data(
        presentation_id=presentation_id, request=request, legacy=unclaimed_data, canonical=canonical.data
    )
    await _set_status(
        session,
        task,
        status="completed",
        message=f"검수가 완료되었습니다. {data['fixed_count']}개 슬라이드를 수정했습니다."
        if data["fixed_count"]
        else "검수는 완료되었지만 자동 수정으로 확정된 변경은 없습니다.",
        data=_bind_canonical_review_policy(data, review_policy),
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

    # Keep the legacy task response, but make the workspace operation the
    # durable execution and recovery authority. ``background_tasks`` remains in
    # the signature for wire compatibility and is intentionally unused.
    return await admit_workspace_authored_task(
        session,
        presentation_id=presentation_id,
        operation_kind="quality.review",
        expected_revision=int(presentation.revision),
        command="AI 작성 프레젠테이션 품질 검토",
        context=request.model_dump(mode="json"),
        task_id_prefix="workspace-quality",
        pending_message="품질 검토 작업을 대기열에 추가했습니다.",
    )
