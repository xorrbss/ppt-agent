"""Source-bound editable free SVG headers; v1 remains a frozen replay path."""
from __future__ import annotations

import base64
import copy
import math
import re
import xml.etree.ElementTree as ET

from services.svg_common_header import (
    GROUP_ID, SVG_NS, _MISSING, _canvas, _digest, _font, _line_evidence,
    _slides, _value, _wrap, common_header_source_digest,
)
from services.svg_common_header_space_advance import space_advance_evidence, validate_space_advance_evidence
from services.svg_common_header_v2_geometry import contains, intersects, paint_boxes
from services.svg_contract import _numbers, validate_svg
from services.svg_master_furniture import master_furniture
from utils.hashing import sha256_prefixed

VERSION = "svg-common-header/v2"
PAGE_VERSION = "svg-common-header-page/v2"
MEASUREMENT_VERSION = "svg-common-header-glyph-paint/v4"
# Frozen contracts keep their own measurement: v2 face advances, v3 the sealed
# space constant. v4 seals the rendering browser's measured space advance.
_RECOGNIZED_MEASUREMENT_VERSIONS = (MEASUREMENT_VERSION, "svg-common-header-glyph-paint/v3",
                                    "svg-common-header-glyph-paint/v2")
MARKER_ID = GROUP_ID + "-v2-contract"
_BOX_KEYS = ("x", "y", "width", "height")


def common_header_group_digest(group: ET.Element) -> str:
    def structure(node):
        return [node.tag, sorted(node.attrib.items()), node.text or "", [structure(child) for child in node]]
    return _digest(structure(group))


def _box(value, canvas):
    if (not isinstance(value, dict) or set(value) != set(_BOX_KEYS)
            or any(not isinstance(value[k], (int, float)) or isinstance(value[k], bool)
                   or not math.isfinite(value[k]) for k in _BOX_KEYS)
            or value["width"] <= 0 or value["height"] <= 0
            or not contains({"x": 0, "y": 0, **canvas}, value)):
        raise ValueError("svg_common_header_geometry_invalid")
    return dict(value)


def _number(value, *, positive=True):
    if (not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value)
            or value < 0 or (positive and value == 0)):
        raise ValueError("svg_common_header_geometry_invalid")
    return value


def _field(raw, canvas):
    if not isinstance(raw, dict) or not all(isinstance(raw.get(key), str) and raw[key]
                                          for key in ("node_id", "role", "value_ref")):
        raise ValueError("svg_common_header_field_invalid")
    # Models copy the style's CSS stack ("Noto Sans KR, Malgun Gothic, ...");
    # resolve it to the bundled face it names, as the v1 header and the page
    # body already do, instead of failing the deck on the stack string.
    family = raw.get("font_family")
    if isinstance(family, str) and "," in family:
        from services.svg_trusted_fonts import bundled_svg_font_family

        family = bundled_svg_font_family(family)
    font = _font(family, raw.get("font_weight"), _number(raw.get("font_size_px")))
    if raw["role"] == "title" and font["size_px"] < 20:
        raise ValueError("svg_common_header_physical_readability_invalid")
    physical = canvas.get("physical_size", {})
    emu_width = physical.get("widthEmu", physical.get("width_emu"))
    point_scale = emu_width / canvas["width"] / 12700 if emu_width else 72 / 96
    if font["size_px"] * point_scale < 9:
        raise ValueError("svg_common_header_physical_readability_invalid")
    lines = raw.get("max_lines", 2 if raw["role"] == "title" else 1)
    if type(lines) is not int or not 1 <= lines <= 2:
        raise ValueError("svg_common_header_field_invalid")
    alignment = raw.get("alignment", "left")
    if alignment not in {"left", "center", "right"} or type(raw.get("optional", False)) is not bool:
        raise ValueError("svg_common_header_field_invalid")
    dependencies = raw.get("dependent_node_ids", [])
    if not isinstance(dependencies, list) or any(not isinstance(item, str) for item in dependencies):
        raise ValueError("svg_common_header_field_invalid")
    line_height = _number(raw.get("line_height_px", font["size_px"] * 1.4))
    if line_height < font["size_px"]:
        raise ValueError("svg_common_header_geometry_invalid")
    return {"node_id": raw["node_id"], "role": raw["role"], "value_ref": raw["value_ref"],
            "box": _box(raw.get("box"), {k: canvas[k] for k in ("width", "height")}), "font": font,
            "line_height_px": line_height, "baseline_offset_px": _number(raw.get("baseline_offset_px", font["size_px"])),
            "max_lines": lines, "alignment": alignment, "optional": raw.get("optional", False),
            "dependent_node_ids": list(dependencies)}


