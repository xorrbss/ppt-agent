"""Vision-QA self-correction for AUTHORED slides (opt-in). Reuses the existing
multimodal critique (critique_slide.py) on the rendered authored PNGs, then
RE-AUTHORS each flagged slide with the critique as feedback and re-renders it,
bounded to a few cycles. Mirrors utils/llm_calls/vision_qa.py — which targets the
template/compose path and captures from /pdf-maker — but operates on the authored
mode's in-memory HTML + PNGs. Best-effort: a failed critique/re-author/re-render
leaves that slide unchanged. Never on the fast default path."""

import asyncio
import hashlib
import logging
import os
import time
from collections import OrderedDict
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

from models.image_prompt import ImagePrompt
from utils.authored_dom_quality import (
    build_compact_dom_map,
    ensure_authored_qids,
    has_dom_quality_regression,
    inspect_authored_dom,
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
    _append_start_tag_attribute,
    _apply_source_replacements,
    _remove_start_tag_attribute,
    _source_start_tags,
    author_slide_html,
    authored_semantic_dual_output_mode,
    is_valid_slide_html,
    normalize_reference_mode,
    validate_authored_slide_html,
)
from utils.llm_calls.author_slide_patch import propose_authored_slide_patch
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
from utils.slide_capture import (
    ensure_browser_metrics,
    get_cached_browser_metrics,
    render_html_to_png,
    render_html_to_png_with_metrics,
)


LOGGER = logging.getLogger(__name__)
_ORIGINAL_RENDER_HTML_TO_PNG = render_html_to_png


class _SemanticAliasEndTagParser(HTMLParser):
    """Collect exact end-tag spans without matching strings in raw-text nodes."""

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self._source = source
        self._line_offsets = [0]
        self._line_offsets.extend(
            offset + 1 for offset, character in enumerate(source) if character == "\n"
        )
        self.tags: list[tuple[int, int, str]] = []

    def handle_endtag(self, tag: str) -> None:
        line, column = self.getpos()
        start = self._line_offsets[line - 1] + column
        end = self._source.find(">", start)
        if end >= 0:
            self.tags.append((start, end + 1, tag.lower()))


def _canonicalize_full_reauthor_semantic_aliases(source: str) -> str:
    """Map a safe semantic ``time`` alias onto the admitted phrasing surface.

    The shared author-stage source parser ensures replacements touch real tags,
    not quoted text. ``datetime`` is preserved as the span's inert ``title``
    value because the renderer does not admit a ``datetime`` attribute on span.
    Ambiguous attributes remain byte-for-byte unchanged so final renderer
    admission fails closed. Active/raw-content tags are never aliases here.
    """

    replacements: list[tuple[int, int, str]] = []
    time_tags = tuple(tag for tag in _source_start_tags(source) if tag.name == "time")
    for tag in time_tags:
        names = [name for name, _value in tag.attrs]
        datetime_values = [
            value for name, value in tag.attrs if name == "datetime"
        ]
        if len(names) != len(set(names)) or len(datetime_values) > 1:
            return source
        if datetime_values and (
            "title" in names
            or datetime_values[0] is None
            or not str(datetime_values[0]).strip()
        ):
            return source
        rewritten = "<span" + tag.raw[1 + len(tag.name) :]
        if datetime_values:
            rewritten = _remove_start_tag_attribute(rewritten, "datetime")
            rewritten = _append_start_tag_attribute(
                rewritten, "title", str(datetime_values[0])
            )
        replacements.append((tag.start, tag.end, rewritten))

    end_parser = _SemanticAliasEndTagParser(source)
    try:
        end_parser.feed(source)
        end_parser.close()
    except Exception:
        return source
    time_end_tags = tuple(
        (start, end) for start, end, name in end_parser.tags if name == "time"
    )
    non_void_time_tags = sum(
        not tag.raw.rstrip().endswith("/>") for tag in time_tags
    )
    if len(time_end_tags) != non_void_time_tags:
        return source
    replacements.extend(
        (start, end, "</span>")
        for start, end in time_end_tags
    )
    return _apply_source_replacements(source, replacements)


class _ImageServiceLike(Protocol):
    async def generate_image(self, prompt: ImagePrompt) -> object: ...


