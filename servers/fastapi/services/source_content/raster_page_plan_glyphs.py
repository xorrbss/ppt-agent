"""Classify leftover glyph clusters as icons/decorations or unreadable text
kept as pixel fallbacks, and merge touching pixel-fallback crops.
"""

from __future__ import annotations

from services.source_content.raster_captures import (
    PageContext,
    _clusters,
    _inside,
    _inter,
    _merge_overlapping,
    _pad,
    _union,
)
from services.source_content.raster_page_plan_regions import PlannedRegion
from services.source_content.raster_page_plan_text import _DIGIT, _HANGUL, _LETTER, _word_box
from services.source_content.raster_redesign_ocr import RedesignLine, line_text


def _merge_fallbacks(page: PageContext, regions: list[PlannedRegion]) -> list[PlannedRegion]:
    """Join touching pixel-fallback strips into one crop (fewer, larger images)."""

    fallback = [r for r in regions if r.editable_fallback and r.role == "screenshot"]
    others = [r for r in regions if not (r.editable_fallback and r.role == "screenshot")]
    groups = _clusters([r.bbox for r in fallback], 8)
    for group in groups:
        members = sorted((fallback[i] for i in group), key=lambda r: (r.bbox[1], r.bbox[0]))
        if len(members) == 1:
            others.append(members[0])
            continue
        others.append(
            PlannedRegion(
                "screenshot",
                _union([r.bbox for r in members]),
                tuple(i for r in members for i in r.word_indices),
                "\n".join(r.text or "" for r in members),
                confidence=min(r.confidence for r in members),
                review_reason=members[0].review_reason,
                reason="low_confidence_text_pixels",
                editable_fallback=True,
            )
        )
    return others


def _glyph_regions(
    page: PageContext,
    groups: list[list[int]],
    placed: list[PlannedRegion],
    unpromoted: set[int],
) -> list[PlannedRegion]:
    """Icons become token-free decorations; unreadable text stays as pixels.

    A group with at most two letters and no digit is an icon misread as
    glyphs: its tokens are noise (unpromoted) and its crop is kept as a
    ``decoration`` unless it would overlap placed content. Anything longer is
    content OCR could not read reliably, kept losslessly as a pixel fallback.
    """

    def is_noise(group: list[int]) -> bool:
        value = "".join(page.words[i].text for i in group)
        letters = len(_LETTER.findall(value))
        if _DIGIT.search(value):
            return False
        latin_junk = not _HANGUL.search(value) and letters <= 5 and page.conf(group) < 0.8
        return letters <= 2 or latin_junk

    noise = [g for g in groups if is_noise(g)]
    legible = [g for g in groups if g not in noise]
    result: list[PlannedRegion] = []
    boxes = [_pad(_word_box(page, g), 3, page.size) for g in legible]
    for box in _merge_overlapping(boxes):
        members = [g for g, b in zip(legible, boxes, strict=True) if _inside(b, box) >= 0.99]
        tokens = tuple(sorted(i for g in members for i in g))
        suggestion = "\n".join(line_text(RedesignLine(tuple(g), box, 1.0), page.words) for g in members)
        result.append(
            PlannedRegion(
                "screenshot",
                box,
                tokens,
                suggestion,
                confidence=0.4,
                review_reason="unreadable_text_pixels",
                reason="unreadable_glyphs",
                editable_fallback=True,
            )
        )
    occupied = [region.bbox for region in (*placed, *result)]
    for box in _merge_overlapping([_pad(_word_box(page, g), 3, page.size) for g in noise]):
        if not any(_inter(box, other) > 0.2 * box[2] * box[3] for other in occupied):
            result.append(PlannedRegion("decoration", box, (), reason="icon"))
    unpromoted.update(i for g in noise for i in g)
    return result
