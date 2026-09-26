"""NativeObjects entries for the validated tables of raster-redesign SVG pages.

Canonical redesign SVG carries only contract-admitted markup: a root id marker
and, per structured table, a ``<g id="rd-<tag>-table-<rows>x<cols>">`` grid of
rect/line/text primitives. This raster-owned module reads such a grid back into
a format-neutral ``NativeObject`` (authored V1 table payload + canvas anchor),
so raster tables reach PowerPoint through the same svg export scene as every
other deck's native objects (``drivers/svg/export_scene.py``). The grid itself
is untouched and stays the fallback if native conversion fails.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from services.generation_core.native_objects import NativeAnchor, NativeObject, NativeObjectsError
from services.source_content.raster_redesign_table import (
    BAND_FILL,
    BODY_TEXT,
    HEADER_FILL,
    HEADER_TEXT,
    RULE,
)
from services.svg_contract import RASTER_REDESIGN_ROOT_ID, SVG_NS

_TABLE_ID = re.compile(r"^(rd-[0-9a-f]{10})-table-(\d{1,3})x(\d{1,3})$")
_CELL_ID = re.compile(r"^rd-[0-9a-f]{10}-r(\d{1,3})c(\d{1,3})$")
# A pixel cell's crop, a sibling after its table group (raster_redesign_table.render_table).
_OVERLAY_ID = re.compile(r"^(rd-[0-9a-f]{10})-cell-r\d{1,3}c\d{1,3}$")
_FAMILY = "Noto Sans KR"
_DEFAULT_FONT_PX = 16.9
_PT_PER_PX = 0.75
# One 1 px rule on every edge of every cell, as drawn by the grid.
_RULE_BORDERS = {edge: {"color": RULE, "widthPt": _PT_PER_PX} for edge in ("top", "right", "bottom", "left")}


def is_redesign_svg(root: ET.Element) -> bool:
    return root.tag == f"{{{SVG_NS}}}svg" and root.get("id") == RASTER_REDESIGN_ROOT_ID


def _local(node) -> str:
    return node.tag.rsplit("}", 1)[-1]


def _boundaries(values: list[float], start: float, size: float) -> list[float]:
    inner = sorted({round(value, 2) for value in values if start + 0.5 < value < start + size - 0.5})
    return [start, *inner, start + size]


def is_table_group(group: ET.Element) -> bool:
    return _TABLE_ID.match(group.get("id") or "") is not None


def _row_fill(row: int) -> str:
    # Header, then the grid's band rule: even body rows are banded.
    return HEADER_FILL if row == 0 else BAND_FILL if row % 2 == 0 else "#FFFFFF"


def table_native_object(group: ET.Element) -> NativeObject | None:
    """Read one validated grid group back into a V1 table ``NativeObject``.

    Grids carry no transform, so the outer rect is the canvas anchor. ``None``
    when the group is not a well-formed grid (it then stays a grid).
    """

    match = _TABLE_ID.match(group.get("id") or "")
    if match is None:
        return None
    rows, columns = int(match[2]), int(match[3])
    outer = next((node for node in group if _local(node) == "rect"), None)
    if outer is None or rows < 1 or columns < 1:
        return None
    x, y = float(outer.get("x")), float(outer.get("y"))
    width, height = float(outer.get("width")), float(outer.get("height"))
    lines = [node for node in group if _local(node) == "line"]
    xs = _boundaries([float(n.get("x1")) for n in lines if n.get("x1") == n.get("x2")], x, width)
    ys = _boundaries([float(n.get("y1")) for n in lines if n.get("y1") == n.get("y2")], y, height)
    if len(xs) != columns + 1 or len(ys) != rows + 1:
        return None
    texts: list[list[str]] = [["" for _ in range(columns)] for _ in range(rows)]
    right = [[False] * columns for _ in range(rows)]
    font_size = None
    for node in group:
        cell = _CELL_ID.match(node.get("id") or "") if _local(node) == "text" else None
        if cell is None:
            continue
        row, column = int(cell[1]), int(cell[2])
        if row >= rows or column >= columns:
            return None
        spans = [span.text or "" for span in node if _local(span) == "tspan"] or [node.text or ""]
        texts[row][column] = "\n".join(spans)
        right[row][column] = node.get("text-anchor") == "end"
        font_size = float(node.get("font-size")) if font_size is None else font_size
    payload = {
        "rows": [[{"text": texts[r][c], "fill": _row_fill(r), "textColor": HEADER_TEXT if r == 0 else BODY_TEXT,
                   "bold": r == 0} for c in range(columns)] for r in range(rows)],
        "cellStyles": [[{"horizontalAlign": "right" if right[r][c] else "center" if r == 0 else "left",
                         "verticalAlign": "middle", "borders": _RULE_BORDERS} for c in range(columns)]
                       for r in range(rows)],
        "headerRows": 1,
        "fontFamily": _FAMILY,
        "fontSizePt": (font_size or _DEFAULT_FONT_PX) * _PT_PER_PX,
        "columnWidths": [round(b - a, 3) for a, b in zip(xs, xs[1:], strict=False)],
        "rowHeightsPt": [round(b - a, 3) * _PT_PER_PX for a, b in zip(ys, ys[1:], strict=False)],
    }
    return NativeObject.build(id=group.get("id"), kind="table", anchor=NativeAnchor(x, y, width, height),
                              payload=payload)


def table_cell_overlays(root: ET.Element) -> dict[str, int]:
    """Table group id -> number of pixel-cell images drawn over that table."""

    tables = {match[1]: group.get("id") for group in root.iter(f"{{{SVG_NS}}}g")
              if (match := _TABLE_ID.match(group.get("id") or ""))}
    counts: dict[str, int] = {}
    for image in root.iter(f"{{{SVG_NS}}}image"):
        match = _OVERLAY_ID.match(image.get("id") or "")
        if match and match[1] in tables:
            counts[tables[match[1]]] = counts.get(tables[match[1]], 0) + 1
    return counts


def redesign_native_objects(root: ET.Element) -> tuple[list[NativeObject], int]:
    """Every well-formed grid of a redesign page, plus how many stay grids."""

    objects, skipped = [], 0
    for group in root.iter(f"{{{SVG_NS}}}g"):
        if not is_table_group(group):
            continue
        try:
            item = table_native_object(group)
        except NativeObjectsError:
            item = None  # outside the V1 table shape (e.g. font size): stays a grid, counted
        if item is None:
            skipped += 1
        else:
            objects.append(item)
    return objects, skipped
