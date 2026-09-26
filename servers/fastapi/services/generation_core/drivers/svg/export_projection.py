"""Project a ``NativeObject`` (authored V1 payload + canvas anchor) into the
vendored svg_to_pptx converter's native-object JSON.

The V1 shape is authored's (``native_objects/payload.py``); its point values
are CSS points of the slide canvas (authored serializes them with 0.75 pt per
canvas unit), so ``px = pt / 0.75`` in canvas units, whatever the canvas's
physical size. Every table-cell property is projected explicitly so no
converter default leaks in; the anchor becomes explicit converter bounds,
which the converter uses as absolute canvas coordinates (ancestor transforms
do not apply to explicit bounds).

Documented differences from authored's own OOXML serializer: the converter
has no transparent-cell mode for generated tables, so a cell without a fill
is filled white; ``justify`` alignment projects to left; row heights are
weights over the anchor height (authored uses them as absolute points; they
agree when they sum to the anchor height).
"""
from __future__ import annotations

from typing import Any

from services.generation_core.native_objects import NativeObject

_PT_PER_PX = 0.75
_AUTHORED_TEXT_COLOR = "#202020"
_AUTHORED_FONT_FAMILY = "Arial"
_AUTHORED_FONT_SIZE_PT = 10
_NO_FILL_PROJECTION = "#FFFFFF"
_AUTHORED_PALETTE = ("#2457D6", "#68A4FF", "#19A974", "#F2B134", "#D84A5B", "#7A5AF8")
_ALIGN = {"left": "l", "center": "ctr", "right": "r", "justify": "l"}
_EDGES = ("top", "right", "bottom", "left")
_CHART_TYPES = {"bar": "bar", "column": "column", "line": "line", "area": "area",
                "pie": "pie", "donut": "doughnut"}


def pt_to_px(value: float) -> float:
    """Geometry (row heights, borders, insets): 0.001 px precision."""
    return round(value / _PT_PER_PX, 3)


def font_pt_to_px(value: float) -> float:
    """Font sizes: V1 canonicalizes to 0.001 pt, finer than the 0.01 px grid,
    so rounding to 0.01 px recovers a size authored on that grid exactly."""
    return round(value / _PT_PER_PX, 2)


def converter_payload(item: NativeObject) -> dict:
    project = _table if item.kind == "table" else _chart
    payload = project(item.payload)
    anchor = item.anchor
    return {"x": anchor.x, "y": anchor.y, "width": anchor.width, "height": anchor.height, **payload}


def _table(table: dict) -> dict:
    header_rows = table.get("headerRows", 0)
    family = table.get("fontFamily", _AUTHORED_FONT_FAMILY)
    size_px = font_pt_to_px(table.get("fontSizePt", _AUTHORED_FONT_SIZE_PT))
    merges = {(m["row"], m["column"]): m for m in table.get("mergedCells", [])}
    covered = {(r, c) for m in merges.values()
               for r in range(m["row"], m["row"] + m["rowSpan"])
               for c in range(m["column"], m["column"] + m["columnSpan"])} - set(merges)
    styles = table.get("cellStyles")
    rows = []
    for r, row in enumerate(table["rows"]):
        cells = []
        for c, cell in enumerate(row):
            if (r, c) in covered:
                # The converter requires merge-covered cells to be bare blanks.
                cells.append({"text": ""})
                continue
            entry = _cell(cell, styles[r][c] if styles else {}, header=r < header_rows, family=family)
            if (r, c) in merges:
                entry["row_span"] = merges[(r, c)]["rowSpan"]
                entry["col_span"] = merges[(r, c)]["columnSpan"]
            cells.append(entry)
        rows.append(cells)
    projected: dict[str, Any] = {
        "rows": rows, "header_rows": header_rows, "strict_grid": True,
        "style": {"font_family": family, "font_size": size_px, "header_font_size": size_px},
    }
    if table.get("columnWidths"):
        projected["column_widths"] = list(table["columnWidths"])
    if table.get("rowHeightsPt"):
        projected["row_heights"] = [pt_to_px(value) for value in table["rowHeightsPt"]]
    return projected


def _cell(cell: dict, style: dict, *, header: bool, family: str) -> dict:
    lines = cell["text"].split("\n")
    entry: dict[str, Any] = {}
    cell_family = cell.get("fontFamily")
    if cell_family is not None and cell_family != family:
        # The converter's cell font face is table-wide; a per-cell face is a run property.
        entry["paragraphs"] = [{"runs": [{"text": line, "font_family": cell_family}]} for line in lines]
    elif len(lines) > 1:
        entry["paragraphs"] = lines
    else:
        entry["text"] = lines[0]
    entry.update({
        "fill": style.get("fill") or cell.get("fill") or _NO_FILL_PROJECTION,
        "color": cell.get("textColor", _AUTHORED_TEXT_COLOR),
        "bold": bool(cell.get("bold")) or header,
        "align": _ALIGN[style.get("horizontalAlign", "left")],
        "valign": style.get("verticalAlign", "middle"),
        "borders": _borders(style.get("borders", {})),
    })
    if cell.get("fontSizePt") is not None:
        entry["font_size"] = font_pt_to_px(cell["fontSizePt"])
    if style.get("marginsPt"):
        entry["padding"] = {edge: pt_to_px(style["marginsPt"][edge]) for edge in _EDGES}
    return entry


def _borders(borders: dict) -> dict:
    """Every edge explicit: an absent V1 border is none, never a table-style line."""
    return {
        edge: ({"style": "solid", "color": borders[edge]["color"], "width": pt_to_px(borders[edge]["widthPt"])}
               if edge in borders else {"style": "none"})
        for edge in _EDGES
    }


def _chart(chart: dict) -> dict:
    """Small, separate chart projection (category charts only; V1 has no other)."""
    chart_type = _CHART_TYPES[chart["chartType"]]
    palette = chart.get("palette") or _AUTHORED_PALETTE
    series = []
    for item in chart["series"]:
        entry: dict[str, Any] = {"name": item["name"], "values": list(item["values"])}
        if item.get("pointColors"):
            entry["point_colors"] = list(item["pointColors"])
        series.append(entry)
    # Authored's series colour rule: its own colour, else the palette cycled.
    style: dict[str, Any] = {"colors": [item.get("color") or palette[index % len(palette)]
                                        for index, item in enumerate(chart["series"])]}
    projected: dict[str, Any] = {"type": chart_type, "categories": list(chart["categories"]), "series": series,
                                 "style": style}
    if chart.get("title") is not None:
        projected["title"] = chart["title"]
    if chart.get("fontFamily") is not None:
        style["font_family"] = chart["fontFamily"]
    if chart.get("fontSizePt") is not None:
        style["font_size"] = font_pt_to_px(chart["fontSizePt"])
    if chart.get("textColor") is not None:
        style["text_color"] = chart["textColor"]
    legend = chart.get("legend")
    if legend is not None:
        style["show_legend"] = legend["visible"]
        if legend.get("position"):
            style["legend_position"] = legend["position"]
        if legend.get("fontSizePt") is not None:
            style["legend_font_size"] = font_pt_to_px(legend["fontSizePt"])
    if chart_type not in {"pie", "doughnut"}:
        titles = {axis: chart[key]["title"] for axis, key in (("category", "categoryAxis"), ("value", "valueAxis"))
                  if chart.get(key, {}).get("title") is not None}
        if titles:
            projected["axis_titles"] = titles
    return projected


__all__ = ["converter_payload", "font_pt_to_px", "pt_to_px"]
