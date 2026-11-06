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
import math
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
    elif tag == "path":
        rings = []
        for ring in primitive["subpaths"]:
            pairs = [f"{_fmt(px * scale)},{_fmt(py * scale)}" for px, py in ring]
            rings.append(f"M{pairs[0]} L" + " ".join(pairs[1:]) + " Z")
        attrs = {"d": " ".join(rings)}
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
    else:
        if primitive.get("stroke", "").startswith("#"):
            attrs["stroke"] = primitive["stroke"]
        if "stroke-width" in primitive:
            attrs["stroke-width"] = _fmt(max(_MIN_STROKE_WIDTH, primitive["stroke-width"] * scale))
    for name in ("opacity", "fill-opacity", "stroke-opacity"):
        if name in primitive:
            attrs[name] = _fmt(primitive[name])
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
    if icon.line_cap != "butt":
        attrs["stroke-linecap"] = icon.line_cap
    if icon.line_join != "miter":
        attrs["stroke-linejoin"] = icon.line_join
    group = ET.Element(f"{{{SVG_NS}}}g", attrs)
    for primitive in icon.primitives:
        ET.SubElement(group, f"{{{SVG_NS}}}{primitive['tag']}", _child_attributes(primitive, scale))
    return group


def rasterize_icon(icon: FlattenedIcon, *, size: int = 256) -> bytes:
    """Antialiased RGBA PNG of ``icon`` contain-fit into ``size`` x ``size``.

    A monochrome icon is drawn as one coverage mask in its colour, so the
    colour stays exact and never fringes at antialiased edges. A multi-colour
    drawing, or any shape with opacity, is painted shape by shape in order.
    A compound path uses the same nonzero winding rule as the emitted SVG.
    """
    from PIL import Image, ImageDraw

    if size <= 0:
        raise ValueError("icon_raster_size_invalid")
    canvas = size * _SUPERSAMPLE
    scale, offset_x, offset_y = _fit(icon, 0.0, 0.0, float(canvas), float(canvas))

    def point(px: float, py: float) -> tuple[float, float]:
        return offset_x + px * scale, offset_y + py * scale

    def compound_coverage(subpaths, draw) -> None:
        # Scan pixel centres, counting directed edge crossings. XOR of polygon
        # masks is even-odd and punches false holes in overlapping or nested
        # same-direction rings; signed winding also handles self intersections.
        edges = []
        for ring in subpaths:
            points = [point(px, py) for px, py in ring]
            for (ax, ay), (bx, by) in zip(points, points[1:] + points[:1]):
                if ay != by:
                    edges.append((ax, ay, bx, by))
        if not edges:
            return
        first_row = max(0, math.ceil(min(min(ay, by) for _, ay, _, by in edges) - 0.5))
        last_row = min(canvas, math.ceil(max(max(ay, by) for _, ay, _, by in edges) - 0.5))
        for row in range(first_row, last_row):
            y = row + 0.5
            crossings = sorted((ax + (y - ay) * (bx - ax) / (by - ay), 1 if by > ay else -1)
                               for ax, ay, bx, by in edges if min(ay, by) <= y < max(ay, by))
            winding, previous_x = 0, None
            for x, direction in crossings:
                if winding and previous_x is not None:
                    left = max(0, math.ceil(previous_x - 0.5))
                    right = min(canvas - 1, math.ceil(x - 0.5) - 1)
                    if left <= right:
                        draw.line((left, row, right, row), fill=255)
                winding += direction
                previous_x = x

    def coverage(primitive: dict, mask) -> None:
        """Draw one primitive's fill and stroke coverage (255) into ``mask``."""
        draw = ImageDraw.Draw(mask)
        tag, filled = primitive["tag"], primitive.get("fill", "none") != "none"
        stroke = 0.0 if primitive.get("stroke") == "none" else \
            primitive.get("stroke-width", icon.stroke_width) * scale
        pixels = max(1, round(stroke))

        def dot(center: tuple[float, float], radius: float) -> None:
            draw.ellipse((center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius), fill=255)

        if tag == "path":
            compound_coverage(primitive["subpaths"], draw)
            return
        if tag in {"polyline", "polygon", "line"}:
            if tag == "line":
                points = [point(primitive["x1"], primitive["y1"]), point(primitive["x2"], primitive["y2"])]
            else:
                points = [point(px, py) for px, py in primitive["points"]]
            if filled and len(points) >= 3:
                draw.polygon(points, fill=255)
            if stroke > 0:
                draw.line(points + points[:1] if tag == "polygon" else points, fill=255, width=pixels)
                for vertex in points:  # round joins and caps
                    dot(vertex, stroke / 2)
            return
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
            shape(box, fill=255)
        if stroke > 0:
            half = stroke / 2
            shape((box[0] - half, box[1] - half, box[2] + half, box[3] + half), outline=255, width=pixels)

    def rgb(color: str) -> tuple[int, int, int]:
        return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))

    layered = icon.multicolor or any(
        name in primitive for primitive in icon.primitives for name in ("opacity", "fill-opacity", "stroke-opacity"))
    if not layered:
        mask = Image.new("L", (canvas, canvas), 0)
        for primitive in icon.primitives:
            coverage(primitive, mask)
        image = Image.new("RGBA", (size, size), (*rgb(icon.color), 255))
        image.putalpha(mask.resize((size, size), Image.LANCZOS))
    else:
        image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
        for primitive in icon.primitives:
            opacity = primitive.get("opacity", 1.0)
            parts = []
            if primitive.get("fill", "none") != "none":
                parts.append(({**primitive, "stroke": "none"}, primitive["fill"],
                              opacity * primitive.get("fill-opacity", 1.0)))
            if primitive.get("stroke") != "none" and primitive["tag"] != "path":
                stroke_color = primitive.get("stroke")
                stroke_color = stroke_color if isinstance(stroke_color, str) and stroke_color.startswith("#") \
                    else icon.color
                parts.append(({**primitive, "fill": "none"}, stroke_color,
                              opacity * primitive.get("stroke-opacity", 1.0)))
            for part, color, alpha in parts:
                mask = Image.new("L", (canvas, canvas), 0)
                coverage(part, mask)
                layer = Image.new("RGBA", (canvas, canvas), (*rgb(color), 0))
                layer.putalpha(mask.point(lambda value, alpha=alpha: round(value * alpha)))
                image = Image.alpha_composite(image, layer)
        image = image.resize((size, size), Image.LANCZOS)
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


