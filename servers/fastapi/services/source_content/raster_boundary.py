"""Crop/text boundary invariant for redesigned pages.

Pixels and editable text must never show the same content twice or split
it: a pixel crop (screenshot, pixel-fallback line, image table, decoration)
may not overlap an editable text region, and a crop and an editable text
region on the same row, touching, may not both carry digits (a large numeral
that OCR split across lines, e.g. "1" in a crop and "0,300" as text).

``boundary_conflicts`` is pure and shared by the planner (which repairs) and
the content ledger (which verifies).
"""

from __future__ import annotations

import re
from dataclasses import replace

import numpy as np

from services.source_content.raster_page_plan import PagePlan, PlannedRegion
from services.source_content.raster_structure import Box, boundary_conflicts, is_pixel_region

_DIGIT = re.compile(r"\d")


def _items(plan: PagePlan, words) -> list[tuple[bool, Box, bool]]:
    result = []
    for region in plan.regions:
        single = region.role != "table" and region.text is not None and "\n" not in region.text
        digits = single and any(_DIGIT.search(words[i].text) for i in region.word_indices)
        result.append((is_pixel_region(region.role, region.table_cells, region.editable_fallback), region.bbox, digits))
    return result


def _tight(region: PlannedRegion, words) -> Box | None:
    """The glyph extent of a pixel-fallback line crop (its box minus padding)."""

    if not region.editable_fallback or region.role != "screenshot" or not region.word_indices:
        return None
    boxes = [words[i].bbox for i in region.word_indices]
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    tight = (x0, y0, x1 - x0, y1 - y0)
    return tight if tight != region.bbox else None


def _trim(region: PlannedRegion, text: Box, words, keep: float = 0.7) -> Box | None:
    """Move one crop edge off a text block if that keeps every carried glyph and most pixels."""

    x, y, w, h = region.bbox
    tx, ty, tw, th = text
    options = [
        (x, ty + th, w, y + h - (ty + th)),  # top edge down below the text
        (x, y, w, ty - y),  # bottom edge up above the text
        (tx + tw, y, x + w - (tx + tw), h),  # left edge right of the text
        (x, y, tx - x, h),  # right edge left of the text
    ]
    for box in sorted((b for b in options if b[2] > 0 and b[3] > 0), key=lambda b: -(b[2] * b[3])):
        if box[2] * box[3] >= keep * region.bbox[2] * region.bbox[3] and all(
            _contains(box, words[i].bbox) for i in region.word_indices
        ):
            return box
    return None


def _contains(outer: Box, inner: Box) -> bool:
    cx, cy = inner[0] + inner[2] / 2, inner[1] + inner[3] / 2
    return outer[0] <= cx <= outer[0] + outer[2] and outer[1] <= cy <= outer[1] + outer[3]


def _union_box(a: Box, b: Box) -> Box:
    x0, y0 = min(a[0], b[0]), min(a[1], b[1])
    return (x0, y0, max(a[0] + a[2], b[0] + b[2]) - x0, max(a[1] + a[3], b[1] + b[3]) - y0)


def _collapse(regions: list[PlannedRegion], members: list[int]) -> PlannedRegion:
    chosen = [regions[i] for i in members]
    x0 = min(r.bbox[0] for r in chosen)
    y0 = min(r.bbox[1] for r in chosen)
    x1 = max(r.bbox[0] + r.bbox[2] for r in chosen)
    y1 = max(r.bbox[1] + r.bbox[3] for r in chosen)
    suggestion = "\n".join(r.text for r in chosen if r.text)
    return PlannedRegion(
        "screenshot",
        (x0, y0, x1 - x0, y1 - y0),
        tuple(i for r in chosen for i in r.word_indices),
        suggestion or None,
        confidence=min(r.confidence for r in chosen),
        review_reason="crop_text_boundary",
        reason="card_pixels",
        editable_fallback=bool(suggestion),
        group_id=chosen[0].group_id,
    )


def _region_lines(region: PlannedRegion, words) -> list[tuple[str, tuple[int, ...]]] | None:
    """Split a text region into its lines with their tokens (None if they do not align)."""

    lines = (region.text or "").split("\n")
    tokens = list(region.word_indices)
    result = []
    for line in lines:
        target, taken, built = "".join(line.split()), [], ""
        while tokens and len(built) < len(target):
            taken.append(tokens.pop(0))
            built += "".join(words[taken[-1]].text.split())
        if built != target:
            return None
        result.append((line, tuple(taken)))
    return result if not tokens else None


