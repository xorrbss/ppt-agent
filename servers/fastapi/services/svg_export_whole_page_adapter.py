"""Bind chart-owned source text to the editable export projection."""
from __future__ import annotations

import json
import hashlib
import math
from copy import deepcopy
from xml.etree import ElementTree as ET

from services.generation_core.drivers.svg.native_chart import CHART_ELEMENTS
from services.generation_core.drivers.svg.native_chart_visual import (
    _bar_matrix, _category_texts, _value_texts,
)
from services.generation_core.drivers.svg.export_chart_style import _source_data_label_format
from services.generation_core.drivers.svg.native_geometry import (
    collect_primitives, find_group, is_bold, normalize_text, parse_label_number,
)
from services.svg_contract import SVG_NS, source_digest


_TEXT = f"{{{SVG_NS}}}text"
_GROUP = f"{{{SVG_NS}}}g"
_BINDING_ATTRIBUTE = "data-pptx-source-binding-id"
_PAGE_ATTRIBUTE = "data-pptx-source-page-index"
_FRAGMENT_ATTRIBUTE = "data-pptx-source-fragment-binding-id"
_FRAGMENT_PAGE_ATTRIBUTE = "data-pptx-source-fragment-page-index"
_FRAGMENT_INDEX_ATTRIBUTE = "data-pptx-source-fragment-index"


def fragment_shape_name(page_index, source_id, fragment_index):
    value = f"{page_index}\0{source_id}\0{fragment_index}".encode("utf-8")
    return "svg-source-fragment-" + hashlib.sha256(value).hexdigest()[:32]


def _signature(text):
    return (text.lines, text.points, text.size, text.family, text.fill,
            text.weight, text.anchor)


def _style(text):
    if text.fill is None or not text.family or text.size <= 0:
        raise ValueError("svg_source_export_text_style_unproven")
    return {"fontSizePx": text.size,
            "fontFamily": text.family.split(",")[0].strip().strip("\"'"),
            "fill": text.fill.upper().lstrip("#"), "bold": is_bold(text.weight)}


def _single_text(node):
    holder = ET.Element(_GROUP)
    holder.append(deepcopy(node))
    texts = collect_primitives(holder, (0, 0), {}, CHART_ELEMENTS).texts
    if len(texts) != 1 or not texts[0].uniform:
        raise ValueError("svg_source_export_overlay_geometry_invalid")
    return texts[0]


def project_source_table_end_alignment(canonical_svg, receipt, native_page):
    """Preserve only explicit, verified source end anchors in the export sidecar."""
    from dataclasses import replace
    from services.generation_core.native_objects import NativeObject, NativeObjects
    if source_digest(canonical_svg) != receipt.get("svg_digest"):
        raise ValueError("svg_source_export_table_style_source_mismatch")
    if native_page.sidecar is None:
        return native_page, []
    root = ET.fromstring(canonical_svg)
    nodes = {}
    for node in root.iter():
        if node.get("id"):
            nodes.setdefault(node.get("id"), []).append(node)
    declarations = {item["element_id"]: item["block_id"] for item in receipt.get("native_tables") or []}
    objects, claims = [], []
    for object_ in native_page.sidecar.objects:
        if object_.kind != "table" or object_.id not in declarations:
            objects.append(object_)
            continue
        payload = deepcopy(object_.payload)
        styles = payload.get("cellStyles")
        if not isinstance(styles, list) or len(styles) != len(payload["rows"]):
            raise ValueError("svg_source_export_table_style_payload_invalid")
        cells = {}
        for binding in receipt.get("bindings") or []:
            if binding.get("kind") == "table_cell" and binding.get("block_id") == declarations[object_.id]:
                cells.setdefault((binding["row"], binding["column"], binding["source_id"]), []).append(binding)
        for (row, column, source_id), bindings in cells.items():
            # Only a numeric label is aligned by this projection. A wrapped or otherwise
            # non-numeric cell contributes nothing here, and its text is proven by the
            # source receipt, so it must not be re-matched against joined line text.
            numeric = parse_label_number("".join(binding["text"] for binding in sorted(bindings, key=lambda binding: binding["start"])))
            if numeric is None:
                continue
            texts = []
            for binding in bindings:
                matches = nodes.get(binding["element_id"], [])
                if len(matches) != 1 or matches[0].tag != _TEXT:
                    raise ValueError("svg_source_export_table_style_binding_invalid")
                node = matches[0]
                primitive = _single_text(node)
                if normalize_text(primitive.text) != normalize_text(binding["text"]):
                    raise ValueError("svg_source_export_table_style_text_mismatch")
                texts.append((node, primitive))
            if texts and all(node.get("text-anchor") == "end" and text.anchor == "end" and text.uniform
                                                    for node, text in texts):
                styles[row][column]["horizontalAlign"] = "right"
                claims.append({"tableId": object_.id, "sourceId": source_id, "row": row, "column": column,
                               "sourceAnchor": "end", "nativeAlignment": "right"})
        objects.append(NativeObject.build(id=object_.id, kind=object_.kind, anchor=object_.anchor,
                                         payload=payload, svgAppearance=object_.svg_appearance))
    return replace(native_page, sidecar=NativeObjects(tuple(objects))), claims


