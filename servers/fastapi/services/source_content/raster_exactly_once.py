"""Exactly-once invariant in source space for redesigned pages.

The renderer places every region at its own position, so any source pixel that
two regions show appears twice on the slide. After all other repairs:

* no pixel region (screenshot, pixel line, image table, ink crop, decoration)
  intersects a word box of a token another region carries;
* no two pixel regions share ink (``ink`` mask, see ``ink_mask``).

Resolution, one per conflict and never trim-and-merge for the same pair:

1. a *word crop* (a pixel region whose box is just its words' box, e.g. a
   pixel-fallback OCR line) gives its words that lie inside another pixel
   region to that region, and is re-boxed around its remaining words, split
   where they are far apart (a line OCR joined across three tables becomes
   the table rows it belongs to);
2. a decoration or token-free ink crop that overlaps is dropped when the
   other region covers most of it, otherwise trimmed;
3. otherwise the smaller crop is trimmed off the larger (keeping its own
   words), else the larger off the smaller, else both merge into one crop.

``exactly_once_conflicts`` is pure and shared with the ledger.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

import numpy as np
from PIL import Image, ImageFilter

Box = tuple[float, float, float, float]
PAD = 3
_LEVEL = 48
_MIN_INK = 24


def _cut(a: Box, b: Box) -> Box | None:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    return (x0, y0, x1 - x0, y1 - y0) if x1 > x0 and y1 > y0 else None


def _area(box: Box | None) -> float:
    return 0.0 if box is None else max(0.0, box[2]) * max(0.0, box[3])


def _union(a: Box, b: Box) -> tuple[int, int, int, int]:
    x0, y0 = int(min(a[0], b[0])), int(min(a[1], b[1]))
    x1 = int(np.ceil(max(a[0] + a[2], b[0] + b[2])))
    y1 = int(np.ceil(max(a[1] + a[3], b[1] + b[3])))
    return (x0, y0, x1 - x0, y1 - y0)


def _hits(a: Box, b: Box, tolerance: float) -> bool:
    cut = _cut(a, b)
    return cut is not None and cut[2] >= tolerance and cut[3] >= tolerance


def ink_mask(gray: np.ndarray | None) -> np.ndarray | None:
    """Full-resolution boolean ink mask (local-background difference, as coverage)."""

    if gray is None:
        return None
    image = Image.fromarray(np.clip(gray, 0, 255).astype(np.uint8))
    small = image.resize((max(1, image.width // 2), max(1, image.height // 2)), Image.Resampling.BILINEAR)
    background = np.asarray(small.filter(ImageFilter.MedianFilter(15)), dtype=np.int16)
    background = np.asarray(
        Image.fromarray(background.astype(np.uint8)).resize(image.size, Image.Resampling.NEAREST), dtype=np.int16
    )
    return np.abs(gray - background) > _LEVEL


def _ink_in(mask: np.ndarray | None, box: Box | None) -> int:
    if box is None:
        return 0
    if mask is None:
        return _MIN_INK  # without pixels every overlap counts
    x0, y0 = max(0, int(box[0])), max(0, int(box[1]))
    x1, y1 = int(np.ceil(box[0] + box[2])), int(np.ceil(box[1] + box[3]))
    return int(mask[y0:y1, x0:x1].sum())


def exactly_once_conflicts(
    items: Sequence[tuple[bool, Box, tuple[int, ...]]],
    word_boxes: Sequence[Box],
    mask: np.ndarray | None = None,
    tolerance: float = 3.0,
) -> list[tuple[int, int, str]]:
    """``items`` are ``(is_pixels, box, tokens)``; returns ``(pixel, other, kind)``.

    kind ``word``: the pixel region shows a word the other region carries;
    kind ``ink``: two pixel regions share ink (only checked with ``mask``).
    """

    result = []
    for p, (pixels, box, _) in enumerate(items):
        if not pixels:
            continue
        for o, (other_pixels, other_box, tokens) in enumerate(items):
            if o == p:
                continue
            if any(k < len(word_boxes) and _hits(box, word_boxes[k], tolerance) for k in tokens):
                result.append((p, o, "word"))
            elif mask is not None and other_pixels and o > p and _ink_in(mask, _cut(box, other_box)) >= _MIN_INK:
                result.append((p, o, "ink"))
    return result


LINE_REASONS = frozenset({"line_pixels", "low_confidence_text_pixels", "unreadable_glyphs"})


def _word_crop(region, boxes) -> bool:
    """A pixel-fallback text line: a crop whose pixels are just its words."""

    return (
        bool(region.word_indices)
        and region.role == "screenshot"
        and region.editable_fallback
        and region.reason in LINE_REASONS
    )


def _clusters(tokens: list[int], boxes) -> list[list[int]]:
    """Tokens split where a horizontal gap exceeds twice the line height, or rows differ."""

    ordered = sorted(tokens, key=lambda k: (boxes[k][1] + boxes[k][3] / 2, boxes[k][0]))
    groups: list[list[int]] = []
    for k in ordered:
        placed = False
        for group in groups:
            box = boxes[group[0]]
            for j in group[1:]:
                box = _union(box, boxes[j])
            b = boxes[k]
            height = max(1.0, min(box[3], b[3]))
            gap_x = max(b[0] - (box[0] + box[2]), box[0] - (b[0] + b[2]))
            gap_y = max(b[1] - (box[1] + box[3]), box[1] - (b[1] + b[3]))
            if gap_x <= 2 * height and gap_y <= 0.6 * height:
                group.append(k)
                placed = True
                break
        if not placed:
            groups.append([k])
    return groups


def _box_of(tokens, boxes, size, fallback=None) -> tuple[int, int, int, int]:
    if not tokens:
        if fallback is None:
            raise ValueError("box of an empty token set")
        return tuple(int(v) for v in fallback)
    box = boxes[tokens[0]]
    for k in tokens[1:]:
        box = _union(box, boxes[k])
    x0, y0 = max(0, int(box[0]) - PAD), max(0, int(box[1]) - PAD)
    x1 = min(size[0], int(np.ceil(box[0] + box[2])) + PAD)
    y1 = min(size[1], int(np.ceil(box[1] + box[3])) + PAD)
    return (x0, y0, x1 - x0, y1 - y0)


def enforce_exactly_once(plan, words, gray=None):
    """Repair until no source pixel is shown by two regions (bounded)."""

    from services.source_content.raster_exactly_once_repair import dissolve_composites, resolve

    boxes = [word.bbox for word in words]
    mask = ink_mask(gray)
    size = (gray.shape[1], gray.shape[0]) if gray is not None else (10**6, 10**6)
    regions = list(plan.regions)
    unpromoted = set(plan.unpromoted)
    regions = dissolve_composites(regions, boxes, size)
    skipped: set[tuple] = set()
    for _ in range(6 * len(regions) + 16):
        items = [
            (_is_pixels(r), r.bbox, tuple(k for k in r.word_indices if k not in unpromoted)) for r in regions
        ]
        found = [
            c
            for c in exactly_once_conflicts(items, boxes, mask)
            if (regions[c[0]].bbox, regions[c[1]].bbox) not in skipped
        ]
        if not found:
            break
        changed = resolve(regions, found[0], words, mask, size, unpromoted)
        if changed is None:  # no exact repair: the ledger reports this pair
            skipped.add((regions[found[0][0]].bbox, regions[found[0][1]].bbox))
            continue
        regions = changed
    regions = finish(regions, words, boxes, mask, unpromoted)
    return replace(plan, regions=tuple(regions), unpromoted=tuple(sorted(unpromoted)))


def finish(regions, words, boxes, mask, unpromoted) -> list:
    """Hard guarantee: what the exact repairs left gives way (picture crops split
    around text), and only after that budget is spent merges (``last_resort``)."""

    from services.source_content.raster_exactly_once_repair import _trim, give_way, last_resort, word_hole

    budget = 3 * len(regions) + 8
    for attempt in range(2 * budget):
        # A crop holds its own words whole (a crop edge never cuts a word it shows).
        regions = [_holding_words(r, boxes, unpromoted) if _is_pixels(r) else r for r in regions]
        items = [
            (_is_pixels(r), r.bbox, tuple(k for k in r.word_indices if k not in unpromoted)) for r in regions
        ]
        found = exactly_once_conflicts(items, boxes, mask) or _box_conflicts(regions, boxes)
        if not found:
            break
        if found[0][2] == "box":
            trimmed = _trim(regions[found[0][0]], regions[found[0][1]].bbox, boxes, 0.3)
            if trimmed is not None:
                regions = [replace(r, bbox=trimmed) if n == found[0][0] else r for n, r in enumerate(regions)]
                continue
        exact = None
        if attempt < budget:
            exact = word_hole(regions, found[0], words, mask, unpromoted)
            exact = exact if exact is not None else give_way(regions, found[0], words, mask, unpromoted)
        if exact is None:
            a, b = regions[found[0][0]].bbox, regions[found[0][1]].bbox
            union = _union(a, b)
            if _area(union) > 2 * max(_area(a), _area(b)):
                break  # a merge would sweep the page: the ledger reports the pair instead
            exact = last_resort(regions, found[0], words)
        regions = exact
    return regions


def _box_conflicts(regions, boxes) -> list[tuple[int, int, str]]:
    """The ledger's region-level boundary rule (a crop box overlapping a text box)."""

    from services.source_content.raster_structure import boundary_conflicts

    items = [(_is_pixels(r), r.bbox, False) for r in regions]
    return [(p, t, "box") for p, t in boundary_conflicts(items)]


def _is_pixels(region) -> bool:
    from services.source_content.raster_structure import is_pixel_region

    return is_pixel_region(region.role, region.table_cells, region.editable_fallback)


def page_exactly_once(page, canvas_width_emu: float) -> list[tuple[int, int, str]]:
    """Word-level exactly-once conflicts of a sealed page (for the ledger)."""

    unpromoted = set(page.unpromoted_ocr_indices or ())
    items = [
        (
            _is_pixels(r),
            (r.bbox.x, r.bbox.y, r.bbox.width, r.bbox.height),
            tuple(k for k in r.ocr_token_indices or () if k not in unpromoted),
        )
        for r in page.regions
    ]
    words = [(t.bbox.x, t.bbox.y, t.bbox.width, t.bbox.height) for t in page.ocr_inventory]
    return exactly_once_conflicts(items, words, None, tolerance=canvas_width_emu / 600)


def _holding_words(region, boxes, unpromoted):
    box = region.bbox
    for k in region.word_indices:
        if k not in unpromoted:
            box = _union(box, boxes[k])
    return region if box == tuple(region.bbox) else replace(region, bbox=box)
