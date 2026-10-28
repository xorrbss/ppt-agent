"""Adapt fixed furniture and compose server layers around unrestricted bodies."""
from __future__ import annotations

import base64
from copy import deepcopy

from lxml import etree

from models.document_canvas import DocumentCanvasMetadata
from models.svg_template_frame import FrameRect, SvgTemplateFrame, SvgTemplateFrameAdaptation
from services.source_content.security import secure_xml
from services.svg_templates.frame_fonts import artifact_digest, bytes_digest, font_asset_for, measure_text, resolve_font_manifest, font_validation_scope
from services.svg_templates.frame_ooxml import SVG_NS, svg_node
from utils.svg_validation import validate_svg

_POLICY = {"version": "physical-frame-adaptation/v1", "font_scale": "physical",
           "body_minimum_fraction": .15, "no_automatic_font_shrink": True}
BACKGROUND_ID = "svg-system-template-background"
BODY_ID = "svg-system-template-body"
FOREGROUND_ID = "svg-system-template-foreground"


def _inside(inner, outer, tolerance=.001):
    return (inner.x >= outer.x - tolerance and inner.y >= outer.y - tolerance
            and inner.x + inner.width <= outer.x + outer.width + tolerance
            and inner.y + inner.height <= outer.y + outer.height + tolerance)


def _intersects(a, b):
    return (a.x < b.x + b.width and b.x < a.x + a.width
            and a.y < b.y + b.height and b.y < a.y + a.height)


def _verify_frame(frame):
    if artifact_digest(frame) != frame.digest:
        raise ValueError("svg_template_frame_digest_mismatch")


