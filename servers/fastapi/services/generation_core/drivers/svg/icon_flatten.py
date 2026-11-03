"""Flatten monochrome library SVG icons into svg-static/v1 contract primitives.

The icon library (``assets/infographics/monochrome-v1``) is Lucide-derived: it
uses curves, arcs, rounded rects and ``scale`` transforms, none of which the
conservative slide contract (``services/svg_contract.py``) admits. The walk
below bakes every transform into coordinates and samples every curve into
polylines/polygons within ``tolerance`` (``icon_geometry.py``), so an icon can
be embedded as plain editable vector shapes. Output is in viewBox units.
"""
from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from services.generation_core.drivers.svg.icon_geometry import (
    apply, ellipse_points, is_axis_aligned, is_similarity, mul, numbers,
    parse_transform, path_subpaths, rounded_rect_points,
)

# Chord error bound in viewBox units: 0.25 of the library's 128-unit box is
# about 0.5 px when an icon is drawn 256 px wide, invisible at slide sizes.
DEFAULT_TOLERANCE = 0.25
# Measured over the 300 library icons at DEFAULT_TOLERANCE (2026-09-26,
# tests/unit/test_svg_embed_assets.py): min 4, median 43, p90 77, p99 120,
# max 151 points. 192 admits every library icon with ~25% headroom while
# capping one icon at under 5% of the contract's 4096-element slide limit;
# anything larger (foreign or future icons) takes the raster fallback.
ICON_POINT_BUDGET = 192
_MAX_PRIMITIVE_POINTS = 1000  # the contract admits at most 1024 points per element
_SHAPES = {"path", "circle", "ellipse", "rect", "line", "polyline", "polygon"}
_IGNORED = {"title", "desc", "metadata"}
_FORBIDDEN_ATTRIBUTES = {"style", "class", "clip-path", "mask", "filter", "href",
                         "marker-start", "marker-mid", "marker-end"}


class IconFlattenBudgetExceeded(ValueError):
    """The flattened icon needs more points than the allowed budget."""


@dataclass(frozen=True)
class FlattenedIcon:
    view_width: float
    view_height: float
    stroke_width: float
    color: str
    primitives: tuple[dict, ...]
    point_count: int
    # The source root's cap/join, kept when the contract admits them (round);
    # anything else falls back to the SVG defaults the contract always had.
    line_cap: str = "butt"
    line_join: str = "miter"
    # Multi-colour drawings carry each shape's own paint; monochrome icons
    # inherit the single ``color`` from the placed group.
    multicolor: bool = False
    snapped_paints: int = field(default=0, compare=False)

    def to_json(self) -> dict:
        value = {"view_width": self.view_width, "view_height": self.view_height,
                 "stroke_width": self.stroke_width, "color": self.color,
                 "primitives": [dict(item) for item in self.primitives], "point_count": self.point_count}
        if self.line_cap != "butt":
            value["line_cap"] = self.line_cap
        if self.line_join != "miter":
            value["line_join"] = self.line_join
        if self.multicolor:
            value["multicolor"] = True
        return value

    @classmethod
    def from_json(cls, value: dict) -> FlattenedIcon:
        primitives = []
        for item in value["primitives"]:
            item = dict(item)
            if "points" in item:
                item["points"] = [[float(x), float(y)] for x, y in item["points"]]
            if "subpaths" in item:
                item["subpaths"] = [[[float(x), float(y)] for x, y in ring] for ring in item["subpaths"]]
            primitives.append(item)
        return cls(float(value["view_width"]), float(value["view_height"]), float(value["stroke_width"]),
                   normalize_color(value["color"]), tuple(primitives), int(value["point_count"]),
                   line_cap=_line_value(value.get("line_cap"), "butt"),
                   line_join=_line_value(value.get("line_join"), "miter"),
                   multicolor=value.get("multicolor") is True)


def _line_value(value, default: str) -> str:
    """``round`` when the source asked for it, else the SVG default (butt / miter)."""
    return "round" if isinstance(value, str) and value.strip() == "round" else default