def _actual_values(slide, index, count, supplied):
    page_id = _value(slide, "page_id")
    title = _value(slide, "title")
    content_digest = common_header_source_digest(_value(slide, "content", ""))
    if not isinstance(title, str) or not title.strip():
        raise ValueError("svg_common_header_title_missing")
    if supplied is not None:
        if not isinstance(supplied, dict) or not isinstance(supplied.get("values"), dict):
            raise ValueError("svg_common_header_value_source_invalid")
        if page_id and supplied.get("page_id") != page_id:
            raise ValueError("svg_common_header_page_id_mismatch")
        page_id = supplied.get("page_id")
    if not isinstance(page_id, str) or not page_id:
        raise ValueError("svg_common_header_page_id_missing")
    values = {"title": {"value": title, "source_ref": _value(slide, "title_source_digest") or content_digest, "user_fixed": False},
              "page_number": {"value": str(index + 1), "source_ref": "page-order:" + page_id, "user_fixed": False},
              "page_count": {"value": str(count), "source_ref": "document-page-count", "user_fixed": False}}
    if supplied:
        for key, raw in supplied["values"].items():
            if (not isinstance(key, str) or not isinstance(raw, dict)
                    or not isinstance(raw.get("value"), str) or not isinstance(raw.get("source_ref"), str)
                    or not raw["source_ref"] or type(raw.get("user_fixed", False)) is not bool):
                raise ValueError("svg_common_header_value_source_invalid")
            if key in values and raw["value"] != values[key]["value"]:
                raise ValueError("svg_common_header_value_source_mismatch")
            values[key] = {"value": raw["value"], "source_ref": raw["source_ref"], "user_fixed": raw.get("user_fixed", False)}
    return page_id, values


