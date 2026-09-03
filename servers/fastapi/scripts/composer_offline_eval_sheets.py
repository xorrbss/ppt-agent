"""Contact-sheet layout math and PNG composition for the Composer offline harness.

``sheet_layout`` is pure so it can be unit tested without Pillow or any slide
image: it decides how many sheets a deck needs, how large every sheet is and
where each slide thumbnail and its label land. ``write_contact_sheets`` then
paints those decisions with Pillow.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

#: The authored frame every slide PNG is captured at.
SLIDE_SIZE = (1280, 720)
DEFAULT_COLUMNS = 4
DEFAULT_SCALE = 0.6
DEFAULT_ROWS_PER_SHEET = 3
GUTTER_PX = 16
LABEL_HEIGHT_PX = 28


@dataclass(frozen=True)
class SheetCell:
    """One thumbnail slot: the slide it shows and its pixel box on the sheet."""

    slide_index: int
    x: int
    y: int
    width: int
    height: int
    label_y: int


@dataclass(frozen=True)
class SheetLayout:
    sheet_index: int
    width: int
    height: int
    cells: tuple[SheetCell, ...] = field(default_factory=tuple)


def thumbnail_size(scale: float = DEFAULT_SCALE, slide_size: tuple[int, int] = SLIDE_SIZE) -> tuple[int, int]:
    """Thumbnail box for one slide at ``scale`` of the authored frame."""

    if scale <= 0:
        raise ValueError("scale must be positive")
    width, height = slide_size
    return max(1, round(width * scale)), max(1, round(height * scale))


def sheet_layout(
    count: int,
    *,
    columns: int = DEFAULT_COLUMNS,
    scale: float = DEFAULT_SCALE,
    rows_per_sheet: int = DEFAULT_ROWS_PER_SHEET,
    slide_size: tuple[int, int] = SLIDE_SIZE,
    gutter: int = GUTTER_PX,
    label_height: int = LABEL_HEIGHT_PX,
) -> list[SheetLayout]:
    """Lay ``count`` slides onto sheets of ``columns`` x ``rows_per_sheet`` cells.

    Slides fill each sheet row-major in deck order. Every sheet is the same
    width; the last sheet is only as tall as the rows it actually uses so a
    two-slide remainder does not produce a mostly empty page.
    """

    if count < 0:
        raise ValueError("count must not be negative")
    if columns < 1 or rows_per_sheet < 1:
        raise ValueError("columns and rows_per_sheet must be at least 1")
    thumb_w, thumb_h = thumbnail_size(scale, slide_size)
    cell_h = thumb_h + label_height
    per_sheet = columns * rows_per_sheet
    width = gutter + columns * (thumb_w + gutter)
    sheets: list[SheetLayout] = []
    for sheet_index, start in enumerate(range(0, count, per_sheet)):
        indices = range(start, min(start + per_sheet, count))
        rows = (len(indices) + columns - 1) // columns
        cells: list[SheetCell] = []
        for offset, slide_index in enumerate(indices):
            row, column = divmod(offset, columns)
            x = gutter + column * (thumb_w + gutter)
            y = gutter + row * (cell_h + gutter)
            cells.append(SheetCell(slide_index, x, y, thumb_w, thumb_h, y + thumb_h))
        height = gutter + rows * (cell_h + gutter)
        sheets.append(SheetLayout(sheet_index, width, height, tuple(cells)))
    return sheets


def sheet_file_name(sheet_index: int, sheet_count: int) -> str:
    digits = max(2, len(str(sheet_count)))
    return f"sheet-{sheet_index + 1:0{digits}d}.png"


def write_contact_sheets(
    out_dir: Path,
    png_paths: Sequence[Path | None],
    labels: Sequence[str],
    *,
    columns: int = DEFAULT_COLUMNS,
    scale: float = DEFAULT_SCALE,
    rows_per_sheet: int = DEFAULT_ROWS_PER_SHEET,
) -> list[Path]:
    """Paint one ``sheet-NN.png`` per ``rows_per_sheet`` rows of thumbnails.

    A slide without a PNG (render failure) gets a hatched grey placeholder so
    its position in the deck stays visible. Returns the written sheet paths.
    """

    from PIL import Image, ImageDraw

    layouts = sheet_layout(len(png_paths), columns=columns, scale=scale, rows_per_sheet=rows_per_sheet)
    written: list[Path] = []
    for layout in layouts:
        sheet = Image.new("RGB", (layout.width, layout.height), "#F3F4F6")
        draw = ImageDraw.Draw(sheet)
        for cell in layout.cells:
            box = (cell.x, cell.y, cell.x + cell.width, cell.y + cell.height)
            source = png_paths[cell.slide_index]
            if source is not None and source.exists():
                with Image.open(source) as image:
                    thumb = image.convert("RGB").resize((cell.width, cell.height), Image.LANCZOS)
                sheet.paste(thumb, (cell.x, cell.y))
            else:
                draw.rectangle(box, fill="#D1D5DB")
                draw.line((box[0], box[1], box[2], box[3]), fill="#9CA3AF", width=3)
                draw.line((box[0], box[3], box[2], box[1]), fill="#9CA3AF", width=3)
            draw.rectangle(box, outline="#9CA3AF", width=1)
            label = labels[cell.slide_index] if cell.slide_index < len(labels) else str(cell.slide_index + 1)
            draw.text((cell.x + 4, cell.label_y + 6), label, fill="#111827")
        path = out_dir / sheet_file_name(layout.sheet_index, len(layouts))
        sheet.save(path, format="PNG")
        written.append(path)
    return written


__all__ = [
    "DEFAULT_COLUMNS",
    "DEFAULT_ROWS_PER_SHEET",
    "DEFAULT_SCALE",
    "SLIDE_SIZE",
    "SheetCell",
    "SheetLayout",
    "sheet_file_name",
    "sheet_layout",
    "thumbnail_size",
    "write_contact_sheets",
]
