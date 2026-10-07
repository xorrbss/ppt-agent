"""Isolated whole-page overlay projection over the frozen PPTX converter."""
from __future__ import annotations

import re
import sys
import hashlib
from html import escape

from services.svg_export_whole_page_adapter import (
    bind_chart_text_projection, bind_source_text_fragments, fragment_shape_name,
    project_source_table_end_alignment,
)
from workers.public_svg_pptx_convert import VENDOR_ROOT, convert_pptx


_CHART_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_BINDING_NS = "urn:presenton:svg-source-chart-binding:v1"
_BINDING_URI = "{4e7bc59a-8a06-4a9f-9c7e-4eeb9930a54f}"


def _extension(parent, records, role):
    from lxml import etree

    ext_list = parent.find(f"{{{_CHART_NS}}}extLst")
    if ext_list is None:
        ext_list = etree.SubElement(parent, f"{{{_CHART_NS}}}extLst")
    if any(item.get("uri") == _BINDING_URI for item in ext_list):
        raise ValueError("svg_source_export_chart_binding_duplicate")
    ext = etree.SubElement(ext_list, f"{{{_CHART_NS}}}ext", uri=_BINDING_URI)
    for record in records:
        etree.SubElement(ext, f"{{{_BINDING_NS}}}sourceBinding",
                         role=role, sourceId=record["sourceId"],
                         **{key: str(value) for key, value in record.items()
                            if key != "sourceId"})


def _bind_chart_sources(chart, claim):
    series = chart._chartSpace.xpath(".//c:ser")
    values = claim["nativeValueIds"]
    categories = claim["nativeCategoryIds"]
    legends = claim["nativeLegendIds"]
    if (len(series) != len(legends)
            or {record["seriesIndex"] for record in legends} != set(range(len(series)))):
        raise ValueError("svg_source_export_chart_series_binding_invalid")
    legend_by_index = {record["seriesIndex"]: record for record in legends}
    for series_index, item in enumerate(series):
        legend = item.xpath("./c:tx/c:strRef")
        cat = item.xpath("./c:cat/c:strRef|./c:cat/c:numRef")
        val = item.xpath("./c:val/c:numRef")
        label_sets = item.xpath("./c:dLbls")
        if any(len(found) != 1 for found in (legend, cat, val, label_sets)):
            raise ValueError("svg_source_export_chart_dynamic_reference_missing")
        _extension(legend[0], [legend_by_index[series_index]], "seriesLegend")
        _extension(cat[0], categories, "category")
        scoped = [record for record in values if record["seriesIndex"] == series_index]
        _extension(val[0], scoped, "valueSeries")
        point_labels = label_sets[0].xpath("./c:dLbl")
        by_index = {}
        for label in point_labels:
            indices = label.xpath("./c:idx")
            if len(indices) != 1 or not (indices[0].get("val") or "").isdecimal():
                raise ValueError("svg_source_export_chart_point_index_invalid")
            index = int(indices[0].get("val"))
            if index in by_index:
                raise ValueError("svg_source_export_chart_point_index_duplicate")
            by_index[index] = label
        if set(by_index) != {record["categoryIndex"] for record in scoped}:
            raise ValueError("svg_source_export_chart_point_coverage_invalid")
        for record in scoped:
            _extension(by_index[record["categoryIndex"]], [record], "valuePoint")


def _apply_source_grid_width(chart, claim, *, slide_width_emu, canvas_width_px):
    expected = claim.get("gridStyle")
    if expected is None:
        return
    widths = expected.get("sourceWidthsPx")
    if (expected.get("nativeWidthPolicy") != "source-scale/v1"
            or not isinstance(widths, list) or len(widths) != 1):
        raise ValueError("svg_source_export_grid_width_unsupported")
    width_emu = round(widths[0] * slide_width_emu / canvas_width_px)
    if not 1 <= width_emu <= 20116800:
        raise ValueError("svg_source_export_grid_width_invalid")
    lines = chart._chartSpace.xpath(".//c:majorGridlines/c:spPr/a:ln")
    if not lines:
        raise ValueError("svg_source_export_grid_style_missing")
    for line in lines:
        line.set("w", str(width_emu))
    expected.update(expectedNativeWidthEmu=width_emu, expectedNativeWidthPt=width_emu / 12700)