class AuthoredVqaResidualDomIssuesError(ValueError):
    """Fail-closed source-reflow result that still exposes durable repairs.

    Local patches are provider work that may have succeeded even when another
    slide still has a blocking DOM issue.  Carry the successful HTML/PNG pairs
    to the checkpoint owner so a retry resumes from those repairs instead of
    presenting the same original deck to VQA again.
    """

    def __init__(
        self,
        summary: str,
        *,
        htmls: Sequence[str],
        pngs: Sequence[bytes],
        fixed: Sequence[int],
        repair_details: Sequence[Mapping[str, Any]],
    ) -> None:
        super().__init__(f"authored_vqa_residual_dom_issues:{summary}")
        self.htmls = list(htmls)
        self.pngs = list(pngs)
        self.fixed = list(fixed)
        self.repair_details = [dict(item) for item in repair_details]


async def _render_correction_html(html: str) -> bytes:
    """Render and retain browser metrics for the subsequent deterministic gate."""
    if render_html_to_png is not _ORIGINAL_RENDER_HTML_TO_PNG:
        return await render_html_to_png(html)
    png, _metrics = await render_html_to_png_with_metrics(html)
    return png


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
    image: bytes,
    context: Optional[str],
    *,
    source_image: Optional[bytes] = None,
    reference_mode: str = STYLE_REFERENCE,
) -> str:
    mode = normalize_reference_mode(reference_mode)
    digest = hashlib.sha256()
    digest.update(_VQA_PROMPT_VERSION.encode("utf-8"))
    digest.update(b"\0")
    # Provider configuration errors remain best-effort here; the actual critique call
    # below will report/fold the failure without letting one slide abort the deck.
    try:
        model = str(get_model() or "")
    except Exception:
        model = ""
    digest.update(model.encode("utf-8"))
    digest.update(b"\0")
    digest.update(mode.encode("utf-8"))
    digest.update(b"\0")
    digest.update((context or "").encode("utf-8"))
    digest.update(b"\0")
    digest.update(hashlib.sha256(source_image or b"").digest())
    digest.update(hashlib.sha256(image).digest())
    return digest.hexdigest()


async def _critique_cached(
    image: bytes,
    context: Optional[str],
    *,
    source_image: Optional[bytes] = None,
    reference_mode: str = STYLE_REFERENCE,
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
                )
            # Keep the style-reference/default call byte-for-byte compatible with the
            # established render-only reviewer contract.
            return await critique_slide_image(image, slide_context=context)

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


def _style_comparison_cache_key(
    baseline: bytes,
    candidate: bytes,
    context: Optional[str],
    *,
    reference_mode: str = STYLE_REFERENCE,
) -> str:
    """Fingerprint a style comparison independently from all existing QA caches."""
    mode = normalize_reference_mode(reference_mode)
    if mode != STYLE_REFERENCE:
        raise ValueError("Authored style comparison only supports style_reference mode")

    digest = hashlib.sha256()
    digest.update(STYLE_COMPARISON_VQA_PROMPT_VERSION.encode("utf-8"))
    digest.update(b"\0")
    try:
        model = str(get_model() or "")
    except Exception:
        model = ""
    digest.update(model.encode("utf-8"))
    digest.update(b"\0")
    digest.update(mode.encode("utf-8"))
    digest.update(b"\0")
    digest.update((context or "").encode("utf-8"))
    digest.update(b"\0")
    digest.update(hashlib.sha256(baseline).digest())
    digest.update(hashlib.sha256(candidate).digest())
    return digest.hexdigest()


