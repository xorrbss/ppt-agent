"""Validate one declared native table against its drawing and derive the V1 payload.

The drawing is authoritative: converted natively, the group's drawn children
are replaced by a table built from the payload, so the payload may only say
what the group already shows.

* The model JSON is ``{"rows": [[str]], "headerRows"?: int}``; the drawn grid is
  authoritative for sizes.
* Text: the multiset of the group's drawn texts (one per ``<text>``, lines
  joined with ``"\\n"``, whitespace-normalized NFC) equals the multiset of the
  JSON's non-empty cells. Nothing drawn is lost, nothing declared is invented.
* Anchor: the bounding box of the group's rect/line primitives (the outer
  frame), canvas px; every text lies inside it.
* Grid (required): the distinct rect edges and straight lines of the group
  must give exactly ``columns + 1`` x- and ``rows + 1`` y-boundaries
  (``native_table_grid_undetermined`` otherwise). Every drawn text must sit in
  the cell the JSON puts it in; the grid supplies ``columnWidths`` (px
  weights) and ``rowHeightsPt``; each cell gets its effective fill and its
  drawn borders (``native_paint.py``), alignment and text style.
"""
from __future__ import annotations

import math

from collections import Counter

from services.generation_core.drivers.svg.native_geometry import (
    Box, DrawnText, GroupPrimitives, NativeRejection, Shape, is_bold, normalize_cell,
)
from services.generation_core.drivers.svg.native_paint import cell_borders, effective_fill, group_strokes

TABLE_ELEMENTS = frozenset({"g", "rect", "line", "text", "tspan"})
_ALIGN = {"start": "left", "middle": "center", "end": "right"}
_EDGE_TOLERANCE = 1.5


def _model_rows(raw: dict) -> tuple[list[list[str]], int | None]:
    if not set(raw) <= {"rows", "headerRows"}:
        raise NativeRejection("native_table_json_invalid")
    rows = raw.get("rows")
    if (not isinstance(rows, list) or not rows or not all(isinstance(r, list) and r for r in rows)
            or not all(isinstance(cell, str) for r in rows for cell in r)):
        raise NativeRejection("native_table_json_invalid")
    if any(len(r) != len(rows[0]) for r in rows):
        raise NativeRejection("native_table_grid_not_rectangular")
    header = raw.get("headerRows")
    if header is not None and (type(header) is not int or not 0 <= header <= len(rows)):
        raise NativeRejection("native_table_json_invalid")
    return rows, header


def _check_text_multiset(cells: list[list[str]], texts: tuple[DrawnText, ...]) -> None:
    declared = Counter(cell for row in cells for cell in row if cell)
    drawn = Counter(text.text for text in texts)
    extra, missing = drawn - declared, declared - drawn
    if extra:
        raise NativeRejection("native_table_cell_drawn_twice" if any(key in declared for key in extra)
                              else "native_table_text_unaccounted")
    if missing:
        raise NativeRejection("native_table_cell_not_drawn")


def _boundaries(values: list[float]) -> list[float]:
    merged: list[float] = []
    for value in sorted(values):
        if not merged or value - merged[-1] > _EDGE_TOLERANCE:
            merged.append(value)
    return merged


def _grid(prims: GroupPrimitives, frame: Box, rows: int, columns: int):
    xs, ys = [frame.x0, frame.x1], [frame.y0, frame.y1]
    for shape in prims.shapes:
        box = shape.box
        if shape.tag == "rect":
            xs += [box.x0, box.x1]
            ys += [box.y0, box.y1]
        elif box.width <= 0.5:
            xs.append(box.x0)
        elif box.height <= 0.5:
            ys.append(box.y0)
        else:
            raise NativeRejection("native_table_grid_undetermined")  # a diagonal line
    xs, ys = _boundaries(xs), _boundaries(ys)
    if len(xs) != columns + 1 or len(ys) != rows + 1:
        raise NativeRejection("native_table_grid_undetermined")
    return xs, ys


def _interval(bounds: list[float], value: float) -> int | None:
    for index in range(len(bounds) - 1):
        if bounds[index] - 0.5 <= value <= bounds[index + 1] + 0.5:
            return index
    return None


def _place(texts, xs, ys, cells, spans=None) -> dict[tuple[int, int], DrawnText]:
    placed: dict[tuple[int, int], DrawnText] = {}
    for text in texts:
        x, y = text.points[0]
        probe_x = x + {"start": 1.0, "end": -1.0}.get(text.anchor, 0.0)
        column, row = _interval(xs, probe_x), _interval(ys, y - 0.35 * text.size)
        if column is not None and row is not None:
            for (span_row, start), width in (spans or {}).items():
                if span_row == row and start <= column < start + width:
                    column = start
                    break
        if column is None or row is None or (row, column) in placed:
            raise NativeRejection("native_table_cell_position_mismatch")
        if normalize_cell(cells[row][column]) != text.text:
            raise NativeRejection("native_table_cell_position_mismatch")
        placed[(row, column)] = text
    return placed


def _text_style(text: DrawnText | None, pt_per_px: float) -> dict:
    if text is None:
        return {}
    style: dict = {"bold": is_bold(text.weight)}
    if text.fill:
        style["textColor"] = text.fill
    family = text.family.split(",")[0].strip().strip("'\"")
    if family:
        style["fontFamily"] = family
    size = round(text.size * pt_per_px, 3)
    if math.isfinite(size) and size > 0:
        style["fontSizePt"] = size
    return style