def _uniform_source_grid_style(object_, primitives, chart_payload):
    """Reuse the established source bar/column plot and grid admission geometry."""
    if object_.payload.get("chartType") not in {"bar", "column"} or not chart_payload.get("axes", {}).get("value", {}).get("major_gridlines"):
        return None
    categories = _category_texts(object_.payload, primitives)
    plots = [shape for series in _bar_matrix(object_.payload, primitives, categories) for shape in series]
    if not plots:
        return None
    plot = plots[0].box
    for shape in plots[1:]:
        plot = plot.union(shape.box)
    horizontal = object_.payload["chartType"] == "bar"
    lines = [shape for shape in primitives.shapes if shape.tag == "line" and shape.stroke and shape.stroke_alpha > 0]
    grids = [shape for shape in lines if (
        shape.box.height >= plot.height * .8 and shape.box.width <= 1
        and plot.x0 + 1 < shape.box.x0 < plot.x1 - 1 if horizontal else
        shape.box.width >= plot.width * .8 and shape.box.height <= 1
        and plot.y0 + 1 < shape.box.y0 < plot.y1 - 1)]
    if not grids or len({shape.stroke for shape in grids}) != 1 or any(shape.stroke_alpha != 1 for shape in grids):
        return None
    color = grids[0].stroke.upper().lstrip("#")
    if len(color) != 6 or any(value not in "0123456789ABCDEF" for value in color):
        return None
    widths = sorted({shape.stroke_width for shape in grids})
    if len(widths) != 1 or not math.isfinite(widths[0]) or widths[0] <= 0:
        raise ValueError("svg_source_export_grid_width_unsupported")
    chart_payload.setdefault("style", {})["grid_color"] = "#" + color
    return {"sourceColor": color, "sourceWidthsPx": widths,
            "sourceGridLineCount": len(grids), "nativeWidthPolicy": "source-scale/v1"}


