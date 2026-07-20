"""Vision-QA self-correction for AUTHORED slides (opt-in). Reuses the existing
multimodal critique (critique_slide.py) on the rendered authored PNGs, then
RE-AUTHORS each flagged slide with the critique as feedback and re-renders it,
bounded to a few cycles. Mirrors utils/llm_calls/vision_qa.py — which targets the
template/compose path and captures from /pdf-maker — but operates on the authored
mode's in-memory HTML + PNGs. Best-effort: a failed critique/re-author/re-render
leaves that slide unchanged. Never on the fast default path."""

import asyncio
from typing import List, Optional, Tuple

from utils.llm_calls.author_slide import (
    AUTHOR_CONCURRENCY,
    AuthoredStyleLike,
    Brand,
    author_slide_html,
    is_valid_slide_html,
)
from utils.authored_styles import reference_images_for_role
from utils.llm_calls.critique_slide import SlideCritique, critique_slide_image
from utils.slide_capture import render_html_to_png


# Bound the vision-QA model-call fan-out PROCESS-WIDE to the same cap as first-pass
# authoring, so opting into vision-QA on a large deck can't burst one critique call per
# slide all at once (the re-author of each flagged slide is bounded separately, inside
# author_slide_html / render_html_to_png). Lazily created inside the running loop.
_vqa_sem: Optional[asyncio.Semaphore] = None


def _get_vqa_sem() -> asyncio.Semaphore:
    global _vqa_sem
    if _vqa_sem is None:
        _vqa_sem = asyncio.Semaphore(AUTHOR_CONCURRENCY)
    return _vqa_sem


def _feedback(critique: SlideCritique) -> str:
    issues = (
        "; ".join(f"{x.type} — {x.detail}" for x in critique.issues) or "visual problems"
    )
    return (
        "A rendered preview of THIS slide had these visual problems: "
        f"{issues}. Re-author the slide to fix them: keep ALL content within the "
        "1280x720 frame (no overflow or clipping), remove any element overlap, ensure "
        "strong contrast, delete any placeholder text, and rebalance the layout — while "
        "keeping the same role, language and shared design system."
    )


async def critique_authored(
    pngs: List[bytes], contexts: Optional[List[Optional[str]]] = None
) -> List[Optional[SlideCritique]]:
    """Critique each rendered authored slide. None where a review failed (so one bad
    call does not sink the pass)."""
    ctx = contexts or []
    sem = _get_vqa_sem()

    async def review(i: int, img: bytes) -> Optional[SlideCritique]:
        async with sem:
            try:
                return await critique_slide_image(
                    img, slide_context=ctx[i] if i < len(ctx) else None
                )
            except Exception:
                return None

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
) -> Tuple[List[str], List[bytes], List[int]]:
    """Bounded critique -> re-author -> re-render loop over an authored deck. Returns
    the (possibly revised) htmls + pngs and the indices that changed."""
    n = len(htmls)
    htmls = list(htmls)
    pngs = list(pngs)
    fixed: List[int] = []

    for _ in range(max(1, max_cycles)):
        critiques = await critique_authored(
            pngs, contexts=[c[:240] for c in contents]
        )
        flagged = [
            i for i, c in enumerate(critiques) if c is not None and c.needs_fix
        ]
        if not flagged:
            break

        async def fix(i: int) -> Tuple[int, Optional[str], Optional[bytes]]:
            try:
                content = f"{contents[i]}\n\n{_feedback(critiques[i])}"
                # Correction-under-critique is the quality-critical step: re-author at
                # HIGH reasoning effort (the max) so the fix gets MORE reasoning than the
                # fast 'low' first pass, regardless of the provider's default effort.
                refs = reference_images_for_role(style, roles[i])
                if refs:
                    new_html = await author_slide_html(
                        content,
                        design_system,
                        brand,
                        roles[i],
                        i,
                        n,
                        reasoning_effort="high",
                        reference_images=refs,
                    )
                else:
                    new_html = await author_slide_html(
                        content, design_system, brand, roles[i], i, n,
                        reasoning_effort="high",
                    )
                new_png = await render_html_to_png(new_html)
                return i, new_html, new_png
            except Exception:
                return i, None, None

        for i, new_html, new_png in await asyncio.gather(
            *[fix(i) for i in flagged]
        ):
            # Only replace the slide if the re-authored HTML is itself valid: a truncated
            # or garbage re-author must NOT overwrite a slide that already rendered fine
            # (the first pass gates on is_valid_slide_html; this pass must too).
            if new_html and is_valid_slide_html(new_html) and new_png:
                htmls[i] = new_html
                pngs[i] = new_png
                if i not in fixed:
                    fixed.append(i)

    return htmls, pngs, fixed
