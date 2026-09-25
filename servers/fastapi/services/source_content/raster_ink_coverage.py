"""Ink-coverage invariant: nothing visible in the source may be silently dropped.

Definition (on a 1/2-scale grayscale page):
* local background = median over a 31x31 window (large flat fills and soft
  gradients are background, so card fills and swooshes are not ink);
* ink = pixels differing from their local background by more than 48 levels;
* a cluster = connected ink after a 5x5 dilation (glyphs of a word join),
  kept when its box is at least 12x10 source px and holds at least 40 ink
  pixels; thin rules (box height or width <= 3 px) are ignored;
* a cluster is covered when at least half of its ink lies inside regions
  that always render: text/table boxes and screenshot/pixel crops.
  Decoration crops count only for icon-like clusters (fewer than three glyph
  blobs, unless text-height with an OCR word on it), since a renderer may
  drop decorations; an uncovered icon-like cluster is optional decoration;
* the page header/footer bands a template replaces are ignored only when
  they hold no OCR word at all.

``uncovered_ink`` is pure (pixels + boxes) so D can verify final output.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

Box = tuple[int, int, int, int]
SCALE = 2
LEVEL = 48
MIN_INK = 40
BAND = 0.035


def _components(mask: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    """(x0, y0, x1, y1, pixels) of 4-connected components, iterative flood fill."""

    height, width = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    result = []
    for y, x in zip(*np.nonzero(mask), strict=False):
        if seen[y, x]:
            continue
        stack = [(y, x)]
        seen[y, x] = True
        x0 = x1 = x
        y0 = y1 = y
        count = 0
        while stack:
            cy, cx = stack.pop()
            count += 1
            x0, x1, y0, y1 = min(x0, cx), max(x1, cx), min(y0, cy), max(y1, cy)
            for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        result.append((int(x0), int(y0), int(x1) + 1, int(y1) + 1, count))
    return result


def _fill(shape: tuple[int, int], boxes: list[Box]) -> np.ndarray:
    covered = np.zeros(shape, dtype=bool)
    for x, y, w, h in boxes:
        covered[max(0, y // SCALE) : (y + h) // SCALE + 1, max(0, x // SCALE) : (x + w) // SCALE + 1] = True
    return covered


def _hits(a: Box, b: Box) -> bool:
    return min(a[0] + a[2], b[0] + b[2]) > max(a[0], b[0]) and min(a[1] + a[3], b[1] + b[3]) > max(a[1], b[1])


def uncovered_ink(
    image: Image.Image,
    rendered: list[Box],
    decorations: list[Box],
    word_boxes: list[Box],
) -> list[Box]:
    """Source-pixel boxes of ink clusters no always-rendered region carries."""

    small = image.convert("L").resize((image.width // SCALE, image.height // SCALE), Image.Resampling.BILINEAR)
    background = np.asarray(small.filter(ImageFilter.MedianFilter(31 // SCALE * 2 + 1)), dtype=np.int16)
    gray = np.asarray(small, dtype=np.int16)
    ink = np.abs(gray - background) > LEVEL
    grown = np.asarray(Image.fromarray(ink.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(5))) > 0
    covered = _fill(ink.shape, rendered)
    covered_by_decoration = _fill(ink.shape, decorations)
    top, bottom = BAND * image.height, (1 - BAND) * image.height
    found: list[Box] = []
    for x0, y0, x1, y1, _ in _components(grown):
        box = (x0 * SCALE, y0 * SCALE, (x1 - x0) * SCALE, (y1 - y0) * SCALE)
        if box[2] < 12 or box[3] < 10 or min(box[2], box[3]) <= 3:
            continue
        cluster_ink = ink[y0:y1, x0:x1]
        pixels = int(cluster_ink.sum())
        if pixels < MIN_INK:
            continue
        has_words = any(_hits(box, word) for word in word_boxes)
        if not has_words and (box[1] + box[3] <= top or box[1] >= bottom):
            continue  # template header/footer decoration without text
        if (cluster_ink & covered[y0:y1, x0:x1]).sum() >= 0.5 * pixels:
            continue
        glyphs = len([c for c in _components(cluster_ink) if c[4] >= 6])
        # Few blobs is a pictogram (OCR often misreads icons as words); a short,
        # text-height mark with an OCR word on it is a character (e.g. "약").
        icon_like = glyphs < 3 and not (has_words and box[3] <= 32)
        if icon_like and (cluster_ink & (covered | covered_by_decoration)[y0:y1, x0:x1]).sum() >= 0.5 * pixels:
            continue
        if icon_like:
            continue  # a lone pictogram: optional decoration, not content
        found.append(box)
    return found


def cover_uncovered_ink(plan, words, image: Image.Image):
    """Add a pixel region for every uncovered ink cluster (see module docstring)."""

    from dataclasses import replace

    from services.source_content.raster_page_plan import PlannedRegion

    rendered = [r.bbox for r in plan.regions if r.role != "decoration"]
    decorations = [r.bbox for r in plan.regions if r.role == "decoration"]
    clusters = uncovered_ink(image, rendered, decorations, [word.bbox for word in words])
    if not clusters:
        return plan
    added = []
    for x, y, w, h in clusters:
        pad = 3
        box = (
            max(0, x - pad),
            max(0, y - pad),
            min(image.width, x + w + pad) - max(0, x - pad),
            min(image.height, y + h + pad) - max(0, y - pad),
        )
        added.append(
            PlannedRegion(
                "screenshot",
                box,
                (),
                confidence=0.5,
                review_reason="uncovered_ink",
                reason="uncovered_ink",
                editable_fallback=True,
                group_id=_nearest_group(plan.regions, box),
            )
        )
    return replace(plan, regions=plan.regions + tuple(added))


def _nearest_group(regions, box: Box) -> str | None:
    cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
    best, best_distance = None, float("inf")
    for region in regions:
        if region.group_id is None:
            continue
        x, y, w, h = region.bbox
        dx = max(x - cx, 0, cx - (x + w))
        dy = max(y - cy, 0, cy - (y + h))
        distance = (dx * dx + dy * dy) ** 0.5
        if distance < best_distance:
            best, best_distance = region.group_id, distance
    return best if best_distance <= 60 else None
