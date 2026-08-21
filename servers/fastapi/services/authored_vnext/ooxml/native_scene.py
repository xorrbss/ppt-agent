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


def _set_shape_identity(shape: Any, description: str, element_id: str) -> None:
    shape.name = element_id
    nodes = shape._element.xpath(".//p:cNvPr")
    if not nodes:
        raise NativeSceneError("authored_vnext_scene_element_identity_write_failed")
    nodes[0].set("name", element_id)
    nodes[0].set("descr", description)


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


def _apply_text(shape: Any, element: dict[str, Any]) -> None:
    text, runs = _text_payload(element)
    style_value = element.get("textStyle") or element.get("style") or {}
    style = dict(style_value) if isinstance(style_value, Mapping) else {}
    frame = shape.text_frame
    frame.clear()
    frame.word_wrap = True
    if element.get("semanticRole") == "title":
        # Scene compilation has already measured and fitted title copy.  The
        # python-pptx default is SHAPE_TO_FIT_TEXT (<a:spAutoFit/>), which lets
        # desktop PowerPoint resize a multi-line CJK title after generation and
        # destroys the approved hierarchy.  Preserve the measured native size.
        frame.auto_size = MSO_AUTO_SIZE.NONE
    frame.margin_left = Pt(2)
    frame.margin_right = Pt(2)
    frame.margin_top = Pt(1)
    frame.margin_bottom = Pt(1)
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
        run.font.name = str(item.get("fontFamily") or style.get("fontFamily") or "Aptos")
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
    if set(payload) != {"cells"}:
        raise NativeSceneError("authored_vnext_scene_table_data_invalid")
    cells = payload.get("cells")
    if (
        not isinstance(cells, list)
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


def _chart_payload(payload: dict[str, Any]) -> tuple[list[str], list[tuple[str, list[float]]]]:
    if set(payload) != {"categories", "series"}:
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
    if columns_value > 0:
        table.columns[0].width = round(available_width * (0.34 if columns_value > 1 else 1.0))
        if columns_value > 1:
            remaining = available_width - table.columns[0].width
            for column_index in range(1, columns_value):
                table.columns[column_index].width = round(
                    remaining / (columns_value - 1)
                )
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
            for paragraph in cell.text_frame.paragraphs:
                paragraph.alignment = (
                    PP_ALIGN.LEFT if column_index == 0 else PP_ALIGN.CENTER
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
    chart.font.name = theme["fontFamily"]
    chart.font.color.rgb = RGBColor.from_string(theme["text"].removeprefix("#"))
    chart.has_legend = len(series) > 1 or chart_type in {"pie", "donut"}
    if chart.has_legend:
        chart.legend.include_in_layout = False
        chart.legend.font.name = theme["fontFamily"]
        chart.legend.font.size = Pt(11)
        chart.legend.font.color.rgb = RGBColor.from_string(
            theme["text"].removeprefix("#")
        )
    chart.chart_style = 2
    palette = [
        theme["accent"],
        _mix_color(theme["accent"], theme["text"], 0.28),
        _mix_color(theme["accent"], theme["surface"], 0.35),
        _mix_color(theme["accent"], theme["text"], 0.52),
    ]
    for index, native_series in enumerate(chart.series):
        color = palette[index % len(palette)].removeprefix("#")
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
            if kind in UNSUPPORTED_SCENE_ELEMENT_KINDS:
                raise NativeSceneError(f"authored_vnext_scene_{kind}_capability_unsupported")
            if kind not in SUPPORTED_SCENE_ELEMENT_KINDS:
                raise NativeSceneError("authored_vnext_scene_element_capability_unsupported")
            if element.get("semanticRole") == "canvasBackground":
                raise NativeSceneError(
                    "authored_vnext_scene_body_background_forbidden"
                )
            slide_element_ids.add(element_id)
            all_element_ids.add(element_id)
            normalized_elements.append((z_value, element, element_id, kind))
        slide = presentation.slides.add_slide(layout)
        slide._element.cSld.set("name", slide_id)
        _set_slide_background(slide, background_color)
        identities: list[dict[str, Any]] = []
        ordered_elements = sorted(
            normalized_elements,
            key=lambda item: (item[0], item[2]),
        )
        body_theme = _native_body_theme(
            ordered_elements,
            background_color=background_color,
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
                fill_value = element.get("fill") or element.get("style", {})
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
            )
            identity: dict[str, Any] = {
                "elementId": element_id,
                "kind": kind,
                "renderedBounds": _shape_bounds_px(shape),
            }
            if kind == "text":
                text, _runs = _text_payload(element)
                identity["textDigest"] = canonical_digest({"text": text})
            elif kind == "image":
                identity["assetDigest"] = digest
                identity["cropGeometry"] = _picture_crop(shape)
            elif kind in {"table", "chart"}:
                identity["dataDigest"] = data_digest
                identity["nativeDataDigest"] = canonical_digest(data_payload)
            else:
                identity["nativeKind"] = "shape"
            identities.append(identity)
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
                "furniture": [background_furniture, *furniture_ids],
                "stackingOrder": [
                    {"layer": "furniture", "id": _BACKGROUND_FURNITURE_ID}
                ]
                + [
                    {"layer": "scene", "id": item["elementId"]}
                    for item in identities
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
        "slides": expected_slides,
    }
    buffer = BytesIO()
    presentation.save(buffer)
    content = _inject_custom_properties(
        buffer.getvalue(),
        {
            "AuthoredSceneDigest": scene["sceneDigest"],
            "AuthoredCompositionDigest": composition_digest,
            "AuthoredCompositionPin": canonical_json(
                dict(composition) if isinstance(composition, Mapping) else None
            ),
            "AuthoredSceneIdentity": canonical_json(identity),
            "AuthoredNativeCapabilities": canonical_json(
                {
                    "supported": sorted(SUPPORTED_SCENE_ELEMENT_KINDS),
                    "unsupported": sorted(UNSUPPORTED_SCENE_ELEMENT_KINDS),
                }
            ),
        },
    )
    receipt = readback_scene_pptx(content)
    if receipt["scene"] != identity:
        raise NativeSceneError("authored_vnext_scene_readback_mismatch")
    return content, receipt


def readback_scene_pptx(content: bytes) -> dict[str, Any]:
    """Read stable Scene identities and object capabilities from saved PPTX bytes."""

    properties = _read_custom_properties(content)
    try:
        identity = json.loads(properties["AuthoredSceneIdentity"])
        capabilities = json.loads(properties["AuthoredNativeCapabilities"])
        composition_pin = json.loads(properties["AuthoredCompositionPin"])
    except (KeyError, TypeError, ValueError) as error:
        raise NativeSceneError("authored_vnext_scene_metadata_invalid") from error
    presentation = Presentation(BytesIO(content))
    actual_slides: list[dict[str, Any]] = []
    expected_slides = identity.get("slides") if isinstance(identity, dict) else None
    if not isinstance(expected_slides, list) or len(expected_slides) != len(presentation.slides):
        raise NativeSceneError("authored_vnext_scene_readback_mismatch")
    native_counts = {kind: 0 for kind in SUPPORTED_SCENE_ELEMENT_KINDS}
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
        stacking_order: list[dict[str, str]] = [
            {"layer": "furniture", "id": _BACKGROUND_FURNITURE_ID}
        ]
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
                if not isinstance(data_digest, str) or _DIGEST.fullmatch(data_digest) is None:
                    raise NativeSceneError("authored_vnext_scene_table_data_readback_failed")
                actual_element["dataDigest"] = data_digest
                actual_element["nativeDataDigest"] = canonical_digest(payload)
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
                actual_element["nativeDataDigest"] = canonical_digest(payload)
            else:
                if shape.shape_type != MSO_SHAPE_TYPE.AUTO_SHAPE:
                    raise NativeSceneError("authored_vnext_scene_element_native_kind_mismatch")
                actual_element["nativeKind"] = "shape"
            elements.append(actual_element)
            stacking_order.append({"layer": "scene", "id": element_id})
        actual_slides.append(
            {
                "slideId": slide_id,
                "slideDigest": expected.get("slideDigest"),
                "elements": elements,
                "furniture": furniture_items,
                "stackingOrder": stacking_order,
            }
        )
    actual_identity = {
        "sceneDigest": properties.get("AuthoredSceneDigest"),
        "compositionDigest": properties.get("AuthoredCompositionDigest"),
        "compositionPin": composition_pin,
        "slides": actual_slides,
    }
    if actual_identity != identity:
        raise NativeSceneError("authored_vnext_scene_readback_mismatch")
    return {
        "scene": actual_identity,
        "capabilities": capabilities,
        "nativeObjectCounts": native_counts,
    }


__all__ = [
    "NativeSceneError",
    "PPTX_MEDIA_TYPE",
    "SCENE_SCHEMA_VERSION",
    "SUPPORTED_SCENE_ELEMENT_KINDS",
    "UNSUPPORTED_SCENE_ELEMENT_KINDS",
    "materialize_scene_pptx",
    "readback_scene_pptx",
]