def normalize_color(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?", value):
        raise ValueError("icon_color_invalid")
    if len(value) == 4:
        value = "#" + "".join(ch * 2 for ch in value[1:])
    return value.upper()


def primitive_points(primitive: dict) -> int:
    if "points" in primitive:
        return len(primitive["points"])
    if "subpaths" in primitive:
        return sum(len(subpath) for subpath in primitive["subpaths"])
    return 2 if primitive["tag"] == "line" else 4


def _r(value: float) -> float:
    return round(value, 4) + 0.0


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _paint(value: str, ink: str, palette: "_Palette | None" = None) -> str | None:
    value = value.strip()
    if value in {"none", "transparent"}:
        return None
    if palette is not None:
        # Multi-colour: currentColor is the ink, a hex colour keeps its palette
        # colour (the nearest one when it is off the palette); nothing else.
        if value == "currentColor":
            return ink
        if re.fullmatch(r"#[0-9a-fA-F]{3}|#[0-9a-fA-F]{6}", value):
            return palette.snap(normalize_color(value))
        raise ValueError("icon_paint_unsupported")
    if re.fullmatch(r"#[0-9a-fA-F]{3,8}|currentColor|[a-zA-Z]+|rgba?\([0-9.,%\s]*\)", value):
        return ink  # monochrome: every solid paint becomes the ink colour
    raise ValueError("icon_paint_unsupported")


class _Palette:
    """The colours a multi-colour drawing may use, and how many paints were moved onto them."""

    def __init__(self, colors):
        self.colors = tuple(dict.fromkeys(normalize_color(color) for color in colors))
        if not self.colors:
            raise ValueError("icon_palette_empty")
        self.snapped = 0

    @staticmethod
    def _rgb(color: str) -> tuple[int, int, int]:
        return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))

    def snap(self, color: str) -> str:
        if color in self.colors:
            return color
        self.snapped += 1
        rgb = self._rgb(color)
        return min(self.colors, key=lambda item: math.dist(self._rgb(item), rgb))


def _scalar(node, name: str, default: float | None = 0.0) -> float | None:
    raw = node.get(name)
    if raw is None:
        return default
    values = numbers(raw)
    if len(values) != 1:
        raise ValueError("icon_attribute_invalid:" + name)
    return values[0]


_OPACITY = ("opacity", "fill-opacity", "stroke-opacity")