@font_validation_scope()
def adapt_frame(frame: SvgTemplateFrame, target_canvas: DocumentCanvasMetadata | dict) -> SvgTemplateFrameAdaptation:
    _verify_frame(frame)
    target = DocumentCanvasMetadata.model_validate(target_canvas)
    physical = target.physical_size
    if not (50 * 36000 <= physical.width_emu <= 1000 * 36000
            and 50 * 36000 <= physical.height_emu <= 1000 * 36000
            and .25 <= physical.width_emu / physical.height_emu <= 4):
        raise ValueError("svg_template_target_canvas_unsupported")
    if any(issue.severity == "blocked" for issue in frame.issues):
        raise ValueError("svg_template_frame_unsupported")
    resolve_font_manifest(frame.font_manifest)
    source = frame.source_canvas
    scale = source.emu_per_unit / target.emu_per_unit
    dw, dh = target.width_px - source.width_px * scale, target.height_px - source.height_px * scale
    elements = []
    page = FrameRect(x=0, y=0, width=target.width_px, height=target.height_px)
    for element in frame.elements:
        box = element.bounds
        dx = dw * {"left": 0, "center": .5, "right": 1}[element.anchor_x]
        dy = dh * {"top": 0, "center": .5, "bottom": 1}[element.anchor_y]
        width, height = box.width * scale, box.height * scale
        if element.size_policy == "span-between-anchors":
            width += dw
        if element.layer == "background":
            height += dh
        if element.dynamic_key == "title":
            width += dw
        if width < 0 or height < 0:
            raise ValueError("svg_template_adaptation_fixed_element_does_not_fit")
        adapted_bounds = FrameRect(x=box.x * scale + dx, y=box.y * scale + dy,
                                   width=width, height=height)
        if element.kind == "text" and not _inside(adapted_bounds, page):
            # A transparent text box is not painted furniture. Trim only empty
            # horizontal container space; the actual glyph coordinates below
            # keep their physical size and anchor-derived position unchanged.
            left = max(0, adapted_bounds.x)
            right = min(page.width, adapted_bounds.x + adapted_bounds.width)
            if right <= left:
                raise ValueError("svg_template_adaptation_fixed_element_does_not_fit")
            adapted_bounds = adapted_bounds.model_copy(update={"x": left, "width": right - left})
        if not _inside(adapted_bounds, page):
            raise ValueError("svg_template_adaptation_fixed_element_does_not_fit")
        node = secure_xml(element.svg.encode(), part_name="frame-element.svg")
        # The existing SVG contract only accepts translations. Bake all physical
        # scale into explicit coordinates; never relax the global SVG allowlist.
        for child in node.iter():
            for key in ("x", "x1", "x2", "cx"):
                if key in child.attrib:
                    child.set(key, str(float(child.get(key)) * scale + dx))
            for key in ("y", "y1", "y2", "cy"):
                if key in child.attrib:
                    child.set(key, str(float(child.get(key)) * scale + dy))
            for key in ("width", "height", "rx", "ry", "r", "font-size", "stroke-width"):
                if key in child.attrib:
                    child.set(key, str(float(child.get(key)) * scale))
            if etree.QName(child).localname == "image":
                child.set("width", str(max(1, round(float(child.get("width"))))))
                child.set("height", str(max(1, round(float(child.get("height"))))))
        if element.kind == "rect":
            node.set("width", str(width))
            node.set("height", str(height))
        elif element.kind == "line" and element.size_policy == "span-between-anchors":
            node.set("x2", str(adapted_bounds.x + width))
        text_region = element.text_region
        if text_region is not None:
            text_region = FrameRect(x=text_region.x * scale + dx, y=text_region.y * scale + dy,
                width=text_region.width * scale + (dw if element.dynamic_key == "title" else 0),
                height=text_region.height * scale)
            if element.kind == "text":
                left = max(adapted_bounds.x, text_region.x)
                right = min(adapted_bounds.x + adapted_bounds.width, text_region.x + text_region.width)
                if right <= left:
                    raise ValueError("svg_template_adaptation_text_space_insufficient")
                text_region = text_region.model_copy(update={"x": left, "width": right - left})
        if element.kind == "text" and not element.dynamic_key:
            for text in node.iter(f"{{{SVG_NS}}}text"):
                asset = font_asset_for(frame.font_manifest, text.get("font-family"), int(text.get("font-weight", "400")))
                run_width, ascent, descent = measure_text(asset, text.text or "", float(text.get("font-size")))
                paint = FrameRect(x=float(text.get("x")), y=float(text.get("y")) - ascent,
                                  width=run_width, height=ascent + descent)
                if not _inside(paint, text_region or adapted_bounds, tolerance=.5):
                    raise ValueError("svg_template_adaptation_text_does_not_fit")
        elements.append(element.model_copy(update={"bounds": adapted_bounds, "text_region": text_region,
                                                   "svg": etree.tostring(node, encoding="unicode")}))
    body = frame.body_region
    body_region = FrameRect(x=body.x * scale, y=body.y * scale,
        width=max(0, body.width * scale + dw), height=max(0, body.height * scale + dh))
    if not _inside(body_region, page) or body_region.height < target.height_px * .15 or body_region.width < target.width_px * .15:
        raise ValueError("svg_template_adaptation_body_space_insufficient")
    # Compare physical boxes against the original frame contract. Reserved
    # interior regions follow their actual corresponding elements.
    forbidden = []
    for original in frame.forbidden_regions:
        candidates = [new.bounds for old, new in zip(frame.elements, elements) if old.bounds == original]
        if not candidates:
            raise ValueError("svg_template_adaptation_reservation_unresolved")
        forbidden.extend(candidates)
    for index, element in enumerate(elements):
        if element.layer == "background":
            continue
        for other_index, other in enumerate(elements[:index]):
            if other.layer == "background":
                continue
            if (_intersects(element.bounds, other.bounds)
                    and not _intersects(frame.elements[index].bounds, frame.elements[other_index].bounds)):
                raise ValueError("svg_template_adaptation_new_collision")
    adapted = frame.model_copy(update={"source_canvas": target, "elements": tuple(elements),
                                       "body_region": body_region, "forbidden_regions": tuple(forbidden), "digest": ""})
    adapted = adapted.model_copy(update={"digest": artifact_digest(adapted)})
    key = artifact_digest({"frame": frame.digest, "canvas": target.model_dump(mode="json"),
        "fonts": frame.font_manifest.digest, "policy": _POLICY})
    result = SvgTemplateFrameAdaptation(adaptation_id="adapt-" + key[7:31], source_frame_digest=frame.digest,
        target_canvas=target, policy_digest=artifact_digest(_POLICY), frame=adapted, digest="")
    return result.model_copy(update={"digest": artifact_digest(result)})


def trusted_frame_images(frame: SvgTemplateFrame) -> dict[str, str]:
    result = {}
    for element in frame.elements:
        node = secure_xml(element.svg.encode(), part_name="frame-element.svg")
        for child in node.iter(f"{{{SVG_NS}}}image"):
            result[child.get("id", element.element_id)] = bytes_digest(base64.b64decode(child.get("href").split(",", 1)[1], validate=True))
    return result


def _element_node(element, frame, values, *, require_dynamic):
    node = secure_xml(element.svg.encode(), part_name="frame-element.svg")
    if element.dynamic_key:
        if element.dynamic_key not in values:
            if require_dynamic:
                raise ValueError("svg_template_dynamic_value_missing:" + element.dynamic_key)
            return node
        texts = list(node.iter(f"{{{SVG_NS}}}text"))
        if len(texts) != 1:
            raise ValueError("svg_template_dynamic_text_invalid")
        text = texts[0]
        value = str(values[element.dynamic_key])
        if not value.strip() or len(value) > 2000 or "\n" in value:
            raise ValueError("svg_template_dynamic_value_invalid")
        asset = font_asset_for(frame.font_manifest, text.get("font-family"), int(text.get("font-weight", "400")))
        width, ascent, descent = measure_text(asset, value, float(text.get("font-size")))
        x, y = float(text.get("x")), float(text.get("y"))
        region = element.text_region or element.bounds
        if element.text_align in {"l", "ctr", "r"}:
            x = region.x + {"l": 0, "ctr": .5, "r": 1}[element.text_align] * (region.width - width)
        text.set("x", str(x))
        measured = FrameRect(x=x, y=y - ascent, width=width, height=ascent + descent)
        if not _inside(measured, region, tolerance=.5):
            raise ValueError("svg_template_dynamic_text_overflow")
        text.text = value
    return node


