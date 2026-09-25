"""Geometry helpers and application-capture detection for raster redesign.

A capture (screenshot, chart, photo) is a bordered panel or a cluster whose
content is dominated by UI evidence: micro text below slide scale and
unreadable UI glyphs. Ordinary slide text vetoes a candidate.
"""

from __future__ import annotations

import re

import numpy as np
from PIL import Image

from services.source_content.raster_panels import panel_rectangles
from services.source_content.raster_regions import _dark_toolbar_candidates
from services.source_content.raster_redesign_ocr import MICRO_TEXT_RATIO, RedesignLine, RedesignWord, line_text

Box = tuple[int, int, int, int]
_LETTER = re.compile(r"[0-9A-Za-z가-힣]")
_HANGUL = re.compile(r"[가-힣]")
_DIGIT = re.compile(r"\d")


def _union(boxes: list[Box]) -> Box:
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return (x0, y0, x1 - x0, y1 - y0)


def _inter(a: Box, b: Box) -> int:
    return max(0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])) * max(
        0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    )


def _inside(box: Box, outer: Box) -> float:
    return _inter(box, outer) / max(1, box[2] * box[3])


def _center_in(box: Box, outer: Box) -> bool:
    cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
    return outer[0] <= cx <= outer[0] + outer[2] and outer[1] <= cy <= outer[1] + outer[3]


def _pad(box: Box, pad: int, size: tuple[int, int]) -> Box:
    x0, y0 = max(0, box[0] - pad), max(0, box[1] - pad)
    x1, y1 = min(size[0], box[0] + box[2] + pad), min(size[1], box[1] + box[3] + pad)
    return (x0, y0, max(1, x1 - x0), max(1, y1 - y0))


def _gap(a: Box, b: Box) -> float:
    dx = max(0, max(a[0], b[0]) - min(a[0] + a[2], b[0] + b[2]))
    dy = max(0, max(a[1], b[1]) - min(a[1] + a[3], b[1] + b[3]))
    return max(dx, dy)


