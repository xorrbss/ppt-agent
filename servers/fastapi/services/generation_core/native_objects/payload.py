"""Validation of the authored V1 native table/chart payload *shape*.

Python ports of ``parseAuthoredNativeTablePayload`` /
``parseAuthoredNativeChartPayload`` (``servers/nextjs/lib/authored-hybrid/
native-table.ts`` and ``native-chart.ts``). authored is frozen, so the shape is
reused here, not imported: a payload accepted by these functions is one the
authored parsers would accept too, with one documented difference: a table
cell's ``text`` may contain ``"\\n"`` as a paragraph break (the authored
``paragraphs`` array is itself joined with ``"\\n"`` to form ``text``); the
v2 ``paragraphs``/``sourceFitPolicy`` extension is not accepted.

Both functions return a canonical copy (colours upper-cased, absent optionals
omitted) or raise :class:`NativePayloadError` with a short reason code.
"""
from __future__ import annotations

import math
import re
from typing import Any

TABLE_SCHEMA = "authored-native-table/v1"
CHART_SCHEMA = "authored-native-chart/v1"
CHART_TYPES = ("bar", "column", "line", "area", "pie", "donut")

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_EDGES = ("top", "right", "bottom", "left")
_MAX_TABLE_ROWS, _MAX_TABLE_COLUMNS, _MAX_CELL_CHARS = 100, 20, 10_000
_MAX_CATEGORIES, _MAX_SERIES, _MAX_LABEL = 500, 50, 256
_TABLE_MAX_FONT_PT = 72


class NativePayloadError(ValueError):
    """A payload outside the V1 shape; ``str(error)`` is the reason code."""


def _fail(code: str):
    raise NativePayloadError(code)


def _only(value: dict, allowed: tuple[str, ...], code: str) -> None:
    if not isinstance(value, dict) or not set(value) <= set(allowed):
        _fail(code)


