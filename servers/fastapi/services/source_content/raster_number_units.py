"""Whole visual numbers: the unit in which a number is verified.

OCR splits large numerals into per-glyph tokens ("128.4" as "1", "2", ...), drops
glyphs at a word's edge ("60%" read as "0%" with a box that starts at the "0"), and
reads a number wrapped over two rows ("18." above "6B") as two numbers. A targeted
re-read of one fragment's own box confirms the fragment ("1" reads "1") and so
confirms a wrong number. The invariant enforced here: a number is verified only as
the WHOLE visual number.

* A unit is a run of OCR words on one visual row (overlapping baselines, similar
  glyph height) whose horizontal gaps are below ``GLYPH_GAP`` glyph heights,
  seeded by digit-bearing words; separators (``, . %``) join regardless of their
  small height. Rows are never merged vertically.
* The unit box is then extended over adjacent ink of the same glyph height on the
  same baseline that no OCR word carries (a glyph OCR dropped), so the crop always
  shows the whole number.
* A number that continues beyond its row is ``wrapped`` and never confirmed: a unit
  alone on its row with a row of glyphs of the same height and colour stacked
  directly above or below it ("0" / "1", "$17" / "0.9", "89.7" over a "%" cut by
  its panel), or with a filled shape of its own colour right under it that may hide
  the rest (a value label on its bar: "53" whose "%" is behind the bar), unless the
  number is complete (it ends in a letter, a counter word, "%" or ")": a column of
  counts is a list); or a unit ending in a separator right after a digit with
  nothing after it on the row ("18." whose "6B" is elsewhere).
* The unit is read once, as one line, from the crop of its whole box (English, and
  restricted to digits and separators; see ``raster_text_guards``). Ink below
  ``MIN_HEIGHT`` pixels is too small for any read to confirm.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

Box = tuple[int, int, int, int]

GLYPH_GAP = 0.25  # glyph gap inside one number, in glyph heights (word spaces are wider)
SAME_SIZE = (0.7, 1.45)  # glyph height ratio range of one number's glyphs
ROW_GAP = 1.0  # a same-size glyph closer than this (glyph heights) shares the row
WRAP_GAP = 1.0  # max vertical gap to a stacked row, in glyph heights
WRAP_WIDTH = 2.6  # a continuation row is at most this many glyph heights wide
CLIPPED_SIZE = 0.4  # least visible height of a continuation glyph cut by a panel edge
SAME_COLOR = 40  # grey levels: a continuation row is drawn in the number's colour
MIN_HEIGHT = 5  # pixels: smaller ink cannot be verified as glyphs
LEVEL = 48  # ink: differs from the local background by more than this (as raster_tight_boxes)
MIN_LEVEL = 16  # ... or, for faint text, by more than this at least
SEPARATORS = set(",.%·'")
NUMBER = re.compile(r"\d[\d,.]*%?")
_HANGUL = re.compile(r"[가-힣]")
_LETTER = re.compile(r"[A-Za-z가-힣]")
_OPEN_END = re.compile(r"\d[.,]$")  # "18." / "2,": a number that goes on elsewhere


@dataclass(frozen=True)
class NumberUnit:
    words: tuple[int, ...]  # OCR words of the unit, left to right
    box: Box  # the whole visual number (word union extended over dropped glyphs)
    text: str  # the OCR words joined without spaces
    wrapped: bool  # continues beyond its row: cannot be verified as one number


@dataclass(frozen=True)
class UnitRead:
    unit: NumberUnit
    plain: tuple[str, ...]  # number tokens of the English single-line read
    restricted: tuple[str, ...] | None  # of the digit-whitelist read (None: not read)

    def readings(self) -> tuple[tuple[str, ...], ...]:
        """Number sequences this read confirms (empty: it confirms nothing)."""

        plain, restricted = self.plain, self.restricted or ()
        if self.unit.wrapped or self.unit.box[3] < MIN_HEIGHT or not plain:
            # The digit-only read alone proves nothing: it forces any glyph into a digit.
            return ()
        if _HANGUL.search(self.unit.text):
            # Both reads turn Hangul glyphs into stray digits after the number
            # ("2,067시간" -> "2,067", "2", "4"; "5.9억" -> "5.99"): a leading run of either
            # read's tokens may be the unit's numbers (``number_matches`` allows the
            # appended digits).
            options = [tokens[:n] for tokens in (plain, restricted) for n in range(len(tokens), 0, -1)]
            return tuple(dict.fromkeys(options))
        if not restricted:
            return (plain,)
        if plain == restricted:
            return (restricted,)
        if _LETTER.search(self.unit.text):
            # The whitelist read maps letters to digits ("G4" -> "64", "6B" -> "68").
            return (plain,)
        # Without letters the two reads must agree, up to the English model's known
        # reading of "%" as "96" ("83%" -> "8396"), where the whitelist read is right.
        if tuple(t.replace("%", "96") for t in restricted) == tuple(t.replace("%", "96") for t in plain):
            return (restricted,)
        return ()


def _union(boxes: Sequence[Box]) -> Box:
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return (x0, y0, x1 - x0, y1 - y0)


def _separator(text: str) -> bool:
    value = "".join(text.split())
    return bool(value) and set(value) <= SEPARATORS


def _same_row(a: Box, b: Box, small_ok: bool) -> bool:
    overlap = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    if small_ok:  # a separator glyph sits inside the row band
        return overlap >= 0.6 * b[3] and b[3] <= 1.1 * a[3]
    ratio = b[3] / max(1, a[3])
    return SAME_SIZE[0] <= ratio <= SAME_SIZE[1] and overlap >= 0.6 * min(a[3], b[3])


def _hgap(a: Box, b: Box) -> int:
    return max(b[0] - (a[0] + a[2]), a[0] - (b[0] + b[2]))


def _joined(words, a: int, b: int) -> bool:
    """Two words are glyphs of one number: same row, similar height (or a separator
    inside the row band), closer than a glyph gap of the smaller glyphs."""

    first, second = words[a], words[b]
    big, small = (first, second) if first.bbox[3] >= second.bbox[3] else (second, first)
    reference = big.bbox[3] if _separator(small.text) else small.bbox[3]
    if _hgap(big.bbox, small.bbox) > GLYPH_GAP * reference:
        return False
    return _same_row(big.bbox, small.bbox, _separator(small.text))


def _group(words, seeds: list[int]) -> list[list[int]]:
    """Seed words grown over row neighbours closer than a glyph gap (connected groups)."""

    live = [j for j, word in enumerate(words) if "".join(word.text.split())]
    groups: list[list[int]] = []
    assigned: set[int] = set()
    for seed in dict.fromkeys(seeds):
        if seed in assigned:
            continue
        group, queue = [seed], [seed]
        assigned.add(seed)
        while queue:
            i = queue.pop()
            for j in live:
                if j not in assigned and _joined(words, i, j):
                    assigned.add(j)
                    group.append(j)
                    queue.append(j)
        groups.append(sorted(group, key=lambda k: words[k].bbox[0]))
    return groups


class Ink:
    """Ink mask of a horizontal band around a box (pixels off the local background)."""

    def __init__(self, gray: np.ndarray, box: Box, reach: float):
        x, y, w, h = box
        self.top = max(0, int(y - reach * h))
        self.bottom = min(gray.shape[0], int(y + h + reach * h) + 1)
        self.x0 = max(0, int(x - 6 * h))
        x1 = min(gray.shape[1], int(x + w + 6 * h) + 1)
        patch = gray[self.top : self.bottom, self.x0 : x1]
        self.mask = _ink(patch) if patch.size else None

    def glyph_rows(self, c0: int, c1: int, y0: int, y1: int) -> tuple[int, int] | None:
        """Contiguous ink rows of columns c0..c1 touching rows y0..y1; None when they
        reach the band's edge (taller than any glyph of this row)."""

        if self.mask is None:
            return None
        rows = self.mask[:, max(0, c0 - self.x0) : max(0, c1 - self.x0)].any(axis=1)
        inside = [r for r in range(max(0, y0 - self.top), min(len(rows), y1 - self.top)) if rows[r]]
        if not inside:
            return None
        start, end = inside[0], inside[-1]
        while start > 0 and rows[start - 1]:
            start -= 1
        while end + 1 < len(rows) and rows[end + 1]:
            end += 1
        if start == 0 or end == len(rows) - 1:
            return None
        return self.top + start, self.top + end + 1

    def columns(self, y0: int, y1: int) -> np.ndarray:
        """Glyph columns of rows y0..y1 (a thin horizontal rule is not a glyph)."""

        if self.mask is None:
            return np.zeros(0, dtype=bool)
        band = self.mask[max(0, y0 - self.top) : max(0, y1 - self.top)]
        return band.sum(axis=0) >= max(2, int(0.12 * (y1 - y0)))


