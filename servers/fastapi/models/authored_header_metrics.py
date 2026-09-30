"""Numeric primitives the authored header contract is built on.

The contract's canvas lives here too. A contract written before it carried its
canvas was written for 1280x720, and it still reads that way: an absent size
means the 16:9 canvas. Serialisation leaves that size out for the same reason,
so a 16:9 contract persists, digests and prompts byte-for-byte as it did before
the field existed.
"""

from __future__ import annotations

import math
from typing import Any

from models.canvas_defaults import DEFAULT_CANVAS_HEIGHT_PX, DEFAULT_CANVAS_WIDTH_PX

SLIDE_WIDTH_PX = DEFAULT_CANVAS_WIDTH_PX
SLIDE_HEIGHT_PX = DEFAULT_CANVAS_HEIGHT_PX

_CANVAS_KEYS = ("canvas_width_px", "canvas_height_px")

#: Line box of a one-line label (eyebrow, subline, right-hand note) as a multiple
#: of its font size. The contract fixes no line-height for those rows, yet the
#: band derivation needs one number per row or two slides could stack the same
#: parts differently. It lives here because the brief, the bounds check and the
#: tests must all read the same number.
LABEL_LINE_RATIO = 1.4

#: Bounds of the derived band's floor. A mapped legacy band must fall inside them
#: to be usable, and so must a floor lowered onto the rhythm's own smallest band.
BAND_FLOOR_PX = 96
BAND_CEILING_PX = 280

#: How tall the derived band's ceiling itself may be. It sits above
#: ``BAND_CEILING_PX`` because the ceiling has to be able to hold everything the
#: rest of the contract permits (see ``required_band_px``); past this the header
#: is no longer a header, and the contract is refused rather than stretched.
BAND_MAX_CEILING_PX = 360

#: Defaults and bounds of the margin and the two widths that divide the
#: header's one row; the defaults are tuned for the 16:9 canvas.
DEFAULT_MARGIN_X_PX = 64
DEFAULT_TITLE_WIDTH_PX = 780
DEFAULT_SLOT_WIDTH_PX = 336
TITLE_MIN_WIDTH_PX = 320
SLOT_MIN_WIDTH_PX = 160
SLOT_MAX_WIDTH_PX = 560


def round_px(value: float) -> int:
    """Round half up, so the brief's arithmetic is reproducible off a calculator."""
    return int(math.floor(value + 0.5))


def label_row_px(font_size_px: int) -> int:
    """Height of one single-line label row at the given font size."""
    return round_px(font_size_px * LABEL_LINE_RATIO)


def omit_default_canvas(data: Any) -> Any:
    """Drop the canvas from a serialised contract when it is the 16:9 default."""
    if isinstance(data, dict) and tuple(data.get(key) for key in _CANVAS_KEYS) == (
        SLIDE_WIDTH_PX,
        SLIDE_HEIGHT_PX,
    ):
        for key in _CANVAS_KEYS:
            data.pop(key, None)
    return data


def split_header_row(
    canvas_width_px: int, *, margin_x_px: int, slot_px: int, title_px: int
) -> tuple[int, int]:
    """``(right slot width, title width)`` narrowed until both share one row.

    The title gives way first, down to its own floor; past that the slot does,
    because the slot is empty unless the deck earned it while the title is on
    every slide.
    """
    row = canvas_width_px - 2 * margin_x_px
    title = min(title_px, row - slot_px)
    if title >= TITLE_MIN_WIDTH_PX:
        return slot_px, title
    slot = max(SLOT_MIN_WIDTH_PX, row - TITLE_MIN_WIDTH_PX)
    return slot, row - slot


def default_row_split(canvas_width_px: int) -> tuple[int, int]:
    """The fallback header's row on a canvas; 1280 reproduces the 16:9 defaults.

    The slot keeps its share of the 16:9 row and the title takes the rest, up to
    its 16:9 width.
    """
    scaled = round_px(DEFAULT_SLOT_WIDTH_PX * canvas_width_px / SLIDE_WIDTH_PX)
    return split_header_row(
        canvas_width_px,
        margin_x_px=DEFAULT_MARGIN_X_PX,
        slot_px=min(max(scaled, SLOT_MIN_WIDTH_PX), SLOT_MAX_WIDTH_PX),
        title_px=DEFAULT_TITLE_WIDTH_PX,
    )


__all__ = [
    "BAND_CEILING_PX",
    "BAND_FLOOR_PX",
    "BAND_MAX_CEILING_PX",
    "DEFAULT_MARGIN_X_PX",
    "DEFAULT_SLOT_WIDTH_PX",
    "DEFAULT_TITLE_WIDTH_PX",
    "LABEL_LINE_RATIO",
    "SLIDE_HEIGHT_PX",
    "SLIDE_WIDTH_PX",
    "SLOT_MAX_WIDTH_PX",
    "SLOT_MIN_WIDTH_PX",
    "TITLE_MIN_WIDTH_PX",
    "default_row_split",
    "label_row_px",
    "omit_default_canvas",
    "round_px",
    "split_header_row",
]
