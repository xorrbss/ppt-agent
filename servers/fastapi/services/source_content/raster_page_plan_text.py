"""Group OCR lines into text blocks and judge whether a reading is confident
enough to publish as text, versus being kept as source pixels.
"""

from __future__ import annotations

import re

from services.source_content.raster_captures import Box, PageContext, _pad, _union
from services.source_content.raster_page_plan_regions import PlannedRegion
from services.source_content.raster_redesign_ocr import RedesignLine, line_text

REVIEW_CONFIDENCE = 0.6
_LETTER = re.compile(r"[0-9A-Za-z가-힣]")
_HANGUL = re.compile(r"[가-힣]")
_DIGIT = re.compile(r"\d")
_LATIN_RUN = re.compile(r"[A-Za-z]+")
_RUN_ON = re.compile(r"[가-힣]{9,}")
_TEXT_SYMBOLS = set("*•-·※▪■□◆○●>→<|/()[]%+~,.:;!?\"'“”‘’&@#=ㆍ・")


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
