"""Deterministic derived web preview and renderer/export plans."""

from __future__ import annotations

import base64
import binascii
from html import escape
import hashlib
from io import BytesIO
import json
import math
import re
import unicodedata
import xml.etree.ElementTree as ET

from PIL import Image

from models.authored_vnext.master.contract import (
    DeckMasterContract,
    FurnitureElement,
    SemanticMasterElement,
)
from models.authored_vnext.master.operations import (
    ExportPlan,
    ExportSlidePlan,
    PngRenderRequest,
    PreviewResponse,
)
from models.authored_vnext.master.slide import SlideSpecV2
from services.authored_vnext.body_grammar import (
    build_body_grammar_snapshot,
)

from .digests import canonical_digest


def _css_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False).replace("<", "\\003c ")


def _class_token(value: str) -> str:
    return "-".join(part for part in re.split(r"[^a-zA-Z0-9_-]+", value) if part)


_WEB_IMAGE_MEDIA_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/svg+xml",
    "image/webp",
}

_NATIVE_PREVIEW_JPEG_SOURCE_LIMIT = 512 * 1024
_NATIVE_PREVIEW_MAX_IMAGE_EDGE = 1600
_DRAWINGML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_PRESENTATIONML_NS = (
    "http://schemas.openxmlformats.org/presentationml/2006/main"
)
_DEFAULT_SCHEME_COLORS = {
    "bg1": "FFFFFF",
    "lt1": "FFFFFF",
    "bg2": "E7E6E6",
    "lt2": "E7E6E6",
    "tx1": "000000",
    "dk1": "000000",
    "tx2": "44546A",
    "dk2": "44546A",
    "accent1": "4472C4",
    "accent2": "ED7D31",
    "accent3": "A5A5A5",
    "accent4": "FFC000",
    "accent5": "5B9BD5",
    "accent6": "70AD47",
}


def _native_scene_number(value: object, *, field: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"native scene {field} is invalid")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"native scene {field} is invalid")
    return number


def _native_scene_bounds(
    element: dict,
    *,
    scale_x: float,
    scale_y: float,
    offset_x: float,
    offset_y: float,
) -> tuple[float, float, float, float] | None:
    bounds = element.get("bounds")
    if not isinstance(bounds, dict):
        return None
    x = _native_scene_number(bounds.get("x"), field="bounds.x")
    y = _native_scene_number(bounds.get("y"), field="bounds.y")
    width = _native_scene_number(bounds.get("width"), field="bounds.width")
    height = _native_scene_number(bounds.get("height"), field="bounds.height")
    if width < 0 or height < 0:
        raise ValueError("native scene bounds are invalid")
    return (
        offset_x + x * scale_x,
        offset_y + y * scale_y,
        width * scale_x,
        height * scale_y,
    )


def _native_scene_image_source(element: dict) -> str | None:
    relationships = element.get("relationships")
    if not isinstance(relationships, list):
        raise ValueError("native scene relationships are invalid")
    for relationship in relationships:
        if not isinstance(relationship, dict) or relationship.get("external") is True:
            continue
        media_type = relationship.get("targetMediaType")
        encoded = relationship.get("targetBytesBase64")
        expected_digest = relationship.get("targetDigest")
        if media_type not in _WEB_IMAGE_MEDIA_TYPES or not isinstance(encoded, str):
            continue
        try:
            value = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise ValueError("native scene image bytes are invalid") from error
        actual_digest = f"sha256:{hashlib.sha256(value).hexdigest()}"
        if expected_digest != actual_digest:
            raise ValueError("native scene image digest is invalid")
        # Retained-reference images keep their original bytes for native PPTX
        # materialization.  The browser preview only needs canvas-resolution
        # pixels, so derive a bounded JPEG after verifying those original bytes.
        # This keeps the complete authored HTML inside the renderer admission
        # limit without weakening the immutable scene or its source digest.
        if media_type == "image/jpeg" and len(value) > _NATIVE_PREVIEW_JPEG_SOURCE_LIMIT:
            try:
                with Image.open(BytesIO(value)) as image:
                    image.thumbnail(
                        (_NATIVE_PREVIEW_MAX_IMAGE_EDGE, _NATIVE_PREVIEW_MAX_IMAGE_EDGE),
                        Image.Resampling.LANCZOS,
                    )
                    output = BytesIO()
                    image.convert("RGB").save(
                        output,
                        format="JPEG",
                        quality=85,
                        optimize=True,
                    )
                    encoded = base64.b64encode(output.getvalue()).decode("ascii")
                    media_type = "image/jpeg"
            except (OSError, ValueError) as error:
                raise ValueError("native scene preview image is invalid") from error
        return f"data:{media_type};base64,{encoded}"
    return None