def _box_of(tokens, words, fallback: Box | None = None) -> Box:
    """Union box of the tokens' glyphs; ``fallback`` when there are none (a region
    may carry no OCR token, e.g. a native table rebuilt from cells)."""

    boxes = [words[i].bbox for i in tokens]
    if not boxes:
        if fallback is None:
            raise ValueError("box of an empty token set")
        return fallback
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    return (x0, y0, max(b[0] + b[2] for b in boxes) - x0, max(b[1] + b[3] for b in boxes) - y0)


def _absorb_lines(regions: list[PlannedRegion], pixel: int, text: int, words, gray) -> list[PlannedRegion]:
    """Merge the crop with only the text lines it conflicts with; the card's other
    lines stay editable. The whole card becomes one crop only when that merged
    crop would cover more than 60% of the card anyway."""

    crop, block = regions[pixel], regions[text]
    lines = _region_lines(block, words)
    digits = bool(crop.text) and "\n" not in crop.text and any(_DIGIT.search(words[i].text) for i in crop.word_indices)
    hit = []
    for n, (line, tokens) in enumerate(lines or ()):
        box = _box_of(tokens, words, block.bbox)
        line_digits = bool(_DIGIT.search(line))
        if tokens and (
            boundary_conflicts([(True, crop.bbox, digits), (False, box, line_digits)])
            or _ink_gap_conflicts([crop, replace(block, bbox=box, text=line, word_indices=tokens)], gray)
        ):
            hit.append(n)
    if not hit and lines and len(lines) > 1 and block.role == "body":
        # The crop sits between the block's lines (a figure between labels on one
        # row): the lines become separate text regions, none is taken.
        pieces = [
            replace(
                block,
                bbox=_box_of(tokens, words),
                word_indices=tokens,
                text=line,
                heading_line_count=1 if n < block.heading_line_count else 0,
            )
            for n, (line, tokens) in enumerate(lines)
            if tokens
        ]
        return regions[:text] + pieces + regions[text + 1 :]
    if hit or not lines:
        # Paragraph atomicity: a crop that needs one line of a text block takes the block.
        hit = list(range(len(lines or ())))
    taken = [t for n in hit for t in lines[n][1]] if lines else list(block.word_indices)
    merged_box = _union_box(crop.bbox, _box_of(taken, words, block.bbox))
    group = block.group_id or crop.group_id
    card = [r for r in regions if group and r.group_id == group]
    card_lines = sum(
        len((r.text or "").split("\n")) for r in card if not r.editable_fallback and r.role in ("body", "title")
    )
    real_capture = crop.role == "screenshot" and not crop.editable_fallback
    # Whole-card collapse only when the crop would take most of the card's text
    # anyway; a real capture takes only the lines it touches (no cascade over cards).
    if card and block.role != "title" and not real_capture and len(hit) > 0.6 * max(1, card_lines):
        members = sorted({pixel, text} | {i for i, r in enumerate(regions) if r.group_id == group})
        merged = replace(_collapse(regions, members), review_reason="crop_text_boundary_card")
        first = members[0]
        return [merged if i == first else r for i, r in enumerate(regions) if i == first or i not in members]
    taken_texts = [lines[n][0] for n in hit] if lines else [block.text or ""]
    suggestion = "\n".join(part for part in (crop.text, *taken_texts) if part)
    taken_box = _box_of(taken, words, block.bbox)
    parts_area = crop.bbox[2] * crop.bbox[3] + taken_box[2] * taken_box[3]
    if merged_box[2] * merged_box[3] > 1.5 * parts_area:
        # A union would sweep in unrelated pixels and grow into a cascade: the
        # conflicting lines become their own pixel crop next to the crop instead.
        absorbed = replace(
            block,
            role="screenshot",
            bbox=taken_box,
            word_indices=tuple(taken),
            text="\n".join(taken_texts) or None,
            table_cells=None,
            editable_fallback=True,
            review_reason="crop_text_boundary_line",
            heading_line_count=0,
        )
        crop_kept: list[PlannedRegion] = [crop]
    else:
        crop_kept = []
    absorbed = (
        absorbed
        if crop_kept
        else replace(
            crop,
            bbox=merged_box,
            word_indices=crop.word_indices + tuple(taken),
            text=None if real_capture else suggestion or None,
            editable_fallback=not real_capture,
            review_reason=crop.review_reason if real_capture else "crop_text_boundary_line",
        )
    )
    rest = [n for n in range(len(lines or ())) if n not in hit]
    pieces = []
    for run in _runs(rest):  # remaining lines stay text, split where the crop took lines out
        tokens = tuple(t for n in run for t in lines[n][1])
        if not tokens:
            continue
        pieces.append(
            replace(
                block,
                bbox=_box_of(tokens, words),
                word_indices=tokens,
                text="\n".join(lines[n][0] for n in run),
                heading_line_count=min(block.heading_line_count, sum(1 for n in run if n < block.heading_line_count)),
            )
        )
    result = [r for i, r in enumerate(regions) if i not in (pixel, text)]
    return result[: min(pixel, text)] + [*crop_kept, absorbed, *pieces] + result[min(pixel, text) :]