def bind_chart_text_projection(path, *, canonical_svg, receipt, native_page, page_index):
    """Name only generated editable overlays with unique source geometry."""
    if (receipt.get("source_authoring_contract") != {"version": "svg-source-whole-page/v1"}
            or source_digest(canonical_svg) != receipt.get("svg_digest")):
        raise ValueError("svg_source_export_overlay_source_mismatch")
    original = ET.fromstring(canonical_svg)
    projected = ET.fromstring(path.read_text(encoding="utf-8"))
    sidecar = native_page.sidecar if native_page is not None else None
    objects = sidecar.by_id() if sidecar is not None else {}
    bindings = {item["element_id"]: item for item in receipt["bindings"]
                if item["kind"] in {"text", "table_cell"}}
    candidates = [node for node in projected.iter()
                  if node.tag == _TEXT and node.get("id") is None]
    parents = {child: parent for parent in projected.iter() for child in parent}
    claims = []
    used = set()
    for record in receipt.get("whole_page_native_groups") or []:
        if record.get("kind") != "chart":
            continue
        chart_id = record["element_id"]
        object_ = objects.get(chart_id)
        found = find_group(original, chart_id)
        projected_group = next((node for node in projected.iter()
                                if node.get("id") == chart_id), None)
        if (object_ is None or object_.kind != "chart" or found is None
                or projected_group is None or projected_group.get("data-pptx-native") != "chart"):
            raise ValueError("svg_source_export_overlay_chart_missing")
        group, offset, inherited = found
        original_nodes = [node for node in group.iter() if node.tag == _TEXT]
        all_primitives = collect_primitives(group, offset, inherited, CHART_ELEMENTS)
        primitives = all_primitives.texts
        if len(original_nodes) != len(primitives):
            raise ValueError("svg_source_export_overlay_text_count_invalid")
        primitive_by_id = dict(zip((id(text) for text in primitives),
                                   (node.get("id") for node in original_nodes), strict=True))
        categories = _category_texts(object_.payload, all_primitives)
        values = _value_texts(object_.payload, all_primitives)
        number_format = _source_data_label_format(values)
        if number_format is None:
            raise ValueError("svg_source_export_number_format_unproven")
        matrix = _bar_matrix(object_.payload, all_primitives, categories)
        category_roles = {primitive_by_id[id(text)]: index
                          for index, text in enumerate(categories)}
        value_roles = {}
        remaining_values = list(values)
        for series_index, (series, bars) in enumerate(zip(
                object_.payload["series"], matrix, strict=True)):
            for category_index, (value, bar) in enumerate(zip(
                    series["values"], bars, strict=True)):
                box = bar.box
                matches = [text for text in remaining_values
                           if parse_label_number(text.text) == value and text.points
                           and box.x0 - 1 <= text.points[0][0] <= box.x1 + 1
                           and box.y0 - 1 <= text.points[0][1] <= box.y1 + 1]
                if len(matches) != 1:
                    raise ValueError("svg_source_export_value_label_owner_ambiguous")
                text = matches[0]
                remaining_values.remove(text)
                value_roles[primitive_by_id[id(text)]] = (series_index, category_index)
        if remaining_values or len(value_roles) != sum(len(s["values"]) for s in object_.payload["series"]):
            raise ValueError("svg_source_export_value_label_coverage_invalid")
        legend_names = [normalize_text(series["name"]) for series in object_.payload["series"]]
        furniture = [normalize_text(value) for value in (
            object_.payload.get("title"),
            (object_.payload.get("categoryAxis") or {}).get("title"),
            (object_.payload.get("valueAxis") or {}).get("title")) if value]
        overlay_ids, legend_ids, native_categories, native_values = [], [], [], []
        source_styles = {}
        for node, primitive in zip(original_nodes, primitives, strict=True):
            source_id = node.get("id")
            binding = bindings.get(source_id)
            if (binding is None or not primitive.uniform
                    or normalize_text(binding["text"]) != normalize_text(primitive.text)):
                raise ValueError("svg_source_export_overlay_binding_invalid")
            matches = [candidate for candidate in candidates
                       if candidate not in used and _signature(_single_text(candidate)) == _signature(primitive)]
            source_styles[source_id] = _style(primitive)
            if source_id in category_roles or source_id in value_roles:
                if len(matches) > 1:
                    raise ValueError("svg_source_export_dynamic_label_owner_ambiguous")
                if matches:
                    parent = parents.get(matches[0])
                    if parent is None:
                        raise ValueError("svg_source_export_dynamic_label_owner_invalid")
                    parent.remove(matches[0])
                    used.add(matches[0])
                if source_id in category_roles:
                    native_categories.append({"sourceId": source_id,
                                              "categoryIndex": category_roles[source_id]})
                else:
                    series_index, category_index = value_roles[source_id]
                    native_values.append({"sourceId": source_id,
                                          "seriesIndex": series_index,
                                          "categoryIndex": category_index})
            elif normalize_text(binding["text"]) in legend_names:
                if legend_names.count(normalize_text(binding["text"])) != 1 or len(matches) > 1:
                    raise ValueError("svg_source_export_legend_binding_ambiguous")
                if matches:
                    parent = parents.get(matches[0])
                    if parent is None:
                        raise ValueError("svg_source_export_legend_overlay_invalid")
                    parent.remove(matches[0])
                    used.add(matches[0])
                series_index = legend_names.index(normalize_text(binding["text"]))
                legend_names[series_index] = None
                legend_ids.append({"sourceId": source_id, "seriesIndex": series_index})
            elif normalize_text(binding["text"]) in furniture and len(matches) == 1:
                if (furniture.count(normalize_text(binding["text"])) != 1
                        or len(primitive.lines) != 1 or len(primitive.points) != 1):
                    raise ValueError("svg_source_export_furniture_binding_ambiguous")
                matches[0].set(_BINDING_ATTRIBUTE, source_id)
                matches[0].set(_PAGE_ATTRIBUTE, str(page_index))
                used.add(matches[0])
                furniture.remove(normalize_text(binding["text"]))
                overlay_ids.append(source_id)
            else:
                raise ValueError("svg_source_export_overlay_owner_ambiguous")
        if any(name is not None for name in legend_names) or furniture:
            raise ValueError("svg_source_export_legend_binding_missing")
        chart_payload = json.loads(projected_group.get("data-pptx-json") or "null")
        if not isinstance(chart_payload, dict):
            raise ValueError("svg_source_export_chart_projection_invalid")
        category_title = (object_.payload.get("categoryAxis") or {}).get("title")
        if category_title:
            axis_titles = chart_payload.get("axis_titles")
            if (not isinstance(axis_titles, dict)
                    or axis_titles.get("category") != category_title
                    or "x" in axis_titles):
                raise ValueError("svg_source_export_category_title_owner_ambiguous")
            axis_titles.pop("category")
            if not axis_titles:
                chart_payload.pop("axis_titles")
        category_axis = chart_payload.get("axes", {}).get("category")
        labels = chart_payload.get("data_labels")
        if (not isinstance(category_axis, dict) or not isinstance(labels, dict)
                or len(native_categories) != len(object_.payload["categories"])
                or labels.get("number_format") != number_format):
            raise ValueError("svg_source_export_chart_axis_missing")
        category_styles = [source_styles[record["sourceId"]]
                           for record in native_categories]
        if (not category_styles or any(style != category_styles[0]
                                       for style in category_styles)
                or category_styles[0]["bold"]):
            raise ValueError("svg_source_export_category_style_unsupported")
        chart_style = chart_payload.setdefault("style", {})
        chart_style["font_face"] = category_styles[0]["fontFamily"]
        chart_style["text_color"] = category_styles[0]["fill"]
        chart_style["axis_font_size"] = category_styles[0]["fontSizePx"]
        category_axis["label_position"] = "nextTo"
        labels["show_value"] = True
        labels["show_category"] = False
        for series_index, projected_series in enumerate(chart_payload.get("series") or []):
            series_labels = projected_series.get("data_labels")
            if (not isinstance(series_labels, dict)
                    or series_labels.get("number_format") != number_format):
                raise ValueError("svg_source_export_point_style_unproven")
            points = series_labels.get("points")
            expected_points = [record for record in native_values
                               if record["seriesIndex"] == series_index]
            if (not isinstance(points, list) or len(points) != len(expected_points)
                    or {point.get("idx") for point in points}
                       != {record["categoryIndex"] for record in expected_points}):
                raise ValueError("svg_source_export_point_style_unproven")
            by_index = {point["idx"]: point for point in points}
            for record in expected_points:
                style = source_styles[record["sourceId"]]
                point = by_index[record["categoryIndex"]]
                if (str(point.get("color", "")).upper().lstrip("#") != style["fill"]
                        or point.get("bold") is not style["bold"]
                        or point.get("font_size") != style["fontSizePx"]
                        or point.get("font_family") != style["fontFamily"]):
                    raise ValueError("svg_source_export_point_style_mismatch")
            series_labels["show_value"] = True
            series_labels["show_category"] = False
        grid_style = _uniform_source_grid_style(object_, all_primitives, chart_payload)
        projected_group.set("data-pptx-json", json.dumps(
            chart_payload, ensure_ascii=False, separators=(",", ":")))
        claims.append({"chartId": chart_id, "overlayIds": overlay_ids,
                       "nativeLegendIds": legend_ids,
                       "nativeCategoryIds": native_categories,
                       "nativeValueIds": native_values,
                       "sourceStyles": source_styles,
                       "numberFormat": number_format,
                       **({"gridStyle": grid_style} if grid_style is not None else {})})
    path.write_text(ET.tostring(projected, encoding="unicode"), encoding="utf-8")
    return claims


