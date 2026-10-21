"""Structural readback of a produced svg-export PPTX (python-pptx, worker side)."""
from __future__ import annotations

import hashlib
import math
import re
import sys
import unicodedata
from collections import Counter
from xml.etree import ElementTree as ET

from pptx.enum.shapes import MSO_SHAPE_TYPE


_CHART_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_BINDING_NS = "urn:presenton:svg-source-chart-binding:v1"
_BINDING_URI = "{4e7bc59a-8a06-4a9f-9c7e-4eeb9930a54f}"
_DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _style_matches_xml(run_properties, source_style, emu_per_unit, *, text=None):
    expected_family = source_style["fontFamily"]
    if text is not None and expected_family == "Noto Sans KR":
        from utils.svg_template_fonts import trusted_font_fallback_plan

        try:
            plan = trusted_font_fallback_plan(
                text, expected_family, 700 if source_style["bold"] else 400)
        except ValueError:
            return False
        families = {segment["family"] for segment in plan["segments"]}
        if len(families) > 1:
            return False
        if families:
            expected_family = next(iter(families))
    expected_hpt = round(float(source_style["fontSizePx"]) * emu_per_unit / 127)
    fills = run_properties.findall(f"{{{_DRAWING_NS}}}solidFill/{{{_DRAWING_NS}}}srgbClr")
    fonts = run_properties.findall(f"{{{_DRAWING_NS}}}latin")
    return (run_properties.get("sz") is not None
            and int(run_properties.get("sz")) == expected_hpt
            and run_properties.get("b") in ({"1", "true"} if source_style["bold"]
                                            else {None, "0", "false"})
            and len(fills) == 1 and fills[0].get("val", "").upper() == source_style["fill"]
            and len(fonts) == 1 and fonts[0].get("typeface") == expected_family)


def _chart_text_style_matches(node, source_style, emu_per_unit):
    properties = node.findall(
        f"{{{_CHART_NS}}}txPr/{{{_DRAWING_NS}}}p/{{{_DRAWING_NS}}}pPr/"
        f"{{{_DRAWING_NS}}}defRPr")
    return len(properties) == 1 and _style_matches_xml(
        properties[0], source_style, emu_per_unit)


def _chart_binding_records(parent, role):
    ext_list = parent.find(f"{{{_CHART_NS}}}extLst")
    matches = [] if ext_list is None else [ext for ext in ext_list
              if ext.get("uri") == _BINDING_URI]
    if len(matches) != 1:
        raise ValueError("svg_source_pptx_chart_binding_metadata_missing")
    records = [dict(node.attrib) for node in matches[0]]
    if (not records or any(node.tag != f"{{{_BINDING_NS}}}sourceBinding"
                           or node.get("role") != role for node in matches[0])):
        raise ValueError("svg_source_pptx_chart_binding_metadata_invalid")
    return records


def _chart_workbook_bound(chart, payload):
    from services.source_content.pptx_chart_provenance import _verify_workbook
    from services.source_content.pptx_extraction_constants import NS
    from services.source_content.errors import SourceContentError
    from services.source_content.security import secure_xml
    from zipfile import BadZipFile

    workbook = chart.part.chart_workbook.xlsx_part
    if workbook is None:
        raise ValueError("svg_source_pptx_chart_workbook_missing")
    try:
        root = secure_xml(chart.part.blob, part_name="chart.xml")
    except (ValueError, SourceContentError) as error:
        raise ValueError("svg_source_pptx_chart_xml_invalid") from error
    plot = root.find(".//c:plotArea", NS)
    charts = [] if plot is None else [node for node in plot
              if node.tag.rsplit("}", 1)[-1].endswith("Chart")]
    if len(charts) != 1:
        raise ValueError("svg_source_pptx_chart_plot_ambiguous")
    expected = [{"name": series["name"], "categories": payload["categories"],
                 "values": series["values"]} for series in payload["series"]]
    try:
        _verify_workbook(workbook.blob, charts[0], expected)
    except (ValueError, KeyError, TypeError, OverflowError, BadZipFile,
            SourceContentError) as error:
        raise ValueError("svg_source_pptx_chart_workbook_mismatch") from error


def iter_shapes(shapes):
    for shape in shapes:
        yield shape
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from iter_shapes(shape.shapes)


def _readback_text(value):
    value = unicodedata.normalize("NFC", value or "")
    return re.sub(r"\s+", " ", value).strip()


_TEXT_BULLET_MARKER = re.compile(r"^\s*[·•●▪■◆◇◦‣]\s*")