def _template(candidate, fields, geometry, snapshot):
    svg = candidate.get("svg")
    if not isinstance(svg, str) or len(svg.encode("utf-8")) > 256 * 1024 or re.search(r"<!|<\?", svg):
        raise ValueError("svg_common_header_template_invalid")
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as exc:
        raise ValueError("svg_common_header_template_invalid") from exc
    if (root.tag != "{" + SVG_NS + "}svg"
            or _numbers(root.get("viewBox", "")) != [0, 0, geometry["width"], geometry["height"]]
            or _numbers(root.get("width", "")) != [geometry["width"]]
            or _numbers(root.get("height", "")) != [geometry["height"]]):
        raise ValueError("svg_common_header_canvas_mismatch")
    nodes = list(root.iter())
    if len(nodes) > 2048:
        raise ValueError("svg_common_header_complexity_limit")
    field_ids = {field["node_id"] for field in fields}
    ids, assets, sources = set(), {}, {}
    admitted = {}
    def collect(value):
        if isinstance(value, dict):
            if value.get("data_url") and value.get("asset_digest"):
                admitted[value["asset_digest"]] = value["data_url"]
            for part in value.values():
                collect(part)
        elif isinstance(value, list):
            for part in value:
                collect(part)
    collect(snapshot)
    asset_inputs = candidate.get("assets", {})
    if not isinstance(asset_inputs, dict):
        raise ValueError("svg_common_header_asset_invalid")
    for position, node in enumerate(nodes):
        node_id = node.get("id") or f"node-{position}"
        if (not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,60}", node_id)
                or node_id in ids or node_id.startswith("svg-system-") or node_id == "v2-contract"):
            raise ValueError("svg_common_header_element_id_invalid")
        ids.add(node_id)
        node.set("id", node_id)
        name = node.tag.removeprefix("{" + SVG_NS + "}")
        if name == "tspan" or (name == "text" and (node_id not in field_ids or len(node) or (node.text or "").strip())):
            raise ValueError("svg_common_header_unbound_text")
        if name == "image":
            asset = asset_inputs.get(node_id)
            if (not isinstance(asset, dict) or not isinstance(asset.get("source_ref"), str) or not asset["source_ref"]
                    or asset.get("asset_digest") not in admitted or asset.get("data_url") != admitted[asset["asset_digest"]]):
                raise ValueError("svg_common_header_asset_source_invalid")
            url = asset["data_url"]
            try:
                digest = sha256_prefixed(base64.b64decode(url.split(",", 1)[1], validate=True))
            except (ValueError, IndexError) as exc:
                raise ValueError("svg_common_header_asset_invalid") from exc
            if digest != asset["asset_digest"]:
                raise ValueError("svg_common_header_asset_invalid")
            node.set("href", url)
            assets[GROUP_ID + "-" + node_id] = digest
            sources[GROUP_ID + "-" + node_id] = asset["source_ref"]
    if not field_ids <= ids or len(fields) != len(field_ids):
        raise ValueError("svg_common_header_field_invalid")
    for field in fields:
        target = next(node for node in nodes if node.get("id") == field["node_id"])
        if target.tag != "{" + SVG_NS + "}text":
            raise ValueError("svg_common_header_field_invalid")
        if set(target.attrib) - {"id", "fill", "x", "y", "font-family", "font-size", "font-weight", "text-anchor", "transform"}:
            raise ValueError("svg_common_header_field_attributes_invalid")
        for dependency in field["dependent_node_ids"]:
            dependent = next((node for node in nodes if node.get("id") == dependency), None)
            if dependent is None or any(node.get("id") in field_ids for node in dependent.iter()):
                raise ValueError("svg_common_header_field_dependency_invalid")
    def visible_text(node, opacity=1.):
        opacity *= float(node.get("opacity", "1")) * float(node.get("fill-opacity", "1"))
        if node.get("id") in field_ids and opacity < 1:
            raise ValueError("svg_common_header_invisible_text")
        for child in node:
            visible_text(child, opacity)
    visible_text(root)
    return ET.tostring(root, encoding="unicode"), assets, sources


def _render(contract, binding):
    root = ET.fromstring(contract["svg_template"])
    field_map = {field["node_id"]: field for field in contract["fields"]}
    values = {field["node_id"]: field for field in binding["fields"]}
    omitted = {dependency for field in contract["fields"] if not values[field["node_id"]]["value"]
               for dependency in field["dependent_node_ids"]}
    for parent in root.iter():
        for child in list(parent):
            if child.get("id") in omitted:
                parent.remove(child)
    for node in list(root.iter()):
        original_id = node.get("id")
        node.set("id", GROUP_ID + "-" + original_id)
        if original_id not in field_map:
            continue
        field, bound = field_map[original_id], values[original_id]
        # Text ownership and geometry are explicit and independent of LLM text attributes.
        transform = node.get("transform")
        node.tag = "{" + SVG_NS + "}g"
        node.attrib.clear()
        node.set("id", GROUP_ID + "-" + original_id)
        if transform:
            node.set("transform", transform)
        font, box = field["font"], field["box"]
        for index, text in enumerate(bound["lines"]):
            x = box["x"] + {"left": 0, "center": box["width"] / 2, "right": box["width"]}[field["alignment"]]
            attrs = {"id": node.get("id") + f"-line-{index + 1}", "x": str(x),
                     "y": str(box["y"] + field["baseline_offset_px"] + index * field["line_height_px"]),
                     "font-family": font["family"], "font-size": str(font["size_px"]),
                     "font-weight": str(font["weight"]), "text-anchor": {"left": "start", "center": "middle", "right": "end"}[field["alignment"]],
                     "fill": field["fill"]}
            ET.SubElement(node, "{" + SVG_NS + "}text", attrs).text = text
    group = ET.Element("{" + SVG_NS + "}g", {"id": GROUP_ID})
    ET.SubElement(group, "{" + SVG_NS + "}g", {"id": MARKER_ID})
    # Preserve supported root paint/translate attributes while discarding canvas attributes.
    root.tag = "{" + SVG_NS + "}g"
    for key in ("width", "height", "viewBox"):
        root.attrib.pop(key, None)
    group.append(root)
    return group


