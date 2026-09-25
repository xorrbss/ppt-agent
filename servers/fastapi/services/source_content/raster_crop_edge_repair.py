"""Grow a crop over the lines its edges cut (see ``raster_crop_edges``).

Used when no shrink keeps the crop's own words and most of its pixels: the
crop takes the full box of every line it touches, and those lines' tokens move
into it, so the line is shown once, as pixels, and never cut.
"""

from __future__ import annotations

import math
from dataclasses import replace

from services.source_content.raster_boundary import _box_of
from services.source_content.raster_page_plan import PlannedRegion
from services.source_content.raster_structure import is_pixel_region


def is_pixels(region: PlannedRegion) -> bool:
    return is_pixel_region(region.role, region.table_cells, region.editable_fallback)


def _union(a, b):
    x0, y0 = min(a[0], b[0]), min(a[1], b[1])
    x1, y1 = max(a[0] + a[2], b[0] + b[2]), max(a[1] + a[3], b[1] + b[3])
    return (int(x0), int(y0), math.ceil(x1) - int(x0), math.ceil(y1) - int(y0))


def _take_line(region: PlannedRegion, word: int, words) -> tuple[tuple[int, ...], str, list[PlannedRegion]]:
    """Paragraph atomicity: a crop that needs one line of a text block takes the whole
    block (its tokens and text); nothing of it stays editable."""

    return tuple(region.word_indices), region.text or "", []


def _grown(crop: PlannedRegion, box, tokens: tuple[int, ...], texts: list[str], real: bool) -> PlannedRegion:
    suggestion = "\n".join(part for part in (crop.text, *texts) if part) or None
    return replace(
        crop,
        role="screenshot" if crop.role == "decoration" else crop.role,
        bbox=_union(crop.bbox, box),
        word_indices=crop.word_indices + tokens,
        text=None if real else suggestion,
        table_cells=None if crop.role == "table" else crop.table_cells,
        editable_fallback=not real,
        review_reason=crop.review_reason if real else crop.review_reason or "crop_edge_line",
        heading_line_count=0,
    )


def absorb_lines(regions, p: int, words, conflicts, grow_ok) -> tuple[list[PlannedRegion], int]:
    """Grow crop ``p`` over every word and line its edges cut (ink only locally);
    returns the regions and the crop's new index."""

    regions = list(regions)
    for _ in range(4 * len(regions) + 64):
        crop = regions[p]
        found = [
            c
            for c in conflicts(regions, only=p)
            if c.kind not in ("ink", "pixel_word") or grow_ok(regions, crop, c.box, p)
        ]
        if not found:
            break
        conflict = found[0]
        real = crop.role == "screenshot" and not crop.editable_fallback
        if conflict.kind in ("ink", "own_word", "pixel_word"):  # grow over it; ownership unchanged
            regions[p] = replace(crop, bbox=_union(crop.bbox, conflict.box))
            continue
        owner = next(n for n, r in enumerate(regions) if conflict.target in r.word_indices)
        tokens, text, pieces = _take_line(regions[owner], conflict.target, words)
        regions[p] = _grown(crop, _box_of(tokens, words, regions[owner].bbox), tokens, [text], real)
        regions = regions[:owner] + pieces + regions[owner + 1 :]
        if owner < p:
            p += len(pieces) - 1
    return regions, p