def _native_scene_shape_background(native: dict) -> str | None:
    """Resolve native solid/gradient fills without replacing transparency by white."""

    fragment = native.get("fragmentXml")
    if not isinstance(fragment, str) or not fragment:
        return native.get("fill") if isinstance(native.get("fill"), str) else None
    try:
        root = ET.fromstring(fragment)
    except ET.ParseError as error:
        raise ValueError("native scene fragment XML is invalid") from error
    shape_properties = root.find(f".//{{{_PRESENTATIONML_NS}}}spPr")
    if shape_properties is None:
        return native.get("fill") if isinstance(native.get("fill"), str) else None
    if shape_properties.find(f"{{{_DRAWINGML_NS}}}noFill") is not None:
        return None

    def resolved_color(parent: ET.Element) -> str | None:
        color = parent.find(f"{{{_DRAWINGML_NS}}}srgbClr")
        value = color.get("val") if color is not None else None
        if not isinstance(value, str) or re.fullmatch(r"[0-9A-Fa-f]{6}", value) is None:
            color = parent.find(f"{{{_DRAWINGML_NS}}}schemeClr")
            scheme = color.get("val") if color is not None else None
            value = _DEFAULT_SCHEME_COLORS.get(scheme or "")
        if color is None or not isinstance(value, str):
            return None
        alpha_node = color.find(f"{{{_DRAWINGML_NS}}}alpha")
        alpha = 1.0
        if alpha_node is not None:
            raw_alpha = alpha_node.get("val")
            if isinstance(raw_alpha, str) and raw_alpha.isdigit():
                alpha = min(1.0, max(0.0, int(raw_alpha) / 100000.0))
        if alpha < 1.0:
            red, green, blue = (
                int(value[index : index + 2], 16) for index in (0, 2, 4)
            )
            return f"rgba({red},{green},{blue},{alpha:g})"
        return f"#{value.upper()}"

    solid = shape_properties.find(f"{{{_DRAWINGML_NS}}}solidFill")
    if solid is not None:
        return resolved_color(solid)

    gradient = shape_properties.find(f"{{{_DRAWINGML_NS}}}gradFill")
    if gradient is not None:
        stops: list[tuple[int, str]] = []
        for stop in gradient.findall(f".//{{{_DRAWINGML_NS}}}gs"):
            position = stop.get("pos")
            if not isinstance(position, str) or not position.isdigit():
                continue
            css_color = resolved_color(stop)
            if css_color is None:
                continue
            stops.append((min(100000, int(position)), css_color))
        if stops:
            line = gradient.find(f"{{{_DRAWINGML_NS}}}lin")
            angle = 0.0
            if line is not None:
                raw_angle = line.get("ang")
                if isinstance(raw_angle, str) and raw_angle.isdigit():
                    angle = int(raw_angle) / 60000.0
            stops.sort(key=lambda item: item[0])
            stop_css = ",".join(
                f"{color} {position / 1000:g}%" for position, color in stops
            )
            return f"linear-gradient({angle:g}deg,{stop_css})"

    if root.find(f".//{{{_PRESENTATIONML_NS}}}ph") is not None:
        return None
    return native.get("fill") if isinstance(native.get("fill"), str) else None


def _native_scene_shape_preset(native: dict) -> str:
    fragment = native.get("fragmentXml")
    if isinstance(fragment, str) and fragment:
        try:
            root = ET.fromstring(fragment)
        except ET.ParseError as error:
            raise ValueError("native scene fragment XML is invalid") from error
        geometry = root.find(f".//{{{_DRAWINGML_NS}}}prstGeom")
        preset = geometry.get("prst") if geometry is not None else None
        if isinstance(preset, str) and preset:
            return preset
    return str(native.get("preset") or "rect")


