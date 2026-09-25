"""Tighten OCR word boxes to the glyph ink they actually cover.

Tesseract boxes of large type are often inflated (a title word 57 px tall
reported 84-92 px tall, reaching into the subtitle below). Every geometric
invariant (crop edges, line crops, transcription crops) needs the real glyph
extent, so each word box is cut to the densest run of ink rows inside it:
rows whose pixels differ from the box's background (its median) by more than
``LEVEL``, joined across gaps of at most ``max(2, 12% of the box height)``.
A box keeps its width and never grows.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

LEVEL = 48
_MIN_ROWS = 4


def tight_box(gray: np.ndarray, box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x, y, w, h = (int(v) for v in box)
    patch = gray[max(0, y) : y + h, max(0, x) : x + w]
    if patch.size == 0 or h < 3 * _MIN_ROWS:
        return box
    ink = np.abs(patch - np.median(patch)) > LEVEL
    rows = ink.sum(axis=1)
    gap_limit = max(2, int(0.12 * h))
    runs: list[list[int]] = []  # [start, end, ink]
    gap = gap_limit + 1
    for r, count in enumerate(rows):
        if count:
            if runs and gap <= gap_limit:
                runs[-1][1] = r
                runs[-1][2] += int(count)
            else:
                runs.append([r, r, int(count)])
            gap = 0
        else:
            gap += 1
    if not runs:
        return box
    start, end, _ = max(runs, key=lambda run: run[2])
    if end - start + 1 < _MIN_ROWS:
        return box
    top = max(0, y) + start
    return (x, top, w, end - start + 1)


def _clamp_to_line_band(gray: np.ndarray, boxes: list, indices) -> None:
    """Cut a line's words to the densest band of ink rows summed across all its
    words: the white rows between a title and the subtitle under it separate
    them even where single word boxes (a comma) bridge the gap."""

    members = [boxes[i] for i in indices]
    if len(members) < 2:
        return
    top = min(b[1] for b in members)
    bottom = max(b[1] + b[3] for b in members)
    height = bottom - top
    if height < 3 * _MIN_ROWS:
        return
    rows = np.zeros(height, dtype=np.int64)
    for x, y, w, h in members:
        patch = gray[max(0, y) : y + h, max(0, x) : x + w]
        if patch.size:
            ink = (np.abs(patch - np.median(patch)) > LEVEL).sum(axis=1)
            rows[y - top : y - top + len(ink)] += ink
    empty = max(1, int(0.005 * sum(b[2] for b in members)))
    gap_limit = max(2, int(0.06 * height))
    runs: list[list[int]] = []
    gap = gap_limit + 1
    for r, count in enumerate(rows):
        if count > empty:
            if runs and gap <= gap_limit:
                runs[-1][1] = r
                runs[-1][2] += int(count)
            else:
                runs.append([r, r, int(count)])
            gap = 0
        else:
            gap += 1
    if len(runs) < 2:
        return
    start, end, ink = max(runs, key=lambda run: run[2])
    if ink < 0.6 * rows.sum():  # no dominant band: leave the boxes alone
        return
    low, high = top + start - 1, top + end + 2
    for i in indices:
        x, y, w, h = boxes[i]
        y0, y1 = max(y, low), min(y + h, high)
        if y1 - y0 >= _MIN_ROWS:
            boxes[i] = (x, y0, w, y1 - y0)


def tighten(words, lines, gray: np.ndarray):
    """Words with tight boxes, and lines whose boxes are their words' union."""

    boxes = [tight_box(gray, word.bbox) for word in words]
    for line in lines:
        _clamp_to_line_band(gray, boxes, line.word_indices)
    for line in lines:
        # A word still taller than its line's typical glyph band (a comma bridging
        # to the next line) is clamped to that band.
        members = [boxes[i] for i in line.word_indices]
        if len(members) < 3:
            continue
        top = float(np.median([b[1] for b in members]))
        bottom = float(np.median([b[1] + b[3] for b in members]))
        band = bottom - top
        low, high = top - 0.15 * band, bottom + 0.3 * band
        for i in line.word_indices:
            x, y, w, h = boxes[i]
            y0, y1 = max(y, int(low)), min(y + h, int(np.ceil(high)))
            if y1 - y0 < _MIN_ROWS or (y0, y1) == (y, y + h):
                continue
            patch = gray[max(0, y) : y + h, max(0, x) : x + w]
            rows = (np.abs(patch - np.median(patch)) > LEVEL).sum(axis=1)
            outside = rows[: y0 - y].sum() + rows[y1 - y :].sum()
            if outside < 0.15 * max(1, rows.sum()):  # a larger glyph (big numeral) keeps its box
                boxes[i] = (x, y0, w, y1 - y0)
    tightened = tuple(replace(word, bbox=box) for word, box in zip(words, boxes, strict=True))
    result = []
    for line in lines:
        boxes = [tightened[i].bbox for i in line.word_indices]
        if not boxes:
            result.append(line)
            continue
        if len(boxes) >= 3:
            # A stray token from another row (OCR joined it to this line) does not
            # stretch the line box over the neighbouring line.
            top = float(np.median([b[1] for b in boxes]))
            bottom = float(np.median([b[1] + b[3] for b in boxes]))
            inside = [b for b in boxes if top <= b[1] + b[3] / 2 <= bottom]
            boxes = inside or boxes
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[0] + b[2] for b in boxes), max(b[1] + b[3] for b in boxes)
        result.append(replace(line, bbox=(x0, y0, x1 - x0, y1 - y0)))
    return tightened, tuple(result)