def _embed_verified_whole_page_fonts(target):
    """Embed licensed trusted faces after the last save and bind final package bytes."""
    import zipfile
    from xml.etree import ElementTree as ET
    from services.svg_pptx_font_embedding import embed_trusted_fonts, FONT_CONTENT_TYPE, FONT_RELATIONSHIP_TYPE
    from services.svg_trusted_fonts import FONT_FAMILY, read_trusted_pretendard_faces

    faces = read_trusted_pretendard_faces()
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("svg_source_export_font_duplicate_part")
        before = {name: archive.read(name) for name in names}
    digests = embed_trusted_fonts(target)
    if tuple(digest for _, digest, _ in faces) != digests:
        raise ValueError("svg_source_export_font_identity_mismatch")
    with zipfile.ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise ValueError("svg_source_export_font_package_invalid")
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("svg_source_export_font_duplicate_part")
        after = {name: archive.read(name) for name in names}
    modified = {"ppt/presentation.xml", "ppt/_rels/presentation.xml.rels", "[Content_Types].xml"}
    if any(after.get(name) != payload for name, payload in before.items() if name not in modified):
        raise ValueError("svg_source_export_font_source_parts_changed")
    p = "http://schemas.openxmlformats.org/presentationml/2006/main"
    r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    rels_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    ct = "http://schemas.openxmlformats.org/package/2006/content-types"
    presentation = ET.fromstring(after["ppt/presentation.xml"])
    lists = presentation.findall(f"{{{p}}}embeddedFontLst")
    if presentation.get("embedTrueTypeFonts") != "1" or len(lists) != 1:
        raise ValueError("svg_source_export_font_list_invalid")
    fonts = lists[0].findall(f"{{{p}}}embeddedFont")
    children = list(presentation)
    if (len(fonts) != 1 or children.index(lists[0]) == 0
            or children[children.index(lists[0]) - 1].tag != f"{{{p}}}notesSz"
            or fonts[0].find(f"{{{p}}}font") is None
            or fonts[0].find(f"{{{p}}}font").get("typeface") != FONT_FAMILY):
        raise ValueError("svg_source_export_font_list_invalid")
    rels = ET.fromstring(after["ppt/_rels/presentation.xml.rels"])
    relationships = rels.findall(f"{{{rels_ns}}}Relationship")
    font_relationships = [item for item in relationships if item.get("Type") == FONT_RELATIONSHIP_TYPE]
    if len(font_relationships) != len(faces):
        raise ValueError("svg_source_export_font_relationship_invalid")
    parts = set()
    for weight, digest, payload in faces:
        slot = fonts[0].find(f"{{{p}}}" + {400: "regular", 700: "bold"}[weight])
        candidates = [item for item in relationships if slot is not None and item.get("Id") == slot.get(f"{{{r}}}id")]
        if len(candidates) != 1 or candidates[0].get("Type") != FONT_RELATIONSHIP_TYPE or candidates[0].get("TargetMode") is not None:
            raise ValueError("svg_source_export_font_relationship_invalid")
        part = "ppt/" + (candidates[0].get("Target") or "")
        if not re.fullmatch(r"ppt/fonts/font[0-9]+\.fntdata", part) or part in parts or after.get(part) != payload:
            raise ValueError("svg_source_export_font_payload_mismatch")
        parts.add(part)
    if set(after) != set(before) | parts or set(after) - set(before) != parts:
        raise ValueError("svg_source_export_font_unexpected_parts")
    defaults = ET.fromstring(after["[Content_Types].xml"]).findall(f"{{{ct}}}Default")
    if [item.get("ContentType") for item in defaults if item.get("Extension") == "fntdata"] != [FONT_CONTENT_TYPE]:
        raise ValueError("svg_source_export_font_content_type_invalid")
    return {"embedded": True, "family": FONT_FAMILY, "faceDigests": list(digests),
            "ooxmlVerified": True, "sourcePartsPreserved": True}


