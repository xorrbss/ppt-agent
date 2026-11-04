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
import re

from collections import Counter

from services.generation_core.drivers.svg.native_geometry import (
    Box, DrawnText, GroupPrimitives, NativeRejection, Shape, is_bold, normalize_cell,
)
from services.generation_core.drivers.svg.native_paint import cell_borders, effective_fill, group_strokes

TABLE_ELEMENTS = frozenset({"g", "rect", "line", "path", "text", "tspan"})
_ALIGN = {"start": "left", "middle": "center", "end": "right"}
_EDGE_TOLERANCE = 1.5


def table_path_segments(node, attrs):
    """Read a stroke-only orthogonal grid without changing its source path."""
    def reject():
        return NativeRejection("native_table_path_unsupported")

    if (attrs.get("_table_path_unsupported_paint")
            or attrs.get("fill", "black") != "none" or not attrs.get("stroke")
            or attrs.get("stroke") == "none"
            or any(key in node.attrib for key in ("style", "class", "clip-path", "mask", "filter",
                                                  "marker-start", "marker-mid", "marker-end"))
            or node.get("stroke-dasharray", "none") != "none"
            or node.get("stroke-dashoffset", "0") != "0"
            or node.get("stroke-linecap", "butt") != "butt"):
        raise reject()
    from services.generation_core.drivers.svg.native_geometry import paint
    width = float(attrs.get("stroke-width", 1.0))
    alpha = float(attrs.get("stroke-opacity", 1.0)) * attrs.get("_opacity", 1.0)
    if (paint(attrs["stroke"]) is None or not math.isfinite(width) or width <= 0
            or not math.isfinite(alpha) or not 0 < alpha <= 1):
        raise reject()
    raw = node.get("d", "")
    pattern = re.compile(r"[A-Za-z]|[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
    matches = list(pattern.finditer(raw))
    tokens, end = [], 0
    for match in matches:
        gap = raw[end:match.start()].strip()
        token = match.group()
        if gap not in {"", ","} or (gap == "," and
                (not tokens or tokens[-1].isalpha() or token.isalpha())):
            raise reject()
        tokens.append(token)
        end = match.end()
    if raw[end:].strip() or not tokens:
        raise reject()
    segments, cursor, command, index = [], None, None, 0
    while index < len(tokens):
        if tokens[index].isalpha():
            command = tokens[index]
            index += 1
        if command not in {"M", "m", "L", "l", "H", "h", "V", "v"}:
            raise reject()
        count = 2 if command.upper() in {"M", "L"} else 1
        if index + count > len(tokens) or any(token.isalpha() for token in tokens[index:index + count]):
            raise reject()
        values = [float(token) for token in tokens[index:index + count]]
        if not all(math.isfinite(value) for value in values):
            raise reject()
        index += count
        if cursor is None and command.upper() != "M":
            raise reject()
        x, y = cursor or (0.0, 0.0)
        relative = command.islower()
        if command.upper() in {"M", "L"}:
            point = (values[0] + (x if relative else 0), values[1] + (y if relative else 0))
        elif command.upper() == "H":
            point = (values[0] + (x if relative else 0), y)
        else:
            point = (x, values[0] + (y if relative else 0))
        if not all(math.isfinite(value) for value in point):
            raise reject()
        if command.upper() == "M":
            cursor = point
            command = "l" if relative else "L"
            continue
        if point == cursor or (point[0] != x and point[1] != y):
            raise reject()
        segments.append((x, y, *point))
        cursor = point
    if not segments:
        raise reject()
    return segments


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


def _wrapped_cell_matches(declared: str, lines) -> bool:
    """A cell drawn over several lines: the lines must spell the declared text exactly, with
    at most one space dropped at each line break (a wrap between words drops it; a mid-word
    wrap, common in Korean, has none). A multi-paragraph cell's paragraph breaks must each
    fall on a drawn line break; a paragraph may itself wrap over several lines."""
    return len(lines) >= 2 and _drawn_line_separators(declared, lines) is not None


def _drawn_line_separators(declared: str, lines) -> list[str] | None:
    """What the declared text has between consecutive drawn lines: a newline (a hard
    paragraph break), a space (a wrap that dropped it) or nothing (a mid-word wrap);
    ``None`` when the lines do not spell the declared text exactly."""
    position, separators = 0, []
    for index, line in enumerate(lines):
        line = normalize_cell(line)
        if not line or not declared.startswith(line, position):
            return None
        position += len(line)
        if index < len(lines) - 1:
            separator = declared[position] if declared.startswith((chr(10), " "), position) else ""
            position += len(separator)
            separators.append(separator)
    return separators if position == len(declared) else None


#: A wrapped cell's lines sit at most this many font sizes apart; the next table row is further.
_FOLD_MAX_PITCH_EM = 1.8


def drawn_cell_text(node) -> str:
    """One ``<text>`` as the multiset check reads it: a tspan with its own ``y`` opens a line."""
    lines, line_y = [node.text or ""], node.get("y")
    for child in node:
        if child.get("y") is not None and child.get("y") != line_y:
            lines.append("")
            line_y = child.get("y")
        lines[-1] += "".join(child.itertext()) + (child.tail or "")
    return normalize_cell("\n".join(lines))


def fold_wrapped_cell_lines(group, raw: dict) -> int:
    """Join a declared cell the model drew as one ``<text>`` per wrapped line into one text.

    Measured on a template deck (2026-11-04, 12 of 20 table attempts on 6 of 10 table pages):
    a long cell such as ``"OS, drivers, CUDA"`` was drawn as two adjacent texts at the same x
    one line pitch apart (``"OS, drivers,"`` and ``"CUDA"``) while the JSON held the whole
    cell. The table rule asks for one text per cell with tspan lines, so the text multiset
    check (``native_table_text_unaccounted`` / ``_cell_drawn_twice``) dropped the table.

    Only a table that check would reject is changed (some declared cell is drawn by no single
    text). A run of adjacent, child-free sibling texts whose attributes are identical except
    ``y``, at a rising uniform pitch of at most ``_FOLD_MAX_PITCH_EM`` font sizes, whose lines
    spell such a cell (``_drawn_line_separators``: a wrap drops at most one space) becomes the
    first text with one ``<tspan x y>`` per further line: the same glyphs at the same
    positions. The JSON cell becomes the drawn lines joined by ``"\\n"``, the form the rule
    documents. Grid, position and every other check still run on the result. ``group`` and
    ``raw`` are changed in place; returns the number of cells folded.
    """
    rows = raw.get("rows")
    if (not isinstance(rows, list) or not all(isinstance(row, list) for row in rows)
            or not all(isinstance(cell, str) for row in rows for cell in row)):
        return 0
    namespace = group.tag[:group.tag.index("}") + 1] if group.tag.startswith("{") else ""
    text_tag = namespace + "text"
    drawn = Counter(drawn_cell_text(node) for node in group.iter(text_tag))
    unmatched = Counter(cell for row in rows for cell in (normalize_cell(value) for value in row) if cell) - drawn
    if not unmatched:
        return 0

    def line_metrics(node) -> tuple[float, float] | None:
        """``(y, font-size)`` of a child-free, non-empty text, else ``None``."""
        if node.tag != text_tag or len(node) or not (node.text or "").strip():
            return None
        try:
            values = float(node.get("y")), float(node.get("font-size"))
        except (TypeError, ValueError):
            return None
        return values if all(math.isfinite(value) for value in values) and values[1] > 0 else None

    def style(node) -> dict:
        return {name: value for name, value in node.attrib.items() if name != "y"}

    folded = 0
    for parent in [node for node in group.iter() if len(node)]:
        children = list(parent)
        index = 0
        while index < len(children):
            first = children[index]
            metrics = line_metrics(first)
            run, ys = [first], [metrics[0]] if metrics else []
            for node in children[index + 1:] if metrics else ():
                current = line_metrics(node)
                if current is None or style(node) != style(first):
                    break
                pitch = current[0] - ys[-1]
                if (not 0 < pitch <= _FOLD_MAX_PITCH_EM * metrics[1]
                        or len(ys) > 1 and abs(pitch - (ys[1] - ys[0])) > 0.5):
                    break
                run.append(node)
                ys.append(current[0])
            match, lines = None, []
            for end in range(len(run), 1, -1):
                lines = [normalize_cell(node.text or "") for node in run[:end]]
                match = next((cell for cell, count in unmatched.items()
                              if count > 0 and _drawn_line_separators(cell, lines) is not None), None)
                if match is not None:
                    break
            if match is None:
                index += 1
                continue
            for node in run[1:len(lines)]:
                tspan = first.makeelement(namespace + "tspan", {"x": first.get("x", "0"), "y": node.get("y")})
                tspan.text = node.text
                first.append(tspan)
                parent.remove(node)
            replaced = False
            for row in rows:
                for position, cell in enumerate(row):
                    if normalize_cell(cell) == match:
                        row[position], replaced = "\n".join(lines), True
                        break
                if replaced:
                    break
            unmatched[match] -= 1
            folded += 1
            children = list(parent)
            index += 1
    return folded


def _place(texts, xs, ys, cells, spans=None, blocks=None) -> dict[tuple[int, int], DrawnText]:
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
            # A cell merged over several rows keeps its text in the origin cell, but the
            # source may vertically centre the drawn text inside any covered row.
            for (origin_row, origin_column), (row_span, column_span) in (blocks or {}).items():
                if (origin_row <= row < origin_row + row_span
                        and origin_column <= column < origin_column + column_span):
                    row, column = origin_row, origin_column
                    break
        if column is None or row is None or (row, column) in placed:
            raise NativeRejection("native_table_cell_position_mismatch")
        declared = normalize_cell(cells[row][column])
        if declared != text.text and not _wrapped_cell_matches(declared, text.lines):
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
        for left, right in zip(separators, separators[1:], strict=False):
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
    blocks = {(merge["row"], merge["column"]): (merge["rowSpan"], merge["columnSpan"])
              for merge in item.payload.get("mergedCells", []) if merge["rowSpan"] > 1}
    placed = _place(prims.texts, xs, ys, cells, spans, blocks)
    for (row, column), text in placed.items():
        cell = projected["rows"][row][column]
        bottom_row = min(row + blocks.get((row, column), (1, 1))[0], len(ys) - 1)
        # A multi-paragraph cell keeps its own hard paragraph breaks: the drawn lines of
        # one declared paragraph stay one paragraph whose drawn wraps are soft line
        # breaks (DrawingML a:br), instead of each drawn line becoming a paragraph.
        declared = normalize_cell(cells[row][column])
        separators = starts = None
        if chr(10) in declared and declared != text.text:
            separators = (_drawn_line_separators(declared, text.lines)
                          if len(text.lines) == len(text.run_lines) else None)
            if separators is None:
                continue
            starts = [0] + [index + 1 for index, separator in enumerate(separators) if separator == chr(10)]
        geometry = (None if any(edge in cell.get("padding", {}) for edge in ("top", "bottom"))
                    else _paragraph_geometry(text, ys[row], ys[bottom_row], starts))
        if not text.run_lines or (text.uniform and geometry is None and separators is None):
            continue
        paragraphs = []
        for index, line in enumerate(text.run_lines):
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
            if separators is not None and index > 0 and separators[index - 1] != chr(10):
                # The drawn wrap becomes a soft break; it stands for the dropped space.
                if runs:
                    runs[0]["break_before"] = True
                paragraphs[-1]["runs"].extend(runs)
                continue
            paragraphs.append({"runs": runs, **(geometry[0][len(paragraphs)] if geometry else {})})
        cell.pop("text", None)
        cell["paragraphs"] = paragraphs
        if geometry is not None:
            # Use the same first-baseline/font box as shared table padding;
            # leave existing horizontal margins and the grid untouched.
            cell["padding"] = {**cell.get("padding", {}), **geometry[1]}
            cell["valign"] = "top"


def _paragraph_geometry(text: DrawnText, top: float, bottom: float, starts=None):
    """Optional canvas-unit spacing for source lines with one common font size.

    ``starts`` are the drawn line indices that open a hard paragraph (default: every
    line). A paragraph's own drawn lines share one baseline pitch, which becomes its
    exact line spacing; the baseline sits at 0.8 of a line box, as the font box the
    shared padding uses (1.25 x size, baseline at size). A paragraph whose drawn
    lines are not evenly spaced cannot be represented and is rejected.
    """
    if (len(text.lines) < 2 or len(text.lines) != len(text.points)
            or len(text.lines) != len(text.run_lines)
            or any(run.size != text.size or run.family != text.family
                   for line in text.run_lines for run in line)
            or any(abs(point[0] - text.points[0][0]) > 1e-6 for point in text.points)):
        return None
    line_box = text.size * 1.25
    starts = list(range(len(text.lines))) if starts is None else sorted(starts)
    groups = [range(start, end) for start, end in zip(starts, starts[1:] + [len(text.lines)], strict=False)]
    baselines = [point[1] for point in text.points]
    spacing = []
    for group in groups:
        pitches = [baselines[index] - baselines[index - 1] for index in group[1:]]
        if pitches and max(pitches) - min(pitches) > 0.5:
            raise NativeRejection("native_table_paragraph_pitch_nonuniform")
        spacing.append(sum(pitches) / len(pitches) if pitches else line_box)
    def ascent(value):  # baseline at 0.8 of the line box (exactly the font size for 1.25 x size)
        return text.size if value == line_box else value * 4 / 5

    def descent(value):
        return text.size * 0.25 if value == line_box else value / 5

    padding = {"top": baselines[0] - ascent(spacing[0]) - top,
               "bottom": bottom - baselines[-1] - descent(spacing[-1])}
    befores = [0.0] + [baselines[groups[k][0]] - baselines[groups[k - 1][-1]]
                       - (spacing[k] if spacing[k - 1] == spacing[k] else descent(spacing[k - 1]) + ascent(spacing[k]))
                       for k in range(1, len(groups))]
    if (not math.isfinite(line_box) or line_box <= 0
            or any(not math.isfinite(value) or value < line_box for value in spacing)
            or any(not math.isfinite(value) or value < 0 for value in befores)
            or any(not math.isfinite(value) or value < 0 for value in padding.values())):
        return None
    return ([{"line_spacing": spacing[k], "space_before": befores[k]} for k in range(len(groups))], padding)
