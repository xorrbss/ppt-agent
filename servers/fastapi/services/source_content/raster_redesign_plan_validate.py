"""Layout invariants checked on a candidate composition before any SVG is
emitted: no image intersects editable text or a table, and every text line
lies inside the card that contains its block origin.
"""

from __future__ import annotations

from services.source_content.raster_redesign_text import GLYPH_ASCENT, GLYPH_DESCENT, visual_width


def _text_boxes(placed):
    """Glyph boxes (Chrome metrics) of every line of one text placement."""
    for line in placed.lines:
        left = placed.x + line.indent
        yield (left, placed.y + line.baseline - GLYPH_ASCENT * line.size,
               left + visual_width(line.text) * line.size, placed.y + line.baseline + GLYPH_DESCENT * line.size)


def _overlap(a, b, tolerance=0.5) -> bool:
    return min(a[2], b[2]) - max(a[0], b[0]) > tolerance and min(a[3], b[3]) - max(a[1], b[1]) > tolerance


def layout_violation(placements) -> str | None:
    """Layout invariants checked before any SVG is emitted.

    * no image (with its 4-unit frame) intersects an editable text line or a table;
    * every text line lies inside the card that contains its block origin.
    """
    frame = 4.0
    images = [(p.x - (0.0 if p.item.inline else frame), p.y - (0.0 if p.item.inline else frame),
               p.x + p.w + (0.0 if p.item.inline else frame), p.y + p.h + (0.0 if p.item.inline else frame))
              for p in placements if p.kind == "image"]
    texts = [box for p in placements if p.kind == "text" for box in _text_boxes(p)]
    tables = [(p.x, p.y, p.x + p.w, p.y + p.h) for p in placements if p.kind == "table"]
    if any(_overlap(image, other) for image in images for other in (*texts, *tables)):
        return "image_over_text"
    cards = [(p.x, p.y, p.x + p.w, p.y + p.h) for p in placements if p.kind in {"card", "panel"}]
    for placed in placements:
        if placed.kind != "text":
            continue
        owner = next((c for c in cards if c[0] <= placed.x <= c[2] and c[1] <= placed.y <= c[3]), None)
        if owner is None:
            continue
        for box in _text_boxes(placed):
            if box[0] < owner[0] - 0.5 or box[2] > owner[2] + 0.5 or box[3] > owner[3] + 0.5:
                return "text_outside_card"
    return None
