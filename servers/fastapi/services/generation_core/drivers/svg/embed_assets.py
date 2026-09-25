"""Embed library vector icons into authored SVG slides as contract-safe shapes.

The author model places an offered vector icon as an ``<image href="<slot>">``
placeholder. After authoring, ``replace_vector_icon_images`` swaps each such
placeholder for the flattened icon (``icon_flatten.py``) drawn as plain
polylines/polygons/circles under a single ``translate`` group, so the icon
stays editable in the exported PPTX. ``rasterize_icon`` renders the same
geometry to PNG for the model-facing preview and for the raster fallback of
icons that exceed ``ICON_POINT_BUDGET``.
"""
from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET

from services.generation_core.drivers.svg.icon_flatten import (
    DEFAULT_TOLERANCE,
    ICON_POINT_BUDGET,
    FlattenedIcon,
    IconFlattenBudgetExceeded,
    flatten_icon_svg,
)
from services.svg_contract import SVG_NS

__all__ = [
    "DEFAULT_TOLERANCE", "ICON_POINT_BUDGET", "FlattenedIcon", "IconFlattenBudgetExceeded",
    "apply_vector_icon_quality", "flatten_icon_svg", "icon_group", "rasterize_icon",
    "replace_vector_icon_images",
]

_MIN_STROKE_WIDTH = 0.5
_MIN_SIZE = 0.01
_HREF_KEYS = ("href", "{http://www.w3.org/1999/xlink}href")
_SUPERSAMPLE = 4


def _fmt(value: float) -> str:
    text = f"{round(value, 2):.2f}".rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _fit(icon: FlattenedIcon, x: float, y: float, width: float, height: float) -> tuple[float, float, float]:
    """Contain-fit the viewBox into the box, centred: (scale, offset_x, offset_y)."""
    scale = min(width / icon.view_width, height / icon.view_height)
    return scale, x + (width - icon.view_width * scale) / 2, y + (height - icon.view_height * scale) / 2


def _child_attributes(primitive: dict, scale: float) -> dict[str, str]:
    tag = primitive["tag"]
    if tag in {"polyline", "polygon"}:
        points: list[str] = []
        for px, py in primitive["points"]:
            pair = f"{_fmt(px * scale)},{_fmt(py * scale)}"
            if not points or points[-1] != pair:
                points.append(pair)
        if len(points) < 2:
            points.append(points[0])
        attrs = {"points": " ".join(points)}
    elif tag == "line":
        attrs = {key: _fmt(primitive[key] * scale) for key in ("x1", "y1", "x2", "y2")}
    elif tag == "circle":
        attrs = {"cx": _fmt(primitive["cx"] * scale), "cy": _fmt(primitive["cy"] * scale),
                 "r": _fmt(max(_MIN_SIZE, primitive["r"] * scale))}
    elif tag == "ellipse":
        attrs = {"cx": _fmt(primitive["cx"] * scale), "cy": _fmt(primitive["cy"] * scale),
                 "rx": _fmt(max(_MIN_SIZE, primitive["rx"] * scale)),
                 "ry": _fmt(max(_MIN_SIZE, primitive["ry"] * scale))}
    elif tag == "rect":
        attrs = {"x": _fmt(primitive["x"] * scale), "y": _fmt(primitive["y"] * scale),
                 "width": _fmt(max(_MIN_SIZE, primitive["width"] * scale)),
                 "height": _fmt(max(_MIN_SIZE, primitive["height"] * scale))}
    else:
        raise ValueError("icon_primitive_unsupported:" + str(tag)[:40])
    if primitive.get("fill", "none") != "none":
        attrs["fill"] = primitive["fill"]
    if primitive.get("stroke") == "none":
        attrs["stroke"] = "none"
    elif "stroke-width" in primitive:
        attrs["stroke-width"] = _fmt(max(_MIN_STROKE_WIDTH, primitive["stroke-width"] * scale))
    return attrs


def icon_group(icon: FlattenedIcon, *, x: float, y: float, width: float, height: float,
               element_id: str | None = None) -> ET.Element:
    """One translate-only ``<g>`` drawing ``icon`` contain-fit into the box."""
    if not (width > 0 and height > 0):
        raise ValueError("icon_box_invalid")
    scale, offset_x, offset_y = _fit(icon, x, y, width, height)
    attrs = {"id": element_id} if element_id else {}
    attrs.update({"transform": f"translate({_fmt(offset_x)},{_fmt(offset_y)})", "fill": "none",
                  "stroke": icon.color, "stroke-width": _fmt(max(_MIN_STROKE_WIDTH, icon.stroke_width * scale))})
    group = ET.Element(f"{{{SVG_NS}}}g", attrs)
    for primitive in icon.primitives:
        ET.SubElement(group, f"{{{SVG_NS}}}{primitive['tag']}", _child_attributes(primitive, scale))
    return group


