"""Versioned conservative SVG admission shared by generation and export."""

from __future__ import annotations

import base64
import io
import math
import re
import xml.etree.ElementTree as ET
from services.authored_canvas import DEFAULT_CANVAS_HEIGHT_PX, DEFAULT_CANVAS_WIDTH_PX
from utils.hashing import sha256_prefixed

SVG_CONTRACT_VERSION = "svg-static/v1"
SVG_NS = "http://www.w3.org/2000/svg"
PAGE_NUMBER_ID = "svg-system-page-number"
# Root id of a deterministic raster-redesign page. Only such pages may use
# ``dy`` on <tspan> (paragraph line spacing that the PPTX export merges into
# one editable text frame per block).
RASTER_REDESIGN_ROOT_ID = "svg-raster-redesign"
_MAX_TSPAN_DY = 1000.0
ET.register_namespace("", SVG_NS)
_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_NUMERIC = {
    "x",
    "y",
    "width",
    "height",
    "rx",
    "ry",
    "cx",
    "cy",
    "r",
    "x1",
    "x2",
    "y1",
    "y2",
    "font-size",
    "stroke-width",
    "opacity",
    "fill-opacity",
    "stroke-opacity",
}
_COMMON = {"id", "fill", "stroke", "stroke-width", "opacity", "fill-opacity", "stroke-opacity", "transform"}
_TEXT = {"x", "y", "font-family", "font-size", "font-weight", "text-anchor"}
ELEMENT_ATTRIBUTES = {
    "svg": {"width", "height", "viewBox"},
    "g": set(),
    "text": _TEXT,
    "tspan": _TEXT,
    "rect": {"x", "y", "width", "height", "rx", "ry"},
    "circle": {"cx", "cy", "r"},
    "ellipse": {"cx", "cy", "rx", "ry"},
    "line": {"x1", "y1", "x2", "y2"},
    "polyline": {"points"},
    "polygon": {"points"},
    "path": {"d"},
    "image": {"x", "y", "width", "height", "href", "preserveAspectRatio"},
}


def validate_raster_data_url(value: str) -> int:
    from PIL import Image

    match = re.fullmatch(r"data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)", value)
    if not match:
        raise ValueError("svg_image_raster_data_required")
    try:
        payload = base64.b64decode(match[2], validate=True)
        if len(payload) > 1536 * 1024:
            raise ValueError("svg_image_size_limit")
        with Image.open(io.BytesIO(payload)) as image:
            if image.format.lower() != match[1] or max(image.size) > 8192 or image.width * image.height > 16_777_216:
                raise ValueError("svg_image_dimensions_or_format_invalid")
            image.verify()
    except (OSError, SyntaxError) as error:
        raise ValueError("svg_image_invalid") from error
    return len(payload)


def source_digest(svg: str) -> str:
    return sha256_prefixed(svg.encode("utf-8"))


def normalize_svg_response(value: str) -> str:
    """Remove presentation wrappers and well-formed inert XML comments only."""
    value = value.strip()
    match = re.fullmatch(r"```(?:svg|xml)?\s*([\s\S]*?)\s*```", value)
    if match:
        value = match[1].strip()
    # Keep malformed comments, declarations and processing instructions for
    # the strict validator to reject. Never remove arbitrary <! ... > input.
    return re.sub(r"<!--(?:[^-]|-(?!-))*-->", "", value).strip()


def _numbers(value: str) -> list[float]:
    if not re.fullmatch(rf"\s*{_NUMBER}(?:[ ,]+{_NUMBER})*\s*", value):
        raise ValueError("svg_contract_invalid_number")
    result = [float(x) for x in re.findall(_NUMBER, value)]
    if any(not math.isfinite(x) or abs(x) > 100000 for x in result):
        raise ValueError("svg_contract_number_out_of_range")
    return result


