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
