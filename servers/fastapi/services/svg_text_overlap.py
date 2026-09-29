"""The svg ``text_overlap`` decision: glyph ink where measured, else line boxes.

Each owned text fragment is compared on its glyph ink box
(``text_ink_fragments``) when the probe could establish one, and on its
``Range.getClientRects()`` line box (``text_fragments``) otherwise.  The line
box is the font's full ascent+descent cell, so comparing it flags tight but
clean leading; the ink box only flags glyphs that actually meet.  The fallback
keeps the previous verdict wherever ink is unknown, so a missing or distrusted
measurement can never hide a collision.
"""
from __future__ import annotations

import math

from utils.slide_capture_text_ink import TEXT_INK_PROBE_VERSION

OVERLAP_THRESHOLD_PX = 0.5
INK = "ink"
LINE_BOX = "line_box"


def _ink_rect(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    try:
        numbers = [float(value[key]) for key in ("x", "y", "width", "height")]
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(number) for number in numbers) or numbers[2] <= 0 or numbers[3] <= 0:
        return None
    return dict(zip(("x", "y", "width", "height"), numbers, strict=True))


def comparison_fragments(item: dict) -> list[tuple[dict, str]]:
    """Return ``(rect, basis)`` per owned fragment of one measured text element."""
    line_boxes = item.get("text_fragments") or []
    ink = item.get("text_ink_fragments")
    if not isinstance(ink, list) or len(ink) != len(line_boxes):
        return [(rect, LINE_BOX) for rect in line_boxes]
    result = []
    for line_box, candidate in zip(line_boxes, ink, strict=True):
        rect = _ink_rect(candidate)
        result.append((rect, INK) if rect is not None else (line_box, LINE_BOX))
    return result


def _intersects(first: dict, second: dict) -> bool:
    dx = min(first["x"] + first["width"], second["x"] + second["width"]) - max(first["x"], second["x"])
    dy = min(first["y"] + first["height"], second["y"] + second["height"]) - max(first["y"], second["y"])
    return dx > OVERLAP_THRESHOLD_PX and dy > OVERLAP_THRESHOLD_PX


def text_overlaps(text: list[dict]) -> tuple[list[list], dict]:
    """Return colliding qid pairs and the ``overlap_basis`` evidence record.

    A direct parent/child pair is never compared (the parent owns only its
    direct text).  Per pair, ``basis`` names the geometry of the first
    colliding fragment pair: ``ink``, ``line_box`` or ``mixed``.
    """
    fragments = [comparison_fragments(item) for item in text]
    overlaps: list[list] = []
    pair_basis: list[dict] = []
    for i, a in enumerate(text):
        for j in range(i + 1, len(text)):
            b = text[j]
            if a.get("parent_qid") == b.get("qid") or b.get("parent_qid") == a.get("qid"):
                continue
            basis = next(
                (first_basis if first_basis == second_basis else "mixed"
                 for first, first_basis in fragments[i]
                 for second, second_basis in fragments[j]
                 if _intersects(first, second)),
                None,
            )
            if basis is not None:
                pair = [a.get("qid"), b.get("qid")]
                overlaps.append(pair)
                pair_basis.append({"pair": pair, "basis": basis})
    counts = {INK: 0, LINE_BOX: 0}
    for element in fragments:
        for _rect, basis in element:
            counts[basis] += 1
    return overlaps, {"version": TEXT_INK_PROBE_VERSION, "fragments": counts, "pairs": pair_basis}