def _ink(patch: np.ndarray, background: int | None = None) -> np.ndarray:
    """Pixels off the background by more than LEVEL, or by 40% of the patch's strongest
    contrast when that is lower (faint grey axis labels on a dark chart)."""

    level = int(np.median(patch)) if background is None else background
    difference = np.abs(patch.astype(np.int16) - level)
    strongest = float(np.percentile(difference, 99.5)) if difference.size else 0.0
    return difference > max(MIN_LEVEL, min(LEVEL, 0.4 * strongest))


def runs(values: np.ndarray, offset: int = 0, bridge: int = 0) -> list[tuple[int, int]]:
    """Runs of true values as (start, end) with ``offset`` added; gaps up to ``bridge`` join."""

    result: list[list[int]] = []
    for n, value in enumerate(values):
        if not value:
            continue
        if result and n - result[-1][1] <= bridge:
            result[-1][1] = n + 1
        else:
            result.append([n, n + 1])
    return [(offset + a, offset + b) for a, b in result]


def row_width(mask: np.ndarray, start: int, end: int, left: int, width: int, h: int) -> float:
    """Width of the glyphs of rows start..end reachable from columns left..left+width.

    Thin rules and long thin strokes (a chart line) are not glyphs, nor are shapes
    continuing above or below the row (a chart marker, a bar); without a glyph over the box columns the row is
    unbounded (inf)."""

    near = max(1, int(0.15 * h))
    mask = mask & ~(mask.mean(axis=1) > 0.9)[:, None]  # a panel edge is not a shape
    continuing = np.zeros(mask.shape[1], dtype=bool)
    if start - near >= 0:
        continuing |= mask[start - near : start].all(axis=0)
    if end + near <= len(mask):
        continuing |= mask[end : end + near].all(axis=0)
    counts = mask[start:end].sum(axis=0)
    columns = [
        (c0, c1)
        for c0, c1 in runs((counts >= max(2, int(0.25 * h))) & ~continuing)
        if not (c1 - c0 > 1.5 * h and counts[c0:c1].mean() <= 0.35 * h)
    ]
    span = [r for r in columns if r[1] > left and r[0] < left + width]
    if not span:
        return float("inf")
    lo, hi = min(r[0] for r in span), max(r[1] for r in span)
    changed = True
    while changed:
        changed = False
        for c0, c1 in columns:
            if c1 <= lo and lo - c1 <= ROW_GAP * h:
                lo, changed = c0, True
            elif c0 >= hi and c0 - hi <= ROW_GAP * h:
                hi, changed = c1, True
    return hi - lo