def _measure(contract, binding):
    if not binding["applied"]:
        return {}, None
    group = _render(contract, binding)
    text_boxes = {}
    for field in binding["fields"]:
        for index, box in enumerate(field["glyph_boxes"]):
            text_boxes[GROUP_ID + "-" + field["node_id"] + f"-line-{index + 1}"] = [box]
    boxes = paint_boxes(group, text_boxes)
    canvas = {"x": 0, "y": 0, "width": contract["canvas"]["width"], "height": contract["canvas"]["height"]}
    header_box = {**canvas, "height": contract["content_top_px"]}
    for extent in (box for values in boxes.values() for box in values):
        if not contains(header_box, extent) or intersects(extent, contract["body_box"]):
            raise ValueError("svg_common_header_paint_outside_header")
        if any(intersects(extent, reserved) for reserved in contract["brand_reservations"]):
            raise ValueError("svg_common_header_brand_overlap")
    text_ids = list(text_boxes)
    for position, first in enumerate(text_ids):
        for second in text_ids[position + 1:]:
            if any(intersects(a, b) for a in boxes[first] for b in boxes[second]):
                raise ValueError("svg_common_header_text_overlap")
    page = ET.Element("{" + SVG_NS + "}svg", {"width": str(canvas["width"]), "height": str(canvas["height"]),
        "viewBox": f"0 0 {canvas['width']} {canvas['height']}"})
    page.append(group)
    validate_svg(ET.tostring(page, encoding="unicode"), width=canvas["width"], height=canvas["height"])
    return boxes, common_header_group_digest(group)