def _verified_final_native_ooxml(target, trace_path, source_originals, receipts, background_paints, expected_geometry, group_aliases):
    """Join the existing converter trace to every final editable OOXML shape."""
    import json
    import posixpath
    import zipfile
    from xml.etree import ElementTree as ET
    from services.svg_contract import source_digest
    from utils.hashing import canonical_json_sha256

    p = "http://schemas.openxmlformats.org/presentationml/2006/main"
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    relns = "http://schemas.openxmlformats.org/package/2006/relationships"
    c = "http://schemas.openxmlformats.org/drawingml/2006/chart"
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or archive.testzip() is not None:
            raise ValueError("svg_native_ooxml_package_invalid")
        parts = {name: archive.read(name) for name in names}
    relationship_maps = {}
    relationship_count = 0
    for name, payload in parts.items():
        if not name.endswith(".rels"):
            continue
        rels = ET.fromstring(payload)
        base = "" if name == "_rels/.rels" else posixpath.dirname(posixpath.dirname(name))
        mapping = {}
        for rel in rels.findall(f"{{{relns}}}Relationship"):
            rid, kind, destination = rel.get("Id"), rel.get("Type"), rel.get("Target")
            if not rid or rid in mapping or not kind or not destination or rel.get("TargetMode") is not None:
                raise ValueError("svg_native_ooxml_relationship_invalid")
            resolved = posixpath.normpath(posixpath.join(base, destination)).lstrip("/")
            if resolved.startswith("../") or resolved not in parts:
                raise ValueError("svg_native_ooxml_relationship_missing_part")
            mapping[rid] = (kind, resolved)
            relationship_count += 1
        relationship_maps[name] = mapping
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    trace_pages = trace.get("slides")
    if (trace.get("slide_count") != len(receipts) or not isinstance(trace_pages, list)
            or len(trace_pages) != len(receipts) or len(source_originals) != len(receipts)):
        raise ValueError("svg_native_ooxml_trace_pages_invalid")
    by_page = {page.get("slide_num"): page for page in trace_pages}
    if set(by_page) != set(range(1, len(receipts) + 1)):
        raise ValueError("svg_native_ooxml_trace_pages_invalid")
    def signature(node):
        return (node.tag, tuple(sorted(node.attrib.items())), (node.text or "").strip(), tuple(signature(child) for child in node))
    pages = []
    total_shapes = total_events = total_groups = total_aliases = 0
    for index, (source, receipt) in enumerate(zip(source_originals, receipts, strict=True)):
        if source_digest(source) != receipt["svg_digest"]:
            raise ValueError("svg_native_ooxml_source_binding_mismatch")
        slide_name = f"ppt/slides/slide{index + 1}.xml"
        slide = ET.fromstring(parts[slide_name])
        tree = slide.find(f"{{{p}}}cSld/{{{p}}}spTree")
        if tree is None:
            raise ValueError("svg_native_ooxml_slide_tree_missing")
        shapes = {}
        for node in tree.iter():
            local = node.tag.rsplit("}", 1)[-1]
            if node.tag.startswith("{" + p + "}") and local in {"cxnSp", "contentPart"}:
                raise ValueError("svg_native_ooxml_untraced_shape_type")
            if node.tag not in {f"{{{p}}}{kind}" for kind in ("sp", "pic", "graphicFrame", "grpSp")}:
                continue
            nv = next((child for child in node if child.tag.rsplit("}", 1)[-1].startswith("nv")), None)
            identity = None if nv is None else nv.find(f"{{{p}}}cNvPr")
            if identity is None or not (identity.get("id") or "").isdecimal():
                raise ValueError("svg_native_ooxml_shape_identity_invalid")
            sid = int(identity.get("id"))
            if sid in shapes:
                raise ValueError("svg_native_ooxml_shape_identity_duplicate")
            xpath = {"sp": f"{{{p}}}spPr/{{{a}}}xfrm", "pic": f"{{{p}}}spPr/{{{a}}}xfrm",
                     "graphicFrame": f"{{{p}}}xfrm", "grpSp": f"{{{p}}}grpSpPr/{{{a}}}xfrm"}[local]
            transform = node.find(xpath)
            if transform is None:
                raise ValueError("svg_native_ooxml_shape_transform_missing")
            off, extent = transform.find(f"{{{a}}}off"), transform.find(f"{{{a}}}ext")
            if off is None or extent is None:
                raise ValueError("svg_native_ooxml_shape_transform_missing")
            x, y, width, height = int(off.get("x")), int(off.get("y")), int(extent.get("cx")), int(extent.get("cy"))
            if width < 0 or height < 0 or not (width or height):
                raise ValueError("svg_native_ooxml_shape_transform_invalid")
            bounds = [x, y, x + width, y + height]
            if local == "grpSp":
                child_off, child_extent = transform.find(f"{{{a}}}chOff"), transform.find(f"{{{a}}}chExt")
                if child_off is None or child_extent is None:
                    raise ValueError("svg_native_ooxml_group_transform_missing")
                gx, gy = int(child_off.get("x")), int(child_off.get("y"))
                gw, gh = int(child_extent.get("cx")), int(child_extent.get("cy"))
                if gw <= 0 or gh <= 0 or (gw, gh) != (width, height):
                    raise ValueError("svg_native_ooxml_group_transform_invalid")
                bounds = [gx, gy, gx + gw, gy + gh]
                total_groups += 1
            if local == "sp" and node.find(f"{{{p}}}spPr/{{{a}}}prstGeom") is None and node.find(f"{{{p}}}spPr/{{{a}}}custGeom") is None:
                raise ValueError("svg_native_ooxml_editable_geometry_missing")
            if local == "graphicFrame":
                graphic = node.find(f"{{{a}}}graphic/{{{a}}}graphicData")
                if graphic is None or not any(child.tag in {f"{{{a}}}tbl", f"{{{c}}}chart"} for child in graphic):
                    raise ValueError("svg_native_ooxml_native_frame_invalid")
            relmap = relationship_maps.get(f"ppt/slides/_rels/slide{index + 1}.xml.rels", {})
            for descendant in node.iter():
                for key, rid in descendant.attrib.items():
                    if key.startswith("{" + r + "}") and rid not in relmap:
                        raise ValueError("svg_native_ooxml_shape_relationship_unbound")
            expected = expected_geometry.get(index + 1, {}).get(sid)
            geometry_tag = {"sp": f"{{{p}}}spPr", "pic": f"{{{p}}}spPr",
                            "graphicFrame": f"{{{p}}}xfrm", "grpSp": f"{{{p}}}grpSpPr"}[local]
            actual_geometry = node.find(geometry_tag)
            if expected is None or actual_geometry is None or signature(actual_geometry) != expected:
                raise ValueError("svg_native_ooxml_geometry_payload_mismatch")
            shapes[sid] = {"type": local, "boundsEmu": bounds,
                           "partDigest": "sha256:" + hashlib.sha256(ET.tostring(node)).hexdigest()}
        events = by_page[index + 1].get("events")
        if not isinstance(events, list) or not events:
            raise ValueError("svg_native_ooxml_trace_events_missing")
        covered = set()
        owner_tags = {}
        aliases_used = {}
        native_events = []
        backgrounds = 0
        for event in events:
            decision = event.get("decision")
            if decision == "skip" and event.get("reason") in {"non-visual", "empty-or-non-rendering"}:
                continue
            if decision == "native-background":
                backgrounds += 1
                actual_bg = slide.find(f"{{{p}}}cSld/{{{p}}}bg")
                paint = background_paints.get(index + 1)
                if actual_bg is None or not paint:
                    raise ValueError("svg_native_ooxml_background_unbound")
                expected_bg = ET.fromstring(f'<root xmlns:p="{p}" xmlns:a="{a}">' + paint + '</root>')[0]
                if signature(actual_bg) != signature(expected_bg):
                    raise ValueError("svg_native_ooxml_background_paint_mismatch")
                continue
            if decision != "native" or type(event.get("shape_id")) is not int:
                raise ValueError("svg_native_ooxml_visual_primitive_unbound")
            sid = event["shape_id"]
            actual = shapes.get(sid)
            expected = event.get("bounds_emu")
            if (actual is None or not isinstance(expected, list) or len(expected) != 4
                    or any(type(value) is not int for value in expected) or expected != actual["boundsEmu"]):
                raise ValueError("svg_native_ooxml_trace_geometry_mismatch")
            tag = event.get("tag")
            expected_type = {"image": "pic", "text": "sp", "rect": "sp", "circle": "sp",
                             "ellipse": "sp", "line": "sp", "path": "sp", "polygon": "sp", "polyline": "sp"}.get(tag)
            if tag != "g" and (expected_type is None or actual["type"] != expected_type):
                raise ValueError("svg_native_ooxml_shape_type_mismatch")
            if actual["type"] == "pic" and event.get("tag") not in {"image", "g"}:
                raise ValueError("svg_native_ooxml_visual_primitive_rasterized")
            if sid in covered:
                allowed_aliases = group_aliases.get(index + 1, {}).get(sid, 0)
                if (tag != "g" or aliases_used.get(sid, 0) >= allowed_aliases
                        or (actual["type"] == "pic" and owner_tags.get(sid) != "image")):
                    raise ValueError("svg_native_ooxml_primitive_owner_duplicate")
                aliases_used[sid] = aliases_used.get(sid, 0) + 1
                total_aliases += 1
            else:
                if actual["type"] == "pic" and tag != "image":
                    raise ValueError("svg_native_ooxml_group_raster_unproven")
                owner_tags[sid] = tag
            covered.add(sid)
            native_events.append({"tag": event.get("tag"), "shapeId": sid, "boundsEmu": expected})
        if set(shapes) != covered or backgrounds > 1:
            raise ValueError("svg_native_ooxml_shape_coverage_incomplete")
        pages.append({"pageIndex": index, "sourcePageId": receipt["source_page_id"], "svgDigest": receipt["svg_digest"],
                      "shapeCount": len(shapes), "nativePrimitiveCount": len(native_events),
                      "shapeInventoryDigest": "sha256:" + canonical_json_sha256(shapes),
                      "traceDigest": "sha256:" + canonical_json_sha256(native_events),
                      "promotedBackgroundCount": backgrounds})
        total_shapes += len(shapes)
        total_events += len(native_events)
    return {"schemaVersion": "svg-native-ooxml-coverage/v1", "passed": True,
            "artifactDigest": "sha256:" + hashlib.sha256(target.read_bytes()).hexdigest(),
            "sourceDigests": [receipt["svg_digest"] for receipt in receipts], "pages": pages,
            "measurements": {"verifiedPages": len(pages), "verifiedShapes": total_shapes,
                             "verifiedGroups": total_groups, "verifiedGroupAliases": total_aliases, "nativePrimitives": total_events,
                             "verifiedRelationships": relationship_count, "unboundShapes": 0,
                             "unboundPrimitives": 0, "geometryMismatches": 0},
            "packagePartsDigest": "sha256:" + canonical_json_sha256({name: hashlib.sha256(payload).hexdigest() for name, payload in parts.items()})}


