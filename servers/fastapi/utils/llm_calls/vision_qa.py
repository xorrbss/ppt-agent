"""Vision-QA detect pass: capture each rendered slide and critique it, returning the
per-slide structured critiques. This is the DETECT half of the optional high-quality
loop (capture + critique); the regenerate half consumes these critiques. Bounded and
opt-in — never on the fast deterministic default path."""

import asyncio
from typing import List, Optional, Tuple

from utils.llm_calls.critique_slide import SlideCritique, critique_slide_image
from utils.slide_capture import capture_slides


async def vision_qa_deck(
    presentation_id: str,
    n_slides: int,
    contexts: Optional[List[Optional[str]]] = None,
    base_url: str = "http://127.0.0.1:5000",
) -> List[Tuple[int, Optional[SlideCritique]]]:
    """Capture the deck and critique each slide. Returns (index, critique) per slide;
    the critique is None if that slide's review failed (so one bad call doesn't sink
    the pass)."""
    images = await capture_slides(presentation_id, n_slides, base_url=base_url)
    ctx = contexts or []

    async def review(i: int, img: bytes) -> Tuple[int, Optional[SlideCritique]]:
        try:
            return i, await critique_slide_image(
                img, slide_context=ctx[i] if i < len(ctx) else None
            )
        except Exception:
            return i, None

    return list(await asyncio.gather(*[review(i, img) for i, img in enumerate(images)]))


def slides_needing_fix(
    results: List[Tuple[int, Optional[SlideCritique]]],
) -> List[int]:
    """Indices of slides the review flagged as needing a fix."""
    return [i for i, c in results if c is not None and c.needs_fix]


def _critique_instructions(critique: SlideCritique) -> str:
    issues = "; ".join(f"{x.type} — {x.detail}" for x in critique.issues) or "visual problems"
    return (
        "The previously rendered version of THIS slide had these visual problems: "
        f"{issues}. Produce a cleaner slide that resolves them: reduce or tighten the "
        "content so it fits the slide without clipping or overflow, remove any "
        "placeholder or Lorem-ipsum text, and keep everything readable and balanced. "
        "You may choose a different archetype if it fits the content better."
    )


async def recompose_slide(
    outline_content: str,
    critique: SlideCritique,
    language: Optional[str] = None,
    tone: Optional[str] = None,
    verbosity: Optional[str] = None,
    instructions: Optional[str] = None,
):
    """Re-compose a single flagged slide from its outline, with the vision critique
    as feedback. Returns a fixed SlideSpec (the composer output for that one slide)."""
    from models.presentation_outline_model import (
        PresentationOutlineModel,
        SlideOutlineModel,
    )
    from utils.llm_calls.compose_slides import compose_slides

    fix = _critique_instructions(critique)
    merged = f"{instructions}\n\n{fix}" if instructions else fix
    outline = PresentationOutlineModel(slides=[SlideOutlineModel(content=outline_content)])
    composition = await compose_slides(
        outline, language=language, tone=tone, verbosity=verbosity, instructions=merged
    )
    return composition.slides[0]


async def run_vision_qa_pass(
    presentation_id: str,
    outline,
    composition,
    language: Optional[str] = None,
    tone: Optional[str] = None,
    verbosity: Optional[str] = None,
    base_url: str = "http://127.0.0.1",
    instructions: Optional[str] = None,
) -> Tuple[object, List[int]]:
    """One bounded vision-QA cycle over a composed+persisted deck: capture & critique
    every slide, then re-compose each flagged slide from its outline with the critique
    as feedback. Mutates and returns `composition` plus the list of fixed slide indices.
    Best-effort: capture/critique/recompose failures leave the original slide intact."""
    slides = getattr(composition, "slides", [])
    n = len(slides)
    if n == 0:
        return composition, []
    out_slides = getattr(outline, "slides", [])
    contexts = [
        (out_slides[i].content[:240] if i < len(out_slides) else None) for i in range(n)
    ]
    try:
        results = await vision_qa_deck(presentation_id, n, contexts=contexts, base_url=base_url)
    except Exception:
        return composition, []
    by_idx = dict(results)
    fixed: List[int] = []
    for i in slides_needing_fix(results):
        if i >= len(out_slides):
            continue
        try:
            slides[i] = await recompose_slide(
                out_slides[i].content, by_idx[i], language, tone, verbosity, instructions
            )
            fixed.append(i)
        except Exception:
            continue
    return composition, fixed