def prepare_common_header_v2(candidate, outline, canvas, *, snapshot, cover_exception=False, page_values=None):
    """Freeze one candidate and every actual page binding, without modifying inputs."""
    if (not isinstance(candidate, dict) or not isinstance(candidate.get("candidate_id"), str)
            or not candidate["candidate_id"] or type(candidate.get("revision")) is not int or candidate["revision"] < 1
            or not isinstance(candidate.get("fields"), list) or type(cover_exception) is not bool):
        raise ValueError("svg_common_header_candidate_invalid")
    slides, geometry = _slides(outline), _canvas(canvas)
    if page_values is not None and (not isinstance(page_values, list) or len(page_values) != len(slides)):
        raise ValueError("svg_common_header_value_source_invalid")
    fields = [_field(raw, geometry) for raw in candidate["fields"]]
    if sum(field["role"] == "title" and field["value_ref"] == "title" and not field["optional"] for field in fields) != 1:
        raise ValueError("svg_common_header_title_field_required")
    body = _box(candidate.get("body_box"), {k: geometry[k] for k in ("width", "height")})
    if (any(field["value_ref"] == "page_number" for field in fields)
            and any(item.get("kind") == "pageNumber" and item.get("visibility") != "hidden"
                    for item in master_furniture(snapshot).get("elements", []))):
        raise ValueError("svg_common_header_page_number_owned_by_master")
    template, assets, sources = _template(candidate, fields, geometry, snapshot)
    template_root = ET.fromstring(template)
    for field in fields:
        node = next(node for node in template_root.iter() if node.get("id") == field["node_id"])
        fill = node.get("fill")
        if not isinstance(fill, str) or fill == "none":
            raise ValueError("svg_common_header_field_fill_required")
        field["fill"] = fill
    reservations = [{key: item.get(key, 0) for key in _BOX_KEYS}
                    for item in master_furniture(snapshot).get("elements", [])
                    if item.get("visibility") != "hidden" and item.get("kind") != "pageNumber"]
    if any(intersects(body, reservation) for reservation in reservations):
        raise ValueError("svg_common_header_body_brand_overlap")
    space_advance = space_advance_evidence([field["font"] for field in fields])
    contract = {"version": VERSION, "origin": "generated-common-header", "candidate_id": candidate["candidate_id"],
        "revision": candidate["revision"], "canvas": geometry, "svg_template": template, "fields": fields,
        "body_box": body, "content_top_px": body["y"], "cover_exception": cover_exception,
        "measurement_version": MEASUREMENT_VERSION, "space_advance": space_advance, "fonts": [dict(font) for font in {f["font"]["digest"] + str(f["font"]["weight"]): f["font"] for f in fields}.values()],
        "assets": assets, "asset_sources": sources, "brand_reservations": reservations,
        "design_intent": copy.deepcopy(candidate.get("design_intent", "")),
        "shared_style": copy.deepcopy(candidate.get("shared_style", {}))}
    contract["digest"] = _digest(contract)
    bindings, page_ids = [], set()
    represented_refs = {field["value_ref"] for field in fields}
    for index, slide in enumerate(slides):
        page_id, values = _actual_values(slide, index, len(slides), page_values[index] if page_values is not None else None)
        if page_id in page_ids:
            raise ValueError("svg_common_header_page_id_duplicate")
        page_ids.add(page_id)
        applied = not (cover_exception and index == 0)
        if applied and any(actual["user_fixed"] and ref not in represented_refs
                           for ref, actual in values.items()):
            raise ValueError("svg_common_header_fixed_values_missing")
        bound_fields = []
        for field in fields:
            actual = values.get(field["value_ref"])
            if actual is None or not actual["value"]:
                if not field["optional"] and applied:
                    raise ValueError("svg_common_header_value_missing")
                actual = {"value": "", "source_ref": "absent:" + field["value_ref"], "user_fixed": False}
            lines = (_wrap(actual["value"], field["font"], field["box"]["width"], field["max_lines"],
                           MEASUREMENT_VERSION, space_advance)
                     if actual["value"] and applied else [])
            widths, boxes = _line_evidence(lines, field["font"], field["box"], field["baseline_offset_px"],
                                         field["line_height_px"], field["alignment"], MEASUREMENT_VERSION,
                                         space_advance)
            bound_fields.append({"node_id": field["node_id"], "role": field["role"], "value_ref": field["value_ref"],
                                 **actual, "lines": lines, "line_widths": widths, "glyph_boxes": boxes, "font": field["font"]})
        title = _value(slide, "title")
        source = common_header_source_digest(_value(slide, "content", ""))
        binding = {"version": PAGE_VERSION, "header_digest": contract["digest"], "page_id": page_id,
            "page_index": index, "page_count": len(slides), "applied": applied, "title": title,
            "title_digest": _digest(title), "source_digest": source, "content_digest": source, "fields": bound_fields}
        if _value(slide, "title_source_digest"):
            binding["title_source_digest"] = _value(slide, "title_source_digest")
        binding["paint_boxes"], binding["rendered_group_digest"] = _measure(contract, binding)
        binding["digest"] = _digest(binding)
        bindings.append(binding)
    return contract, bindings


