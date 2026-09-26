"""Vision-QA self-correction for AUTHORED slides (opt-in). Reuses the existing
multimodal critique (critique_slide.py) on the rendered authored PNGs, then
RE-AUTHORS each flagged slide with the critique as feedback and re-renders it,
bounded to a few cycles. Mirrors utils/llm_calls/vision_qa.py — which targets the
template/compose path and captures from /pdf-maker — but operates on the authored
mode's in-memory HTML + PNGs. Best-effort: a failed critique/re-author/re-render
leaves that slide unchanged. Never on the fast default path."""

import asyncio
import json
from functools import partial
import hashlib
from importlib import import_module
import logging
import math
import os
import sys
from utils.llm_calls import author_vision_qa_helpers as _vision_helpers
import time
from collections import OrderedDict
from collections.abc import Collection
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

from models.authored_infographic_plan import AuthoredSlideInfographic
from models.image_prompt import ImagePrompt
from services.authored_admission_producers import author_html as admit_author_html
from services.authored_admission_producers import illustrate_html as admit_expanded_html
from services.authored_admission_capture import capture_once, captured_metrics_or_existing
from services.authored_infographic_assets import (
    InfographicAssetContext,
    fill_scene_cutout,
    substitute_figure_icons,
)
from services.authored_structural_repair import (
    RepairAssessment,
    SlideRepairResult,
    bind_repair_candidate,
    protected_scope_violations,
    source_repair_html_violations,
)
from services.authored_source_visibility_repair import _is_root_page_number, source_visibility_issues
from utils.authored_dom_categories import (
    RESIDUAL_BLOCKING_CATEGORIES,
    critique_type_for_category,
)
from utils.llm_calls.author_vqa_critique_routing import (
    deterministic_critique_supersedes_vqa,
    with_advisory_findings as _with_advisory_findings,
)
from utils.authored_dom_quality import (
    build_compact_dom_map,
    ensure_authored_qids,
    has_dom_quality_regression,
    inspect_authored_dom,
    normalize_browser_metrics,
    validate_declared_authored_qids,
)
from utils.authored_patch_engine import apply_authored_patch
from utils.authored_illustrations import (
    apply_authored_illustration,
    strip_illustration_slots,
)
from utils.llm_calls.author_slide import (
    EXACT_RECONSTRUCTION,
    STYLE_REFERENCE,
    AUTHOR_CONCURRENCY,
    AuthoredStyleLike,
    Brand,
    author_slide_html,
    authored_semantic_dual_output_mode,
    is_valid_slide_html,
    normalize_reference_mode,
    validate_authored_slide_html,
)
from utils.llm_calls.author_slide_patch import (
    build_structural_repair_scopes,
    propose_authored_slide_patch,
)
from utils.llm_calls.author_deck import plan_authored_visual_story
from utils.authored_styles import reference_images_for_role
from utils.llm_calls.critique_slide import (
    CRITIQUE_PROMPT_VERSION,
    STYLE_COMPARISON_PROMPT_VERSION,
    STYLE_COMPARISON_REGRESSION_PREFIX as _STYLE_COMPARISON_REGRESSION_PREFIX,
    CritiqueIssue,
    SlideCritique,
    StyleComparisonCritique,
    critique_slide_image,
    critique_slide_style_comparison,
)
from utils.llm_provider import get_model


async def review_planned_source_images(
    *, html: str, png: bytes, assignments: Sequence[Mapping], candidates: Mapping,
    geometry_receipt: Mapping, slide_index: int, slide_context: str = "",
) -> dict:
    _vision_helpers.bind_runtime(sys.modules[__name__])
    return await _vision_helpers.review_planned_source_images(
        html=html, png=png, assignments=assignments, candidates=candidates,
        geometry_receipt=geometry_receipt, slide_index=slide_index, slide_context=slide_context,
    )


_slide_capture = import_module("utils.slide_capture")
ensure_browser_metrics = _slide_capture.ensure_browser_metrics
get_cached_browser_metrics = _slide_capture.get_cached_browser_metrics
render_html_to_png = _slide_capture.render_html_to_png
render_html_to_png_with_metrics = _slide_capture.render_html_to_png_with_metrics
_author_structural_repair_evidence = import_module("utils.llm_calls.author_structural_repair_evidence")
AuthoredVqaResidualDomIssuesError = _author_structural_repair_evidence.AuthoredVqaResidualDomIssuesError
StructuralRepairBudget = _author_structural_repair_evidence.StructuralRepairBudget
_advance_repair_evaluator = _author_structural_repair_evidence._advance_repair_evaluator
_measured_structural_issue_qids = _author_structural_repair_evidence._measured_structural_issue_qids
_repair_evaluator_for = _author_structural_repair_evidence._repair_evaluator_for
_structural_compact_dom = _author_structural_repair_evidence._structural_compact_dom
_author_structural_repair_loop = import_module("utils.llm_calls.author_structural_repair_loop")
_revise_structural_candidates_impl = _author_structural_repair_loop.revise_structural_candidates
_author_vision_qa_style = import_module("utils.llm_calls.author_vision_qa_style")
_style_comparison_cache_key = _author_vision_qa_style._style_comparison_cache_key
_style_comparison_cached = _author_vision_qa_style._style_comparison_cached
critique_authored_style_comparison = _author_vision_qa_style.critique_authored_style_comparison
style_comparison_failure_diagnostic = _author_vision_qa_style.style_comparison_failure_diagnostic


LOGGER = logging.getLogger(__name__)
_ORIGINAL_RENDER_HTML_TO_PNG = render_html_to_png


# Pure HTML text work, extracted to keep this module's growth in check.
from utils.llm_calls.authored_semantic_alias_canonicalizer import (  # noqa: E402
    _canonicalize_full_reauthor_semantic_aliases as _canonicalize_full_reauthor_semantic_aliases,
)


class _ImageServiceLike(Protocol):
    async def generate_image(self, prompt: ImagePrompt) -> object: ...