def _expected_visible_text(value):
    """Text left in a shape after the converter turns a leading bullet marker into a
    native paragraph bullet (same rule and marker set as the vendored converter)."""
    text = _readback_text(value)
    match = _TEXT_BULLET_MARKER.match(text)
    if match and text[match.end():].strip():
        return _readback_text(text[match.end():])
    return text


def _binding_digest(value):
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _source_native_tables(shapes, receipt, deck):
    """Bind native table identity, grid and merged cells to immutable source cells."""
    declarations = receipt.get("native_tables") or []
    actual = [shape for shape in shapes if shape.has_table]
    if not declarations:
        if actual:
            raise ValueError("svg_source_pptx_table_binding_mismatch")
        return set(), []
    by_name = {shape.name: shape for shape in actual}
    if len(by_name) != len(actual) or len(actual) != len(declarations):
        raise ValueError("svg_source_pptx_table_binding_mismatch")
    consumed, evidence = set(), []
    for declaration in declarations:
        block_id, element_id = declaration["block_id"], declaration["element_id"]
        if block_id in consumed or element_id not in by_name:
            raise ValueError("svg_source_pptx_table_binding_mismatch")
        shape = by_name.pop(element_id)
        frame = declaration["frame"]
        scale_x = deck.slide_width / receipt["canvas"]["width"]
        scale_y = deck.slide_height / receipt["canvas"]["height"]
        if any(abs(actual_value - frame[key] * scale) > max(1, scale * .5)
               for actual_value, key, scale in ((shape.left, "x", scale_x), (shape.top, "y", scale_y),
                   (shape.width, "width", scale_x), (shape.height, "height", scale_y))):
            raise ValueError("svg_source_pptx_table_geometry_mismatch")
        table = shape.table
        entries = [b for b in receipt["bindings"]
                   if b["kind"] == "table_cell" and b.get("block_id") == block_id]
        cells = {}
        for binding in entries:
            cells.setdefault(binding["source_id"], []).append(binding)
        if not cells:
            raise ValueError("svg_source_pptx_table_binding_mismatch")
        coverage, cell_evidence = set(), []
        for source_id, lines in cells.items():
            lines.sort(key=lambda b: b["start"])
            first = lines[0]
            r, c, rs, cs = (first[k] for k in ("row", "column", "row_span", "column_span"))
            if min(r, c) < 0 or min(rs, cs) < 1 or r + rs > len(table.rows) or c + cs > len(table.columns):
                raise ValueError("svg_source_pptx_table_structure_mismatch")
            cursor, fragments = 0, []
            for line in lines:
                if (any(line[k] != first[k] for k in ("row", "column", "row_span", "column_span"))
                        or line["start"] != cursor or line["end"] != cursor + len(line["text"])):
                    raise ValueError("svg_source_pptx_table_binding_mismatch")
                fragments.append(line["text"])
                cursor = line["end"]
            value = "".join(fragments)
            cell = table.cell(r, c)
            if cell.is_spanned or cell.span_height != rs or cell.span_width != cs:
                raise ValueError("svg_source_pptx_table_cell_mismatch")
            if _readback_text(cell.text) != _readback_text(value):
                # A source-wrapped cell keeps one paragraph per drawn line; the declared cell
                # text then equals those lines joined with an optional space per boundary.
                from services.svg_export_whole_page_adapter import fragment_lines_match
                paragraph_lines = [line for paragraph in cell.text_frame.paragraphs
                                   if (line := _readback_text(paragraph.text))]
                if not paragraph_lines or not fragment_lines_match(paragraph_lines, _readback_text(value)):
                    raise ValueError("svg_source_pptx_table_cell_mismatch")
            for row in range(r, r + rs):
                for column in range(c, c + cs):
                    key = (row, column)
                    covered = table.cell(row, column)
                    if key in coverage or (key != (r, c) and (not covered.is_spanned or covered.text)):
                        raise ValueError("svg_source_pptx_table_structure_mismatch")
                    coverage.add(key)
            cell_evidence.append({"sourceId": source_id, "row": r, "column": c,
                "rowSpan": rs, "columnSpan": cs, "valueDigest": _binding_digest(value)})
        if len(coverage) != len(table.rows) * len(table.columns):
            raise ValueError("svg_source_pptx_table_structure_mismatch")
        consumed.add(block_id)
        evidence.append({"sourceBlockId": block_id, "elementId": element_id,
            "rows": len(table.rows), "columns": len(table.columns), "cells": cell_evidence})
    if by_name:
        raise ValueError("svg_source_pptx_table_binding_mismatch")
    return consumed, evidence


