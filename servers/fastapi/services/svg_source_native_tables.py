"""Native tables from verified source cells and the server's actual painted grid."""
from __future__ import annotations

import hashlib
import json
import math
from xml.etree import ElementTree as ET

from services.generation_core.drivers.svg.native_declarations import TABLE_ATTRIBUTE
from services.generation_core.drivers.svg.native_geometry import Box, collect_primitives, normalize_cell
from services.generation_core.drivers.svg.native_paint import background_layers, cell_borders, effective_fill, group_strokes
from services.generation_core.drivers.svg.native_table import TABLE_ELEMENTS, _text_style
from services.generation_core.native_objects import NativeObject, NativeObjects

_NS = "{http://www.w3.org/2000/svg}"


def source_table_group_id(block_id: str) -> str:
    return "source-native-table-" + hashlib.sha256(block_id.encode()).hexdigest()[:16]


def _grid_cell_box(xs, ys, row, column, row_span, column_span):
    return (xs[column], ys[row], xs[column + column_span] - xs[column],
            ys[row + row_span] - ys[row])


class _GridMismatch(ValueError):
    def __init__(self, detail):
        super().__init__("svg_source_native_table_grid_mismatch")
        self.detail = detail


def _authored_grid(rows, cells, rects, frame=None):
    """Prove shared authored boundaries using each immutable source cell span."""
    try:
        boxes = [tuple(float(node.get(key)) for key in ("x", "y", "width", "height")) for node in rects]
    except (ValueError, TypeError) as error:
        raise _GridMismatch(dict(problem="invalid_rectangle_geometry")) from error
    if not boxes or any(not all(math.isfinite(value) for value in box) or min(box[2:]) <= 0 for box in boxes):
        raise _GridMismatch(dict(problem="invalid_rectangle_geometry"))
    if frame is None:
        x, y = min(box[0] for box in boxes), min(box[1] for box in boxes)
        frame = dict(x=x, y=y, width=max(a+c for a,b,c,d in boxes)-x,
                     height=max(b+d for a,b,c,d in boxes)-y)
    if (any(not math.isfinite(value) for value in frame.values())
            or min(frame["width"], frame["height"]) <= 0):
        raise _GridMismatch(dict(problem="invalid_frame"))
    detail = dict(frame=frame,
        formula="x=xs[column]; y=ys[row]; width=xs[column+columnSpan]-xs[column]; height=ys[row+rowSpan]-ys[row]")
    if len(rects) != len(cells):
        raise _GridMismatch({**detail, "problem": "rectangle_count"})
    xs, ys = [None] * (len(rows[0]) + 1), [None] * (len(rows) + 1)
    xs[0], xs[-1] = frame["x"], frame["x"] + frame["width"]
    ys[0], ys[-1] = frame["y"], frame["y"] + frame["height"]
    for (row, column, rs, cs, _cell), box in zip(cells, boxes, strict=True):
        x, y, width, height = box
        for axis, bounds, index, value in (("x", xs, column, x), ("x", xs, column + cs, x + width),
                                          ("y", ys, row, y), ("y", ys, row + rs, y + height)):
            if bounds[index] is not None and abs(value - bounds[index]) > .000001:
                raise _GridMismatch({**detail, "problem": "inconsistent_grid_boundary",
                    "axis": axis, "boundary_index": index, "expected_edge": bounds[index], "actual_edge": value,
                    "first_mismatch": dict(row=row, column=column, row_span=rs, column_span=cs,
                        actual_bbox=dict(zip(("x", "y", "width", "height"), box, strict=True)))})
            if bounds[index] is None:
                bounds[index] = value
    for axis, bounds in (("x", xs), ("y", ys)):
        missing = [index for index, value in enumerate(bounds) if value is None]
        if missing:
            raise _GridMismatch({**detail, "problem": "unproven_grid_boundary", "axis": axis,
                                 "missing_boundary_indices": missing})
        if any(right <= left for left, right in zip(bounds, bounds[1:], strict=False)):
            raise _GridMismatch({**detail, "problem": "non_increasing_grid_boundary", "axis": axis})
    for (row, column, rs, cs, _cell), actual in zip(cells, boxes, strict=True):
        expected = _grid_cell_box(xs, ys, row, column, rs, cs)
        if any(abs(value - target) > .000001 for value, target in zip(actual, expected, strict=True)):
            raise _GridMismatch({**detail, "problem": "inconsistent_cell_rectangle",
                "first_mismatch": dict(row=row, column=column, row_span=rs, column_span=cs,
                    actual_bbox=dict(zip(("x", "y", "width", "height"), actual, strict=True)),
                    expected_bbox=dict(zip(("x", "y", "width", "height"), expected, strict=True)))})
    return xs, ys


