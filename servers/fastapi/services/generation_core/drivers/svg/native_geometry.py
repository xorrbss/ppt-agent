"""Absolute-coordinate primitives of one declared native group in a final SVG.

Every coordinate returned here is in canvas px: each ancestor's and the
element's own ``translate()`` is applied (the SVG contract admits no other
transform). Paint properties (``fill``, ``stroke``, their opacities,
``stroke-width``) and ``font-weight``/``text-anchor`` inherit through ``<g>``
ancestors as in SVG; ``opacity`` multiplies down the ancestor chain.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import re
import math
import unicodedata
from xml.etree import ElementTree as ET

from services.generation_core.drivers.svg.fallback import _text_em

SVG_NS = "{http://www.w3.org/2000/svg}"
_INHERITED = ("fill", "stroke", "fill-opacity", "stroke-opacity", "stroke-width", "font-weight",
              "text-anchor", "font-size", "font-family")
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
    fill_alpha: float = 1.0
    stroke_alpha: float = 1.0
    stroke_width: float = 1.0
    area: float = 0.0
    centroid: tuple[float, float] | None = None
    vertices: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class DrawnTextRun:
    text: str
    size: float
    family: str
    fill: str | None
    weight: str


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
    run_lines: tuple[tuple[DrawnTextRun, ...], ...] = ()

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


def inherit(attrs: dict, node: ET.Element) -> dict:
    """``attrs`` as seen by ``node``'s children (``_opacity`` is the opacity product)."""
    merged = {**attrs, **{k: node.get(k) for k in _INHERITED if node.get(k) is not None}}
    merged["_opacity"] = attrs.get("_opacity", 1.0) * float(node.get("opacity", 1.0))
    # Only the table path reader consumes this flag; ordinary shapes/charts
    # retain their established paint interpretation.
    unsupported = {"style": {None}, "class": {None},
        "clip-path": {None, "none"}, "mask": {None, "none"}, "filter": {None, "none"},
        "marker-start": {None, "none"}, "marker-mid": {None, "none"}, "marker-end": {None, "none"},
        "stroke-dasharray": {None, "none"}, "stroke-dashoffset": {None, "0"},
        "stroke-linecap": {None, "butt"}, "stroke-linejoin": {None, "miter"},
        "stroke-miterlimit": {None, "4"}, "vector-effect": {None, "none"}}
    if (attrs.get("_table_path_unsupported_paint")
            or any(node.get(key) not in permitted for key, permitted in unsupported.items())):
        merged["_table_path_unsupported_paint"] = True
    return merged


def _compose_transform(parent, child):
    dx, dy, scale = parent
    tx, ty, factor = child
    result = (dx + scale*tx, dy + scale*ty, scale*factor)
    if not all(math.isfinite(value) for value in result) or result[2] <= 0:
        raise NativeRejection("native_group_transform_unsupported")
    return result