def _native_scene_element_html(
    element: dict,
    *,
    layer_kind: str,
    layer_index: int,
    element_index: int,
    scale_x: float,
    scale_y: float,
    offset_x: float,
    offset_y: float,
) -> str:
    native = element.get("native")
    if not isinstance(native, dict):
        raise ValueError("native scene element payload is invalid")
    bounds = _native_scene_bounds(
        element,
        scale_x=scale_x,
        scale_y=scale_y,
        offset_x=offset_x,
        offset_y=offset_y,
    )
    if bounds is None:
        return ""
    left, top, width, height = bounds
    kind = str(element.get("kind") or "shape")
    source_index = element.get("sourceIndex", element_index)
    if not isinstance(source_index, int) or isinstance(source_index, bool):
        raise ValueError("native scene source index is invalid")
    fragment_digest = element.get("fragmentDigest")
    if not isinstance(fragment_digest, str) or not fragment_digest.startswith("sha256:"):
        raise ValueError("native scene fragment digest is invalid")
    # The source indexes and digests stay in the immutable nativeScene evidence.
    # They must not be copied to custom DOM attributes because the isolated
    # renderer intentionally admits only a small, fixed attribute vocabulary.
    attributes = f'title="native {escape(layer_kind, quote=True)} {layer_index}:{source_index}"'
    rotation = _native_scene_number(native.get("rotation", 0), field="rotation")
    common = (
        f"position:absolute;left:{left:g}px;top:{top:g}px;"
        f"width:{width:g}px;height:{height:g}px;"
        f"transform:rotate({rotation:g}deg);transform-origin:center center;"
        "overflow:hidden"
    )
    if kind == "image":
        source = _native_scene_image_source(element)
        if source is None:
            raise ValueError("native scene image has no web-compatible asset")
        return (
            f'<img class="native-scene-element native-scene-image" {attributes} '
            f'src="{escape(source, quote=True)}" alt="" '
            f'style="{escape(common + ";object-fit:fill", quote=True)}">'
        )

    preset = _native_scene_shape_preset(native)
    fill = _native_scene_shape_background(native)
    stroke = native.get("stroke")
    stroke_width = _native_scene_number(
        native.get("strokeWidth", 0), field="strokeWidth"
    )
    if stroke_width < 0:
        raise ValueError("native scene stroke width is invalid")
    if kind in {"connector", "line"} or preset == "line":
        line_width = max(width, 1.0)
        line_height = max(height, stroke_width, 1.0)
        color = stroke if isinstance(stroke, str) and stroke else "#000000"
        return (
            f'<svg class="native-scene-element native-scene-line" {attributes} '
            f'viewBox="0 0 {line_width:g} {line_height:g}" preserveAspectRatio="none" '
            f'style="{escape(common, quote=True)}">'
            f'<line x1="0" y1="0" x2="{line_width:g}" y2="{line_height:g}" '
            f'stroke="{escape(color, quote=True)}" '
            f'stroke-width="{max(stroke_width, 1.0):g}"/></svg>'
        )

    styles = [common, "display:flex", "white-space:pre-wrap"]
    if isinstance(fill, str) and fill:
        styles.append(f"background:{fill}")
    if isinstance(stroke, str) and stroke and stroke_width > 0:
        styles.append(f"border:{stroke_width:g}px solid {stroke}")
    if preset in {"ellipse", "arc"}:
        styles.append("border-radius:50%")
    elif preset == "triangle":
        styles.append("clip-path:polygon(50% 0,0 100%,100% 100%)")
    elif preset in {"roundRect", "round1Rect", "round2DiagRect", "round2SameRect"}:
        styles.append("border-radius:10px")
    text_style = native.get("textStyle")
    if isinstance(text_style, dict):
        font_family = text_style.get("fontFamily")
        font_pt = text_style.get("fontPt")
        color = text_style.get("color")
        if isinstance(font_family, str) and font_family:
            styles.append(f"font-family:{_css_string(font_family)}")
        if isinstance(font_pt, (int, float)) and not isinstance(font_pt, bool):
            styles.append(f"font-size:{float(font_pt):g}pt")
        if isinstance(color, str) and color:
            styles.append(f"color:{color}")
    text = native.get("text")
    visible_text = text if isinstance(text, str) else ""
    return (
        f'<div class="native-scene-element native-scene-{escape(_class_token(kind), quote=True)}" '
        f'{attributes} style="{escape(";".join(styles), quote=True)}">'
        f"{escape(visible_text)}</div>"
    )


