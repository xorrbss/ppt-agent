"""Editable table grid (rect/line/text primitives) for raster redesign pages.

The SVG contract admits no native-table payload, so a structured table is
drawn as one text object per cell over rule lines. The first row is treated
as the header row. Cell text is never truncated; if the grid cannot fit at
the 9.5 pt floor the caller receives ``None`` and must choose another layout.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from services.source_content.raster_redesign_text import (
    GLYPH_ASCENT,
    line_pitch,
    visual_width,
    wrap,
)
from services.source_content.raster_svg_layout import MIN_FONT_UNITS

_FLOOR_FONT = math.ceil(MIN_FONT_UNITS * 100) / 100
_PIXEL_TEXT_RATIO = 1.36  # crop height per unit of glyph height (raster_redesign_pixel_lines)
_MAX_UPSCALE = 1.25  # pixels standing in for text are never magnified beyond this
HEADER_FILL = "#435D80"
HEADER_TEXT = "#FFFFFF"
BODY_TEXT = "#081D49"
BAND_FILL = "#F4F8FD"
RULE = "#CBDFF4"
_DIGIT = re.compile(r"\d")


@dataclass(frozen=True)
class CellImage:
    """A pixel cell (raster_table_overlay): its crop region and source size in px."""

    row: int
    col: int
    region_id: str
    width_px: float
    height_px: float
    text_px: float | None = None  # median glyph height of its OCR tokens

    def scales(self, font: float) -> tuple[float, float]:
        """(text-matched scale, legibility minimum) in units per source px, as pixel lines."""
        estimate = self.height_px / _PIXEL_TEXT_RATIO
        glyph = min(max(self.text_px, self.height_px * 0.08), self.height_px) if self.text_px else estimate
        minimum = min(_FLOOR_FONT / glyph, 0.6)
        return max(minimum, min(font / glyph, _MAX_UPSCALE)), minimum


@dataclass(frozen=True)
class TableCell:
    lines: tuple[str, ...]
    numeric: bool
    image: tuple[str, int, int] | None = None  # pixel cell: (region id, width, height) in units


@dataclass(frozen=True)
class TableLayout:
    width: float
    height: float
    font: float
    pad_x: float
    pad_y: float
    column_widths: tuple[float, ...]
    row_heights: tuple[float, ...]
    cells: tuple[tuple[TableCell, ...], ...]


def normalize_rows(rows) -> list[list[str]]:
    table = [[" ".join(str(cell or "").split()) for cell in row] for row in rows]
    columns = max((len(row) for row in table), default=0)
    if not table or columns == 0:
        raise ValueError("raster_redesign_table_empty")
    return [row + [""] * (columns - len(row)) for row in table]


def _longest_token(value: str) -> float:
    return max((visual_width(token) for token in value.split(" ") if token), default=0.0)


def layout_table(rows, width: float, font: float, pixel_cells=()) -> TableLayout | None:
    """Fit a table into ``width`` at ``font``; return None when it cannot.

    A pixel cell (``CellImage``) holds its crop at the text-matched scale, never
    below its legibility minimum; its text is "" (raster_table_overlay)."""

    table = normalize_rows(rows)
    columns = len(table[0])
    pad_x, pad_y = font * 0.55, font * 0.42
    images = {(cell.row, cell.col): cell for cell in pixel_cells}
    minimum, desired = [], []
    for column in range(columns):
        values = [row[column] for row in table]
        token = max((_longest_token(value) for value in values), default=0.0)
        full = max((visual_width(value) for value in values), default=0.0)
        crops = [cell for (_, c), cell in images.items() if c == column]
        least = max((cell.width_px * cell.scales(font)[1] for cell in crops), default=0.0)
        wanted = max((cell.width_px * cell.scales(font)[0] for cell in crops), default=0.0)
        minimum.append(max(max(2.2, min(token, 7.0)) * font, least) + pad_x * 2)
        desired.append(max(max(2.2, full) * font, wanted) + pad_x * 2)
    if sum(minimum) > width + 0.01:
        return None
    if sum(desired) <= width:
        extra = width - sum(desired)
        widths = [value + extra * value / sum(desired) for value in desired]
    else:
        spare = width - sum(minimum)
        wants = [d - m for d, m in zip(desired, minimum, strict=True)]
        total_want = sum(wants) or 1.0
        widths = [m + spare * w / total_want for m, w in zip(minimum, wants, strict=True)]
    numeric_columns = [
        all(_DIGIT.search(value) and visual_width(value) <= 12 for value in body if value) and any(body)
        for body in ([row[column] for row in table[1:]] for column in range(columns))
    ]
    cells, heights = [], []
    for row_index, row in enumerate(table):
        wrapped_row = []
        for column, value in enumerate(row):
            inner = widths[column] - pad_x * 2
            lines = tuple(wrap(value, inner, font)) if value else ()
            image = _cell_image(images.get((row_index, column)), inner, font)
            if image is False:
                return None  # the crop cannot stay legible in this column
            wrapped_row.append(TableCell(lines, bool(row_index and numeric_columns[column]), image))
        line_count = max((len(cell.lines) for cell in wrapped_row), default=1) or 1
        tallest = max((cell.image[2] for cell in wrapped_row if cell.image), default=0)
        heights.append(max(font * GLYPH_ASCENT + (line_count - 1) * line_pitch(font, font) + font * 0.34,
                           tallest) + pad_y * 2)
        cells.append(tuple(wrapped_row))
    return TableLayout(
        width=width, height=sum(heights), font=font, pad_x=pad_x, pad_y=pad_y,
        column_widths=tuple(widths), row_heights=tuple(heights), cells=tuple(cells),
    )


def _cell_image(cell: CellImage | None, inner: float, font: float):
    """(region id, width, height) of a pixel cell's crop in ``inner`` width; False if illegible."""

    if cell is None:
        return None
    scale, least = cell.scales(font)
    scale = min(scale, inner / cell.width_px)
    if scale < least - 1e-9:
        return False
    return cell.region_id, max(1, math.floor(cell.width_px * scale)), max(1, math.floor(cell.height_px * scale))


