"""One deck contact sheet, composed from the renders an attempt already holds.

Split out of ``services/authored_vision_advisory.py`` (H6) so the drawing has
no provider dependency and can be tested with a handful of PNGs.

The existing contact-sheet tooling (``scripts/authored_run_contact_sheet*``)
composes from files on disk and lays several runs out side by side for a human
to compare; this one composes from bytes in memory for a single model call, so
it is a separate, much smaller grid rather than a second entry point into a
benchmark script.
"""

from __future__ import annotations

import asyncio
import io
import math
from collections.abc import Sequence

#: Thumbnails per row, and the sheet's pixel envelope. Four columns of a 16:9
#: slide at 512 px is the widest grid that keeps a 1280 px render legible to a
#: vision model without exceeding a typical image budget.
SHEET_COLUMNS = 4
SHEET_MAX_WIDTH_PX = 2048
SHEET_MAX_HEIGHT_PX = 4096
SHEET_JPEG_QUALITY = 80
SHEET_LABEL_HEIGHT_PX = 18


def _font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - older Pillow
        return ImageFont.load_default()


def compose_contact_sheet_sync(
    pngs: Sequence[bytes],
    *,
    columns: int = SHEET_COLUMNS,
    max_width: int = SHEET_MAX_WIDTH_PX,
    max_height: int = SHEET_MAX_HEIGHT_PX,
    quality: int = SHEET_JPEG_QUALITY,
) -> bytes | None:
    """Draw one JPEG grid of every render, or ``None`` when there is nothing to draw.

    Each thumbnail carries its 1-based position in the top-left corner: that
    number is the only way the model can name a slide, so it is drawn on the
    sheet rather than described in the prompt.
    """

    from PIL import Image, ImageDraw

    frames = []
    for data in pngs:
        if not isinstance(data, (bytes, bytearray)) or not data:
            continue
        with Image.open(io.BytesIO(bytes(data))) as image:
            frames.append(image.convert("RGB"))
    if not frames:
        return None

    columns = max(1, min(columns, len(frames)))
    rows = math.ceil(len(frames) / columns)
    tallest = max(frame.height / max(1, frame.width) for frame in frames)
    thumb_width = max(1, max_width // columns)
    row_height = int(round(thumb_width * tallest)) + SHEET_LABEL_HEIGHT_PX
    if rows * row_height > max_height:
        # Solve for the thumbnail width the height cap allows rather than
        # scaling the row: the label band is a constant per row, so scaling the
        # whole row by the overshoot ratio never converges on a long deck.
        allowed_row = max_height / rows - SHEET_LABEL_HEIGHT_PX
        thumb_width = max(1, int(allowed_row / tallest)) if allowed_row > 0 else 1
        row_height = int(round(thumb_width * tallest)) + SHEET_LABEL_HEIGHT_PX

    sheet = Image.new("RGB", (thumb_width * columns, row_height * rows), "white")
    draw = ImageDraw.Draw(sheet)
    font = _font(14)
    for position, frame in enumerate(frames):
        left = (position % columns) * thumb_width
        top = (position // columns) * row_height
        draw.text((left + 4, top + 2), str(position + 1), fill="black", font=font)
        height = max(1, int(round(thumb_width * frame.height / max(1, frame.width))))
        sheet.paste(
            frame.resize((thumb_width, height)), (left, top + SHEET_LABEL_HEIGHT_PX)
        )
        frame.close()
    buffer = io.BytesIO()
    sheet.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


async def compose_contact_sheet(pngs: Sequence[bytes]) -> bytes | None:
    """Compose the deck contact sheet off the event loop."""

    return await asyncio.to_thread(compose_contact_sheet_sync, pngs)


__all__ = [
    "SHEET_COLUMNS",
    "SHEET_JPEG_QUALITY",
    "SHEET_LABEL_HEIGHT_PX",
    "SHEET_MAX_HEIGHT_PX",
    "SHEET_MAX_WIDTH_PX",
    "compose_contact_sheet",
    "compose_contact_sheet_sync",
]
