"""Geometry and image transforms for source PPTX objects."""

from __future__ import annotations

import math
import re
from typing import Any

from lxml import etree

from services.source_content.errors import SourceContentError, UNSUPPORTED_OBJECT
from services.source_content.pptx_extraction_constants import AffineTransform, IDENTITY_TRANSFORM, NS


def _compose_affine(
    outer: AffineTransform, inner: AffineTransform
) -> AffineTransform:
    oa, ob, oc, od, oe, of = outer
    ia, ib, ic, id_, ie, if_ = inner
    return (
        oa * ia + oc * ib,
        ob * ia + od * ib,
        oa * ic + oc * id_,
        ob * ic + od * id_,
        oa * ie + oc * if_ + oe,
        ob * ie + od * if_ + of,
    )


def _apply_affine(transform: AffineTransform, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = transform
    return a * x + c * y + e, b * x + d * y + f


def _group_child_transform(element: etree._Element) -> AffineTransform:
    xfrm = element.find("./p:grpSpPr/a:xfrm", namespaces=NS)
    if xfrm is None:
        raise SourceContentError(
            UNSUPPORTED_OBJECT,
            detail={"reason": "invalid_group_coordinate_space"},
        )
    off = xfrm.find("a:off", namespaces=NS)
    ext = xfrm.find("a:ext", namespaces=NS)
    child_off = xfrm.find("a:chOff", namespaces=NS)
    child_ext = xfrm.find("a:chExt", namespaces=NS)
    if ext is None or child_ext is None:
        raise SourceContentError(
            UNSUPPORTED_OBJECT,
            detail={"reason": "invalid_group_coordinate_space"},
        )
    width = float(ext.get("cx", "0"))
    height = float(ext.get("cy", "0"))
    child_width = float(child_ext.get("cx", "0"))
    child_height = float(child_ext.get("cy", "0"))
    if width <= 0 or height <= 0 or child_width <= 0 or child_height <= 0:
        raise SourceContentError(
            UNSUPPORTED_OBJECT,
            detail={"reason": "invalid_group_coordinate_space"},
        )
    x = float(off.get("x", "0")) if off is not None else 0.0
    y = float(off.get("y", "0")) if off is not None else 0.0
    child_x = float(child_off.get("x", "0")) if child_off is not None else 0.0
    child_y = float(child_off.get("y", "0")) if child_off is not None else 0.0
    scale_x = width / child_width
    scale_y = height / child_height
    base: AffineTransform = (
        scale_x,
        0.0,
        0.0,
        scale_y,
        x - child_x * scale_x,
        y - child_y * scale_y,
    )
    center_x = x + width / 2.0
    center_y = y + height / 2.0
    angle = math.radians(float(xfrm.get("rot", "0")) / 60_000.0)
    flip_x = -1.0 if xfrm.get("flipH", "0").lower() in {"1", "true", "on"} else 1.0
    flip_y = -1.0 if xfrm.get("flipV", "0").lower() in {"1", "true", "on"} else 1.0
    cos_angle = math.cos(angle)
    sin_angle = math.sin(angle)
    around_center: AffineTransform = (
        cos_angle * flip_x,
        sin_angle * flip_x,
        -sin_angle * flip_y,
        cos_angle * flip_y,
        center_x - cos_angle * flip_x * center_x + sin_angle * flip_y * center_y,
        center_y - sin_angle * flip_x * center_x - cos_angle * flip_y * center_y,
    )
    return _compose_affine(around_center, base)


def _geometry(
    element: etree._Element,
    *,
    parent_transform: AffineTransform = IDENTITY_TRANSFORM,
    inherited_shapes: tuple[tuple[str, etree._Element], ...] = (),
    local_source: str = "slide",
    source_block_id: str | None = None,
) -> dict[str, Any]:
    def transform_of(shape: etree._Element) -> etree._Element | None:
        transform = shape.find("./p:spPr/a:xfrm", namespaces=NS)
        return transform if transform is not None else shape.find("./p:xfrm", namespaces=NS)

    candidates = [(local_source, element), *inherited_shapes]
    transforms = [
        (source, transform)
        for source, shape in candidates
        if (transform := transform_of(shape)) is not None
    ]
    if not transforms:
        return {}

    def component(node_name: str, attribute: str) -> tuple[float, str] | None:
        for source, transform in transforms:
            node = transform.find(f"a:{node_name}", namespaces=NS)
            if node is None or node.get(attribute) is None:
                continue
            try:
                return float(node.get(attribute, "0")), source
            except ValueError:
                return None
        return None

    resolved = {
        "x": component("off", "x"),
        "y": component("off", "y"),
        "cx": component("ext", "cx"),
        "cy": component("ext", "cy"),
    }
    if any(value is None for value in resolved.values()):
        return {}
    x, source_x = resolved["x"]  # type: ignore[misc]
    y, source_y = resolved["y"]  # type: ignore[misc]
    width, source_width = resolved["cx"]  # type: ignore[misc]
    height, source_height = resolved["cy"]  # type: ignore[misc]
    corners = (
        _apply_affine(parent_transform, x, y),
        _apply_affine(parent_transform, x + width, y),
        _apply_affine(parent_transform, x, y + height),
        _apply_affine(parent_transform, x + width, y + height),
    )
    left = min(point[0] for point in corners)
    top = min(point[1] for point in corners)
    right = max(point[0] for point in corners)
    bottom = max(point[1] for point in corners)
    sources = {
        "x": source_x,
        "y": source_y,
        "cx": source_width,
        "cy": source_height,
    }
    geometry: dict[str, Any] = {
        "x": round(left),
        "y": round(top),
        "cx": round(right - left),
        "cy": round(bottom - top),
        "source": next(iter(set(sources.values()))) if len(set(sources.values())) == 1 else "mixed",
        "sources": sources,
    }
    if source_block_id is not None:
        geometry["sourceBlockId"] = source_block_id
    for attribute, key, divisor in (
        ("rot", "rotationDegrees", 60_000.0),
        ("flipH", "flipH", None),
        ("flipV", "flipV", None),
    ):
        for source, transform in transforms:
            value = transform.get(attribute)
            if value is None:
                continue
            if divisor is not None:
                try:
                    geometry[key] = float(value) / divisor
                except ValueError:
                    pass
            else:
                geometry[key] = value.lower() in {"1", "true", "on"}
            geometry["sources"][key] = source
            break
    return geometry


def _image_crop(element: etree._Element) -> dict[str, float]:
    source_rect = element.find(".//p:blipFill/a:srcRect", namespaces=NS)

    def fraction(attribute: str) -> float:
        if source_rect is None:
            return 0.0
        try:
            return int(source_rect.get(attribute, "0")) / 100_000.0
        except ValueError:
            return 0.0

    return {
        "left": fraction("l"),
        "top": fraction("t"),
        "right": fraction("r"),
        "bottom": fraction("b"),
    }


def _image_color_transforms(element: etree._Element) -> list[dict[str, Any]]:
    """Supported occurrence-local ``a:blip`` effects in source order."""

    def unsupported(reason: str) -> SourceContentError:
        return SourceContentError(
            UNSUPPORTED_OBJECT,
            detail={"reason": "unsupported_image_color_transform", "detail": reason},
        )

    def literal_color(container: etree._Element | None) -> tuple[str, float]:
        if container is None or len(container) != 1:
            raise unsupported("color_container_invalid")
        color = container[0]
        if etree.QName(color).localname != "srgbClr":
            raise unsupported("color_model_not_srgb")
        value = (color.get("val") or "").strip()
        if not re.fullmatch(r"[0-9A-Fa-f]{6}", value):
            raise unsupported("srgb_value_invalid")
        alpha = 1.0
        for transform in color:
            if etree.QName(transform).localname != "alpha":
                raise unsupported("nested_color_transform_unsupported")
            try:
                raw_alpha = int(transform.get("val", ""))
            except ValueError as exc:
                raise unsupported("alpha_value_invalid") from exc
            if not 0 <= raw_alpha <= 100_000:
                raise unsupported("alpha_value_out_of_range")
            alpha = raw_alpha / 100_000.0
        return f"#{value.upper()}", alpha

    def fixed_percentage(effect: etree._Element, attribute: str) -> float:
        raw = effect.get(attribute, "0").strip()
        try:
            value = float(raw[:-1]) / 100.0 if raw.endswith("%") else int(raw) / 100_000.0
        except ValueError as exc:
            raise unsupported(f"{attribute}_value_invalid") from exc
        if not -1.0 <= value <= 1.0:
            raise unsupported(f"{attribute}_value_out_of_range")
        return value

    blip = element.find(".//a:blip", namespaces=NS)
    if blip is None:
        return []
    result: list[dict[str, Any]] = []
    for effect in blip:
        effect_name = etree.QName(effect).localname
        if effect_name == "extLst":
            continue
        if effect_name == "clrChange":
            raw_use_alpha = effect.get("useA")
            if raw_use_alpha is None or raw_use_alpha in {"1", "true"}:
                use_alpha = True
            elif raw_use_alpha in {"0", "false"}:
                use_alpha = False
            else:
                raise unsupported("use_alpha_invalid")
            from_color, from_alpha = literal_color(
                effect.find("a:clrFrom", namespaces=NS)
            )
            to_color, to_alpha = literal_color(effect.find("a:clrTo", namespaces=NS))
            result.append(
                {
                    "kind": "colorChange",
                    "fromColor": from_color,
                    "fromAlpha": from_alpha,
                    "toColor": to_color,
                    "toAlpha": to_alpha,
                    "useAlpha": use_alpha,
                }
            )
            continue
        if effect_name == "lum":
            if len(effect):
                raise unsupported("luminance_children_unsupported")
            result.append(
                {
                    "kind": "luminance",
                    "brightness": fixed_percentage(effect, "bright"),
                    "contrast": fixed_percentage(effect, "contrast"),
                }
            )
            continue
        raise unsupported(f"effect_{effect_name}_unsupported")
    return result