def _native_scene_html(scene: dict) -> str:
    """Render the immutable native scene behind authored title/body layers."""

    if not isinstance(scene, dict) or scene.get("schemaVersion") != "retained-native-scene/v1":
        raise ValueError("native scene is invalid")
    digest = scene.get("digest")
    if not isinstance(digest, str) or not digest.startswith("sha256:"):
        raise ValueError("native scene digest is invalid")
    transform = scene.get("transform")
    layers = scene.get("layers")
    if not isinstance(transform, dict) or not isinstance(layers, list):
        raise ValueError("native scene transform is invalid")
    scale_x = _native_scene_number(transform.get("scaleX"), field="scaleX")
    scale_y = _native_scene_number(transform.get("scaleY"), field="scaleY")
    offset_x = _native_scene_number(transform.get("offsetX"), field="offsetX")
    offset_y = _native_scene_number(transform.get("offsetY"), field="offsetY")
    if scale_x <= 0 or scale_y <= 0:
        raise ValueError("native scene scale is invalid")
    parts: list[str] = []
    for layer_index, layer in enumerate(layers):
        if not isinstance(layer, dict) or not isinstance(layer.get("elements"), list):
            raise ValueError("native scene layer is invalid")
        layer_kind = str(layer.get("kind") or "unknown")
        element_parts = [
            _native_scene_element_html(
                element,
                layer_kind=layer_kind,
                layer_index=layer_index,
                element_index=element_index,
                scale_x=scale_x,
                scale_y=scale_y,
                offset_x=offset_x,
                offset_y=offset_y,
            )
            for element_index, element in enumerate(layer["elements"])
            if isinstance(element, dict)
        ]
        parts.append(
        f'<div class="native-scene-layer '
        f'native-scene-layer-{escape(_class_token(layer_kind), quote=True)}" '
            f'style="position:absolute;inset:0">'
            f'{"".join(element_parts)}</div>'
        )
    return (
        '<div class="native-scene" aria-hidden="true" '
        'style="position:absolute;inset:0;z-index:0;overflow:hidden">'
        f'{"".join(parts)}</div>'
    )


def _element_html(
    element: FurnitureElement,
    *,
    page_number: int | None = None,
) -> str:
    if element.visibility == "hidden":
        return ""
    bounds = element.bounds
    styles = [
        "position:absolute",
        f"left:{bounds.x:g}px",
        f"top:{bounds.y:g}px",
        f"width:{bounds.width:g}px",
        f"height:{bounds.height:g}px",
    ]
    if element.fill:
        styles.append(f"color:{element.fill}")
        if element.kind == "rect":
            styles.append(f"background:{element.fill}")
    if element.stroke:
        styles.append(f"border-color:{element.stroke}")
    if element.kind == "line":
        styles.extend([f"background:{element.stroke or '#000000'}", "height:1px"])
    # Logo bytes are rendered by the native asset pipeline.  A web preview that
    # cannot resolve those bytes must stay empty instead of publishing a fake
    # literal placeholder as user-visible brand content.
    text = (
        str(page_number)
        if element.kind == "pageNumber" and page_number is not None
        else element.text or ""
    )
    return (
        f'<div id="{escape(element.id, quote=True)}" '
        f'class="master-furniture furniture-{escape(_class_token(element.kind), quote=True)}" '
        f'style="{escape(";".join(styles), quote=True)}">{escape(text)}</div>'
    )


def _overlaps_furniture(
    element: SemanticMasterElement, furniture: list[FurnitureElement]
) -> bool:
    bounds = element.bounds
    return any(
        item.visibility != "hidden"
        and item.bounds.x < bounds.right
        and item.bounds.right > bounds.x
        and item.bounds.y < bounds.bottom
        and item.bounds.bottom > bounds.y
        for item in furniture
    )


def _semantic_text_html(
    element: SemanticMasterElement,
    *,
    layout_id: str,
    is_cover: bool,
    furniture: list[FurnitureElement],
    default_color: str,
) -> str:
    if element.type != "text" or element.visibility == "hidden":
        return ""
    if element.scope.kind == "layout" and element.scope.layout_id != layout_id:
        return ""
    if element.visibility == "coverOnly" and not is_cover:
        return ""
    if element.visibility == "exceptCover" and is_cover:
        return ""
    if (
        element.content.role != "ai-text-zone"
        and _overlaps_furniture(element, furniture)
    ):
        return ""
    bounds = element.bounds
    style = element.style
    styles = [
        "position:absolute",
        f"left:{bounds.x:g}px",
        f"top:{bounds.y:g}px",
        f"width:{bounds.width:g}px",
        f"height:{bounds.height:g}px",
        f"z-index:{element.z_index}",
        "white-space:pre-wrap",
        "overflow:hidden",
        "display:flex",
        f"transform:rotate({element.rotation:g}deg)",
        "transform-origin:center center",
        f"color:{style.color or style.fill or default_color}",
    ]
    if style.font_family:
        styles.append(f"font-family:{_css_string(style.font_family)}")
    if style.font_size is not None:
        styles.append(f"font-size:{style.font_size:g}px")
    if style.font_weight is not None:
        styles.append(f"font-weight:{style.font_weight}")
    if style.text_align:
        styles.append(f"text-align:{style.text_align}")
        styles.append(
            f"justify-content:{'center' if style.text_align == 'center' else 'flex-end' if style.text_align == 'right' else 'flex-start'}"
        )
    if style.vertical_align:
        styles.append(
            f"align-items:{'center' if style.vertical_align == 'middle' else 'flex-end' if style.vertical_align == 'bottom' else 'flex-start'}"
        )
    if style.opacity is not None:
        styles.append(f"opacity:{style.opacity:g}")
    return (
        f'<div id="{escape(element.id, quote=True)}" '
        f'class="master-semantic semantic-role-{escape(_class_token(element.content.role or "none"), quote=True)}" '
        f'style="{escape(";".join(styles), quote=True)}">{escape(element.content.text or "")}</div>'
    )


