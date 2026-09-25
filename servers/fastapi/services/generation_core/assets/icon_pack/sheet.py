"""Sprite-sheet prompts and the geometry that splits one back into icons.

One provider call draws up to nine icons on a regular grid; the sheet is then
cut into equal cells and each cell is trimmed to the alpha bounding box of
what it holds. Everything here is pure Pillow arithmetic - no provider, no
filesystem policy - so the verification script and the unit tests exercise
exactly the code the service runs.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from statistics import mean, pstdev
from typing import Sequence

from PIL import Image

#: The most concepts one sheet may carry; past this the cells get too small
#: for a 341px icon to survive the trim and the model starts merging cells.
MAX_CONCEPTS_PER_SHEET = 9
#: Cells whose alpha box comes within this many pixels of the cell edge are
#: treated as bleeding into a neighbour (or as a drawn grid line).
_EDGE_MARGIN_PX = 4
#: Alpha below this counts as background when locating an icon.
_ALPHA_FLOOR = 24
#: Trimmed icons are padded on a square canvas so every icon in a pack shares
#: the same optical size when the slide scales them by height.
_CELL_PADDING_RATIO = 0.08


@dataclass(frozen=True)
class SheetLayout:
    columns: int
    rows: int
    size: str  # provider size string, e.g. "1024x1024"

    @property
    def capacity(self) -> int:
        return self.columns * self.rows


def layout_for(count: int) -> SheetLayout:
    """Grid that fits ``count`` icons in square-ish cells of >=341px."""
    if count <= 0:
        raise ValueError("a sheet needs at least one concept")
    if count > MAX_CONCEPTS_PER_SHEET:
        raise ValueError("a sheet carries at most nine concepts")
    if count <= 3:
        return SheetLayout(columns=3, rows=1, size="1536x1024")
    if count <= 6:
        return SheetLayout(columns=3, rows=2, size="1536x1024")
    return SheetLayout(columns=3, rows=3, size="1024x1024")


def build_sheet_prompt(
    concepts: Sequence[str],
    layout: SheetLayout,
    *,
    style_prompt: str | None = None,
    color: str = "#1E5EFF",
) -> str:
    """Prompt for one sheet; concept names are quoted verbatim, never drawn."""
    rows: list[str] = []
    for row in range(layout.rows):
        chunk = concepts[row * layout.columns : (row + 1) * layout.columns]
        if not chunk:
            break
        cells = ", ".join(f"'{concept}'" for concept in chunk)
        rows.append(f"Row {row + 1} (left to right): {cells}.")
    style = (style_prompt or "").strip()
    style_clause = f" Visual style: {style}." if style else ""
    return (
        f"A sprite sheet of exactly {len(concepts)} flat pictogram icons arranged on "
        f"a regular {layout.columns}x{layout.rows} grid on a fully TRANSPARENT "
        "background. Every icon is centered in its own equal cell, the same "
        "visual size, the same line weight and the same style; large clear "
        "margins between icons; nothing touches a cell edge; no grid lines, no "
        "cell borders, no boxes or circles around icons, no drop shadows. "
        f"Single color {color} on transparency.{style_clause} "
        "Each icon symbolizes one concept from the list below; these names are "
        "for meaning only. ABSOLUTELY NO TEXT, letters, digits, labels or "
        "captions anywhere in the image. " + " ".join(rows)
    )


@dataclass(frozen=True)
class CellMetrics:
    index: int
    empty: bool
    bleeds: bool
    box: tuple[int, int, int, int] | None  # left, top, right, bottom in cell coords
    extent_px: int  # max(width, height) of the alpha box
    fill_ratio: float  # alpha-box area over cell area

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "empty": self.empty,
            "bleeds": self.bleeds,
            "box": list(self.box) if self.box else None,
            "extent_px": self.extent_px,
            "fill_ratio": round(self.fill_ratio, 4),
        }


@dataclass(frozen=True)
class SheetAnalysis:
    cells: tuple[CellMetrics, ...]
    extent_cv: float  # coefficient of variation of icon extents
    transparent_fraction: float
    has_alpha: bool

    @property
    def empty_cells(self) -> tuple[int, ...]:
        return tuple(c.index for c in self.cells if c.empty)

    @property
    def bleeding_cells(self) -> tuple[int, ...]:
        return tuple(c.index for c in self.cells if c.bleeds)

    def to_dict(self) -> dict:
        return {
            "cells": [c.to_dict() for c in self.cells],
            "extent_cv": round(self.extent_cv, 4),
            "transparent_fraction": round(self.transparent_fraction, 4),
            "has_alpha": self.has_alpha,
            "empty_cells": list(self.empty_cells),
            "bleeding_cells": list(self.bleeding_cells),
        }


def _alpha_box(cell: Image.Image) -> tuple[int, int, int, int] | None:
    alpha = cell.getchannel("A").point(lambda v: 255 if v >= _ALPHA_FLOOR else 0)
    return alpha.getbbox()


def _cell_metrics(index: int, cell: Image.Image) -> CellMetrics:
    box = _alpha_box(cell)
    if box is None:
        return CellMetrics(index, True, False, None, 0, 0.0)
    left, top, right, bottom = box
    width, height = cell.size
    bleeds = (
        left < _EDGE_MARGIN_PX
        or top < _EDGE_MARGIN_PX
        or right > width - _EDGE_MARGIN_PX
        or bottom > height - _EDGE_MARGIN_PX
    )
    extent = max(right - left, bottom - top)
    fill = ((right - left) * (bottom - top)) / float(width * height)
    return CellMetrics(index, False, bleeds, box, extent, fill)


def open_rgba(payload: bytes) -> Image.Image:
    with Image.open(io.BytesIO(payload)) as image:
        image.load()
        return image.convert("RGBA")


def has_alpha_channel(payload: bytes) -> bool:
    with Image.open(io.BytesIO(payload)) as image:
        return image.mode in ("RGBA", "LA") or "transparency" in image.info


def transparent_fraction(image: Image.Image) -> float:
    alpha = image.getchannel("A")
    histogram = alpha.histogram()
    total = image.size[0] * image.size[1]
    return sum(histogram[:_ALPHA_FLOOR]) / float(total) if total else 0.0


def split_cells(
    image: Image.Image, layout: SheetLayout, count: int
) -> list[Image.Image]:
    """Equal-grid cells in row-major order, only as many as were requested."""
    width, height = image.size
    cell_w = width // layout.columns
    cell_h = height // layout.rows
    cells: list[Image.Image] = []
    for row in range(layout.rows):
        for column in range(layout.columns):
            if len(cells) == count:
                return cells
            box = (
                column * cell_w,
                row * cell_h,
                (column + 1) * cell_w,
                (row + 1) * cell_h,
            )
            cells.append(image.crop(box))
    return cells


def analyze_sheet(payload: bytes, layout: SheetLayout, count: int) -> SheetAnalysis:
    image = open_rgba(payload)
    cells = split_cells(image, layout, count)
    metrics = tuple(_cell_metrics(i, cell) for i, cell in enumerate(cells))
    extents = [m.extent_px for m in metrics if not m.empty]
    average = mean(extents) if extents else 0.0
    cv = (pstdev(extents) / average) if len(extents) > 1 and average else 0.0
    return SheetAnalysis(
        cells=metrics,
        extent_cv=cv,
        transparent_fraction=transparent_fraction(image),
        has_alpha=has_alpha_channel(payload),
    )


def trim_cell(
    cell: Image.Image, metrics: CellMetrics, *, canvas: int | None = None
) -> Image.Image:
    """Crop a cell to its icon and center it on a square transparent canvas.

    ``canvas`` fixes the output edge so every icon of one sheet lands at the
    same pixel size; by default it is the icon's own padded extent.
    """
    if metrics.box is None:
        raise ValueError("cannot trim an empty cell")
    icon = cell.crop(metrics.box)
    edge = canvas or int(round(metrics.extent_px * (1 + 2 * _CELL_PADDING_RATIO)))
    edge = max(edge, max(icon.size))
    out = Image.new("RGBA", (edge, edge), (0, 0, 0, 0))
    out.paste(icon, ((edge - icon.size[0]) // 2, (edge - icon.size[1]) // 2), icon)
    return out


def encode_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


__all__ = [
    "MAX_CONCEPTS_PER_SHEET",
    "CellMetrics",
    "SheetAnalysis",
    "SheetLayout",
    "analyze_sheet",
    "build_sheet_prompt",
    "encode_png",
    "has_alpha_channel",
    "layout_for",
    "open_rgba",
    "split_cells",
    "transparent_fraction",
    "trim_cell",
]
