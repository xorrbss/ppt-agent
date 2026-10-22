"""Crop-edge invariant for redesigned pages.

A pixel crop shows every glyph inside its box, so its edges must fall between
text, never through it, and it must not show text that is also published as
editable text:

* every OCR word a crop carries lies wholly inside the crop;
* no editable word intersects the crop, not even by a sliver ("...함께 ㅅ"
  under a crop edge), and no word owned by another crop is cut by its edge;
* no text-scale ink cluster (connected ink of a glyph or word, see
  ``text_ink_clusters``) is cut by a crop edge.

Repair per crop, never both for one crop: shrink it so the line lies wholly
outside (keeping its own words and most of its pixels), or grow it to the full
box of every line it touches and move those lines into it (text leaves the
editable region, so nothing is shown twice). ``crop_edge_conflicts`` is pure
and shared by the planner and by verification of a sealed analysis.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np
from PIL import Image, ImageFilter

Box = tuple[float, float, float, float]
TOLERANCE = 3.0
_SCALE = 2
_LEVEL = 48
_MAX_TEXT_HEIGHT = 90
_MAX_TEXT_WIDTH = 600
GROWTH_MARGIN = 16
CAPTURE_GROWTH = 1.5


@dataclass(frozen=True)
class EdgeItem:
    pixels: bool
    box: Box
    words: tuple[int, ...]


@dataclass(frozen=True)
class EdgeConflict:
    crop: int
    kind: str  # own_word | text_word | pixel_word | ink
    target: int  # word index, or cluster index for ink
    box: Box


def _cut(a: Box, b: Box) -> tuple[float, float]:
    width = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    height = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return width, height


def _hits(a: Box, b: Box, tolerance: float) -> bool:
    width, height = _cut(a, b)
    return width >= tolerance and height >= tolerance


def _inside(outer: Box, inner: Box, tolerance: float) -> bool:
    return (
        inner[0] >= outer[0] - tolerance
        and inner[1] >= outer[1] - tolerance
        and inner[0] + inner[2] <= outer[0] + outer[2] + tolerance
        and inner[1] + inner[3] <= outer[1] + outer[3] + tolerance
    )


def _crosses(crop: Box, cluster: Box, tolerance: float) -> bool:
    return _hits(crop, cluster, tolerance + 1) and not _inside(crop, cluster, tolerance)


def crop_edge_conflicts(
    items: Sequence[EdgeItem],
    word_boxes: Sequence[Box],
    clusters: Sequence[Box] = (),
    tolerance: float = TOLERANCE,
    only: int | None = None,
) -> list[EdgeConflict]:
    owner = {k: n for n, item in enumerate(items) for k in item.words}
    result = []
    for p, item in enumerate(items):
        if not item.pixels or (only is not None and p != only):
            continue
        for k in item.words:
            if k < len(word_boxes) and not _inside(item.box, word_boxes[k], tolerance):
                result.append(EdgeConflict(p, "own_word", k, word_boxes[k]))
        for k, n in owner.items():
            if n == p or k >= len(word_boxes) or not _hits(item.box, word_boxes[k], tolerance):
                continue
            if not items[n].pixels:
                result.append(EdgeConflict(p, "text_word", k, word_boxes[k]))
            elif not _inside(item.box, word_boxes[k], tolerance):
                # Pixels shown twice are harmless; a word cut by an edge is not.
                result.append(EdgeConflict(p, "pixel_word", k, word_boxes[k]))
        for c, cluster in enumerate(clusters):
            if _crosses(item.box, cluster, tolerance):
                result.append(EdgeConflict(p, "ink", c, cluster))
    return result


def text_ink_clusters(gray: np.ndarray | None) -> list[Box]:
    """Boxes (source px) of text-scale connected ink: glyphs, words, small icons."""

    if gray is None:
        return []
    from services.source_content.raster_ink_coverage import _components

    image = Image.fromarray(np.clip(gray, 0, 255).astype(np.uint8))
    small = image.resize((image.width // _SCALE, image.height // _SCALE), Image.Resampling.BILINEAR)
    background = np.asarray(small.filter(ImageFilter.MedianFilter(15)), dtype=np.int16)
    ink = np.abs(np.asarray(small, dtype=np.int16) - background) > _LEVEL
    grown = np.asarray(Image.fromarray(ink.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(3))) > 0
    result = []
    for x0, y0, x1, y1, count in _components(grown):
        box = (x0 * _SCALE, y0 * _SCALE, (x1 - x0) * _SCALE, (y1 - y0) * _SCALE)
        if count < 12 or min(box[2], box[3]) <= 4 or box[3] > _MAX_TEXT_HEIGHT or box[2] > _MAX_TEXT_WIDTH:
            continue
        result.append(box)
    return result


def _union(a: Box, b: Box) -> Box:
    x0, y0 = min(a[0], b[0]), min(a[1], b[1])
    return (x0, y0, max(a[0] + a[2], b[0] + b[2]) - x0, max(a[1] + a[3], b[1] + b[3]) - y0)


def _area(box: Box) -> float:
    return max(0.0, box[2]) * max(0.0, box[3])


def _shrinks(box: Box, target: Box) -> list[Box]:
    x, y, w, h = box
    tx, ty, tw, th = target
    gap = TOLERANCE + 1
    options = [
        (x, ty + th + gap, w, y + h - (ty + th + gap)),
        (x, y, w, ty - gap - y),
        (tx + tw + gap, y, x + w - (tx + tw + gap), h),
        (x, y, tx - gap - x, h),
    ]
    return [option for option in options if option[2] > 0 and option[3] > 0]


def _grid_box(box: Box) -> Box:
    x0, y0 = int(box[0]), int(box[1])
    return (x0, y0, int(np.ceil(box[0] + box[2])) - x0, int(np.ceil(box[1] + box[3])) - y0)


def near(crop, target: Box, margin: float = GROWTH_MARGIN) -> bool:
    """Whether growing ``crop`` over the ink cluster ``target`` is safe: always for
    a text-line or ink crop (a cluster is at most one text line tall), only within
    ``margin`` px for a capture, whose growth could cascade across the page."""

    if not (crop.role == "screenshot" and not crop.editable_fallback):
        return True
    box = crop.bbox
    return _inside((box[0] - margin, box[1] - margin, box[2] + 2 * margin, box[3] + 2 * margin), target, 0)


def _grows_into_text(regions, p: int, box: Box, is_pixels) -> bool:
    """Growing crop ``p`` to ``box`` would overlap an editable text region (the
    boundary invariant): that growth is not taken, the ink is reported instead."""

    from services.source_content.raster_structure import boundary_conflicts

    items = [(True, box, False)] + [(is_pixels(r), r.bbox, False) for n, r in enumerate(regions) if n != p]
    return any(pixel == 0 for pixel, _ in boundary_conflicts(items))


def enforce_crop_edges(plan, words, gray=None, clusters: Sequence[Box] | None = None):
    """Repair every crop until no edge cuts a word or line; ink clusters are
    repaired by a shrink or a local growth (``GROWTH_MARGIN``), never by a
    growth that could cascade across the page."""

    from services.source_content.raster_crop_edge_repair import absorb_lines, is_pixels

    clusters = list(text_ink_clusters(gray) if clusters is None else clusters)
    boxes = [word.bbox for word in words]
    regions = list(plan.regions)
    unpromoted = set(plan.unpromoted)
    given_up: set[tuple] = set()
    stuck: set[tuple] = set()

    def key(region) -> tuple:
        return (region.role, tuple(region.word_indices), region.group_id, region.reason)

    def grow_ok(current, crop, target: Box, index: int) -> bool:
        grown = _union(crop.bbox, target)
        # Growth never shows a word another region carries (exactly-once).
        foreign = any(
            _hits(grown, boxes[k], TOLERANCE) and not _hits(crop.bbox, boxes[k], TOLERANCE)
            for n, region in enumerate(current)
            if n != index
            for k in region.word_indices
            if k not in unpromoted
        )
        return near(crop, target) and not foreign and not _grows_into_text(current, index, grown, is_pixels)

    def conflicts(current, only=None):
        items = [
            EdgeItem(is_pixels(r), r.bbox, tuple(k for k in r.word_indices if k not in unpromoted)) for r in current
        ]
        found = crop_edge_conflicts(items, boxes, clusters, only=only)
        return [
            c
            for c in found
            if (c.kind != "text_word" or key(current[c.crop]) not in stuck)
            and (key(current[c.crop]), c.kind, c.target) not in given_up
        ]

    for _ in range(8 * len(regions) + len(clusters) + 16):
        found = conflicts(regions)
        if not found:
            break
        p = found[0].crop
        mine = [c for c in found if c.crop == p]
        crop = regions[p]
        own = [boxes[k] for k in crop.word_indices if k not in unpromoted]
        grown = crop.bbox
        for conflict in mine:
            if conflict.kind == "own_word":
                grown = _union(grown, conflict.box)
            elif conflict.kind in ("ink", "pixel_word") and grow_ok(regions, crop, conflict.box, p):
                grown = _union(grown, conflict.box)
        if grown != crop.bbox:
            regions[p] = replace(crop, bbox=_grid_box(grown))
            continue
        # A capture keeps most of its picture; a line crop only needs its own words.
        keep = 0.4 if crop.role == "screenshot" and not crop.editable_fallback else 0.3 if own else 0.5
        targets = {(c.kind, c.target) for c in mine}
        best = None
        for conflict in mine:
            for option in _shrinks(crop.bbox, conflict.box):
                option = _grid_box(option)
                if _area(option) < keep * _area(crop.bbox) or not all(_inside(option, b, 0) for b in own):
                    continue
                trial = regions[:p] + [replace(crop, bbox=option)] + regions[p + 1 :]
                left = conflicts(trial, only=p)
                # Shrinking one side at a time: no new conflict, and strictly fewer.
                if not {(c.kind, c.target) for c in left} < targets:
                    continue
                if best is None or _area(option) > _area(best):
                    best = option
        if best is not None:
            regions[p] = replace(crop, bbox=best)
            continue
        if crop.role == "decoration" and not crop.word_indices:
            regions.pop(p)  # an icon crop carries no content: never let it cut text
            continue
        if all(c.kind in ("ink", "pixel_word") for c in mine):
            # Far-reaching ink or another crop's word: reported, not chased.
            given_up.update((key(crop), c.kind, c.target) for c in mine)
            continue
        absorbed, moved = absorb_lines(regions, p, words, conflicts, grow_ok)
        real = crop.role == "screenshot" and not crop.editable_fallback
        if real and _area(absorbed[moved].bbox) > CAPTURE_GROWTH * _area(crop.bbox):
            # A capture that would sweep across the page: its conflicts are reported, not chased.
            given_up.update((key(crop), c.kind, c.target) for c in mine if c.kind != "text_word")
            stuck.add(key(crop))
            continue
        regions = absorbed
    return replace(plan, regions=tuple(regions))


def page_edge_conflicts(page, canvas_width_emu: float, clusters: Sequence[Box] = (), scale: float = 1.0):
    """Word-level (and, given source-pixel ``clusters``, ink) conflicts of a sealed page."""

    from services.source_content.raster_structure import is_pixel_region

    unpromoted = set(page.unpromoted_ocr_indices or ())
    items = [
        EdgeItem(
            is_pixel_region(r.role, r.table_cells, r.editable_fallback),
            (r.bbox.x * scale, r.bbox.y * scale, r.bbox.width * scale, r.bbox.height * scale),
            tuple(k for k in r.ocr_token_indices or () if k not in unpromoted),
        )
        for r in page.regions
    ]
    words = [
        (t.bbox.x * scale, t.bbox.y * scale, t.bbox.width * scale, t.bbox.height * scale) for t in page.ocr_inventory
    ]
    # 1/600 of the canvas width is about 3 source pixels on a 1600-2000 px slide.
    return crop_edge_conflicts(items, words, clusters, tolerance=canvas_width_emu * scale / 600)