def _runs(indices: list[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for n in indices:
        if runs and runs[-1][-1] == n - 1:
            runs[-1].append(n)
        else:
            runs.append([n])
    return runs


def _ink_gap_conflicts(regions: list[PlannedRegion], gray, both_pixels: bool = False) -> list[tuple[int, int]]:
    """A crop and a neighbour on one row with unread ink between them split one item."""

    if gray is None:
        return []
    result = []
    for p, pixel in enumerate(regions):
        # Only text-line crops split items; a capture next to text with an arrow
        # or icon between them is a legitimate layout, not a split.
        if not pixel.editable_fallback or pixel.role != "screenshot" or not pixel.word_indices:
            continue
        for t, text in enumerate(regions):
            if t == p or not text.word_indices:
                continue
            if is_pixel_region(text.role, text.table_cells, text.editable_fallback) != both_pixels:
                continue
            if both_pixels and not (text.editable_fallback and text.role == "screenshot"):
                continue
            a, b = pixel.bbox, text.bbox
            top, bottom = max(a[1], b[1]), min(a[1] + a[3], b[1] + b[3])
            low = min(a[3], b[3])
            left, right = sorted((a, b), key=lambda box: box[0])
            x0, x1 = int(left[0] + left[2]), int(right[0])
            if bottom - top < 0.5 * low or not 0 < x1 - x0 <= 3 * low:
                continue
            strip = gray[int(top) : int(bottom), x0:x1]
            if strip.size and float((abs(strip - float(np.median(strip))) > 40).mean()) > 0.02:
                result.append((p, t))
    return result


def enforce_boundaries(plan: PagePlan, words, gray=None, _depth: int = 0) -> PagePlan:
    """Repair conflicts: trim a grazing crop, let a real screenshot absorb the text it
    covers, otherwise the whole card (group) involved becomes one pixel crop."""

    regions = list(plan.regions)
    for _ in range(4 * len(regions) + 1):  # every repair removes a region or shrinks a crop
        conflicts = boundary_conflicts(_items(replace(plan, regions=tuple(regions)), words))
        split = [] if conflicts else _ink_gap_conflicts(regions, gray)
        if not conflicts and not split:
            break
        pixel, text = (conflicts or split)[0]
        if regions[pixel].role == "decoration" and not regions[pixel].word_indices:
            regions.pop(pixel)  # an icon crop carries no content: never let it cover text
            continue
        pixel_digits = _items(replace(plan, regions=(regions[pixel],)), words)[0][2]
        text_item = _items(replace(plan, regions=(regions[text],)), words)[0]

        current_box = regions[pixel].bbox

        def resolves(box: Box, current=current_box, digits=pixel_digits, item=text_item) -> bool:
            return box != current and not boundary_conflicts([(True, box, digits), item])

        tight = _tight(regions[pixel], words)
        if tight is not None and resolves(tight):
            regions[pixel] = replace(regions[pixel], bbox=tight)  # only the padding overlapped
            continue
        trimmed = _trim(regions[pixel], regions[text].bbox, words) if not split else None
        if trimmed is not None and resolves(trimmed):
            regions[pixel] = replace(regions[pixel], bbox=trimmed)  # the crop only grazed the text block
            continue
        if regions[text].role == "title":
            relaxed = _trim(regions[pixel], regions[text].bbox, words, keep=0.2)
            if relaxed is not None and resolves(relaxed):
                regions[pixel] = replace(regions[pixel], bbox=relaxed)  # titles stay editable
                continue
        regions = _absorb_lines(regions, pixel, text, words, gray)
    # Two pixel crops of one split item become one crop covering the unread ink;
    # the wider crop is then checked against text again.
    pairs = _ink_gap_conflicts(regions, gray, both_pixels=True)
    if pairs and _depth < 8:
        first, second = sorted(pairs[0])
        regions[first] = _collapse(regions, [first, second])
        regions.pop(second)
        return enforce_boundaries(replace(plan, regions=tuple(regions)), words, gray, _depth + 1)
    return replace(plan, regions=tuple(regions))
