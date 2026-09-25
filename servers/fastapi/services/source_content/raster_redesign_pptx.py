"""PPTX conversion inputs for raster-redesign SVG pages.

Canonical redesign SVG carries only contract-admitted markup: a root id marker
and, per structured table, a ``<g id="rd-<tag>-table-<rows>x<cols>">`` grid of
rect/line/text primitives. Right before the vendored converter runs, this
module derives the converter's native-table marker (``data-pptx-native`` and
``data-pptx-json``) from that validated grid, so PowerPoint receives a real
editable table. Nothing outside marked redesign pages is touched; the grid
stays the fallback if native conversion fails.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

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
_FAMILY = "Noto Sans KR"


@dataclass
class RedesignPptxInputs:
    active: bool = False
    native_tables: int = 0
    skipped_tables: int = 0
    grid_sources: dict[Path, str] = field(default_factory=dict)

    def restore_grid(self) -> None:
        """Rewrite every prepared page back to its validated grid-only source."""
        for path, source in self.grid_sources.items():
            path.write_text(source, encoding="utf-8")

    def evidence(self, *, delivered_native: bool, fallback_reason: str | None = None) -> dict:
        return {
            "rasterRedesign": self.active,
            "nativeTablesRequested": self.native_tables,
            "nativeTablesDelivered": self.native_tables if delivered_native else 0,
            "tablesKeptAsGrid": self.skipped_tables + (0 if delivered_native else self.native_tables),
            "nativeTableFallbackReason": fallback_reason,
        }


def is_redesign_svg(root: ET.Element) -> bool:
    return root.tag == f"{{{SVG_NS}}}svg" and root.get("id") == RASTER_REDESIGN_ROOT_ID


def _local(node) -> str:
    return node.tag.rsplit("}", 1)[-1]


def _boundaries(values: list[float], start: float, size: float) -> list[float]:
    inner = sorted({round(value, 2) for value in values if start + 0.5 < value < start + size - 0.5})
    return [start, *inner, start + size]


def table_payload(group: ET.Element) -> dict | None:
    """Build the converter's native-table JSON from one validated grid group."""

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
    cells: list[list[dict]] = [[{"text": ""} for _ in range(columns)] for _ in range(rows)]
    font_size = None
    for node in group:
        cell = _CELL_ID.match(node.get("id") or "") if _local(node) == "text" else None
        if cell is None:
            continue
        row, column = int(cell[1]), int(cell[2])
        if row >= rows or column >= columns:
            return None
        spans = [span.text or "" for span in node if _local(span) == "tspan"] or [node.text or ""]
        entry: dict = {"paragraphs": spans} if len(spans) > 1 else {"text": spans[0]}
        if node.get("text-anchor") == "end":
            entry["align"] = "r"
        cells[row][column] = entry
        font_size = float(node.get("font-size")) if font_size is None else font_size
    return {
        # Explicit bounds: inferring them from fallback text extents overshoots.
        "x": x, "y": y, "width": width, "height": height,
        "rows": cells,
        "header_rows": 1,
        "strict_grid": True,
        "column_widths": [round(b - a, 3) for a, b in zip(xs, xs[1:], strict=False)],
        "row_heights": [round(b - a, 3) for a, b in zip(ys, ys[1:], strict=False)],
        "style": {
            "header_fill": HEADER_FILL, "header_text": HEADER_TEXT, "body_fill": "#FFFFFF",
            "body_text": BODY_TEXT, "band_fill": BAND_FILL, "band_row": True,
            "font_family": _FAMILY, "font_size": font_size or 16.9,
            "header_font_size": font_size or 16.9, "border_color": RULE, "border_width": 1,
        },
    }


def prepare_redesign_pptx_inputs(paths: list[Path]) -> RedesignPptxInputs:
    """Mark native tables on redesign pages in place; other pages are untouched."""

    result = RedesignPptxInputs()
    for path in paths:
        source = path.read_text(encoding="utf-8")
        root = ET.fromstring(source)
        if not is_redesign_svg(root):
            continue
        result.active = True
        marked = 0
        for group in root.iter(f"{{{SVG_NS}}}g"):
            if not _TABLE_ID.match(group.get("id") or ""):
                continue
            payload = table_payload(group)
            if payload is None:
                result.skipped_tables += 1
                continue
            group.set("data-pptx-native", "table")
            group.set("data-pptx-json", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            marked += 1
        if marked:
            result.grid_sources[path] = source
            result.native_tables += marked
            path.write_text(ET.tostring(root, encoding="unicode"), encoding="utf-8")
    return result
