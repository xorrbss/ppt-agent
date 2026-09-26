"""Check a chart's declared appearance against its drawn SVG primitives."""
from __future__ import annotations

import math

from services.generation_core.drivers.svg.native_geometry import (
    Box, GroupPrimitives, NativeRejection, normalize_text, parse_label_number,
)

_ORDERS = {"bar": "topToBottom", "column": "leftToRight", "line": "leftToRight",
           "area": "leftToRight", "pie": "clockwise", "donut": "clockwise"}
_POSITIONS = {"bar": {"center", "insideEnd", "insideBase", "outsideEnd"},
              "column": {"center", "insideEnd", "insideBase", "outsideEnd"},
              "line": {"above", "center"}, "area": {"above", "center"},
              "pie": {"center", "bestFit", "outsideEnd"},
              "donut": {"center", "bestFit", "outsideEnd"}}


def _mid(box: Box, axis: str) -> float:
    return (box.x0 + box.x1) / 2 if axis == "x" else (box.y0 + box.y1) / 2


def _category_texts(payload: dict, prims: GroupPrimitives) -> list:
    categories = [normalize_text(value) for value in payload["categories"]]
    found = []
    for category in categories:
        matches = [text for text in prims.texts if normalize_text(" ".join(text.lines)) == category]
        if len(matches) != 1:
            raise NativeRejection("native_chart_category_order_mismatch")
        found.append(matches[0])
    return found


def _bar_points(payload: dict, prims: GroupPrimitives, labels: list) -> list:
    axis = "y" if payload["chartType"] == "bar" else "x"
    coordinates = [_mid(text.box(), axis) for text in labels]
    if coordinates != sorted(coordinates) or len(set(coordinates)) != len(coordinates):
        raise NativeRejection("native_chart_category_order_mismatch")
    if len(payload["series"]) != 1:
        return []
    candidates = [shape for shape in prims.shapes if shape.tag == "rect" and shape.fill
                  and shape.box.width > 1 and shape.box.height > 1]
    matched = []
    for index, coordinate in enumerate(coordinates):
        near = [shape for shape in candidates
                if abs(_mid(shape.box, axis) - coordinate) <
                (min(abs(coordinate - other) for j, other in enumerate(coordinates) if j != index) / 2
                 if len(coordinates) > 1 else float("inf"))]
        if len(near) != 1:
            raise NativeRejection("native_chart_point_color_mismatch")
        matched.append(near[0])
    if len(set(map(id, matched))) != len(matched):
        raise NativeRejection("native_chart_point_color_mismatch")
    return matched


def _value_texts(payload: dict, prims: GroupPrimitives) -> list:
    reserved = {normalize_text(value) for value in payload["categories"]}
    reserved.update(normalize_text(item["name"]) for item in payload["series"])
    if payload.get("title"):
        reserved.add(normalize_text(payload["title"]))
    return [text for text in prims.texts if normalize_text(" ".join(text.lines)) not in reserved]


def _label_position(chart_type: str, label: Box, shape: Box) -> str:
    if chart_type == "bar":
        center = _mid(label, "x")
        if center > shape.x1:
            return "outsideEnd"
        if center < shape.x0 + shape.width * 0.34:
            return "insideBase"
        return "insideEnd" if center > shape.x0 + shape.width * 0.66 else "center"
    center = _mid(label, "y")
    if center < shape.y0:
        return "outsideEnd"
    if center > shape.y1 - shape.height * 0.34:
        return "insideBase"
    return "insideEnd" if center < shape.y0 + shape.height * 0.34 else "center"


def _inside_polygon(x: float, y: float, vertices: tuple[tuple[float, float], ...]) -> bool:
    inside = False
    for index, (x0, y0) in enumerate(vertices):
        x1, y1 = vertices[(index + 1) % len(vertices)]
        if (y0 > y) != (y1 > y) and x < (x1 - x0) * (y - y0) / (y1 - y0) + x0:
            inside = not inside
    return inside


