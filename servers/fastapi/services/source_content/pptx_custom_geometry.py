"""Bounded numeric DrawingML custom paths, with deterministic SVG flattening."""

from __future__ import annotations

import math

_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_ARITY = {"moveTo": 1, "lnTo": 1, "quadBezTo": 2, "cubicBezTo": 3, "close": 0}
_LIMIT = 2048


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("svg_source_custom_geometry_formula_unsupported") from error
    if not math.isfinite(number) or abs(number) > 1e12:
        raise ValueError("svg_source_custom_geometry_number_invalid")
    return number


def extract_custom_geometry(element):
    """Return an explicit unsupported marker instead of losing source geometry.

    Other pipelines can continue reading the manifest. SVG admission rejects
    an unsupported marker before purchasing a model attempt.
    """
    custom = element.find(f"{{http://schemas.openxmlformats.org/presentationml/2006/main}}spPr/{{{_NS}}}custGeom")
    if custom is None:
        return None
    try:
        if custom.findall(f"{{{_NS}}}gdLst/{{{_NS}}}gd") or custom.findall(f"{{{_NS}}}avLst/{{{_NS}}}gd"):
            raise ValueError("svg_source_custom_geometry_formula_unsupported")
        paths = custom.findall(f"{{{_NS}}}pathLst/{{{_NS}}}path")
        if len(paths) != 1:
            raise ValueError("svg_source_custom_geometry_path_count_unsupported")
        path = paths[0]
        fill = path.get("fill", "norm")
        if fill not in {"norm", "none"}:
            raise ValueError("svg_source_custom_geometry_fill_unsupported")
        result = dict(
            version="numeric-custom-path/v1",
            width=_number(path.get("w")),
            height=_number(path.get("h")),
            fill=fill,
            stroke=path.get("stroke", "1") not in {"0", "false"},
            commands=[],
        )
        for node in path:
            command = node.tag.removeprefix(f"{{{_NS}}}")
            if command not in _ARITY or len(node) != _ARITY[command]:
                raise ValueError("svg_source_custom_geometry_command_unsupported")
            points = []
            for point in node:
                if point.tag != f"{{{_NS}}}pt":
                    raise ValueError("svg_source_custom_geometry_command_unsupported")
                points.append([_number(point.get("x")), _number(point.get("y"))])
            result["commands"].append(dict(command=command, points=points))
        validate_custom_geometry(result)
        return result
    except ValueError as error:
        return dict(version="numeric-custom-path/v1", unsupported=str(error))


def validate_custom_geometry(value):
    if not isinstance(value, dict) or value.get("version") != "numeric-custom-path/v1":
        raise ValueError("svg_source_custom_geometry_missing")
    if value.get("unsupported"):
        raise ValueError(str(value["unsupported"]))
    if min(_number(value.get("width")), _number(value.get("height"))) <= 0:
        raise ValueError("svg_source_custom_geometry_size_invalid")
    commands = value.get("commands")
    if not isinstance(commands, list) or not 1 <= len(commands) <= _LIMIT:
        raise ValueError("svg_source_custom_geometry_complexity_limit")
    if (
        commands[0].get("command") != "moveTo"
        or value.get("fill") not in {"norm", "none"}
        or type(value.get("stroke")) is not bool
    ):
        raise ValueError("svg_source_custom_geometry_invalid")
    for entry in commands:
        command, points = entry.get("command"), entry.get("points")
        if command not in _ARITY or not isinstance(points, list) or len(points) != _ARITY[command]:
            raise ValueError("svg_source_custom_geometry_command_unsupported")
        for point in points:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError("svg_source_custom_geometry_invalid")
            for coordinate in point:
                _number(coordinate)
    return value


def flatten_custom_geometry(value, *, x, y, width, height, tolerance_px=0.25):
    """Flatten Bézier segments in target CSS pixels, keeping subpath order.

    De Casteljau subdivision stops only when every control point is within the
    tolerance of the chord segment, so loops and overshoot are not lost.
    """
    validate_custom_geometry(value)
    if not 0 < tolerance_px <= 0.25:
        raise ValueError("svg_source_custom_geometry_tolerance_invalid")
    output, point_count = [], 0
    current = origin = None

    def emit(command, point=None):
        nonlocal point_count
        point_count += int(point is not None)
        if point_count > 2048:
            raise ValueError("svg_source_custom_geometry_complexity_limit")
        output.append(
            command if point is None else command + " ".join(f"{v:.4f}".rstrip("0").rstrip(".") for v in point)
        )

    def distance(point, a, b):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = dx * dx + dy * dy
        fraction = max(0, min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length)) if length else 0
        return math.hypot(point[0] - a[0] - fraction * dx, point[1] - a[1] - fraction * dy)

    def curve(points, depth=0):
        if max(distance(p, points[0], points[-1]) for p in points[1:-1]) <= tolerance_px:
            emit("L", points[-1])
            return
        if depth >= 16:
            raise ValueError("svg_source_custom_geometry_subdivision_limit")
        left, right, layer = [points[0]], [points[-1]], points
        while len(layer) > 1:
            layer = [((a[0] + b[0]) / 2, (a[1] + b[1]) / 2) for a, b in zip(layer, layer[1:], strict=False)]
            left.append(layer[0])
            right.append(layer[-1])
        curve(left, depth + 1)
        curve(list(reversed(right)), depth + 1)

    for entry in value["commands"]:
        command = entry["command"]
        points = [(x + px * width / value["width"], y + py * height / value["height"]) for px, py in entry["points"]]
        if command == "moveTo":
            current = origin = points[0]
            emit("M", current)
        elif command == "lnTo":
            current = points[0]
            emit("L", current)
        elif command == "close":
            emit("Z")
            current = origin
        else:
            curve([current, *points])
            current = points[-1]
    path = " ".join(output)
    if len(path) > 32000:
        raise ValueError("svg_source_custom_geometry_complexity_limit")
    return path