def rasterize_icon(icon: FlattenedIcon, *, size: int = 256) -> bytes:
    """Antialiased RGBA PNG of ``icon`` contain-fit into ``size`` x ``size``."""
    from PIL import Image, ImageDraw

    if size <= 0:
        raise ValueError("icon_raster_size_invalid")
    canvas = size * _SUPERSAMPLE
    scale, offset_x, offset_y = _fit(icon, 0.0, 0.0, float(canvas), float(canvas))
    rgb = tuple(int(icon.color[i:i + 2], 16) for i in (1, 3, 5))
    # Draw coverage only and resample that: the icon is monochrome, so the
    # colour stays exact and never fringes or overshoots at antialiased edges.
    ink = 255
    mask = Image.new("L", (canvas, canvas), 0)
    draw = ImageDraw.Draw(mask)

    def point(px: float, py: float) -> tuple[float, float]:
        return offset_x + px * scale, offset_y + py * scale

    def dot(center: tuple[float, float], radius: float) -> None:
        draw.ellipse((center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius), fill=ink)

    for primitive in icon.primitives:
        tag, filled = primitive["tag"], primitive.get("fill", "none") != "none"
        stroke = 0.0 if primitive.get("stroke") == "none" else \
            primitive.get("stroke-width", icon.stroke_width) * scale
        pixels = max(1, round(stroke))
        if tag in {"polyline", "polygon", "line"}:
            if tag == "line":
                points = [point(primitive["x1"], primitive["y1"]), point(primitive["x2"], primitive["y2"])]
            else:
                points = [point(px, py) for px, py in primitive["points"]]
            if filled and len(points) >= 3:
                draw.polygon(points, fill=ink)
            if stroke > 0:
                draw.line(points + points[:1] if tag == "polygon" else points, fill=ink, width=pixels)
                for vertex in points:  # round joins and caps
                    dot(vertex, stroke / 2)
            continue
        if tag == "rect":
            (x0, y0) = point(primitive["x"], primitive["y"])
            box = (x0, y0, x0 + primitive["width"] * scale, y0 + primitive["height"] * scale)
        else:
            cx, cy = point(primitive["cx"], primitive["cy"])
            rx = primitive["r" if tag == "circle" else "rx"] * scale
            ry = primitive["r" if tag == "circle" else "ry"] * scale
            box = (cx - rx, cy - ry, cx + rx, cy + ry)
        shape = draw.rectangle if tag == "rect" else draw.ellipse
        if filled:
            shape(box, fill=ink)
        if stroke > 0:
            half = stroke / 2
            shape((box[0] - half, box[1] - half, box[2] + half, box[3] + half), outline=ink, width=pixels)
    image = Image.new("RGBA", (size, size), (*rgb, 255))
    image.putalpha(mask.resize((size, size), Image.LANCZOS))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _number(value: str | None, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return None


def _translation(value: str | None) -> tuple[float, float] | None:
    if not value:
        return 0.0, 0.0
    match = re.fullmatch(r"\s*translate\(\s*([^()]*?)\s*\)\s*", value)
    if not match:
        return None
    parts = [float(item) for item in re.split(r"[\s,]+", match[1]) if item]
    if len(parts) not in {1, 2}:
        return None
    return parts[0], parts[1] if len(parts) == 2 else 0.0


def _sanitize_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "-", value)[:101]


def replace_vector_icon_images(root: ET.Element, vector_icons: dict[str, FlattenedIcon]) -> set[str]:
    """Replace ``<image href=slot>`` placeholders by vector icon groups in place."""
    targets = []
    for parent in root.iter():
        for child in parent:
            if _local(child.tag) != "image":
                continue
            href = next((child.get(key) for key in _HREF_KEYS if child.get(key) is not None), None)
            if href in vector_icons:
                targets.append((parent, child, href))
    replaced_nodes = {id(child) for _, child, _ in targets}
    used_ids = {node.get("id") for node in root.iter() if node.get("id") and id(node) not in replaced_nodes}
    replaced: set[str] = set()
    for parent, child, slot in targets:
        x, y = _number(child.get("x"), 0.0), _number(child.get("y"), 0.0)
        width, height = _number(child.get("width")), _number(child.get("height"))
        shift = _translation(child.get("transform"))
        if None in (x, y, width, height) or shift is None or width <= 0 or height <= 0:
            continue  # not a well-formed placeholder: leave it for the validator
        base = _sanitize_id(f"asset-{slot}")
        element_id, suffix = base, 2
        while element_id in used_ids:
            element_id, suffix = f"{base[:95]}-{suffix}", suffix + 1
        used_ids.add(element_id)
        group = icon_group(vector_icons[slot], x=x + shift[0], y=y + shift[1], width=width, height=height,
                           element_id=element_id)
        if child.get("opacity") is not None:
            group.set("opacity", child.get("opacity"))
        group.tail = child.tail
        index = list(parent).index(child)
        parent.remove(child)
        parent.insert(index, group)
        replaced.add(slot)
    return replaced


def apply_vector_icon_quality(quality: dict, payload: dict) -> None:
    """Record offered/used vector icons; an offered icon left unused fails quality."""
    offer = (payload or {}).get("assetOffer")
    vector = offer.get("vectorIcons") if isinstance(offer, dict) else None
    if not isinstance(vector, dict):
        return
    offered = {str(slot) for slot in vector.get("offered") or []}
    if not offered:
        return
    used = {str(slot) for slot in vector.get("used") or []}
    omitted = sorted(offered - used)
    quality["assetOffer"] = {"offered": sorted(offered), "used": sorted(used), "omitted": omitted}
    if omitted:
        quality["passed"] = False
        quality.setdefault("failures", []).append({"kind": "asset_icon_omitted", "slotIds": omitted})