def source_table_grid_correction(block, group):
    """Describe the first failed native grid cell without changing its drawing."""
    from services.svg_source_layout import _cells
    rows, cells = _cells(block)
    rects = [node for node in group if node.tag == _NS + "rect"]
    detail = dict(source_id=block.source_block_id, element_id=group.get("id"),
        rows=len(rows), columns=len(rows[0]), expected_rect_count=len(cells), actual_rect_count=len(rects))
    try:
        _authored_grid(rows, cells, rects)
    except _GridMismatch as error:
        return {**detail, **error.detail}
    return None


def _evidence(root, receipt, page):
    from services.svg_source_layout import _cells
    records = receipt.get("native_tables")
    if records is None:
        return []  # Historical receipts remain valid without this annotation.
    tables = {block.source_block_id: block for block in page.blocks if block.kind == "table"}
    if not isinstance(records, list) or len(records) != len(tables):
        raise ValueError("svg_source_native_table_coverage_mismatch")
    seen, evidence = set(), []
    for record in records:
        if not isinstance(record, dict) or set(record) != {"block_id", "element_id", "frame"}:
            raise ValueError("svg_source_native_table_attestation_invalid")
        block_id, group_id, frame = record["block_id"], record["element_id"], record["frame"]
        if block_id not in tables or block_id in seen or group_id != source_table_group_id(block_id):
            raise ValueError("svg_source_native_table_attestation_invalid")
        seen.add(block_id)
        if not isinstance(frame, dict) or set(frame) != {"x", "y", "width", "height"} or any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            for value in frame.values()) or min(frame["width"], frame["height"]) <= 0:
            raise ValueError("svg_source_native_table_frame_invalid")
        canvas = receipt.get("canvas", {})
        if (frame["x"] < 0 or frame["y"] < 0 or
                frame["x"] + frame["width"] > canvas.get("width", 0) + .000001 or
                frame["y"] + frame["height"] > canvas.get("height", 0) + .000001):
            raise ValueError("svg_source_native_table_frame_invalid")
        found = [node for node in root if node.get("id") == group_id]
        if len(found) != 1 or found[0].tag != _NS + "g" or found[0].attrib != {"id": group_id}:
            raise ValueError("svg_source_native_table_group_invalid")
        group = found[0]
        if group.text or group.tail or any(node.tag not in {_NS + "rect", _NS + "text"} for node in group):
            raise ValueError("svg_source_native_table_group_invalid")
        owned = [binding for binding in receipt["bindings"]
                 if binding.get("kind") == "table_cell" and binding.get("block_id") == block_id]
        expected_ids = {binding["element_id"] for binding in owned}
        actual_ids = {node.get("id") for node in group if node.get("id")}
        if (actual_ids != expected_ids or len(actual_ids) != sum(bool(node.get("id")) for node in group) or
                any(node.tag != _NS + "text" for node in group if node.get("id"))):
            raise ValueError("svg_source_native_table_membership_mismatch")
        rows, cells = _cells(tables[block_id])
        rects = [node for node in group if node.tag == _NS + "rect"]
        xs, ys = _authored_grid(rows, cells, rects, frame)
        for (row, column, rs, cs, cell), _rect in zip(cells, rects, strict=True):
            expected = _grid_cell_box(xs, ys, row, column, rs, cs)
            cell_id = cell.get("sourceCellId", f"{block_id}:row:{row}:column:{column}")
            bindings = [binding for binding in owned if binding["source_id"] == cell_id]
            node_ids = {binding["element_id"] for binding in bindings}
            for node in group:
                if node.get("id") in node_ids:
                    x, y = float(node.get("x")), float(node.get("y"))
                    if not (expected[0] <= x <= expected[0] + expected[2] and
                            expected[1] <= y <= expected[1] + expected[3]):
                        raise ValueError("svg_source_native_table_text_outside_cell")
        evidence.append((record, group, rows, cells, rects, owned, xs, ys))
    return evidence