def _semantic_shape_html(
    element: SemanticMasterElement,
    *,
    layout_id: str,
    is_cover: bool,
) -> str:
    """Render deterministic non-text visual furniture from the effective contract."""

    if element.type not in {"shape", "line"} or element.visibility == "hidden":
        return ""
    if element.scope.kind == "layout" and element.scope.layout_id != layout_id:
        return ""
    if element.visibility == "coverOnly" and not is_cover:
        return ""
    if element.visibility == "exceptCover" and is_cover:
        return ""
    bounds = element.bounds
    style = element.style
    styles = [
        "position:absolute",
        f"left:{bounds.x:g}px",
        f"top:{bounds.y:g}px",
        f"width:{bounds.width:g}px",
        f"height:{bounds.height:g}px",
        f"z-index:{element.z_index}",
        f"transform:rotate({element.rotation:g}deg)",
        "transform-origin:center center",
    ]
    if style.fill:
        styles.append(f"background:{style.fill}")
    if style.stroke:
        width = style.stroke_width if style.stroke_width is not None else 1
        styles.append(f"border:{width:g}px solid {style.stroke}")
    if element.type == "line":
        styles.extend(
            [
                f"background:{style.stroke or style.fill or '#000000'}",
                f"height:{max(1.0, style.stroke_width or 1.0):g}px",
            ]
        )
    elif style.shape == "ellipse":
        styles.append("border-radius:50%")
    if style.opacity is not None:
        styles.append(f"opacity:{style.opacity:g}")
    return (
        f'<div id="{escape(element.id, quote=True)}" '
        f'class="master-semantic semantic-role-{escape(_class_token(element.content.role or "none"), quote=True)}" '
        f'style="{escape(";".join(styles), quote=True)}"></div>'
    )


def _glyph_em_width(character: str) -> float:
    if character.isspace():
        return 0.35
    if unicodedata.east_asian_width(character) in {"W", "F"}:
        return 1.0
    if character in "ilI1.,:;|'`":
        return 0.32
    if character in "MW@#%&":
        return 0.9
    return 0.58


def _wrapped_line_count(text: str, width_px: float, font_pt: float) -> int:
    font_px = font_pt * (4 / 3)
    capacity = max(1.0, width_px / font_px)
    return sum(
        max(
            1,
            math.ceil(sum(_glyph_em_width(char) for char in line) / capacity),
        )
        for line in (text.splitlines() or [""])
    )


def _fitted_body_font_pt(
    contract: DeckMasterContract,
    body_slot,
    body: str,
) -> float:
    """Choose one deterministic, conservative size without adding overflow slides."""

    style = contract.semantic_styles.body
    horizontal = (style.margins.left_pt + style.margins.right_pt) * (4 / 3)
    vertical = (style.margins.top_pt + style.margins.bottom_pt) * (4 / 3)
    width = max(1.0, body_slot.bounds.width - horizontal)
    height = max(1.0, body_slot.bounds.height - vertical)
    # The preview must never crop authored text or grow the deck.  Normal
    # content remains at the contracted size; exceptionally dense content may
    # go below the editing floor, while native PPTX uses normAutofit.
    minimum = 1.0
    candidate = style.font_pt
    while candidate > minimum:
        lines = _wrapped_line_count(body, width, candidate)
        required = lines * candidate * (4 / 3) * style.line_height
        if required <= height:
            return round(candidate, 2)
        candidate = max(minimum, candidate - 0.5)
    return round(minimum, 2)


