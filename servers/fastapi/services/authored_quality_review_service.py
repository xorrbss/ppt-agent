"""Post-generation quality review for saved AI-authored presentations.

The review renders the persisted authored HTML, critiques the resulting images in
parallel, and optionally re-authors only the slides with visible defects. The live
deck is checkpointed before any replacement so the operation can be undone through
the existing presentation version history.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import traceback
import uuid
from collections import Counter
from pathlib import Path
from typing import Iterable, List, Mapping

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.presentation_from_template import AuthoredQualityReviewRequest
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services import presentation_version_service as version_service
from services.database import async_session_maker
from utils.asset_directory_utils import (
    filesystem_image_path_to_app_data_reference,
    get_images_directory,
)
from utils.authored_styles import resolve_authored_style
from utils.datetime_utils import get_current_utc_datetime
from utils.llm_calls.author_deck import prepare_authored_deck
from utils.llm_calls.author_slide import (
    EXACT_RECONSTRUCTION,
    STYLE_REFERENCE,
    Brand,
    normalize_reference_mode,
)
from utils.llm_calls.critique_slide import SlideCritique
from utils.llm_calls import author_vision_qa
from utils.llm_calls.author_vision_qa import critique_authored, revise_authored_deck
from utils.slide_capture import render_html_to_png


LOGGER = logging.getLogger(__name__)
_INDETERMINATE_PREFIX = "INDETERMINATE:"
_SAFE_OBSERVATION_TOKEN = re.compile(r"^[A-Za-z0-9._:/@+-]{1,128}$")
_SAFE_REJECTION_REASONS = {
    "no_candidate",
    "unchanged",
    "worsened",
    "initial_unavailable",
    "initial_inconsistent",
    "initial_indeterminate",
    "final_unavailable",
    "final_inconsistent",
    "final_indeterminate",
}


def _is_authored_slide(slide: SlideModel) -> bool:
    return (
        slide.layout_group == "authored"
        or (slide.layout or "").startswith("authored:")
        or (
            isinstance(slide.content, dict)
            and slide.content.get("__authored__") is True
        )
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
) -> List[int]:
    if request.scope == "all":
        return list(range(slide_count))
    if len(request.slide_indices) != 1:
        raise ValueError("Current-slide review requires exactly one slide index")
    index = request.slide_indices[0]
    if index < 0 or index >= slide_count:
        raise ValueError("Slide index is outside the presentation")
    return [index]


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


def _serialize_critiques(positions: Iterable[int], critiques: list) -> List[dict]:
    results: List[dict] = []
    for position, critique in zip(positions, critiques):
        if critique is None or not critique.needs_fix:
            continue
        results.append(
            {
                "slide_index": position,
                "issues": [
                    issue.model_dump(mode="json") for issue in critique.issues
                ],
            }
        )
    return results


def _read_exact_sources(reference_images: Iterable[tuple[Path, ...]]) -> List[bytes]:
    sources: List[bytes] = []
    for references in reference_images:
        if len(references) != 1:
            raise ValueError(
                "Exact reconstruction review requires exactly one source per slide"
            )
        try:
            source = references[0].read_bytes()
        except OSError as exc:
            raise ValueError(
                "Exact reconstruction source image is unreadable"
            ) from exc
        if not source:
            raise ValueError("Exact reconstruction source image is empty")
        sources.append(source)
    return sources


def _critique_unusable_reason(critique: SlideCritique | None) -> str | None:
    if critique is None:
        return "unavailable"
    if critique.needs_fix != bool(critique.issues):
        return "inconsistent"
    if any(
        issue.detail.lstrip().upper().startswith(_INDETERMINATE_PREFIX)
        for issue in critique.issues
    ):
        return "indeterminate"
    return None


def _critique_score(critique: SlideCritique | None) -> tuple[int, int, int] | None:
    """Return a deterministic defect score, or None when comparison is unsafe."""
    if _critique_unusable_reason(critique) is not None:
        return None
    severity = {"high": 3, "medium": 2, "low": 1}
    return (
        int(critique.needs_fix),
        sum(severity.get(issue.severity, 3) for issue in critique.issues),
        len(critique.issues),
    )


def _acceptance_decision(
    before: SlideCritique | None, after: SlideCritique | None
) -> tuple[bool, str]:
    """Accept exact HTML only after a strictly better, determinate comparison."""
    before_error = _critique_unusable_reason(before)
    if before_error is not None:
        return False, f"initial_{before_error}"
    after_error = _critique_unusable_reason(after)
    if after_error is not None:
        return False, f"final_{after_error}"

    before_score = _critique_score(before)
    after_score = _critique_score(after)
    assert before_score is not None and after_score is not None
    if after_score < before_score:
        return True, "improved"
    if after_score == before_score:
        return False, "unchanged"
    return False, "worsened"


def _is_improved(
    before: SlideCritique | None, after: SlideCritique | None
) -> bool:
    return _acceptance_decision(before, after)[0]


def _ordered_image_batch_sha256(images: Iterable[bytes] | None) -> str | None:
    """Fingerprint a batch without retaining or logging any image content.

    The length-prefixed per-image digest makes the order significant. That matters for
    exact reconstruction: a reordered source/render mapping must not look like the
    same review batch in operational evidence.
    """
    if images is None:
        return None
    digest = hashlib.sha256()
    for image in images:
        image_digest = hashlib.sha256(image).digest()
        digest.update(len(image_digest).to_bytes(2, "big"))
        digest.update(image_digest)
    return digest.hexdigest()


def _safe_review_model() -> str:
    """Return a bounded configuration token, never a prompt or provider response."""
    try:
        model = str(author_vision_qa.get_model() or "unknown")
    except Exception:  # Provider configuration is best-effort for telemetry only.
        return "unknown"
    return model if _SAFE_OBSERVATION_TOKEN.fullmatch(model) else "unknown"


def _review_observation_metadata() -> dict[str, str]:
    """Read static reviewer configuration without initiating any VQA request."""
    return {
        "model": _safe_review_model(),
        "prompt_version": str(
            getattr(author_vision_qa, "CRITIQUE_PROMPT_VERSION", "unknown")
        ),
        "cache_version": str(
            getattr(author_vision_qa, "_VQA_PROMPT_VERSION", "unknown")
        ),
    }


def _observe_critique_cache(
    images: Iterable[bytes],
    contexts: Iterable[str | None],
    *,
    source_images: Iterable[bytes] | None = None,
    reference_mode: str = STYLE_REFERENCE,
) -> tuple[int, int]:
    """Count cache lookups immediately before one ``critique_authored`` call.

    This deliberately mirrors the existing cache key rather than changing the vision
    QA API. A cache miss includes an in-flight coalesced request; it did not have a
    completed cached result at call start. The helper never invokes the model.
    """
    image_list = list(images)
    context_list = list(contexts)
    source_list = (
        list(source_images) if source_images is not None else [None] * len(image_list)
    )
    if len(context_list) != len(image_list) or len(source_list) != len(image_list):
        return 0, len(image_list)
    try:
        cache_enabled = int(getattr(author_vision_qa, "VQA_CACHE_SIZE", 0)) > 0
    except (TypeError, ValueError):
        cache_enabled = False
    if not cache_enabled:
        return 0, len(image_list)

    key_builder = getattr(author_vision_qa, "_critique_cache_key", None)
    cache = getattr(author_vision_qa, "_vqa_cache", None)
    if not callable(key_builder) or cache is None:
        return 0, len(image_list)

    hits = 0
    for image, context, source_image in zip(image_list, context_list, source_list):
        try:
            key = key_builder(
                image,
                context,
                source_image=source_image,
                reference_mode=reference_mode,
            )
            hits += int(key in cache)
        except Exception:  # Cache observability must never affect a review.
            continue
    return hits, len(image_list) - hits


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
    source_batch_sha256: str | None = None,
    render_batch_sha256: str | None = None,
    critique_batch_count: int = 0,
    critique_slide_request_count: int = 0,
    cache_hits: int = 0,
    cache_misses: int = 0,
    render_ms: float = 0.0,
    critique_ms: float = 0.0,
    revision_ms: float = 0.0,
    indeterminate_count: int = 0,
) -> None:
    """Emit a closed, content-free high-quality-review observation."""
    raw_reasons = rejection_reasons or {}
    reasons: Counter[str] = Counter()
    for reason, count in raw_reasons.items():
        safe_reason = reason if reason in _SAFE_REJECTION_REASONS else "other"
        reasons[safe_reason] += max(0, int(count))
    rejected = sum(reasons.values())
    event = {
        "schema_version": 1,
        "pipeline": "authored_quality_review",
        "reference_mode": reference_mode,
        "stage": stage,
        "slides": slide_count,
        "attempted": attempted,
        "accepted": accepted,
        "rejected": rejected,
        "rejection_reasons": dict(sorted(reasons.items())),
        "model": model or "unknown",
        "prompt_version": prompt_version or "unknown",
        "cache_version": cache_version or "unknown",
        "source_batch_sha256": source_batch_sha256,
        "render_batch_sha256": render_batch_sha256,
        # One batch fans out to one cached critique per slide. A miss means there was
        # no completed cache result; an in-flight coalesced request can lower the
        # actual remote-call count, so this is a conservative request estimate.
        "critique_batch_count": critique_batch_count,
        "critique_slide_request_count": critique_slide_request_count,
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "render_ms": round(render_ms, 3),
        "ocr_ms": 0.0,
        "ocr_executed": False,
        "critique_ms": round(critique_ms, 3),
        "revision_ms": round(revision_ms, 3),
        "indeterminate_count": indeterminate_count,
    }
    LOGGER.info(
        "authored_quality_review %s",
        json.dumps(event, sort_keys=True, separators=(",", ":")),
    )


def _write_reviewed_png(
    presentation_id: uuid.UUID, slide_position: int, png: bytes
) -> str:
    relative_directory = os.path.join("authored", str(presentation_id))
    absolute_directory = os.path.join(get_images_directory(), relative_directory)
    os.makedirs(absolute_directory, exist_ok=True)
    filename = f"slide_{slide_position}.png"
    absolute_path = os.path.join(absolute_directory, filename)
    with open(absolute_path, "wb") as image_file:
        image_file.write(png)
    return filesystem_image_path_to_app_data_reference(absolute_path)


async def run_authored_quality_review(
    presentation_id: uuid.UUID,
    request: AuthoredQualityReviewRequest,
    task: AsyncPresentationGenerationTaskModel,
    session: AsyncSession,
) -> None:
    presentation = await session.get(PresentationModel, presentation_id)
    if presentation is None:
        raise ValueError("Presentation not found")
    outline = presentation.get_presentation_outline()
    if outline is None or not outline.slides:
        raise ValueError("The saved AI-authored outline is missing")

    slides = list(
        (
            await session.scalars(
                select(SlideModel)
                .where(SlideModel.presentation == presentation_id)
                .order_by(SlideModel.index)
            )
        ).all()
    )
    if not slides or not presentation.is_authored() or not all(
        _is_authored_slide(slide) for slide in slides
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
    style = resolve_authored_style(str(theme.get("style") or "default"))
    saved_reference_mode = normalize_reference_mode(
        theme.get("reference_mode")
        or getattr(style, "reference_mode", STYLE_REFERENCE)
    )
    style_reference_mode = normalize_reference_mode(
        getattr(style, "reference_mode", STYLE_REFERENCE)
    )
    if (
        saved_reference_mode == EXACT_RECONSTRUCTION
        and style_reference_mode != EXACT_RECONSTRUCTION
    ):
        raise ValueError(
            "The exact-reconstruction source style is unavailable; review cannot "
            "fall back to a style-only comparison"
        )
    plan = prepare_authored_deck(outline, _saved_brand(presentation), style)
    plan_reference_mode = normalize_reference_mode(
        getattr(plan, "reference_mode", saved_reference_mode)
    )
    if plan_reference_mode != saved_reference_mode:
        raise ValueError("Saved authored reference mode does not match its source style")
    contents = [outline.slides[position].content for position in positions]
    roles = [
        _saved_role(target, plan.roles[position])
        for position, target in zip(positions, targets)
    ]
    htmls = [str(target.html_content) for target in targets]
    exact_reconstruction = plan_reference_mode == EXACT_RECONSTRUCTION
    if exact_reconstruction:
        planned_references = getattr(plan, "reference_images", None)
        if planned_references is None or len(planned_references) != len(slides):
            raise ValueError(
                "Exact reconstruction review requires a complete source mapping"
            )
        target_references = [planned_references[position] for position in positions]
    else:
        target_references = [() for _ in positions]
    source_images = (
        _read_exact_sources(target_references) if exact_reconstruction else None
    )
    review_metadata = _review_observation_metadata()
    source_batch_sha256 = _ordered_image_batch_sha256(source_images)

    await _set_status(
        session,
        task,
        status="processing",
        message=f"{len(targets)}개 슬라이드를 렌더링하고 있습니다.",
    )
    render_started_at = time.perf_counter()
    pngs = list(await asyncio.gather(*(render_html_to_png(html) for html in htmls)))
    render_ms = (time.perf_counter() - render_started_at) * 1000
    render_batch_sha256 = _ordered_image_batch_sha256(pngs)

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
    initial_cache_hits, initial_cache_misses = _observe_critique_cache(
        pngs,
        critique_contexts,
        source_images=source_images,
        reference_mode=plan_reference_mode,
    )
    initial_critique_started_at = time.perf_counter()
    initial_critiques = await critique_authored(
        pngs,
        contexts=critique_contexts,
        **critique_kwargs,
    )
    initial_critique_ms = (time.perf_counter() - initial_critique_started_at) * 1000
    initial_indeterminate_count = sum(
        _critique_unusable_reason(critique) == "indeterminate"
        for critique in initial_critiques
    )
    _log_review_event(
        reference_mode=plan_reference_mode,
        stage="initial_review",
        slide_count=len(pngs),
        model=review_metadata["model"],
        prompt_version=review_metadata["prompt_version"],
        cache_version=review_metadata["cache_version"],
        source_batch_sha256=source_batch_sha256,
        render_batch_sha256=render_batch_sha256,
        critique_batch_count=1,
        critique_slide_request_count=initial_cache_misses,
        cache_hits=initial_cache_hits,
        cache_misses=initial_cache_misses,
        render_ms=render_ms,
        critique_ms=initial_critique_ms,
        indeterminate_count=initial_indeterminate_count,
    )
    if exact_reconstruction and any(
        _critique_score(critique) is None for critique in initial_critiques
    ):
        raise RuntimeError(
            "Exact reconstruction comparison failed; no slide was treated as clean"
        )
    initial_issues = _serialize_critiques(positions, initial_critiques)

    if request.mode == "analyze_only" or not initial_issues:
        message = (
            "검수가 완료되었습니다. 수정이 필요한 슬라이드가 없습니다."
            if not initial_issues
            else f"검수가 완료되었습니다. {len(initial_issues)}개 슬라이드에서 문제를 확인했습니다."
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
                "version_saved": False,
            },
        )
        return

    await _set_status(
        session,
        task,
        status="processing",
        message=f"문제가 발견된 {len(initial_issues)}개 슬라이드를 자동 수정하고 있습니다.",
    )
    revise_kwargs = {}
    if exact_reconstruction:
        revise_kwargs = {
            "initial_critiques": initial_critiques,
            "reference_images_by_slide": target_references,
        }
    revision_started_at = time.perf_counter()
    revised_htmls, revised_pngs, candidate_local_indices = await revise_authored_deck(
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
    revision_ms = (time.perf_counter() - revision_started_at) * 1000
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
        source_batch_sha256=source_batch_sha256,
        render_batch_sha256=render_batch_sha256,
        render_ms=render_ms,
        revision_ms=revision_ms,
    )

    await _set_status(
        session,
        task,
        status="processing",
        message="수정 결과를 다시 검증하고 있습니다.",
    )
    if exact_reconstruction:
        accepted_local_indices: List[int] = []
        chosen_critiques = list(initial_critiques)
        rejection_reasons: Counter[str] = Counter()
        if candidate_local_indices:
            candidate_pngs = [
                revised_pngs[index] for index in candidate_local_indices
            ]
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
            _log_review_event(
                reference_mode=plan_reference_mode,
                stage="final_review",
                slide_count=len(candidate_local_indices),
                attempted=len(candidate_local_indices),
                model=review_metadata["model"],
                prompt_version=review_metadata["prompt_version"],
                cache_version=review_metadata["cache_version"],
                source_batch_sha256=_ordered_image_batch_sha256(candidate_sources),
                render_batch_sha256=_ordered_image_batch_sha256(candidate_pngs),
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
            )
            for local_index, final_critique in zip(
                candidate_local_indices, candidate_critiques
            ):
                accepted, reason = _acceptance_decision(
                    initial_critiques[local_index], final_critique
                )
                if accepted:
                    accepted_local_indices.append(local_index)
                    chosen_critiques[local_index] = final_critique
                else:
                    rejection_reasons[reason] += 1
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
            source_batch_sha256=source_batch_sha256,
            render_batch_sha256=render_batch_sha256,
            render_ms=render_ms,
            revision_ms=revision_ms,
            indeterminate_count=sum(
                _critique_unusable_reason(critique) == "indeterminate"
                for critique in final_critiques
            ),
        )
    else:
        fixed_local_indices = candidate_local_indices
        final_contexts = [content[:240] for content in contents]
        final_cache_hits, final_cache_misses = _observe_critique_cache(
            revised_pngs,
            final_contexts,
            reference_mode=plan_reference_mode,
        )
        final_critique_started_at = time.perf_counter()
        final_critiques = await critique_authored(
            revised_pngs, contexts=final_contexts
        )
        final_critique_ms = (time.perf_counter() - final_critique_started_at) * 1000
        _log_review_event(
            reference_mode=plan_reference_mode,
            stage="final_review",
            slide_count=len(revised_pngs),
            attempted=len(candidate_local_indices),
            model=review_metadata["model"],
            prompt_version=review_metadata["prompt_version"],
            cache_version=review_metadata["cache_version"],
            render_batch_sha256=_ordered_image_batch_sha256(revised_pngs),
            critique_batch_count=1,
            critique_slide_request_count=final_cache_misses,
            cache_hits=final_cache_hits,
            cache_misses=final_cache_misses,
            render_ms=render_ms,
            critique_ms=final_critique_ms,
            revision_ms=revision_ms,
        )
        _log_review_event(
            reference_mode=plan_reference_mode,
            stage="acceptance",
            slide_count=len(targets),
            attempted=len(candidate_local_indices),
            accepted=len(candidate_local_indices),
            model=review_metadata["model"],
            prompt_version=review_metadata["prompt_version"],
            cache_version=review_metadata["cache_version"],
            render_batch_sha256=render_batch_sha256,
            render_ms=render_ms,
            revision_ms=revision_ms,
        )
    remaining_issues = _serialize_critiques(positions, final_critiques)

    fixed_positions = [positions[index] for index in fixed_local_indices]
    if fixed_local_indices:
        await version_service.snapshot_slides(
            session,
            presentation_id,
            [slide.model_dump(mode="json") for slide in slides],
            label="고품질 검수 전",
            force=True,
        )
        for local_index in fixed_local_indices:
            position = positions[local_index]
            slide = targets[local_index]
            image_reference = _write_reviewed_png(
                presentation_id, position, revised_pngs[local_index]
            )
            slide.html_content = revised_htmls[local_index]
            slide.content = {
                **(slide.content if isinstance(slide.content, dict) else {}),
                "__authored__": True,
                "image": image_reference,
                "role": roles[local_index],
            }
            slide.properties = {
                **(slide.properties if isinstance(slide.properties, dict) else {}),
                "image": image_reference,
            }
            session.add(slide)
        await session.commit()

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
            "remaining_issue_count": len(remaining_issues),
            "remaining_slide_indices": [
                issue["slide_index"] for issue in remaining_issues
            ],
            "remaining_issues": remaining_issues,
            "version_saved": bool(fixed_positions),
        },
    )


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
            await run_authored_quality_review(
                presentation_id, request, task, session
            )
        except Exception as exc:  # noqa: BLE001 - background task boundary
            traceback.print_exc()
            await session.rollback()
            await _set_status(
                session,
                task,
                status="error",
                message="고품질 검수 중 오류가 발생했습니다.",
                error={"detail": str(exc)},
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

    slides = list(
        (
            await session.scalars(
                select(SlideModel)
                .where(SlideModel.presentation == presentation_id)
                .order_by(SlideModel.index)
            )
        ).all()
    )
    if not presentation.is_authored() or not slides or not all(
        _is_authored_slide(slide) for slide in slides
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
    background_tasks.add_task(
        _run_quality_review, presentation_id, request, task.id
    )
    return task
