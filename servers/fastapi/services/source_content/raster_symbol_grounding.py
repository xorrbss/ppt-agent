"""Pixel grounding for characters that no independent reading contains.

Two readers agreeing within an edit budget is not proof of every character: the
model transcript can carry a character no OCR pass read anywhere on the line
("감가상각↑" where the readings end in "감가상각" and the source shows a middle
dot). Every such character must be grounded by the source with a shape of ITS
class, or the line is not published as text:

* a letter (or symbol) that replaces an OCR glyph of the same shape: a homoglyph
  pair of one size class ("Al" / "AI", "seqment" / "segment", "x" / "×") or a
  glyph read in the wrong script ("시" / "AI"). A case change ("summary" /
  "Summary"), another letter ("공개치" / "공개처") or a letter no reader saw at
  all ("승를" / "승패를") has no evidence of its identity;
* a symbol where the pixels show a glyph of that symbol's shape: one isolated
  glyph in the word gap where the OCR read nothing, or the glyph of the OCR symbol
  it replaces ("-" read for a middle dot, "~" for "≈"). A dot is small and round,
  a dash long and flat, an arrow a shaft with a head, "≈" two stacked strokes, a
  colon two stacked marks. A symbol without a shape rule is never grounded.
"""

from __future__ import annotations

import difflib
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from services.source_content.raster_text_correction import canonical, script_swaps_only

INK_DELTA = 40  # grey-level distance from the local background that counts as ink
EDGE_REACH = 1.5  # line heights searched before the first / after the last word
# Glyph pairs one reader can take for the other at the same size (never a case change).
HOMOGLYPHS = ("lI|", "gq", "x×")
DOT, COLON, DASH, TILDE, APPROX = "·", ":;", "-", "~", "≈"
ARROWS_RIGHT, ARROWS_LEFT, ARROWS_BOTH = "→⇒", "←⇐", "↔⇔"
ARROWS_UP, ARROWS_DOWN = "↑", "↓"


@dataclass(frozen=True)
class Glyph:
    box: tuple[int, int, int, int]  # page coordinates
    parts: int  # separate row runs inside the glyph's columns
    ink: np.ndarray  # boolean mask cropped to ``box``


def runs(values) -> list[tuple[int, int]]:
    """Maximal runs of True as (start, end) index pairs."""

    found, start = [], None
    for position, value in enumerate([*values, False]):
        if value and start is None:
            start = position
        elif not value and start is not None:
            found.append((start, position))
            start = None
    return found


def ink_glyphs(gray: np.ndarray, box: tuple[int, int, int, int]) -> list[Glyph]:
    """Ink glyphs (column runs) inside ``box``."""

    x, y, w, h = box
    region = gray[y : y + h, x : x + w]
    if w <= 0 or h <= 0 or region.size == 0:
        return []
    ink = np.abs(region - np.median(region)) > INK_DELTA
    glyphs = []
    for c0, c1 in runs(ink.any(axis=0)):
        rows = runs(ink[:, c0:c1].any(axis=1))
        r0, r1 = rows[0][0], rows[-1][1]
        glyphs.append(Glyph((x + c0, y + r0, c1 - c0, r1 - r0), len(rows), ink[r0:r1, c0:c1]))
    return glyphs