async def _render_correction_html(
    html: str, *, index: int | None = None, canvas: object | None = None,
) -> tuple[str, bytes]:
    _vision_helpers.bind_runtime(sys.modules[__name__])
    return await _vision_helpers._render_correction_html(html, index=index, canvas=canvas)


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "").strip())
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def _env_nonnegative_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "").strip())
        return value if value >= 0 else default
    except (TypeError, ValueError):
        return default


# Vision review and slide authoring have different provider/latency characteristics, so
# give review its own process-wide limit. The default preserves the previous fan-out;
# deploys that see provider throttling can tune it independently without slowing first-pass
# authoring. Lazily created inside the running loop.
VQA_CONCURRENCY = _env_int("AUTHORED_VQA_CONCURRENCY", AUTHOR_CONCURRENCY)
_vqa_sem: Optional[asyncio.Semaphore] = None

# Repeated export/regeneration commonly presents an identical rendered slide to QA. Cache
# successful structured reviews and coalesce identical in-flight calls so those workflows
# do not pay for the same multimodal request again. Include the model and a prompt-version
# token in the fingerprint so configuration or reviewer-policy changes invalidate safely.
VQA_CACHE_SIZE = _env_nonnegative_int("AUTHORED_VQA_CACHE_SIZE", 64)
_VQA_PROMPT_VERSION = f"authored-vqa-v2:{CRITIQUE_PROMPT_VERSION}"
STYLE_COMPARISON_REGRESSION_PREFIX = _STYLE_COMPARISON_REGRESSION_PREFIX
STYLE_COMPARISON_VQA_PROMPT_VERSION = (
    f"authored-style-comparison-v2:{STYLE_COMPARISON_PROMPT_VERSION}"
)
_vqa_cache: "OrderedDict[str, SlideCritique]" = OrderedDict()
_vqa_inflight: Dict[str, "asyncio.Task[SlideCritique]"] = {}
_style_comparison_vqa_cache: "OrderedDict[str, StyleComparisonCritique]" = OrderedDict()
_style_comparison_vqa_inflight: Dict[str, "asyncio.Task[StyleComparisonCritique]"] = {}
_style_comparison_vqa_failures: "OrderedDict[str, dict[str, object]]" = OrderedDict()


def _get_vqa_sem() -> asyncio.Semaphore:
    global _vqa_sem
    if _vqa_sem is None:
        _vqa_sem = asyncio.Semaphore(VQA_CONCURRENCY)
    return _vqa_sem


def _critique_cache_key(
    image: bytes, context: Optional[str], *, source_image: Optional[bytes] = None,
    reference_mode: str = STYLE_REFERENCE,
) -> str:
    _vision_helpers.bind_runtime(sys.modules[__name__])
    return _vision_helpers._critique_cache_key(
        image, context, source_image=source_image, reference_mode=reference_mode,
    )


async def _critique_cached(
    image: bytes,
    context: Optional[str],
    *,
    source_image: Optional[bytes] = None,
    reference_mode: str = STYLE_REFERENCE,
    slide_index: Optional[int] = None,
    slot_run: str = "",
) -> Optional[StyleComparisonCritique]:
    """Return one best-effort critique, caching only successful model responses."""
    mode = normalize_reference_mode(reference_mode)
    if mode == EXACT_RECONSTRUCTION and source_image is None:
        raise ValueError("Exact reconstruction critique requires a source image")

    async def invoke() -> StyleComparisonCritique:
        async with _get_vqa_sem():
            if mode == EXACT_RECONSTRUCTION:
                return await critique_slide_image(
                    image,
                    slide_context=context,
                    source_image=source_image,
                    reference_mode=mode,
                    slide_index=slide_index,
                    slot_run=slot_run,
                )
            # Keep the style-reference/default call byte-for-byte compatible with the
            # established render-only reviewer contract.
            return await critique_slide_image(
                image,
                slide_context=context,
                slide_index=slide_index,
                slot_run=slot_run,
            )

    if VQA_CACHE_SIZE <= 0:
        try:
            return await invoke()
        except Exception:
            return None

    key = _critique_cache_key(
        image,
        context,
        source_image=source_image,
        reference_mode=mode,
    )
    cached = _vqa_cache.get(key)
    if cached is not None:
        _vqa_cache.move_to_end(key)
        return cached

    task = _vqa_inflight.get(key)
    if task is None:
        task = asyncio.create_task(invoke())
        _vqa_inflight[key] = task

    try:
        critique = await asyncio.shield(task)
    except Exception:
        return None
    finally:
        if _vqa_inflight.get(key) is task and task.done():
            _vqa_inflight.pop(key, None)

    _vqa_cache[key] = critique
    _vqa_cache.move_to_end(key)
    while len(_vqa_cache) > VQA_CACHE_SIZE:
        _vqa_cache.popitem(last=False)
    return critique


def _feedback(critique: SlideCritique, *, exact_reconstruction: bool = False, planned_images: bool = False) -> str:
    issues = (
        "; ".join(f"{x.type} — {x.detail}" for x in critique.issues) or "visual problems"
    )
    if exact_reconstruction:
        return (
            "A rendered preview of THIS exact reconstruction had these visual problems: "
            f"{issues}. Re-author it against the attached source image and fix only "
            "unintended implementation defects. Preserve source text character-for-"
            "character, including line breaks; preserve exact typography and geometry; "
            "and do not remove intentional overlap/cropping, restructure the layout, "
            "shorten content, or make discretionary design improvements."
        )
    return (
        "A rendered preview of THIS slide had these visual problems: "
        f"{issues}. Re-author the slide to fix them: keep ALL content within the "
        "declared canvas frame (no unintended overflow or clipping), "
        + ("preserve planned image-group overlap while protecting text and critical image regions, ensure "
           if planned_images else "remove unintended element overlap, ensure ") +
        "strong contrast, delete any placeholder text, avoid unnecessarily shrinking text "
        "until it becomes difficult to read, and rebalance the layout — while "
        "keeping the same role, language and shared design system."
    )


