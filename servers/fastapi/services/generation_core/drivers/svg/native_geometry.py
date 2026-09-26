"""Absolute-coordinate primitives of one declared native group in a final SVG.

Every coordinate returned here is in canvas px: each ancestor's and the
element's own ``translate()`` is applied (the SVG contract admits no other
transform). ``fill``/``stroke``/``font-weight``/``text-anchor`` inherit through
``<g>`` ancestors as in SVG.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from xml.etree import ElementTree as ET

from services.generation_core.drivers.svg.fallback import _text_em

SVG_NS = "{http://www.w3.org/2000/svg}"
_INHERITED = ("fill", "stroke", "font-weight", "text-anchor")
_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
_NAMED = {"black": "#000000", "white": "#FFFFFF"}


class NativeRejection(ValueError):
    """One declaration is not honoured; ``str(error)`` is the reason code."""


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def contains(self, x: float, y: float, tolerance: float = 1.0) -> bool:
        return (self.x0 - tolerance <= x <= self.x1 + tolerance
                and self.y0 - tolerance <= y <= self.y1 + tolerance)

    def union(self, other: "Box") -> "Box":
        return Box(min(self.x0, other.x0), min(self.y0, other.y0),
                   max(self.x1, other.x1), max(self.y1, other.y1))

    def anchor(self) -> dict:
        return {"x": self.x0, "y": self.y0, "width": self.width, "height": self.height}


@dataclass(frozen=True)
class Shape:
    tag: str
    box: Box
    fill: str | None
    stroke: str | None
    point_count: int = 0


@dataclass(frozen=True)
class DrawnText:
    lines: tuple[str, ...]
    points: tuple[tuple[float, float], ...]
    size: float
    family: str
    fill: str | None
    weight: str
    anchor: str
    uniform: bool  # no tspan overrides a font or paint attribute

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def box(self) -> Box:
        boxes = []
        for line, (x, y) in zip(self.lines, self.points, strict=True):
            width = _text_em(line) * self.size
            left = x - {"middle": width / 2, "end": width}.get(self.anchor, 0.0)
            boxes.append(Box(left, y - self.size, left + width, y + 0.25 * self.size))
        result = boxes[0]
        for item in boxes[1:]:
            result = result.union(item)
        return result


@dataclass(frozen=True)
class GroupPrimitives:
    shapes: tuple[Shape, ...]
    texts: tuple[DrawnText, ...]
    tags: frozenset[str]


def normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def normalize_cell(value: str) -> str:
    """A cell's text: each line whitespace-normalized, empty lines dropped."""
    return "\n".join(line for line in (normalize_text(p) for p in value.split("\n")) if line)


def paint(value: str | None) -> str | None:
    if value is None or value == "none":
        return None
    if value in _NAMED:
        return _NAMED[value]
    if re.fullmatch(r"#[0-9a-fA-F]{3}", value):
        return "#" + "".join(ch * 2 for ch in value[1:]).upper()
    return value.upper() if re.fullmatch(r"#[0-9a-fA-F]{6}", value) else None


def is_bold(weight: str) -> bool:
    return weight == "bold" or (weight.isdigit() and int(weight) >= 600)


def _num(node: ET.Element, key: str, default: float = 0.0) -> float:
    value = node.get(key)
    return float(value) if value is not None else default


def _translate(node: ET.Element) -> tuple[float, float]:
    value = node.get("transform")
    if value is None:
        return 0.0, 0.0
    match = re.fullmatch(r"\s*translate\(([^()]*)\)\s*", value)
    numbers = [float(n) for n in _NUMBER.findall(match[1])] if match else []
    if len(numbers) not in (1, 2):
        raise NativeRejection("native_group_transform_unsupported")
    return numbers[0], numbers[1] if len(numbers) == 2 else 0.0


def _tag(node: ET.Element) -> str:
    return node.tag.removeprefix(SVG_NS)


def find_group(root: ET.Element, group_id: str):
    """``(group, (dx, dy), inherited)`` of the ``<g>`` with ``group_id``, or ``None``."""
    stack = [(root, (0.0, 0.0), {})]
    while stack:
        node, (dx, dy), inherited = stack.pop()
        tx, ty = _translate(node)
        offset = (dx + tx, dy + ty)
        attrs = {**inherited, **{k: node.get(k) for k in _INHERITED if node.get(k) is not None}}
        if node.get("id") == group_id:
            return (node, offset, attrs) if _tag(node) == "g" else None
        stack.extend((child, offset, attrs) for child in node)
    return None


def _path_points(d: str) -> list[tuple[float, float]]:
    points, x, y, start = [], 0.0, 0.0, (0.0, 0.0)
    for command, raw in re.findall(r"([MLHVZmlhvz])([^MLHVZmlhvz]*)", d):
        values = [float(n) for n in _NUMBER.findall(raw)]
        upper, relative = command.upper(), command.islower()
        if upper == "Z":
            x, y = start
            continue
        step = 1 if upper in "HV" else 2
        for i in range(0, len(values), step):
            if upper == "H":
                x = x + values[i] if relative else values[i]
            elif upper == "V":
                y = y + values[i] if relative else values[i]
            else:
                x, y = (x + values[i], y + values[i + 1]) if relative else (values[i], values[i + 1])
            if upper == "M" and i == 0:
                start = (x, y)
            points.append((x, y))
    return points