def table_object(raw: dict, prims: GroupPrimitives, *, pt_per_px: float,
                 layers: list[Shape]) -> tuple[dict, dict]:
    """``(anchor, payload)`` for one declared table; raises :class:`NativeRejection`.

    ``layers`` are the slide's filled shapes under the table's texts
    (``native_paint.background_layers``).
    """
    if not prims.tags <= TABLE_ELEMENTS:
        raise NativeRejection("native_group_element_unsupported")
    cells, header = _model_rows(raw)
    cells = [[normalize_cell(cell) for cell in row] for row in cells]
    _check_text_multiset(cells, prims.texts)
    frames = [s.box for s in prims.shapes]
    if not frames:
        raise NativeRejection("native_table_frame_missing")
    frame = frames[0]
    for box in frames[1:]:
        frame = frame.union(box)
    if frame.width <= 0 or frame.height <= 0:
        raise NativeRejection("native_table_frame_missing")
    if not all(frame.contains(x, y) for text in prims.texts for x, y in text.points):
        raise NativeRejection("native_table_text_outside_frame")
    xs, ys = _grid(prims, frame, len(cells), len(cells[0]))
    spans = _horizontal_spans(prims, xs, ys, cells)
    placed = _place(prims.texts, xs, ys, cells, spans)
    strokes = group_strokes(prims.shapes)
    payload: dict = {"rows": [], "cellStyles": []}
    for r, row in enumerate(cells):
        payload_row, style_row = [], []
        for c, cell in enumerate(row):
            text, box = placed.get((r, c)), Box(xs[c], ys[r], xs[c + spans.get((r, c), 1)], ys[r + 1])
            fill = effective_fill(layers, (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2)
            payload_row.append({"text": cell, "fill": fill, **_text_style(text, pt_per_px)})
            borders = cell_borders(strokes, box, under=fill, pt_per_px=pt_per_px)
            style_row.append({**({"horizontalAlign": _ALIGN[text.anchor]} if text else {}),
                              **({"borders": borders} if borders else {})})
        payload["rows"].append(payload_row)
        payload["cellStyles"].append(style_row)
    payload["columnWidths"] = [round(xs[i + 1] - xs[i], 3) for i in range(len(xs) - 1)]
    payload["rowHeightsPt"] = [round((ys[i + 1] - ys[i]) * pt_per_px, 3) for i in range(len(ys) - 1)]
    if spans:
        payload["mergedCells"] = [{"row": r, "column": c, "rowSpan": 1, "columnSpan": width} for (r, c), width in sorted(spans.items())]
    if header:
        payload["headerRows"] = header
    return frame.anchor(), payload


def _horizontal_spans(prims, xs, ys, cells):
    """Attest a horizontal merge from visible borders and unchanged cells."""
    strokes = group_strokes(prims.shapes)
    tolerance = 0.5
    def covered(horizontal, at, start, end):
        intervals = sorted((line.start, line.end) for line in strokes
            if line.horizontal == horizontal and abs(line.at - at) <= tolerance
            and line.end >= start - tolerance and line.start <= end + tolerance)
        reached = start
        for lo, hi in intervals:
            if lo > reached + tolerance:
                return False
            reached = max(reached, hi)
        return reached >= end - tolerance
    spans = {}
    for r, row in enumerate(cells):
        separators = [c for c, x in enumerate(xs) if covered(False, x, ys[r], ys[r + 1])]
        for left, right in zip(separators, separators[1:]):
            if right - left <= 1 or not row[left] or any(row[left + 1:right]):
                continue
            if not all(covered(True, y, xs[left], xs[right]) for y in (ys[r], ys[r + 1])):
                continue
            matching = [text for text in prims.texts if text.text == row[left]
                and _interval(ys, text.points[0][1] - 0.35 * text.size) == r
                and xs[left] < text.points[0][0] < xs[right]]
            if len(matching) == 1:
                spans[(r, left)] = right - left
    return spans


def apply_drawn_table_style(projected: dict, item, root) -> None:
    """Recover SVG rich paint at export without extending the public V1 payload."""
    from services.generation_core.drivers.svg.native_geometry import collect_primitives, find_group

    found = find_group(root, item.id)
    if found is None:
        return
    prims = collect_primitives(*found, allowed=TABLE_ELEMENTS)
    anchor = item.anchor
    frame = Box(anchor.x, anchor.y, anchor.x + anchor.width, anchor.y + anchor.height)
    cells = [[cell["text"] for cell in row] for row in item.payload["rows"]]
    xs, ys = _grid(prims, frame, len(cells), len(cells[0]))
    spans = {(merge["row"], merge["column"]): merge["columnSpan"]
             for merge in item.payload.get("mergedCells", []) if merge["rowSpan"] == 1}
    placed = _place(prims.texts, xs, ys, cells, spans)
    for (row, column), text in placed.items():
        if text.uniform or not text.run_lines:
            continue
        paragraphs = []
        for line in text.run_lines:
            runs = []
            for source in line:
                run = {"text": source.text, "font_size": source.size,
                       "bold": is_bold(source.weight)}
                if source.fill:
                    run["color"] = source.fill
                family = source.family.split(",")[0].strip().strip("\"'")
                if family:
                    run["font_family"] = family
                runs.append(run)
            paragraphs.append({"runs": runs})
        cell = projected["rows"][row][column]
        cell.pop("text", None)
        cell["paragraphs"] = paragraphs