def validate_svg(
    svg: str,
    *,
    width: int = DEFAULT_CANVAS_WIDTH_PX,
    height: int = DEFAULT_CANVAS_HEIGHT_PX,
    allow_system_page_number: bool = True,
    source_manifest: dict | None = None,
    allow_image_only: bool = False,
    trusted_furniture_images: dict[str, str] | None = None,
) -> dict:
    if not isinstance(svg, str) or len(svg.encode("utf-8")) > 2 * 1024 * 1024:
        raise ValueError("svg_contract_size_limit")
    if re.search(r"<!|<\?", svg):
        raise ValueError("svg_contract_declaration_forbidden")
    try:
        root = ET.fromstring(svg)
    except ET.ParseError as error:
        raise ValueError("svg_contract_invalid_xml") from error
    if root.tag != f"{{{SVG_NS}}}svg":
        raise ValueError("svg_contract_namespace_required")
    if _numbers(root.get("viewBox", "")) != [0, 0, width, height]:
        raise ValueError("svg_contract_canvas_mismatch")
    if _numbers(root.get("width", "")) != [width] or _numbers(root.get("height", "")) != [height]:
        raise ValueError("svg_contract_canvas_mismatch")
    ids, text_runs, used_slots = set(), [], set()
    count, asset_bytes = 0, 0
    paragraph_dy = root.get("id") == RASTER_REDESIGN_ROOT_ID

    def visit(node, parent=None, depth=0):
        nonlocal count, asset_bytes
        count += 1
        if count > 4096 or depth > 32:
            raise ValueError("svg_contract_complexity_limit")
        name = node.tag.removeprefix(f"{{{SVG_NS}}}")
        if name not in ELEMENT_ATTRIBUTES or len(node.attrib) > 24:
            raise ValueError("svg_contract_unsupported_element")
        if (name == "svg" and parent is not None) or (name == "tspan" and parent != "text"):
            raise ValueError("svg_contract_invalid_nesting")
        if parent in {"text", "tspan"} and name != "tspan":
            raise ValueError("svg_contract_invalid_text_child")
        if parent not in {None, "svg", "g", "text"}:
            raise ValueError("svg_contract_invalid_parent")
        for key, value in node.attrib.items():
            if paragraph_dy and name == "tspan" and key == "dy":
                values = _numbers(value)
                if len(values) != 1 or not 0 < values[0] <= _MAX_TSPAN_DY:
                    raise ValueError("svg_contract_invalid_dy")
                continue
            if key not in ELEMENT_ATTRIBUTES[name] | _COMMON:
                raise ValueError("svg_contract_unsupported_attribute:" + key[:40])
            if key in _NUMERIC:
                values = _numbers(value)
                if len(values) != 1:
                    raise ValueError("svg_contract_scalar_required")
                number = values[0]
                if key in {"width", "height", "r", "font-size"} and number <= 0:
                    raise ValueError("svg_contract_positive_required")
                if key in {"rx", "ry", "stroke-width"} and number < 0:
                    raise ValueError("svg_contract_nonnegative_required")
                if "opacity" in key and not 0 <= number <= 1:
                    raise ValueError("svg_contract_invalid_opacity")
            elif key in {"fill", "stroke"}:
                if not re.fullmatch(r"#[0-9a-fA-F]{6}|#[0-9a-fA-F]{3}|none|black|white", value):
                    raise ValueError("svg_contract_solid_paint_required")
            elif key == "transform":
                match = re.fullmatch(r"translate\(([^()]+)\)", value)
                if not match or len(_numbers(match[1])) not in {1, 2}:
                    raise ValueError("svg_contract_translate_only")
            elif key == "points":
                values = _numbers(value)
                if len(values) % 2 or len(values) > 2048:
                    raise ValueError("svg_contract_invalid_points")
            elif key == "d":
                if len(value) > 32000 or not re.fullmatch(r"[MLHVZmlhvz0-9eE+.,\s-]+", value):
                    raise ValueError("svg_contract_linear_path_only")
                chunks = re.findall(r"([MLHVZmlhvz])([^MLHVZmlhvz]*)", value)
                if not chunks or chunks[0][0].upper() != "M":
                    raise ValueError("svg_contract_path_moveto_required")
                for command, raw in chunks:
                    values = _numbers(raw.strip()) if raw.strip() else []
                    arity = {"M": 2, "L": 2, "H": 1, "V": 1, "Z": 0}[command.upper()]
                    if (arity == 0 and values) or (arity and (not values or len(values) % arity)):
                        raise ValueError("svg_contract_path_arguments_invalid")
                for number in re.findall(_NUMBER, value):
                    _numbers(number)
            elif key == "font-family" and not re.fullmatch(r"[\w ,'-]{1,120}", value):
                raise ValueError("svg_contract_invalid_font")
            elif key == "font-weight" and value not in {"normal", "bold", *map(str, range(100, 1000, 100))}:
                raise ValueError("svg_contract_invalid_weight")
            elif key == "text-anchor" and value not in {"start", "middle", "end"}:
                raise ValueError("svg_contract_invalid_anchor")
            elif key == "id":
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,100}", value) or value in ids:
                    raise ValueError("svg_contract_invalid_id")
                if value == PAGE_NUMBER_ID and not allow_system_page_number:
                    raise ValueError("svg_contract_reserved_page_number")
                ids.add(value)
            elif key == "href":
                if source_manifest is not None:
                    trusted_digest = (trusted_furniture_images or {}).get(node.get("id", ""))
                    if trusted_digest is not None:
                        asset_bytes += validate_raster_data_url(value)
                        if asset_bytes > 2 * 1024 * 1024:
                            raise ValueError("svg_image_total_size_limit")
                        if sha256_prefixed(base64.b64decode(value.split(",", 1)[1], validate=True)) != trusted_digest:
                            raise ValueError("svg_master_furniture_image_digest_mismatch")
                    else:
                        if not re.fullmatch(r"source-slot-[A-Za-z0-9_-]+", value) or value not in source_manifest:
                            raise ValueError("svg_source_image_unknown_slot")
                        used_slots.add(value)
                else:
                    asset_bytes += validate_raster_data_url(value)
                    if asset_bytes > 2 * 1024 * 1024:
                        raise ValueError("svg_image_total_size_limit")
            elif key == "preserveAspectRatio" and value != "xMidYMid meet":
                raise ValueError("svg_image_contain_required")
        if name == "image":
            if not {"x", "y", "width", "height", "href", "preserveAspectRatio"} <= node.attrib.keys():
                raise ValueError("svg_image_attributes_required")
            if not node.get("width", "").isdigit() or not node.get("height", "").isdigit():
                raise ValueError("svg_image_integer_dimensions_required")
        if name == "text":
            if not {"x", "y", "font-family", "font-size", "fill"} <= node.attrib.keys():
                raise ValueError("svg_contract_explicit_text_attributes_required")
            if node.get("fill") == "none":
                raise ValueError("svg_contract_invisible_text")
            text_runs.append("".join(node.itertext()))
        if name not in {"text", "tspan"} and (node.text or "").strip():
            raise ValueError("svg_contract_text_outside_text_element")
        for child in node:
            if (child.tail or "").strip() and name not in {"text", "tspan"}:
                raise ValueError("svg_contract_text_outside_text_element")
            visit(child, name, depth + 1)

    visit(root)
    if source_manifest is not None and any(
        (item.get("required") or item.get("allowOmit") is False) and slot not in used_slots
        for slot, item in source_manifest.items()
    ):
        raise ValueError("svg_required_source_image_missing")
    if not any(t.strip() for t in text_runs) and not (
        allow_image_only and any(str(node.tag).endswith("}image") or str(node.tag) == "image" for node in root.iter())
    ):
        raise ValueError("svg_contract_visible_text_required")
    return {
        "contract_version": SVG_CONTRACT_VERSION,
        "source_digest": source_digest(svg),
        "element_count": count,
        "text": "\n".join(text_runs),
        "syntax_valid": True,
    }