def svg_rect(x, y, w, h, fill, extra=""):
    return f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" fill="{fill}"{extra}/>'


def svg_line(x1, y1, x2, y2, stroke):
    return f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" stroke="{stroke}" stroke-width="1"/>'


def _cell_text(layout, cell, element_id, left, top, width, header, family, escape):
    if cell.numeric:
        anchor, tx = "end", left + width - layout.pad_x
    else:
        anchor, tx = "start", left + layout.pad_x
    baseline = top + layout.pad_y + layout.font * GLYPH_ASCENT
    spans = "".join(
        f'<tspan x="{tx:.2f}" y="{baseline + index * line_pitch(layout.font, layout.font):.2f}">{escape(value)}</tspan>'
        for index, value in enumerate(cell.lines)
    )
    return (
        f'<text id="{element_id}" x="{tx:.2f}" y="{baseline:.2f}" '
        f'font-family="{family}" font-size="{layout.font:.2f}" '
        f'font-weight="{"700" if header else "400"}" text-anchor="{anchor}" '
        f'fill="{HEADER_TEXT if header else BODY_TEXT}">{spans}</text>'
    )


def render_table(layout: TableLayout, x: float, y: float, *, id_prefix: str, family: str, escape,
                 image_href=None) -> list[str]:
    """Emit rect/line/text primitives for a laid-out table.

    Pixel cells follow the group as ``<image>`` siblings (drawn above the table;
    a native PPTX table converts the group whole), ``image_href(region_id)``."""

    # The group id carries the grid size so the PPTX export can derive a
    # native table from this validated grid (raster_redesign_pptx).
    rows, columns = len(layout.cells), len(layout.column_widths)
    xml = [f'<g id="{id_prefix}-table-{rows}x{columns}">',
           svg_rect(x, y, layout.width, layout.height, "#FFFFFF", f' stroke="{RULE}" stroke-width="1"')]
    cursor, images = y, []
    for row_index, (row, height) in enumerate(zip(layout.cells, layout.row_heights, strict=True)):
        if row_index == 0:
            xml.append(svg_rect(x, cursor, layout.width, height, HEADER_FILL))
        elif row_index % 2 == 0:
            xml.append(svg_rect(x, cursor, layout.width, height, BAND_FILL))
        if row_index:
            xml.append(svg_line(x, cursor, x + layout.width, cursor, RULE))
        left = x
        for column_index, (cell, column_width) in enumerate(zip(row, layout.column_widths, strict=True)):
            if column_index:
                xml.append(svg_line(left, cursor, left, cursor + height, "#6F84A3" if row_index == 0 else RULE))
            if cell.lines:
                xml.append(_cell_text(
                    layout, cell, f"{id_prefix}-r{row_index}c{column_index}", left, cursor,
                    column_width, row_index == 0, family, escape,
                ))
            if cell.image:
                images.append(_cell_image_xml(layout, cell, f"{id_prefix}-cell-r{row_index}c{column_index}",
                                              left, cursor, column_width, image_href))
            left += column_width
        cursor += height
    xml.append("</g>")
    return xml + images


def _cell_image_xml(layout, cell, element_id, left, top, column_width, image_href) -> str:
    if image_href is None:
        raise ValueError("raster_table_pixel_cell_unplaced")
    region_id, width, height = cell.image
    x = left + column_width - layout.pad_x - width if cell.numeric else left + layout.pad_x
    return (
        f'<image id="{element_id}" x="{x:.2f}" y="{top + layout.pad_y:.2f}" width="{width}" height="{height}" '
        f'href="{image_href(region_id)}" preserveAspectRatio="xMidYMid meet"/>'
    )