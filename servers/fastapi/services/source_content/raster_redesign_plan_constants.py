"""Shared layout constants for raster redesign page composition."""

from __future__ import annotations

import math

from services.source_content.raster_svg_layout import MIN_FONT_UNITS

NAVY, COBALT, SLATE = "#081D49", "#0866FF", "#536785"
BODY_TEXT = "#1B2F52"
CARD_PAD, CARD_ACCENT = 14.0, 4.0
BAND_GAP, GRID_GAP_X, GRID_GAP_Y = 10.0, 14.0, 8.0
# Sizes are emitted with two decimals, so the floor step is rounded up.
FLOOR_FONT = math.ceil(MIN_FONT_UNITS * 100) / 100
TEXT_FONTS = (25.4, 22.6, 19.8, 16.9, 15.5, 14.1, FLOOR_FONT)
TABLE_FONTS = (19.8, 16.9, 15.5, 14.1, FLOOR_FONT)
IMAGE_ROLES = frozenset({"screenshot", "logo"})
# An unresolved table kept as pixels must stay at least as legible as the source.
TABLE_CROP_MIN_SCALE = 0.6