def verify_source_table_groups(root, receipt, page) -> set[str]:
    return {record["element_id"] for record, *_ in _evidence(root, receipt, page)}


def unwrap_source_table_groups(svg: str, receipt: dict) -> str:
    """Remove only exact attested, paint-free wrappers for parent-geometry proof."""
    root = ET.fromstring(svg)
    ids = {record["element_id"] for record in receipt.get("native_tables", [])}
    for group in list(root):
        if group.get("id") not in ids:
            continue
        if group.attrib != {"id": group.get("id")} or group.text or group.tail:
            raise ValueError("svg_source_native_table_group_invalid")
        index = list(root).index(group)
        root.remove(group)
        for child in group:
            root.insert(index, child)
            index += 1
    return ET.tostring(root, encoding="unicode")


def _mixed_cell_borders(strokes, cell, *, under, pt_per_px):
    """Project only complete, single-style edges proven from actual paint order."""
    from services.generation_core.drivers.svg.native_paint import Stroke
    # The existing cell_borders matcher uses a 1.5 px edge tolerance; opposite
    # edges of a smaller cell are not independently representable by it.
    if cell.width <= 1.5 or cell.height <= 1.5:
        raise ValueError("svg_source_native_table_border_style_mismatch")
    edges = ((True, cell.y0, cell.x0, cell.x1), (False, cell.x1, cell.y0, cell.y1),
             (True, cell.y1, cell.x0, cell.x1), (False, cell.x0, cell.y0, cell.y1))
    projected = []
    epsilon = .000001
    for horizontal, at, start, end in edges:
        candidates = [stroke for stroke in strokes if stroke.horizontal == horizontal
                      and abs(stroke.at - at) <= epsilon
                      and min(stroke.end, end) - max(stroke.start, start) > epsilon]
        if not candidates:
            continue
        bounds = sorted({start, end, *(max(start, stroke.start) for stroke in candidates),
                         *(min(end, stroke.end) for stroke in candidates)})
        styles = set()
        for left, right in zip(bounds, bounds[1:], strict=False):
            if right - left <= epsilon:
                continue
            midpoint = (left + right) / 2
            covering = [stroke for stroke in candidates if stroke.start <= midpoint <= stroke.end]
            if not covering:
                raise ValueError("svg_source_native_table_border_style_mismatch")
            top = covering[-1]
            # A single native line cannot reproduce exposed wider under-strokes
            # or translucent paint over another stroke; fail closed for those.
            if any(stroke.width > top.width + epsilon for stroke in covering[:-1]) or (
                    top.alpha < 1 and len(covering) > 1):
                raise ValueError("svg_source_native_table_border_style_mismatch")
            styles.add((top.color, top.alpha, top.width))
        if len(styles) != 1:
            raise ValueError("svg_source_native_table_border_style_mismatch")
        color, alpha, width = styles.pop()
        projected.append(Stroke(horizontal, at, start, end, color, alpha, width))
    return cell_borders(projected, cell, under=under, pt_per_px=pt_per_px)


