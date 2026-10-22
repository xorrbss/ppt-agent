"""Per-page robustness for the redesign analysis.

An exception while structuring one page must never fail the whole deck. The
page falls back to the heuristic plan (no vision structurer) and, if that also
fails, to a lossless page-level pixel plan: the title band as one pixel crop in
the title group and the rest of the page as one content crop. Every fallback
leaves an advisory ``page_structuring_failed:<error>``; nothing is silent. A page
the structurer could not structure (none configured, provider error, rejected
answer) carries the plan's own ``heuristic_plan:<why>`` advisory; a deck analysed
without any structurer is reported once for the deck, not per page.

Tests must still see such bugs: under pytest (``PYTEST_CURRENT_TEST``), or when
``RASTER_STRICT_PAGE_ERRORS=1``, the exception is re-raised instead.
``RASTER_STRICT_PAGE_ERRORS=0`` forces the fallback (to test it).
"""

from __future__ import annotations

import logging
import os

from services.source_content.raster_page_plan import PagePlan, PlannedRegion
from utils.env_settings.authored_runtime import get_raster_strict_page_errors

logger = logging.getLogger(__name__)
TITLE_BAND = 0.25


def strict_page_errors() -> bool:
    declared = get_raster_strict_page_errors()
    if declared is not None:
        return declared
    return "PYTEST_CURRENT_TEST" in os.environ


def robust_plan(image, words, lines, structurer, executable, tessdata) -> tuple[PagePlan, list[str]]:
    """The structured plan, else the heuristic plan, else the pixel page plan."""

    from services.source_content.raster_page_structuring import structured_plan

    advisories: list[str] = []
    for attempt in (structurer, None):
        if attempt is None and structurer is None and advisories:
            break  # the heuristic already failed
        try:
            plan = structured_plan(image, words, lines, attempt, executable, tessdata)
            # The page's own degraded-mode notes; after a failure, the failure advisory says it.
            return plan, advisories or list(plan.advisories)
        except Exception as error:
            if strict_page_errors():
                raise
            logger.warning("raster page structuring failed: %s", type(error).__name__)
            advisories.append(f"page_structuring_failed:{type(error).__name__}")
    return pixel_page_plan(image.size, words, lines), advisories


def pixel_page_plan(size: tuple[int, int], words, lines) -> PagePlan:
    """Title band + one page-content crop; every OCR token is carried by one of them."""

    width, height = size
    band = [line for line in lines if line.bbox[1] + line.bbox[3] / 2 <= TITLE_BAND * height]
    tallest = max((line.height for line in band), default=0)
    title_lines = [line for line in band if line.height >= 0.75 * tallest] if tallest else []
    cut = max((line.bbox[1] + line.bbox[3] for line in title_lines), default=0)
    # Tokens go by centre; the cut moves below any token it would split.
    for word in words:
        x, y, w, h = word.bbox
        if y < cut < y + h and y + h / 2 <= cut:
            cut = y + h
    cut = min(height, int(cut) + 2) if cut else 0
    title_tokens = tuple(n for n, word in enumerate(words) if word.bbox[1] + word.bbox[3] / 2 < cut)
    rest = tuple(n for n in range(len(words)) if n not in title_tokens)
    regions = []
    if cut:
        regions.append(
            PlannedRegion(
                "screenshot",
                (0, 0, width, cut),
                title_tokens,
                " ".join(words[n].text for n in title_tokens) or None,
                confidence=0.3,
                review_reason="page_structuring_failed",
                reason="page_pixels",
                editable_fallback=bool(title_tokens),
                group_id="title",
            )
        )
    regions.append(
        PlannedRegion(
            "screenshot",
            (0, cut, width, height - cut),
            rest,
            None,
            confidence=0.3,
            review_reason="page_structuring_failed",
            reason="page_pixels",
        )
    )
    return PagePlan(tuple(regions), ())