def fragment_lines_match(lines, text):
    """True when ``text`` is the lines joined with an optional single space per boundary.

    A source line may break between words (a space is dropped) or inside a word, as
    Korean text commonly does (no space exists), so both joins describe the same text.
    """
    text = normalize_text(text)
    position = 0
    for index, line in enumerate(lines):
        if not text.startswith(line, position):
            return False
        position += len(line)
        if index < len(lines) - 1 and text.startswith(" ", position):
            position += 1
    return position == len(text)


def source_fragment_lines(node, text, parents):
    """Per-line fragments of a start-anchored, uniformly styled multi-line source text.

    Returns ``None`` for any other multi-line text. The converter and the readback both
    call this one predicate so they always agree on which texts carry fragment identity.
    """
    children = list(node)
    if len(children) < 2 or node.tag != _TEXT:
        return None
    if (node.text and node.text.strip()) or any(child.tail and child.tail.strip() for child in children):
        return None
    if node.get("transform") or any(child.get("style") or child.get("transform") for child in children):
        return None
    ancestor = node
    while ancestor in parents:
        ancestor = parents[ancestor]
        if ancestor.get("transform"):
            return None
    lines, positions, fragments = [], set(), []
    for child in children:
        if child.tag != f"{{{SVG_NS}}}tspan" or list(child):
            return None
        try:
            x, y = float(child.attrib["x"]), float(child.attrib["y"])
        except (KeyError, TypeError, ValueError):
            return None
        if not math.isfinite(x) or not math.isfinite(y) or (x, y) in positions:
            return None
        positions.add((x, y))
        line = normalize_text(child.text or "")
        if not line:
            return None
        lines.append(line)
        fragments.append({"index": len(fragments), "x": x, "y": y,
                          "textDigest": hashlib.sha256(line.encode("utf-8")).hexdigest()})
    if not fragment_lines_match(lines, text):
        return None
    try:
        drawn = _single_text(node)
        _style(drawn)
    except ValueError:
        return None
    if drawn.anchor != "start":
        return None
    return lines, fragments