def _whole_page_native_charts(shapes, receipt, source, native_page, claims,
                              slide_width_emu):
    """Assign source-bound chart text to an exact native chart readback."""
    from services.svg_contract import source_digest

    if (receipt.get("source_authoring_contract") != {"version": "svg-source-whole-page/v1"}
            or source_digest(source) != receipt.get("svg_digest")
            or native_page is None):
        raise ValueError("svg_source_pptx_chart_owner_invalid")
    groups = [item for item in receipt.get("whole_page_native_groups") or []
              if item.get("kind") == "chart"]
    declared = {item.id: item for item in (
        native_page.sidecar.objects if native_page.sidecar is not None else ()) if item.kind == "chart"}
    chart_shapes = [shape for shape in shapes if shape.has_chart]
    actual = {shape.name: shape for shape in chart_shapes}
    claims_by_id = {claim.get("chartId"): claim for claim in claims}
    if (len(groups) != len(declared) or len(actual) != len(declared)
            or len(chart_shapes) != len(actual)
            or {item.get("element_id") for item in groups} != set(declared)
            or set(actual) != set(declared) or len(claims_by_id) != len(claims)
            or set(claims_by_id) != set(declared)):
        raise ValueError("svg_source_pptx_chart_binding_mismatch")
    root = ET.fromstring(source)
    view_box = (root.get("viewBox") or "").split()
    if len(view_box) != 4:
        raise ValueError("svg_source_pptx_chart_canvas_invalid")
    source_width = float(view_box[2])
    if not math.isfinite(source_width) or source_width <= 0:
        raise ValueError("svg_source_pptx_chart_canvas_invalid")
    emu_per_unit = float(slide_width_emu) / source_width
    nodes = {node.get("id"): node for node in root.iter() if node.get("id")}
    from services.generation_core.drivers.svg.native_chart import CHART_ELEMENTS
    from services.generation_core.drivers.svg.native_geometry import collect_primitives, find_group
    from services.svg_export_whole_page_adapter import _style
    bound_text_ids = {item["element_id"] for item in receipt["bindings"]
                      if item["kind"] in {"text", "table_cell"}}
    native_role_ids, evidence = set(), []
    for chart_id, item in declared.items():
        group = nodes.get(chart_id)
        if group is None:
            raise ValueError("svg_source_pptx_chart_binding_mismatch")
        owned = {node.get("id") for node in group.iter() if node.get("id")}
        scoped = owned & bound_text_ids
        claim = claims_by_id[chart_id]
        found = find_group(root, chart_id)
        if found is None:
            raise ValueError("svg_source_pptx_chart_source_geometry_missing")
        source_group, source_offset, source_inherited = found
        source_nodes = [node for node in source_group.iter()
                        if node.tag == f"{{http://www.w3.org/2000/svg}}text"]
        source_texts = collect_primitives(
            source_group, source_offset, source_inherited, CHART_ELEMENTS).texts
        if len(source_nodes) != len(source_texts):
            raise ValueError("svg_source_pptx_chart_source_text_invalid")
        source_styles = {node.get("id"): _style(text)
                         for node, text in zip(source_nodes, source_texts, strict=True)}
        if (len(source_styles) != len(source_nodes)
                or claim.get("sourceStyles") != source_styles):
            raise ValueError("svg_source_pptx_chart_source_style_mismatch")
        overlay_ids = claim.get("overlayIds")
        native_ids = claim.get("nativeLegendIds")
        categories_claim = claim.get("nativeCategoryIds")
        values_claim = claim.get("nativeValueIds")
        bounds = claim.get("overlayBoundsEmu")
        if (not scoped or not isinstance(overlay_ids, list) or not isinstance(native_ids, list)
                or not isinstance(categories_claim, list) or not isinstance(values_claim, list)
                or not isinstance(bounds, dict)
                or len(overlay_ids) != len(set(overlay_ids))
                or set(bounds) != set(overlay_ids)
                or any(set(record) != {"sourceId", "categoryIndex"}
                       for record in categories_claim)
                or any(set(record) != {"sourceId", "seriesIndex", "categoryIndex"}
                       for record in values_claim)):
            raise ValueError("svg_source_pptx_chart_binding_mismatch")
        categories_ids = [record["sourceId"] for record in categories_claim]
        value_ids = [record["sourceId"] for record in values_claim]
        legend_ids = [record["sourceId"] for record in native_ids]
        if (any(set(record) != {"sourceId", "seriesIndex"} for record in native_ids)
                or {record["seriesIndex"] for record in native_ids}
                   != set(range(len(item.payload["series"])))):
            raise ValueError("svg_source_pptx_legend_binding_mismatch")
        assigned = [*overlay_ids, *legend_ids, *categories_ids, *value_ids]
        if (len(assigned) != len(set(assigned)) or set(assigned) != scoped
                or native_role_ids & (set(legend_ids) | set(categories_ids) | set(value_ids))):
            raise ValueError("svg_source_pptx_chart_binding_mismatch")
        native_role_ids.update([*legend_ids, *categories_ids, *value_ids])
        anchor = item.anchor
        shape = actual[chart_id]
        expected_anchor = [round(value * emu_per_unit) for value in (
            anchor.x, anchor.y, anchor.width, anchor.height)]
        observed_anchor = [shape.left, shape.top, shape.width, shape.height]
        if any(abs(expected - observed) > 1 for expected, observed in zip(
                expected_anchor, observed_anchor, strict=True)):
            raise ValueError("svg_source_pptx_chart_geometry_mismatch")
        chart = actual[chart_id].chart
        payload = item.payload
        try:
            bindings_by_id = {entry["element_id"]: entry for entry in receipt["bindings"]}
            furniture = [_readback_text(value) for value in (
                payload.get("title"),
                (payload.get("categoryAxis") or {}).get("title"),
                (payload.get("valueAxis") or {}).get("title")) if value]
            if (len(furniture) != len(set(furniture))
                    or Counter(_readback_text(bindings_by_id[source_id]["text"])
                               for source_id in overlay_ids) != Counter(furniture)):
                raise ValueError("svg_source_pptx_overlay_role_invalid")
            overlay_shapes = [shape for shape in shapes if shape.has_text_frame
                              and shape.name in overlay_ids]
            if (len(overlay_shapes) != len(overlay_ids)
                    or {shape.name for shape in overlay_shapes} != set(overlay_ids)):
                raise ValueError("svg_source_pptx_overlay_identity_mismatch")
            for shape in overlay_shapes:
                source_id = shape.name
                recorded = bounds[source_id]
                c_nv_pr = shape._element.nvSpPr.cNvPr
                text_runs = shape._element.xpath(".//a:r")
                if (c_nv_pr.get("descr") != source_id
                        or _readback_text(shape.text_frame.text)
                           != _readback_text(bindings_by_id[source_id]["text"])
                        or [shape.left, shape.top, shape.left + shape.width,
                            shape.top + shape.height] != recorded
                        or not text_runs
                        or any((properties := run.find(f"{{{_DRAWING_NS}}}rPr")) is None
                               or not _style_matches_xml(properties, source_styles[source_id],
                                                         emu_per_unit,
                                                         text=run.findtext(f"{{{_DRAWING_NS}}}t", ""))
                               for run in text_runs)):
                    raise ValueError("svg_source_pptx_overlay_readback_mismatch")
            categories = [_readback_text(category.label) for category in chart.plots[0].categories]
            expected_categories = [_readback_text(value) for value in payload["categories"]]
            series = list(chart.series)
            expected_series = payload["series"]
            if (categories != expected_categories or len(series) != len(expected_series)
                    or any(_readback_text(actual_series.name) != _readback_text(expected["name"])
                           or len(actual_series.values) != len(expected["values"])
                           or any(not math.isclose(float(actual_value), float(expected_value),
                                                   rel_tol=1e-9, abs_tol=1e-9)
                                  for actual_value, expected_value in zip(
                                      actual_series.values, expected["values"], strict=True))
                           for actual_series, expected in zip(series, expected_series, strict=True))):
                raise ValueError("svg_source_pptx_chart_data_mismatch")
            if (len(categories_claim) != len(categories)
                    or {record["categoryIndex"] for record in categories_claim}
                       != set(range(len(categories)))
                    or any(_readback_text(bindings_by_id[record["sourceId"]]["text"])
                           != categories[record["categoryIndex"]] for record in categories_claim)):
                raise ValueError("svg_source_pptx_category_binding_mismatch")
            wanted_points = {(series_index, category_index)
                             for series_index, expected in enumerate(expected_series)
                             for category_index in range(len(expected["values"]))}
            from services.generation_core.drivers.svg.native_geometry import parse_label_number
            if (len(values_claim) != len(wanted_points)
                    or {(record["seriesIndex"], record["categoryIndex"])
                        for record in values_claim} != wanted_points
                    or any(parse_label_number(bindings_by_id[record["sourceId"]]["text"])
                           != expected_series[record["seriesIndex"]]["values"][record["categoryIndex"]]
                           for record in values_claim)):
                raise ValueError("svg_source_pptx_value_binding_mismatch")
            if payload.get("title") and (chart.has_title
                    or not any(_readback_text(bindings_by_id[source_id]["text"])
                               == _readback_text(payload["title"]) for source_id in overlay_ids)):
                raise ValueError("svg_source_pptx_chart_title_owner_mismatch")
            axis_title = (payload.get("valueAxis") or {}).get("title")
            if axis_title and (chart.value_axis.has_title
                    or not any(_readback_text(bindings_by_id[source_id]["text"])
                               == _readback_text(axis_title) for source_id in overlay_ids)):
                raise ValueError("svg_source_pptx_axis_title_owner_mismatch")
            category_title = (payload.get("categoryAxis") or {}).get("title")
            if category_title and (chart.category_axis.has_title
                    or not any(_readback_text(bindings_by_id[source_id]["text"])
                               == _readback_text(category_title) for source_id in overlay_ids)):
                raise ValueError("svg_source_pptx_category_title_owner_mismatch")
            if (not chart.has_legend or len(native_ids) != len(series)
                    or any(_readback_text(bindings_by_id[record["sourceId"]]["text"])
                           != _readback_text(series[record["seriesIndex"]].name)
                           for record in native_ids)):
                raise ValueError("svg_source_pptx_legend_binding_mismatch")
            legends = chart._chartSpace.xpath("./c:chart/c:legend")
            legend_styles = [source_styles[record["sourceId"]]
                             for record in native_ids]
            if (len(legends) != 1 or not legend_styles
                    or any(style != legend_styles[0] for style in legend_styles)
                    or not _chart_text_style_matches(legends[0], legend_styles[0],
                                                    emu_per_unit)):
                raise ValueError("svg_source_pptx_legend_style_mismatch")
            _chart_workbook_bound(chart, payload)
            xml_series = chart._chartSpace.xpath(".//c:ser")
            if len(xml_series) != len(series):
                raise ValueError("svg_source_pptx_chart_series_count_mismatch")
            legend_by_index = {record["seriesIndex"]: record for record in native_ids}
            for series_index, node in enumerate(xml_series):
                tx = node.xpath("./c:tx/c:strRef")
                cat = node.xpath("./c:cat/c:strRef|./c:cat/c:numRef")
                val = node.xpath("./c:val/c:numRef")
                label_sets = node.xpath("./c:dLbls")
                if any(len(found) != 1 for found in (tx, cat, val, label_sets)):
                    raise ValueError("svg_source_pptx_chart_dynamic_reference_missing")
                expected_legend = {"role": "seriesLegend", "sourceId":
                                   legend_by_index[series_index]["sourceId"],
                                   "seriesIndex": str(series_index)}
                expected_categories = [{"role": "category", "sourceId":
                                        record["sourceId"], "categoryIndex":
                                        str(record["categoryIndex"])} for record in categories_claim]
                scoped_values = [record for record in values_claim
                                 if record["seriesIndex"] == series_index]
                expected_values = [{"role": "valueSeries", "sourceId":
                                    record["sourceId"], "seriesIndex": str(series_index),
                                    "categoryIndex": str(record["categoryIndex"])}
                                   for record in scoped_values]
                if (_chart_binding_records(tx[0], "seriesLegend") != [expected_legend]
                        or sorted(_chart_binding_records(cat[0], "category"),
                                  key=lambda item: int(item["categoryIndex"]))
                           != sorted(expected_categories, key=lambda item: int(item["categoryIndex"]))
                        or sorted(_chart_binding_records(val[0], "valueSeries"),
                                  key=lambda item: int(item["categoryIndex"]))
                           != sorted(expected_values, key=lambda item: int(item["categoryIndex"]))):
                    raise ValueError("svg_source_pptx_chart_binding_metadata_mismatch")
                labels = label_sets[0].xpath("./c:dLbl")
                if len(labels) != len(scoped_values):
                    raise ValueError("svg_source_pptx_data_label_count_mismatch")
                expected_by_index = {record["categoryIndex"]: record
                                     for record in scoped_values}
                seen_points = set()
                for label in labels:
                    indices = label.xpath("./c:idx")
                    if len(indices) != 1 or not (indices[0].get("val") or "").isdecimal():
                        raise ValueError("svg_source_pptx_data_label_index_invalid")
                    point_index = int(indices[0].get("val"))
                    if point_index not in expected_by_index or point_index in seen_points:
                        raise ValueError("svg_source_pptx_data_label_index_invalid")
                    seen_points.add(point_index)
                    expected_record = expected_by_index[point_index]
                    expected_binding = {"role": "valuePoint", "sourceId":
                                        expected_record["sourceId"],
                                        "seriesIndex": str(series_index),
                                        "categoryIndex": str(point_index)}
                    if (_chart_binding_records(label, "valuePoint") != [expected_binding]
                            or label.xpath("./c:delete|./c:tx")
                            or len(label.xpath("./c:showVal")) != 1
                            or label.xpath("./c:showVal")[0].get("val") not in {"1", "true"}
                            or not _chart_text_style_matches(
                                label, source_styles[expected_record["sourceId"]],
                                emu_per_unit)):
                        raise ValueError("svg_source_pptx_data_label_static_or_hidden")
                    formats = label.xpath("./c:numFmt")
                    if (len(formats) != 1
                            or formats[0].get("formatCode") != claim.get("numberFormat")
                            or formats[0].get("sourceLinked") not in {"0", "false"}):
                        raise ValueError("svg_source_pptx_data_label_format_mismatch")
                    for flag in ("showCatName", "showSerName", "showPercent"):
                        flags = label.xpath(f"./c:{flag}")
                        if len(flags) != 1 or flags[0].get("val") not in {"0", "false"}:
                            raise ValueError("svg_source_pptx_data_label_owner_ambiguous")
                if seen_points != set(range(len(categories))):
                    raise ValueError("svg_source_pptx_data_label_coverage_invalid")
            from pptx.enum.chart import XL_TICK_LABEL_POSITION
            if chart.category_axis.tick_label_position != XL_TICK_LABEL_POSITION.NEXT_TO_AXIS:
                raise ValueError("svg_source_pptx_category_owner_ambiguous")
            category_axes = chart._chartSpace.xpath(".//c:catAx")
            category_styles = [source_styles[record["sourceId"]]
                               for record in categories_claim]
            if (len(category_axes) != 1 or not category_styles
                    or any(style != category_styles[0] for style in category_styles)
                    or not _chart_text_style_matches(category_axes[0], category_styles[0],
                                                    emu_per_unit)):
                raise ValueError("svg_source_pptx_category_style_mismatch")
            label_sets = chart._chartSpace.xpath(".//c:dLbls")
            if not label_sets:
                raise ValueError("svg_source_pptx_data_label_missing")
            for labels in label_sets:
                show_value = labels.find("{http://schemas.openxmlformats.org/drawingml/2006/chart}showVal")
                if show_value is None or show_value.get("val") not in {"1", "true"}:
                    raise ValueError("svg_source_pptx_data_label_owner_ambiguous")
            value_axis = payload.get("valueAxis") or {}
            for field, attr in (("minimum", "minimum_scale"), ("maximum", "maximum_scale"),
                                ("majorUnit", "major_unit")):
                if field in value_axis and not math.isclose(
                        float(getattr(chart.value_axis, attr)), float(value_axis[field]),
                        rel_tol=1e-9, abs_tol=1e-9):
                    raise ValueError("svg_source_pptx_chart_axis_mismatch")
        except (AttributeError, IndexError, TypeError, KeyError) as error:
            raise ValueError("svg_source_pptx_chart_readback_invalid") from error
        evidence.append({"elementId": chart_id, "seriesCount": len(series),
                         "categoryCount": len(categories), "sourceTextBindingCount": len(scoped),
                         "editableOverlayCount": len(overlay_ids),
                         "nativeLegendBindingCount": len(native_ids), "nativeDataVerified": True})
        evidence[-1]["nativeCategoryBindingCount"] = len(categories_claim)
        evidence[-1]["nativeValueBindingCount"] = len(values_claim)
    return native_role_ids, evidence