def render_frame_groups(frame: SvgTemplateFrame, *, dynamic_values: dict | None = None,
                        require_dynamic: bool = True) -> tuple[etree._Element, etree._Element]:
    _verify_frame(frame)
    background = svg_node("g", id=BACKGROUND_ID)
    foreground = svg_node("g", id=FOREGROUND_ID)
    for element in frame.elements:
        (background if element.layer == "background" else foreground).append(
            _element_node(element, frame, dynamic_values or {}, require_dynamic=require_dynamic))
    return background, foreground


@font_validation_scope()
def render_frame_svg(frame: SvgTemplateFrame, *, dynamic_values: dict | None = None) -> str:
    """Review candidate with original fixed text; no body or fidelity claim."""
    _verify_frame(frame)
    canvas = frame.source_canvas
    root = svg_node("svg", width=canvas.width_px, height=canvas.height_px,
                    viewBox=f"0 0 {canvas.width_px} {canvas.height_px}")
    root.extend(render_frame_groups(frame, dynamic_values=dynamic_values, require_dynamic=False))
    return etree.tostring(root, encoding="unicode")


@font_validation_scope()
def compose_frame_svg(frame: SvgTemplateFrame | SvgTemplateFrameAdaptation, body_svg: str, *,
                      dynamic_values: dict, source_manifest: dict | None = None) -> str:
    """Compose a candidate. validate_body_paint must gate final publication."""
    if isinstance(frame, SvgTemplateFrameAdaptation):
        if artifact_digest(frame) != frame.digest:
            raise ValueError("svg_template_adaptation_digest_mismatch")
        frame = frame.frame
    _verify_frame(frame)
    resolve_font_manifest(frame.font_manifest)
    if any(issue.severity == "blocked" for issue in frame.issues):
        raise ValueError("svg_template_frame_unsupported")
    canvas = frame.source_canvas
    validate_svg(body_svg, width=canvas.width_px, height=canvas.height_px,
                 allow_system_page_number=False, source_manifest=source_manifest)
    body = secure_xml(body_svg.encode(), part_name="body.svg")
    for index, node in enumerate(body.iter()):
        node_id = node.get("id")
        if node_id and not node_id.startswith("body-"):
            raise ValueError("svg_template_body_namespace_required")
        node.set("id", node_id or f"body-node-{index}")
        if etree.QName(node).localname in {"text", "tspan"}:
            parent = node.getparent()
            family = node.get("font-family") or (parent.get("font-family") if parent is not None else None)
            weight = node.get("font-weight") or (parent.get("font-weight", "400") if parent is not None else "400")
            weight = {"normal": "400", "bold": "700"}.get(weight, weight)
            asset = font_asset_for(frame.font_manifest, family or "", int(weight))
            from services.svg_templates.frame_fonts import verify_font_text
            verify_font_text(asset, "".join(node.itertext()))
    root = svg_node("svg", width=canvas.width_px, height=canvas.height_px,
                    viewBox=f"0 0 {canvas.width_px} {canvas.height_px}")
    background, foreground = render_frame_groups(frame, dynamic_values=dynamic_values)
    content = svg_node("g", id=BODY_ID)
    for child in body:
        content.append(deepcopy(child))
    root.extend((background, content, foreground))
    result = etree.tostring(root, encoding="unicode")
    validate_svg(result, width=canvas.width_px, height=canvas.height_px, source_manifest=source_manifest,
                 trusted_furniture_images=trusted_frame_images(frame))
    return result


def validate_body_paint(frame: SvgTemplateFrame, bounds: tuple[FrameRect | dict, ...]) -> dict:
    """Only trusted browser measurements before foreground occlusion qualify."""
    _verify_frame(frame)
    boxes = tuple(FrameRect.model_validate(box) for box in bounds)
    if not boxes:
        raise ValueError("svg_template_body_measurement_missing")
    if any(not _inside(box, frame.body_region, tolerance=.5) for box in boxes):
        raise ValueError("svg_template_body_outside_allowed_region")
    if any(_intersects(box, reserved) for box in boxes for reserved in frame.forbidden_regions):
        raise ValueError("svg_template_body_intersects_reserved_region")
    return {"frame_digest": frame.digest, "body_bounds_digest": artifact_digest([b.model_dump() for b in boxes]),
            "body_region_verified": True}