def _body_grammar_html(
    snapshot: dict,
    *,
    body_slot,
) -> str:
    """Render the exact grammar snapshot used by native OOXML export."""

    kind = snapshot["kind"]
    origin = snapshot.get("contentRect") or {
        "x": body_slot.bounds.x,
        "y": body_slot.bounds.y,
    }
    parts: list[str] = []
    for block in snapshot["blocks"]:
        bounds = block["bounds"]
        insets = block["textInsets"]
        block_text_style = block.get("textStyle") or {}
        block_font_family = block_text_style.get("fontFamily")
        font_family_css = (
            f'font-family:{escape(_css_string(str(block_font_family)), quote=True)};'
            if block_font_family
            else ""
        )
        common = (
            f'position:absolute;left:{bounds["x"] - origin["x"]:g}px;'
            f'top:{bounds["y"] - origin["y"]:g}px;'
            f'width:{bounds["width"]:g}px;height:{bounds["height"]:g}px;'
            f'background:{block["fill"]};border:1px solid {block["stroke"]};'
            f'color:{block["textColor"]};font-size:{block["fontPt"]:g}pt;'
            f'{font_family_css}'
            f'line-height:{block["lineHeight"]:g};'
            f'padding:{insets["top"]:g}px {insets["right"]:g}px '
            f'{insets["bottom"]:g}px {insets["left"]:g}px;'
            'white-space:pre-wrap;overflow:hidden;overflow-wrap:anywhere;'
            'word-break:break-word'
        )
        text = escape(block["text"])
        sequence = int(block["sequence"])
        block_id = escape(block["id"], quote=True)
        if kind == "evidence-stack":
            parts.append(
                f'<article class="evidence-card" id="{block_id}" '
                f'style="{common};border-radius:12px">'
                f'<span class="evidence-index" aria-hidden="true" '
                f'style="position:absolute;left:8px;top:8px;width:20px;height:20px;'
                f'border-radius:50%;background:{block["accent"]};color:#FFFFFF;'
                f'display:flex;align-items:center;justify-content:center;font-size:9pt">'
                f'{sequence}</span><div class="evidence-copy">{text}</div></article>'
            )
        elif kind == "process-grid":
            parts.append(
                f'<li class="process-step" id="{block_id}" '
                f'style="{common};list-style:none;'
                f'border-radius:8px;border-top:6px solid {block["accent"]}">'
                f'<span class="process-index" aria-hidden="true" '
                f'style="position:absolute;left:12px;top:8px;color:{block["accent"]};'
                f'font-weight:700;font-size:10pt">STEP {sequence:02d}</span>'
                f'<div class="process-copy">{text}</div></li>'
            )
        elif kind == "data-metrics":
            parts.append(
                f'<article class="metric-tile" id="{block_id}" '
                f'style="{common};border-radius:10px;border-top:5px solid {block["accent"]}">'
                f'<span aria-hidden="true" style="position:absolute;left:14px;top:8px;'
                f'color:{block["accent"]};font-size:9pt;font-weight:700">KPI {sequence:02d}</span>'
                f'<div class="metric-copy">{text}</div></article>'
            )
        elif kind == "timeline":
            parts.append(
                f'<article class="timeline-step" id="{block_id}" '
                f'style="{common};border-radius:8px;border-bottom:5px solid {block["accent"]}">'
                f'<span aria-hidden="true" style="position:absolute;left:12px;top:8px;'
                f'color:{block["accent"]};font-weight:700;font-size:9pt">{sequence:02d}</span>'
                f'<div class="timeline-copy">{text}</div></article>'
            )
        elif kind == "architecture":
            parts.append(
                f'<article class="architecture-layer" id="{block_id}" '
                f'style="{common};border-radius:6px;border-left:8px solid {block["accent"]}">'
                f'<div class="architecture-copy">{text}</div></article>'
            )
        elif kind in {"cover-statement", "section-bridge", "closing"}:
            semantic_role = escape(
                _class_token(block.get("semanticRole", block["kind"])),
                quote=True,
            )
            parts.append(
                f'<article class="{kind} semantic-role-{semantic_role}" id="{block_id}" '
                f'style="{common};border:none;background:transparent">{text}</article>'
            )
        else:
            parts.append(
                f'<article class="content-card" id="{block_id}" '
                f'style="{common};border-radius:8px">{text}</article>'
            )
    metadata = snapshot.get("documentMetadata")
    metadata_html = ""
    if isinstance(metadata, dict) and metadata.get("visible") is True:
        metadata_html = (
            '<aside class="deck-document-metadata semantic-role-document-id" '
            'style="position:absolute;right:8px;bottom:6px;font-size:9pt;opacity:.7">'
            f'{escape(str(metadata.get("value", "")))}</aside>'
        )
    if kind == "evidence-stack":
        return '<div class="evidence-rail" aria-hidden="true"></div>' + "".join(parts) + metadata_html
    if kind == "process-grid":
        return '<ol class="process-grid" style="margin:0;padding:0">' + "".join(parts) + "</ol>" + metadata_html
    if kind in {"data-metrics", "timeline"}:
        return f'<div class="{kind}" style="margin:0;padding:0">' + "".join(parts) + "</div>" + metadata_html
    if kind == "architecture":
        return '<div class="architecture-stack">' + "".join(parts) + "</div>" + metadata_html
    if kind in {"cover-statement", "section-bridge", "closing"}:
        return "".join(parts) + metadata_html
    return '<div class="content-cards">' + "".join(parts) + "</div>" + metadata_html