def _source_fragment_readback(shapes, receipt, source_svg, claims, page_index, slide_width):
    from services.svg_export_whole_page_adapter import (
        _single_text, _style, fragment_shape_name, source_fragment_lines,
    )
    from workers.public_svg_pptx_convert import VENDOR_ROOT

    root = ET.fromstring(source_svg)
    view_box = (root.get("viewBox") or "").split()
    if (len(view_box) != 4 or not all(math.isfinite(float(value)) for value in view_box)
            or float(view_box[0]) != 0 or float(view_box[1]) != 0
            or float(view_box[2]) <= 0):
        raise ValueError("svg_source_pptx_fragment_canvas_invalid")
    emu_per_unit = float(slide_width) / float(view_box[2])
    nodes = {node.get("id"): node for node in root.iter() if node.get("id")}
    if len(nodes) != sum(bool(node.get("id")) for node in root.iter()):
        raise ValueError("svg_source_pptx_fragment_source_id_duplicate")
    parents = {child: parent for parent in root.iter() for child in parent}
    sys.path.insert(0, str(VENDOR_ROOT))
    try:
        from svg_to_pptx.drawingml.elements import _textbox_padding
    finally:
        sys.path.remove(str(VENDOR_ROOT))
    source = {}
    for binding in receipt["bindings"]:
        if binding["kind"] != "text" or not binding.get("text"):
            continue
        source_id = binding["element_id"]
        node = nodes.get(source_id)
        if node is None:
            raise ValueError("svg_source_pptx_fragment_source_missing")
        if len(list(node)) < 2:
            continue
        # The converter marks exactly the texts this shared predicate accepts; every other
        # multi-line text stays ordinary text whose lines are accounted for individually.
        eligible = source_fragment_lines(node, binding["text"], parents)
        if eligible is None:
            continue
        lines, fragments = eligible
        style = _style(_single_text(node))
        for index, fragment in enumerate(fragments):
            key = (source_id, index)
            if key in source:
                raise ValueError("svg_source_pptx_fragment_source_duplicate")
            source[key] = (fragment["x"], fragment["y"], lines[index], style)
    claimed = {}
    for claim in claims:
        key = (claim.get("sourceId"), claim.get("fragmentIndex"))
        if key in claimed:
            raise ValueError("svg_source_pptx_fragment_claim_duplicate")
        claimed[key] = claim
    if set(source) != set(claimed):
        raise ValueError("svg_source_pptx_fragment_claim_incomplete")
    shape_names = [shape.name for shape in shapes if shape.has_text_frame]
    expected_names = {fragment_shape_name(page_index, *key) for key in source}
    if any(shape_names.count(name) != 1 for name in expected_names):
        raise ValueError("svg_source_pptx_fragment_shape_ambiguous")
    by_name = {shape.name: shape for shape in shapes if shape.name in expected_names}
    for key, (x, y, value, style) in source.items():
        claim = claimed[key]
        name = fragment_shape_name(page_index, *key)
        shape = by_name[name]
        bounds = [shape.left, shape.top, shape.left + shape.width,
                  shape.top + shape.height]
        runs = shape._element.xpath(".//a:r")
        expected_left = round((x - _textbox_padding(float(style["fontSizePx"])))
                              * emu_per_unit)
        expected_top = round((y - float(style["fontSizePx"]) * 0.85)
                             * emu_per_unit)
        if (claim.get("shapeName") != name or claim.get("sourceX") != x
                or claim.get("sourceY") != y
                or claim.get("textDigest") != hashlib.sha256(value.encode("utf-8")).hexdigest()
                or claim.get("boundsEmu") != bounds
                or abs(shape.left - expected_left) > 1
                or abs(shape.top - expected_top) > 1
                or shape._element.nvSpPr.cNvPr.get("descr") != name
                or _readback_text(shape.text_frame.text) != _expected_visible_text(value)
                or not runs
                or any((properties := run.find(f"{{{_DRAWING_NS}}}rPr")) is None
                       or not _style_matches_xml(properties, style, emu_per_unit,
                                                 text=run.findtext(f"{{{_DRAWING_NS}}}t", ""))
                       for run in runs)):
            raise ValueError("svg_source_pptx_fragment_readback_mismatch")
    return Counter(_expected_visible_text(value) for _, _, value, _ in source.values()), len(source)


