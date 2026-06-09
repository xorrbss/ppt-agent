"""Vision-QA self-correction for AUTHORED slides (opt-in). Reuses the existing
multimodal critique (critique_slide.py) on the rendered authored PNGs, then
RE-AUTHORS each flagged slide with the critique as feedback and re-renders it,
bounded to a few cycles. Mirrors utils/llm_calls/vision_qa.py — which targets the
template/compose path and captures from /pdf-maker — but operates on the authored
mode's in-memory HTML + PNGs. Best-effort: a failed critique/re-author/re-render
leaves that slide unchanged. Never on the fast default path."""

import asyncio
from typing import List, Optional, Tuple

from utils.llm_calls.author_slide import Brand, author_slide_html, build_design_system
from utils.llm_calls.critique_slide import SlideCritique, critique_slide_image
from utils.slide_capture import render_html_to_png


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

    async def review(i: int, img: bytes) -> Optional[SlideCritique]:
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
    max_cycles: int = 1,
) -> Tuple[List[str], List[bytes], List[int]]:
    """Bounded critique -> re-author -> re-render loop over an authored deck. Returns
    the (possibly revised) htmls + pngs and the indices that changed."""
    design_system = build_design_system(brand)
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
                # the provider's full default reasoning effort ('off' = no 'low'
                # override) instead of the fast authoring effort used on the first pass.
                new_html = await author_slide_html(
                    content, design_system, brand, roles[i], i, n,
                    reasoning_effort="off",
                )
                new_png = await render_html_to_png(new_html)
                return i, new_html, new_png
            except Exception:
                return i, None, None

        for i, new_html, new_png in await asyncio.gather(
            *[fix(i) for i in flagged]
        ):
            if new_html and new_png:
                htmls[i] = new_html
                pngs[i] = new_png
                if i not in fixed:
                    fixed.append(i)

    return htmls, pngs, fixed
