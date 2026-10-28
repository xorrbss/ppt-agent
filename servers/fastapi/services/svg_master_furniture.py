"""Deterministic brand furniture from an admitted, owner-verified master."""
from __future__ import annotations

from copy import deepcopy
import xml.etree.ElementTree as ET

GROUP_ID = "svg-system-master-furniture"
SVG_NS = "http://www.w3.org/2000/svg"


def master_furniture(snapshot: dict | None) -> dict:
    snapshot = snapshot or {}
    return (snapshot.get("system_furniture") or snapshot.get("master_template", {}).get("system_furniture")
            or snapshot.get("source_template", {}).get("system_furniture") or {})


def furniture_image_manifest(snapshot: dict | None) -> dict[str, str]:
    images = {item["id"]: item["asset_digest"] for item in master_furniture(snapshot).get("elements", [])
              if item.get("data_url") and item.get("asset_digest")}
    header = (snapshot or {}).get("common_header") or {}
    if header.get("version") == "svg-common-header/v2":
        images.update(header.get("assets", {}))
    if (snapshot or {}).get("svg_template", {}).get("mode") == "template-redesign":
        from services.svg_templates.frame_attestation import template_image_manifest
        images.update(template_image_manifest(snapshot))
    return images


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
    generated = furniture.get("generated_common_header")
    group_ids = [GROUP_ID] if furniture.get("master_digest", furniture.get("digest")) else []
    if furniture.get("generated_template_frame"):
        from services.svg_templates.frame_attestation import validate_template_furniture_binding
        if generated or furniture.get("master_digest"):
            raise ValueError("svg_template_furniture_owner_conflict")
        group_ids = validate_template_furniture_binding(root, furniture)
    if generated:
        from services.svg_common_header import GROUP_ID as HEADER_ID, render_common_header
        expected = render_common_header(generated.get("contract"), generated.get("page"),
            index=generated.get("index"), count=generated.get("count"))
        if expected is None or generated.get("origin") != "generated-common-header":
            raise ValueError("svg_common_header_digest_mismatch")
        matches = [node for node in root.iter() if node.get("id") == HEADER_ID]
        if len(matches) != 1 or _structure(matches[0]) != _structure(expected):
            raise ValueError("svg_common_header_digest_mismatch")
        group_ids.append(HEADER_ID)
    groups = [node for node in root.iter() if node.get("id") in group_ids]
    if (len(groups) != len(group_ids) or not groups or not furniture.get("digest")
            or not isinstance(furniture_ids, list) or not furniture_ids
            or any(not isinstance(node_id, str) or not node_id.startswith("svg-system-") for node_id in furniture_ids)
            or len(furniture_ids) != len(set(furniture_ids))):
        raise ValueError("svg_source_furniture_receipt_mismatch")
    elements = {node.get("id"): node for group in groups for node in group.iter()}
    if (any(group.tag != f"{{{SVG_NS}}}g" for group in groups) or set(elements) != set(furniture_ids)
            or len(elements) != sum(len(list(group.iter())) for group in groups)
            or set(furniture.get("images", {})) != {node.get("id") for group in groups for node in group.iter()
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
                 allow_system_page_number=bool(generated and not furniture.get("master_digest")),
                 trusted_furniture_images=furniture.get("images"))
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
                             index: int, n: int, source_manifest=None,
                             header_page: dict | None = None,
                             template_page_context: dict | None = None) -> dict | None:
    """Attest only deterministic furniture actually stamped from an admitted snapshot."""
    furniture = master_furniture(snapshot)
    if snapshot.get("svg_template", {}).get("mode") == "template-redesign":
        from services.svg_templates.frame_attestation import template_furniture_receipt
        if furniture or snapshot.get("common_header"):
            raise ValueError("svg_template_furniture_owner_conflict")
        receipt = template_furniture_receipt(svg, snapshot, width=width, height=height,
            index=index, count=n, page_context=template_page_context)
        attested_svg_furniture(svg, receipt, source_manifest=source_manifest, width=width, height=height)
        return receipt
    from services.svg_common_header import render_common_header
    header = render_common_header(snapshot.get("common_header"), header_page, index=index, count=n)
    if not furniture and header is None:
        return None
    from services.svg_contract import source_digest

    def group_from(source):
        groups = [node for node in ET.fromstring(source).iter() if node.get("id") == GROUP_ID]
        if len(groups) != 1:
            raise ValueError("svg_source_furniture_receipt_mismatch")
        return groups[0]

    def structure(node):
        return (node.tag, sorted(node.attrib.items()), node.text or "", tuple(structure(child) for child in node))

    groups = []
    if furniture:
        actual = group_from(svg)
        expected = group_from(stamp_master_furniture(svg, snapshot, index, n))
        if structure(actual) != structure(expected):
            raise ValueError("svg_source_furniture_receipt_mismatch")
        groups.append(actual)
    generated = None
    if header is not None:
        matches = [node for node in ET.fromstring(svg).iter() if node.get("id") == header.get("id")]
        if len(matches) != 1 or structure(matches[0]) != structure(header):
            raise ValueError("svg_common_header_digest_mismatch")
        groups.append(matches[0])
        generated = {"origin": "generated-common-header", "contract": snapshot["common_header"],
                     "page": header_page, "index": index, "count": n}
        if snapshot["common_header"].get("version") == "svg-common-header/v2":
            generated["selection"] = deepcopy(snapshot.get("common_header_selection"))
    images = {node.get("id") for group in groups for node in group.iter() if node.tag == f"{{{SVG_NS}}}image"}
    receipt = {"version": "svg-system-furniture/v1", "svg_digest": source_digest(svg),
               "canvas": {"width": width, "height": height},
               "digest": furniture.get("digest") or snapshot["common_header"]["digest"],
               "element_ids": [node.get("id") for group in groups for node in group.iter()],
               "images": {key: value for key, value in furniture_image_manifest(snapshot).items() if key in images}}
    if generated:
        receipt.update(master_digest=furniture.get("digest"), generated_common_header=generated)
    attested_svg_furniture(svg, receipt, source_manifest=source_manifest, width=width, height=height)
    return receipt


def _structure(node):
    return (node.tag, sorted(node.attrib.items()), node.text or "", tuple(_structure(child) for child in node))


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