def source_preservation_readback(deck, receipts, expected_image_digests, *,
                                 native_objects=None, source_svgs=None, ownership_claims=None,
                                 fragment_claims=None):
    whole_page = native_objects is not None or source_svgs is not None
    if whole_page and (native_objects is None or source_svgs is None or ownership_claims is None
                       or fragment_claims is None
                       or len(native_objects) != len(receipts) or len(source_svgs) != len(receipts)
                       or len(ownership_claims) != len(receipts)
                       or len(fragment_claims) != len(receipts)):
        raise ValueError("svg_source_pptx_readback_input_invalid")
    pages = []
    for index, (slide, receipt, image_digests) in enumerate(
        zip(deck.slides, receipts, expected_image_digests, strict=True)
    ):
        shapes = list(iter_shapes(slide.shapes))
        native_blocks, native_table_evidence = _source_native_tables(shapes, receipt, deck)
        native_role_ids, native_chart_evidence = (
            _whole_page_native_charts(shapes, receipt, source_svgs[index],
                                      native_objects[index], ownership_claims[index],
                                      deck.slide_width)
            if whole_page else (set(), []))
        text_bindings = [
            binding for binding in receipt["bindings"]
            if binding["kind"] in {"text", "table_cell"} and binding["text"]
        ]
        fragment_text, fragment_count = (
            _source_fragment_readback(shapes, receipt, source_svgs[index],
                                      fragment_claims[index], index, deck.slide_width)
            if whole_page else (Counter(), 0))
        fragment_ids = {claim["sourceId"] for claim in fragment_claims[index]} if whole_page else set()
        if fragment_ids & native_role_ids:
            raise ValueError("svg_source_pptx_fragment_native_conflict")
        ordinary_lines = {}
        if whole_page:
            from services.svg_export_whole_page_adapter import fragment_lines_match, source_text_lines
            source_nodes = {node.get("id"): node for node in ET.fromstring(source_svgs[index]).iter() if node.get("id")}
            for binding in text_bindings:
                node = source_nodes.get(binding["element_id"])
                if (binding["kind"] == "text" and node is not None and binding["element_id"] not in fragment_ids
                        and binding.get("block_id") not in native_blocks
                        and binding["element_id"] not in native_role_ids):
                    lines = source_text_lines(node)
                    if lines is not None:
                        # The converter flattens positional tspans to one shape per line.
                        if not fragment_lines_match(lines, binding["text"]):
                            raise ValueError("svg_source_pptx_text_line_mismatch")
                        ordinary_lines[binding["element_id"]] = lines
        expected_text = Counter(_expected_visible_text(binding["text"]) for binding in text_bindings
                                if binding.get("block_id") not in native_blocks
                                and binding["element_id"] not in native_role_ids
                                and binding["element_id"] not in fragment_ids
                                and binding["element_id"] not in ordinary_lines)
        expected_text.update(_expected_visible_text(line) for lines in ordinary_lines.values() for line in lines)
        expected_text.update(fragment_text)
        furniture = receipt.get("_export_furniture") or {}
        furniture_text = Counter(value for text in furniture.get("texts", []) if (value := _readback_text(text)))
        expected_text.update(furniture_text)
        actual_text = Counter(
            value for shape in shapes if shape.has_text_frame
            if (value := _readback_text(shape.text_frame.text))
        )
        if actual_text != expected_text:
            raise ValueError("svg_source_pptx_text_readback_mismatch")

        image_bindings = [binding for binding in receipt["bindings"] if binding["kind"] == "image"]
        if set(image_digests) != {binding["slot_id"] for binding in image_bindings}:
            raise ValueError("svg_source_pptx_image_expectation_mismatch")
        expected_images = Counter(image_digests[binding["slot_id"]] for binding in image_bindings)
        furniture_images = Counter(furniture.get("image_digests", []))
        expected_images.update(furniture_images)
        actual_images = Counter(
            hashlib.sha256(shape.image.blob).hexdigest()
            for shape in shapes if shape.shape_type == MSO_SHAPE_TYPE.PICTURE
        )
        if actual_images != expected_images:
            raise ValueError("svg_source_pptx_image_readback_mismatch")

        table_bindings = [binding for binding in receipt["bindings"] if binding["kind"] == "table_cell"]
        table_shapes = [shape for shape in shapes if shape.has_table]
        pages.append({
            "sourcePageId": receipt["source_page_id"],
            "sourcePageIndex": index,
            "svgDigest": receipt["svg_digest"],
            "text": {
                "representation": "native-text-shapes",
                "expected": [{"sourceId": binding["source_id"],
                    "valueDigest": _binding_digest(binding["text"])} for binding in text_bindings],
                "matchedBindingCount": len(text_bindings),
                "blankBindingCount": sum(
                    binding["kind"] in {"text", "table_cell"} and not binding["text"]
                    for binding in receipt["bindings"]
                ),
            },
            "tables": {
                "representation": "native-tables" if native_blocks else "native-text-and-shape-cells",
                "nativeTableShapeCount": len(table_shapes),
                "nativeTableEditabilityVerified": bool(native_blocks) and native_blocks == {
                    binding["block_id"] for binding in table_bindings},
                "verifiedNativeTables": native_table_evidence,
                "expectedCells": [{
                    "sourceId": binding["source_id"],
                    "row": binding["row"],
                    "column": binding["column"],
                    "rowSpan": binding["row_span"],
                    "columnSpan": binding["column_span"],
                    "valueDigest": _binding_digest(binding["text"]),
                } for binding in table_bindings],
                "matchedCellValueCount": len(table_bindings),
            },
            "images": {
                "representation": "native-picture-shapes",
                "expectedOccurrences": [{
                    "sourceId": binding["source_id"],
                    "slotId": binding["slot_id"],
                    "sourceAssetDigest": binding["asset_digest"],
                    "exportAssetDigest": "sha256:" + image_digests[binding["slot_id"]],
                } for binding in image_bindings],
                "matchedOccurrenceCount": len(image_bindings),
            },
            **({"nativeCharts": {"verified": native_chart_evidence,
                                  "nativeBindingCount": len(native_role_ids)}} if whole_page else {}),
            **({"editableSourceFragments": fragment_count} if whole_page else {}),
        })
        if furniture:
            pages[-1]["systemFurniture"] = {
                "digest": furniture["digest"],
                "textShapeCount": sum(furniture_text.values()),
                "imageShapeCount": sum(furniture_images.values()),
                "valuesVerified": True,
            }
    return {
        "schemaVersion": "svg-source-preservation-export-readback/v1",
        "passed": True,
        "sourceDigest": receipts[0]["source_digest"],
        "manifestDigest": receipts[0]["manifest_digest"],
        "pages": pages,
    }