def _transform_of(node):
    value = node.get("transform")
    if not value:
        return (0.0, 0.0, 1.0)
    result, end = (0.0, 0.0, 1.0), 0
    for match in re.finditer(r"([A-Za-z]+)\s*\(([^()]*)\)", value):
        if value[end:match.start()].strip():
            raise NativeRejection("native_group_transform_unsupported")
        raw = match[2].strip()
        if raw.startswith(",") or raw.endswith(",") or re.search(r",\s*,", raw):
            raise NativeRejection("native_group_transform_unsupported")
        tokens = re.split(r"[\s,]+", raw) if raw else []
        if not tokens or any(not re.fullmatch(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", token) for token in tokens):
            raise NativeRejection("native_group_transform_unsupported")
        numbers = [float(token) for token in tokens]
        if not all(math.isfinite(number) for number in numbers):
            raise NativeRejection("native_group_transform_unsupported")
        name = match[1]
        if name == "translate" and len(numbers) in {1, 2}:
            child = (numbers[0], numbers[1] if len(numbers) == 2 else 0.0, 1.0)
        elif name == "scale" and len(numbers) in {1, 2} and (len(numbers) == 1 or numbers[0] == numbers[1]) and numbers[0] > 0:
            child = (0.0, 0.0, numbers[0])
        elif name == "matrix" and len(numbers) == 6 and numbers[1] == 0 and numbers[2] == 0 and numbers[0] == numbers[3] and numbers[0] > 0:
            child = (numbers[4], numbers[5], numbers[0])
        else:
            raise NativeRejection("native_group_transform_unsupported")
        result = _compose_transform(result, child)
        end = match.end()
    if not end or value[end:].strip():
        raise NativeRejection("native_group_transform_unsupported")
    return result


def tag_of(node: ET.Element) -> str:
    return node.tag.removeprefix(SVG_NS)


def find_group(root, group_id):
    stack = [(root, (0.0, 0.0, 1.0), {})]
    while stack:
        node, position, inherited = stack.pop()
        position = _compose_transform(position, _transform_of(node))
        attrs = inherit(inherited, node)
        if node.get("id") == group_id:
            offset = position[:2] if position[2] == 1.0 else position
            return (node, offset, attrs) if tag_of(node) == "g" else None
        stack.extend((child, position, attrs) for child in reversed(list(node)))
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


def shape_of(node: ET.Element, tag: str, dx: float, dy: float, attrs: dict) -> Shape:
    """One non-text element in canvas px; ``attrs`` already include the node's own."""
    if tag in ("rect", "image"):
        x, y = _num(node, "x") + dx, _num(node, "y") + dy
        points = [(x, y), (x + _num(node, "width"), y + _num(node, "height"))]
        dx = dy = 0.0
    elif tag == "line":
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
    vertices = [(x + dx, y + dy) for x, y in points]
    xs, ys = [p[0] for p in vertices], [p[1] for p in vertices]
    area, centroid = 0.0, None
    if tag in ("polygon", "path") and len(vertices) >= 3:
        edges = [(vertices[i], vertices[(i + 1) % len(vertices)]) for i in range(len(vertices))]
        signed = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in edges)
        if abs(signed) > 1e-6:
            area = abs(signed) / 2
            centroid = (sum((x0 + x1) * (x0 * y1 - x1 * y0) for (x0, y0), (x1, y1) in edges)
                        / (3 * signed),
                        sum((y0 + y1) * (x0 * y1 - x1 * y0) for (x0, y0), (x1, y1) in edges)
                        / (3 * signed))
    opacity = attrs.get("_opacity", 1.0)
    return Shape(tag, Box(min(xs), min(ys), max(xs), max(ys)),
                 None if tag in ("line", "image") else paint(attrs.get("fill", "black")),
                 paint(attrs.get("stroke")), 0 if tag in ("rect", "image") else len(points),
                 fill_alpha=float(attrs.get("fill-opacity", 1.0)) * opacity,
                 stroke_alpha=float(attrs.get("stroke-opacity", 1.0)) * opacity,
                 stroke_width=float(attrs.get("stroke-width", 1.0)), area=area, centroid=centroid,
                 vertices=tuple(vertices))


def _rich_text_runs(node: ET.Element, attrs: dict) -> list[DrawnTextRun]:
    style = inherit(attrs, node)
    def fragment(value):
        return DrawnTextRun(value, float(style.get("font-size", 16.0)),
                            style.get("font-family", ""), paint(style.get("fill")),
                            style.get("font-weight", "normal"))
    runs = [fragment(node.text)] if node.text else []
    for child in node:
        if child.get("transform") is not None:
            raise NativeRejection("native_group_transform_unsupported")
        runs.extend(_rich_text_runs(child, style))
        if child.tail:
            runs.append(fragment(child.tail))
    return runs


def _normalize_runs(runs: list[DrawnTextRun]) -> tuple[DrawnTextRun, ...]:
    # Normalize the same whitespace as the V1 cell, retaining each glyph's paint.
    result: list[DrawnTextRun] = []
    pending_space = None
    for run in runs:
        for character in unicodedata.normalize("NFC", run.text):
            if character.isspace():
                if result:
                    pending_space = replace(run, text=" ")
                continue
            if pending_space is not None:
                if result and replace(result[-1], text="") == replace(pending_space, text=""):
                    result[-1] = replace(result[-1], text=result[-1].text + " ")
                else:
                    result.append(pending_space)
                pending_space = None
            current = replace(run, text=character)
            if result and replace(result[-1], text="") == replace(current, text=""):
                result[-1] = replace(current, text=result[-1].text + character)
            else:
                result.append(current)
    return tuple(result)


def _text(node: ET.Element, dx: float, dy: float, attrs: dict) -> DrawnText:
    x, y = _num(node, "x"), _num(node, "y")
    base = DrawnTextRun("", float(attrs.get("font-size", 16.0)),
                        attrs.get("font-family", ""), paint(attrs.get("fill")),
                        attrs.get("font-weight", "normal"))
    lines: list[list] = [[[replace(base, text=node.text)] if node.text else [], (x + dx, y + dy)]]
    for tspan in node:
        if tspan.get("transform") is not None:
            raise NativeRejection("native_group_transform_unsupported")
        runs = _rich_text_runs(tspan, attrs)
        line_y = tspan.get("y")
        if line_y is not None and float(line_y) + dy != lines[-1][1][1]:
            line_x = float(tspan.get("x")) if tspan.get("x") is not None else x
            lines.append([runs, (line_x + dx, float(line_y) + dy)])
        else:
            lines[-1][0].extend(runs)
        if tspan.tail:
            lines[-1][0].append(replace(base, text=tspan.tail))
    kept = [(normalized, point) for runs, point in lines if (normalized := _normalize_runs(runs))]
    uniform = all(replace(run, text="") == base for runs, _ in kept for run in runs)
    return DrawnText(lines=tuple("".join(run.text for run in runs) for runs, _ in kept),
                     points=tuple(point for _, point in kept), size=base.size,
                     family=base.family, fill=base.fill, weight=base.weight,
                     anchor=attrs.get("text-anchor", "start"), uniform=uniform,
                     run_lines=tuple(runs for runs, _ in kept))


def transform_shape(shape, position):
    dx, dy, scale = position
    box = shape.box
    transformed = replace(shape, box=Box(dx+scale*box.x0, dy+scale*box.y0, dx+scale*box.x1, dy+scale*box.y1), stroke_width=shape.stroke_width*scale, area=shape.area*scale*scale, centroid=(dx+scale*shape.centroid[0], dy+scale*shape.centroid[1]) if shape.centroid is not None else None, vertices=tuple((dx+scale*x, dy+scale*y) for x, y in shape.vertices))
    values = (transformed.box.x0, transformed.box.y0, transformed.box.x1, transformed.box.y1, transformed.stroke_width, transformed.area, *(value for point in transformed.vertices for value in point), *(transformed.centroid or ()))
    if not all(math.isfinite(value) for value in values):
        raise NativeRejection("native_group_transform_unsupported")
    return transformed


def collect_primitives(group, offset, inherited, allowed):
    shapes, texts, tags = [], [], set()
    initial = (*offset, 1.0) if len(offset) == 2 else offset
    stack = [(child, initial, inherited) for child in reversed(list(group))]
    while stack:
        node, position, attrs = stack.pop()
        tag = tag_of(node)
        if tag not in allowed:
            raise NativeRejection("native_group_element_unsupported")
        tags.add(tag)
        position = _compose_transform(position, _transform_of(node))
        attrs = inherit(attrs, node)
        dx, dy, scale = position
        if scale != 1.0 and node.get("vector-effect", "none") != "none":
            raise NativeRejection("native_group_transform_unsupported")
        if tag == "g":
            stack.extend((child, position, attrs) for child in reversed(list(node)))
        elif tag == "text":
            drawn = _text(node, 0.0, 0.0, attrs)
            if drawn.lines:
                transformed = replace(drawn, points=tuple((dx + scale*x, dy + scale*y) for x, y in drawn.points), size=drawn.size*scale, run_lines=tuple(tuple(replace(run, size=run.size*scale) for run in runs) for runs in drawn.run_lines))
                if not all(math.isfinite(value) for value in (transformed.size, *(run.size for runs in transformed.run_lines for run in runs), *(value for point in transformed.points for value in point))):
                    raise NativeRejection("native_group_transform_unsupported")
                texts.append(transformed)
        else:
            if tag == "path":
                from services.generation_core.drivers.svg.native_table import TABLE_ELEMENTS, table_path_segments
                if allowed is TABLE_ELEMENTS:
                    for x1, y1, x2, y2 in table_path_segments(node, attrs):
                        line = ET.Element("line", {"x1": str(x1), "y1": str(y1),
                                                   "x2": str(x2), "y2": str(y2)})
                        shapes.append(transform_shape(shape_of(line, "line", 0.0, 0.0, attrs), position))
                    continue
            shapes.append(transform_shape(shape_of(node, tag, 0.0, 0.0, attrs), position))
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