def _row_glyphs(ink: Ink, box: Box) -> list[tuple[int, int]]:
    """Column runs of glyphs on the box's row: same glyph height, same baseline band."""

    x, y, w, h = box
    result = []
    for c0, c1 in runs(ink.columns(y, y + h), ink.x0):
        rows = ink.glyph_rows(c0, c1, y, y + h)
        if rows is None:
            continue  # reaches beyond the band: a rule, a bar, a picture
        top, bottom = rows
        overlap = min(bottom, y + h) - max(top, y)
        if SAME_SIZE[0] * h <= bottom - top <= SAME_SIZE[1] * h and overlap >= 0.5 * h:
            result.append((c0, c1))
    return result


def _extended(glyphs: list[tuple[int, int]], box: Box) -> Box:
    """The box grown over adjacent same-size glyphs (glyphs no OCR word carries)."""

    x, y, w, h = box
    left, right = x, x + w
    limit = max(2.0, GLYPH_GAP * h)
    changed = True
    while changed:
        changed = False
        for c0, c1 in glyphs:
            if c0 < left and left - c1 <= limit:  # a glyph cut by, or just before, the box
                left, changed = c0, True
            if c1 > right and c0 - right <= limit:
                right, changed = c1, True
    return (left, y, right - left, h)


def _carried(glyph: tuple[int, int], box: Box, words, group) -> bool:
    """Whether another OCR word of the row carries the glyph (it is not a dropped one)."""

    c0, c1 = glyph
    members = set(group)
    for j, word in enumerate(words):
        if j in members:
            continue
        x, y, w, h = word.bbox
        overlap = min(y + h, box[1] + box[3]) - max(y, box[1])
        if overlap >= 0.5 * min(h, box[3]) and min(c1, x + w) - max(c0, x) >= 0.5 * (c1 - c0):
            return True
    return False