def source_table_native_objects(svg, receipt, manifest, page_index, *, pt_per_px):
    """Bind immutable table semantics to verified actual source grid/paint/font."""
    from services.svg_source_layout import source_pages, verify_source_receipt
    pages = source_pages(manifest, chart_contract=receipt.get("source_chart_contract"))
    verify_source_receipt(svg, receipt, manifest, page_index, _validated_pages=pages)
    root = ET.fromstring(svg)
    evidence = _evidence(root, receipt, pages[page_index])
    objects = []
    for record, group, rows, cells, _rects, owned, xs, ys in evidence:
        primitives = collect_primitives(group, (0., 0.), {}, TABLE_ELEMENTS)
        text_nodes = [node for node in group if node.tag == _NS + "text" and normalize_cell("".join(node.itertext()))]
        if len(text_nodes) != len(primitives.texts):
            raise ValueError("svg_source_native_table_text_invalid")
        drawn = {node.get("id"): text for node, text in zip(text_nodes, primitives.texts, strict=True)}
        frame = record["frame"]
        uniform_strokes = len({(shape.stroke, shape.stroke_width, shape.stroke_alpha)
                               for shape in primitives.shapes}) == 1
        # A later filled rect can hide half an earlier neighbour's stroke.
        # Require its own opaque positive stroke before using final-edge paint.
        if not uniform_strokes and any(shape.stroke is None or shape.stroke_width <= 0
                                       or shape.stroke_alpha != 1 for shape in primitives.shapes):
            raise ValueError("svg_source_native_table_border_style_mismatch")
        strokes = group_strokes(primitives.shapes) if not uniform_strokes else None
        layers = background_layers(root, group)
        payload_rows = [[{"text": cell["text"]} for cell in row] for row in rows]
        cell_styles = [[{} for _ in row] for row in rows]
        merged = []
        for (row, column, rs, cs, cell), shape in zip(cells, primitives.shapes, strict=True):
            cell_id = cell.get("sourceCellId", f"{record['block_id']}:row:{row}:column:{column}")
            lines = [drawn[binding["element_id"]] for binding in owned
                     if binding["source_id"] == cell_id and binding["element_id"] in drawn]
            if lines and any((line.size, line.family, line.fill, line.weight, line.anchor) !=
                             (lines[0].size, lines[0].family, lines[0].fill, lines[0].weight, lines[0].anchor)
                             for line in lines):
                raise ValueError("svg_source_native_table_cell_font_mismatch")
            box = Box(xs[column], ys[row], xs[column + cs], ys[row + rs])
            fill = effective_fill(layers, (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2)
            payload_rows[row][column].update(fill=fill, **_text_style(lines[0] if lines else None, pt_per_px))
            borders = (cell_borders(group_strokes((shape,)), box, under=fill, pt_per_px=pt_per_px)
                       if uniform_strokes else _mixed_cell_borders(
                           strokes, box, under=fill, pt_per_px=pt_per_px))
            cell_styles[row][column] = {"horizontalAlign": "left", **({"borders": borders} if borders else {})}
            if rs > 1 or cs > 1:
                merged.append(dict(row=row, column=column, rowSpan=rs, columnSpan=cs))
        payload = dict(rows=payload_rows, cellStyles=cell_styles,
                       columnWidths=[round(right-left, 6) for left, right in zip(xs, xs[1:], strict=False)],
                       rowHeightsPt=[round((bottom-top) * pt_per_px, 6)
                                     for top, bottom in zip(ys, ys[1:], strict=False)])
        if merged:
            payload["mergedCells"] = merged
        objects.append(NativeObject.build(id=record["element_id"], kind="table", anchor=frame, payload=payload))
    return (NativeObjects(tuple(objects)).to_wire() if objects else None,
            {"declared": len(evidence), "accepted": len(objects), "dropped": [],
             "source_cells_verified": True} if evidence else None)


# --- Source output view, native-table stage -----------------------------------------------------
# The model writes ``data-native-table='{"headerRows":N}'`` on a source table group; the server
# rebuilds today's full declaration from the table's immutable source cells.  Publication and export
# of source tables read the source cells and the drawn grid, never the model's JSON, so the rows are
# only a parse-time copy.  ``services/generation_core/drivers/svg/native_declarations.py`` is a
# digest-pinned converter file and is deliberately left untouched: the rule wording is rewritten on
# the per-call rules string instead, never on a module constant.

NATIVE_TABLE_VIEW_VERSION = "svg-native-table-source-rows/v1"

#: Message of every rule rewriter when the sentence it replaces is not exactly once in its input.
_RULE_ANCHOR_MISSING = "svg_output_view_rule_anchor_missing"


def source_table_rows_from_facts(facts) -> dict[str, list[list[str]]]:
    """Canonical source-table group id -> declaration rows, from whole-page ``source_facts``.

    Each row list has the source grid's size (``data.rows``, as ``svg_source_layout._cells`` reads
    it); the anchor cell's source text sits at (row, column) and every merge-covered or empty
    position holds ``""``.  Only ``cells[]`` text is read, so the prompt view that drops
    ``data.rows[r][c].text`` is equivalent.  ``facts`` must carry canonical ids (before any alias).
    Facts are server-built, not model output: a malformed table fact is a server defect and raises
    ``ValueError("svg_output_view_table_facts_invalid")`` instead of yielding wrong rows.
    """
    tables: dict[str, list[list[str]]] = {}
    for fact in facts:
        if not isinstance(fact, dict) or fact.get("kind") != "table":
            continue
        group_id, data, cells = fact.get("element_id"), fact.get("data"), fact.get("cells")
        grid = data.get("rows") if isinstance(data, dict) else None
        if (not isinstance(group_id, str) or group_id in tables or not isinstance(cells, list)
                or not isinstance(grid, list) or not grid or not isinstance(grid[0], list) or not grid[0]
                or any(not isinstance(row, list) or len(row) != len(grid[0]) for row in grid)):
            raise ValueError("svg_output_view_table_facts_invalid")
        rows = [["" for _ in grid[0]] for _ in grid]
        placed = set()
        for cell in cells:
            if not isinstance(cell, dict):
                raise ValueError("svg_output_view_table_facts_invalid")
            row, column, text = cell.get("row"), cell.get("column"), cell.get("text")
            if (type(row) is not int or type(column) is not int or not isinstance(text, str)
                    or not 0 <= row < len(rows) or not 0 <= column < len(rows[0]) or (row, column) in placed):
                raise ValueError("svg_output_view_table_facts_invalid")
            placed.add((row, column))
            rows[row][column] = text
        tables[group_id] = rows
    return tables


def _compact_table_declaration(value: str) -> dict | None:
    """The parsed compact declaration, or ``None`` when ``value`` is not exactly that form.

    Compact means a JSON object whose keys are within ``{"headerRows"}`` and whose ``headerRows``
    (when present) is an integer; booleans and strings are not integers.
    """
    try:
        raw = json.loads(value)
    except (ValueError, RecursionError):
        return None
    if not isinstance(raw, dict) or not set(raw) <= {"headerRows"}:
        return None
    if "headerRows" in raw and type(raw["headerRows"]) is not int:
        return None
    return raw


def expand_source_table_declarations(root, table_rows) -> dict[str, int]:
    """Rewrite each compact source-table declaration in ``root`` to today's full form, in place.

    Only a ``data-native-table`` value in the compact form on a node whose ``id`` is a key of
    ``table_rows`` is rewritten, to ``{"rows": <source rows>, "headerRows": N}`` serialized as the
    model writes it (``ensure_ascii=False``, compact separators).  Nothing is ever synthesized and
    nothing raises on model output; every other declaration is left for the existing validators.
    Counters:

    * ``expanded``  - compact declarations on source table groups, rewritten
    * ``full_form`` - declarations on source table groups left as written (full JSON, or any value
      that is not the compact form, e.g. extra keys, ``"headerRows":"1"``, invalid JSON)
    * ``absent``    - source table groups (``table_rows`` keys) with no declaration attribute anywhere
    * ``foreign``   - declarations on nodes whose id is not a source table group of this page
    """
    counts = {"expanded": 0, "full_form": 0, "absent": 0, "foreign": 0}
    declared = set()
    for node in root.iter():
        value = node.get(TABLE_ATTRIBUTE)
        if value is None:
            continue
        group_id = node.get("id")
        if group_id not in table_rows:
            counts["foreign"] += 1
            continue
        declared.add(group_id)
        compact = _compact_table_declaration(value)
        if compact is None:
            counts["full_form"] += 1
            continue
        node.set(TABLE_ATTRIBUTE, json.dumps({"rows": table_rows[group_id], **compact},
                                             ensure_ascii=False, separators=(",", ":")))
        counts["expanded"] += 1
    counts["absent"] = sum(1 for group_id in table_rows if group_id not in declared)
    return counts


# The native table rule is ``NATIVE_TABLE_RULE`` in native_declarations.py, concatenated into the
# per-call rules by ``author_slide_svg.svg_author_rules``.  Each (old, new) pair below is applied
# exactly once; the old text is anchored on its neighbouring words so a second application cannot
# match the rewritten sentence.
_COMPACT_TABLE_RULE_EDITS = (
    ('one explicit y per line). JSON is {"rows":[["Header","Header"],["cell","1,200"]],"headerRows":1}:',
     'one explicit y per line). For a source table (a source_facts entry of kind table) the group id is its '
     'element_id and the JSON is only {"headerRows":N} (N = the rows you draw as header); the server fills '
     "the rows from that table's source cells, so never copy cell text into this attribute. "
     'For any other table JSON is {"rows":[["Header","Header"],["cell","1,200"]],"headerRows":1}:'),
    ("Preserve explicit source cell paragraph breaks in the drawing and JSON;",
     "Preserve explicit source cell paragraph breaks in the drawing;"),
)
# Conditional edits: (marker, (old, new)).  The sentence is rewritten only where its marker is
# present, and once the marker is there the exact sentence must be too, otherwise a reworded
# sentence would keep contradicting the compact form.
_COMPACT_CONDITIONAL_RULE_EDITS = (
    # The source-grounded addendum of the native rule (quality policy v2 only).
    ("When current source-native bindings or source visual evidence explicitly establish a horizontal merged cell",
     ("Keep the exact rectangular rows and original empty covered cells in JSON; never repeat the value "
      "into the covered cells or add mergedCells to this declaration.",
      "For a source table the server writes the exact rectangular rows and original empty covered cells, so "
      "declare only headerRows; for any other table keep them in JSON. Never repeat the value into the "
      "covered cells or add mergedCells to this declaration.")),
    # ``SVG_AUTHOR_RULES`` (and so ``SVG_NEW_COPY_AUTHOR_RULES``): every svg_author_rules() output
    # carries it, a bare native rule string does not.
    ("must have its own data-native-table",
     ("Every explicitly requested table or supported data chart must have its own data-native-table or\n"
      "data-native-chart declaration containing that exact requested data.",
      "Every explicitly requested table or supported data chart must have its own data-native-table or\n"
      "data-native-chart declaration. A chart declaration, and the declaration of a table that is not a "
      "source table, contains that exact requested data; for a source table the compact "
      '{"headerRows":N} declaration is that declaration and the server supplies its rows.')),
)


def compact_source_table_rules(rules: str) -> str:
    """The svg author rules with the native table sentences rewritten for the compact declaration.

    Apply it only to the per-call rules string, never to ``SVG_AUTHOR_RULES`` or any rule constant
    (the constants are digest inputs).  Raises ``ValueError("svg_output_view_rule_anchor_missing")``
    when a sentence to replace is absent or not unique, which includes a second application.
    """
    edits = list(_COMPACT_TABLE_RULE_EDITS)
    edits += [edit for marker, edit in _COMPACT_CONDITIONAL_RULE_EDITS if marker in rules]
    for old, new in edits:
        if rules.count(old) != 1:
            raise ValueError(_RULE_ANCHOR_MISSING)
        rules = rules.replace(old, new)
    return rules


#: (old, new) for ``author_source_svg_layout._author_source_whole_page``'s ``source_rules``: the
#: source rule says to keep table group attributes to the id, which the compact declaration extends.
COMPACT_SOURCE_TABLE_GROUP_RULE = (
    "keep table group attributes to its id, with no transform",
    "keep table group attributes to its id and the compact data-native-table, with no transform",
)