def build_preview(
    contract: DeckMasterContract,
    master_revision: int,
    slide: SlideSpecV2,
    *,
    page_number: int | None = None,
) -> PreviewResponse:
    if page_number is not None and page_number < 1:
        raise ValueError("preview page number must be positive")
    canonical_input = {
        "masterDigest": contract.digest,
        "masterRevision": master_revision,
        "slide": slide.model_dump(mode="json", by_alias=True),
    }
    if page_number is not None:
        canonical_input["pageNumber"] = page_number
    canonical = canonical_digest(
        canonical_input
    )
    layout = next(
        (
            candidate
            for candidate in (
                contract.layouts.cover,
                contract.layouts.section,
                contract.layouts.standard,
                contract.layouts.long_title,
            )
            if candidate.id == slide.layout_id
        ),
        None,
    )
    if layout is None:
        raise ValueError("slide layout is not registered by the master")
    title_slot = next(slot for slot in layout.slots if slot.role == "title")
    body_slot = next(slot for slot in layout.slots if slot.role == "body")
    title_style = contract.semantic_styles.title
    body_style = contract.semantic_styles.body
    furniture = [
        contract.furniture.logo,
        contract.furniture.header,
        contract.furniture.footer,
        contract.furniture.divider,
        contract.furniture.page_number,
    ]
    furniture_html = "".join(
        _element_html(element, page_number=page_number) for element in furniture
    )
    semantic_html = "".join(
        _semantic_shape_html(
            element,
            layout_id=slide.layout_id,
            is_cover=layout.kind == "cover",
        )
        + _semantic_text_html(
            element,
            layout_id=slide.layout_id,
            is_cover=layout.kind == "cover",
            furniture=furniture,
            default_color=contract.tokens.colors.text_primary,
        )
        for element in sorted(
            contract.semantic_elements,
            key=lambda item: (item.z_index, item.id),
        )
    )
    title_lines = "".join(
        f"<span>{escape(line)}</span>" for line in slide.title.lines
    )
    body_grammar = build_body_grammar_snapshot(contract, slide)
    retained_v3 = (
        body_grammar is not None
        and body_grammar.get("schemaVersion") == "authored-body-grammar/v3"
    )
    if retained_v3:
        title_snapshot_style = body_grammar["title"].get("textStyle") or {}
        title_font = str(title_snapshot_style.get("fontFamily") or "Noto Sans KR")
        block_fonts = [
            str(block.get("textStyle", {}).get("fontFamily"))
            for block in body_grammar["blocks"]
            if block.get("textStyle", {}).get("fontFamily")
        ]
        body_font = block_fonts[0] if block_fonts else title_font
        preview_fonts = list(dict.fromkeys((body_font, title_font, *block_fonts)))
    else:
        body_font = contract.font_manifest.primary
        preview_fonts = list(
            dict.fromkeys((body_font, *contract.font_manifest.fallbacks))
        )
    font_stack = ",".join(_css_string(name) for name in preview_fonts)
    body_font_family = escape(_css_string(body_font), quote=True)
    render_title_bounds = {
        "x": title_slot.bounds.x,
        "y": title_slot.bounds.y,
        "width": title_slot.bounds.width,
        "height": title_slot.bounds.height,
    }
    render_body_bounds = {
        "x": body_slot.bounds.x,
        "y": body_slot.bounds.y,
        "width": body_slot.bounds.width,
        "height": body_slot.bounds.height,
    }
    if retained_v3:
        render_title_bounds = dict(body_grammar["title"]["bounds"])
        render_body_bounds = dict(body_grammar["contentRect"])
        render_title_style = dict(body_grammar["title"].get("textStyle") or {})
        native_scene = body_grammar.get("nativeScene")
        if not isinstance(native_scene, dict):
            raise ValueError("retained body grammar native scene is missing")
        native_scene_html = _native_scene_html(native_scene)
    else:
        native_scene_html = ""
        render_title_style = {
            "fontFamily": contract.font_manifest.primary,
            "fontPt": slide.title_fit.chosen_font_pt,
            "fontWeight": title_style.font_weight,
            "color": contract.tokens.colors.text_primary,
            "alignment": "left",
            "lineHeight": title_style.line_height,
            "verticalAlignment": "top",
        }
    render_title_font_family = escape(
        _css_string(str(render_title_style["fontFamily"])),
        quote=True,
    )
    body_font_pt = _fitted_body_font_pt(contract, body_slot, slide.body)
    if body_grammar is None:
        body_content = escape(slide.body)
        grammar_attributes = ""
    else:
        body_content = (
            f'<!-- authored-body-grammar:{body_grammar["kind"]}:'
            f'{body_grammar["styleId"]}:{body_grammar["digest"]}:'
            f'{body_grammar["sourceTextDigest"]} -->'
            + _body_grammar_html(
                body_grammar,
                body_slot=body_slot,
            )
        )
        grammar_attributes = ""
    html = (
        "<!doctype html><html><head><meta charset=\"utf-8\"><style>"
        "*{box-sizing:border-box}html,body{margin:0;background:#fff}"
        f".slide{{position:relative;overflow:hidden;width:{contract.canvas.width_px}px;"
        f"height:{contract.canvas.height_px}px;background:{contract.tokens.colors.background};"
        f"font-family:{font_stack}}}"
        ".title span{display:block}"
        "</style></head><body>"
        f'<main class="slide">{native_scene_html}{furniture_html}{semantic_html}'
        f'<section class="title" id="{escape(title_slot.id, quote=True)}" '
        f'style="position:absolute;z-index:2;left:{render_title_bounds["x"]:g}px;top:{render_title_bounds["y"]:g}px;'
        f'width:{render_title_bounds["width"]:g}px;height:{render_title_bounds["height"]:g}px;'
        f'padding:{0 if retained_v3 else title_style.margins.top_pt:g}pt '
        f'{0 if retained_v3 else title_style.margins.right_pt:g}pt '
        f'{0 if retained_v3 else title_style.margins.bottom_pt:g}pt '
        f'{0 if retained_v3 else title_style.margins.left_pt:g}pt;'
        f'font-family:{render_title_font_family};'
        f'font-size:{float(render_title_style["fontPt"]):g}pt;'
        f'line-height:{float(render_title_style["lineHeight"]):g};'
        f'font-weight:{int(render_title_style["fontWeight"])};'
        f'color:{render_title_style["color"]};text-align:{render_title_style["alignment"]};'
        f'display:flex;flex-direction:column;'
        f'justify-content:{"center" if render_title_style["verticalAlignment"] == "middle" else "flex-end" if render_title_style["verticalAlignment"] == "bottom" else "flex-start"}">'
        f"{title_lines}</section>"
        f'<section class="body" id="{escape(body_slot.id, quote=True)}"{grammar_attributes} '
        f'style="position:absolute;z-index:2;left:{render_body_bounds["x"]:g}px;top:{render_body_bounds["y"]:g}px;'
        f'width:{render_body_bounds["width"]:g}px;height:{render_body_bounds["height"]:g}px;'
        f'padding:{0 if retained_v3 else body_style.margins.top_pt:g}pt '
        f'{0 if retained_v3 else body_style.margins.right_pt:g}pt '
        f'{0 if retained_v3 else body_style.margins.bottom_pt:g}pt '
        f'{0 if retained_v3 else body_style.margins.left_pt:g}pt;'
        f'font-family:{body_font_family};'
        f'font-size:{body_font_pt:g}pt;line-height:{body_style.line_height:g};'
        f'color:{contract.tokens.colors.text_secondary};white-space:pre-wrap;'
        'overflow:hidden;overflow-wrap:anywhere;word-break:break-word">'
        f"{body_content}</section></main></body></html>"
    )
    html_digest = canonical_digest({"html": html})
    export_plan = ExportPlan(
        compiler_port="OoxmlMasterCompilerPort",
        compiler_version="ooxml-master-port/v1",
        master_id=contract.master_id,
        master_revision=master_revision,
        master_digest=contract.digest,
        canonical_digest=canonical,
        source_digest=slide.spec_digest,
        expected_package_hash=None,
        slides=[
            ExportSlidePlan(
                slide_id=slide.slide_id,
                layout_id=slide.layout_id,
                spec_digest=slide.spec_digest,
            )
        ],
    )
    return PreviewResponse(
        canonical_digest=canonical,
        html=html,
        png_render_request=PngRenderRequest(
            renderer="authored-vnext-web-preview/1",
            canonical_digest=canonical,
            html_digest=html_digest,
            viewport_width=contract.canvas.width_px,
            viewport_height=contract.canvas.height_px,
        ),
        export_plan=export_plan,
        slide=slide,
    )
