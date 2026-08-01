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
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from utils.llm_calls.author_slide import (
    EXACT_RECONSTRUCTION,
    STYLE_REFERENCE,
    AUTHOR_CONCURRENCY,
    AuthoredStyleLike,
    Brand,
    author_slide_html,
    is_valid_slide_html,
    normalize_reference_mode,
)
from utils.authored_styles import reference_images_for_role
from utils.llm_calls.critique_slide import (
    CRITIQUE_PROMPT_VERSION,
    SlideCritique,
    critique_slide_image,
)
from utils.llm_provider import get_model
from utils.slide_capture import render_html_to_png


LOGGER = logging.getLogger(__name__)


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
_vqa_cache: "OrderedDict[str, SlideCritique]" = OrderedDict()
_vqa_inflight: Dict[str, "asyncio.Task[SlideCritique]"] = {}


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
) -> Optional[SlideCritique]:
    """Return one best-effort critique, caching only successful model responses."""
    mode = normalize_reference_mode(reference_mode)
    if mode == EXACT_RECONSTRUCTION and source_image is None:
        raise ValueError("Exact reconstruction critique requires a source image")

    async def invoke() -> SlideCritique:
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
) -> Tuple[List[str], List[bytes], List[int]]:
    """Bounded critique -> re-author -> re-render loop over an authored deck. Returns
    the (possibly revised) htmls + pngs and the local indices that changed.

    ``slide_indices`` and ``total_slides`` let callers review a subset while keeping
    the original deck position in the authoring prompt. This matters for cover/closing
    composition and slide numbering during post-generation quality review.
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

    reference_mode = normalize_reference_mode(
        getattr(style, "reference_mode", STYLE_REFERENCE)
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

    async def review_and_fix(
        i: int,
    ) -> Tuple[int, str, bytes, bool, float, float]:
        """Pipeline one slide through review -> correction -> render immediately.

        This intentionally avoids the former deck-wide review barrier: a fast review can
        begin its expensive correction while slower reviews are still in flight.
        """
        current_html = htmls[i]
        current_png = pngs[i]
        changed = False
        slide_critique_work = 0.0
        slide_correction_work = 0.0

        for cycle in range(max(1, max_cycles)):
            if cycle == 0 and initial_critiques is not None:
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
            try:
                content = (
                    f"{contents[i]}\n\n"
                    f"{_feedback(critique, exact_reconstruction=reference_mode == EXACT_RECONSTRUCTION)}"
                )
                # Correction-under-critique is the quality-critical step: re-author at
                # HIGH reasoning effort (the max) so the fix gets MORE reasoning than the
                # fast 'low' first pass, regardless of the provider's default effort.
                refs = (
                    reference_images_by_slide[i]
                    if reference_mode == EXACT_RECONSTRUCTION
                    and reference_images_by_slide is not None
                    else reference_images_for_role(style, roles[i])
                )
                if refs:
                    reference_kwargs = {"reference_images": refs}
                    if reference_mode == EXACT_RECONSTRUCTION:
                        reference_kwargs["reference_mode"] = reference_mode
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
                else:
                    reference_kwargs = {}
                    if reference_mode == EXACT_RECONSTRUCTION:
                        reference_kwargs["reference_mode"] = reference_mode
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

                # Reject truncated/garbage model output BEFORE launching Chrome. The old
                # order rendered it first and discarded it afterwards, wasting the most
                # expensive local stage while providing no quality benefit.
                if not is_valid_slide_html(new_html):
                    break
                new_png = await render_html_to_png(new_html)
                current_html = new_html
                current_png = new_png
                changed = True
            except Exception:
                break
            finally:
                slide_correction_work += time.monotonic() - correction_started

        return (
            i,
            current_html,
            current_png,
            changed,
            slide_critique_work,
            slide_correction_work,
        )

    # gather starts every per-slide pipeline concurrently while preserving input order.
    results = await asyncio.gather(*(review_and_fix(i) for i in range(n)))
    for i, new_html, new_png, changed, review_time, correction_time in results:
        critique_work += review_time
        correction_work += correction_time
        if changed:
            htmls[i] = new_html
            pngs[i] = new_png
            fixed.append(i)

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

    return htmls, pngs, fixed
