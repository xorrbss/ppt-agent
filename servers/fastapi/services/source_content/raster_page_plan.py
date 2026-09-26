"""Classify one image-only slide into editable text, tables and preserved crops.

Only pixels that cannot become editable content stay images: application
screen captures (recognised by dense micro text inside a frame) and icons.
Everything else is reconstructed as title/body/table text. Confidence is
measured, never assumed, and only low-confidence regions are flagged.

The supporting logic lives in sibling modules and is re-exported here so
existing imports of ``services.source_content.raster_page_plan`` keep working:
``raster_page_plan_regions`` (the ``PlannedRegion``/``PagePlan`` data model),
``raster_page_plan_text`` (OCR line grouping and confidence), ``raster_page_plan_glyphs``
(icon/pixel-fallback classification) and ``raster_page_plan_tables`` (ruled-table
detection).
"""

from __future__ import annotations

from PIL import Image

from services.source_content.raster_captures import PageContext, _center_in, _pad, boundary_confidence, detect_captures
from services.source_content.raster_page_plan_glyphs import _glyph_regions, _merge_fallbacks
from services.source_content.raster_page_plan_regions import PagePlan, PlannedRegion
from services.source_content.raster_page_plan_tables import _tables
from services.source_content.raster_page_plan_text import (
    _strip_edges,
    _text_blocks,
    _unpromoted,
    _unreadable_fragment,
    _word_box,
    line_needs_help,
)
from services.source_content.raster_redesign_ocr import RedesignLine, RedesignWord
from services.source_content.raster_structure import xy_cut_order

__all__ = ["PagePlan", "PlannedRegion", "line_needs_help", "plan_page"]


def plan_page(image: Image.Image, words: tuple[RedesignWord, ...], lines: tuple[RedesignLine, ...]) -> PagePlan:
    page = PageContext(image, words, lines)
    unpromoted = _unpromoted(page)
    screenshots = detect_captures(page)
    regions: list[PlannedRegion] = []
    claimed: set[int] = set(unpromoted)
    for box in (_pad(capture, 2, page.size) for capture in screenshots):  # never clip the frame
        carried = tuple(i for i, w in enumerate(words) if i not in claimed and _center_in(w.bbox, box))
        claimed.update(carried)
        confidence = round(boundary_confidence(page, box), 4)
        regions.append(
            PlannedRegion(
                "screenshot",
                box,
                carried,
                confidence=confidence,
                review_reason="screenshot_boundary_uncertain" if confidence < 0.5 else None,
                reason="micro_text_capture",
            )
        )
    regions.extend(_tables(page, screenshots, claimed))
    remaining: list[tuple[list[int], RedesignLine]] = []
    icons: list[list[int]] = []
    tallest = max((line.height for line in page.lines if not page.is_garbage(line)), default=0.0)
    for line in page.lines:
        free = [i for i in line.word_indices if i not in claimed]
        if not free:
            continue
        sub = RedesignLine(
            tuple(free), _word_box(page, free), line.height, line.agreement, line.numbers_disagree, line.alternates
        )
        headline = sub.height >= 0.8 * tallest  # a title stays text (reviewed if doubtful)
        if page.is_garbage(sub) or (not headline and _unreadable_fragment(page, sub)):
            icons.append(free)
            continue
        kept, junk = _strip_edges(page, sub)
        if junk:
            icons.append(junk)
        remaining.append((kept, sub))
    texts = _text_blocks(page, remaining)
    regions.extend(texts)
    regions.extend(_glyph_regions(page, icons, regions, unpromoted))
    regions = _merge_fallbacks(page, regions)
    order = xy_cut_order([region.bbox for region in regions])
    return PagePlan(tuple(regions[i] for i in order), tuple(sorted(unpromoted)))