async def critique_authored(
    pngs: List[bytes],
    contexts: Optional[List[Optional[str]]] = None,
    *,
    source_images: Optional[List[bytes]] = None,
    reference_mode: str = STYLE_REFERENCE,
    slide_indices: Optional[Sequence[int]] = None,
    critique_round: str = "",
) -> List[Optional[SlideCritique]]:
    """Critique each rendered authored slide. None where a review failed (so one bad
    call does not sink the pass).

    ``slide_indices`` gives every reviewed slide its own provider-admission
    identity. The ``vqa`` stage runs the whole deck inside one deck-level usage
    scope, so without it the second slide onward is admitted as a replay of the
    first (``utils.structured_semantic_slots.deck_stage_slot_suffix``). A caller
    that reviews the same slides a second time inside that scope must separate
    its round as well, with ``critique_round``.
    """
    mode = normalize_reference_mode(reference_mode)
    ctx = contexts or []
    exact_sources: Optional[List[bytes]] = None
    if mode == EXACT_RECONSTRUCTION:
        if source_images is None or len(source_images) != len(pngs):
            raise ValueError(
                "Exact reconstruction critique requires one source image per render"
            )
        if any(not isinstance(source, bytes) or not source for source in source_images):
            raise ValueError(
                "Exact reconstruction critique source images must be non-empty bytes"
            )
        exact_sources = source_images

    async def review(i: int, img: bytes) -> Optional[SlideCritique]:
        context = ctx[i] if i < len(ctx) else None
        slide_index = slide_indices[i] if slide_indices is not None else None
        if mode == STYLE_REFERENCE:
            return await _critique_cached(
                img, context, slide_index=slide_index, slot_run=critique_round
            )
        return await _critique_cached(
            img,
            context,
            source_image=exact_sources[i] if exact_sources is not None else None,
            reference_mode=mode,
            slide_index=slide_index,
            slot_run=critique_round,
        )

    return list(await asyncio.gather(*[review(i, p) for i, p in enumerate(pngs)]))


_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


def deterministic_dom_critique(report: Any) -> SlideCritique | None:
    """Project a measured DOM report into the critique the repair round reads.

    Bench run 36 (2026-09-05) lost a whole deck here: a dict literal mapped the
    category, and the two detectors ``f0d2b126c`` registered were not in it, so
    slides 3..17 raised ``KeyError`` instead of getting a brief.  Both lookups
    are total now (``utils.authored_dom_categories``).
    """

    if report.telemetry_status != "available" or not report.issues:
        return None
    ranked = sorted(
        report.issues, key=lambda item: _SEVERITY_RANK.get(item.severity, 1)
    )[:8]
    return SlideCritique(
        needs_fix=True,
        issues=[
            CritiqueIssue(
                type=critique_type_for_category(item.category),
                severity=item.severity if item.severity in _SEVERITY_RANK else "medium",
                detail=(
                    f"DOM {item.category}: {item.detail}; targets: "
                    f"{', '.join(item.qids) or 'slide'}"
                )[:200],
            )
            for item in ranked
        ],
    )


async def review_slides_for_evidence(
    pngs: Sequence[bytes],
    *,
    slide_indices: Sequence[int],
    contexts: Optional[Sequence[Optional[str]]] = None,
    reference_mode: str = STYLE_REFERENCE,
    critique_round: str = "evidence",
) -> dict[int, Optional[SlideCritique]]:
    """Vision review as an EVIDENCE SOURCE only: it produces no HTML.

    The single repair loop
    (``docs/authored-verbatim-placeholder-authoring-design-20260911.md`` 2.5)
    keeps the visual review and drops its ability to rewrite a slide: what the
    reviewer sees becomes part of the loop's one prompt, and the loop's own
    candidates face the same admission every other candidate does. This is the
    existing ``critique_authored`` call with its result returned per slide and
    nothing else done with it -- no re-author, no patch, no fallback.
    """

    if len(slide_indices) != len(pngs):
        raise ValueError("evidence review needs one slide index per render")
    critiques = await critique_authored(
        list(pngs),
        list(contexts) if contexts is not None else None,
        reference_mode=reference_mode,
        slide_indices=list(slide_indices),
        critique_round=critique_round,
    )
    return {int(index): critiques[i] for i, index in enumerate(slide_indices)}


async def _revise_structural_candidates(**kwargs: Any) -> Any:
    return await _revise_structural_candidates_impl(
        runtime=sys.modules[__name__],
        **kwargs,
    )


