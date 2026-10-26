"""SVG service finalization over the shared validation contract."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from utils.svg_validation import (
    SVG_CONTRACT_VERSION as SVG_CONTRACT_VERSION,
    SVG_NS as SVG_NS,
    PAGE_NUMBER_ID as PAGE_NUMBER_ID,
    RASTER_REDESIGN_ROOT_ID as RASTER_REDESIGN_ROOT_ID,
    _MAX_TSPAN_DY as _MAX_TSPAN_DY,
    _NUMBER as _NUMBER,
    _NUMERIC as _NUMERIC,
    _COMMON as _COMMON,
    _TEXT as _TEXT,
    ELEMENT_ATTRIBUTES as ELEMENT_ATTRIBUTES,
    validate_raster_data_url as validate_raster_data_url,
    source_digest as source_digest,
    normalize_svg_response as normalize_svg_response,
    _numbers as _numbers,
    validate_svg as validate_svg,
    DEFAULT_CANVAS_HEIGHT_PX as DEFAULT_CANVAS_HEIGHT_PX,
    DEFAULT_CANVAS_WIDTH_PX as DEFAULT_CANVAS_WIDTH_PX,
)


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