async def _style_comparison_cached(
    baseline: bytes,
    candidate: bytes,
    context: Optional[str],
    *,
    reference_mode: str = STYLE_REFERENCE,
) -> Optional[SlideCritique]:
    """Return one best-effort baseline-vs-candidate style critique."""
    mode = normalize_reference_mode(reference_mode)
    if mode != STYLE_REFERENCE:
        raise ValueError("Authored style comparison only supports style_reference mode")

    key = _style_comparison_cache_key(
        baseline,
        candidate,
        context,
        reference_mode=mode,
    )

    def record_failure(error: Exception) -> None:
        _style_comparison_vqa_failures[key] = {
            "error_type": type(error).__name__,
            "reason_code": str(getattr(error, "code", "vqa.provider_failed")),
            "status_code": getattr(error, "status_code", None),
        }
        _style_comparison_vqa_failures.move_to_end(key)
        limit = max(1, VQA_CACHE_SIZE)
        while len(_style_comparison_vqa_failures) > limit:
            _style_comparison_vqa_failures.popitem(last=False)

    async def invoke() -> SlideCritique:
        async with _get_vqa_sem():
            return await critique_slide_style_comparison(
                baseline,
                candidate,
                slide_context=context,
            )

    if VQA_CACHE_SIZE <= 0:
        try:
            critique = await invoke()
            _style_comparison_vqa_failures.pop(key, None)
            return critique
        except Exception as error:
            record_failure(error)
            return None
    cached = _style_comparison_vqa_cache.get(key)
    if cached is not None:
        _style_comparison_vqa_cache.move_to_end(key)
        return cached

    task = _style_comparison_vqa_inflight.get(key)
    if task is None:
        task = asyncio.create_task(invoke())
        _style_comparison_vqa_inflight[key] = task

    try:
        critique = await asyncio.shield(task)
    except Exception as error:
        record_failure(error)
        return None
    finally:
        if _style_comparison_vqa_inflight.get(key) is task and task.done():
            _style_comparison_vqa_inflight.pop(key, None)

    _style_comparison_vqa_cache[key] = critique
    _style_comparison_vqa_failures.pop(key, None)
    _style_comparison_vqa_cache.move_to_end(key)
    while len(_style_comparison_vqa_cache) > VQA_CACHE_SIZE:
        _style_comparison_vqa_cache.popitem(last=False)
    return critique


def style_comparison_failure_diagnostic(
    baseline: bytes,
    candidate: bytes,
    context: Optional[str],
    *,
    reference_mode: str = STYLE_REFERENCE,
) -> dict[str, object] | None:
    """Return bounded provider failure evidence for one exact paired request."""

    key = _style_comparison_cache_key(
        baseline,
        candidate,
        context,
        reference_mode=normalize_reference_mode(reference_mode),
    )
    value = _style_comparison_vqa_failures.get(key)
    return dict(value) if value is not None else None


async def critique_authored_style_comparison(
    baseline_pngs: List[bytes],
    candidate_pngs: List[bytes],
    contexts: Optional[List[Optional[str]]] = None,
    *,
    reference_mode: str = STYLE_REFERENCE,
) -> List[Optional[StyleComparisonCritique]]:
    """Compare post-fix authored renders with non-authoritative pre-fix baselines.

    Returns ``None`` for a candidate whose single model call failed. Empty batches are
    valid and return immediately without invoking the model.
    """
    mode = normalize_reference_mode(reference_mode)
    if mode != STYLE_REFERENCE:
        raise ValueError("Authored style comparison only supports style_reference mode")
    if len(baseline_pngs) != len(candidate_pngs):
        raise ValueError("Style comparison baseline and candidate counts must match")
    if any(not isinstance(image, bytes) or not image for image in baseline_pngs):
        raise ValueError("Style comparison baselines must be non-empty bytes")
    if any(not isinstance(image, bytes) or not image for image in candidate_pngs):
        raise ValueError("Style comparison candidates must be non-empty bytes")

    ctx = contexts or []

    async def review(i: int) -> Optional[StyleComparisonCritique]:
        context = ctx[i] if i < len(ctx) else None
        return await _style_comparison_cached(
            baseline_pngs[i],
            candidate_pngs[i],
            context,
            reference_mode=mode,
        )

    return list(await asyncio.gather(*[review(i) for i in range(len(candidate_pngs))]))


