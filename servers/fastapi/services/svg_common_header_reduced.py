"""Deterministic fixed-value extension of the measured title-only header."""

from xml.etree import ElementTree as ET

from services.svg_common_header_v2 import prepare_common_header_v2
from services.svg_common_header_values import prepare_header_page_values


def preserve_fixed_header_values(request, outline, canvas, snapshot, title_contract, *, plan):
    values = prepare_header_page_values(request, outline)
    refs = sorted({ref for page in values for ref, value in page["values"].items()
                   if value["user_fixed"] and ref != "title"})
    geometry, font = title_contract["canvas"], title_contract["font"]
    title_box = title_contract["title_box"]
    fields = [{"node_id": "title", "role": "title", "value_ref": "title", "box": title_box,
        "font_family": font["family"], "font_weight": font["weight"], "font_size_px": font["size_px"],
        "line_height_px": title_contract["line_height_px"],
        "baseline_offset_px": title_contract["baseline_offset_px"], "max_lines": 2,
        "alignment": title_contract["alignment"], "optional": False}]
    size = max(20, font["size_px"] * .6)
    ratio = size / font["size_px"]
    row = title_contract["line_height_px"] * ratio
    y = title_box["y"] + title_box["height"] + row * .5
    for index, ref in enumerate(refs):
        fields.append({"node_id": f"value-{index}", "role": "metadata", "value_ref": ref,
            "box": {"x": title_box["x"], "y": y, "width": title_box["width"], "height": row * 2},
            "font_family": font["family"], "font_weight": font["weight"], "font_size_px": size,
            "line_height_px": row, "baseline_offset_px": title_contract["baseline_offset_px"] * ratio,
            "max_lines": 2, "alignment": title_contract["alignment"],
            "optional": any(ref not in page["values"] for page in values)})
        y += row * 2.5
    body = dict(title_contract["body_box"])
    shift = max(0, y - body["y"])
    body.update(y=body["y"] + shift, height=body["height"] - shift)
    if body["height"] < 120:
        raise ValueError("svg_common_header_body_space_unresolved")
    root = ET.Element("svg", {"xmlns": "http://www.w3.org/2000/svg",
        "width": str(geometry["width"]), "height": str(geometry["height"]),
        "viewBox": f'0 0 {geometry["width"]} {geometry["height"]}'})
    for field in fields:
        ET.SubElement(root, "text", {"id": field["node_id"], "fill": title_contract["color"]})
    candidate = {"candidate_id": "reduced-" + plan["plan_digest"].split(":", 1)[1], "revision": 1,
        "svg": ET.tostring(root, encoding="unicode"), "fields": fields, "body_box": body}
    return prepare_common_header_v2(candidate, outline, canvas, snapshot=snapshot,
        cover_exception=title_contract["cover_exception"], page_values=values)