def _clusters(boxes: list[Box], distance: float) -> list[list[int]]:
    parent = list(range(len(boxes)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if _gap(boxes[i], boxes[j]) <= distance:
                parent[root(j)] = root(i)
    groups: dict[int, list[int]] = {}
    for i in range(len(boxes)):
        groups.setdefault(root(i), []).append(i)
    return list(groups.values())


def _merge_overlapping(boxes: list[Box]) -> list[Box]:
    merged = list(boxes)
    changed = True
    while changed:
        changed = False
        for i in range(len(merged)):
            for j in range(i + 1, len(merged)):
                if _inter(merged[i], merged[j]) > 0:
                    merged[i] = _union([merged[i], merged[j]])
                    del merged[j]
                    changed = True
                    break
            if changed:
                break
    return merged


class PageContext:
    def __init__(self, image: Image.Image, words: tuple[RedesignWord, ...], lines: tuple[RedesignLine, ...]):
        self.image = image
        self.size = image.size
        self.words = words
        self.lines = [line for line in lines if line.word_indices]
        self.micro_px = MICRO_TEXT_RATIO * image.height
        self.gray = np.asarray(image.convert("L"), dtype=np.int16)
        self.rgb = np.asarray(image.convert("RGB"), dtype=np.int16)

    def conf(self, indices) -> float:
        weights = [(max(1, len(self.words[i].text)), self.words[i].confidence) for i in indices]
        total = sum(w for w, _ in weights)
        return sum(w * (c if c is not None else 0.5) for w, c in weights) / max(1, total)

    def doubtful(self, line: RedesignLine) -> bool:
        """UI captures read badly: a low combined score is capture evidence too."""
        agreement = line.agreement if line.agreement is not None else 1.0
        return 0.5 * self.conf(line.word_indices) + 0.5 * agreement < 0.6 or line.numbers_disagree

    def is_garbage(self, line: RedesignLine) -> bool:
        value = "".join(line_text(line, self.words).split())
        letters = _LETTER.findall(value)
        if not letters:
            return True
        confidence = self.conf(line.word_indices)
        if confidence < 0.45 or (line.agreement is not None and line.agreement < 0.3 and confidence < 0.8):
            return True
        if not _HANGUL.search(value) and not _DIGIT.search(value) and len(value) <= 4:
            return not (value.isupper() and len(letters) >= 2 and confidence >= 0.85)
        return False


def detect_captures(page: PageContext) -> list[Box]:
    """Application captures: bordered panels, or clusters, dominated by UI evidence.

    Evidence is micro text (below the slide text scale), unreadable UI glyphs
    and chart/photo pixels. Ordinary slide text vetoes a candidate, so text
    cards with a few icons stay editable.
    """

    evidence_lines = [line for line in page.lines if line.height <= page.micro_px or page.is_garbage(line)]
    normal = [line for line in page.lines if line.height > page.micro_px and not page.is_garbage(line)]
    page_area = page.size[0] * page.size[1]

    def counts(box: Box) -> tuple[int, list[RedesignLine]]:
        inside = sum(1 for line in evidence_lines if _center_in(line.bbox, box))
        return inside, [line for line in normal if _center_in(line.bbox, box)]

    def dominated(box: Box, minimum: int, ratio: float) -> bool:
        evidence, texts = counts(box)
        if len(texts) >= 3 and sorted(line.height for line in texts)[len(texts) // 2] > 1.3 * page.micro_px:
            return False  # a card of slide-scale text, whatever icons it holds
        return evidence >= minimum and evidence >= ratio * len(texts)

    def pictorial(box: Box) -> bool:
        # Charts and photos: mostly saturated non-text pixels.
        _, texts = counts(box)
        ratio = _graphic_ratio(page, box, texts)
        return ratio >= 0.5 or (ratio >= 0.2 and not texts)

    small_px = 1.3 * page.micro_px
    panels = [box for box in panel_rectangles(page.gray) if box[2] * box[3] >= 0.002 * page_area]
    large = [box for box in panels if box[2] * box[3] >= 0.012 * page_area]
    accepted = [box for box in panels if pictorial(box)]
    accepted += [box for box in large if dominated(box, 5, 1.0)]
    accepted += _toolbar_windows(page, panels, small_px)
    kept_panels = []
    for box in accepted:
        children = [
            other
            for other in accepted
            if other != box and _inside(other, box) >= 0.9 and other[2] * other[3] < 0.8 * box[2] * box[3]
        ]
        if children:
            # Slide text outside the inner captures (a card heading above a
            # screenshot) belongs to the slide: keep only the inner captures.
            envelope = _union(children)
            _, texts = counts(box)
            if sum(len(line.word_indices) for line in texts if not _center_in(line.bbox, envelope)) >= 6:
                continue
        kept_panels.append(box)
    outermost: list[Box] = []
    for box in sorted(kept_panels, key=lambda b: b[2] * b[3], reverse=True):
        if not any(_inside(box, kept) >= 0.9 for kept in outermost):
            outermost.append(box)
    parts = list(outermost)
    found = _merge_overlapping(outermost)
    # Borderless captures: dense clusters of sub-slide text and UI glyphs.
    small = [
        line
        for line in page.lines
        if (line.height <= page.micro_px or page.is_garbage(line) or page.doubtful(line))
        and not any(_center_in(line.bbox, box) for box in found)
    ]
    for group in _clusters([line.bbox for line in small], 0.05 * page.size[1]):
        if len(group) < 10:
            continue
        cluster = _union([small[i].bbox for i in group])
        inside = [line for line in page.lines if _center_in(line.bbox, cluster)]
        slide_scale = [line for line in inside if line.height > small_px and not page.is_garbage(line)]
        heights = sorted(line.height for line in inside)
        ui_scale = heights[len(heights) // 2] <= page.micro_px
        if cluster[2] * cluster[3] >= 0.02 * page_area and ui_scale and len(slide_scale) <= 0.25 * len(inside):
            snapped = _snap_to_border(page, _pad(cluster, 6, page.size))
            parts.append(snapped)
            found.append(snapped)
    return _without_slide_text(page, parts, _merge_overlapping(found), small_px)


def _slide_heavy(page: PageContext, box: Box, small_px: float) -> bool:
    inside = [line for line in page.lines if _center_in(line.bbox, box)]
    slide = [line for line in inside if line.height > small_px and not page.is_garbage(line)]
    chars = sum(len(line.word_indices) for line in inside)
    heights = sorted(line.height for line in inside)
    typical = heights[len(heights) // 2] if heights else 0.0
    return typical > 1.1 * page.micro_px or sum(len(line.word_indices) for line in slide) > 0.25 * max(1, chars)


def _without_slide_text(page: PageContext, parts: list[Box], merged: list[Box], small_px: float) -> list[Box]:
    """A capture must not swallow slide text: undo merges that would."""

    result: list[Box] = []
    for box in merged:
        if not _slide_heavy(page, box, small_px):
            result.append(box)
            continue
        result.extend(part for part in parts if _inside(part, box) >= 0.99 and not _slide_heavy(page, part, small_px))
    final: list[Box] = []
    for box in sorted(result, key=lambda b: b[2] * b[3], reverse=True):
        if not any(_inside(box, kept) >= 0.9 for kept in final):
            final.append(box)
    return final


def _toolbar_windows(page: PageContext, panels: list[Box], small_px: float) -> list[Box]:
    """Application windows recognised by a dark title/tool bar above UI-scale text."""

    scale = min(1.0, 640 / max(page.size))
    windows: list[Box] = []
    for _, (left, top, width, height) in _dark_toolbar_candidates(page.image, scale):
        x0, y0 = round(left / scale), round(top / scale)
        x1, y1 = min(page.size[0], round((left + width) / scale)), min(page.size[1], round((top + height) / scale))
        # Prefer a bordered panel that starts at the bar; otherwise stop above a
        # headline-scale line in the lower half (the window ended before it).
        matches = [b for b in panels if abs(b[0] - x0) <= 12 and abs(b[0] + b[2] - x1) <= 12 and abs(b[1] - y0) <= 12]
        if matches:
            y1 = max(b[1] + b[3] for b in matches)
        else:
            below = [
                line.bbox[1]
                for line in page.lines
                if line.height > 1.6 * page.micro_px
                and not page.is_garbage(line)
                and y0 + 0.5 * (y1 - y0) < line.bbox[1] < y1
                and x0 <= line.bbox[0] + line.bbox[2] / 2 <= x1
            ]
            y1 = min([y1, *[y - 6 for y in below]])
        box = (x0, y0, x1 - x0, y1 - y0)
        heights = sorted(line.height for line in page.lines if _center_in(line.bbox, box))
        if box[3] > 60 and len(heights) >= 5 and heights[len(heights) // 2] <= 1.2 * page.micro_px:
            windows.append(box)
    return windows


def _graphic_ratio(page: PageContext, box: Box, texts: list[RedesignLine]) -> float:
    """Share of a box covered by saturated, non-text pixels (charts, photos)."""

    x, y, width, height = box
    rgb = page.rgb[y : y + height, x : x + width]
    saturated = (rgb.max(axis=2) - rgb.min(axis=2)) >= 60
    for line in texts:
        lx, ly, lw, lh = line.bbox
        saturated[max(0, ly - y) : max(0, ly - y + lh), max(0, lx - x) : max(0, lx - x + lw)] = False
    return float(saturated.mean()) if saturated.size else 0.0


def _snap_to_border(page: PageContext, box: Box, reach: int = 90) -> Box:
    """Grow each side of a capture envelope to the nearest panel border line."""

    gray = page.gray
    height, width = gray.shape
    x0, y0, x1, y1 = box[0], box[1], box[0] + box[2], box[1] + box[3]

    def column_line(x: int) -> bool:
        if not 1 <= x < width - 1:
            return False
        strip = np.abs(gray[y0:y1, x + 1] - gray[y0:y1, x - 1])
        return strip.size > 0 and float((strip >= 6).mean()) >= 0.6

    def row_line(y: int) -> bool:
        if not 1 <= y < height - 1:
            return False
        strip = np.abs(gray[y + 1, x0:x1] - gray[y - 1, x0:x1])
        return strip.size > 0 and float((strip >= 6).mean()) >= 0.6

    left = next((x for x in range(x0, max(0, x0 - reach), -1) if column_line(x)), None)
    right = next((x for x in range(x1, min(width, x1 + reach)) if column_line(x)), None)
    top = next((y for y in range(y0, max(0, y0 - reach), -1) if row_line(y)), None)
    bottom = next((y for y in range(y1, min(height, y1 + reach)) if row_line(y)), None)
    nx0 = max(0, left - 2) if left is not None else x0
    ny0 = max(0, top - 2) if top is not None else y0
    nx1 = min(width, right + 3) if right is not None else x1
    ny1 = min(height, bottom + 3) if bottom is not None else y1
    return (nx0, ny0, nx1 - nx0, ny1 - ny0)


def boundary_confidence(page: PageContext, box: Box) -> float:
    """A clean crop edge has quiet pixels just outside it; cut content does not."""

    height, width = page.gray.shape
    x0, y0, x1, y1 = box[0] - 3, box[1] - 3, box[0] + box[2] + 3, box[1] + box[3] + 3
    samples: list[np.ndarray] = []
    if y0 >= 1:
        samples.append(np.abs(np.diff(page.gray[y0, max(0, x0) : min(width, x1)])))
    if y1 < height:
        samples.append(np.abs(np.diff(page.gray[y1, max(0, x0) : min(width, x1)])))
    if x0 >= 1:
        samples.append(np.abs(np.diff(page.gray[max(0, y0) : min(height, y1), x0])))
    if x1 < width:
        samples.append(np.abs(np.diff(page.gray[max(0, y0) : min(height, y1), x1])))
    values = np.concatenate(samples) if samples else np.zeros(1)
    busy = float((values >= 25).mean()) if values.size else 0.0
    return max(0.0, min(1.0, 1.0 - 2.5 * busy))