def _neighbours(glyphs: list[tuple[int, int]], box: Box) -> tuple[bool, bool]:
    """Whether a same-size glyph lies within a row gap to the left / to the right."""

    x, y, w, h = box
    reach = ROW_GAP * h
    left = any(x - reach <= c1 <= x - 1 for c0, c1 in glyphs)
    right = any(x + w + 1 <= c0 <= x + w + reach for c0, c1 in glyphs)
    return left, right


def _stacked(gray: np.ndarray, box: Box, below: bool) -> bool:
    """A row of glyphs of the same height directly above/below the box, over its columns.

    Ink is judged against the box's own background, so a panel edge is not a row;
    a filled band (a panel, a bar) is not glyphs, and the row is drawn in the
    number's own colour. Below, that row is the
    continuation and must be short; above, the box itself is the continuation and
    must be short."""

    x, y, w, h = box
    if not below and w > WRAP_WIDTH * h:
        return False
    reach = int((WRAP_GAP + SAME_SIZE[1]) * h) + 1
    y0, y1 = (y + h, min(gray.shape[0], y + h + reach)) if below else (max(0, y - reach), y)
    x0, x1 = max(0, x - 6 * h), min(gray.shape[1], x + w + 6 * h)
    if y1 - y0 < 2 or x1 <= x0:
        return False
    band = gray[y : y + h, x0:x1]
    background = int(np.median(band))
    own = _ink(band, background)[:, x - x0 : x - x0 + w]
    if not own.any():
        return False
    color = float(np.median(band[:, x - x0 : x - x0 + w][own]))
    region = gray[y0:y1, x0:x1]
    mask = _ink(region, background)
    left = x - x0
    edge = mask.mean(axis=1) > 0.9  # another background: the panel ends here
    for start, end in runs(mask[:, left : left + w].any(axis=1) & ~edge, 0, max(1, int(0.06 * h))):
        distance = start if below else len(mask) - end
        # A glyph row cut by the panel's edge ("89.7" over a clipped "%") shows only part of its height.
        clipped = (below and end < len(mask) and edge[end]) or (not below and start > 0 and edge[start - 1])
        low = CLIPPED_SIZE if clipped else SAME_SIZE[0]
        if distance > WRAP_GAP * h or not low * h <= end - start <= SAME_SIZE[1] * h:
            continue  # a small label, a rule, or a partial glyph edge: look further
        cells = mask[start:end, left : left + w]
        if cells.mean() > 0.6:
            continue  # filled: a panel or a bar, not glyphs
        if abs(float(np.median(region[start:end, left : left + w][cells])) - color) > SAME_COLOR:
            continue  # drawn in another colour (a chart line, a label): not the number's row
        if not below or row_width(mask, start, end, left, w, h) <= WRAP_WIDTH * h:
            return True
    return False


def _occluded(gray: np.ndarray, box: Box) -> bool:
    """A bounded filled shape of the number's own colour right under it, across all its
    columns (a value label on its bar): a continuation row ("%", a unit) may hide
    behind it, so the number cannot be seen whole. Panels wider than the search
    window (a card, a table body) are not such shapes."""

    x, y, w, h = box
    x0, x1 = max(0, x - 6 * h), min(gray.shape[1], x + w + 6 * h)
    y0, y1 = y + h, min(gray.shape[0], y + h + int((WRAP_GAP + 0.5) * h) + 1)
    if y1 - y0 < 2 or x1 <= x0:
        return False
    band = gray[y : y + h, x0:x1]
    own = _ink(band, int(np.median(band)))[:, x - x0 : x - x0 + w]
    if not own.any():
        return False
    color = float(np.median(band[:, x - x0 : x - x0 + w][own]))
    region = gray[y0:y1, x0:x1]
    filled = np.abs(region.astype(np.int16) - color) <= SAME_COLOR / 2
    left, depth = x - x0, max(2, int(0.4 * h))
    rows = filled[:, left : left + w].mean(axis=1) >= 0.9
    for r in range(min(len(rows) - depth, int(WRAP_GAP * h)) + 1):
        if not rows[r : r + depth].all():
            continue
        spans = [(c0, c1) for c0, c1 in runs(filled[r]) if c0 <= left and c1 >= left + w]
        return bool(spans) and all(c0 > 0 and c1 < filled.shape[1] for c0, c1 in spans)
    return False


