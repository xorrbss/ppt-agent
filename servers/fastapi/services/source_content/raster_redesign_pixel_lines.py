"""Pixel lines: source text lines kept as pixels, placed inline in the text flow.

Pieces that share one source line box (e.g. per-word crops) are laid out as
one line, left to right, at one scale -- never as separate tiles or cards.
"""

from __future__ import annotations

import math

from services.source_content.raster_redesign_dedupe import same_line
from services.source_content.raster_redesign_tree import Fixed, Placed
from services.source_content.raster_svg_layout import MIN_FONT_UNITS

FLOOR_FONT = math.ceil(MIN_FONT_UNITS * 100) / 100
PIXEL_LINE_TEXT_RATIO = 1.36  # crop height per unit of font size (a text line box with its padding)
WORD_GAP = 0.3  # em


def _scales(item, f):
    native = item.native_height or f
    estimate = native / PIXEL_LINE_TEXT_RATIO
    # Source px of the text itself; clamp OCR outliers (stray marks, merged lines).
    # A measured glyph may be much smaller than the crop (a multi-line paragraph).
    glyph = min(max(item.text_px, native * 0.08), native) if item.text_px else estimate
    # Legibility floor: the 9.5 pt glyph size, or at least as legible as the source (0.6x).
    minimum = min(FLOOR_FONT / glyph, 0.6)
    # Text as large as the surrounding font; on the densest (floor-font) layouts
    # the line may use its legibility minimum instead.
    scale = max(f, FLOOR_FONT) / glyph if f > FLOOR_FONT + 0.01 else minimum
    return native, scale, minimum


def pixel_line(items, x, w, f):
    """One line of pixel pieces (reading order), wrapping only between pieces.

    Each piece is scaled so its text matches the surrounding font size, never
    below its legibility minimum; a single piece wider than the column shrinks
    to fit or the composition is rejected (None).
    """
    items = items if isinstance(items, (list, tuple)) else [items]
    placements, cursor_x, cursor_y, row_height = [], x, 0.0, 0.0
    gap = WORD_GAP * f
    # One source line = one text size: every piece uses the same scale.
    shared = min(_scales(item, f)[1] for item in items) if len(items) > 1 else None
    for item in items:
        native, scale, minimum = _scales(item, f)
        scale = max(shared, minimum) if shared is not None else scale
        height = native * scale
        width = height / item.aspect
        if width > w:
            width, height = w, w * item.aspect
            if height < native * minimum - 0.01:
                return None  # cannot stay legible in this column
        if placements and cursor_x + width > x + w + 0.01:
            cursor_x, cursor_y, row_height = x, cursor_y + row_height + gap, 0.0
        placements.append(Placed("image", cursor_x, cursor_y, width, height, item))
        cursor_x += width + gap
        row_height = max(row_height, height)
    return Fixed(cursor_y + row_height, placements)


def group_same_line(items):
    """Consecutive inline pieces sharing a source line box become one tuple."""
    grouped: list = []
    for item in items:
        previous = grouped[-1] if grouped else None
        if (getattr(item, "inline", False) and isinstance(previous, tuple)
                and same_line(previous[-1].region.source_pixel_bbox, item.region.source_pixel_bbox)):
            grouped[-1] = (*previous, item)
        elif getattr(item, "inline", False):
            grouped.append((item,))
        else:
            grouped.append(item)
    return grouped
