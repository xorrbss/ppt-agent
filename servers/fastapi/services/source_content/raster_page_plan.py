"""Classify one image-only slide into editable text, tables and preserved crops.

Only pixels that cannot become editable content stay images: application
screen captures (recognised by dense micro text inside a frame) and icons.
Everything else is reconstructed as title/body/table text. Confidence is
measured, never assumed, and only low-confidence regions are flagged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from PIL import Image

from services.source_content.raster_redesign_ocr import RedesignLine, RedesignWord, line_text
from services.source_content.raster_captures import (
    Box,
    PageContext,
    _center_in,
    _clusters,
    _inter,
    _inside,
    _merge_overlapping,
    _pad,
    _union,
    boundary_confidence,
    detect_captures,
)
from services.source_content.raster_structure import xy_cut_order
from services.source_content.raster_tables import TableCells, TableGrid, assign_table_cells, detect_ruled_tables

REVIEW_CONFIDENCE = 0.6
_LETTER = re.compile(r"[0-9A-Za-z가-힣]")
_HANGUL = re.compile(r"[가-힣]")
_DIGIT = re.compile(r"\d")
_LATIN_RUN = re.compile(r"[A-Za-z]+")
_RUN_ON = re.compile(r"[가-힣]{9,}")
_TEXT_SYMBOLS = set("*•-·※▪■□◆○●>→<|/()[]%+~,.:;!?\"'“”‘’&@#=ㆍ・")


@dataclass(frozen=True)
class PlannedRegion:
    role: str
    bbox: Box
    word_indices: tuple[int, ...] = ()
    text: str | None = None
    table_cells: tuple[tuple[str, ...], ...] | None = None
    confidence: float = 1.0
    review_reason: str | None = None
    reason: str = ""
    # Low-confidence text kept as source pixels; ``text``/``table_cells``
    # carry the OCR reading only as a suggestion for optional review.
    editable_fallback: bool = False
    # Card/section membership and, for body text, how many leading lines
    # form the card heading.
    group_id: str | None = None
    heading_line_count: int = 0


@dataclass(frozen=True)
class PagePlan:
    regions: tuple[PlannedRegion, ...]
    unpromoted: tuple[int, ...]
    # Token inventory when corrected readings were added (None: the OCR words).
    words: tuple[RedesignWord, ...] | None = None
    # Audit log of accepted text corrections: "before => after".
    corrections: tuple[str, ...] = ()
    structured_by: str | None = None
    # Page-level notes for review (e.g. "heuristic_plan:structuring_failed:TimeoutError").
    advisories: tuple[str, ...] = ()


def _unpromoted(page: PageContext) -> set[int]:
    result = set()
    for index, word in enumerate(page.words):
        if word.bbox[3] < 4:
            result.add(index)  # a rule or border edge, not a glyph
            continue
        if _DIGIT.search(word.text):
            continue
        symbols_only = all(ch in _TEXT_SYMBOLS for ch in "".join(word.text.split()))
        if word.bbox[3] < 4 or (not _LETTER.search(word.text) and not symbols_only and (word.confidence or 0) < 0.6):
            result.add(index)
    return result


def _is_edge_junk(page: PageContext, index: int, line_height: float) -> bool:
    word = page.words[index]
    value = "".join(word.text.split())
    if _HANGUL.search(value) or _DIGIT.search(value) or all(ch in _TEXT_SYMBOLS for ch in value):
        return False
    if word.bbox[3] > 1.5 * line_height:
        return True
    letters = _LETTER.findall(value)
    if not letters:
        return True
    return len(value) <= 3 and not (value.isupper() and len(value) >= 2 and (word.confidence or 0) >= 0.85)


def _strip_edges(page: PageContext, line: RedesignLine) -> tuple[list[int], list[int]]:
    kept = list(line.word_indices)
    junk: list[int] = []
    while len(kept) > 1 and _is_edge_junk(page, kept[0], line.height):
        junk.append(kept.pop(0))
    while len(kept) > 1 and _is_edge_junk(page, kept[-1], line.height):
        junk.append(kept.pop())
    return kept, junk


def _text_confidence(page: PageContext, lines: list[tuple[list[int], RedesignLine]]) -> tuple[float, str | None]:
    indices = [i for kept, _ in lines for i in kept]
    engine = page.conf(indices)
    weighted = [(len(kept), line.agreement) for kept, line in lines if line.agreement is not None]
    agreement = sum(w * a for w, a in weighted) / sum(w for w, _ in weighted) if weighted else engine
    confidence = round(0.5 * engine + 0.5 * agreement, 4)
    if any(line.numbers_disagree for _, line in lines):
        return min(confidence, 0.55), "numeric_reading_disagreement"
    # Korean prose rarely runs 9+ syllables without a space: a reading that does
    # lost its word boundaries and, typically, some glyphs with them.
    rendered = [line_text(RedesignLine(tuple(kept), line.bbox, line.height), page.words) for kept, line in lines]
    if any(_RUN_ON.search(value) for value in rendered):
        return min(confidence, 0.55), "word_spacing_lost"
    if confidence < REVIEW_CONFIDENCE:
        return confidence, "low_ocr_confidence"
    return confidence, None


def _block_text(page: PageContext, lines: list[tuple[list[int], RedesignLine]]) -> str:
    rendered = []
    for kept, line in lines:
        rendered.append(line_text(RedesignLine(tuple(kept), line.bbox, line.height), page.words))
    return "\n".join(rendered)


def _text_blocks(page: PageContext, lines: list[tuple[list[int], RedesignLine]]) -> list[PlannedRegion]:
    if not lines:
        return []
    boxes = [_word_box(page, kept) for kept, _ in lines]
    heights = [line.height for _, line in lines]
    parent = list(range(len(lines)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    doubtful = [_text_confidence(page, [line])[1] is not None for line in lines]
    order = sorted(range(len(lines)), key=lambda i: (boxes[i][1], boxes[i][0]))
    for a_pos, a in enumerate(order):
        for b in order[a_pos + 1 :]:
            if doubtful[a] != doubtful[b]:
                continue  # a doubtful line never drags a sound paragraph into pixels
            ha, hb = heights[a], heights[b]
            gap = boxes[b][1] - (boxes[a][1] + boxes[a][3])
            if gap > 1.2 * max(ha, hb):
                break
            if max(ha, hb) > 1.3 * min(ha, hb) or gap < -0.3 * min(ha, hb):
                continue
            overlap = min(boxes[a][0] + boxes[a][2], boxes[b][0] + boxes[b][2]) - max(boxes[a][0], boxes[b][0])
            aligned = abs(boxes[a][0] - boxes[b][0]) <= 1.5 * max(ha, hb)
            if gap <= 0.9 * min(ha, hb) + 4 and (aligned or overlap >= 0.5 * min(boxes[a][2], boxes[b][2])):
                parent[root(b)] = root(a)
    groups: dict[int, list[int]] = {}
    for i in order:
        groups.setdefault(root(i), []).append(i)
    title_group = _title_group(groups, boxes, heights, lines, page)
    regions = []
    for key, members in groups.items():
        members.sort(key=lambda i: (boxes[i][1], boxes[i][0]))
        block = [lines[i] for i in members]
        confidence, review = _text_confidence(page, block)
        box = _union([boxes[i] for i in members])
        tokens = tuple(i for kept, _ in block for i in kept)
        if review is not None:
            # Never publish a doubtful reading as text: the pixels carry it.
            regions.append(
                PlannedRegion(
                    "screenshot",
                    _pad(box, 3, page.size),
                    tokens,
                    _block_text(page, block),
                    confidence=confidence,
                    review_reason=review,
                    reason="low_confidence_text_pixels",
                    editable_fallback=True,
                )
            )
            continue
        role = "title" if key == title_group else "body"
        regions.append(
            PlannedRegion(role, box, tokens, _block_text(page, block), confidence=confidence, reason="ocr_text_block")
        )
    return regions


def _title_group(groups, boxes, heights, lines, page: PageContext) -> int | None:
    """Pick the headline block and fold adjacent headline-sized lines into it."""

    median = sorted(heights)[(len(heights) - 1) // 2]
    tall_of = {key: max(heights[i] for i in members) for key, members in groups.items()}
    top_of = {key: min(boxes[i][1] for i in members) for key, members in groups.items()}
    candidates = [
        key
        for key, members in groups.items()
        if tall_of[key] >= 1.25 * median and page.conf([w for i in members for w in lines[i][0]]) >= 0.5
    ]
    if not candidates:
        return None
    # Slide titles sit in the top band; a cover page centres a larger one.
    overall = max(tall_of[key] for key in candidates)
    banded = [
        key for key in candidates if top_of[key] <= 0.22 * page.size[1] and tall_of[key] >= 0.5 * overall
    ] or candidates
    tallest = max(tall_of[key] for key in banded)
    title = min((key for key in banded if tall_of[key] >= 0.8 * tallest), key=lambda key: top_of[key])
    changed = True
    while changed:
        changed = False
        box = _union([boxes[i] for i in groups[title]])
        for key in list(groups):
            if key == title or tall_of[key] < 0.75 * tall_of[title]:
                continue
            other = _union([boxes[i] for i in groups[key]])
            gap = max(other[1] - (box[1] + box[3]), box[1] - (other[1] + other[3]))
            overlap = min(box[0] + box[2], other[0] + other[2]) - max(box[0], other[0])
            if gap <= 0.8 * tall_of[title] and (overlap > 0 or abs(box[0] - other[0]) <= tall_of[title]):
                groups[title].extend(groups.pop(key))
                changed = True
                break
    return title


def _word_box(page: PageContext, indices) -> Box:
    return _union([page.words[i].bbox for i in indices])


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


def line_needs_help(page: PageContext, line: RedesignLine) -> bool:
    """A line whose OCR reading cannot be published as text on its own."""

    text = line_text(line, page.words)
    value = "".join(text.split())
    if page.is_garbage(line) or len(_LETTER.findall(value)) < 1:
        return True
    # A Latin run inside Korean text that is not acronym-shaped (all caps, at
    # most five letters) is a typical misread ("AYLIch"): it needs confirmation.
    if _HANGUL.search(value) and any(not (run.isupper() and len(run) <= 5) for run in _LATIN_RUN.findall(text)):
        return True
    return _text_confidence(page, [(list(line.word_indices), line)])[1] is not None


def _unreadable_fragment(page: PageContext, line: RedesignLine) -> bool:
    """A few low-confidence glyphs are kept as pixels, not offered as text."""

    value = "".join(line_text(line, page.words).split())
    agreement = line.agreement if line.agreement is not None else 1.0
    confidence = 0.5 * page.conf(line.word_indices) + 0.5 * agreement
    return len(value) <= 10 and (confidence < REVIEW_CONFIDENCE or line.numbers_disagree)


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


def _tables(page: PageContext, screenshots: list[Box], claimed: set[int]) -> list[PlannedRegion]:
    result = []
    for detected in detect_ruled_tables(page.image, screenshots):
        grid, inside, cells = _best_grid(page, detected, claimed)
        if not inside:
            continue
        structured = cells.cells is not None and _dense_enough(cells.cells)
        if not structured and not grid.ruled_columns:
            continue  # stacked cards or fields, not a table: leave the text editable
        order = (
            tuple(i for row in cells.cell_words for cell in row for i in cell)
            if structured
            else tuple(sorted(inside, key=lambda i: (inside[i].bbox[1], inside[i].bbox[0])))
        )
        claimed.update(order)
        confidence, review, suggestion = 0.7, None, None
        if structured:
            line_map = {i: line for line in page.lines for i in line.word_indices if i in inside}
            unique = [(list(line.word_indices), line) for line in {id(v): v for v in line_map.values()}.values()]
            confidence, _ = _text_confidence(page, unique)
            if any(_text_confidence(page, [item])[1] for item in unique):
                # A doubtful cell reading keeps the whole table as source pixels.
                review, structured = "table_cell_ocr_low_confidence", False
                suggestion = "\n".join("\t".join(row) for row in cells.cells)
        result.append(
            PlannedRegion(
                "table",
                grid.bbox,
                order,
                text=suggestion,
                table_cells=cells.cells if structured else None,
                confidence=confidence,
                review_reason=review,
                reason=cells.reason,
                editable_fallback=review is not None,
            )
        )
    return result


def _best_grid(page: PageContext, grid: TableGrid, claimed: set[int]):
    """Try the detected grid, then without an outer band (a card header or
    footer framed by the same rules), and keep the first clean structure."""

    attempts = []
    edges = grid.row_edges
    for top, bottom in ((0, 0), (1, 0), (0, 1), (1, 1)):
        rows = edges[top : len(edges) - bottom]
        if len(rows) < 3:
            continue
        x0, _, width, _ = grid.bbox
        candidate = TableGrid((x0, rows[0], width, rows[-1] - rows[0]), rows, grid.column_edges, grid.ruled_columns)
        inside = {
            i: page.words[i]
            for i in range(len(page.words))
            if i not in claimed and _center_in(page.words[i].bbox, candidate.bbox)
        }
        cells = assign_table_cells(
            candidate,
            {i: w.bbox for i, w in inside.items()},
            {i: w.text for i, w in inside.items()},
            {i: w.space_before for i, w in inside.items()},
        )
        attempts.append((candidate, inside, cells))
        if cells.cells is not None and _dense_enough(cells.cells):
            return candidate, inside, cells
    return attempts[0] if attempts else (grid, {}, TableCells(None, (), "table_grid_too_small"))


def _dense_enough(cells: tuple[tuple[str, ...], ...]) -> bool:
    rows_with_text = sum(1 for row in cells if sum(1 for cell in row if cell) >= 2)
    filled = sum(1 for row in cells for cell in row if cell)
    return rows_with_text >= max(2, 0.6 * len(cells)) and filled >= 0.5 * len(cells) * len(cells[0])