def finalize_svg(
    svg: str,
    *,
    index: int,
    n: int,
    width: int = DEFAULT_CANVAS_WIDTH_PX,
    height: int = DEFAULT_CANVAS_HEIGHT_PX,
    style_snapshot: dict | None = None,
    page_number_mode: str = "template",
    source_manifest: dict | None = None,
    allow_image_only: bool = False,
    header_page: dict | None = None,
    allow_existing_common_header: bool = False,
) -> str:
    from services.svg_master_furniture import furniture_image_manifest, master_furniture, stamp_master_furniture
    from services.svg_common_header import GROUP_ID, render_common_header

    header = render_common_header((style_snapshot or {}).get("common_header"), header_page,
                                  index=index, count=n)
    if header is None:
        validate_svg(svg, width=width, height=height, source_manifest=source_manifest,
                     allow_image_only=allow_image_only,
                     trusted_furniture_images=furniture_image_manifest(style_snapshot))
    # In uniform mode the provider authors only the body. Its visible title is
    # supplied by the validated server header; validate that complete document
    # below, with all existing SVG, asset and security checks still required.
    if not 0 <= index < n:
        raise ValueError("svg_page_index_invalid")
    root = ET.fromstring(svg)
    reserved = [node for node in root.iter() if node.get("id", "").startswith(GROUP_ID)]
    if reserved:
        if not allow_existing_common_header or header is None:
            raise ValueError("svg_common_header_forged")
        def structure(node):
            return (node.tag, sorted(node.attrib.items()), node.text or "",
                    tuple(structure(child) for child in node))
        groups = [node for node in reserved if node.get("id") == GROUP_ID]
        if (len(groups) != 1 or groups[0] not in list(root)
                or structure(groups[0]) != structure(header)
                or len(reserved) != len(list(header.iter()))):
            raise ValueError("svg_common_header_digest_mismatch")
        root.remove(groups[0])
    for parent in root.iter():
        for child in list(parent):
            if child.get("id") == PAGE_NUMBER_ID:
                parent.remove(child)
    furniture = master_furniture(style_snapshot)
    header_contract = (style_snapshot or {}).get("common_header") or {}
    header_numbers_page = (header is not None and header_contract.get("version") == "svg-common-header/v2"
                           and any(field.get("value_ref") == "page_number" for field in header_contract.get("fields", [])))
    if page_number_mode not in {"none", "hidden", "off"} and not furniture and not header_numbers_page:
        tokens = (style_snapshot or {}).get("tokens", {})
        node = ET.SubElement(
            root,
            f"{{{SVG_NS}}}text",
            {
                "id": PAGE_NUMBER_ID,
                "x": str(width - 32),
                "y": str(height - 20),
                "font-family": tokens.get("body_font") or "Noto Sans KR",
                "font-size": "12",
                "fill": tokens.get("primary") or "#2563EB",
                "text-anchor": "end",
            },
        )
        node.text = str(index + 1).zfill(2)
    if header is not None:
        root.append(header)
    result = ET.tostring(root, encoding="unicode")
    result = stamp_master_furniture(result, style_snapshot, index, n)
    validate_svg(result, width=width, height=height, source_manifest=source_manifest, allow_image_only=allow_image_only,
                 trusted_furniture_images=furniture_image_manifest(style_snapshot))
    return result