def vector_icon_id_prefix(style_snapshot: dict | None) -> str:
    """ID prefix of the icon groups a page's placeholders become.

    A template-redesign page may only carry ``body-`` IDs inside its body
    (``svg_templates.frame_composition.compose_frame_svg``), so its icon groups
    are named ``body-asset-...``; every other deck keeps ``asset-...``, which
    its stored responses replay to byte for byte.
    """

    from services.svg_templates.generation import template_selection

    selection = template_selection(style_snapshot or {})
    return "body-asset-" if selection and selection.get("mode") == "template-redesign" else "asset-"


def replace_vector_icon_images(root: ET.Element, vector_icons: dict[str, FlattenedIcon], *,
                               id_prefix: str = "asset-") -> set[str]:
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
        base = _sanitize_id(f"{id_prefix}{slot}")
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
    """Record offered/used vector slots; an offered icon left unused fails quality.

    Slots listed as ``optional`` (model-drawn pictures) may be left out: they
    are reported under ``omittedOptional`` and never fail the page.
    """
    offer = (payload or {}).get("assetOffer")
    vector = offer.get("vectorIcons") if isinstance(offer, dict) else None
    if not isinstance(vector, dict):
        return
    offered = {str(slot) for slot in vector.get("offered") or []}
    if not offered:
        return
    used = {str(slot) for slot in vector.get("used") or []}
    optional = {str(slot) for slot in vector.get("optional") or []}
    omitted = sorted(offered - used - optional)
    quality["assetOffer"] = {"offered": sorted(offered), "used": sorted(used), "omitted": omitted,
                             **({"omittedOptional": sorted((offered & optional) - used)} if optional else {})}
    if omitted:
        quality["passed"] = False
        quality.setdefault("failures", []).append({"kind": "asset_icon_omitted", "slotIds": omitted})