def _shape(node: ET.Element, tag: str, dx: float, dy: float, attrs: dict) -> Shape:
    if tag == "rect":
        x, y = _num(node, "x") + dx, _num(node, "y") + dy
        box = Box(x, y, x + _num(node, "width"), y + _num(node, "height"))
        return Shape(tag, box, paint(attrs.get("fill", "black")), paint(attrs.get("stroke")))
    if tag == "line":
        points = [(_num(node, "x1"), _num(node, "y1")), (_num(node, "x2"), _num(node, "y2"))]
    elif tag in ("circle", "ellipse"):
        cx, cy = _num(node, "cx"), _num(node, "cy")
        rx, ry = (_num(node, "r"),) * 2 if tag == "circle" else (_num(node, "rx"), _num(node, "ry"))
        points = [(cx - rx, cy - ry), (cx + rx, cy + ry)]
    elif tag == "path":
        points = _path_points(node.get("d", ""))
    else:
        values = [float(n) for n in _NUMBER.findall(node.get("points", ""))]
        points = list(zip(values[0::2], values[1::2], strict=False))
    if not points:
        raise NativeRejection("native_group_shape_empty")
    xs, ys = [p[0] + dx for p in points], [p[1] + dy for p in points]
    return Shape(tag, Box(min(xs), min(ys), max(xs), max(ys)), paint(attrs.get("fill", "black")),
                 paint(attrs.get("stroke")), len(points))


def _text(node: ET.Element, dx: float, dy: float, attrs: dict) -> DrawnText:
    x, y = _num(node, "x"), _num(node, "y")
    lines: list[list] = [[node.text or "", (x + dx, y + dy)]]
    uniform = True
    for tspan in node:
        if tspan.get("transform") is not None:
            raise NativeRejection("native_group_transform_unsupported")
        for key in ("font-size", "font-family", "fill", "font-weight"):
            if tspan.get(key) is not None and tspan.get(key) != (node.get(key) or attrs.get(key)):
                uniform = False
        text = "".join(tspan.itertext())
        line_y = tspan.get("y")
        if line_y is not None and float(line_y) + dy != lines[-1][1][1]:
            line_x = float(tspan.get("x")) if tspan.get("x") is not None else x
            lines.append([text, (line_x + dx, float(line_y) + dy)])
        else:
            lines[-1][0] += text
        lines[-1][0] += tspan.tail or ""
    kept = [(normalize_text(text), point) for text, point in lines if normalize_text(text)]
    return DrawnText(lines=tuple(t for t, _ in kept), points=tuple(p for _, p in kept),
                     size=_num(node, "font-size", 16.0), family=node.get("font-family", ""),
                     fill=paint(node.get("fill", attrs.get("fill"))),
                     weight=node.get("font-weight", attrs.get("font-weight", "normal")),
                     anchor=node.get("text-anchor", attrs.get("text-anchor", "start")), uniform=uniform)


def collect_primitives(group: ET.Element, offset: tuple[float, float], inherited: dict,
                       allowed: frozenset[str]) -> GroupPrimitives:
    """Flatten ``group`` into shapes and texts; raises on an element outside ``allowed``."""
    shapes: list[Shape] = []
    texts: list[DrawnText] = []
    tags: set[str] = set()
    stack = [(child, offset, inherited) for child in reversed(list(group))]
    while stack:
        node, (dx, dy), attrs = stack.pop()
        tag = _tag(node)
        if tag not in allowed:
            raise NativeRejection("native_group_element_unsupported")
        tags.add(tag)
        tx, ty = _translate(node)
        position = (dx + tx, dy + ty)
        attrs = {**attrs, **{k: node.get(k) for k in _INHERITED if node.get(k) is not None}}
        if tag == "g":
            stack.extend((child, position, attrs) for child in reversed(list(node)))
        elif tag == "text":
            drawn = _text(node, *position, attrs)
            if drawn.lines:
                texts.append(drawn)
        else:
            shapes.append(_shape(node, tag, *position, attrs))
    return GroupPrimitives(tuple(shapes), tuple(texts), frozenset(tags))


def parse_label_number(value: str) -> float | None:
    """The number a drawn value label shows, or ``None`` if it is not one.

    Accepts an optional sign and currency symbol, ``,`` thousands separators, a
    decimal point and a short unit suffix (``%``, ``억원``, ``pt``...). The suffix
    is not interpreted: ``12%`` shows 12 and ``1.2M`` shows 1.2.
    """
    match = re.fullmatch(r"([-+−]?)[₩$€£¥]?\s*(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*([%A-Za-z가-힣]{0,4})",
                         normalize_text(value))
    if not match:
        return None
    number = float(match[2].replace(",", "") + (match[3] or ""))
    return -number if match[1] in ("-", "−") else number