def _feedback(critique: SlideCritique, *, exact_reconstruction: bool = False) -> str:
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
        "1280x720 frame (no overflow or clipping), remove any element overlap, ensure "
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
) -> List[Optional[SlideCritique]]:
    """Critique each rendered authored slide. None where a review failed (so one bad
    call does not sink the pass)."""
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
        if mode == STYLE_REFERENCE:
            return await _critique_cached(img, context)
        return await _critique_cached(
            img,
            context,
            source_image=exact_sources[i] if exact_sources is not None else None,
            reference_mode=mode,
        )

    return list(await asyncio.gather(*[review(i, p) for i, p in enumerate(pngs)]))


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
    reference_images_by_slide: Optional[List[Tuple[Path, ...]]] = None,
    return_details: bool = False,
    force_full_reauthor_indices: Optional[Iterable[int]] = None,
    chapter_prefixes: Optional[Sequence[Optional[str]]] = None,
    image_service: Optional[_ImageServiceLike] = None,
    illustration_slots: Optional[Sequence[bool]] = None,
    local_patch_only: bool = False,
    fail_on_residual_dom_issues: bool = False,
    capacity_briefs: Optional[Sequence[str]] = None,
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
    if not (len(pngs) == len(contents) == len(roles) == n):
        raise ValueError("Authored QA inputs must have matching lengths")
    absolute_indices = list(slide_indices) if slide_indices is not None else list(range(n))
    if len(absolute_indices) != n:
        raise ValueError("slide_indices must match the reviewed slide count")
    deck_size = total_slides if total_slides is not None else n
    if deck_size < n:
        raise ValueError("total_slides cannot be smaller than the reviewed slide count")
    if initial_critiques is not None and len(initial_critiques) != n:
        raise ValueError("initial_critiques must match the reviewed slide count")
    if chapter_prefixes is not None and len(chapter_prefixes) != n:
        raise ValueError("chapter_prefixes must match the reviewed slide count")
    if illustration_slots is not None and len(illustration_slots) != n:
        raise ValueError("illustration_slots must match the reviewed slide count")
    if capacity_briefs is not None and len(capacity_briefs) != n:
        raise ValueError("capacity_briefs must match the reviewed slide count")
    forced_full_reauthor = set(force_full_reauthor_indices or ())
    if any(type(index) is not int or index < 0 or index >= n for index in forced_full_reauthor):
        raise ValueError("force_full_reauthor_indices contains an invalid local index")

    reference_mode = normalize_reference_mode(
        getattr(style, "reference_mode", STYLE_REFERENCE)
    )
    visual_plan = (
        None
        if reference_mode == EXACT_RECONSTRUCTION
        else plan_authored_visual_story(
            contents,
            roles,
            illustration_slots=tuple(illustration_slots or ()),
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
                *(ensure_browser_metrics(html) for html in htmls)
            )
        )
        if fail_on_residual_dom_issues
        else [get_cached_browser_metrics(html) for html in htmls]
    )

    def local_report(html: str, metrics: Mapping[str, Any] | None):
        return inspect_authored_dom(
            html,
            metrics,
            reference_mode=reference_mode,
            strict_structural_off_canvas=fail_on_residual_dom_issues,
        )

    async def metrics_for(html: str) -> Mapping[str, Any] | None:
        if fail_on_residual_dom_issues:
            return await ensure_browser_metrics(html)
        return get_cached_browser_metrics(html)

    def deterministic_critique(
        html: str, metrics: Mapping[str, Any] | None
    ) -> SlideCritique | None:
        report = local_report(html, metrics)
        if report.telemetry_status != "available" or not report.issues:
            return None
        issue_type = {
            "overflow": "overflow",
            "clipping": "overflow",
            "off_canvas": "overflow",
            "overlap": "overlap",
            "contrast": "low-contrast",
            "font": "overflow",
            "image": "other",
            "content": "other",
        }
        ranked = sorted(
            report.issues,
            key=lambda item: {"high": 0, "medium": 1, "low": 2}[item.severity],
        )[:8]
        return SlideCritique(
            needs_fix=True,
            issues=[
                CritiqueIssue(
                    type=issue_type[item.category],
                    severity=item.severity,
                    detail=(
                        f"DOM {item.category}: {item.detail}; targets: "
                        f"{', '.join(item.qids) or 'slide'}"
                    )[:200],
                )
                for item in ranked
            ],
        )

    async def full_reauthor(
        i: int, current_html: str, critique: SlideCritique
    ) -> tuple[str, bytes]:
        content = (
            f"{contents[i]}\n\n"
            f"{_feedback(critique, exact_reconstruction=reference_mode == EXACT_RECONSTRUCTION)}"
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
        if capacity_briefs is not None:
            reference_kwargs["capacity_brief"] = capacity_briefs[i]
        new_html = await author_slide_html(
            content,
            design_system,
            brand,
            roles[i],
            absolute_indices[i],
            deck_size,
            reasoning_effort="high",
            **reference_kwargs,
        )
        new_html = _canonicalize_full_reauthor_semantic_aliases(new_html)
        if not is_valid_slide_html(new_html):
            raise ValueError("full re-author returned invalid slide HTML")
        lowered_html = new_html.lower()
        if "data-asset-id" in lowered_html:
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
            can_generate = (
                illustration_allowed
                and image_service is not None
                and bool(
                    (getattr(style, "illustration_prompt", None) or "").strip()
                )
            )
            if can_generate:
                new_html = await apply_authored_illustration(
                    new_html,
                    style,
                    image_service,
                    slide_index=absolute_indices[i],
                    slide_content=contents[i],
                    raise_on_failure=True,
                )
            else:
                # Finalization alone would turn an unresolved authored slot into
                # a src-less <img> box. Withdraw it through the same path used by
                # the first-pass illustration stage when the deck disallows it.
                new_html = strip_illustration_slots(new_html)
        finalized_html = validate_authored_slide_html(new_html, finalized=True)
        # Real author output is already canonicalized at the author-stage boundary.
        # Preserve the legacy lexical HTML for test doubles/older callers that return
        # an otherwise-valid document without author-only attributes, while still
        # running the final admission check. When finalization has work to do, its
        # returned copy is authoritative so no marker can reach the renderer.
        if had_authoring_only_attributes:
            new_html = finalized_html
        # Fallback may replace structure, but every resulting edit target still gets
        # a deterministic id. The normal local-patch path preserves every existing id.
        if return_details and authored_semantic_dual_output_mode() != "declared":
            new_html = ensure_authored_qids(new_html)
        elif return_details:
            validate_declared_authored_qids(new_html)
        return new_html, await _render_correction_html(new_html)

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
            critique = deterministic_critique(current_html, current_metrics)
            if critique is not None:
                pass
            elif cycle == 0 and initial_critiques is not None:
                critique = initial_critiques[i]
            else:
                review_started = time.monotonic()
                if reference_mode == EXACT_RECONSTRUCTION:
                    critiques = await critique_authored(
                        [current_png],
                        contexts=[contents[i][:240]],
                        source_images=[exact_source_images[i]],
                        reference_mode=reference_mode,
                    )
                else:
                    # Preserve the established STYLE_REFERENCE call shape so callers
                    # and test doubles that implement the original two-argument
                    # contract continue to work unchanged.
                    critiques = await critique_authored(
                        [current_png],
                        contexts=[contents[i][:240]],
                    )
                slide_critique_work += time.monotonic() - review_started
                critique = critiques[0] if critiques else None
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
            try:
                if return_details and i not in forced_full_reauthor:
                    patch_attempted = True
                    compact_dom = [
                        node.to_dict()
                        for node in build_compact_dom_map(
                            current_html,
                            (
                                current_metrics
                                if fail_on_residual_dom_issues
                                else get_cached_browser_metrics(current_html)
                            ),
                        )
                    ]
                    patch_started = time.monotonic()
                    try:
                        proposal = await propose_authored_slide_patch(
                            current_png,
                            compact_dom,
                            critique,
                            reference_mode=reference_mode,
                            retry_invalid_model_patch=fail_on_residual_dom_issues,
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
                                candidate_png = await _render_correction_html(
                                    applied.html
                                )
                            except Exception as error:
                                log_failure("patch_render", error)
                                patch_failure = failure_detail("patch_render", error)
                                fallback_reason = patch_failure["code"]
                            else:
                                if not has_dom_quality_regression(
                                    local_report(current_html, current_metrics),
                                    local_report(
                                        applied.html,
                                        await metrics_for(applied.html),
                                    ),
                                ):
                                    new_html, new_png = applied.html, candidate_png
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
                if (new_html is None or new_png is None) and not local_patch_only:
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
                    current_metrics = await metrics_for(current_html)
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

        return (
            i,
            current_html,
            current_png,
            changed,
            slide_critique_work,
            slide_correction_work,
            repair_detail,
            local_report(current_html, current_metrics),
        )

    # gather starts every per-slide pipeline concurrently while preserving input order.
    results = await asyncio.gather(*(review_and_fix(i) for i in range(n)))
    final_reports = []
    for (
        i,
        new_html,
        new_png,
        changed,
        review_time,
        correction_time,
        detail,
        final_report,
    ) in results:
        critique_work += review_time
        correction_work += correction_time
        if changed:
            htmls[i] = new_html
            pngs[i] = new_png
            fixed.append(i)
        if detail is not None:
            repair_details.append(detail)
        final_reports.append(final_report)

    if fail_on_residual_dom_issues:
        blocking_categories = {
            "image",
            "off_canvas",
            "clipping",
            "overflow",
            "overlap",
        }
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

    LOGGER.info(
        "[authored-vqa] slides=%d fixed=%d total=%.1fs critique_work=%.1fs "
        "correction_work=%.1fs concurrency=%d cache_size=%d",
        n,
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