def _style(node, inherited: dict, ink: str, palette: "_Palette | None" = None) -> dict:
    for key in node.attrib:
        name = _local(key)
        if name in _FORBIDDEN_ATTRIBUTES:
            raise ValueError("icon_attribute_unsupported:" + name)
    style = dict(inherited)
    for name in _OPACITY:
        if node.get(name) is not None:
            value = _scalar(node, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError("icon_opacity_invalid")
            # Group opacity composites; multiplying it into each shape is the
            # flattened approximation, exact for shapes that do not overlap.
            style[name] = style.get(name, 1.0) * value
    if node.get("fill") is not None:
        style["fill"] = _paint(node.get("fill"), ink, palette)
    if node.get("stroke") is not None:
        style["stroke"] = _paint(node.get("stroke"), ink, palette)
    if node.get("stroke-width") is not None:
        style["width"] = max(0.0, _scalar(node, "stroke-width"))
    return style


#: A compound path (one filled shape with holes) stays one element only up to
#: this many points; beyond it the contract's per-element limits come close.
_MAX_COMPOUND_POINTS = 1000


class _Walker:
    def __init__(self, ink: str, tolerance: float, palette: "_Palette | None" = None):
        self.ink, self.tolerance, self.items, self.palette = ink, tolerance, [], palette
        self._current: dict = {}

    def visit(self, node, matrix, style, *, root: bool = False) -> None:
        name = _local(node.tag)
        if name in _IGNORED or node.get("display") == "none":
            return
        if name not in _SHAPES and not (name == "g" or (name == "svg" and root)):
            raise ValueError("icon_element_unsupported:" + name[:40])
        style = _style(node, style, self.ink, self.palette)
        if node.get("transform"):
            matrix = mul(matrix, parse_transform(node.get("transform")))
        if name in {"svg", "g"}:
            for child in node:
                self.visit(child, matrix, style)
            return
        if len(node):
            raise ValueError("icon_shape_children_unsupported")
        width = style["width"] * math.sqrt(abs(matrix[0] * matrix[3] - matrix[1] * matrix[2]))
        stroke = width if style["stroke"] and width > 0 else None
        if style["fill"] is None and stroke is None:
            return
        self._current = style
        getattr(self, "_" + name)(node, matrix, style["fill"], stroke)

    def _emit(self, tag: str, fill, stroke, **attrs) -> None:
        primitive = {"tag": tag, **attrs, "fill": fill or "none"}
        if stroke is None:
            primitive["stroke"] = "none"
        elif self.palette is not None:
            primitive["stroke"] = self._current.get("stroke") or self.ink
        for name in _OPACITY:
            value = self._current.get(name, 1.0)
            if value < 1.0 - 1e-6:
                primitive[name] = round(value, 4)
        self.items.append((primitive, stroke))

    def _points(self, points, closed: bool, fill, stroke) -> None:
        clean = []
        for point in points:
            if not clean or math.dist(clean[-1], point) > 1e-6:
                clean.append(point)
        if closed and len(clean) > 1 and math.dist(clean[0], clean[-1]) <= 1e-6:
            clean.pop()
        length = sum(math.dist(p, q) for p, q in zip(clean, clean[1:], strict=False))
        if len(clean) < 2 or (stroke and not (closed and fill) and length < stroke / 2):
            if stroke:  # a dot: a filled circle draws it whatever the cap
                cx = sum(p[0] for p in clean) / len(clean)
                cy = sum(p[1] for p in clean) / len(clean)
                ink = (self._current.get("stroke") if self.palette is not None else None) or self.ink
                self._emit("circle", ink, None, cx=_r(cx), cy=_r(cy), r=_r(stroke / 2))
            return
        if closed and fill and len(clean) >= 3:
            tag = "polygon"
            if len(clean) > _MAX_PRIMITIVE_POINTS:
                clean = clean[::math.ceil(len(clean) / _MAX_PRIMITIVE_POINTS)]
        else:
            tag = "polyline"
            if closed:
                clean.append(clean[0])
        step = _MAX_PRIMITIVE_POINTS - 1
        for start in range(0, max(1, len(clean) - 1), step):
            chunk = clean[start:start + _MAX_PRIMITIVE_POINTS] if tag == "polyline" else clean
            self._emit(tag, fill, stroke, points=[[_r(x), _r(y)] for x, y in chunk])
            if tag == "polygon":
                break

    def _path(self, node, matrix, fill, stroke) -> None:
        subpaths = path_subpaths(node.get("d", ""), matrix, self.tolerance)
        if fill and stroke is None and len(subpaths) > 1 and self._compound(subpaths, fill):
            return
        for points, closed in subpaths:
            self._points(points, closed, fill, stroke)

    def _compound(self, subpaths, fill) -> bool:
        """A filled path of several subpaths as ONE element, so its holes stay holes.

        Drawn separately, each subpath would be filled on its own and a hole
        (the check cut out of a solid shield) would be painted over.
        """
        rings = []
        for points, _closed in subpaths:
            ring = []
            for point in points:
                if not ring or math.dist(ring[-1], point) > 1e-6:
                    ring.append(point)
            if len(ring) > 1 and math.dist(ring[0], ring[-1]) <= 1e-6:
                ring.pop()
            if len(ring) >= 3:
                rings.append([[_r(x), _r(y)] for x, y in ring])
        if len(rings) < 2 or sum(len(ring) for ring in rings) > _MAX_COMPOUND_POINTS:
            return False
        self._emit("path", fill, None, subpaths=rings)
        return True

    def _line(self, node, matrix, fill, stroke) -> None:
        if stroke is None:
            return
        (x1, y1), (x2, y2) = (apply(matrix, _scalar(node, "x1"), _scalar(node, "y1")),
                              apply(matrix, _scalar(node, "x2"), _scalar(node, "y2")))
        if math.hypot(x2 - x1, y2 - y1) < stroke / 2:
            self._points([(x1, y1), (x2, y2)], False, None, stroke)
            return
        self._emit("line", None, stroke, x1=_r(x1), y1=_r(y1), x2=_r(x2), y2=_r(y2))

    def _polyline(self, node, matrix, fill, stroke, closed: bool = False) -> None:
        values = numbers(node.get("points", ""))
        if len(values) % 2:
            raise ValueError("icon_points_invalid")
        points = [apply(matrix, values[i], values[i + 1]) for i in range(0, len(values), 2)]
        self._points(points, closed, fill, stroke)

    def _polygon(self, node, matrix, fill, stroke) -> None:
        self._polyline(node, matrix, fill, stroke, closed=True)

    def _circle(self, node, matrix, fill, stroke) -> None:
        r = _scalar(node, "r")
        if r > 0:
            self._oval(_scalar(node, "cx"), _scalar(node, "cy"), r, r, matrix, fill, stroke)

    def _ellipse(self, node, matrix, fill, stroke) -> None:
        rx, ry = _scalar(node, "rx", None), _scalar(node, "ry", None)
        rx, ry = (rx if rx is not None else ry), (ry if ry is not None else rx)
        if rx and ry and rx > 0 and ry > 0:
            self._oval(_scalar(node, "cx"), _scalar(node, "cy"), rx, ry, matrix, fill, stroke)

    def _oval(self, cx, cy, rx, ry, matrix, fill, stroke) -> None:
        x, y = apply(matrix, cx, cy)
        scale = math.hypot(matrix[0], matrix[1])
        if abs(rx - ry) < 1e-9 and is_similarity(matrix):
            self._emit("circle", fill, stroke, cx=_r(x), cy=_r(y), r=_r(rx * scale))
        elif is_axis_aligned(matrix):
            self._emit("ellipse", fill, stroke, cx=_r(x), cy=_r(y),
                       rx=_r(rx * abs(matrix[0])), ry=_r(ry * abs(matrix[3])))
        else:
            self._points(ellipse_points(cx, cy, rx, ry, matrix, self.tolerance), True, fill, stroke)

    def _rect(self, node, matrix, fill, stroke) -> None:
        x, y, w, h = (_scalar(node, key) for key in ("x", "y", "width", "height"))
        if w <= 0 or h <= 0:
            return
        rx, ry = _scalar(node, "rx", None), _scalar(node, "ry", None)
        rx, ry = (rx if rx is not None else ry) or 0.0, (ry if ry is not None else rx) or 0.0
        rx, ry = min(max(rx, 0.0), w / 2), min(max(ry, 0.0), h / 2)
        if rx > 0 and ry > 0:
            self._points(rounded_rect_points(x, y, w, h, rx, ry, matrix, self.tolerance), True, fill, stroke)
        elif is_axis_aligned(matrix):
            (ax, ay), (bx, by) = apply(matrix, x, y), apply(matrix, x + w, y + h)
            self._emit("rect", fill, stroke, x=_r(min(ax, bx)), y=_r(min(ay, by)),
                       width=_r(abs(bx - ax)), height=_r(abs(by - ay)))
        else:
            corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
            self._points([apply(matrix, px, py) for px, py in corners], True, fill, stroke)


def flatten_icon_svg(svg_text: str, *, color: str, point_budget: int | None = ICON_POINT_BUDGET,
                     tolerance: float = DEFAULT_TOLERANCE, palette=None) -> FlattenedIcon:
    """Bake and sample an SVG icon or drawing into contract-safe primitives.

    Monochrome by default: every paint becomes ``color``. With ``palette`` the
    drawing keeps each shape's own colour, moved onto the nearest palette
    colour when it is off the palette (``FlattenedIcon.snapped_paints``).
    Opacity is kept in both modes.
    """
    ink = normalize_color(color)
    palette = _Palette(palette) if palette is not None else None
    if not isinstance(svg_text, str) or re.search(r"<!|<\?xml-stylesheet", svg_text):
        raise ValueError("icon_declaration_forbidden")
    if not tolerance > 0:
        raise ValueError("icon_tolerance_invalid")
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as error:
        raise ValueError("icon_invalid_xml") from error
    if _local(root.tag) != "svg":
        raise ValueError("icon_root_not_svg")
    if root.get("viewBox"):
        view = numbers(root.get("viewBox"))
    else:
        view = [0.0, 0.0, _scalar(root, "width", 0.0), _scalar(root, "height", 0.0)]
    if len(view) != 4 or view[2] <= 0 or view[3] <= 0:
        raise ValueError("icon_viewbox_invalid")
    walker = _Walker(ink, tolerance, palette)
    origin = (1.0, 0.0, 0.0, 1.0, -view[0], -view[1])
    walker.visit(root, origin, {"fill": ink, "stroke": None, "width": 1.0}, root=True)
    widths = [stroke for _, stroke in walker.items if stroke is not None]
    icon_width = widths[0] if widths else 0.0
    primitives = []
    for primitive, stroke in walker.items:
        if stroke is not None and abs(stroke - icon_width) > 1e-6:
            primitive["stroke-width"] = _r(stroke)
        primitives.append(primitive)
    count = sum(primitive_points(item) for item in primitives)
    if point_budget is not None and count > point_budget:
        raise IconFlattenBudgetExceeded(f"icon_point_budget_exceeded:{count}>{point_budget}")
    if not primitives:
        raise ValueError("icon_empty")
    return FlattenedIcon(float(view[2]), float(view[3]), _r(icon_width), ink, tuple(primitives), count,
                         line_cap=_line_value(root.get("stroke-linecap"), "butt"),
                         line_join=_line_value(root.get("stroke-linejoin"), "miter"),
                         multicolor=palette is not None,
                         snapped_paints=palette.snapped if palette is not None else 0)