def source_text_lines(node):
    """Lines of a multi-line source text that the converter flattens to one shape per line.

    The first line may be the element's own text; every further line is a positional
    tspan. A tspan with no position of its own (no x, y, dx or dy) is an inline run that
    continues the current line, which the converter draws in that line's shape.
    Anything else is a single shape and is not described here.
    """
    children = list(node)
    if node.tag != _TEXT or not children:
        return None
    raw_lines = []
    if normalize_text(node.text or ""):
        raw_lines.append(node.text)
    for child in children:
        if child.tag != f"{{{SVG_NS}}}tspan" or list(child) or (child.tail and child.tail.strip()):
            return None
        positioned = "x" in child.attrib and "y" in child.attrib
        if not positioned and any(key in child.attrib for key in ("x", "y", "dx", "dy")):
            return None
        if positioned or not raw_lines:
            raw_lines.append(child.text or "")
        else:
            raw_lines[-1] += child.text or ""
        # A whitespace-only tail separates this run from an inline run that follows.
        raw_lines[-1] += child.tail or ""
    lines = [line for line in (normalize_text(raw) for raw in raw_lines) if line]
    return lines if len(lines) >= 2 else None


def bind_source_text_fragments(path, *, canonical_svg, receipt, page_index):
    """Mark only source-proven multi-line text outside native objects."""
    if source_digest(canonical_svg) != receipt.get("svg_digest"):
        raise ValueError("svg_source_export_fragment_source_mismatch")
    source = ET.fromstring(canonical_svg)
    projected = ET.fromstring(path.read_text(encoding="utf-8"))
    source_nodes, projected_nodes = {}, {}
    for root, index in ((source, source_nodes), (projected, projected_nodes)):
        for node in root.iter():
            source_id = node.get("id")
            if source_id:
                if source_id in index:
                    raise ValueError("svg_source_export_fragment_id_duplicate")
                index[source_id] = node
    parents = {child: parent for parent in projected.iter() for child in parent}
    source_parents = {child: parent for parent in source.iter() for child in parent}
    marked = []
    for binding in receipt["bindings"]:
        if binding["kind"] != "text" or not binding.get("text"):
            continue
        source_id = binding["element_id"]
        original, current = source_nodes.get(source_id), projected_nodes.get(source_id)
        if original is None or current is None or original.tag != _TEXT:
            raise ValueError("svg_source_export_fragment_source_text_missing")
        children = list(original)
        if len(children) < 2:
            continue
        eligible = source_fragment_lines(original, binding["text"], source_parents)
        if eligible is None:
            # Not representable as per-line fragments (centered, mixed styling, ...): it stays
            # ordinary editable text, and the readback expects its lines individually.
            continue
        lines, fragments = eligible
        if ET.tostring(original) != ET.tostring(current):
            raise ValueError("svg_source_export_fragment_projection_mismatch")
        ancestor = current
        while ancestor in parents:
            ancestor = parents[ancestor]
            if ancestor.get("transform") or ancestor.get("data-pptx-native"):
                raise ValueError("svg_source_export_fragment_parent_unsupported")
        current.set(_FRAGMENT_ATTRIBUTE, source_id)
        current.set(_FRAGMENT_PAGE_ATTRIBUTE, str(page_index))
        marked.append({"sourceId": source_id, "fragments": fragments})
    path.write_text(ET.tostring(projected, encoding="unicode"), encoding="utf-8")
    return marked