def _head(extents: np.ndarray) -> tuple[bool, bool]:
    """Whether the first / last third of a stroke is at least twice as thick as its shaft."""

    third = max(1, len(extents) // 3)
    shaft = max(1.0, float(np.median(extents)))
    return (
        float(extents[:third].max()) >= max(3.0, 2 * shaft),
        float(extents[-third:].max()) >= max(3.0, 2 * shaft),
    )


def shape_matches(ch: str, glyph: Glyph, band: tuple[int, int], height: float) -> bool:
    """The glyph has the shape of the symbol ``ch`` (canonical form) on a line of ``height``."""

    _, gy, gw, gh = glyph.box
    top, bottom = band
    centre = (gy + gh / 2 - top) / max(1, bottom - top)
    if ch == DOT:
        round_ = max(gw, gh) <= 2 * min(gw, gh) + 2
        return glyph.parts == 1 and gw <= 0.5 * height and gh <= 0.5 * height and round_ and 0.2 <= centre <= 0.85
    if ch in COLON:
        return glyph.parts == 2 and gw <= 0.5 * height and gh <= 0.9 * height
    if ch == APPROX:  # small waves overlap in rows, so count strokes per column
        stacked = sum(len(runs(column)) == 2 for column in glyph.ink.T)
        return stacked >= 0.6 * gw and gw >= gh and gh <= 0.7 * height
    if glyph.parts != 1:
        return False
    columns = glyph.ink.sum(axis=0)
    rows = glyph.ink.sum(axis=1)
    if ch in DASH + TILDE:
        flat = gw >= 2 * gh and gh <= 0.3 * height and not any(_head(columns))
        wavy = gh >= 1.8 * max(1.0, float(np.median(columns)))
        return flat and (wavy if ch == TILDE else not wavy)
    if ch in ARROWS_RIGHT + ARROWS_LEFT + ARROWS_BOTH:
        left, right = _head(columns)
        wanted = (ch in ARROWS_LEFT + ARROWS_BOTH, ch in ARROWS_RIGHT + ARROWS_BOTH)
        return gw >= 1.5 * gh and gh <= 0.8 * height and (left, right) == wanted
    if ch in ARROWS_UP + ARROWS_DOWN:
        up, down = _head(rows)
        return gh >= 1.5 * gw and gh <= 1.2 * height and (up, down) == (ch in ARROWS_UP, ch in ARROWS_DOWN)
    return False


def gap_holds(page, left, right, ch: str) -> bool:
    """Exactly one small isolated glyph of the symbol's shape between the word boxes
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
    for glyph in ink_glyphs(page.gray, (x0, top, x1 - x0, bottom - top)):
        touches_left, touches_right = glyph.box[0] <= x0, glyph.box[0] + glyph.box[2] >= x1
        if (touches_left and left is None) or (touches_right and right is None):
            return False
        if not (touches_left or touches_right):
            glyphs.append(glyph)
    return len(glyphs) == 1 and shape_matches(ch, glyphs[0], (top, bottom), height)


def reading_chars(words, carried: Sequence[int], unify=canonical) -> list[tuple[str, int]]:
    """Characters of the carried words (in ``unify`` form), each with its word index."""

    return [(ch, index) for index in carried for ch in unify(words[index].text)]


def _same_shape(read: str, text: str, offset: int) -> bool:
    if len(read) == len(text) and any(read[offset] in pair and text[offset] in pair for pair in HOMOGLYPHS):
        return True
    return script_swaps_only(read, text)


def _read_as_same_shape(readings: Sequence[str], text: str, position: int) -> bool:
    """Some reading holds, at this position, a glyph of the same shape."""

    for read in readings:
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, read, text, autojunk=False).get_opcodes():
            if tag == "replace" and j1 <= position < j2 and _same_shape(read[i1:i2], text[j1:j2], position - j1):
                return True
    return False


class _Slots:
    """Pixel slots of the primary reading: word gaps and the glyphs of read symbols."""

    def __init__(self, page, carried: Sequence[int]):
        self.page = page
        self.chars = reading_chars(page.words, carried)
        boxes = [page.words[i].bbox for i in carried]
        self.band = (min(b[1] for b in boxes), max(b[1] + b[3] for b in boxes)) if boxes else (0, 0)
        self.height = max((b[3] for b in boxes), default=0)
        self.used: set[tuple[int, int, int, int]] = set()

    def inserted(self, i1: int, ch: str) -> bool:
        left = self.chars[i1 - 1][1] if i1 > 0 else None
        right = self.chars[i1][1] if i1 < len(self.chars) else None
        if left == right:
            return False  # inside one OCR word: no gap holds the glyph
        words = self.page.words
        return gap_holds(
            self.page,
            words[left].bbox if left is not None else None,
            words[right].bbox if right is not None else None,
            ch,
        )

    def replaced(self, i1: int, ch: str) -> bool:
        """The OCR word holding the replaced symbol shows a glyph of ``ch``'s shape
        near that symbol's place in the word (each glyph grounds one character)."""

        word = self.chars[i1][1]
        x, _, w, _ = self.page.words[word].bbox
        own = [n for n, (_, index) in enumerate(self.chars) if index == word]
        expected = x + w * (own.index(i1) + 0.5) / len(own)
        reach = max(self.height, 1.5 * w / len(own))
        top, bottom = self.band
        # An OCR box can hold only part of a symbol (the shaft of an arrow): whole glyphs
        # are cut from a wider strip and kept when they overlap the word.
        x0, x1 = max(0, int(x - self.height)), min(self.page.size[0], int(x + w + self.height))
        for glyph in ink_glyphs(self.page.gray, (x0, top, x1 - x0, bottom - top)):
            gx, _, gw, _ = glyph.box
            centre = gx + gw / 2
            if gx + gw <= x or gx >= x + w or glyph.box in self.used or abs(centre - expected) > reach:
                continue
            if shape_matches(ch, glyph, self.band, self.height):
                self.used.add(glyph.box)
                return True
        return False


def unread_glyphs_grounded(page, carried: Sequence[int], candidates: Sequence[str], text: str) -> bool:
    """Every character of ``text`` that no candidate reading contains is grounded
    by a same-shape reading or by pixels of its own shape class."""

    value = canonical(text)
    readings = [canonical(c) for c in candidates if c and c.strip()]
    seen = set("".join(readings))
    unread = [j for j, ch in enumerate(value) if ch not in seen]
    if not unread:
        return True
    unread = [j for j in unread if not _read_as_same_shape(readings, value, j)]
    if not unread:
        return True
    if any(value[j].isalnum() for j in unread) or not carried:
        return False  # a letter or digit nobody read has no pixel evidence of its identity
    slots = _Slots(page, carried)
    primary = "".join(ch for ch, _ in slots.chars)
    ops = difflib.SequenceMatcher(None, primary, value, autojunk=False).get_opcodes()
    for j in unread:
        tag, i1, i2, j1, j2 = next(op for op in ops if op[3] <= j < op[4])
        if tag == "insert" and j2 - j1 == 1:
            grounded = slots.inserted(i1, value[j])
        elif tag == "replace" and i2 - i1 == j2 - j1 == 1 and not primary[i1].isalnum():
            grounded = slots.replaced(i1, value[j])
        else:
            grounded = False
        if not grounded:
            return False
    return True

