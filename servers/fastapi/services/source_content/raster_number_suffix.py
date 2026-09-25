"""Numbers with a unit word written right after them ("약 12.5억원", "120회", "9,860시간").

The unit word joins the number's visual unit (the glyph gap is a glyph gap), and
the English and digit-only readers turn its Hangul glyphs into stray digits or a
"%", and lose a decimal point next to them ("2.0억원" read "204", "5.9억원" read
"5.9%"). A unit whose read does not confirm its own OCR text is therefore also read
split in two at a glyph gap before the unit word:

* the head (the number glyphs) is read like a unit, by the English and the
  digit-only reader, which must agree;
* the tail (the unit word) is read by the Korean reader, which must read Hangul
  only, with its words spanning the tail's ink from edge to edge (a digit dropped at
  the tail's edge would leave ink uncovered).

A reading's number is confirmed by a split only when the head reads it, the head's
glyphs have its shape, and the reading writes the tail's Hangul right after it
(``raster_number_match``): a cut inside the number never confirms it, since the
cut-off digits are then in the tail, which does not read as the reading's Hangul.

Cuts are glyph gaps (ink-free columns of the unit's row) at the left edge of each
later OCR word of the unit (words OCR read as garbage still end where glyphs end),
and after the glyphs of the number a word's text starts with, when the word's box
holds more glyphs ("120회" as one word, or "19,720" whose box also covers "시간").

Both readers may drop a decimal point kerned tight between two digits ("5.9" read
"59"). The head's glyph shape (``glyph_shape``: one glyph per character, a small dot
on the baseline at each point) shows the point, so such a read still confirms "5.9",
and never confirms a reading "59".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from services.source_content.raster_number_units import MIN_HEIGHT, Box, NumberUnit, _ink, runs

MAX_SPLITS = 3  # cuts tried per unit, the rightmost first (unit words are short)
SNAP = 0.35  # a cut snaps to a glyph gap this close to a word edge, in glyph heights
HEAD_WIDTH = 0.4  # least head width, in glyph heights
TAIL_WIDTH = (0.5, 4.5)  # tail width range (one to four syllables), in glyph heights
COVER_SLACK = 0.25  # Korean read words may miss this much of the tail's ink at an edge
TALL = 0.6  # a full-height glyph, as a share of the tallest glyph of the head
DOT = 0.35  # a separator's greatest width, and a point's greatest height, as a share of the tallest glyph
COMMA = 0.45  # a comma's greatest height (its tail reaches below the baseline)
_SUFFIX = re.compile(r"[가-힣]+")
_HEAD = re.compile(r"[^가-힣\d]*\d[\d,.]*%?")  # "120" of "120회", "~3" of "~3배"


@dataclass(frozen=True)
class SuffixSplit:
    head: Box  # the number glyphs, left of the cut
    tail: Box  # the unit word, right of the cut


@dataclass(frozen=True)
class SplitRead:
    plain: tuple[str, ...]  # number tokens of the head's English read
    restricted: tuple[str, ...]  # of the head's digit-only read
    suffix: str  # the tail's Korean read ("" unless Hangul only and spanning the tail's ink)
    shape: str  # glyph kinds of the head (``glyph_shape``)

    def option(self) -> tuple[tuple[str, ...], str, str] | None:
        if not self.suffix or not self.plain or self.plain != self.restricted:
            return None
        return (self.plain, self.suffix, self.shape)


def glyph_runs(gray: np.ndarray, box: Box) -> list[tuple[int, int]]:
    """Ink column runs of the box (absolute x, end exclusive)."""

    x, y, w, h = box
    patch = gray[max(0, y) : y + h, max(0, x) : x + w]
    if not patch.size:
        return []
    return runs(_ink(patch).any(axis=0), max(0, x))


def suffix_splits(gray: np.ndarray, unit: NumberUnit, words) -> tuple[SuffixSplit, ...]:
    """Cuts of the unit before a unit word, at glyph gaps (rightmost ``MAX_SPLITS``)."""

    x, y, w, h = unit.box
    if unit.wrapped or h < MIN_HEIGHT:
        return ()
    glyphs = glyph_runs(gray, unit.box)
    gaps = [(a[1], b[0]) for a, b in zip(glyphs, glyphs[1:], strict=False)]
    if not gaps:
        return ()
    order = sorted(unit.words, key=lambda i: words[i].bbox[0])
    targets = [float(words[i].bbox[0]) for i in order[1:]]
    for i in order:
        head = _HEAD.match("".join(words[i].text.split()))
        inside = [g for g in glyphs if g[1] > words[i].bbox[0]]
        if head and len(inside) > len(head.group()):
            k = len(head.group())
            targets.append((inside[k - 1][1] + inside[k][0]) / 2)
    cuts = set()
    for target in targets:
        near = [g for g in gaps if abs((g[0] + g[1]) / 2 - target) <= SNAP * h]
        if near:
            a, b = min(near, key=lambda g: abs((g[0] + g[1]) / 2 - target))
            cut = (a + b + 1) // 2
            if cut - x >= HEAD_WIDTH * h and TAIL_WIDTH[0] * h <= x + w - cut <= TAIL_WIDTH[1] * h:
                cuts.add(cut)
    chosen = sorted(cuts, reverse=True)[:MAX_SPLITS]
    return tuple(SuffixSplit((x, y, cut - x, h), (cut, y, x + w - cut, h)) for cut in sorted(chosen))


def glyph_shape(gray: np.ndarray, box: Box) -> str:
    """Kinds of the box's glyphs left to right: "d" full height, "." a small blob on the
    baseline, "," a small blob reaching below it, "?" anything else."""

    x, y, w, h = box
    pad = max(2, int(0.3 * h))
    top = max(0, y - pad)
    patch = gray[top : y + h + pad, max(0, x) : x + w]
    if not patch.size:
        return ""
    mask = _ink(patch)
    extents = []
    for c0, c1 in runs(mask.any(axis=0)):
        rows = np.flatnonzero(mask[:, c0:c1].any(axis=1))
        extents.append((c1 - c0, int(rows[0]), int(rows[-1]) + 1))
    if not extents:
        return ""
    tallest = max(bottom - start for _, start, bottom in extents)
    baseline = float(np.median([bottom for _, start, bottom in extents if bottom - start >= TALL * tallest]))
    kinds = []
    for width, start, bottom in extents:
        if bottom - start >= TALL * tallest:
            kinds.append("d")
        elif width <= DOT * tallest and start >= baseline - 0.45 * tallest:
            low = bottom <= baseline + 0.1 * tallest
            if low and bottom - start <= DOT * tallest:
                kinds.append(".")
            elif not low and bottom - start <= COMMA * tallest:
                kinds.append(",")
            else:
                kinds.append("?")
        else:
            kinds.append("?")
    return "".join(kinds)


def tail_suffix(spans, origin: int, factor: float, ink: list[tuple[int, int]], height: int) -> str:
    """The tail's Korean read when it is Hangul only and its words span the tail's ink.

    ``spans`` are the read's words as (text, left, width) in the crop, which starts at
    ``origin`` and was scaled by ``factor``; ``ink`` are the tail's glyph runs."""

    text = "".join("".join(t.split()) for t, _, _ in spans)
    if not spans or not ink or not _SUFFIX.fullmatch(text):
        return ""
    left = origin + min(left for _, left, _ in spans) / factor
    right = origin + max(left + width for _, left, width in spans) / factor
    slack = COVER_SLACK * height
    return text if left <= ink[0][0] + slack and right >= ink[-1][1] - slack else ""
