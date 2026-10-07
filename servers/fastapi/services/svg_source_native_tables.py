"""Native tables from verified source cells and the server's actual painted grid."""
from __future__ import annotations

import hashlib
import math
from xml.etree import ElementTree as ET

from services.generation_core.drivers.svg.native_geometry import Box, collect_primitives, normalize_cell
from services.generation_core.drivers.svg.native_paint import background_layers, cell_borders, effective_fill, group_strokes
from services.generation_core.drivers.svg.native_table import TABLE_ELEMENTS, _text_style
from services.generation_core.native_objects import NativeObject, NativeObjects

_NS = "{http://www.w3.org/2000/svg}"


def source_table_group_id(block_id: str) -> str:
    return "source-native-table-" + hashlib.sha256(block_id.encode()).hexdigest()[:16]


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
        if len(rects) != len(cells):
            raise ValueError("svg_source_native_table_grid_mismatch")
        cw, rh = frame["width"] / len(rows[0]), frame["height"] / len(rows)
        for (row, column, rs, cs, cell), rect in zip(cells, rects, strict=True):
            expected = (frame["x"] + column * cw, frame["y"] + row * rh, cs * cw, rs * rh)
            try:
                actual = tuple(float(rect.get(key)) for key in ("x", "y", "width", "height"))
            except (TypeError, ValueError) as error:
                raise ValueError("svg_source_native_table_grid_mismatch") from error
            if any(not math.isfinite(value) or abs(value - target) > .000001
                   for value, target in zip(actual, expected, strict=True)):
                raise ValueError("svg_source_native_table_grid_mismatch")
            cell_id = cell.get("sourceCellId", f"{block_id}:row:{row}:column:{column}")
            bindings = [binding for binding in owned if binding["source_id"] == cell_id]
            node_ids = {binding["element_id"] for binding in bindings}
            for node in group:
                if node.get("id") in node_ids:
                    x, y = float(node.get("x")), float(node.get("y"))
                    if not (expected[0] <= x <= expected[0] + expected[2] and
                            expected[1] <= y <= expected[1] + expected[3]):
                        raise ValueError("svg_source_native_table_text_outside_cell")
        evidence.append((record, group, rows, cells, rects, owned))
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


def source_table_native_objects(svg, receipt, manifest, page_index, *, pt_per_px):
    """Bind immutable table semantics to verified actual source grid/paint/font."""
    from services.svg_source_layout import source_pages, verify_source_receipt
    pages = source_pages(manifest, chart_contract=receipt.get("source_chart_contract"))
    verify_source_receipt(svg, receipt, manifest, page_index, _validated_pages=pages)
    root = ET.fromstring(svg)
    evidence = _evidence(root, receipt, pages[page_index])
    objects = []
    for record, group, rows, cells, rects, owned in evidence:
        primitives = collect_primitives(group, (0., 0.), {}, TABLE_ELEMENTS)
        text_nodes = [node for node in group if node.tag == _NS + "text" and normalize_cell(node.text or "")]
        if len(text_nodes) != len(primitives.texts):
            raise ValueError("svg_source_native_table_text_invalid")
        drawn = {node.get("id"): text for node, text in zip(text_nodes, primitives.texts, strict=True)}
        frame = record["frame"]
        cw, rh = frame["width"] / len(rows[0]), frame["height"] / len(rows)
        # Server source grids use one stroke style. Neighbouring split edges of
        # a merged cell have the same paint, so use that cell's complete rect.
        if len({(shape.stroke, shape.stroke_width, shape.stroke_alpha) for shape in primitives.shapes}) != 1:
            raise ValueError("svg_source_native_table_border_style_mismatch")
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
            box = Box(frame["x"] + column * cw, frame["y"] + row * rh,
                      frame["x"] + (column + cs) * cw, frame["y"] + (row + rs) * rh)
            fill = effective_fill(layers, (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2)
            payload_rows[row][column].update(fill=fill, **_text_style(lines[0] if lines else None, pt_per_px))
            borders = cell_borders(group_strokes((shape,)), box, under=fill, pt_per_px=pt_per_px)
            cell_styles[row][column] = {"horizontalAlign": "left", **({"borders": borders} if borders else {})}
            if rs > 1 or cs > 1:
                merged.append(dict(row=row, column=column, rowSpan=rs, columnSpan=cs))
        payload = dict(rows=payload_rows, cellStyles=cell_styles,
                       columnWidths=[round(cw, 6)] * len(rows[0]),
                       rowHeightsPt=[round(rh * pt_per_px, 6)] * len(rows))
        if merged:
            payload["mergedCells"] = merged
        objects.append(NativeObject.build(id=record["element_id"], kind="table", anchor=frame, payload=payload))
    return (NativeObjects(tuple(objects)).to_wire() if objects else None,
            {"declared": len(evidence), "accepted": len(objects), "dropped": [],
             "source_cells_verified": True} if evidence else None)