def _source_style_readback(target, table_alignment_claims, chart_claims):
    """Measure final post-font OOXML alignment and source-proven grid colour."""
    from pptx import Presentation
    from pptx.enum.text import PP_ALIGN
    from utils.hashing import canonical_json_sha256
    deck = Presentation(str(target))
    tables, grids = [], []
    for page_index, claims in enumerate(table_alignment_claims):
        by_name = {shape.name: shape for shape in deck.slides[page_index].shapes if shape.has_table}
        for claim in claims:
            shape = by_name.get(claim["tableId"])
            if shape is None:
                raise ValueError("svg_source_export_table_style_owner_missing")
            cell = shape.table.cell(claim["row"], claim["column"])
            if cell.is_spanned or any(paragraph.alignment != PP_ALIGN.RIGHT for paragraph in cell.text_frame.paragraphs):
                raise ValueError("svg_source_export_table_alignment_mismatch")
            tables.append({"pageIndex": page_index, **claim, "ooxmlAlignment": "r"})
    for page_index, claims in enumerate(chart_claims):
        by_name = {shape.name: shape for shape in deck.slides[page_index].shapes if shape.has_chart}
        for claim in claims:
            expected = claim.get("gridStyle")
            if expected is None:
                continue
            shape = by_name.get(claim["chartId"])
            if shape is None:
                raise ValueError("svg_source_export_grid_style_owner_missing")
            lines = shape.chart._chartSpace.xpath(".//c:majorGridlines/c:spPr/a:ln")
            if not lines:
                raise ValueError("svg_source_export_grid_style_missing")
            for line in lines:
                colors = line.xpath("./a:solidFill/a:srgbClr")
                if (len(colors) != 1 or colors[0].get("val") != expected["sourceColor"]
                        or line.get("w") != str(expected["expectedNativeWidthEmu"])):
                    raise ValueError("svg_source_export_grid_style_mismatch")
            grids.append({"pageIndex": page_index, "chartId": claim["chartId"], **expected,
                          "actualNativeColor": expected["sourceColor"],
                          "actualNativeWidthsPt": [int(line.get("w")) / 12700 for line in lines],
                          "widthPreserved": True, "colorPreserved": True})
    result = {"schemaVersion": "svg-source-export-style-readback/v1", "passed": True,
              "rightAlignedCells": tables, "uniformGridCharts": grids}
    result["digest"] = "sha256:" + canonical_json_sha256(result)
    return result


