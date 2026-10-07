"""Deterministic brand furniture from an admitted, owner-verified master."""
from __future__ import annotations

import xml.etree.ElementTree as ET

GROUP_ID = "svg-system-master-furniture"
SVG_NS = "http://www.w3.org/2000/svg"


def master_furniture(snapshot: dict | None) -> dict:
    snapshot = snapshot or {}
    return (snapshot.get("system_furniture") or snapshot.get("master_template", {}).get("system_furniture")
            or snapshot.get("source_template", {}).get("system_furniture") or {})


def furniture_image_manifest(snapshot: dict | None) -> dict[str, str]:
    return {item["id"]: item["asset_digest"] for item in master_furniture(snapshot).get("elements", [])
            if item.get("data_url") and item.get("asset_digest")}


def attested_svg_furniture(svg: str, furniture_receipt: dict, *, source_manifest=None,
                           width: int, height: int) -> dict[str, ET.Element]:
    """Validate the exact system group and canonical input attested by the writer."""
    from services.svg_contract import source_digest, validate_svg

    furniture = furniture_receipt or {}
    if not furniture:
        return {}
    if (furniture.get("version") != "svg-system-furniture/v1"
            or furniture.get("svg_digest") != source_digest(svg)
            or furniture.get("canvas") != {"width": width, "height": height}):
        raise ValueError("svg_source_furniture_receipt_mismatch")
    root = ET.fromstring(svg)
    furniture_ids = furniture.get("element_ids", [])
    groups = [node for node in root.iter() if node.get("id") == GROUP_ID]
    if (len(groups) != 1 or not furniture.get("digest")
            or not isinstance(furniture_ids, list) or not furniture_ids
            or any(not isinstance(node_id, str) or not node_id.startswith("svg-system-") for node_id in furniture_ids)
            or len(furniture_ids) != len(set(furniture_ids))):
        raise ValueError("svg_source_furniture_receipt_mismatch")
    group = groups[0]
    elements = {node.get("id"): node for node in group.iter()}
    if (group.tag != f"{{{SVG_NS}}}g" or set(elements) != set(furniture_ids)
            or len(elements) != len(list(group.iter()))
            or set(furniture.get("images", {})) != {node.get("id") for node in group.iter()
                                                    if node.tag == f"{{{SVG_NS}}}image"}):
        raise ValueError("svg_source_furniture_receipt_mismatch")
    # Independently check image digests even on ordinary inline SVG, where the
    # source-slot validator does not otherwise need a trusted-image exemption.
    import base64
    from utils.hashing import sha256_prefixed
    for node_id, digest in furniture.get("images", {}).items():
        href = elements[node_id].get("href", "")
        if not href.startswith("data:image/") or sha256_prefixed(base64.b64decode(href.split(",", 1)[1], validate=True)) != digest:
            raise ValueError("svg_master_furniture_image_digest_mismatch")
    validate_svg(svg, width=width, height=height, source_manifest=source_manifest,
                 allow_system_page_number=False, trusted_furniture_images=furniture.get("images"))
    return elements


def attested_source_furniture(svg: str, receipt: dict) -> dict[str, ET.Element]:
    """Source bindings and system furniture remain disjoint exact sets."""
    furniture = receipt.get("system_furniture") or {}
    if not furniture:
        return {}
    attestation = {**furniture, "version": "svg-system-furniture/v1",
                   "svg_digest": receipt.get("svg_digest"), "canvas": receipt.get("canvas")}
    canvas = receipt.get("canvas", {})
    nodes = attested_svg_furniture(svg, attestation,
        source_manifest={binding["slot_id"]: {"required": True}
                         for binding in receipt.get("bindings", []) if binding.get("kind") == "image"},
        width=canvas.get("width"), height=canvas.get("height"))
    if set(nodes).intersection(binding["element_id"] for binding in receipt.get("bindings", [])):
        raise ValueError("svg_source_furniture_receipt_mismatch")
    return nodes


