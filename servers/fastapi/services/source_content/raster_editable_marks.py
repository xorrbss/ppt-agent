"""Pixel grounding for punctuation marks the OCR dropped or misread.

Tesseract routinely loses small separator marks: a middle dot between words
("PART II · GEOGRAPHY" read as "PART II GEOGRAPHY"), a list bullet, or reads
them as another symbol ("-", "*", "."). The model transcription keeps them, so
the two readers "disagree" on clean text and the line stays pixels. A mark the
model adds is still never trusted on its own ("구성해드리:" was a hallucinated
colon); it is grounded only by the source itself:

* an inserted mark at a boundary between two OCR words (or just before the
  first / after the last word) is grounded when that gap holds exactly one
  small, isolated ink glyph of the mark's shape;
* a middle dot / bullet the OCR read as another single symbol ("-", "*", ".")
  is grounded by that OCR glyph (same position, both readers saw a mark).

Marks between two digits are never grounded here: a separator inside a number
("1.5" vs "1·5") is a number question for the number gate. ``grounded_view``
returns the transcript with grounded marks rewritten to what the OCR read there,
for the agreement comparison only; the published text is still the model's
transcript and still passes every number and symbol guard.
"""

from __future__ import annotations

import difflib

import numpy as np

from services.source_content.raster_text_correction import normalized

DOT_MARKS = "·"  # normalized form of middle dots and bullets (•, ㆍ, ・, ∙)
COLON_MARKS = ":;"
# Single OCR glyphs a middle dot or bullet is commonly read as.
DOT_MISREADS = frozenset("-.*'`,°")
INK_DELTA = 40  # grey-level distance from the local background that counts as ink
EDGE_REACH = 1.5  # line heights searched before the first / after the last word


def _reading_chars(words, carried: tuple[int, ...]) -> list[tuple[str, int]]:
    """Normalized characters of the carried words, each with its word index."""

    chars: list[tuple[str, int]] = []
    for index in carried:
        for ch in normalized(words[index].text):
            chars.append((ch, index))
    return chars


def _runs(values) -> list[tuple[int, int]]:
    """Maximal runs of True as (start, end) index pairs."""

    runs, start = [], None
    for position, value in enumerate([*values, False]):
        if value and start is None:
            start = position
        elif not value and start is not None:
            runs.append((start, position))
            start = None
    return runs


def _ink_glyphs(gray: np.ndarray, box: tuple[int, int, int, int]) -> list[tuple[tuple[int, int, int, int], int]]:
    """Ink glyphs (column runs) inside ``box``: page box and number of separate row runs."""

    x, y, w, h = box
    region = gray[y : y + h, x : x + w]
    if w <= 0 or h <= 0 or region.size == 0:
        return []
    ink = np.abs(region - np.median(region)) > INK_DELTA
    glyphs = []
    for c0, c1 in _runs(ink.any(axis=0)):
        rows = _runs(ink[:, c0:c1].any(axis=1))
        glyphs.append(((x + c0, y + rows[0][0], c1 - c0, rows[-1][1] - rows[0][0]), len(rows)))
    return glyphs


def _mark_shape(mark: str, glyph: tuple[int, int, int, int], parts: int, band: tuple[int, int], height: float) -> bool:
    _, gy, gw, gh = glyph
    top, bottom = band
    if gw > 0.5 * height:
        return False
    if mark in DOT_MARKS:
        centre = (gy + gh / 2 - top) / max(1, bottom - top)
        return parts == 1 and gh <= 0.5 * height and 0.2 <= centre <= 0.85
    return parts == 2 and gh <= 0.9 * height  # colon / semicolon: two stacked marks


def _gap_holds_mark(page, left, right, mark: str) -> bool:
    """Exactly one small isolated glyph of the mark's shape between the word boxes
    (or beyond the edge word when one side is None).

    Word boxes can cut a glyph by a pixel, so a sliver touching a word box is that
    word's ink and ignored; ink touching the far end of an edge search means more
    content follows, and nothing is grounded."""

    boxes = [b for b in (left, right) if b is not None]
    top = min(b[1] for b in boxes)
    bottom = max(b[1] + b[3] for b in boxes)
    height = max(b[3] for b in boxes)
    reach = int(EDGE_REACH * height)
    x0 = left[0] + left[2] if left is not None else max(0, right[0] - reach)
    x1 = right[0] if right is not None else min(page.size[0], left[0] + left[2] + reach)
    if x1 - x0 < 3:
        return False
    glyphs = []
    for glyph, parts in _ink_glyphs(page.gray, (x0, top, x1 - x0, bottom - top)):
        touches_left, touches_right = glyph[0] <= x0, glyph[0] + glyph[2] >= x1
        if (touches_left and left is None) or (touches_right and right is None):
            return False
        if not (touches_left or touches_right):
            glyphs.append((glyph, parts))
    if len(glyphs) != 1:
        return False
    glyph, parts = glyphs[0]
    return _mark_shape(mark, glyph, parts, (top, bottom), height)


def _between_digits(reading: str, start: int, end: int) -> bool:
    before = reading[start - 1] if start > 0 else ""
    after = reading[end] if end < len(reading) else ""
    return before.isdigit() and after.isdigit()


def grounded_view(page, carried: tuple[int, ...], transcript: str) -> str:
    """The normalized transcript with every pixel-grounded mark rewritten to the
    OCR's reading at that spot (removed where the OCR read nothing)."""

    chars = _reading_chars(page.words, carried)
    reading = "".join(ch for ch, _ in chars)
    text = normalized(transcript)
    if not chars or not text:
        return text
    out: list[str] = []
    matcher = difflib.SequenceMatcher(None, reading, text, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        segment = text[j1:j2]
        if tag == "equal" or len(segment) != 1 or segment not in DOT_MARKS + COLON_MARKS:
            out.append(segment)
            continue
        if tag == "insert" and not _between_digits(reading, i1, i2):
            left = chars[i1 - 1][1] if i1 > 0 else None
            right = chars[i1][1] if i1 < len(chars) else None
            if left != right and _gap_holds_mark(
                page,
                page.words[left].bbox if left is not None else None,
                page.words[right].bbox if right is not None else None,
                segment,
            ):
                continue  # the pixels show the mark the OCR dropped
        if (
            tag == "replace"
            and segment in DOT_MARKS
            and i2 - i1 == 1
            and reading[i1] in DOT_MISREADS
            and not _between_digits(reading, i1, i2)
        ):
            out.append(reading[i1])  # the OCR saw a mark here, only its shape differs
            continue
        out.append(segment)
    return "".join(out)