def convert_whole_page_pptx(paths, target, notes, canvas, *, receipts,
                            native_objects, source_originals, font_embedding):
    if (len(paths) != len(receipts) or len(paths) != len(source_originals)
            or native_objects is None or len(paths) != len(native_objects)
            or type(font_embedding) is not bool):
        raise ValueError("svg_source_export_overlay_input_invalid")
    projected_pages, table_alignment_claims = [], []
    for page, source, receipt in zip(native_objects, source_originals, receipts, strict=True):
        projected_page, alignments = project_source_table_end_alignment(source, receipt, page)
        projected_pages.append(projected_page)
        table_alignment_claims.append(alignments)
    native_objects = projected_pages
    from services.generation_core.drivers.svg import export_scene

    sys.path.insert(0, str(VENDOR_ROOT))
    try:
        from svg_to_pptx.drawingml import converter as drawing_converter
        from svg_to_pptx.drawingml.context import ShapeResult
        from svg_finalize import flatten_tspan

        import svg_to_pptx
        original_create_pptx = svg_to_pptx.create_pptx_with_native_svg
        trace_path = target.with_suffix(".native-ooxml-trace.json")
        original_background = drawing_converter._extract_background_candidate
        original_convert_element = drawing_converter.convert_element
        background_paints = {}
        expected_geometry = {}
        expected_return_xml = {}
        group_aliases = {}

        def signature(node):
            return (node.tag, tuple(sorted(node.attrib.items())), (node.text or "").strip(), tuple(signature(child) for child in node))

        def traced_element(element, context):
            result = original_convert_element(element, context)
            if result is not None:
                from xml.etree import ElementTree as ET
                ns = "http://schemas.openxmlformats.org/presentationml/2006/main"
                drawing_ns = "http://schemas.openxmlformats.org/drawingml/2006/main"
                relation_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
                wrapper = ET.fromstring(f'<root xmlns:p="{ns}" xmlns:a="{drawing_ns}" xmlns:r="{relation_ns}">' + result.xml + '</root>')
                page = expected_geometry.setdefault(context.slide_num, {})
                returned_xml = expected_return_xml.setdefault(context.slide_num, {})
                if element.tag.rsplit("}", 1)[-1] == "g" and len(wrapper) == 1:
                    visual = [child for child in element if child.tag.rsplit("}", 1)[-1]
                              not in {"defs", "metadata", "title", "desc"}]
                    output = wrapper[0]
                    nv = next((child for child in output if child.tag.rsplit("}", 1)[-1].startswith("nv")), None)
                    identity = None if nv is None else nv.find(f"{{{ns}}}cNvPr")
                    if (len(visual) == 1 and identity is not None and int(identity.get("id")) in page
                            and returned_xml.get(int(identity.get("id"))) == signature(output)):
                        sid = int(identity.get("id"))
                        aliases = group_aliases.setdefault(context.slide_num, {})
                        aliases[sid] = aliases.get(sid, 0) + 1
                for node in wrapper.iter():
                    if node.tag not in {f"{{{ns}}}{tag}" for tag in ("sp", "pic", "graphicFrame", "grpSp")}:
                        continue
                    nv = next((child for child in node if child.tag.rsplit("}", 1)[-1].startswith("nv")), None)
                    identity = None if nv is None else nv.find(f"{{{ns}}}cNvPr")
                    if identity is None:
                        raise ValueError("svg_native_ooxml_expected_identity_missing")
                    sid = int(identity.get("id"))
                    kind = node.tag.rsplit("}", 1)[-1]
                    geometry = node.find({"sp": f"{{{ns}}}spPr", "pic": f"{{{ns}}}spPr",
                                          "graphicFrame": f"{{{ns}}}xfrm", "grpSp": f"{{{ns}}}grpSpPr"}[kind])
                    if geometry is None:
                        raise ValueError("svg_native_ooxml_expected_geometry_missing")
                    value = signature(geometry)
                    if sid in page and page[sid] != value:
                        raise ValueError("svg_native_ooxml_expected_geometry_conflict")
                    page[sid] = value
                    node_signature = signature(node)
                    if sid in returned_xml and returned_xml[sid] != node_signature:
                        raise ValueError("svg_native_ooxml_expected_result_conflict")
                    returned_xml[sid] = node_signature
            return result

        def traced_background(root, context):
            result = original_background(root, context)
            if result[0]:
                background_paints[context.slide_num] = result[0]
            return result

        def traced_create_pptx(*args, **kwargs):
            kwargs["conversion_trace_path"] = trace_path
            return original_create_pptx(*args, **kwargs)

        original_prepare = export_scene.prepare_svg_pptx_inputs
        original_text = drawing_converter._CONVERTERS["text"]
        original_copy_text_attrs = flatten_tspan.copy_text_attrs
        original_create_line = flatten_tspan._create_text_element_from_line
        claims = []
        named = {}
        fragment_sources = {}
        named_fragments = {}

        def prepared(page_paths, pages, *, width, height, pt_per_px):
            scene = original_prepare(page_paths, pages, width=width, height=height,
                                     pt_per_px=pt_per_px)
            for page_index, path in enumerate(page_paths):
                claims.append(bind_chart_text_projection(
                    path, canonical_svg=source_originals[page_index],
                    receipt=receipts[page_index], native_page=pages[page_index],
                    page_index=page_index))
                for record in bind_source_text_fragments(
                        path, canonical_svg=source_originals[page_index],
                        receipt=receipts[page_index], page_index=page_index):
                    for fragment in record["fragments"]:
                        key = (page_index, record["sourceId"], fragment["index"])
                        if key in fragment_sources:
                            raise ValueError("svg_source_export_fragment_duplicate")
                        fragment_sources[key] = fragment
            return scene

        def named_text(element, context):
            result = original_text(element, context)
            fragment_id = element.get("data-pptx-source-fragment-binding-id")
            if fragment_id is not None:
                if element.get("data-pptx-source-binding-id") is not None:
                    raise ValueError("svg_source_export_fragment_marker_conflict")
                page = element.get("data-pptx-source-fragment-page-index")
                index = element.get("data-pptx-source-fragment-index")
                if (page is None or index is None or not page.isdecimal()
                        or not index.isdecimal()):
                    raise ValueError("svg_source_export_fragment_marker_invalid")
                key = (int(page), fragment_id, int(index))
                source = fragment_sources.get(key)
                if result is None or result.bounds_emu is None or source is None or key in named_fragments:
                    raise ValueError("svg_source_export_fragment_conversion_invalid")
                if (abs(float(element.get("x")) - source["x"]) > 1e-6
                        or abs(float(element.get("y")) - source["y"]) > 1e-6
                        or hashlib.sha256((element.text or "").strip().encode("utf-8")).hexdigest()
                           != source["textDigest"]):
                    raise ValueError("svg_source_export_fragment_projection_invalid")
                shape_name = fragment_shape_name(*key)
                replaced, count = re.subn(
                    r'(<p:cNvPr id="\d+") name="TextBox \d+"',
                    lambda match: match.group(1) + f' name="{shape_name}" descr="{shape_name}"',
                    result.xml, count=1)
                if count != 1:
                    raise ValueError("svg_source_export_fragment_identity_lost")
                named_fragments[key] = {"sourceId": fragment_id,
                                        "fragmentIndex": int(index),
                                        "shapeName": shape_name,
                                        "sourceX": source["x"], "sourceY": source["y"],
                                        "textDigest": source["textDigest"],
                                        "boundsEmu": list(result.bounds_emu)}
                return ShapeResult(xml=replaced, bounds_emu=result.bounds_emu)
            source_id = element.get("data-pptx-source-binding-id")
            if source_id is None:
                return result
            page_index = element.get("data-pptx-source-page-index")
            if (page_index is None or not page_index.isdecimal()
                    or int(page_index) >= len(paths)):
                raise ValueError("svg_source_export_overlay_page_invalid")
            key = (int(page_index), source_id)
            if result is None or key in named or result.bounds_emu is None:
                raise ValueError("svg_source_export_overlay_conversion_invalid")
            shape_name = escape(source_id, quote=True)
            replaced, count = re.subn(
                r'(<p:cNvPr id="\d+") name="TextBox \d+"',
                lambda match: match.group(1) + f' name="{shape_name}" descr="{shape_name}"',
                result.xml, count=1)
            if count != 1:
                raise ValueError("svg_source_export_overlay_identity_lost")
            named[key] = result.bounds_emu
            return ShapeResult(xml=replaced, bounds_emu=result.bounds_emu)

        def bound_copy_text_attrs(source, target, exclude=None):
            original_copy_text_attrs(source, target, exclude)
            source_id = source.get("data-pptx-source-binding-id")
            page_index = source.get("data-pptx-source-page-index")
            if (source_id is None) != (page_index is None):
                raise ValueError("svg_source_export_overlay_marker_partial")
            if source_id is not None:
                target.set("data-pptx-source-binding-id", source_id)
                target.set("data-pptx-source-page-index", page_index)

        def bound_create_line(text_el, lead_text, tspans, x, y):
            result = original_create_line(text_el, lead_text, tspans, x, y)
            source_id = text_el.get("data-pptx-source-fragment-binding-id")
            if source_id is None:
                return result
            page = text_el.get("data-pptx-source-fragment-page-index")
            if (page is None or not page.isdecimal() or lead_text
                    or len(tspans) != 1 or x is None or y is None):
                raise ValueError("svg_source_export_fragment_flatten_invalid")
            candidates = [(key, item) for key, item in fragment_sources.items()
                          if key[:2] == (int(page), source_id)
                          and abs(item["x"] - x) <= 1e-6
                          and abs(item["y"] - y) <= 1e-6]
            if len(candidates) != 1:
                raise ValueError("svg_source_export_fragment_line_ambiguous")
            key, item = candidates[0]
            if hashlib.sha256((result.text or "").strip().encode("utf-8")).hexdigest() != item["textDigest"]:
                raise ValueError("svg_source_export_fragment_line_text_invalid")
            result.set("data-pptx-source-fragment-binding-id", source_id)
            result.set("data-pptx-source-fragment-page-index", page)
            result.set("data-pptx-source-fragment-index", str(key[2]))
            return result

        drawing_converter.convert_element = traced_element
        drawing_converter._extract_background_candidate = traced_background
        svg_to_pptx.create_pptx_with_native_svg = traced_create_pptx
        export_scene.prepare_svg_pptx_inputs = prepared
        drawing_converter._CONVERTERS["text"] = named_text
        flatten_tspan.copy_text_attrs = bound_copy_text_attrs
        flatten_tspan._create_text_element_from_line = bound_create_line
        try:
            readback = convert_pptx(paths, target, notes, canvas,
                source_preservation=None, font_embedding=False,
                native_objects=native_objects)
        finally:
            drawing_converter.convert_element = original_convert_element
            drawing_converter._extract_background_candidate = original_background
            svg_to_pptx.create_pptx_with_native_svg = original_create_pptx
            flatten_tspan._create_text_element_from_line = original_create_line
            flatten_tspan.copy_text_attrs = original_copy_text_attrs
            drawing_converter._CONVERTERS["text"] = original_text
            export_scene.prepare_svg_pptx_inputs = original_prepare
        expected = [(page_index, binding) for page_index, page in enumerate(claims)
                    for chart in page for binding in chart["overlayIds"]]
        if sorted(named) != sorted(expected):
            raise ValueError("svg_source_export_overlay_identity_incomplete")
        if set(named_fragments) != set(fragment_sources):
            raise ValueError("svg_source_export_fragment_identity_incomplete")
        for page_index, page_claims in enumerate(claims):
            for claim in page_claims:
                claim["overlayBoundsEmu"] = {
                    binding: list(named[(page_index, binding)])
                    for binding in claim["overlayIds"]}
        from pptx import Presentation
        from pptx.enum.chart import XL_TICK_LABEL_POSITION

        deck = Presentation(str(target))
        for page_index, page_claims in enumerate(claims):
            charts = [shape for shape in deck.slides[page_index].shapes if shape.has_chart]
            by_name = {shape.name: shape for shape in charts}
            if len(by_name) != len(charts) or set(by_name) != {
                    claim["chartId"] for claim in page_claims}:
                raise ValueError("svg_source_export_native_chart_owner_mismatch")
            for claim in page_claims:
                chart = by_name[claim["chartId"]].chart
                chart.category_axis.tick_label_position = XL_TICK_LABEL_POSITION.NEXT_TO_AXIS
                for labels in chart._chartSpace.xpath(".//c:dLbls"):
                    labels.get_or_add_showVal().val = True
                _bind_chart_sources(chart, claim)
                _apply_source_grid_width(chart, claim, slide_width_emu=deck.slide_width,
                                         canvas_width_px=canvas.width_px)
        deck.save(str(target))
        if font_embedding:
            readback["fontEmbedding"] = _embed_verified_whole_page_fonts(target)
        fragments = [[named_fragments[key] for key in sorted(named_fragments)
                      if key[0] == page_index] for page_index in range(len(paths))]
        readback["sourceStyleReadback"] = _source_style_readback(target, table_alignment_claims, claims)
        readback["nativeCoverageOoxml"] = _verified_final_native_ooxml(
            target, trace_path, source_originals, receipts, background_paints, expected_geometry, group_aliases)
        from utils.hashing import canonical_json_sha256
        style_readback = readback["sourceStyleReadback"]
        readback["nativeCoverageOoxml"]["sourceStyleReadbackDigest"] = "sha256:" + canonical_json_sha256(style_readback)
        readback["nativeCoverageOoxml"]["measurements"].update(
            verifiedRightAlignedCells=len(style_readback["rightAlignedCells"]),
            verifiedSourceGridColorCharts=len(style_readback["uniformGridCharts"]))
        return readback, claims, fragments
    finally:
        sys.path.remove(str(VENDOR_ROOT))