def master_furniture_receipt(svg: str, snapshot: dict, *, width: int, height: int,
                             index: int, n: int, source_manifest=None) -> dict | None:
    """Attest only deterministic furniture actually stamped from an admitted snapshot."""
    furniture = master_furniture(snapshot)
    if not furniture:
        return None
    from services.svg_contract import source_digest

    def group_from(source):
        groups = [node for node in ET.fromstring(source).iter() if node.get("id") == GROUP_ID]
        if len(groups) != 1:
            raise ValueError("svg_source_furniture_receipt_mismatch")
        return groups[0]

    def structure(node):
        return (node.tag, sorted(node.attrib.items()), node.text or "", tuple(structure(child) for child in node))

    actual = group_from(svg)
    expected = group_from(stamp_master_furniture(svg, snapshot, index, n))
    if structure(actual) != structure(expected):
        raise ValueError("svg_source_furniture_receipt_mismatch")
    images = {node.get("id") for node in actual.iter() if node.tag == f"{{{SVG_NS}}}image"}
    receipt = {"version": "svg-system-furniture/v1", "svg_digest": source_digest(svg),
               "canvas": {"width": width, "height": height}, "digest": furniture["digest"],
               "element_ids": [node.get("id") for node in actual.iter()],
               "images": {key: value for key, value in furniture_image_manifest(snapshot).items() if key in images}}
    attested_svg_furniture(svg, receipt, source_manifest=source_manifest, width=width, height=height)
    return receipt


def stamp_master_furniture(svg: str, style_snapshot: dict | None, index: int, n: int) -> str:
    from services.svg_contract import PAGE_NUMBER_ID

    furniture = master_furniture(style_snapshot)
    if not furniture:
        return svg
    root = ET.fromstring(svg)
    for parent in root.iter():
        for child in list(parent):
            if child.get("id") == PAGE_NUMBER_ID or child.get("id", "").startswith("svg-system-master-"):
                parent.remove(child)
    group = ET.SubElement(root, f"{{{SVG_NS}}}g", {"id": GROUP_ID})
    for item in furniture.get("elements", []):
        visibility = item.get("visibility")
        if visibility == "hidden" or (visibility == "exceptCover" and index == 0) or (visibility == "coverOnly" and index != 0):
            continue
        kind = item["kind"]
        x, y, width, height = (item[key] for key in ("x", "y", "width", "height"))
        attrs = {"id": item["id"]}
        if kind == "logo":
            attrs.update(x=str(x), y=str(y), width=str(int(width)), height=str(int(height)),
                         href=item["data_url"], preserveAspectRatio="xMidYMid meet")
            ET.SubElement(group, f"{{{SVG_NS}}}image", attrs)
        elif kind in {"text", "pageNumber"}:
            text = str(index + 1).zfill(2) if kind == "pageNumber" else item.get("text", "")
            family, font_size = item["font_family"], item["font_size"]
            from utils.svg_template_fonts import measure_font
            face = measure_font(family, item["font_weight"], 20)
            measured = face.getlength(text) * font_size / 20
            if measured > width:
                font_size *= width / measured
            attrs.update(x=str(x), y=str(y + min(height, font_size)), fill=item["fill"],
                         **{"font-family": family, "font-size": str(font_size), "font-weight": str(item["font_weight"])})
            node = ET.SubElement(group, f"{{{SVG_NS}}}text", attrs)
            node.text = text
        elif kind == "line":
            attrs.update(x1=str(x), y1=str(y + height / 2), x2=str(x + width), y2=str(y + height / 2),
                         stroke=item["stroke"], **{"stroke-width": str(max(.5, min(height, 2)))})
            ET.SubElement(group, f"{{{SVG_NS}}}line", attrs)
        elif kind == "rect":
            attrs.update(x=str(x), y=str(y), width=str(width), height=str(height), fill=item["fill"])
            ET.SubElement(group, f"{{{SVG_NS}}}rect", attrs)
    return ET.tostring(root, encoding="unicode")