def validate_common_header_binding_v2(contract, binding, *, index=None, count=None, content=_MISSING, title=_MISSING,
                                     page_id=_MISSING):
    if (not isinstance(contract, dict) or contract.get("version") != VERSION
            or contract.get("measurement_version") not in _RECOGNIZED_MEASUREMENT_VERSIONS
            or contract.get("digest") != _digest({k: v for k, v in contract.items() if k != "digest"})
            or not isinstance(binding, dict) or binding.get("version") != PAGE_VERSION
            or binding.get("header_digest") != contract["digest"]
            or binding.get("digest") != _digest({k: v for k, v in binding.items() if k != "digest"})):
        raise ValueError("svg_common_header_digest_mismatch")
    page_index, page_count = binding.get("page_index"), binding.get("page_count")
    if (type(page_index) is not int or type(page_count) is not int or not 0 <= page_index < page_count
            or (index is not None and index != page_index) or (count is not None and count != page_count)
            or not isinstance(binding.get("page_id"), str) or not binding["page_id"]
            or binding.get("applied") is not (not (contract["cover_exception"] and page_index == 0))
            or binding.get("title_digest") != _digest(binding.get("title"))):
        raise ValueError("svg_common_header_binding_invalid")
    if content is not _MISSING and any(binding.get(key) != common_header_source_digest(content) for key in ("source_digest", "content_digest")):
        raise ValueError("svg_common_header_source_mismatch")
    if title is not _MISSING and binding.get("title") != title:
        raise ValueError("svg_common_header_title_mismatch")
    if page_id is not _MISSING and binding.get("page_id") != page_id:
        raise ValueError("svg_common_header_page_id_mismatch")
    if len(binding["fields"]) != len(contract["fields"]):
        raise ValueError("svg_common_header_binding_invalid")
    space_advance = contract.get("space_advance")
    if contract["measurement_version"] == MEASUREMENT_VERSION:
        validate_space_advance_evidence(space_advance, [field["font"] for field in contract["fields"]])
    elif "space_advance" in contract:
        raise ValueError("svg_common_header_digest_mismatch")
    for field, bound in zip(contract["fields"], binding["fields"], strict=False):
        font = field["font"]
        if _font(font["family"], font["weight"], font["size_px"]) != font:
            raise ValueError("svg_common_header_font_identity_invalid")
        if (any(bound.get(key) != field[key] for key in ("node_id", "role", "value_ref", "font"))
                or not isinstance(bound.get("value"), str) or not isinstance(bound.get("source_ref"), str)
                or not bound["source_ref"] or type(bound.get("user_fixed")) is not bool
                or (not bound["value"] and not field["optional"] and binding["applied"])):
            raise ValueError("svg_common_header_binding_invalid")
        value = bound["value"]
        expected_value = {"title": binding["title"], "page_number": str(page_index + 1), "page_count": str(page_count)}.get(field["value_ref"])
        if expected_value is not None and value != expected_value:
            raise ValueError("svg_common_header_value_source_mismatch")
        lines = (_wrap(value, font, field["box"]["width"], field["max_lines"], contract["measurement_version"],
                       space_advance)
                 if value and binding["applied"] else [])
        widths, boxes = _line_evidence(lines, font, field["box"], field["baseline_offset_px"],
                                       field["line_height_px"], field["alignment"],
                                       contract["measurement_version"], space_advance)
        if bound.get("lines") != lines or bound.get("line_widths") != widths or bound.get("glyph_boxes") != boxes:
            raise ValueError("svg_common_header_measurement_mismatch")
    boxes, digest = _measure(contract, binding)
    if binding.get("paint_boxes") != boxes or binding.get("rendered_group_digest") != digest:
        raise ValueError("svg_common_header_measurement_mismatch")


def render_common_header_v2(contract, binding, *, index, count):
    validate_common_header_binding_v2(contract, binding, index=index, count=count)
    return _render(contract, binding) if binding["applied"] else None
