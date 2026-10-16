"""Paint bounds for the existing editable, conservative SVG capability."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from services.svg_contract import SVG_NS, _numbers


def intersects(a, b):
    return (a["x"] < b["x"] + b["width"] - .5 and b["x"] < a["x"] + a["width"] - .5
            and a["y"] < b["y"] + b["height"] - .5 and b["y"] < a["y"] + a["height"] - .5)


def contains(outer, inner):
    return (inner["x"] >= outer["x"] - .5 and inner["y"] >= outer["y"] - .5
            and inner["x"] + inner["width"] <= outer["x"] + outer["width"] + .5
            and inner["y"] + inner["height"] <= outer["y"] + outer["height"] + .5)


def paint_boxes(root: ET.Element, text_boxes: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Include accumulated translation and inherited stroke in every extent."""
    result = {}

    def visit(node, dx=0., dy=0., inherited=None):
        inherited = {**(inherited or {}), **node.attrib}
        transform = node.get("transform")
        if transform:
            match = re.fullmatch(r"translate\(([^()]+)\)", transform)
            if not match:
                raise ValueError("svg_common_header_transform_unsupported")
            values = _numbers(match[1])
            dx += values[0]
            dy += values[1] if len(values) == 2 else 0
        name = node.tag.removeprefix("{" + SVG_NS + "}")
        number = lambda key, default=0: float(node.get(key, default))
        boxes = []
        if name == "text":
            boxes = text_boxes[node.get("id")]
        elif name in {"rect", "image"}:
            boxes = [{"x": number("x"), "y": number("y"), "width": number("width"), "height": number("height")}]
        elif name in {"circle", "ellipse"}:
            rx, ry = (number("r"), number("r")) if name == "circle" else (number("rx"), number("ry"))
            boxes = [{"x": number("cx") - rx, "y": number("cy") - ry, "width": 2 * rx, "height": 2 * ry}]
        elif name == "line":
            xs, ys = [number("x1"), number("x2")], [number("y1"), number("y2")]
            boxes = [{"x": min(xs), "y": min(ys), "width": max(xs) - min(xs), "height": max(ys) - min(ys)}]
        elif name in {"polygon", "polyline", "path"}:
            points = []
            if name != "path":
                values = _numbers(node.get("points", ""))
                points = list(zip(values[::2], values[1::2]))
            else:
                x = y = 0.
                start = (x, y)
                for command, raw in re.findall(r"([MLHVZmlhvz])([^MLHVZmlhvz]*)", node.get("d", "")):
                    values = _numbers(raw.strip()) if raw.strip() else []
                    relative = command.islower()
                    cmd = command.upper()
                    if cmd == "Z":
                        x, y = start
                        points.append((x, y))
                    elif cmd in {"M", "L"}:
                        for offset in range(0, len(values), 2):
                            x, y = ((x + values[offset], y + values[offset + 1]) if relative
                                    else (values[offset], values[offset + 1]))
                            if cmd == "M" and offset == 0:
                                start = (x, y)
                            points.append((x, y))
                    else:
                        for value in values:
                            if cmd == "H":
                                x = x + value if relative else value
                            else:
                                y = y + value if relative else value
                            points.append((x, y))
            if points:
                xs, ys = zip(*points)
                boxes = [{"x": min(xs), "y": min(ys), "width": max(xs) - min(xs), "height": max(ys) - min(ys)}]
        stroke = float(inherited.get("stroke-width", 1)) / 2 if inherited.get("stroke", "none") != "none" else 0
        if name in {"polygon", "polyline", "path", "rect", "text"}:
            # The conservative contract does not expose linejoin/miterlimit;
            # reserve the default miter limit, including sharp path corners.
            stroke *= 4
        if boxes:
            result[node.get("id")] = [{"x": b["x"] + dx - stroke, "y": b["y"] + dy - stroke,
                "width": b["width"] + 2 * stroke, "height": b["height"] + 2 * stroke} for b in boxes]
        for child in node:
            visit(child, dx, dy, inherited)

    visit(root)
    return result