async def revise_authored_deck(
    htmls: List[str],
    pngs: List[bytes],
    contents: List[str],
    roles: List[str],
    brand: Brand,
    design_system: str,
    max_cycles: int = 1,
    style: Optional[AuthoredStyleLike] = None,
    slide_indices: Optional[List[int]] = None,
    total_slides: Optional[int] = None,
    *,
    initial_critiques: Optional[List[Optional[SlideCritique]]] = None,
    instructions: Optional[str] = None,
    reference_images_by_slide: Optional[List[Tuple[Path, ...]]] = None,
    source_images_by_slide: Optional[Sequence[Mapping]] = None,
    source_image_placement_plans_by_slide: Optional[Sequence[Mapping]] = None,
    return_details: bool = False,
    force_full_reauthor_indices: Optional[Iterable[int]] = None,
    chapter_prefixes: Optional[Sequence[Optional[str]]] = None,
    image_service: Optional[_ImageServiceLike] = None,
    illustration_slots: Optional[Sequence[bool]] = None,
    infographic_briefs: Optional[Sequence[AuthoredSlideInfographic | None]] = None,
    infographic_assets: Optional[InfographicAssetContext] = None,
    local_patch_only: bool = False,
    fail_on_residual_dom_issues: bool = False,
    capacity_briefs: Optional[Sequence[str]] = None,
    # QUAL-04: receives every correction record even when ``return_details`` is off.
    repair_details_sink: Optional[list[dict[str, Any]]] = None,
    # QUAL-04 reachability: how many slides the vision-QA MODEL actually looked
    # at, as distinct from how many the deterministic critique flagged.
    review_stats_sink: Optional[dict[str, Any]] = None,
    provider_recovery_allowed: Optional[Sequence[bool]] = None,
    provider_recovery_blocked_issue_types: Optional[Collection[str]] = None,
    allow_patch_proposal_retry: bool = True,
    candidate_validator: Optional[
        Callable[[int, str, Mapping[str, Any]], Awaitable[None]]
    ] = None,
    candidate_evaluator: Optional[
        Callable[[int, str, Mapping[str, Any]], Awaitable[RepairAssessment]]
    ] = None,
    candidate_evaluators: Optional[Mapping[
        int, Callable[[int, str, Mapping[str, Any]], Awaitable[RepairAssessment]]
    ]] = None,
    initial_browser_metrics: Optional[Sequence[Mapping[str, Any] | None]] = None,
    initial_assessments: Optional[Sequence[RepairAssessment | None]] = None,
    initial_repair_history: Optional[Sequence[Sequence[Mapping[str, Any]]]] = None,
    structural_results_sink: Optional[list[SlideRepairResult]] = None,
    structural_candidate_capture: Optional[
        Callable[[str, int], Awaitable[tuple[str, bytes, Mapping[str, Any]]]]
    ] = None,
    structural_budgets: Optional[Mapping[int, StructuralRepairBudget]] = None,
    canvas: object | None = None,
) -> Any:
    """Bounded critique -> re-author -> re-render loop over an authored deck. Returns
    the (possibly revised) htmls + pngs and the local indices that changed.

    ``slide_indices`` and ``total_slides`` let callers review a subset while keeping
    the original deck position in the authoring prompt. This matters for cover/closing
    composition and slide numbering during post-generation quality review.

    ``chapter_prefixes`` carries the same per-slide eyebrow prefix the first
    authoring pass was given, one entry per REVIEWED slide. A full re-author is
    an ordinary authoring call, so without it a corrected slide would be asked
    for a breadcrumb and handed no chapter to open it with.

    ``illustration_slots`` carries the deck plan's decision for each REVIEWED
    slide. A full re-author must consume any author-only illustration marker
    before rendering just like the first-pass pipeline: an allowed slot is
    filled through ``image_service`` when the style supports it, while every
    other unresolved slot is removed.
    """
    n = len(htmls)
    if instructions:
        from utils.llm_calls.author_slide_prompt import user_direction_brief
        design_system += "\n\n" + user_direction_brief(instructions)
    canvas_width_px = int(getattr(canvas, "width_px", 1280))
    canvas_height_px = int(getattr(canvas, "height_px", 720))
    render_kwargs = (
        {}
        if (canvas_width_px, canvas_height_px) == (1280, 720)
        else {"width_px": canvas_width_px, "height_px": canvas_height_px}
    )
    ensure_metrics = partial(ensure_browser_metrics, **render_kwargs)
    cached_metrics = partial(get_cached_browser_metrics, **render_kwargs)
    if candidate_evaluator is not None and candidate_evaluators is not None:
        raise ValueError("candidate_evaluator and candidate_evaluators are mutually exclusive")
    if candidate_validator is not None and (
        candidate_evaluator is not None or candidate_evaluators is not None
    ):
        raise ValueError("candidate_validator and candidate_evaluator are mutually exclusive")
    if not (len(pngs) == len(contents) == len(roles) == n):
        raise ValueError("Authored QA inputs must have matching lengths")
    absolute_indices = list(slide_indices) if slide_indices is not None else list(range(n))
    if len(absolute_indices) != n:
        raise ValueError("slide_indices must match the reviewed slide count")
    if initial_browser_metrics is not None and len(initial_browser_metrics) != n:
        raise ValueError("initial_browser_metrics must match the reviewed slide count")
    if initial_assessments is not None and len(initial_assessments) != n:
        raise ValueError("initial_assessments must match the reviewed slide count")
    if initial_repair_history is not None and len(initial_repair_history) != n:
        raise ValueError("initial_repair_history must match the reviewed slide count")
    deck_size = total_slides if total_slides is not None else n
    if deck_size < n:
        raise ValueError("total_slides cannot be smaller than the reviewed slide count")
    if initial_critiques is not None and len(initial_critiques) != n:
        raise ValueError("initial_critiques must match the reviewed slide count")
    if chapter_prefixes is not None and len(chapter_prefixes) != n:
        raise ValueError("chapter_prefixes must match the reviewed slide count")
    if illustration_slots is not None and len(illustration_slots) != n:
        raise ValueError("illustration_slots must match the reviewed slide count")
    if infographic_briefs is not None and len(infographic_briefs) != n:
        raise ValueError("infographic_briefs must match the reviewed slide count")
    if capacity_briefs is not None and len(capacity_briefs) != n:
        raise ValueError("capacity_briefs must match the reviewed slide count")
    if provider_recovery_allowed is not None and len(provider_recovery_allowed) != n:
        raise ValueError(
            "provider_recovery_allowed must match the reviewed slide count"
        )
    blocked_recovery_issue_types = frozenset(
        str(value) for value in (provider_recovery_blocked_issue_types or ())
    )
    forced_full_reauthor = set(force_full_reauthor_indices or ())
    for per_slide in (source_images_by_slide, source_image_placement_plans_by_slide):
        if per_slide is not None and len(per_slide) != len(htmls):
            raise ValueError("planned source image inputs must match slides")
    planned_missing_indices = {
        index for index, plan in enumerate(source_image_placement_plans_by_slide or ())
        if plan.get("repair_missing_image") and source_images_by_slide and source_images_by_slide[index]
    }
    forced_full_reauthor.update(planned_missing_indices)
    if any(type(index) is not int or index < 0 or index >= n for index in forced_full_reauthor):
        raise ValueError("force_full_reauthor_indices contains an invalid local index")

    reference_mode = normalize_reference_mode(
        getattr(style, "reference_mode", STYLE_REFERENCE)
    )
    if candidate_evaluator is not None or candidate_evaluators is not None:
        if reference_mode == EXACT_RECONSTRUCTION or forced_full_reauthor:
            raise ValueError("Structural source repair cannot re-author or reconstruct slides")
        return await _revise_structural_candidates(
            htmls=htmls,
            pngs=pngs,
            absolute_indices=absolute_indices,
            max_cycles=max_cycles,
            candidate_evaluator=candidate_evaluator,
            candidate_evaluators=candidate_evaluators,
            initial_browser_metrics=initial_browser_metrics,
            initial_assessments=initial_assessments,
            initial_repair_history=initial_repair_history,
            initial_critiques=initial_critiques,
            provider_recovery_allowed=provider_recovery_allowed,
            blocked_recovery_issue_types=blocked_recovery_issue_types,
            fail_on_residual_dom_issues=fail_on_residual_dom_issues,
            return_details=return_details,
            repair_details_sink=repair_details_sink,
            structural_results_sink=structural_results_sink,
            review_stats_sink=review_stats_sink,
            structural_candidate_capture=structural_candidate_capture,
            structural_budgets=structural_budgets,
        )
    visual_plan = (
        None
        if reference_mode == EXACT_RECONSTRUCTION
        else plan_authored_visual_story(
            contents,
            roles,
            illustration_slots=tuple(illustration_slots or ()),
            infographic_briefs=tuple(infographic_briefs or ()),
        )
    )
    exact_source_images: Optional[List[bytes]] = None
    if reference_mode == EXACT_RECONSTRUCTION:
        if reference_images_by_slide is None or len(reference_images_by_slide) != n:
            raise ValueError(
                "Exact reconstruction QA requires one per-slide reference mapping"
            )
        exact_source_images = []
        for refs in reference_images_by_slide:
            if len(refs) != 1:
                raise ValueError(
                    "Exact reconstruction QA requires exactly one source per slide"
                )
            try:
                source = Path(refs[0]).read_bytes()
            except OSError as error:
                raise ValueError(
                    "Exact reconstruction QA source image is unreadable"
                ) from error
            if not source:
                raise ValueError("Exact reconstruction QA source image is empty")
            exact_source_images.append(source)

    htmls = list(htmls)
    pngs = list(pngs)
    fixed: List[int] = []
    started = time.monotonic()
    critique_work = 0.0
    correction_work = 0.0
    repair_details: list[dict[str, Any]] = []

    # A 43-slide deck is larger than the default in-memory metrics LRU.  Source
    # reflow publication needs telemetry for every slide, so retain the concrete
    # mappings locally instead of consulting the cache later and mistaking an
    # eviction for a clean deterministic pass.
    retained_metrics: list[Mapping[str, Any] | None] = (
        list(
            await asyncio.gather(
                *(
                    captured_metrics_or_existing(
                        html, index=absolute_indices[i], ensure=ensure_metrics
                    )
                    for i, html in enumerate(htmls)
                )
            )
        )
        if fail_on_residual_dom_issues
        else [cached_metrics(html) for html in htmls]
    )

    def local_report(html: str, metrics: Mapping[str, Any] | None):
        return inspect_authored_dom(
            html,
            metrics,
            reference_mode=reference_mode,
            strict_structural_off_canvas=fail_on_residual_dom_issues,
        )

    async def metrics_for(html: str, index: int | None = None) -> Mapping[str, Any] | None:
        if fail_on_residual_dom_issues:
            return await captured_metrics_or_existing(
                html, index=index, ensure=ensure_metrics
            )
        return cached_metrics(html)

    def deterministic_critique(
        html: str, metrics: Mapping[str, Any] | None
    ) -> tuple[SlideCritique | None, bool]:
        """The measured brief, and whether it stands in for the model call."""

        report = local_report(html, metrics)
        return (
            deterministic_dom_critique(report),
            deterministic_critique_supersedes_vqa(report),
        )

    async def full_reauthor(
        i: int, current_html: str, critique: SlideCritique
    ) -> tuple[str, bytes]:
        content = (
            f"{contents[i]}\n\n"
            f"{_feedback(critique, exact_reconstruction=reference_mode == EXACT_RECONSTRUCTION, planned_images=bool(source_images_by_slide and source_images_by_slide[i]))}"
        )
        refs = (
            reference_images_by_slide[i]
            if reference_mode == EXACT_RECONSTRUCTION
            and reference_images_by_slide is not None
            else reference_images_for_role(style, roles[i])
        )
        reference_kwargs: dict[str, Any] = {}
        if refs:
            reference_kwargs["reference_images"] = refs
        if reference_mode == EXACT_RECONSTRUCTION:
            reference_kwargs["reference_mode"] = reference_mode
        if chapter_prefixes is not None:
            reference_kwargs["chapter_breadcrumb"] = True
            reference_kwargs["chapter_prefix"] = chapter_prefixes[i]
        illustration_allowed = (
            illustration_slots[i] if illustration_slots is not None else True
        )
        if (
            reference_mode != EXACT_RECONSTRUCTION
            and illustration_slots is not None
        ):
            reference_kwargs["illustration_allowed"] = illustration_allowed
        if visual_plan is not None:
            reference_kwargs["visual_intent"] = visual_plan.at(i)
        if (
            reference_mode != EXACT_RECONSTRUCTION
            and infographic_briefs is not None
        ):
            reference_kwargs["infographic"] = infographic_briefs[i]
        if capacity_briefs is not None:
            reference_kwargs["capacity_brief"] = capacity_briefs[i]
        offered_sources = source_images_by_slide[i] if source_images_by_slide else {}
        if offered_sources and reference_mode != EXACT_RECONSTRUCTION:
            reference_kwargs["source_images"] = offered_sources
            reference_kwargs["source_image_placement_plan"] = (
                source_image_placement_plans_by_slide[i] if source_image_placement_plans_by_slide else None
            )
        new_html = await author_slide_html(
            content,
            design_system,
            brand,
            roles[i],
            absolute_indices[i],
            deck_size,
            # The durable generation preset is request-scoped and remains
            # authoritative during the optional re-author pass as well.
            reasoning_effort=None,
            receipt_retry_class="recovery",
            receipt_attempt_ordinal=2,
            allow_provider_fallback=False,
            instructions=instructions,
            **reference_kwargs,
        )
        new_html = admit_author_html(new_html, index=absolute_indices[i], producer="vqa_repair", validate_policy=False)
        new_html = _canonicalize_full_reauthor_semantic_aliases(new_html)
        if not is_valid_slide_html(new_html):
            raise ValueError("full re-author returned invalid slide HTML")
        lowered_html = new_html.lower()
        if offered_sources and reference_mode != EXACT_RECONSTRUCTION:
            new_html = await apply_authored_illustration(
                new_html, style, image_service, slide_index=absolute_indices[i],
                source_images=offered_sources, generate_fallback=False, raise_on_failure=True,
            )
            lowered_html = new_html.lower()
        if "data-asset-id" in lowered_html or "data-source-slot-id" in lowered_html:
            # This re-authoring path never offers source-image candidates.  Silently
            # finalizing an invented/stale asset id would leave a src-less <img> box.
            # Reject the candidate so the existing fail-closed path keeps the original.
            raise ValueError("full re-author returned an unresolved source-image slot")
        had_authoring_only_attributes = "data-illust-prompt" in lowered_html
        if reference_mode == EXACT_RECONSTRUCTION and had_authoring_only_attributes:
            # Exact reconstruction has no illustration stage: consuming the marker
            # would invent pixels, while stripping only the attribute would preserve
            # an empty box that was not in the source.
            raise ValueError("exact full re-author returned an illustration slot")
        if reference_mode != EXACT_RECONSTRUCTION:
            style_prompt = (getattr(style, "illustration_prompt", None) or "").strip()
            can_generate = (
                illustration_allowed
                and image_service is not None
                and (bool(style_prompt) or infographic_assets is not None)
            )
            brief = infographic_briefs[i] if infographic_briefs is not None else None
            if can_generate:
                # A scene's cutout goes first, like the first-pass stage: when
                # it lands there is no slot left for the ordinary fill.
                new_html = await fill_scene_cutout(
                    new_html,
                    slide=brief,
                    context=infographic_assets,
                    slide_index=absolute_indices[i],
                )
                if style_prompt:
                    new_html = await apply_authored_illustration(
                        new_html,
                        style,
                        image_service,
                        slide_index=absolute_indices[i],
                        slide_content=contents[i],
                        raise_on_failure=True,
                    )
                else:
                    # Without a style prompt the cutout was the only fill; a
                    # slot it did not take is withdrawn like any failed one.
                    new_html = strip_illustration_slots(new_html)
            else:
                # Finalization alone would turn an unresolved authored slot into
                # a src-less <img> box. Withdraw it through the same path used by
                # the first-pass illustration stage when the deck disallows it.
                new_html = strip_illustration_slots(new_html)
            # The re-authored figure carries the library's built-in icons again;
            # give it the same pack icons its first draft had.
            new_html = await substitute_figure_icons(
                new_html, slide=brief, context=infographic_assets
            )
        finalized_html = admit_expanded_html(new_html, index=absolute_indices[i], validate=validate_authored_slide_html, producer="vqa_repair")
        if had_authoring_only_attributes:
            new_html = finalized_html
        # Fallback may replace structure, but every resulting edit target still gets
        # a deterministic id. The normal local-patch path preserves every existing id.
        if return_details and authored_semantic_dual_output_mode() != "declared":
            new_html = ensure_authored_qids(new_html)
        elif return_details:
            validate_declared_authored_qids(new_html)
        return await _render_correction_html(
            new_html, index=absolute_indices[i], canvas=canvas
        )

    async def review_and_fix(
        i: int,
    ) -> Tuple[
        int,
        str,
        bytes,
        bool,
        float,
        float,
        dict[str, Any] | None,
        object,
        bool,
    ]:
        """Pipeline one slide through review -> correction -> render immediately.

        This intentionally avoids the former deck-wide review barrier: a fast review can
        begin its expensive correction while slower reviews are still in flight.
        """
        current_html = htmls[i]
        current_metrics = retained_metrics[i]
        if return_details:
            if authored_semantic_dual_output_mode() == "declared":
                validate_declared_authored_qids(current_html)
            else:
                current_html = ensure_authored_qids(current_html)
        current_png = pngs[i]
        changed = False
        # QUAL-04 reachability: "the model looked at this slide" is a different
        # fact from "the deterministic critique produced findings", and until
        # 2026-09-07 the receipt could not tell them apart.
        vqa_reviewed = False
        slide_critique_work = 0.0
        slide_correction_work = 0.0
        repair_detail: dict[str, Any] | None = None

        def failure_detail(stage: str, error: Exception) -> dict[str, str]:
            return {
                "stage": stage,
                "code": f"{stage}_failed",
                "error_type": type(error).__name__,
            }

        def log_failure(stage: str, error: Exception) -> None:
            LOGGER.error(
                "[authored-vqa] correction failed slide_index=%d stage=%s error_type=%s",
                absolute_indices[i],
                stage,
                type(error).__name__,
                exc_info=(type(error), error, error.__traceback__),
            )

        for cycle in range(max(1, max_cycles)):
            patch_ms = 0.0
            critique, supersedes_vqa = deterministic_critique(
                current_html, current_metrics
            )
            advisory = None if supersedes_vqa else critique
            critique_source = "deterministic"
            if critique is not None and supersedes_vqa:
                pass
            elif cycle == 0 and initial_critiques is not None:
                critique_source = "initial"
                critique = _with_advisory_findings(initial_critiques[i], advisory)
            else:
                critique_source = "vision_qa"
                review_started = time.monotonic()
                # This re-review runs per slide AND per cycle inside the one
                # deck-level `vqa` scope, so both have to reach the slot.
                cycle_round = f"cycle-{cycle}"
                if reference_mode == EXACT_RECONSTRUCTION:
                    critiques = await critique_authored(
                        [current_png],
                        contexts=[contents[i][:240]],
                        source_images=[exact_source_images[i]],
                        reference_mode=reference_mode,
                        slide_indices=[absolute_indices[i]],
                        critique_round=cycle_round,
                    )
                else:
                    # The STYLE_REFERENCE call keeps its positional shape; the
                    # two identity keywords are the only addition, and a double
                    # that accepts **kwargs stays compatible.
                    critiques = await critique_authored(
                        [current_png],
                        contexts=[contents[i][:240]],
                        slide_indices=[absolute_indices[i]],
                        critique_round=cycle_round,
                    )
                slide_critique_work += time.monotonic() - review_started
                vqa_reviewed = True
                critique = _with_advisory_findings(
                    critiques[0] if critiques else None, advisory
                )
            if critique is None or not critique.needs_fix:
                break

            correction_started = time.monotonic()
            fallback_reason = (
                "forced_after_local_rejection"
                if i in forced_full_reauthor
                else "local_patch_not_enabled"
            )
            new_html: str | None = None
            new_png: bytes | None = None
            patch_attempted = False
            patch_succeeded = False
            patch_failure: dict[str, str] | None = None
            fallback_attempted = False
            fallback_succeeded = False
            fallback_failure: dict[str, str] | None = None
            element_changes: list[dict[str, Any]] = []
            recovery_allowed = (
                provider_recovery_allowed is None
                or provider_recovery_allowed[i] is True
            ) and not any(
                issue.type in blocked_recovery_issue_types
                for issue in critique.issues
            )
            try:
                if (
                    return_details
                    and i not in forced_full_reauthor
                    and recovery_allowed
                ):
                    patch_attempted = True
                    compact_dom = [
                        node.to_dict()
                        for node in build_compact_dom_map(
                            current_html,
                            (
                                current_metrics
                                if fail_on_residual_dom_issues
                                else cached_metrics(current_html)
                            ),
                        )
                    ]
                    patch_started = time.monotonic()
                    try:
                        proposal = await propose_authored_slide_patch(
                            current_png,
                            compact_dom,
                            critique,
                            slide_index=absolute_indices[i],
                            slot_run=f"cycle-{cycle}",
                            reference_mode=reference_mode,
                            allow_source_reflow_safe_styles=fail_on_residual_dom_issues,
                            retry_invalid_model_patch=(
                                fail_on_residual_dom_issues
                                and allow_patch_proposal_retry
                            ),
                        )
                    except Exception as error:
                        log_failure("patch_proposal", error)
                        patch_failure = failure_detail("patch_proposal", error)
                        proposal = None
                    patch_ms = (time.monotonic() - patch_started) * 1000
                    if proposal is not None:
                        fallback_reason = (
                            proposal.fallback_reason
                            or proposal.failure_reason
                            or "local_patch_unavailable"
                        )
                    elif patch_failure is not None:
                        fallback_reason = patch_failure["code"]
                    if proposal is not None and proposal.ok and proposal.plan is not None:
                        try:
                            applied = apply_authored_patch(
                                current_html,
                                proposal.plan,
                                reference_mode=reference_mode,
                                allow_source_reflow_safe_styles=(
                                    fail_on_residual_dom_issues
                                ),
                            )
                        except Exception as error:
                            log_failure("patch_application", error)
                            patch_failure = failure_detail("patch_application", error)
                            applied = None
                        if applied is None:
                            fallback_reason = patch_failure["code"]
                        elif applied.ok and is_valid_slide_html(applied.html):
                            try:
                                candidate_html, candidate_png = await _render_correction_html(
                                    applied.html, index=absolute_indices[i], canvas=canvas
                                )
                            except Exception as error:
                                log_failure("patch_render", error)
                                patch_failure = failure_detail("patch_render", error)
                                fallback_reason = patch_failure["code"]
                            else:
                                candidate_metrics = await metrics_for(
                                    candidate_html, absolute_indices[i]
                                )
                                if candidate_validator is not None:
                                    await candidate_validator(
                                        absolute_indices[i],
                                        candidate_html,
                                        candidate_metrics,
                                    )
                                if not has_dom_quality_regression(
                                    local_report(current_html, current_metrics),
                                    local_report(candidate_html, candidate_metrics),
                                ):
                                    new_html, new_png = candidate_html, candidate_png
                                    patch_succeeded = True
                                    element_changes = [
                                        {
                                            "element_id": change.qid,
                                            "action": change.action,
                                            "before": change.before,
                                            "after": change.after,
                                            "path": "local_patch",
                                        }
                                        for change in applied.changes
                                    ]
                                else:
                                    fallback_reason = "deterministic_local_regression"
                                    patch_failure = {
                                        "stage": "patch_deterministic_check",
                                        "code": fallback_reason,
                                        "error_type": "DomQualityRegression",
                                    }
                        else:
                            fallback_reason = (
                                applied.fallback_reason
                                or applied.failure_reason
                                or "patch_application_failed"
                            )
                            patch_failure = {
                                "stage": "patch_application",
                                "code": fallback_reason,
                                "error_type": "PatchApplicationFailure",
                            }
                    elif proposal is not None:
                        patch_failure = {
                            "stage": "patch_proposal",
                            "code": fallback_reason,
                            "error_type": "PatchProposalFailure",
                        }
                if (
                    (new_html is None or new_png is None)
                    and (not local_patch_only or i in planned_missing_indices)
                    and recovery_allowed
                ):
                    fallback_started = time.monotonic()
                    fallback_attempted = True
                    try:
                        new_html, new_png = await full_reauthor(i, current_html, critique)
                    except Exception as error:
                        log_failure("full_reauthor", error)
                        fallback_failure = failure_detail("full_reauthor", error)
                        new_html = new_png = None
                    fallback_ms = (time.monotonic() - fallback_started) * 1000
                    fallback_succeeded = new_html is not None and new_png is not None
                    repair_detail = {
                        "local_index": i,
                        "slide_index": absolute_indices[i],
                        "path": "full_fallback",
                        "fallback_reason": fallback_reason,
                        "patch_ms": round(patch_ms, 3),
                        "fallback_ms": round(fallback_ms, 3),
                        "attempted": True,
                        "succeeded": fallback_succeeded,
                        "failure_detail": fallback_failure,
                        "patch": {
                            "attempted": patch_attempted,
                            "succeeded": patch_succeeded,
                            "failure_detail": patch_failure,
                        },
                        "fallback": {
                            "attempted": True,
                            "succeeded": fallback_succeeded,
                            "failure_detail": fallback_failure,
                        },
                        "element_changes": (
                            [
                                {
                                    "element_id": "slide-root",
                                    "action": "full_reauthor",
                                    "before": {"summary": "original authored slide"},
                                    "after": {"summary": "structurally re-authored slide"},
                                    "path": "full_fallback",
                                }
                            ]
                            if fallback_succeeded
                            else []
                        ),
                    }
                elif new_html is not None and new_png is not None:
                    repair_detail = {
                        "local_index": i,
                        "slide_index": absolute_indices[i],
                        "path": "local_patch",
                        "fallback_reason": None,
                        "patch_model": getattr(proposal, "model", None),
                        "patch_prompt_version": getattr(proposal, "prompt_version", None),
                        "patch_ms": round(patch_ms, 3),
                        "fallback_ms": 0.0,
                        "attempted": True,
                        "succeeded": True,
                        "failure_detail": None,
                        "patch": {
                            "attempted": patch_attempted,
                            "succeeded": patch_succeeded,
                            "failure_detail": patch_failure,
                        },
                        "fallback": {
                            "attempted": False,
                            "succeeded": False,
                            "failure_detail": None,
                        },
                        "element_changes": element_changes,
                    }
                else:
                    # A source-content reflow has already bound the admitted
                    # source images into this HTML.  A structural re-author
                    # does not receive those immutable image candidates and
                    # could silently drop them.  Keep the original slide when
                    # a bounded local CSS/text patch cannot be admitted.
                    repair_detail = {
                        "local_index": i,
                        "slide_index": absolute_indices[i],
                        "path": "local_patch",
                        "fallback_reason": fallback_reason,
                        "patch_ms": round(patch_ms, 3),
                        "fallback_ms": 0.0,
                        "attempted": patch_attempted,
                        "succeeded": False,
                        "failure_detail": patch_failure,
                        "patch": {
                            "attempted": patch_attempted,
                            "succeeded": False,
                            "failure_detail": patch_failure,
                        },
                        "fallback": {
                            "attempted": False,
                            "succeeded": False,
                            "failure_detail": None,
                        },
                        "element_changes": [],
                    }
                if new_html is not None and new_png is not None:
                    current_html = new_html
                    current_png = new_png
                    current_metrics = await metrics_for(current_html, absolute_indices[i])
                    changed = True
            except Exception as error:
                log_failure("correction", error)
                unexpected_failure = failure_detail("correction", error)
                repair_detail = {
                    "local_index": i,
                    "slide_index": absolute_indices[i],
                    "path": (
                        "full_fallback" if fallback_attempted else "local_patch"
                    ),
                    "fallback_reason": fallback_reason,
                    "patch_ms": round(patch_ms, 3),
                    "fallback_ms": 0.0,
                    "attempted": True,
                    "succeeded": False,
                    "failure_detail": unexpected_failure,
                    "patch": {
                        "attempted": patch_attempted,
                        "succeeded": patch_succeeded,
                        "failure_detail": patch_failure,
                    },
                    "fallback": {
                        "attempted": fallback_attempted,
                        "succeeded": fallback_succeeded,
                        "failure_detail": fallback_failure,
                    },
                    "element_changes": [],
                }
            finally:
                slide_correction_work += time.monotonic() - correction_started
                if repair_detail is not None:
                    repair_detail["critique_source"] = critique_source

        return (
            i,
            current_html,
            current_png,
            changed,
            slide_critique_work,
            slide_correction_work,
            repair_detail,
            local_report(current_html, current_metrics),
            vqa_reviewed,
        )

    # gather starts every per-slide pipeline concurrently while preserving input order.
    results = await asyncio.gather(*(review_and_fix(i) for i in range(n)))
    final_reports = []
    vqa_reviewed_slides = 0
    for (
        i,
        new_html,
        new_png,
        changed,
        review_time,
        correction_time,
        detail,
        final_report,
        slide_vqa_reviewed,
    ) in results:
        vqa_reviewed_slides += int(bool(slide_vqa_reviewed))
        critique_work += review_time
        correction_work += correction_time
        if changed:
            htmls[i] = new_html
            pngs[i] = new_png
            fixed.append(i)
        if detail is not None:
            repair_details.append(detail)
        final_reports.append(final_report)
    if repair_details_sink is not None:
        repair_details_sink.extend(repair_details)

    if fail_on_residual_dom_issues:
        blocking_categories = RESIDUAL_BLOCKING_CATEGORIES
        residual = [
            (index, report)
            for index, report in enumerate(final_reports)
            if report.telemetry_status != "available"
            or any(
                issue.category in blocking_categories
                and issue.severity in {"medium", "high"}
                for issue in report.issues
            )
        ]
        if residual:
            summary = ",".join(
                f"{absolute_indices[index] + 1}:"
                f"{report.telemetry_status}:"
                f"{sum(issue.category in blocking_categories and issue.severity in {'medium', 'high'} for issue in report.issues)}"
                for index, report in residual[:12]
            )
            raise AuthoredVqaResidualDomIssuesError(
                summary,
                htmls=htmls,
                pngs=pngs,
                fixed=fixed,
                repair_details=repair_details,
            )

    if review_stats_sink is not None:
        review_stats_sink["vqaReviewedSlideCount"] = vqa_reviewed_slides
        review_stats_sink["deterministicOnlySlideCount"] = n - vqa_reviewed_slides

    LOGGER.info(
        "[authored-vqa] slides=%d model_reviewed=%d fixed=%d total=%.1fs "
        "critique_work=%.1fs correction_work=%.1fs concurrency=%d cache_size=%d",
        n,
        vqa_reviewed_slides,
        len(fixed),
        time.monotonic() - started,
        critique_work,
        correction_work,
        VQA_CONCURRENCY,
        VQA_CACHE_SIZE,
    )

    if return_details:
        return htmls, pngs, fixed, repair_details
    return htmls, pngs, fixed