def _color(value: Any, code: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _HEX.match(value):
        _fail(code)
    return "#" + value[1:].upper()


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _font_family(value: Any, maximum: int, code: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        _fail(code)
    return value.strip()


def _font_size(value: Any, maximum: float, code: str) -> float | None:
    """Table sizes (max 72) are canonicalized to 0.001 pt like the authored parser."""
    if value is None:
        return None
    if not _number(value) or not 6 <= value <= maximum:
        _fail(code)
    return round(round(value / 0.001) * 0.001, 3) if maximum == _TABLE_MAX_FONT_PT else value


def _positive_vector(value: Any, count: int, code: str) -> list[float] | None:
    if value is None:
        return None
    if (not isinstance(value, list) or len(value) != count
            or not all(_number(item) and item > 0 for item in value)):
        _fail(code)
    return list(value)


def _cell(value: Any) -> dict:
    _only(value, ("text", "fill", "textColor", "bold", "fontFamily", "fontSizePt"), "table_cell_invalid")
    text = value.get("text")
    if not isinstance(text, str) or len(text) > _MAX_CELL_CHARS:
        _fail("table_cell_text_invalid")
    if value.get("bold") is not None and type(value["bold"]) is not bool:
        _fail("table_cell_bold_invalid")
    cell: dict = {"text": text}
    for key, parsed in (
        ("fill", _color(value.get("fill"), "table_cell_fill_invalid")),
        ("textColor", _color(value.get("textColor"), "table_cell_color_invalid")),
        ("bold", value.get("bold")),
        ("fontFamily", _font_family(value.get("fontFamily"), 128, "table_cell_font_invalid")),
        ("fontSizePt", _font_size(value.get("fontSizePt"), _TABLE_MAX_FONT_PT, "table_cell_font_size_invalid")),
    ):
        if parsed is not None:
            cell[key] = parsed
    return cell


def _cell_style(value: Any) -> dict:
    _only(value, ("fill", "borders", "horizontalAlign", "verticalAlign", "marginsPt"), "table_style_invalid")
    style: dict = {}
    if (fill := _color(value.get("fill"), "table_style_fill_invalid")) is not None:
        style["fill"] = fill
    if value.get("horizontalAlign") is not None:
        if value["horizontalAlign"] not in ("left", "center", "right", "justify"):
            _fail("table_style_align_invalid")
        style["horizontalAlign"] = value["horizontalAlign"]
    if value.get("verticalAlign") is not None:
        if value["verticalAlign"] not in ("top", "middle", "bottom"):
            _fail("table_style_valign_invalid")
        style["verticalAlign"] = value["verticalAlign"]
    if value.get("marginsPt") is not None:
        margins = value["marginsPt"]
        _only(margins, _EDGES, "table_style_margins_invalid")
        if set(margins) != set(_EDGES) or not all(_number(margins[e]) and 0 <= margins[e] <= 720 for e in _EDGES):
            _fail("table_style_margins_invalid")
        style["marginsPt"] = {edge: margins[edge] for edge in _EDGES}
    if value.get("borders") is not None:
        _only(value["borders"], _EDGES, "table_style_borders_invalid")
        borders = {}
        for edge, border in value["borders"].items():
            _only(border, ("color", "widthPt"), "table_style_border_invalid")
            color = _color(border.get("color"), "table_style_border_invalid")
            width = border.get("widthPt")
            if color is None or not _number(width) or not 0 < width <= 20:
                _fail("table_style_border_invalid")
            borders[edge] = {"color": color, "widthPt": width}
        style["borders"] = borders
    return style


def parse_table_payload(value: Any) -> dict:
    _only(value, ("schemaVersion", "rows", "columnWidths", "rowHeightsPt", "mergedCells", "cellStyles",
                  "headerRows", "fontFamily", "fontSizePt"), "table_payload_invalid")
    if value.get("schemaVersion", TABLE_SCHEMA) != TABLE_SCHEMA:
        _fail("table_schema_unsupported")
    rows = value.get("rows")
    if not isinstance(rows, list) or not 1 <= len(rows) <= _MAX_TABLE_ROWS:
        _fail("table_rows_invalid")
    width = len(rows[0]) if isinstance(rows[0], list) else 0
    if not 1 <= width <= _MAX_TABLE_COLUMNS or any(not isinstance(r, list) or len(r) != width for r in rows):
        _fail("table_grid_not_rectangular")
    parsed: dict = {"schemaVersion": TABLE_SCHEMA, "rows": [[_cell(cell) for cell in row] for row in rows]}
    if (widths := _positive_vector(value.get("columnWidths"), width, "table_column_widths_invalid")):
        parsed["columnWidths"] = widths
    if (heights := _positive_vector(value.get("rowHeightsPt"), len(rows), "table_row_heights_invalid")):
        parsed["rowHeightsPt"] = heights
    header_rows = value.get("headerRows")
    if header_rows is not None:
        if type(header_rows) is not int or not 0 <= header_rows <= len(rows):
            _fail("table_header_rows_invalid")
        if header_rows:
            parsed["headerRows"] = header_rows
    if (family := _font_family(value.get("fontFamily"), 128, "table_font_invalid")) is not None:
        parsed["fontFamily"] = family
    if (size := _font_size(value.get("fontSizePt"), _TABLE_MAX_FONT_PT, "table_font_size_invalid")) is not None:
        parsed["fontSizePt"] = size
    if value.get("cellStyles") is not None:
        styles = value["cellStyles"]
        if (not isinstance(styles, list) or len(styles) != len(rows)
                or any(not isinstance(row, list) or len(row) != width for row in styles)):
            _fail("table_cell_styles_invalid")
        parsed["cellStyles"] = [[_cell_style(style) for style in row] for row in styles]
    merged = _merged_cells(value.get("mergedCells"), parsed["rows"], width)
    if merged:
        parsed["mergedCells"] = merged
    return parsed


def _merged_cells(value: Any, rows: list[list[dict]], width: int) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        _fail("table_merged_cells_invalid")
    occupied: set[tuple[int, int]] = set()
    merged = []
    for item in value:
        _only(item, ("row", "column", "rowSpan", "columnSpan"), "table_merged_cells_invalid")
        row, column = item.get("row"), item.get("column")
        row_span, column_span = item.get("rowSpan"), item.get("columnSpan")
        if (any(type(v) is not int for v in (row, column, row_span, column_span))
                or row < 0 or column < 0 or row_span < 1 or column_span < 1
                or row + row_span > len(rows) or column + column_span > width
                or (row_span == 1 and column_span == 1)):
            _fail("table_merged_cells_invalid")
        for r in range(row, row + row_span):
            for c in range(column, column + column_span):
                if (r, c) in occupied or ((r, c) != (row, column) and rows[r][c]["text"] != ""):
                    _fail("table_merged_cells_invalid")
                occupied.add((r, c))
        merged.append({"row": row, "column": column, "rowSpan": row_span, "columnSpan": column_span})
    return merged


def _label(value: Any, code: str, maximum: int = _MAX_LABEL) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        _fail(code)
    return value


def _text_style(value: Any, *, title: bool, code: str) -> dict | None:
    if value is None:
        return None
    keys = ("fontFamily", "fontSizePt", "color") + (("title",) if title else ("visible", "position"))
    _only(value, keys, code)
    style = {}
    if (family := _font_family(value.get("fontFamily"), 200, code)) is not None:
        style["fontFamily"] = family
    if (size := _font_size(value.get("fontSizePt"), 96, code)) is not None:
        style["fontSizePt"] = size
    if (color := _color(value.get("color"), code)) is not None:
        style["color"] = color
    if title and value.get("title") is not None:
        style["title"] = _label(value["title"], code, 512)
    return style


def parse_chart_payload(value: Any) -> dict:
    _only(value, ("schemaVersion", "chartType", "categories", "series", "chartStyleVersion", "chartStyle",
                  "title", "fontFamily", "fontSizePt", "textColor", "palette", "legend", "categoryAxis",
                  "valueAxis"), "chart_payload_invalid")
    if value.get("schemaVersion", CHART_SCHEMA) != CHART_SCHEMA:
        _fail("chart_schema_unsupported")
    if value.get("chartType") not in CHART_TYPES:
        _fail("chart_type_unsupported")
    categories, series = value.get("categories"), value.get("series")
    if not isinstance(categories, list) or not 1 <= len(categories) <= _MAX_CATEGORIES:
        _fail("chart_categories_invalid")
    if not isinstance(series, list) or not 1 <= len(series) <= _MAX_SERIES:
        _fail("chart_series_invalid")
    parsed: dict = {"schemaVersion": CHART_SCHEMA, "chartType": value["chartType"],
                    "categories": [_label(item, "chart_categories_invalid") for item in categories],
                    "series": [_series(item, len(categories)) for item in series]}
    style_version, style = value.get("chartStyleVersion"), value.get("chartStyle")
    if style_version is not None or style is not None:
        if (style_version != "bounded-native-chart-style/v1" or type(style) is not int
                or not 1 <= style <= 48):
            _fail("chart_style_invalid")
        parsed.update(chartStyleVersion=style_version, chartStyle=style)
    if value.get("title") is not None:
        parsed["title"] = _label(value["title"], "chart_title_invalid", 512)
    if (family := _font_family(value.get("fontFamily"), 200, "chart_font_invalid")) is not None:
        parsed["fontFamily"] = family
    if (size := _font_size(value.get("fontSizePt"), 96, "chart_font_size_invalid")) is not None:
        parsed["fontSizePt"] = size
    if (color := _color(value.get("textColor"), "chart_text_color_invalid")) is not None:
        parsed["textColor"] = color
    if value.get("palette") is not None:
        if not isinstance(value["palette"], list):
            _fail("chart_palette_invalid")
        parsed["palette"] = [_color(item, "chart_palette_invalid") or _fail("chart_palette_invalid")
                             for item in value["palette"]]
    for key in ("categoryAxis", "valueAxis"):
        if (axis := _text_style(value.get(key), title=True, code=f"chart_{key}_invalid")) is not None:
            parsed[key] = axis
    if value.get("legend") is not None:
        legend = value["legend"]
        style = _text_style(legend, title=False, code="chart_legend_invalid")
        if type(legend.get("visible")) is not bool:
            _fail("chart_legend_invalid")
        if legend.get("position") is not None and legend["position"] not in ("bottom", "top", "right", "left"):
            _fail("chart_legend_invalid")
        parsed["legend"] = {"visible": legend["visible"], **({"position": legend["position"]}
                                                               if legend.get("position") else {}), **style}
    return parsed


def _series(value: Any, count: int) -> dict:
    _only(value, ("name", "values", "color", "pointColors"), "chart_series_invalid")
    values = value.get("values")
    if (not isinstance(values, list) or len(values) != count
            or not all(_number(item) for item in values)):
        _fail("chart_series_values_invalid")
    item = {"name": _label(value.get("name"), "chart_series_name_invalid"), "values": list(values)}
    if (color := _color(value.get("color"), "chart_series_color_invalid")) is not None:
        item["color"] = color
    if value.get("pointColors") is not None:
        points = value["pointColors"]
        if not isinstance(points, list) or len(points) != count:
            _fail("chart_point_colors_invalid")
        item["pointColors"] = [_color(p, "chart_point_colors_invalid") or _fail("chart_point_colors_invalid")
                               for p in points]
    return item