def _terminated(text: str) -> bool:
    """A reading that ends in a letter, a closing bracket or "%" (a counter word, a unit)
    completes its number: the row under it does not continue that number."""

    value = "".join(text.split())
    return bool(value) and (value[-1].isalpha() or value[-1] in "%)")


def _wraps(gray: np.ndarray, box: Box, text: str, words, alone: bool) -> bool:
    """The unit continues a number on the row below (or behind a shape under it), or
    continues the row above."""

    if not _terminated(text) and _occluded(gray, box):
        return True
    if not alone:
        return False
    if not _terminated(text) and _stacked(gray, box, True):
        return True
    if not _stacked(gray, box, False):
        return False
    # The row above, when OCR read it, must not be a complete number itself
    # (a column of counts "4건" / "1건" is a list, not one wrapped number).
    x, y, w, h = box
    reach = (WRAP_GAP + SAME_SIZE[1]) * h
    above = [
        word
        for word in words
        if word.bbox[1] + word.bbox[3] <= y + 0.2 * h
        and word.bbox[1] + word.bbox[3] >= y - reach
        and min(word.bbox[0] + word.bbox[2], x + w) - max(word.bbox[0], x) > 0
        and SAME_SIZE[0] * h <= word.bbox[3] <= SAME_SIZE[1] * h
    ]
    if not above:
        return True
    nearest = max(word.bbox[1] + word.bbox[3] for word in above)
    row = [word for word in above if word.bbox[1] + word.bbox[3] >= nearest - 0.5 * h]
    return not _terminated(max(row, key=lambda word: word.bbox[0] + word.bbox[2]).text)


def number_units(gray: np.ndarray, words, seeds: Iterable[int]) -> tuple[NumberUnit, ...]:
    """The whole visual numbers of the digit-bearing ``seeds`` (word indices)."""

    seeds = [i for i in seeds if any(ch.isdigit() for ch in words[i].text)]
    units = []
    for group in _group(words, seeds):
        base = _union([words[i].bbox for i in group])
        glyphs = _row_glyphs(Ink(gray, base, 0.6), base)
        box = _extended([g for g in glyphs if not _carried(g, base, words, group)], base)
        left, right = _neighbours(glyphs, box)
        text = "".join("".join(words[i].text.split()) for i in group)
        truncated = bool(_OPEN_END.search(text)) and not right
        stacked = _wraps(gray, box, text, words, alone=not left and not right)
        units.append(NumberUnit(tuple(group), box, text, truncated or stacked))
    return tuple(sorted(units, key=lambda unit: (unit.box[1], unit.box[0])))


def text_numbers(text: str) -> list[tuple[str, bool]]:
    """Number tokens of a reading in order, with whether a Hangul glyph follows each.

    Spaces OCR put inside a number next to a separator ("9, 860") are ignored;
    other spaces separate numbers ("Q3 2025" holds two)."""

    value = re.sub(r"(?<=\d)\s+(?=[,.%]\d?)|(?<=\d[,.])\s+(?=\d)", "", text)
    return [(m.group(), bool(_HANGUL.match(value[m.end() : m.end() + 1]))) for m in NUMBER.finditer(value)]


def number_matches(number: str, read: str, hangul_follows: bool) -> bool:
    """A read confirms a number when equal; the English model may append digits for a
    Hangul glyph right after the number ("40,000원" read "40,0008")."""

    if read == number:
        return True
    return hangul_follows and read.startswith(number) and read[len(number) :].rstrip(",.").isdigit()


def sequence_confirmed(numbers: Sequence[tuple[str, bool]], reads: Sequence[UnitRead], *, allow_skip: bool) -> bool:
    """The numbers of a reading equal, in order, the numbers of its units' reads.

    Each unit confirms a contiguous run of the numbers; a wrapped or unreadable unit
    confirms nothing. With ``allow_skip`` a unit may be left out (the reading omits
    that number), but every number of the reading must be confirmed by a unit."""

    options = [read.readings() for read in reads]

    def match(position: int, unit: int) -> bool:
        if unit == len(options):
            return position == len(numbers)
        if allow_skip and match(position, unit + 1):
            return True
        for sequence in options[unit]:
            end = position + len(sequence)
            if end <= len(numbers) and all(
                number_matches(numbers[position + k][0], token, numbers[position + k][1])
                for k, token in enumerate(sequence)
            ):
                if match(end, unit + 1):
                    return True
        return False

    return match(0, 0)
