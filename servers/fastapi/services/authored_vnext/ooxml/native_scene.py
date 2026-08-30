"""Fail-closed native PPTX materializer for canonical authored Scene IR.

This module intentionally supports only the object types that can be emitted and
read back without a lossy HTML/raster fallback.  The receipt is produced from a
reopened OOXML package, not from the input scene.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
import math
import re
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree
from pptx import Presentation
from pptx.chart.data import ChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE, MSO_SHAPE_TYPE
from pptx.enum.text import MSO_AUTO_SIZE, PP_ALIGN, MSO_VERTICAL_ANCHOR
from pptx.parts.image import Image as PptxImage
from pptx.util import Inches, Pt

from services.authored_vnext.capability_registry import (
    CapabilityRegistryMismatchError,
    EDITABLE_HYBRID_PROFILE_ID,
    NATIVE_CAPABILITY_IDS_V1,
    capability_metadata,
    hybrid_capability_for_kind,
    hybrid_ooxml_capabilities,
    is_hybrid_native_capability,
    require_capability_metadata,
)
from services.authored_vnext.master.digests import canonical_digest, canonical_json


PPTX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)
SCENE_SCHEMA_VERSION = "deck-scene-ir/v1"
SUPPORTED_SCENE_ELEMENT_KINDS = frozenset(
    {"text", "shape", "image", "table", "chart"}
)
UNSUPPORTED_SCENE_ELEMENT_KINDS = frozenset(
    {"connector", "group"}
)
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEX_COLOR = re.compile(r"^#?([0-9a-fA-F]{6})$")
_DESCRIPTION_PREFIX = "authored-vnext:"
_RASTER_DESCRIPTION_PREFIX = "authored-raster-island:"
_FURNITURE_DESCRIPTION_PREFIX = "authored-furniture:"
_BACKGROUND_FURNITURE_ID = "company-standard:background"
_CUSTOM_NS = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
_VT_NS = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
_CONTENT_TYPES_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
_PACKAGE_RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_CUSTOM_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties"
)
_CUSTOM_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.custom-properties+xml"
)
_PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_RELATIONSHIPS_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
_TAGS_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/tags"
)
_TAGS_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.tags+xml"
)
_REVIEWED_JA_ZH_TYPEFACES = frozenset(
    {
        "Yu Gothic",
        "Yu Mincho",
        "Microsoft YaHei",
        "Microsoft JhengHei",
        "SimSun",
        "SimHei",
        "Noto Sans CJK JP",
        "Noto Sans CJK SC",
        "Noto Serif CJK JP",
        "Noto Serif CJK SC",
    }
)
_OWNER_ROLE_BY_KIND = {
    "text": "actual-text-carrier",
    "shape": "primary-semantic-geometry",
    "image": "actual-image-crop-object",
    "table": "actual-native-table",
    "chart": "native-chart-object",
}
_QID = re.compile(r"^[A-Za-z][A-Za-z0-9._:-]{0,95}$")


class NativeSceneError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _mapping(value: object, code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise NativeSceneError(code)
    return dict(value)


def _string(value: object, code: str) -> str:
    if not isinstance(value, str) or not value:
        raise NativeSceneError(code)
    return value


def _number(value: object, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NativeSceneError(code)
    result = float(value)
    if not math.isfinite(result):
        raise NativeSceneError(code)
    return result


def _digest_payload(value: dict[str, Any], *names: str) -> tuple[str, dict[str, Any]]:
    payload = dict(value)
    actual: object | None = None
    for name in names:
        candidate = payload.pop(name, None)
        if actual is None and candidate is not None:
            actual = candidate
    if not isinstance(actual, str) or _DIGEST.fullmatch(actual) is None:
        raise NativeSceneError("authored_vnext_scene_digest_invalid")
    if canonical_digest(payload) != actual:
        raise NativeSceneError("authored_vnext_scene_digest_mismatch")
    return actual, payload


def _scene_value(scene: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in scene:
            return scene[name]
    return None


def _normalized_scene(scene_value: object) -> dict[str, Any]:
    scene = _mapping(scene_value, "authored_vnext_scene_required")
    if scene.get("schemaVersion") != SCENE_SCHEMA_VERSION:
        raise NativeSceneError("authored_vnext_scene_schema_unsupported")
    # The full pin is a transport/read-back witness.  Canonical Scene identity
    # contains its compositionDigest, not a duplicated mutable pin object.
    composition_pin = scene.pop("compositionPin", None)
    scene_digest, _ = _digest_payload(scene, "sceneDigest", "digest")
    if composition_pin is not None:
        scene["compositionPin"] = composition_pin
    slides = scene.get("slides")
    if not isinstance(slides, list) or not slides or len(slides) > 500:
        raise NativeSceneError("authored_vnext_scene_slides_invalid")
    if len({slide.get("slideId") for slide in slides if isinstance(slide, Mapping)}) != len(slides):
        raise NativeSceneError("authored_vnext_scene_slide_ids_invalid")
    return {**scene, "sceneDigest": scene_digest}


def _canvas(
    scene: dict[str, Any], master: dict[str, Any] | None = None
) -> tuple[float, float]:
    value = _scene_value(scene, "canvas", "slideSizePx", "size")
    if value is None and master is not None:
        value = master.get("slideSizePx")
    canvas = _mapping(value, "authored_vnext_scene_canvas_invalid")
    width = _number(_scene_value(canvas, "width", "widthPx"), "authored_vnext_scene_canvas_invalid")
    height = _number(_scene_value(canvas, "height", "heightPx"), "authored_vnext_scene_canvas_invalid")
    if width <= 0 or height <= 0 or width > 10000 or height > 10000:
        raise NativeSceneError("authored_vnext_scene_canvas_invalid")
    return width, height


def _bounds(element: dict[str, Any], canvas: tuple[float, float]) -> tuple[float, float, float, float]:
    value = _mapping(element.get("bounds"), "authored_vnext_scene_bounds_invalid")
    x = _number(value.get("x"), "authored_vnext_scene_bounds_invalid")
    y = _number(value.get("y"), "authored_vnext_scene_bounds_invalid")
    width = _number(value.get("width"), "authored_vnext_scene_bounds_invalid")
    height = _number(value.get("height"), "authored_vnext_scene_bounds_invalid")
    if width <= 0 or height <= 0 or x < 0 or y < 0:
        raise NativeSceneError("authored_vnext_scene_bounds_invalid")
    if x + width > canvas[0] + 0.01 or y + height > canvas[1] + 0.01:
        raise NativeSceneError("authored_vnext_scene_bounds_out_of_canvas")
    return x, y, width, height


def _color(value: object, default: str) -> RGBColor:
    candidate = value
    if isinstance(value, Mapping):
        candidate = value.get("color") or value.get("value")
    match = _HEX_COLOR.fullmatch(candidate) if isinstance(candidate, str) else None
    hexadecimal = match.group(1) if match is not None else default
    return RGBColor.from_string(hexadecimal.upper())


def _hex_color(value: object, default: str) -> str:
    candidate = value
    if isinstance(value, Mapping):
        candidate = value.get("color") or value.get("value")
    match = _HEX_COLOR.fullmatch(candidate) if isinstance(candidate, str) else None
    return f"#{(match.group(1) if match is not None else default).upper()}"


def _master_background(master: dict[str, Any]) -> dict[str, str]:
    tokens = master.get("tokens")
    if not isinstance(tokens, Mapping):
        raise NativeSceneError("authored_vnext_master_background_invalid")
    value = tokens.get("color.background")
    if not isinstance(value, str) or _HEX_COLOR.fullmatch(value) is None:
        raise NativeSceneError("authored_vnext_master_background_invalid")
    color = _hex_color(value, "FFFFFF")
    return {
        "furnitureId": _BACKGROUND_FURNITURE_ID,
        "kind": "background",
        "fill": color,
        "styleDigest": canonical_digest({"fill": color}),
    }


def _set_slide_background(slide: Any, color: str) -> None:
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = _color(color, "FFFFFF")


def _read_slide_background(slide: Any) -> str:
    try:
        value = slide.background.fill.fore_color.rgb
    except (AttributeError, TypeError, ValueError) as error:
        raise NativeSceneError(
            "authored_vnext_master_background_readback_failed"
        ) from error
    if value is None:
        raise NativeSceneError("authored_vnext_master_background_readback_failed")
    return f"#{str(value).upper()}"


def _relative_luminance(color: str) -> float:
    match = _HEX_COLOR.fullmatch(color)
    if match is None:
        raise NativeSceneError("authored_vnext_master_furniture_color_invalid")
    hexadecimal = match.group(1)
    channels = [int(hexadecimal[index:index + 2], 16) / 255 for index in (0, 2, 4)]
    linear = [
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(foreground: str, background: str) -> float:
    left = _relative_luminance(foreground)
    right = _relative_luminance(background)
    lighter, darker = max(left, right), min(left, right)
    return (lighter + 0.05) / (darker + 0.05)


def _mix_color(left: str, right: str, ratio: float) -> str:
    """Blend two validated colors for quiet Authored-owned data surfaces."""

    left_match = _HEX_COLOR.fullmatch(left)
    right_match = _HEX_COLOR.fullmatch(right)
    if left_match is None or right_match is None or not 0 <= ratio <= 1:
        raise NativeSceneError("authored_vnext_scene_native_theme_invalid")
    left_hex, right_hex = left_match.group(1), right_match.group(1)
    channels = [
        round(
            int(left_hex[index : index + 2], 16) * (1 - ratio)
            + int(right_hex[index : index + 2], 16) * ratio
        )
        for index in (0, 2, 4)
    ]
    return "#" + "".join(f"{channel:02X}" for channel in channels)


def _readable_color(background: str, *candidates: str) -> str:
    valid = [
        candidate
        for candidate in dict.fromkeys(candidates)
        if _HEX_COLOR.fullmatch(candidate) is not None
    ]
    if not valid:
        raise NativeSceneError("authored_vnext_scene_native_theme_invalid")
    best = max(valid, key=lambda candidate: _contrast_ratio(candidate, background))
    if _contrast_ratio(best, background) < 4.5:
        raise NativeSceneError("authored_vnext_scene_native_theme_contrast_unsupported")
    return best


def _native_body_theme(
    elements: list[tuple[int, dict[str, Any], str, str]],
    *,
    background_color: str,
) -> dict[str, str]:
    """Derive table/chart styling from the canonical Authored Scene, not furniture."""

    text_color = "#111827"
    font_family = "Aptos"
    surface = "#FFFFFF"
    divider = "#CBD5E1"
    accent = "#2563EB"
    for _z, element, _element_id, kind in elements:
        role = str(element.get("semanticRole") or "")
        if kind == "text" and role != "title":
            style_value = element.get("textStyle") or element.get("style") or {}
            style = dict(style_value) if isinstance(style_value, Mapping) else {}
            family = style.get("fontFamily")
            if isinstance(family, str) and family:
                font_family = family
            text_color = _hex_color(style.get("color"), text_color.removeprefix("#"))
        if kind != "shape":
            continue
        fill = _hex_color(element.get("fill"), surface.removeprefix("#"))
        stroke_value = element.get("stroke")
        if isinstance(stroke_value, str) and _HEX_COLOR.fullmatch(stroke_value):
            divider = _hex_color(stroke_value, divider.removeprefix("#"))
        role_folded = role.casefold()
        if any(marker in role_folded for marker in ("accent", "marker", "axis", "band", "rule", "hub")):
            accent = fill
        elif any(marker in role_folded for marker in ("panel", "surface", "row", "frame")):
            surface = fill
    text_color = _readable_color(surface, text_color, "#111827", "#FFFFFF")
    # A company-standard warning/accent can land in the mid-luminance range
    # where neither the body token nor white reaches WCAG AA. Keep the exact
    # accent, but allow true black as the final native header-text fallback.
    header_text = _readable_color(
        accent, surface, text_color, "#FFFFFF", "#111827", "#000000"
    )
    muted_surface = _mix_color(surface, background_color, 0.34)
    return {
        "fontFamily": font_family,
        "text": text_color,
        "headerText": header_text,
        "accent": accent,
        "surface": surface,
        "mutedSurface": muted_surface,
        "divider": divider,
        "background": background_color,
    }


def _furniture_text_style(
    item: dict[str, Any], master: dict[str, Any], background_color: str
) -> dict[str, Any]:
    requested = _hex_color(item.get("fill") or item.get("stroke"), "111827")
    tokens = master.get("tokens", {})
    tokens = dict(tokens) if isinstance(tokens, Mapping) else {}
    candidates = [
        requested,
        *(
            _hex_color(tokens.get(name), "111827")
            for name in (
                "color.text.primary",
                "color.text.secondary",
                "color.accent",
                "color.warning",
                "color.background",
            )
            if tokens.get(name) is not None
        ),
    ]
    effective: str | None = None
    ratio = 0.0
    for candidate in dict.fromkeys(candidates):
        candidate_ratio = _contrast_ratio(candidate, background_color)
        if candidate_ratio >= 4.5:
            effective = candidate
            ratio = candidate_ratio
            break
    if effective is None:
        raise NativeSceneError(
            "authored_vnext_master_furniture_contrast_unsupported"
        )
    font_manifest = master.get("fontManifest", {})
    font_manifest = (
        dict(font_manifest) if isinstance(font_manifest, Mapping) else {}
    )
    font_family = (
        tokens.get("font.body")
        or font_manifest.get("primary")
        or "Aptos"
    )
    if not isinstance(font_family, str) or not font_family:
        raise NativeSceneError("authored_vnext_master_furniture_font_invalid")
    return {
        "requestedColor": requested,
        "effectiveColor": effective,
        "fontFamily": font_family,
        "contrastRatio": round(ratio, 4),
        "contrastAdjusted": effective != requested,
        "styleDigest": canonical_digest(
            {"fontColor": effective, "fontFamily": font_family}
        ),
    }


def _element_id(element: dict[str, Any]) -> str:
    value = _scene_value(element, "elementId", "id")
    return _string(value, "authored_vnext_scene_element_id_invalid")


def _element_kind(element: dict[str, Any]) -> str:
    return _string(
        _scene_value(element, "kind", "type"),
        "authored_vnext_scene_element_kind_invalid",
    )


def _shape_description(
    *,
    element_id: str,
    kind: str,
    z_order: int,
    semantic_role: object,
    native_metadata: dict[str, Any] | None = None,
) -> str:
    return _DESCRIPTION_PREFIX + canonical_json(
        {
            "elementId": element_id,
            "kind": kind,
            "semanticRole": semantic_role if isinstance(semantic_role, str) else None,
            "zOrder": z_order,
            **(native_metadata or {}),
        }
    )


def _set_shape_identity(
    shape: Any,
    description: str,
    element_id: str,
    *,
    display_name: str | None = None,
) -> None:
    shape.name = display_name or element_id
    nodes = shape._element.xpath(".//p:cNvPr")
    if not nodes:
        raise NativeSceneError("authored_vnext_scene_element_identity_write_failed")
    nodes[0].set("name", display_name or element_id)
    nodes[0].set("descr", description)


def _owner_display_name(kind: str, qid: str) -> str:
    if _QID.fullmatch(qid) is None:
        raise NativeSceneError("authored_vnext_scene_qid_invalid")
    return f"Presenton hybrid {kind} {qid}"


def _owner_tags(kind: str, qid: str) -> dict[str, str]:
    role = _OWNER_ROLE_BY_KIND.get(kind)
    if role is None:
        raise NativeSceneError("authored_vnext_scene_owner_kind_unsupported")

    def carrier(prefix: str, value: str) -> str:
        encoded = base64.b32encode(value.encode("utf-8")).decode("ascii").rstrip("=")
        return f"{prefix}2_{encoded}"

    return {
        "PRESENTON_QID_V2": carrier("Q", qid),
        "PRESENTON_KIND_V2": carrier("K", kind),
        "PRESENTON_OWNER_ROLE_V2": carrier("R", role),
    }


def _tag_list_xml(tags: dict[str, str]) -> bytes:
    root = etree.Element(
        f"{{{_PRESENTATION_NS}}}tagLst",
        nsmap={"p": _PRESENTATION_NS},
    )
    for name, value in tags.items():
        etree.SubElement(
            root,
            f"{{{_PRESENTATION_NS}}}tag",
            name=name,
            val=value,
        )
    return etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", standalone=True
    )


def _inject_shape_owner_tags(content: bytes) -> bytes:
    """Attach PowerPoint Shape.Tags parts to every native semantic owner."""

    with ZipFile(BytesIO(content), "r") as source:
        entries = {name: source.read(name) for name in source.namelist()}
    tag_numbers = [
        int(match.group(1))
        for name in entries
        if (match := re.fullmatch(r"ppt/tags/tag([0-9]+)\.xml", name))
    ]
    next_tag_number = max(tag_numbers, default=0) + 1
    content_types = etree.fromstring(entries["[Content_Types].xml"])
    for slide_name in sorted(
        (
            name
            for name in entries
            if re.fullmatch(r"ppt/slides/slide[0-9]+\.xml", name)
        ),
        key=lambda name: int(re.search(r"([0-9]+)", name).group(1)),
    ):
        slide_number = int(re.search(r"([0-9]+)", slide_name).group(1))
        rels_name = f"ppt/slides/_rels/slide{slide_number}.xml.rels"
        relationships = etree.fromstring(entries[rels_name])
        relationship_ids = {
            node.get("Id")
            for node in relationships
            if isinstance(node.get("Id"), str)
        }
        next_relationship = max(
            (
                int(match.group(1))
                for value in relationship_ids
                if (match := re.fullmatch(r"rId([0-9]+)", value or ""))
            ),
            default=0,
        ) + 1
        slide_root = etree.fromstring(entries[slide_name])
        owners = slide_root.xpath(
            ".//p:cNvPr[starts-with(@descr, $prefix)]",
            namespaces={"p": _PRESENTATION_NS},
            prefix=_DESCRIPTION_PREFIX,
        )
        for owner in owners:
            try:
                metadata = json.loads(owner.get("descr")[len(_DESCRIPTION_PREFIX):])
            except (TypeError, ValueError) as error:
                raise NativeSceneError(
                    "authored_vnext_scene_element_identity_write_failed"
                ) from error
            qid = metadata.get("elementId")
            kind = metadata.get("kind")
            if not isinstance(qid, str) or not isinstance(kind, str):
                raise NativeSceneError(
                    "authored_vnext_scene_element_identity_write_failed"
                )
            expected_name = _owner_display_name(kind, qid)
            if owner.get("name") != expected_name:
                raise NativeSceneError(
                    "authored_vnext_scene_element_identity_write_failed"
                )
            non_visual = owner.getparent()
            application_properties = non_visual.find(
                f"{{{_PRESENTATION_NS}}}nvPr"
            )
            if application_properties is None or application_properties.find(
                f"{{{_PRESENTATION_NS}}}custDataLst"
            ) is not None:
                raise NativeSceneError("authored_vnext_scene_qid_tag_duplicate")
            relationship_id = f"rId{next_relationship}"
            next_relationship += 1
            tag_part = f"ppt/tags/tag{next_tag_number}.xml"
            next_tag_number += 1
            customer_data = etree.SubElement(
                application_properties,
                f"{{{_PRESENTATION_NS}}}custDataLst",
            )
            tag_reference = etree.SubElement(
                customer_data,
                f"{{{_PRESENTATION_NS}}}tags",
            )
            tag_reference.set(f"{{{_RELATIONSHIPS_NS}}}id", relationship_id)
            etree.SubElement(
                relationships,
                f"{{{_PACKAGE_RELS_NS}}}Relationship",
                Id=relationship_id,
                Type=_TAGS_REL_TYPE,
                Target=f"../tags/{tag_part.rsplit('/', 1)[-1]}",
            )
            etree.SubElement(
                content_types,
                f"{{{_CONTENT_TYPES_NS}}}Override",
                PartName=f"/{tag_part}",
                ContentType=_TAGS_CONTENT_TYPE,
            )
            entries[tag_part] = _tag_list_xml(_owner_tags(kind, qid))
        entries[slide_name] = etree.tostring(
            slide_root, xml_declaration=True, encoding="UTF-8", standalone=True
        )
        entries[rels_name] = etree.tostring(
            relationships,
            xml_declaration=True,
            encoding="UTF-8",
            standalone=True,
        )
    entries["[Content_Types].xml"] = etree.tostring(
        content_types, xml_declaration=True, encoding="UTF-8", standalone=True
    )
    destination = BytesIO()
    with ZipFile(destination, "w", ZIP_DEFLATED) as output:
        for name, value in entries.items():
            output.writestr(name, value)
    return destination.getvalue()


def _read_shape_owner_tags(content: bytes) -> list[dict[str, dict[str, str]]]:
    """Read and validate the exact OOXML relationship graph for Shape.Tags."""

    with ZipFile(BytesIO(content), "r") as archive:
        names = set(archive.namelist())
        slides: list[dict[str, dict[str, str]]] = []
        slide_names = sorted(
            (
                name
                for name in names
                if re.fullmatch(r"ppt/slides/slide[0-9]+\.xml", name)
            ),
            key=lambda name: int(re.search(r"([0-9]+)", name).group(1)),
        )
        referenced_tag_parts: set[str] = set()
        for slide_name in slide_names:
            slide_number = int(re.search(r"([0-9]+)", slide_name).group(1))
            rels_name = f"ppt/slides/_rels/slide{slide_number}.xml.rels"
            relationships = etree.fromstring(archive.read(rels_name))
            by_id = {
                node.get("Id"): node
                for node in relationships
                if node.get("Type") == _TAGS_REL_TYPE
            }
            root = etree.fromstring(archive.read(slide_name))
            slide_tags: dict[str, dict[str, str]] = {}
            for owner in root.xpath(
                ".//p:cNvPr[starts-with(@descr, $prefix)]",
                namespaces={"p": _PRESENTATION_NS},
                prefix=_DESCRIPTION_PREFIX,
            ):
                try:
                    metadata = json.loads(
                        owner.get("descr")[len(_DESCRIPTION_PREFIX):]
                    )
                except (TypeError, ValueError) as error:
                    raise NativeSceneError(
                        "authored_vnext_scene_qid_tag_readback_failed"
                    ) from error
                qid = metadata.get("elementId")
                kind = metadata.get("kind")
                if (
                    not isinstance(qid, str)
                    or not isinstance(kind, str)
                    or qid in slide_tags
                    or owner.get("name") != _owner_display_name(kind, qid)
                ):
                    raise NativeSceneError("authored_vnext_scene_qid_duplicate_or_ghost")
                non_visual = owner.getparent()
                references = non_visual.xpath(
                    "./p:nvPr/p:custDataLst/p:tags/@r:id",
                    namespaces={
                        "p": _PRESENTATION_NS,
                        "r": _RELATIONSHIPS_NS,
                    },
                )
                if len(references) != 1 or references[0] not in by_id:
                    raise NativeSceneError(
                        "authored_vnext_scene_qid_tag_readback_failed"
                    )
                relationship = by_id[references[0]]
                target = relationship.get("Target")
                if (
                    relationship.get("TargetMode") is not None
                    or not isinstance(target, str)
                    or re.fullmatch(r"\.\./tags/tag[0-9]+\.xml", target) is None
                ):
                    raise NativeSceneError(
                        "authored_vnext_scene_qid_tag_relationship_invalid"
                    )
                tag_part = f"ppt/tags/{target.rsplit('/', 1)[-1]}"
                if tag_part not in names or tag_part in referenced_tag_parts:
                    raise NativeSceneError(
                        "authored_vnext_scene_qid_tag_relationship_invalid"
                    )
                referenced_tag_parts.add(tag_part)
                tag_root = etree.fromstring(archive.read(tag_part))
                tags: dict[str, str] = {}
                for tag in tag_root.xpath(
                    "./p:tag", namespaces={"p": _PRESENTATION_NS}
                ):
                    name = tag.get("name")
                    value = tag.get("val")
                    if (
                        not isinstance(name, str)
                        or not isinstance(value, str)
                        or name in tags
                    ):
                        raise NativeSceneError(
                            "authored_vnext_scene_qid_tag_duplicate"
                        )
                    tags[name] = value
                if tags != _owner_tags(kind, qid):
                    raise NativeSceneError(
                        "authored_vnext_scene_qid_tag_readback_failed"
                    )
                slide_tags[qid] = tags
            slides.append(slide_tags)
        orphan_tag_parts = {
            name for name in names if re.fullmatch(r"ppt/tags/tag[0-9]+\.xml", name)
        }.difference(referenced_tag_parts)
        if orphan_tag_parts:
            raise NativeSceneError("authored_vnext_scene_qid_tag_relationship_invalid")
    return slides


def _add_furniture(
    slide: Any,
    master: dict[str, Any],
    assets: dict[str, tuple[str, bytes, str]],
    *,
    page_number: int,
    canvas: tuple[float, float],
    background_color: str,
) -> list[dict[str, Any]]:
    furniture = master.get("furniture", [])
    if not isinstance(furniture, list):
        raise NativeSceneError("authored_vnext_master_furniture_invalid")
    identities: list[dict[str, Any]] = []
    for item_value in furniture:
        item = _mapping(item_value, "authored_vnext_master_furniture_invalid")
        visibility = item.get("visibility", "always")
        if visibility == "hidden" or visibility == "coverOnly" and page_number != 1 or visibility == "exceptCover" and page_number == 1:
            continue
        furniture_id = _string(item.get("id"), "authored_vnext_master_furniture_invalid")
        kind = _string(item.get("kind"), "authored_vnext_master_furniture_invalid")
        x, y, width, height = _bounds(item, canvas)
        geometry = tuple(Inches(value / 96) for value in (x, y, width, height))
        if kind == "logo":
            asset_id = item.get("assetId")
            if isinstance(asset_id, str):
                record = assets.get(asset_id)
                if record is None:
                    raise NativeSceneError("authored_vnext_master_asset_bytes_unavailable")
                shape = slide.shapes.add_picture(BytesIO(record[1]), *geometry)
                content_identity: dict[str, Any] = {"assetDigest": record[0]}
            else:
                # An unconfigured logo is still an explicit locked furniture
                # zone. Preserve its identity without inventing brand pixels.
                shape = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, *geometry)
                shape.fill.background()
                shape.line.fill.background()
                content_identity = {"unconfigured": True}
        elif kind in {"text", "pageNumber"}:
            shape = slide.shapes.add_textbox(*geometry)
            text = str(page_number) if kind == "pageNumber" else item.get("text")
            text_style = _furniture_text_style(item, master, background_color)
            _apply_text(
                shape,
                {
                    "text": str(text or ""),
                    "textStyle": {
                        "fontPt": 10,
                        "fontFamily": text_style["fontFamily"],
                        "color": text_style["effectiveColor"],
                    },
                },
            )
            content_identity = {
                "textDigest": canonical_digest({"text": str(text or "")}),
                "textStyle": text_style,
            }
        elif kind in {"line", "rect"}:
            shape = slide.shapes.add_shape(MSO_AUTO_SHAPE_TYPE.RECTANGLE, *geometry)
            shape.fill.solid()
            shape.fill.fore_color.rgb = _color(item.get("fill") or item.get("stroke"), "94A3B8")
            shape.line.color.rgb = _color(item.get("stroke"), "94A3B8")
            content_identity = {"nativeKind": "shape"}
        else:
            raise NativeSceneError("authored_vnext_master_furniture_capability_unsupported")
        _set_shape_identity(
            shape,
            _FURNITURE_DESCRIPTION_PREFIX + canonical_json(
                {"furnitureId": furniture_id, "kind": kind, **content_identity}
            ),
            furniture_id,
        )
        identities.append(
            {"furnitureId": furniture_id, "kind": kind, **content_identity}
        )
    return identities


def _text_payload(element: dict[str, Any]) -> tuple[str, list[dict[str, Any]] | None]:
    content = element.get("content")
    source = dict(content) if isinstance(content, Mapping) else element
    runs = source.get("runs")
    if runs is not None:
        if not isinstance(runs, list) or not runs:
            raise NativeSceneError("authored_vnext_scene_text_invalid")
        normalized: list[dict[str, Any]] = []
        for run in runs:
            item = _mapping(run, "authored_vnext_scene_text_invalid")
            text = item.get("text")
            if not isinstance(text, str):
                raise NativeSceneError("authored_vnext_scene_text_invalid")
            normalized.append(item)
        return "".join(str(item["text"]) for item in normalized), normalized
    text = source.get("text")
    if not isinstance(text, str):
        raise NativeSceneError("authored_vnext_scene_text_invalid")
    return text, None


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _text_run_map(
    text: str, runs: list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
    """Return the COM-compatible UTF-16 identity map, including empty runs."""

    source = runs if runs is not None else [{"text": text}]
    offset = 0
    result: list[dict[str, Any]] = []
    for index, item in enumerate(source):
        value = str(item["text"])
        length = _utf16_length(value)
        result.append(
            {
                "runIndex": index,
                "utf16Start": offset,
                "utf16Length": length,
                "textDigest": canonical_digest({"text": value}),
            }
        )
        offset += length
    return result


def _run_map_from_shape(shape: Any) -> list[dict[str, Any]]:
    runs = [run.text for paragraph in shape.text_frame.paragraphs for run in paragraph.runs]
    offset = 0
    result: list[dict[str, Any]] = []
    for index, value in enumerate(runs):
        logical = value.replace("\v", "\n")
        length = _utf16_length(logical)
        result.append(
            {
                "runIndex": index,
                "utf16Start": offset,
                "utf16Length": length,
                "textDigest": canonical_digest({"text": logical}),
            }
        )
        offset += length
    return result


def _intended_text_style_projection(element: dict[str, Any]) -> list[dict[str, Any]]:
    """Seal the run styles that are supposed to reach native DrawingML."""

    text, runs = _text_payload(element)
    style_value = element.get("textStyle") or element.get("style") or {}
    style = dict(style_value) if isinstance(style_value, Mapping) else {}
    source = runs if runs is not None else [{"text": text}]
    projection: list[dict[str, Any]] = []
    for item in source:
        font_size = (
            item.get("fontSize")
            or item.get("fontPt")
            or style.get("fontSize")
            or style.get("fontPt")
        )
        font_weight = item.get("fontWeight", style.get("fontWeight"))
        projection.append(
            {
                "fontFamily": str(
                    item.get("fontFamily") or style.get("fontFamily") or "Aptos"
                ),
                "fontSizePt": (
                    None
                    if font_size is None
                    else round(
                        _number(
                            font_size,
                            "authored_vnext_scene_text_style_invalid",
                        ),
                        6,
                    )
                ),
                "bold": bool(
                    item.get("bold", style.get("bold", False))
                    or isinstance(font_weight, (int, float))
                    and not isinstance(font_weight, bool)
                    and font_weight >= 600
                ),
                "italic": bool(item.get("italic", style.get("italic", False))),
                "underline": bool(
                    item.get("underline", style.get("underline", False))
                ),
                "color": _hex_color(
                    item.get("color") or style.get("color"), "111827"
                ),
            }
        )
    return projection


def _native_text_style_projection(shape: Any) -> list[dict[str, Any]]:
    """Read the persisted native run styles from the candidate PPTX."""

    projection: list[dict[str, Any]] = []
    for paragraph in shape.text_frame.paragraphs:
        for run in paragraph.runs:
            color = run.font.color.rgb
            family = run.font.name
            if color is None or not isinstance(family, str) or not family:
                raise NativeSceneError(
                    "authored_vnext_scene_text_style_readback_failed"
                )
            projection.append(
                {
                    "fontFamily": family,
                    "fontSizePt": (
                        None
                        if run.font.size is None
                        else round(float(run.font.size.pt), 6)
                    ),
                    "bold": bool(run.font.bold),
                    "italic": bool(run.font.italic),
                    "underline": bool(run.font.underline),
                    "color": f"#{str(color).upper()}",
                }
            )
    return projection


def _text_frame_margin(style: Mapping[str, Any], key: str) -> float:
    value = style.get(key)
    if value is None:
        return 0.0
    margin = _number(value, "authored_vnext_scene_text_style_invalid")
    if margin < 0 or margin > 1000:
        raise NativeSceneError("authored_vnext_scene_text_style_invalid")
    return margin


def _set_run_typefaces(run: Any, family: str) -> None:
    """Pin Latin, East Asian, and complex-script faces in DrawingML.

    python-pptx's ``Font.name`` currently writes only ``a:latin``. Korean text
    without ``a:ea`` is allowed to fall through to the theme's East Asian face,
    changing glyph advances and therefore line wrapping.
    """

    run.font.name = family
    run_properties = run._r.get_or_add_rPr()
    for local_name in ("latin", "ea", "cs"):
        tag = f"{{{_DRAWING_NS}}}{local_name}"
        typeface = run_properties.find(tag)
        if typeface is None:
            typeface = etree.SubElement(run_properties, tag)
        typeface.set("typeface", family)


def _apply_text(shape: Any, element: dict[str, Any]) -> None:
    text, runs = _text_payload(element)
    style_value = element.get("textStyle") or element.get("style") or {}
    style = dict(style_value) if isinstance(style_value, Mapping) else {}
    frame = shape.text_frame
    frame.clear()
    frame.word_wrap = True
    # Scene compilation has already measured and fitted copy.  The python-pptx
    # default is SHAPE_TO_FIT_TEXT (<a:spAutoFit/>), which lets desktop
    # PowerPoint resize edited Korean/English copy after first-open.  Every
    # admitted native text owner therefore keeps its sealed box geometry.
    auto_fit = style.get("autoFit", "none")
    if auto_fit not in {"none", "shrink"}:
        raise NativeSceneError("authored_vnext_scene_text_autofit_unsupported")
    frame.auto_size = (
        MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
        if auto_fit == "shrink"
        else MSO_AUTO_SIZE.NONE
    )
    frame.margin_left = Pt(_text_frame_margin(style, "marginLeftPt"))
    frame.margin_right = Pt(_text_frame_margin(style, "marginRightPt"))
    frame.margin_top = Pt(_text_frame_margin(style, "marginTopPt"))
    frame.margin_bottom = Pt(_text_frame_margin(style, "marginBottomPt"))
    vertical_align = style.get("verticalAlign")
    frame.vertical_anchor = {
        "top": MSO_VERTICAL_ANCHOR.TOP,
        "middle": MSO_VERTICAL_ANCHOR.MIDDLE,
        "bottom": MSO_VERTICAL_ANCHOR.BOTTOM,
    }.get(vertical_align, MSO_VERTICAL_ANCHOR.TOP)
    paragraph = frame.paragraphs[0]
    paragraph.text = "" if runs else text
    paragraph.alignment = {
        "left": PP_ALIGN.LEFT,
        "center": PP_ALIGN.CENTER,
        "right": PP_ALIGN.RIGHT,
        "justify": PP_ALIGN.JUSTIFY,
    }.get(style.get("textAlign"), PP_ALIGN.LEFT)
    paragraph.space_before = Pt(0)
    paragraph.space_after = Pt(0)
    line_height = style.get("lineHeight")
    if line_height is not None:
        spacing = _number(
            line_height, "authored_vnext_scene_text_style_invalid"
        )
        if spacing < 0.5 or spacing > 5:
            raise NativeSceneError("authored_vnext_scene_text_style_invalid")
        paragraph.line_spacing = spacing
    if runs:
        target_runs = []
        for item in runs:
            run = paragraph.add_run()
            run.text = str(item["text"])
            target_runs.append((run, item))
    else:
        # Assigning text with explicit line breaks makes python-pptx create one
        # run per visual line.  Style every generated run; styling only index 0
        # leaves later Korean/CJK lines at the Office theme default.
        target_runs = [(run, {"text": run.text}) for run in paragraph.runs]
    for run, item in target_runs:
        font_family = str(
            item.get("fontFamily") or style.get("fontFamily") or "Aptos"
        )
        _set_run_typefaces(run, font_family)
        font_size = item.get("fontSize") or item.get("fontPt") or style.get("fontSize") or style.get("fontPt")
        if font_size is not None:
            size = _number(font_size, "authored_vnext_scene_text_style_invalid")
            if size <= 0 or size > 400:
                raise NativeSceneError("authored_vnext_scene_text_style_invalid")
            run.font.size = Pt(size)
        font_weight = item.get("fontWeight", style.get("fontWeight"))
        run.font.bold = bool(
            item.get("bold", style.get("bold", False))
            or isinstance(font_weight, (int, float)) and not isinstance(font_weight, bool) and font_weight >= 600
        )
        run.font.italic = bool(item.get("italic", style.get("italic", False)))
        run.font.underline = bool(item.get("underline", style.get("underline", False)))
        run.font.color.rgb = _color(item.get("color") or style.get("color"), "111827")


def _shape_type(element: dict[str, Any]) -> MSO_AUTO_SHAPE_TYPE:
    shape_type = str(_scene_value(element, "shapeType", "shape") or "rect")
    mapping = {
        "rect": MSO_AUTO_SHAPE_TYPE.RECTANGLE,
        "rectangle": MSO_AUTO_SHAPE_TYPE.RECTANGLE,
        "roundRect": MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE,
        "roundedRectangle": MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE,
        "ellipse": MSO_AUTO_SHAPE_TYPE.OVAL,
        "triangle": MSO_AUTO_SHAPE_TYPE.ISOSCELES_TRIANGLE,
        "diamond": MSO_AUTO_SHAPE_TYPE.DIAMOND,
        "chevron": MSO_AUTO_SHAPE_TYPE.CHEVRON,
        # A Scene shape-line has no endpoint semantics and remains a regular
        # editable shape.  Connector IR is a separate unsupported capability.
        "line": MSO_AUTO_SHAPE_TYPE.RECTANGLE,
    }
    result = mapping.get(shape_type)
    if result is None:
        raise NativeSceneError("authored_vnext_scene_shape_capability_unsupported")
    return result


def _asset_lookup(assets_value: object) -> dict[str, tuple[str, bytes, str]]:
    if not isinstance(assets_value, list):
        raise NativeSceneError("authored_vnext_scene_assets_invalid")
    result: dict[str, tuple[str, bytes, str]] = {}
    by_digest: dict[str, tuple[str, bytes, str]] = {}
    for value in assets_value:
        asset = _mapping(value, "authored_vnext_scene_assets_invalid")
        asset_id = _string(asset.get("assetId"), "authored_vnext_scene_assets_invalid")
        media_type = _string(asset.get("mediaType"), "authored_vnext_scene_assets_invalid")
        if media_type not in {"image/png", "image/jpeg"}:
            raise NativeSceneError("authored_vnext_scene_image_media_type_unsupported")
        raw_digest = _string(asset.get("sha256"), "authored_vnext_scene_assets_invalid")
        size_bytes = asset.get("sizeBytes")
        if re.fullmatch(r"[0-9a-f]{64}", raw_digest) is None:
            raise NativeSceneError("authored_vnext_scene_assets_invalid")
        try:
            content = base64.b64decode(asset.get("bytesBase64"), validate=True)
        except (TypeError, ValueError, binascii.Error) as error:
            raise NativeSceneError("authored_vnext_scene_asset_bytes_invalid") from error
        digest = f"sha256:{raw_digest}"
        if (
            isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or size_bytes != len(content)
        ):
            raise NativeSceneError("authored_vnext_scene_asset_size_mismatch")
        if not content or f"sha256:{sha256(content).hexdigest()}" != digest:
            raise NativeSceneError("authored_vnext_scene_asset_digest_mismatch")
        try:
            decoded_image = PptxImage.from_blob(content)
        except Exception as error:
            raise NativeSceneError("authored_vnext_scene_image_bytes_invalid") from error
        if decoded_image.content_type != media_type:
            raise NativeSceneError(
                "authored_vnext_scene_image_media_type_mismatch"
            )
        record = (digest, content, media_type)
        if asset_id in result or digest in by_digest:
            raise NativeSceneError("authored_vnext_scene_asset_identity_duplicate")
        result[asset_id] = record
        by_digest[digest] = record
    return {**result, **by_digest}


def _data_lookup(scene: dict[str, Any]) -> dict[str, tuple[str, str, dict[str, Any]]]:
    manifest = scene.get("dataManifest", [])
    if not isinstance(manifest, list):
        raise NativeSceneError("authored_vnext_scene_data_manifest_invalid")
    result: dict[str, tuple[str, str, dict[str, Any]]] = {}
    for value in manifest:
        item = _mapping(value, "authored_vnext_scene_data_manifest_invalid")
        data_id = _string(item.get("dataId"), "authored_vnext_scene_data_manifest_invalid")
        digest = _string(item.get("dataDigest"), "authored_vnext_scene_data_manifest_invalid")
        kind = _string(item.get("kind"), "authored_vnext_scene_data_manifest_invalid")
        payload = _mapping(item.get("payload"), "authored_vnext_scene_data_manifest_invalid")
        if (
            data_id in result
            or kind not in {"table", "chart"}
            or _DIGEST.fullmatch(digest) is None
            or canonical_digest(payload) != digest
        ):
            raise NativeSceneError("authored_vnext_scene_data_manifest_invalid")
        result[data_id] = (digest, kind, payload)
    return result


def _table_payload(
    payload: dict[str, Any], *, rows: int, columns: int
) -> list[list[str]]:
    if "cells" not in payload or not set(payload).issubset(
        {"cells", "cellStyles", "columnWidthsPt", "rowHeightsPt", "mergedCells"}
    ):
        raise NativeSceneError("authored_vnext_scene_table_data_invalid")
    cells = payload.get("cells")
    if (
        isinstance(rows, bool)
        or not isinstance(rows, int)
        or isinstance(columns, bool)
        or not isinstance(columns, int)
        or not isinstance(cells, list)
        or len(cells) != rows
        or rows < 1
        or rows > 100
        or columns < 1
        or columns > 30
        or any(
            not isinstance(row, list)
            or len(row) != columns
            or any(not isinstance(cell, str) or len(cell) > 5000 for cell in row)
            for row in cells
        )
    ):
        raise NativeSceneError("authored_vnext_scene_table_data_invalid")
    return cells


def _table_row_heights(payload: dict[str, Any], *, rows: int) -> list[int] | None:
    value = payload.get("rowHeightsPt")
    if value is None:
        return None
    if (
        not isinstance(value, list)
        or len(value) != rows
        or any(
            isinstance(height, bool)
            or not isinstance(height, (int, float))
            or not math.isfinite(float(height))
            or float(height) <= 0
            or float(height) > 1000
            for height in value
        )
    ):
        raise NativeSceneError("authored_vnext_scene_table_row_heights_invalid")
    return [int(Pt(float(height))) for height in value]


def _table_merged_cells(
    payload: dict[str, Any], *, rows: int, columns: int
) -> list[dict[str, int]]:
    value = payload.get("mergedCells")
    if value is None:
        return []
    if not isinstance(value, list):
        raise NativeSceneError("authored_vnext_scene_table_merge_invalid")
    occupied: set[tuple[int, int]] = set()
    result: list[dict[str, int]] = []
    for raw in value:
        item = _mapping(raw, "authored_vnext_scene_table_merge_invalid")
        if set(item) != {"row", "column", "rowSpan", "columnSpan"}:
            raise NativeSceneError("authored_vnext_scene_table_merge_invalid")
        if any(isinstance(item[key], bool) or not isinstance(item[key], int) for key in item):
            raise NativeSceneError("authored_vnext_scene_table_merge_invalid")
        row = item["row"]
        column = item["column"]
        row_span = item["rowSpan"]
        column_span = item["columnSpan"]
        if (
            row < 0
            or column < 0
            or row_span < 1
            or column_span < 1
            or row_span * column_span < 2
            or row + row_span > rows
            or column + column_span > columns
        ):
            raise NativeSceneError("authored_vnext_scene_table_merge_invalid")
        covered = {
            (row_index, column_index)
            for row_index in range(row, row + row_span)
            for column_index in range(column, column + column_span)
        }
        if occupied.intersection(covered):
            raise NativeSceneError("authored_vnext_scene_table_merge_invalid")
        occupied.update(covered)
        result.append(dict(item))
    return result


def _table_column_widths(
    payload: dict[str, Any], *, columns: int, total_width_emu: int
) -> list[int] | None:
    value = payload.get("columnWidthsPt")
    if value is None:
        return None
    if (
        not isinstance(value, list)
        or len(value) != columns
        or any(
            isinstance(width, bool)
            or not isinstance(width, (int, float))
            or not math.isfinite(float(width))
            or float(width) <= 0
            for width in value
        )
    ):
        raise NativeSceneError("authored_vnext_scene_table_column_widths_invalid")
    widths = [int(Pt(float(width))) for width in value]
    if abs(sum(widths) - total_width_emu) > int(Pt(0.25)):
        raise NativeSceneError("authored_vnext_scene_table_column_widths_invalid")
    widths[-1] += total_width_emu - sum(widths)
    return widths


def _set_cell_border(cell: Any, side: str, value: object) -> None:
    style = _mapping(value, "authored_vnext_scene_table_cell_style_invalid")
    if set(style) != {"color", "widthPt"}:
        raise NativeSceneError("authored_vnext_scene_table_cell_style_invalid")
    width = _number(
        style.get("widthPt"), "authored_vnext_scene_table_cell_style_invalid"
    )
    if width < 0 or width > 100:
        raise NativeSceneError("authored_vnext_scene_table_cell_style_invalid")
    color = _hex_color(
        style.get("color"), "64748B"
    ).removeprefix("#")
    properties = cell._tc.get_or_add_tcPr()
    tag = f"{{{_DRAWING_NS}}}ln{side}"
    for existing in properties.findall(tag):
        properties.remove(existing)
    line = etree.SubElement(properties, tag, w=str(int(Pt(width))))
    solid = etree.SubElement(line, f"{{{_DRAWING_NS}}}solidFill")
    etree.SubElement(solid, f"{{{_DRAWING_NS}}}srgbClr", val=color)
    etree.SubElement(line, f"{{{_DRAWING_NS}}}prstDash", val="solid")


def _table_cell_style(
    payload: dict[str, Any], *, row: int, column: int, rows: int, columns: int
) -> dict[str, Any] | None:
    value = payload.get("cellStyles")
    if value is None:
        return None
    if (
        not isinstance(value, list)
        or len(value) != rows
        or any(not isinstance(item, list) or len(item) != columns for item in value)
    ):
        raise NativeSceneError("authored_vnext_scene_table_cell_style_invalid")
    style = _mapping(
        value[row][column], "authored_vnext_scene_table_cell_style_invalid"
    )
    if not set(style).issubset(
        {"fill", "borders", "horizontalAlign", "verticalAlign"}
    ):
        raise NativeSceneError("authored_vnext_scene_table_cell_style_invalid")
    return style


def _resolved_table_style_projection(
    payload: dict[str, Any],
    *,
    rows: int,
    columns: int,
    total_width_emu: int,
    theme: dict[str, str],
) -> dict[str, Any]:
    widths = _table_column_widths(
        payload, columns=columns, total_width_emu=total_width_emu
    )
    if widths is None:
        widths = [0] * columns
        widths[0] = round(total_width_emu * (0.34 if columns > 1 else 1.0))
        if columns > 1:
            remaining = total_width_emu - widths[0]
            for column_index in range(1, columns):
                widths[column_index] = round(remaining / (columns - 1))
            widths[-1] += total_width_emu - sum(widths)
    cells: list[list[dict[str, Any]]] = []
    for row in range(rows):
        projected_row: list[dict[str, Any]] = []
        for column in range(columns):
            style = _table_cell_style(
                payload,
                row=row,
                column=column,
                rows=rows,
                columns=columns,
            ) or {}
            projected: dict[str, Any] = {
                "fill": _hex_color(
                    style.get("fill")
                    or (
                        theme["accent"]
                        if row == 0
                        else theme["mutedSurface"]
                        if row % 2 == 0
                        else theme["surface"]
                    ),
                    "FFFFFF",
                ),
                "horizontalAlign": style.get("horizontalAlign")
                or ("left" if column == 0 else "center"),
                "verticalAlign": style.get("verticalAlign") or "middle",
            }
            borders = style.get("borders")
            if borders is not None:
                border_map = _mapping(
                    borders, "authored_vnext_scene_table_cell_style_invalid"
                )
                projected_borders: dict[str, dict[str, Any]] = {}
                for side in ("top", "right", "bottom", "left"):
                    if border_map.get(side) is None:
                        continue
                    border = _mapping(
                        border_map[side],
                        "authored_vnext_scene_table_cell_style_invalid",
                    )
                    projected_borders[side] = {
                        "color": _hex_color(border.get("color"), "64748B"),
                        "widthPt": _number(
                            border.get("widthPt"),
                            "authored_vnext_scene_table_cell_style_invalid",
                        ),
                    }
                if projected_borders:
                    projected["borders"] = projected_borders
            projected_row.append(projected)
        cells.append(projected_row)
    row_heights = _table_row_heights(payload, rows=rows)
    projection: dict[str, Any] = {
        "columnWidthsPt": [round(width / 12700, 6) for width in widths],
        "cellStyles": cells,
    }
    if row_heights is not None:
        projection["rowHeightsPt"] = [
            round(height / 12700, 6) for height in row_heights
        ]
    if payload.get("mergedCells") is not None:
        projection["mergedCells"] = _table_merged_cells(
            payload, rows=rows, columns=columns
        )
    return projection


def _read_table_style_projection(
    shape: Any, *, include_row_heights: bool = False, include_merges: bool = False
) -> dict[str, Any]:
    alignments = {
        PP_ALIGN.LEFT: "left",
        PP_ALIGN.CENTER: "center",
        PP_ALIGN.RIGHT: "right",
        PP_ALIGN.JUSTIFY: "justify",
    }
    verticals = {
        MSO_VERTICAL_ANCHOR.TOP: "top",
        MSO_VERTICAL_ANCHOR.MIDDLE: "middle",
        MSO_VERTICAL_ANCHOR.BOTTOM: "bottom",
    }
    cells: list[list[dict[str, Any]]] = []
    for row in range(len(shape.table.rows)):
        projected_row: list[dict[str, Any]] = []
        for column in range(len(shape.table.columns)):
            cell = shape.table.cell(row, column)
            fill_rgb = cell.fill.fore_color.rgb
            if fill_rgb is None:
                raise NativeSceneError(
                    "authored_vnext_scene_table_style_readback_failed"
                )
            paragraph = cell.text_frame.paragraphs[0]
            projected: dict[str, Any] = {
                "fill": f"#{str(fill_rgb).upper()}",
                "horizontalAlign": alignments.get(paragraph.alignment),
                "verticalAlign": verticals.get(cell.vertical_anchor),
            }
            if (
                projected["horizontalAlign"] is None
                or projected["verticalAlign"] is None
            ):
                raise NativeSceneError(
                    "authored_vnext_scene_table_style_readback_failed"
                )
            borders: dict[str, dict[str, Any]] = {}
            properties = cell._tc.get_or_add_tcPr()
            for side, tag_suffix in {
                "top": "T",
                "right": "R",
                "bottom": "B",
                "left": "L",
            }.items():
                line = properties.find(f"{{{_DRAWING_NS}}}ln{tag_suffix}")
                if line is None:
                    continue
                color_node = line.find(
                    f"{{{_DRAWING_NS}}}solidFill/{{{_DRAWING_NS}}}srgbClr"
                )
                width = line.get("w")
                if (
                    color_node is None
                    or not isinstance(color_node.get("val"), str)
                    or not isinstance(width, str)
                    or not width.isdigit()
                ):
                    raise NativeSceneError(
                        "authored_vnext_scene_table_style_readback_failed"
                    )
                borders[side] = {
                    "color": f"#{color_node.get('val').upper()}",
                    "widthPt": round(int(width) / 12700, 6),
                }
            if borders:
                projected["borders"] = borders
            projected_row.append(projected)
        cells.append(projected_row)
    merged_cells: list[dict[str, int]] = []
    for row in range(len(shape.table.rows)):
        for column in range(len(shape.table.columns)):
            cell = shape.table.cell(row, column)
            if not cell.is_merge_origin:
                continue
            row_span = int(cell._tc.get("rowSpan") or 1)
            column_span = int(cell._tc.get("gridSpan") or 1)
            merged_cells.append(
                {
                    "row": row,
                    "column": column,
                    "rowSpan": row_span,
                    "columnSpan": column_span,
                }
            )
    projection: dict[str, Any] = {
        "columnWidthsPt": [
            round(column.width / 12700, 6) for column in shape.table.columns
        ],
        "cellStyles": cells,
    }
    if include_row_heights:
        projection["rowHeightsPt"] = [
            round(row.height / 12700, 6) for row in shape.table.rows
        ]
    if include_merges:
        projection["mergedCells"] = merged_cells
    return projection


def _chart_payload(payload: dict[str, Any]) -> tuple[list[str], list[tuple[str, list[float]]]]:
    if not {"categories", "series"}.issubset(payload) or not set(payload).issubset(
        {"categories", "series", "style"}
    ):
        raise NativeSceneError("authored_vnext_scene_chart_data_invalid")
    categories = payload.get("categories")
    series_value = payload.get("series")
    if (
        not isinstance(categories, list)
        or not categories
        or len(categories) > 100
        or any(not isinstance(item, str) or not item or len(item) > 500 for item in categories)
        or not isinstance(series_value, list)
        or not series_value
        or len(series_value) > 30
    ):
        raise NativeSceneError("authored_vnext_scene_chart_data_invalid")
    series: list[tuple[str, list[float]]] = []
    for value in series_value:
        item = _mapping(value, "authored_vnext_scene_chart_data_invalid")
        name = item.get("name")
        values = item.get("values")
        if (
            set(item) != {"name", "values"}
            or not isinstance(name, str)
            or not name
            or len(name) > 500
            or not isinstance(values, list)
            or len(values) != len(categories)
            or any(
                isinstance(number, bool)
                or not isinstance(number, (int, float))
                or not math.isfinite(float(number))
                for number in values
            )
        ):
            raise NativeSceneError("authored_vnext_scene_chart_data_invalid")
        series.append((name, [float(number) for number in values]))
    if len({name for name, _values in series}) != len(series):
        raise NativeSceneError("authored_vnext_scene_chart_data_invalid")
    return categories, series


def _chart_style_projection(
    payload: dict[str, Any], *, series_count: int, theme: dict[str, str]
) -> dict[str, Any]:
    raw = payload.get("style") or {}
    style = _mapping(raw, "authored_vnext_scene_chart_style_invalid")
    if not set(style).issubset({"chartStyle", "seriesColors", "legend"}):
        raise NativeSceneError("authored_vnext_scene_chart_style_invalid")
    chart_style = style.get("chartStyle", 2)
    legend = style.get("legend")
    series_colors = style.get("seriesColors")
    if (
        isinstance(chart_style, bool)
        or not isinstance(chart_style, int)
        or chart_style < 1
        or chart_style > 48
        or legend is not None
        and not isinstance(legend, bool)
    ):
        raise NativeSceneError("authored_vnext_scene_chart_style_invalid")
    palette = [
        theme["accent"],
        _mix_color(theme["accent"], theme["text"], 0.28),
        _mix_color(theme["accent"], theme["surface"], 0.35),
        _mix_color(theme["accent"], theme["text"], 0.52),
    ]
    if series_colors is not None:
        if (
            not isinstance(series_colors, list)
            or len(series_colors) != series_count
        ):
            raise NativeSceneError("authored_vnext_scene_chart_style_invalid")
        colors = [_hex_color(value, "1463FF") for value in series_colors]
    else:
        colors = [palette[index % len(palette)] for index in range(series_count)]
    return {"chartStyle": chart_style, "seriesColors": colors, "legend": legend}


def _shape_bounds_px(shape: Any) -> dict[str, float]:
    return {
        "x": round(shape.left / 914400 * 96, 6),
        "y": round(shape.top / 914400 * 96, 6),
        "width": round(shape.width / 914400 * 96, 6),
        "height": round(shape.height / 914400 * 96, 6),
    }


def _picture_crop(shape: Any) -> dict[str, float]:
    return {
        "left": round(float(shape.crop_left), 6),
        "top": round(float(shape.crop_top), 6),
        "right": round(float(shape.crop_right), 6),
        "bottom": round(float(shape.crop_bottom), 6),
    }


def _add_scene_picture(
    slide: Any,
    content: bytes,
    geometry_px: tuple[float, float, float, float],
    element: dict[str, Any],
) -> Any:
    x, y, width, height = geometry_px
    crop_value = element.get("cropGeometry")
    if crop_value is not None:
        crop = _mapping(crop_value, "authored_vnext_scene_image_crop_invalid")
        if set(crop) != {"left", "top", "right", "bottom"}:
            raise NativeSceneError("authored_vnext_scene_image_crop_invalid")
        insets = {
            key: _number(crop[key], "authored_vnext_scene_image_crop_invalid")
            for key in ("left", "top", "right", "bottom")
        }
        if (
            any(value < 0 or value >= 1 for value in insets.values())
            or insets["left"] + insets["right"] >= 1
            or insets["top"] + insets["bottom"] >= 1
        ):
            raise NativeSceneError("authored_vnext_scene_image_crop_invalid")
        shape = slide.shapes.add_picture(
            BytesIO(content),
            Inches(x / 96),
            Inches(y / 96),
            Inches(width / 96),
            Inches(height / 96),
        )
        shape.crop_left = insets["left"]
        shape.crop_top = insets["top"]
        shape.crop_right = insets["right"]
        shape.crop_bottom = insets["bottom"]
        return shape
    image_width, image_height = PptxImage.from_blob(content).size
    if image_width <= 0 or image_height <= 0:
        raise NativeSceneError("authored_vnext_scene_image_bytes_invalid")
    image_ratio = image_width / image_height
    frame_ratio = width / height
    crop_mode = element.get("crop", "cover")
    if crop_mode == "contain":
        if image_ratio >= frame_ratio:
            rendered_width = width
            rendered_height = width / image_ratio
        else:
            rendered_height = height
            rendered_width = height * image_ratio
        return slide.shapes.add_picture(
            BytesIO(content),
            Inches((x + (width - rendered_width) / 2) / 96),
            Inches((y + (height - rendered_height) / 2) / 96),
            Inches(rendered_width / 96),
            Inches(rendered_height / 96),
        )
    if crop_mode != "cover":
        raise NativeSceneError("authored_vnext_scene_image_crop_invalid")
    shape = slide.shapes.add_picture(
        BytesIO(content),
        Inches(x / 96),
        Inches(y / 96),
        Inches(width / 96),
        Inches(height / 96),
    )
    if image_ratio > frame_ratio:
        horizontal = (1 - frame_ratio / image_ratio) / 2
        shape.crop_left = horizontal
        shape.crop_right = horizontal
    elif image_ratio < frame_ratio:
        vertical = (1 - image_ratio / frame_ratio) / 2
        shape.crop_top = vertical
        shape.crop_bottom = vertical
    return shape


def _add_scene_table(
    slide: Any,
    geometry: tuple[Any, Any, Any, Any],
    element: dict[str, Any],
    payload: dict[str, Any],
    theme: dict[str, str],
) -> Any:
    rows_value = element.get("rows")
    columns_value = element.get("columns")
    if (
        isinstance(rows_value, bool)
        or not isinstance(rows_value, int)
        or isinstance(columns_value, bool)
        or not isinstance(columns_value, int)
    ):
        raise NativeSceneError("authored_vnext_scene_table_dimensions_invalid")
    cells = _table_payload(payload, rows=rows_value, columns=columns_value)
    shape = slide.shapes.add_table(rows_value, columns_value, *geometry)
    table = shape.table
    available_width = geometry[2]
    sealed_widths = _table_column_widths(
        payload, columns=columns_value, total_width_emu=available_width
    )
    if sealed_widths is not None:
        for column_index, width in enumerate(sealed_widths):
            table.columns[column_index].width = width
    elif columns_value > 0:
        table.columns[0].width = round(available_width * (0.34 if columns_value > 1 else 1.0))
        if columns_value > 1:
            remaining = available_width - table.columns[0].width
            for column_index in range(1, columns_value):
                table.columns[column_index].width = round(
                    remaining / (columns_value - 1)
                )
            # Keep the saved native grid exactly equal to the sealed Scene
            # width. Evenly splitting an integer EMU width can leave a one-EMU
            # remainder, which otherwise changes the table style digest after
            # PowerPoint save/readback.
            table.columns[columns_value - 1].width += available_width - sum(
                column.width for column in table.columns
            )
    sealed_heights = _table_row_heights(payload, rows=rows_value)
    if sealed_heights is not None:
        for row_index, height in enumerate(sealed_heights):
            table.rows[row_index].height = height
    for row_index, row in enumerate(cells):
        for column_index, value in enumerate(row):
            cell = table.cell(row_index, column_index)
            cell.text = value
            cell.margin_left = Pt(6)
            cell.margin_right = Pt(6)
            cell.margin_top = Pt(4)
            cell.margin_bottom = Pt(4)
            cell.vertical_anchor = MSO_VERTICAL_ANCHOR.MIDDLE
            cell.fill.solid()
            cell.fill.fore_color.rgb = RGBColor.from_string(
                (
                    theme["accent"]
                    if row_index == 0
                    else theme["mutedSurface"]
                    if row_index % 2 == 0
                    else theme["surface"]
                ).removeprefix("#")
            )
            sealed_style = _table_cell_style(
                payload,
                row=row_index,
                column=column_index,
                rows=rows_value,
                columns=columns_value,
            )
            if sealed_style is not None:
                if sealed_style.get("fill") is not None:
                    cell.fill.fore_color.rgb = _color(
                        sealed_style["fill"], "FFFFFF"
                    )
                cell.vertical_anchor = {
                    "top": MSO_VERTICAL_ANCHOR.TOP,
                    "middle": MSO_VERTICAL_ANCHOR.MIDDLE,
                    "bottom": MSO_VERTICAL_ANCHOR.BOTTOM,
                }.get(
                    sealed_style.get("verticalAlign"),
                    cell.vertical_anchor,
                )
                borders = sealed_style.get("borders")
                if borders is not None:
                    border_map = _mapping(
                        borders,
                        "authored_vnext_scene_table_cell_style_invalid",
                    )
                    if not set(border_map).issubset(
                        {"top", "right", "bottom", "left"}
                    ):
                        raise NativeSceneError(
                            "authored_vnext_scene_table_cell_style_invalid"
                        )
                    for side, tag_suffix in {
                        "top": "T",
                        "right": "R",
                        "bottom": "B",
                        "left": "L",
                    }.items():
                        if border_map.get(side) is not None:
                            _set_cell_border(cell, tag_suffix, border_map[side])
            for paragraph in cell.text_frame.paragraphs:
                paragraph.alignment = {
                    "left": PP_ALIGN.LEFT,
                    "center": PP_ALIGN.CENTER,
                    "right": PP_ALIGN.RIGHT,
                    "justify": PP_ALIGN.JUSTIFY,
                }.get(
                    sealed_style.get("horizontalAlign")
                    if sealed_style is not None
                    else None,
                    PP_ALIGN.LEFT if column_index == 0 else PP_ALIGN.CENTER,
                )
                for run in paragraph.runs:
                    run.font.name = theme["fontFamily"]
                    run.font.size = Pt(13 if row_index == 0 else 12)
                    run.font.bold = row_index == 0 or column_index == 0
                    run.font.color.rgb = RGBColor.from_string(
                        (
                            theme["headerText"]
                            if row_index == 0
                            else theme["text"]
                        ).removeprefix("#")
                    )
    for merge in _table_merged_cells(
        payload, rows=rows_value, columns=columns_value
    ):
        table.cell(merge["row"], merge["column"]).merge(
            table.cell(
                merge["row"] + merge["rowSpan"] - 1,
                merge["column"] + merge["columnSpan"] - 1,
            )
        )
    return shape


_CHART_TYPES = {
    "bar": XL_CHART_TYPE.BAR_CLUSTERED,
    "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
    "line": XL_CHART_TYPE.LINE_MARKERS,
    "area": XL_CHART_TYPE.AREA,
    "pie": XL_CHART_TYPE.PIE,
    "donut": XL_CHART_TYPE.DOUGHNUT,
}


def _add_scene_chart(
    slide: Any,
    geometry: tuple[Any, Any, Any, Any],
    element: dict[str, Any],
    payload: dict[str, Any],
    theme: dict[str, str],
) -> Any:
    chart_type = element.get("chartType")
    native_type = _CHART_TYPES.get(chart_type)
    if native_type is None:
        raise NativeSceneError("authored_vnext_scene_chart_type_unsupported")
    categories, series = _chart_payload(payload)
    if chart_type in {"pie", "donut"} and len(series) != 1:
        raise NativeSceneError("authored_vnext_scene_chart_series_unsupported")
    data = ChartData()
    data.categories = categories
    for name, values in series:
        data.add_series(name, values)
    shape = slide.shapes.add_chart(native_type, *geometry, data)
    chart = shape.chart
    # python-pptx creates ``c:autoTitleDeleted`` with val="0" even though
    # ``has_title`` reads False. PowerPoint then materializes the lone series
    # name as an automatic title when a single-series chart is saved/reopened.
    # Seal the intended no-title state explicitly so COM readback is stable.
    chart.has_title = False
    chart.font.name = theme["fontFamily"]
    chart.font.color.rgb = RGBColor.from_string(theme["text"].removeprefix("#"))
    style = _chart_style_projection(payload, series_count=len(series), theme=theme)
    default_legend = len(series) > 1 or chart_type in {"pie", "donut"}
    chart.has_legend = (
        default_legend if style["legend"] is None else style["legend"]
    )
    if chart.has_legend:
        chart.legend.include_in_layout = False
        chart.legend.font.name = theme["fontFamily"]
        chart.legend.font.size = Pt(11)
        chart.legend.font.color.rgb = RGBColor.from_string(
            theme["text"].removeprefix("#")
        )
    chart.chart_style = style["chartStyle"]
    for index, native_series in enumerate(chart.series):
        color = style["seriesColors"][index].removeprefix("#")
        if chart_type == "line":
            native_series.format.line.color.rgb = RGBColor.from_string(color)
            native_series.format.line.width = Pt(2.5)
        else:
            native_series.format.fill.solid()
            native_series.format.fill.fore_color.rgb = RGBColor.from_string(color)
            native_series.format.line.color.rgb = RGBColor.from_string(color)
    if chart_type not in {"pie", "donut"}:
        chart.value_axis.has_major_gridlines = True
        chart.value_axis.major_gridlines.format.line.color.rgb = RGBColor.from_string(
            theme["divider"].removeprefix("#")
        )
        chart.value_axis.tick_labels.font.name = theme["fontFamily"]
        chart.value_axis.tick_labels.font.size = Pt(11)
        chart.value_axis.tick_labels.font.color.rgb = RGBColor.from_string(
            theme["text"].removeprefix("#")
        )
        chart.category_axis.tick_labels.font.name = theme["fontFamily"]
        chart.category_axis.tick_labels.font.size = Pt(11)
        chart.category_axis.tick_labels.font.color.rgb = RGBColor.from_string(
            theme["text"].removeprefix("#")
        )
    return shape


def _read_chart_style_projection(shape: Any, *, chart_type: str) -> dict[str, Any]:
    colors: list[str] = []
    for series in shape.chart.series:
        color = (
            series.format.line.color.rgb
            if chart_type == "line"
            else series.format.fill.fore_color.rgb
        )
        if color is None:
            raise NativeSceneError("authored_vnext_scene_chart_style_readback_failed")
        colors.append(f"#{str(color).upper()}")
    chart_style = shape.chart.chart_style
    if not isinstance(chart_style, int):
        raise NativeSceneError("authored_vnext_scene_chart_style_readback_failed")
    return {
        "chartStyle": chart_style,
        "seriesColors": colors,
        "legend": bool(shape.chart.has_legend),
    }


def _custom_properties_xml(properties: dict[str, str]) -> bytes:
    root = etree.Element(
        f"{{{_CUSTOM_NS}}}Properties",
        nsmap={None: _CUSTOM_NS, "vt": _VT_NS},
    )
    for index, (name, value) in enumerate(properties.items(), start=2):
        prop = etree.SubElement(
            root,
            f"{{{_CUSTOM_NS}}}property",
            fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}",
            pid=str(index),
            name=name,
        )
        node = etree.SubElement(prop, f"{{{_VT_NS}}}lpwstr")
        node.text = value
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _inject_custom_properties(content: bytes, properties: dict[str, str]) -> bytes:
    source = BytesIO(content)
    destination = BytesIO()
    with ZipFile(source, "r") as archive, ZipFile(destination, "w", ZIP_DEFLATED) as output:
        entries = {name: archive.read(name) for name in archive.namelist()}
        content_types = etree.fromstring(entries["[Content_Types].xml"])
        if not content_types.xpath(
            "./ct:Override[@PartName='/docProps/custom.xml']",
            namespaces={"ct": _CONTENT_TYPES_NS},
        ):
            etree.SubElement(
                content_types,
                f"{{{_CONTENT_TYPES_NS}}}Override",
                PartName="/docProps/custom.xml",
                ContentType=_CUSTOM_CONTENT_TYPE,
            )
        entries["[Content_Types].xml"] = etree.tostring(
            content_types, xml_declaration=True, encoding="UTF-8", standalone=True
        )
        relationships = etree.fromstring(entries["_rels/.rels"])
        custom = relationships.xpath(
            "./r:Relationship[@Type=$kind]",
            namespaces={"r": _PACKAGE_RELS_NS},
            kind=_CUSTOM_REL_TYPE,
        )
        if not custom:
            used = {node.get("Id") for node in relationships}
            number = 1
            while f"rId{number}" in used:
                number += 1
            etree.SubElement(
                relationships,
                f"{{{_PACKAGE_RELS_NS}}}Relationship",
                Id=f"rId{number}",
                Type=_CUSTOM_REL_TYPE,
                Target="docProps/custom.xml",
            )
        entries["_rels/.rels"] = etree.tostring(
            relationships, xml_declaration=True, encoding="UTF-8", standalone=True
        )
        entries["docProps/custom.xml"] = _custom_properties_xml(properties)
        for name, value in entries.items():
            output.writestr(name, value)
    return destination.getvalue()


def _read_custom_properties(content: bytes) -> dict[str, str]:
    with ZipFile(BytesIO(content), "r") as archive:
        try:
            root = etree.fromstring(archive.read("docProps/custom.xml"))
        except KeyError as error:
            raise NativeSceneError("authored_vnext_scene_metadata_missing") from error
    result: dict[str, str] = {}
    for prop in root.xpath("./cp:property", namespaces={"cp": _CUSTOM_NS}):
        name = prop.get("name")
        if name and len(prop):
            result[name] = str(prop[0].text or "")
    return result


def _slide_digest(slide: dict[str, Any]) -> str:
    digest, _ = _digest_payload(slide, "slideDigest", "digest")
    return digest


def _hybrid_text_is_ko_en(value: str) -> bool:
    for character in value:
        codepoint = ord(character)
        if character in {"\n", "\t"} or 0x20 <= codepoint <= 0x7E:
            continue
        if (
            codepoint == 0x00B7
            or 0x1100 <= codepoint <= 0x11FF
            or 0x3130 <= codepoint <= 0x318F
            or 0xA960 <= codepoint <= 0xA97F
            or 0xAC00 <= codepoint <= 0xD7AF
            or 0xD7B0 <= codepoint <= 0xD7FF
            or 0x2000 <= codepoint <= 0x206F
            or 0x20A0 <= codepoint <= 0x20CF
            or 0x2100 <= codepoint <= 0x214F
            or 0x2190 <= codepoint <= 0x21FF
            or 0x2460 <= codepoint <= 0x24FF
            or 0x3000 <= codepoint <= 0x303F
        ):
            continue
        return False
    return True


def _hybrid_text_contains_ja_zh(value: str) -> bool:
    return any(
        0x2E80 <= ord(character) <= 0x2FFF
        or 0x3040 <= ord(character) <= 0x30FF
        or 0x31F0 <= ord(character) <= 0x31FF
        or 0x3400 <= ord(character) <= 0x4DBF
        or 0x4E00 <= ord(character) <= 0x9FFF
        or 0xF900 <= ord(character) <= 0xFAFF
        for character in value
    )


def _hybrid_text_is_ja_zh(value: str) -> bool:
    if not _hybrid_text_contains_ja_zh(value):
        return False
    return all(
        _hybrid_text_is_ko_en(character)
        or _hybrid_text_contains_ja_zh(character)
        for character in value
    )


def _text_typefaces(element: dict[str, Any]) -> set[str]:
    _text, runs = _text_payload(element)
    style_value = element.get("textStyle") or element.get("style") or {}
    style = dict(style_value) if isinstance(style_value, Mapping) else {}
    if runs is None:
        return {str(style.get("fontFamily") or "Aptos")}
    return {
        str(run.get("fontFamily") or style.get("fontFamily") or "Aptos")
        for run in runs
    }


def _expected_hybrid_capability(kind: str, element: dict[str, Any]) -> str | None:
    if kind == "text":
        text, runs = _text_payload(element)
        style_value = element.get("textStyle") or element.get("style") or {}
        style = dict(style_value) if isinstance(style_value, Mapping) else {}
        if _hybrid_text_is_ja_zh(text):
            if not _text_typefaces(element).issubset(_REVIEWED_JA_ZH_TYPEFACES):
                return None
            # A reviewed a:ea face alone is insufficient: this module has no
            # licensed font-binary input from which to create an embedded-font
            # part.  Keep ja/zh raster-only until that separate authority is
            # sealed; never advertise native readiness from an installed font.
            return None
        if not _hybrid_text_is_ko_en(text):
            return None
        if runs is not None or style.get("autoFit") == "shrink":
            return "editable.text.advanced.v1"
        return "editable.text.ko-en.v1"
    if kind == "shape":
        shape_type = str(_scene_value(element, "shapeType", "shape"))
        if shape_type in {"triangle", "diamond", "chevron"}:
            return "editable.shape.advanced.v1"
        return "editable.shape.solid-basic.v1"
    if kind == "table":
        return "editable.table.advanced.v1" if element.get("nativeAdvanced") else "editable.table.basic.v1"
    if kind == "chart":
        return (
            "editable.chart.line-pie.v1"
            if element.get("chartType") in {"line", "pie"}
            else None
        )
    return hybrid_capability_for_kind(kind)


def _rectangles_overlap(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> bool:
    return not (
        left[0] + left[2] <= right[0]
        or right[0] + right[2] <= left[0]
        or left[1] + left[3] <= right[1]
        or right[1] + right[3] <= left[1]
    )


def _hybrid_plan_for_slide(
    *,
    scene: dict[str, Any],
    slide_scene: dict[str, Any],
    elements: list[tuple[int, dict[str, Any], str, str]],
    assets: dict[str, tuple[str, bytes, str]],
    data_manifest: dict[str, tuple[str, str, dict[str, Any]]],
    canvas: tuple[float, float],
) -> dict[str, Any] | None:
    profile_id = scene.get("hybridProfileId")
    plan_value = slide_scene.get("hybridExport")
    if profile_id is None and plan_value is None:
        return None
    if profile_id != EDITABLE_HYBRID_PROFILE_ID or plan_value is None:
        raise NativeSceneError("authored_vnext_hybrid_profile_mismatch")
    plan = _mapping(plan_value, "authored_vnext_hybrid_export_plan_invalid")
    if set(plan) != {"schemaVersion", "ownerDecisions", "rasterIslands"} or plan.get(
        "schemaVersion"
    ) != "slide-hybrid-export-plan/v1":
        raise NativeSceneError("authored_vnext_hybrid_export_plan_invalid")
    decisions_value = plan.get("ownerDecisions")
    islands_value = plan.get("rasterIslands")
    if not isinstance(decisions_value, list) or not isinstance(islands_value, list):
        raise NativeSceneError("authored_vnext_hybrid_export_plan_invalid")
    by_element = {element_id: item for _z, item, element_id, _kind in elements}
    kind_by_element = {
        element_id: kind for _z, _item, element_id, kind in elements
    }
    decisions: dict[str, dict[str, Any]] = {}
    for decision_value in decisions_value:
        decision = _mapping(
            decision_value, "authored_vnext_hybrid_owner_decision_invalid"
        )
        if not set(decision).issubset(
            {"ownerId", "decision", "capabilityId", "reasonCode"}
        ) or not {"ownerId", "decision", "capabilityId"}.issubset(decision):
            raise NativeSceneError("authored_vnext_hybrid_owner_decision_invalid")
        owner_id = _string(
            decision.get("ownerId"), "authored_vnext_hybrid_owner_decision_invalid"
        )
        capability_id = _string(
            decision.get("capabilityId"),
            "authored_vnext_hybrid_owner_decision_invalid",
        )
        if capability_id not in NATIVE_CAPABILITY_IDS_V1:
            raise NativeSceneError(
                "authored_vnext_hybrid_native_capability_unsupported"
            )
        outcome = decision.get("decision")
        if (
            _QID.fullmatch(owner_id) is None
            or owner_id in decisions
            or owner_id not in by_element
            or outcome
            not in {"nativeEditable", "rasterIsland", "publicationBlocked"}
        ):
            raise NativeSceneError("authored_vnext_hybrid_owner_decision_invalid")
        reason_code = decision.get("reasonCode")
        if outcome != "nativeEditable" and not isinstance(reason_code, str):
            raise NativeSceneError("authored_vnext_hybrid_owner_decision_invalid")
        if outcome == "publicationBlocked":
            raise NativeSceneError("authored_vnext_hybrid_publication_blocked")
        kind = kind_by_element[owner_id]
        if outcome == "nativeEditable":
            element = by_element[owner_id]
            expected_capability = _expected_hybrid_capability(kind, element)
            if kind == "table":
                table_record = data_manifest.get(element.get("dataId"))
                if table_record is not None and table_record[1] == "table" and any(
                    key in table_record[2] for key in ("rowHeightsPt", "mergedCells")
                ):
                    expected_capability = "editable.table.advanced.v1"
            if expected_capability is None:
                raise NativeSceneError(
                    "authored_vnext_hybrid_native_capability_unsupported"
                )
            if not is_hybrid_native_capability(
                capability_id
            ) or capability_id != expected_capability:
                raise NativeSceneError(
                    "authored_vnext_hybrid_native_capability_mismatch"
                )
            if kind == "text":
                text, runs = _text_payload(element)
                if (
                    not text
                ):
                    raise NativeSceneError(
                        "authored_vnext_hybrid_text_capability_unsupported"
                    )
            elif kind == "shape":
                if str(_scene_value(element, "shapeType", "shape")) not in {
                    "rect",
                    "rectangle",
                    "roundRect",
                    "roundedRectangle",
                    "ellipse",
                    "triangle",
                    "diamond",
                    "chevron",
                    "line",
                }:
                    raise NativeSceneError(
                        "authored_vnext_hybrid_shape_capability_unsupported"
                    )
            elif kind == "table":
                record = data_manifest.get(element.get("dataId"))
                if record is None or record[1] != "table":
                    raise NativeSceneError(
                        "authored_vnext_scene_table_data_unavailable"
                    )
                cells = _table_payload(
                    record[2],
                    rows=element.get("rows"),
                    columns=element.get("columns"),
                )
                if any(not _hybrid_text_is_ko_en(cell) for row in cells for cell in row):
                    raise NativeSceneError(
                        "authored_vnext_hybrid_text_capability_unsupported"
                    )
            elif kind == "chart":
                record = data_manifest.get(element.get("dataId"))
                if record is None or record[1] != "chart":
                    raise NativeSceneError(
                        "authored_vnext_scene_chart_data_unavailable"
                    )
                categories, series = _chart_payload(record[2])
                if element.get("chartType") == "pie" and len(series) != 1:
                    raise NativeSceneError(
                        "authored_vnext_scene_chart_series_unsupported"
                    )
                if not categories:
                    raise NativeSceneError(
                        "authored_vnext_scene_chart_data_invalid"
                    )
            elif kind != "image":
                raise NativeSceneError(
                    "authored_vnext_hybrid_native_capability_unsupported"
                )
        decisions[owner_id] = decision
    if set(decisions) != set(by_element):
        raise NativeSceneError("authored_vnext_hybrid_owner_inventory_mismatch")

    raster_owner_ids = {
        owner_id
        for owner_id, decision in decisions.items()
        if decision["decision"] == "rasterIsland"
    }
    island_owner_ids: set[str] = set()
    islands: list[dict[str, Any]] = []
    island_ids: set[str] = set()
    for island_value in islands_value:
        island = _mapping(
            island_value, "authored_vnext_hybrid_raster_island_invalid"
        )
        if set(island) != {
            "islandId",
            "sourceOwnerIds",
            "assetId",
            "assetDigest",
            "bounds",
            "zBand",
            "rasterPlaneDigest",
        }:
            raise NativeSceneError("authored_vnext_hybrid_raster_island_invalid")
        island_id = _string(
            island.get("islandId"), "authored_vnext_hybrid_raster_island_invalid"
        )
        source_owner_ids = island.get("sourceOwnerIds")
        if (
            re.fullmatch(r"island:[a-z0-9][a-z0-9._-]*", island_id) is None
            or island_id in island_ids
            or not isinstance(source_owner_ids, list)
            or not source_owner_ids
            or len(set(source_owner_ids)) != len(source_owner_ids)
            or any(
                not isinstance(owner_id, str)
                or owner_id not in raster_owner_ids
                or owner_id in island_owner_ids
                for owner_id in source_owner_ids
            )
        ):
            raise NativeSceneError("authored_vnext_hybrid_raster_island_invalid")
        island_ids.add(island_id)
        island_owner_ids.update(source_owner_ids)
        asset_id = island.get("assetId")
        asset_digest = island.get("assetDigest")
        record = assets.get(asset_id) if isinstance(asset_id, str) else None
        if (
            record is None
            or record[0] != asset_digest
            or not isinstance(island.get("rasterPlaneDigest"), str)
            or _DIGEST.fullmatch(island["rasterPlaneDigest"]) is None
        ):
            raise NativeSceneError(
                "authored_vnext_hybrid_raster_island_asset_mismatch"
            )
        bounds = _bounds(island, canvas)
        z_band = _mapping(
            island.get("zBand"), "authored_vnext_hybrid_raster_island_invalid"
        )
        if set(z_band) != {"minZ", "maxZ"}:
            raise NativeSceneError("authored_vnext_hybrid_raster_island_invalid")
        min_z = z_band.get("minZ")
        max_z = z_band.get("maxZ")
        if (
            isinstance(min_z, bool)
            or not isinstance(min_z, int)
            or isinstance(max_z, bool)
            or not isinstance(max_z, int)
            or min_z > max_z
            or any(
                not min_z
                <= int(by_element[owner_id].get("zOrder"))
                <= max_z
                for owner_id in source_owner_ids
            )
        ):
            raise NativeSceneError("authored_vnext_hybrid_raster_island_invalid")
        islands.append(
            {
                **island,
                "boundsTuple": bounds,
                "assetRecord": record,
                "minZ": min_z,
                "maxZ": max_z,
            }
        )
    if island_owner_ids != raster_owner_ids:
        raise NativeSceneError("authored_vnext_hybrid_owner_inventory_mismatch")
    native = [
        item
        for item in elements
        if decisions[item[2]]["decision"] == "nativeEditable"
    ]
    for z_order, element, _element_id_value, _kind in native:
        native_bounds = _bounds(element, canvas)
        for island in islands:
            if (
                _rectangles_overlap(native_bounds, island["boundsTuple"])
                and island["minZ"] <= z_order <= island["maxZ"]
            ):
                raise NativeSceneError(
                    "authored_vnext_hybrid_raster_native_z_interleaving"
                )
    return {
        "ownerDecisions": [decisions[key] for key in sorted(decisions)],
        "native": native,
        "rasterIslands": islands,
    }


def materialize_scene_pptx(
    scene_value: object,
    assets_value: object,
    master_value: object,
) -> tuple[bytes, dict[str, Any]]:
    """Materialize and reopen a canonical Scene IR as native PowerPoint objects."""

    scene = _normalized_scene(scene_value)
    master = _mapping(master_value, "authored_vnext_master_required")
    canvas = _canvas(scene, master)
    assets = _asset_lookup(assets_value)
    data_manifest = _data_lookup(scene)
    background_furniture = _master_background(master)
    background_color = background_furniture["fill"]
    presentation = Presentation()
    presentation.slide_width = Inches(canvas[0] / 96)
    presentation.slide_height = Inches(canvas[1] / 96)
    presentation.core_properties.created = datetime(2000, 1, 1, tzinfo=timezone.utc)
    presentation.core_properties.modified = datetime(2000, 1, 1, tzinfo=timezone.utc)
    while presentation.slides:
        relationship_id = presentation.slides._sldIdLst[0].rId
        presentation.part.drop_rel(relationship_id)
        del presentation.slides._sldIdLst[0]
    layout = presentation.slide_layouts[6]
    expected_slides: list[dict[str, Any]] = []
    all_element_ids: set[str] = set()
    for slide_value in scene["slides"]:
        slide_scene = _mapping(slide_value, "authored_vnext_scene_slide_invalid")
        slide_id = _string(slide_scene.get("slideId"), "authored_vnext_scene_slide_id_invalid")
        slide_digest = _slide_digest(slide_scene)
        elements = slide_scene.get("elements")
        if not isinstance(elements, list) or len(elements) > 1000:
            raise NativeSceneError("authored_vnext_scene_elements_invalid")
        normalized_elements: list[tuple[int, dict[str, Any], str, str]] = []
        slide_element_ids: set[str] = set()
        for element_value in elements:
            element = _mapping(element_value, "authored_vnext_scene_element_invalid")
            element_id = _element_id(element)
            kind = _element_kind(element)
            z_value = element.get("zOrder")
            if isinstance(z_value, bool) or not isinstance(z_value, int):
                raise NativeSceneError("authored_vnext_scene_z_order_invalid")
            if element_id in slide_element_ids or element_id in all_element_ids:
                raise NativeSceneError("authored_vnext_scene_element_identity_duplicate")
            if kind not in SUPPORTED_SCENE_ELEMENT_KINDS.union(
                UNSUPPORTED_SCENE_ELEMENT_KINDS
            ):
                raise NativeSceneError("authored_vnext_scene_element_capability_unsupported")
            if element.get("semanticRole") == "canvasBackground":
                raise NativeSceneError(
                    "authored_vnext_scene_body_background_forbidden"
                )
            slide_element_ids.add(element_id)
            all_element_ids.add(element_id)
            normalized_elements.append((z_value, element, element_id, kind))
        hybrid_plan = _hybrid_plan_for_slide(
            scene=scene,
            slide_scene=slide_scene,
            elements=normalized_elements,
            assets=assets,
            data_manifest=data_manifest,
            canvas=canvas,
        )
        if hybrid_plan is None:
            for _z_order, _element, _element_id_value, kind in normalized_elements:
                if kind in UNSUPPORTED_SCENE_ELEMENT_KINDS:
                    raise NativeSceneError(
                        f"authored_vnext_scene_{kind}_capability_unsupported"
                    )
            native_elements = normalized_elements
            raster_islands: list[dict[str, Any]] = []
        else:
            native_elements = hybrid_plan["native"]
            raster_islands = hybrid_plan["rasterIslands"]
        slide = presentation.slides.add_slide(layout)
        slide._element.cSld.set("name", slide_id)
        _set_slide_background(slide, background_color)
        identities: list[dict[str, Any]] = []
        raster_identities: list[dict[str, Any]] = []
        rendered_body: list[tuple[int, str, str, Any]] = []
        ordered_elements = sorted(
            native_elements,
            key=lambda item: (item[0], item[2]),
        )
        body_theme = _native_body_theme(
            ordered_elements,
            background_color=background_color,
        )
        for island in sorted(
            raster_islands, key=lambda item: (item["minZ"], item["islandId"])
        ):
            x, y, width, height = island["boundsTuple"]
            record = island["assetRecord"]
            shape = slide.shapes.add_picture(
                BytesIO(record[1]),
                Inches(x / 96),
                Inches(y / 96),
                Inches(width / 96),
                Inches(height / 96),
            )
            raster_metadata = {
                "islandId": island["islandId"],
                "sourceOwnerIds": island["sourceOwnerIds"],
                "assetDigest": island["assetDigest"],
                "rasterPlaneDigest": island["rasterPlaneDigest"],
                "zBand": island["zBand"],
            }
            _set_shape_identity(
                shape,
                _RASTER_DESCRIPTION_PREFIX + canonical_json(raster_metadata),
                island["islandId"],
                display_name=f"Presenton raster island {island['islandId']}",
            )
            raster_identities.append(
                {
                    **raster_metadata,
                    "renderedBounds": _shape_bounds_px(shape),
                }
            )
            rendered_body.append(
                (island["minZ"], island["islandId"], "rasterIsland", shape._element)
            )
        for z_order, element, element_id, kind in ordered_elements:
            x, y, width, height = _bounds(element, canvas)
            geometry = tuple(Inches(value / 96) for value in (x, y, width, height))
            native_metadata: dict[str, Any] = {
                "frameBounds": {"x": x, "y": y, "width": width, "height": height}
            }
            digest: str | None = None
            data_digest: str | None = None
            data_payload: dict[str, Any] | None = None
            if kind == "text":
                shape = slide.shapes.add_textbox(*geometry)
                _apply_text(shape, element)
                text_value, text_runs = _text_payload(element)
                if text_runs is not None:
                    native_metadata["textRunMapDigest"] = canonical_digest(
                        _text_run_map(text_value, text_runs)
                    )
                text_style_value = element.get("textStyle") or element.get("style") or {}
                text_style = (
                    dict(text_style_value)
                    if isinstance(text_style_value, Mapping)
                    else {}
                )
                native_metadata["autoFit"] = text_style.get("autoFit", "none")
                if text_runs is not None:
                    native_metadata["textStyleDigest"] = canonical_digest(
                        _intended_text_style_projection(element)
                    )
                if element.get("semanticRole") == "title":
                    # Canonical body colors must remain byte-identical across
                    # company standards.  Give titles the same neutral
                    # projection surface in every standard so a dark furniture
                    # background cannot turn canonical navy copy low-contrast.
                    shape.fill.solid()
                    shape.fill.fore_color.rgb = RGBColor(248, 250, 252)
                    shape.line.fill.background()
            elif kind == "shape":
                shape = slide.shapes.add_shape(_shape_type(element), *geometry)
                fill_value = element.get("fill")
                if fill_value is None:
                    shape.fill.background()
                else:
                    shape.fill.solid()
                    shape.fill.fore_color.rgb = _color(fill_value, "E5E7EB")
                stroke = element.get("stroke")
                if stroke is None:
                    shape.line.fill.background()
                else:
                    shape.line.color.rgb = _color(stroke, "64748B")
                    stroke_width = element.get("strokeWidth")
                    if stroke_width is not None:
                        width_value = _number(
                            stroke_width,
                            "authored_vnext_scene_shape_stroke_invalid",
                        )
                        if width_value < 0 or width_value > 100:
                            raise NativeSceneError(
                                "authored_vnext_scene_shape_stroke_invalid"
                            )
                        shape.line.width = Pt(width_value)
                if "text" in element or isinstance(element.get("content"), Mapping):
                    content = element.get("content")
                    if "text" in element or (isinstance(content, Mapping) and ("text" in content or "runs" in content)):
                        _apply_text(shape, element)
            elif kind == "image":
                digest = _string(element.get("assetDigest"), "authored_vnext_scene_image_asset_invalid")
                asset_id = element.get("assetId")
                record = assets.get(asset_id) if isinstance(asset_id, str) else assets.get(digest)
                if record is None or record[0] != digest:
                    raise NativeSceneError("authored_vnext_scene_image_asset_unavailable")
                shape = _add_scene_picture(
                    slide, record[1], (x, y, width, height), element
                )
                native_metadata.update(
                    {
                        "assetDigest": digest,
                        "crop": element.get("crop", "cover"),
                        "altText": element.get("altText"),
                    }
                )
            elif kind in {"table", "chart"}:
                data_id = element.get("dataId")
                data_digest = _string(
                    element.get("dataDigest"),
                    f"authored_vnext_scene_{kind}_data_invalid",
                )
                record = (
                    data_manifest.get(data_id)
                    if isinstance(data_id, str)
                    else None
                )
                if (
                    record is None
                    or record[0] != data_digest
                    or record[1] != kind
                ):
                    raise NativeSceneError(
                        f"authored_vnext_scene_{kind}_data_unavailable"
                    )
                data_payload = record[2]
                shape = (
                    _add_scene_table(
                        slide,
                        geometry,
                        element,
                        data_payload,
                        body_theme,
                    )
                    if kind == "table"
                    else _add_scene_chart(
                        slide,
                        geometry,
                        element,
                        data_payload,
                        body_theme,
                    )
                )
                native_metadata.update(
                    {"dataId": data_id, "dataDigest": data_digest}
                )
                if kind == "table":
                    native_metadata.update(
                        {
                            "tableCellsDigest": canonical_digest(
                                {"cells": data_payload["cells"]}
                            ),
                            "tableStyleDigest": canonical_digest(
                                _resolved_table_style_projection(
                                    data_payload,
                                    rows=element.get("rows"),
                                    columns=element.get("columns"),
                                    total_width_emu=geometry[2],
                                    theme=body_theme,
                                )
                            ),
                            "tableHasRowHeights": "rowHeightsPt" in data_payload,
                            "tableHasMergedCells": "mergedCells" in data_payload,
                        }
                    )
                else:
                    chart_categories, chart_series = _chart_payload(data_payload)
                    native_metadata["chartType"] = element.get("chartType")
                    if "style" in data_payload:
                        native_metadata["chartStyleDigest"] = canonical_digest(
                            _chart_style_projection(
                                data_payload,
                                series_count=len(chart_series),
                                theme=body_theme,
                            )
                        )
            else:  # pragma: no cover - capability fence above is closed
                raise NativeSceneError(
                    "authored_vnext_scene_element_capability_unsupported"
                )
            rotation = _number(element.get("rotation", 0), "authored_vnext_scene_rotation_invalid")
            shape.rotation = rotation % 360
            _set_shape_identity(
                shape,
                _shape_description(
                    element_id=element_id,
                    kind=kind,
                    z_order=z_order,
                    semantic_role=element.get("semanticRole"),
                    native_metadata=native_metadata,
                ),
                element_id,
                display_name=_owner_display_name(kind, element_id),
            )
            identity: dict[str, Any] = {
                "elementId": element_id,
                "kind": kind,
                "renderedBounds": _shape_bounds_px(shape),
            }
            if kind == "text":
                text, text_runs = _text_payload(element)
                identity["textDigest"] = canonical_digest({"text": text})
                if text_runs is not None:
                    identity["textRunMap"] = _text_run_map(text, text_runs)
                identity["autoFit"] = native_metadata["autoFit"]
                if "textStyleDigest" in native_metadata:
                    identity["styleDigest"] = native_metadata["textStyleDigest"]
            elif kind == "image":
                identity["assetDigest"] = digest
                identity["cropGeometry"] = _picture_crop(shape)
            elif kind in {"table", "chart"}:
                identity["dataDigest"] = data_digest
                identity["nativeDataDigest"] = canonical_digest(data_payload)
            else:
                identity["nativeKind"] = "shape"
            identities.append(identity)
            rendered_body.append((z_order, element_id, "scene", shape._element))
        ordered_body = sorted(rendered_body, key=lambda item: (item[0], item[1]))
        for _z_order, _owner_id, _layer, shape_element in ordered_body:
            slide.shapes._spTree.remove(shape_element)
            slide.shapes._spTree.append(shape_element)
        # Company-standard furniture is always the final/native top layer. It
        # cannot take ownership of, or be obscured by, authored body objects.
        furniture_ids = _add_furniture(
            slide,
            master,
            assets,
            page_number=len(expected_slides) + 1,
            canvas=canvas,
            background_color=background_color,
        )
        expected_slides.append(
            {
                "slideId": slide_id,
                "slideDigest": slide_digest,
                "elements": identities,
                **(
                    {
                        "ownerDecisions": hybrid_plan["ownerDecisions"],
                        "rasterIslands": raster_identities,
                    }
                    if hybrid_plan is not None
                    else {}
                ),
                "furniture": [background_furniture, *furniture_ids],
                "stackingOrder": [
                    {"layer": "furniture", "id": _BACKGROUND_FURNITURE_ID}
                ]
                + [
                    {"layer": layer, "id": owner_id}
                    for _z_order, owner_id, layer, _shape_element in ordered_body
                ]
                + [
                    {"layer": "furniture", "id": item["furnitureId"]}
                    for item in furniture_ids
                ],
            }
        )
    composition = scene.get("compositionPin")
    composition_digest = scene.get("compositionDigest")
    if isinstance(composition, Mapping):
        pinned_digest = composition.get("digest")
        if composition_digest is None:
            composition_digest = pinned_digest
        if pinned_digest != composition_digest:
            raise NativeSceneError("authored_vnext_scene_composition_mismatch")
    if not isinstance(composition_digest, str) or _DIGEST.fullmatch(composition_digest) is None:
        raise NativeSceneError("authored_vnext_scene_composition_required")
    identity = {
        "sceneDigest": scene["sceneDigest"],
        "compositionDigest": composition_digest,
        "compositionPin": dict(composition) if isinstance(composition, Mapping) else None,
        **(
            {"hybridProfileId": scene["hybridProfileId"]}
            if scene.get("hybridProfileId") is not None
            else {}
        ),
        "slides": expected_slides,
    }
    buffer = BytesIO()
    presentation.save(buffer)
    tagged_content = _inject_shape_owner_tags(buffer.getvalue())
    capabilities = (
        {
            **hybrid_ooxml_capabilities(),
            "rasterIsland": sorted(
                SUPPORTED_SCENE_ELEMENT_KINDS.union(
                    UNSUPPORTED_SCENE_ELEMENT_KINDS
                )
            ),
        }
        if scene.get("hybridProfileId") == EDITABLE_HYBRID_PROFILE_ID
        else {
            "supported": sorted(SUPPORTED_SCENE_ELEMENT_KINDS),
            "unsupported": sorted(UNSUPPORTED_SCENE_ELEMENT_KINDS),
            **capability_metadata(consumer="ooxml"),
        }
    )
    content = _inject_custom_properties(
        tagged_content,
        {
            "AuthoredSceneDigest": scene["sceneDigest"],
            "AuthoredCompositionDigest": composition_digest,
            "AuthoredCompositionPin": canonical_json(
                dict(composition) if isinstance(composition, Mapping) else None
            ),
            "AuthoredSceneIdentity": canonical_json(identity),
            "AuthoredNativeCapabilities": canonical_json(capabilities),
            "AuthoredHybridProfileId": str(scene.get("hybridProfileId") or ""),
        },
    )
    receipt = readback_scene_pptx(content)
    if receipt["scene"] != identity:
        raise NativeSceneError("authored_vnext_scene_readback_mismatch")
    return content, receipt


def readback_scene_pptx(content: bytes) -> dict[str, Any]:
    """Read stable Scene identities and object capabilities from saved PPTX bytes."""

    properties = _read_custom_properties(content)
    owner_tags_by_slide = _read_shape_owner_tags(content)
    try:
        identity = json.loads(properties["AuthoredSceneIdentity"])
        capabilities = json.loads(properties["AuthoredNativeCapabilities"])
        composition_pin = json.loads(properties["AuthoredCompositionPin"])
    except (KeyError, TypeError, ValueError) as error:
        raise NativeSceneError("authored_vnext_scene_metadata_invalid") from error
    presentation = Presentation(BytesIO(content))
    hybrid_profile_id = properties.get("AuthoredHybridProfileId")
    expected_capabilities = (
        {
            **hybrid_ooxml_capabilities(),
            "rasterIsland": sorted(
                SUPPORTED_SCENE_ELEMENT_KINDS.union(
                    UNSUPPORTED_SCENE_ELEMENT_KINDS
                )
            ),
        }
        if hybrid_profile_id
        else {
            "supported": sorted(SUPPORTED_SCENE_ELEMENT_KINDS),
            "unsupported": sorted(UNSUPPORTED_SCENE_ELEMENT_KINDS),
            **capability_metadata(consumer="ooxml"),
        }
    )
    try:
        require_capability_metadata(capabilities, consumer="ooxml")
    except CapabilityRegistryMismatchError as error:
        raise NativeSceneError(
            "authored_vnext_capability_registry_mismatch"
        ) from error
    if capabilities != expected_capabilities:
        raise NativeSceneError("authored_vnext_capability_registry_mismatch")
    actual_slides: list[dict[str, Any]] = []
    expected_slides = identity.get("slides") if isinstance(identity, dict) else None
    if not isinstance(expected_slides, list) or len(expected_slides) != len(presentation.slides):
        raise NativeSceneError("authored_vnext_scene_readback_mismatch")
    native_counts = {kind: 0 for kind in SUPPORTED_SCENE_ELEMENT_KINDS}
    raster_island_count = 0
    raster_owner_count = 0
    for index, slide in enumerate(presentation.slides):
        slide_id = slide._element.cSld.get("name")
        expected = _mapping(expected_slides[index], "authored_vnext_scene_metadata_invalid")
        elements: list[dict[str, Any]] = []
        background_color = _read_slide_background(slide)
        background_furniture = {
            "furnitureId": _BACKGROUND_FURNITURE_ID,
            "kind": "background",
            "fill": background_color,
            "styleDigest": canonical_digest({"fill": background_color}),
        }
        furniture_items: list[dict[str, Any]] = [background_furniture]
        raster_items: list[dict[str, Any]] = []
        stacking_order: list[dict[str, str]] = [
            {"layer": "furniture", "id": _BACKGROUND_FURNITURE_ID}
        ]
        expected_owner_decisions = expected.get("ownerDecisions")
        expected_native_ids = {
            item.get("ownerId")
            for item in expected_owner_decisions or []
            if isinstance(item, dict) and item.get("decision") == "nativeEditable"
        }
        expected_raster_owner_ids = {
            item.get("ownerId")
            for item in expected_owner_decisions or []
            if isinstance(item, dict) and item.get("decision") == "rasterIsland"
        }
        actual_native_ids: set[str] = set()
        actual_raster_owner_ids: set[str] = set()
        for shape in slide.shapes:
            nodes = shape._element.xpath(".//p:cNvPr")
            description = nodes[0].get("descr") if nodes else None
            if isinstance(description, str) and description.startswith(_FURNITURE_DESCRIPTION_PREFIX):
                try:
                    furniture = json.loads(
                        description.removeprefix(_FURNITURE_DESCRIPTION_PREFIX)
                    )
                except ValueError as error:
                    raise NativeSceneError("authored_vnext_master_furniture_readback_failed") from error
                furniture_id = furniture.get("furnitureId")
                kind = furniture.get("kind")
                if not isinstance(furniture_id, str) or not isinstance(kind, str):
                    raise NativeSceneError("authored_vnext_master_furniture_readback_failed")
                actual_furniture: dict[str, Any] = {
                    "furnitureId": furniture_id,
                    "kind": kind,
                }
                if "textDigest" in furniture:
                    if not shape.has_text_frame:
                        raise NativeSceneError("authored_vnext_master_furniture_readback_failed")
                    actual_furniture["textDigest"] = canonical_digest(
                        {"text": shape.text}
                    )
                    expected_style = furniture.get("textStyle")
                    if not isinstance(expected_style, dict):
                        raise NativeSceneError(
                            "authored_vnext_master_furniture_style_readback_failed"
                        )
                    runs = [
                        run
                        for paragraph in shape.text_frame.paragraphs
                        for run in paragraph.runs
                    ]
                    if not runs or runs[0].font.color.rgb is None:
                        raise NativeSceneError(
                            "authored_vnext_master_furniture_style_readback_failed"
                        )
                    actual_color = f"#{str(runs[0].font.color.rgb).upper()}"
                    actual_font = runs[0].font.name
                    if not isinstance(actual_font, str) or not actual_font:
                        raise NativeSceneError(
                            "authored_vnext_master_furniture_style_readback_failed"
                        )
                    actual_furniture["textStyle"] = {
                        "requestedColor": expected_style.get("requestedColor"),
                        "effectiveColor": actual_color,
                        "fontFamily": actual_font,
                        "contrastRatio": expected_style.get("contrastRatio"),
                        "contrastAdjusted": expected_style.get(
                            "contrastAdjusted"
                        ),
                        "styleDigest": canonical_digest(
                            {
                                "fontColor": actual_color,
                                "fontFamily": actual_font,
                            }
                        ),
                    }
                elif "assetDigest" in furniture:
                    if shape.shape_type != MSO_SHAPE_TYPE.PICTURE:
                        raise NativeSceneError("authored_vnext_master_furniture_readback_failed")
                    actual_furniture["assetDigest"] = (
                        f"sha256:{sha256(shape.image.blob).hexdigest()}"
                    )
                elif furniture.get("unconfigured") is True:
                    actual_furniture["unconfigured"] = True
                else:
                    if shape.shape_type != MSO_SHAPE_TYPE.AUTO_SHAPE:
                        raise NativeSceneError("authored_vnext_master_furniture_readback_failed")
                    actual_furniture["nativeKind"] = "shape"
                furniture_items.append(actual_furniture)
                stacking_order.append(
                    {"layer": "furniture", "id": furniture_id}
                )
                continue
            if isinstance(description, str) and description.startswith(
                _RASTER_DESCRIPTION_PREFIX
            ):
                try:
                    raster = json.loads(
                        description.removeprefix(_RASTER_DESCRIPTION_PREFIX)
                    )
                except ValueError as error:
                    raise NativeSceneError(
                        "authored_vnext_hybrid_raster_island_readback_failed"
                    ) from error
                island_id = raster.get("islandId")
                source_owner_ids = raster.get("sourceOwnerIds")
                if (
                    shape.shape_type != MSO_SHAPE_TYPE.PICTURE
                    or not isinstance(island_id, str)
                    or not isinstance(source_owner_ids, list)
                    or not source_owner_ids
                    or any(not isinstance(owner_id, str) for owner_id in source_owner_ids)
                    or any(owner_id in actual_raster_owner_ids for owner_id in source_owner_ids)
                    or shape.name != f"Presenton raster island {island_id}"
                ):
                    raise NativeSceneError(
                        "authored_vnext_hybrid_raster_island_readback_failed"
                    )
                embedded_digest = f"sha256:{sha256(shape.image.blob).hexdigest()}"
                if embedded_digest != raster.get("assetDigest"):
                    raise NativeSceneError(
                        "authored_vnext_hybrid_raster_island_readback_failed"
                    )
                actual_raster_owner_ids.update(source_owner_ids)
                actual_raster = {
                    "islandId": island_id,
                    "sourceOwnerIds": source_owner_ids,
                    "assetDigest": embedded_digest,
                    "rasterPlaneDigest": raster.get("rasterPlaneDigest"),
                    "zBand": raster.get("zBand"),
                    "renderedBounds": _shape_bounds_px(shape),
                }
                raster_items.append(actual_raster)
                raster_island_count += 1
                raster_owner_count += len(source_owner_ids)
                stacking_order.append({"layer": "rasterIsland", "id": island_id})
                continue
            if not isinstance(description, str) or not description.startswith(_DESCRIPTION_PREFIX):
                raise NativeSceneError("authored_vnext_scene_element_identity_readback_failed")
            try:
                metadata = json.loads(description.removeprefix(_DESCRIPTION_PREFIX))
            except ValueError as error:
                raise NativeSceneError("authored_vnext_scene_element_identity_readback_failed") from error
            element_id = metadata.get("elementId")
            kind = metadata.get("kind")
            if not isinstance(element_id, str) or kind not in SUPPORTED_SCENE_ELEMENT_KINDS:
                raise NativeSceneError("authored_vnext_scene_element_identity_readback_failed")
            if element_id in actual_native_ids:
                raise NativeSceneError("authored_vnext_scene_qid_duplicate_or_ghost")
            actual_native_ids.add(element_id)
            if owner_tags_by_slide[index].get(element_id) != _owner_tags(
                kind, element_id
            ):
                raise NativeSceneError("authored_vnext_scene_qid_tag_readback_failed")
            native_counts[kind] += 1
            actual_element: dict[str, Any] = {
                "elementId": element_id,
                "kind": kind,
                "renderedBounds": _shape_bounds_px(shape),
            }
            if kind == "text":
                if not shape.has_text_frame:
                    raise NativeSceneError("authored_vnext_scene_element_native_kind_mismatch")
                # python-pptx exposes an OOXML ``a:br`` soft line break as a
                # vertical-tab character.  Scene text uses a logical newline;
                # restore that exact authored value before digest comparison.
                # Paragraph boundaries remain ``\n`` and are unchanged.
                logical_text = shape.text.replace("\v", "\n")
                actual_element["textDigest"] = canonical_digest({"text": logical_text})
                run_map_digest = metadata.get("textRunMapDigest")
                if run_map_digest is not None:
                    run_map = _run_map_from_shape(shape)
                    if run_map_digest != canonical_digest(run_map):
                        raise NativeSceneError(
                            "authored_vnext_scene_text_run_map_readback_failed"
                        )
                    actual_element["textRunMap"] = run_map
                expected_auto_fit = metadata.get("autoFit", "none")
                actual_auto_fit = (
                    "shrink"
                    if shape.text_frame.auto_size == MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
                    else "none"
                    if shape.text_frame.auto_size == MSO_AUTO_SIZE.NONE
                    else "unsupported"
                )
                if actual_auto_fit != expected_auto_fit:
                    raise NativeSceneError(
                        "authored_vnext_scene_text_autofit_readback_failed"
                    )
                actual_element["autoFit"] = actual_auto_fit
                expected_style_digest = metadata.get("textStyleDigest")
                if expected_style_digest is not None:
                    actual_style_digest = canonical_digest(
                        _native_text_style_projection(shape)
                    )
                    if actual_style_digest != expected_style_digest:
                        raise NativeSceneError(
                            "authored_vnext_scene_text_style_readback_failed"
                        )
                    actual_element["styleDigest"] = actual_style_digest
            elif kind == "image":
                if shape.shape_type != MSO_SHAPE_TYPE.PICTURE:
                    raise NativeSceneError("authored_vnext_scene_element_native_kind_mismatch")
                actual_element["assetDigest"] = (
                    f"sha256:{sha256(shape.image.blob).hexdigest()}"
                )
                actual_element["cropGeometry"] = _picture_crop(shape)
            elif kind == "table":
                if shape.shape_type != MSO_SHAPE_TYPE.TABLE:
                    raise NativeSceneError(
                        "authored_vnext_scene_element_native_kind_mismatch"
                    )
                payload = {
                    "cells": [
                        [shape.table.cell(row, column).text for column in range(len(shape.table.columns))]
                        for row in range(len(shape.table.rows))
                    ]
                }
                data_digest = metadata.get("dataDigest")
                table_cells_digest = metadata.get("tableCellsDigest")
                table_style_digest = metadata.get("tableStyleDigest")
                if (
                    not isinstance(data_digest, str)
                    or _DIGEST.fullmatch(data_digest) is None
                    or table_cells_digest != canonical_digest(payload)
                    or table_style_digest
                    != canonical_digest(
                        _read_table_style_projection(
                            shape,
                            include_row_heights=bool(
                                metadata.get("tableHasRowHeights")
                            ),
                            include_merges=bool(
                                metadata.get("tableHasMergedCells")
                            ),
                        )
                    )
                ):
                    raise NativeSceneError("authored_vnext_scene_table_data_readback_failed")
                actual_element["dataDigest"] = data_digest
                actual_element["nativeDataDigest"] = data_digest
            elif kind == "chart":
                if shape.shape_type != MSO_SHAPE_TYPE.CHART:
                    raise NativeSceneError(
                        "authored_vnext_scene_element_native_kind_mismatch"
                    )
                try:
                    categories = [
                        str(category.label)
                        for category in shape.chart.plots[0].categories
                    ]
                    payload = {
                        "categories": categories,
                        "series": [
                            {
                                "name": str(series.name),
                                "values": [float(value) for value in series.values],
                            }
                            for series in shape.chart.series
                        ],
                    }
                except (AttributeError, TypeError, ValueError) as error:
                    raise NativeSceneError(
                        "authored_vnext_scene_chart_data_readback_failed"
                    ) from error
                data_digest = metadata.get("dataDigest")
                if not isinstance(data_digest, str) or _DIGEST.fullmatch(data_digest) is None:
                    raise NativeSceneError("authored_vnext_scene_chart_data_readback_failed")
                actual_element["dataDigest"] = data_digest
                chart_style_digest = metadata.get("chartStyleDigest")
                if chart_style_digest is not None:
                    chart_type = metadata.get("chartType")
                    if chart_type not in {"line", "pie"}:
                        raise NativeSceneError(
                            "authored_vnext_scene_chart_style_readback_failed"
                        )
                    style = _read_chart_style_projection(
                        shape, chart_type=chart_type
                    )
                    if canonical_digest(style) != chart_style_digest:
                        raise NativeSceneError(
                            "authored_vnext_scene_chart_style_readback_failed"
                        )
                    payload["style"] = style
                actual_element["nativeDataDigest"] = canonical_digest(payload)
            else:
                if shape.shape_type != MSO_SHAPE_TYPE.AUTO_SHAPE:
                    raise NativeSceneError("authored_vnext_scene_element_native_kind_mismatch")
                actual_element["nativeKind"] = "shape"
            elements.append(actual_element)
            stacking_order.append({"layer": "scene", "id": element_id})
        if expected_owner_decisions is not None and (
            actual_native_ids != expected_native_ids
            or actual_raster_owner_ids != expected_raster_owner_ids
            or actual_native_ids.intersection(actual_raster_owner_ids)
        ):
            raise NativeSceneError("authored_vnext_scene_qid_duplicate_or_ghost")
        if set(owner_tags_by_slide[index]) != actual_native_ids:
            raise NativeSceneError("authored_vnext_scene_qid_duplicate_or_ghost")
        actual_slides.append(
            {
                "slideId": slide_id,
                "slideDigest": expected.get("slideDigest"),
                "elements": elements,
                **(
                    {
                        "ownerDecisions": expected_owner_decisions,
                        "rasterIslands": raster_items,
                    }
                    if expected_owner_decisions is not None
                    else {}
                ),
                "furniture": furniture_items,
                "stackingOrder": stacking_order,
            }
        )
    actual_identity = {
        "sceneDigest": properties.get("AuthoredSceneDigest"),
        "compositionDigest": properties.get("AuthoredCompositionDigest"),
        "compositionPin": composition_pin,
        **(
            {"hybridProfileId": properties.get("AuthoredHybridProfileId")}
            if properties.get("AuthoredHybridProfileId")
            else {}
        ),
        "slides": actual_slides,
    }
    if actual_identity != identity:
        raise NativeSceneError("authored_vnext_scene_readback_mismatch")
    return {
        "scene": actual_identity,
        "capabilities": {
            **capabilities,
            "readbackRegistry": capability_metadata(consumer="readback"),
        },
        "nativeObjectCounts": native_counts,
        "hybridOwnership": {
            "profileId": properties.get("AuthoredHybridProfileId") or None,
            "nativeOwnerCount": sum(native_counts.values()),
            "rasterIslandCount": raster_island_count,
            "rasterOwnerCount": raster_owner_count,
            "nativeRasterDuplicateOwners": 0,
            "missingOrGhostQids": 0,
        },
    }


__all__ = [
    "EDITABLE_HYBRID_PROFILE_ID",
    "NativeSceneError",
    "PPTX_MEDIA_TYPE",
    "SCENE_SCHEMA_VERSION",
    "SUPPORTED_SCENE_ELEMENT_KINDS",
    "UNSUPPORTED_SCENE_ELEMENT_KINDS",
    "materialize_scene_pptx",
    "readback_scene_pptx",
]