def chart_appearance(raw: dict, payload: dict, prims: GroupPrimitives) -> dict | None:
    """Return the SVG-only export options, or reject an appearance mismatch."""
    chart_type = payload["chartType"]
    if chart_type not in _ORDERS:
        raise NativeRejection("native_chart_type_unsupported")
    category_order = raw.get("categoryOrder")
    labels_decl = raw.get("valueLabels")
    markers = raw.get("markers", False)
    if (category_order is None and labels_decl is None and "markers" not in raw):
        return None  # Older declarations keep their established export shape.
    if (category_order != _ORDERS[chart_type] or type(markers) is not bool
            or not isinstance(labels_decl, dict) or set(labels_decl) != {"shown", "position"}
            or type(labels_decl["shown"]) is not bool
            or labels_decl["position"] not in (_POSITIONS[chart_type] if labels_decl["shown"] else {None})
            or (markers and chart_type != "line")):
        raise NativeRejection("native_chart_appearance_invalid")
    categories = _category_texts(payload, prims)
    points = _bar_points(payload, prims, categories) if chart_type in {"bar", "column"} else []
    if chart_type in {"bar", "column"} and not points and any(
            item.get("pointColors") for item in payload["series"]):
        raise NativeRejection("native_chart_point_color_mismatch")
    if chart_type in {"line", "area"}:
        xs = [_mid(text.box(), "x") for text in categories]
        if xs != sorted(xs) or len(set(xs)) != len(xs):
            raise NativeRejection("native_chart_category_order_mismatch")
    if points and payload["series"][0].get("pointColors") != [point.fill for point in points]:
        raise NativeRejection("native_chart_point_color_mismatch")
    if chart_type in {"pie", "donut"}:
        slices = [shape for shape in prims.shapes if shape.tag in {"path", "polygon"} and shape.fill]
        if (len(slices) != len(payload["categories"]) or
                payload["series"][0].get("pointColors") != [shape.fill for shape in slices]):
            raise NativeRejection("native_chart_point_color_mismatch")
        amounts = payload["series"][0]["values"]
        total_area, total_amount = sum(shape.area for shape in slices), sum(amounts)
        if (total_area <= 0 or total_amount <= 0 or any(value <= 0 for value in amounts)
                or any(abs(shape.area / total_area - amount / total_amount) > 0.035
                       for shape, amount in zip(slices, amounts, strict=True))):
            raise NativeRejection("native_chart_slice_value_mismatch")
        if any(shape.centroid is None for shape in slices):
            raise NativeRejection("native_chart_slice_geometry_invalid")
        center = (sum(shape.centroid[0] * shape.area for shape in slices) / total_area,
                  sum(shape.centroid[1] * shape.area for shape in slices) / total_area)
        angles = [math.atan2(shape.centroid[1] - center[1], shape.centroid[0] - center[0])
                  for shape in slices]
        if any(not 0 < (angles[(i + 1) % len(angles)] - angle) % (2 * math.pi) < math.pi
               for i, angle in enumerate(angles)):
            raise NativeRejection("native_chart_category_order_mismatch")
        for shape, label in zip(slices, categories, strict=True):
            shape_angle = math.atan2(shape.centroid[1] - center[1], shape.centroid[0] - center[0])
            label_angle = math.atan2(_mid(label.box(), "y") - center[1], _mid(label.box(), "x") - center[0])
            if abs((shape_angle - label_angle + math.pi) % (2 * math.pi) - math.pi) > math.pi / 4:
                raise NativeRejection("native_chart_category_order_mismatch")
    values = _value_texts(payload, prims)
    if labels_decl["shown"] != bool(values):
        raise NativeRejection("native_chart_value_labels_mismatch")
    if values:
        if chart_type == "area":
            raise NativeRejection("native_chart_value_label_position_mismatch")
        if len(payload["series"]) != 1 or len(values) != len(payload["categories"]):
            raise NativeRejection("native_chart_value_labels_mismatch")
        expected = payload["series"][0]["values"]
        remaining = list(values)
        for index, number in enumerate(expected):
            matches = [text for text in remaining if parse_label_number(" ".join(text.lines)) == number]
            if not matches:
                raise NativeRejection("native_chart_value_labels_mismatch")
            text = matches[0]
            remaining.remove(text)
            if points:
                cross_axis = "y" if chart_type == "bar" else "x"
                if (abs(_mid(text.box(), cross_axis) - _mid(points[index].box, cross_axis)) >
                        (points[index].box.height if chart_type == "bar" else points[index].box.width)
                        or _label_position(chart_type, text.box(), points[index].box) != labels_decl["position"]):
                    raise NativeRejection("native_chart_value_label_position_mismatch")
            if chart_type in {"pie", "donut"} and labels_decl["position"] != "bestFit":
                slices = [shape for shape in prims.shapes if shape.tag in {"path", "polygon"}]
                inside = _inside_polygon(_mid(text.box(), "x"), _mid(text.box(), "y"),
                                         slices[index].vertices)
                if inside != (labels_decl["position"] == "center"):
                    raise NativeRejection("native_chart_value_label_position_mismatch")
            if chart_type == "line":
                curves = [shape for shape in prims.shapes if shape.tag in {"polyline", "path"} and shape.stroke]
                if len(curves) != 1 or len(curves[0].vertices) != len(expected):
                    raise NativeRejection("native_chart_line_geometry_invalid")
                px, py = curves[0].vertices[index]
                above = text.box().y1 < py
                centered = text.box().contains(px, py, tolerance=4)
                if abs(_mid(categories[index].box(), "x") - px) > 30 or not (
                        above if labels_decl["position"] == "above" else centered):
                    raise NativeRejection("native_chart_value_label_position_mismatch")
        if remaining:
            raise NativeRejection("native_chart_value_labels_mismatch")
    if markers:
        circles = [shape for shape in prims.shapes if shape.tag in {"circle", "ellipse"}]
        if len(circles) < len(payload["categories"]):
            raise NativeRejection("native_chart_markers_mismatch")
    elif chart_type == "line" and any(shape.tag in {"circle", "ellipse"} for shape in prims.shapes):
        raise NativeRejection("native_chart_markers_mismatch")
    if payload.get("title"):
        title = next(text for text in prims.texts
                     if normalize_text(" ".join(text.lines)) == normalize_text(payload["title"]))
        plot_shapes = [shape for shape in prims.shapes if shape.tag != "line"]
        if plot_shapes and title.box().y1 >= min(shape.box.y0 for shape in plot_shapes):
            raise NativeRejection("native_chart_title_position_mismatch")
    appearance = {"categoryOrder": category_order, "valueLabels": labels_decl, "markers": markers}
    if chart_type == "line" and len(payload["series"]) == 1:
        lines = [shape for shape in prims.shapes if shape.tag in {"polyline", "path"} and shape.stroke]
        if len(lines) != 1:
            raise NativeRejection("native_chart_line_geometry_invalid")
        appearance["lineWidthPx"] = lines[0].stroke_width
        if markers:
            sizes = [shape.box.width for shape in prims.shapes if shape.tag in {"circle", "ellipse"}]
            if len(sizes) != len(payload["categories"]) or max(sizes) - min(sizes) > 0.5:
                raise NativeRejection("native_chart_markers_mismatch")
            appearance["markerDiameterPx"] = sizes[0]
    return appearance
