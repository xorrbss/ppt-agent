"""Check a chart's declared appearance against its drawn SVG primitives."""
from __future__ import annotations

import math
from collections import Counter

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


_STACKED = ("stacked", "percentStacked")
_STACK_LABEL_POSITIONS = {"center", "insideEnd", "insideBase"}
#: A drawn segment may differ from its exact length by rounding only.
_STACK_ABS_TOLERANCE_PX = 1.0
_STACK_REL_TOLERANCE = 0.03


def _stacked_matrix(payload: dict, prims: GroupPrimitives, labels: list) -> list[list]:
    """Prove the drawn stack and return ``[series][category]`` segment shapes.

    Per category the segments must be one chain from the shared category
    baseline outward, in series order, each touching the previous one, all with
    the same thickness and centred on the category label. Segment lengths are
    proportional to their values on one scale (stacked) or to their share of
    the category total, with every stack spanning the same full extent
    (percentStacked).
    """
    horizontal = payload["chartType"] == "bar"
    axis = "y" if horizontal else "x"
    count, series = len(labels), payload["series"]
    coordinates = [_mid(text.box(), axis) for text in labels]
    if coordinates != sorted(coordinates) or len(set(coordinates)) != len(coordinates):
        raise NativeRejection("native_chart_category_order_mismatch")
    spacing = min((b - a for a, b in zip(coordinates, coordinates[1:], strict=False)),
                  default=float("inf"))
    values = [item["values"] for item in series]
    if any(type(value) not in (int, float) or value <= 0 or not math.isfinite(value)
           for row in values for value in row):
        raise NativeRejection("native_chart_stack_geometry_invalid")

    def thick(shape):
        return shape.box.height if horizontal else shape.box.width

    def length(shape):
        return shape.box.width if horizontal else shape.box.height

    def near_edge(shape):
        return shape.box.x0 if horizontal else shape.box.y1

    def far_edge(shape):
        return shape.box.x1 if horizontal else shape.box.y0

    candidates = [shape for shape in prims.shapes if shape.tag == "rect" and shape.fill
                  and shape.fill_alpha > 0 and length(shape) >= 1 and thick(shape) > 1
                  and thick(shape) < spacing * .9]
    # The baseline is the outermost edge from which one segment per category
    # starts; a legend swatch row can share a coordinate but not every category.
    rows: dict[int, list] = {}
    for shape in candidates:
        rows.setdefault(round(near_edge(shape)), []).append(shape)

    def starts(row):
        found = []
        for coordinate in coordinates:
            near = [shape for shape in row if abs(_mid(shape.box, axis) - coordinate) < spacing / 2]
            if len(near) != 1:
                return None
            found.append(near[0])
        return found

    proven = [(key, found) for key, row in rows.items() if (found := starts(row)) is not None]
    if not proven:
        raise NativeRejection("native_chart_stack_geometry_invalid")
    _, first = (min if horizontal else max)(proven, key=lambda item: item[0])
    if max(near_edge(shape) for shape in first) - min(near_edge(shape) for shape in first) > 1:
        raise NativeRejection("native_chart_stack_baseline_mismatch")
    chains: list[list] = []
    for coordinate, start in zip(coordinates, first, strict=True):
        chain = [start]
        near = [shape for shape in candidates if shape is not start
                and abs(_mid(shape.box, axis) - coordinate) < spacing / 2]
        for _ in range(len(series) - 1):
            tolerance = max(1.5, length(chain[-1]) * .01)
            following = [shape for shape in near if shape not in chain
                         and abs(near_edge(shape) - far_edge(chain[-1])) <= tolerance]
            if len(following) != 1:
                raise NativeRejection("native_chart_stack_not_cumulative")
            chain.append(following[0])
        if any(shape not in chain and _overlaps_stack(shape, chain, horizontal) for shape in near):
            raise NativeRejection("native_chart_stack_geometry_invalid")
        chains.append(chain)
    thicknesses = [thick(shape) for chain in chains for shape in chain]
    if max(thicknesses) - min(thicknesses) > 1:
        raise NativeRejection("native_chart_stack_geometry_invalid")
    for coordinate, chain in zip(coordinates, chains, strict=True):
        if any(abs(_mid(shape.box, axis) - coordinate) > max(1.0, spacing * .03) for shape in chain):
            raise NativeRejection("native_chart_stack_geometry_invalid")
    lengths = [[length(shape) for shape in chain] for chain in chains]
    totals = [sum(row) for row in lengths]
    if payload["grouping"] == "stacked":
        scale = sum(totals) / sum(value for row in values for value in row)
        for c in range(count):
            for s in range(len(series)):
                expected = values[s][c] * scale
                if abs(lengths[c][s] - expected) > max(_STACK_ABS_TOLERANCE_PX, _STACK_REL_TOLERANCE * expected):
                    raise NativeRejection("native_chart_value_geometry_mismatch")
    else:
        full = sum(totals) / count
        if any(abs(total - full) > max(1.5, full * .01) for total in totals):
            raise NativeRejection("native_chart_stack_extent_mismatch")
        for c in range(count):
            column = sum(values[s][c] for s in range(len(series)))
            for s in range(len(series)):
                expected = values[s][c] / column * totals[c]
                if abs(lengths[c][s] - expected) > max(_STACK_ABS_TOLERANCE_PX, _STACK_REL_TOLERANCE * expected):
                    raise NativeRejection("native_chart_value_geometry_mismatch")
    return [[chains[c][s] for c in range(count)] for s in range(len(series))]


def _overlaps_stack(shape, chain, horizontal: bool) -> bool:
    """Whether an unclaimed rect sits on the stack's own ink (a stray segment)."""
    low = min((b.box.x0 if horizontal else b.box.y0) for b in chain)
    high = max((b.box.x1 if horizontal else b.box.y1) for b in chain)
    a0, a1 = (shape.box.x0, shape.box.x1) if horizontal else (shape.box.y0, shape.box.y1)
    return a0 < high - 1 and a1 > low + 1


def _bar_matrix(payload: dict, prims: GroupPrimitives, labels: list) -> list[list]:
    if payload.get("grouping") in _STACKED:
        return _stacked_matrix(payload, prims, labels)
    axis = "y" if payload["chartType"] == "bar" else "x"
    coordinates = [_mid(text.box(), axis) for text in labels]
    if coordinates != sorted(coordinates) or len(set(coordinates)) != len(coordinates):
        raise NativeRejection("native_chart_category_order_mismatch")
    spacing = min((b - a for a, b in zip(coordinates, coordinates[1:], strict=False)),
                  default=float("inf"))
    candidates = [shape for shape in prims.shapes if shape.tag == "rect" and shape.fill
                  and shape.box.width > 1 and shape.box.height > 1
                  and (shape.box.height if axis == "y" else shape.box.width) < spacing * .9]
    def baseline(shape):
        return shape.box.x0 if axis == "y" else shape.box.y1
    if not candidates:
        raise NativeRejection("native_chart_bar_geometry_invalid")
    common = Counter(round(baseline(shape)) for shape in candidates).most_common(1)[0][0]
    bars = [shape for shape in candidates if abs(baseline(shape) - common) <= 1]
    per_category = []
    for coordinate in coordinates:
        near = [shape for shape in bars if abs(_mid(shape.box, axis) - coordinate) < spacing / 2]
        if len(near) != len(payload["series"]):
            raise NativeRejection("native_chart_bar_geometry_invalid")
        per_category.append(sorted(near, key=lambda shape: _mid(shape.box, "y" if axis == "y" else "x")))
    if len({id(shape) for row in per_category for shape in row}) != len(bars):
        raise NativeRejection("native_chart_bar_geometry_invalid")
    matrix = [[row[series] for row in per_category] for series in range(len(payload["series"]))]
    for series, shapes in zip(payload["series"], matrix, strict=True):
        values = series["values"]
        if len(values) != len(shapes) or any(type(value) not in (int, float) or value <= 0 for value in values):
            raise NativeRejection("native_chart_bar_geometry_invalid")
        lengths = [shape.box.width if axis == "y" else shape.box.height for shape in shapes]
        scales = [length / value for length, value in zip(lengths, values, strict=True)]
        if max(scales) / min(scales) > 1.08:
            raise NativeRejection("native_chart_value_geometry_mismatch")
    return matrix


def _color_near(declared: str, drawn: str) -> bool:
    if not (isinstance(declared, str) and len(declared) == len(drawn) == 7
            and declared.startswith("#") and drawn.startswith("#")):
        return False
    try:
        return max(abs(int(declared[i:i + 2], 16) - int(drawn[i:i + 2], 16))
                   for i in (1, 3, 5)) <= 16
    except ValueError:
        return False


def _value_texts(payload: dict, prims: GroupPrimitives) -> list:
    reserved = {normalize_text(value) for value in payload["categories"]}
    reserved.update(normalize_text(item["name"]) for item in payload["series"])
    if payload.get("title"):
        reserved.add(normalize_text(payload["title"]))
    declared_values = [value for series in payload["series"] for value in series["values"]]
    return [text for text in prims.texts if normalize_text(" ".join(text.lines)) not in reserved
            and parse_label_number(" ".join(text.lines)) in declared_values]


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
    stacked = payload.get("grouping") in _STACKED
    if (stacked and labels_decl["shown"]
            and labels_decl["position"] not in _STACK_LABEL_POSITIONS):
        # Office has no outside-end label for a stacked bar.
        raise NativeRejection("native_chart_appearance_invalid")
    categories = _category_texts(payload, prims)
    matrix = _bar_matrix(payload, prims, categories) if chart_type in {"bar", "column"} else []
    if chart_type in {"line", "area"}:
        xs = [_mid(text.box(), "x") for text in categories]
        if xs != sorted(xs) or len(set(xs)) != len(xs):
            raise NativeRejection("native_chart_category_order_mismatch")
    for series, shapes in zip(payload["series"], matrix, strict=False):
        drawn_colors = [shape.fill for shape in shapes]
        declared_colors = series.get("pointColors")
        if (not isinstance(declared_colors, list) or len(declared_colors) != len(drawn_colors)
                or not all(_color_near(a, b) for a, b in zip(declared_colors, drawn_colors, strict=True))):
            series["pointColors"] = drawn_colors
    if chart_type in {"pie", "donut"}:
        slices = [shape for shape in prims.shapes if shape.tag in {"path", "polygon"} and shape.fill]
        if len(slices) != len(payload["categories"]):
            raise NativeRejection("native_chart_point_color_mismatch")
        payload["series"][0]["pointColors"] = [shape.fill for shape in slices]
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
    labels_decl = {"shown": bool(values), "position": labels_decl["position"] if values else None}
    if values:
        labels_decl["position"] = labels_decl["position"] or (
            "bestFit" if chart_type in {"pie", "donut"} else "above" if chart_type == "line" else "outsideEnd")
        if len(values) != len(payload["categories"]) * len(payload["series"]):
            raise NativeRejection("native_chart_value_labels_mismatch")
        remaining = list(values)
        drawn_positions = []
        inside_end_proofs = []
        for series_index, series in enumerate(payload["series"]):
            for index, number in enumerate(series["values"]):
                matches = [text for text in remaining if parse_label_number(" ".join(text.lines)) == number]
                if not matches:
                    raise NativeRejection("native_chart_value_labels_mismatch")
                shape = matrix[series_index][index] if matrix else None
                if stacked:
                    # Segments of one stack share a category coordinate; the label sits in its segment.
                    text = min(matches, key=lambda item: math.hypot(
                        _mid(item.box(), "x") - _mid(shape.box, "x"),
                        _mid(item.box(), "y") - _mid(shape.box, "y")))
                else:
                    text = (min(matches, key=lambda item: abs(_mid(item.box(), "x") - _mid(shape.box, "x")))
                            if shape else matches[0])
                remaining.remove(text)
                if shape:
                    label_box = text.box()
                    inside_end_proofs.append(
                        chart_type == "column" and number > 0
                        and shape.box.x0 <= label_box.x0 <= label_box.x1 <= shape.box.x1
                        and shape.box.y0 <= label_box.y0 <= label_box.y1 <= shape.box.y1
                        # Reuse the existing positive-end third; full containment disambiguates short bars.
                        and label_box.y0 <= shape.box.y0 + (shape.box.y1 - shape.box.y0) * 0.34
                        and label_box.y0 - shape.box.y0 <= (label_box.y1 - label_box.y0) * 0.34
                    )
                    drawn_positions.append(_label_position(chart_type, text.box(), shape.box))
                if chart_type in {"pie", "donut"}:
                    slices = [item for item in prims.shapes if item.tag in {"path", "polygon"} and item.fill]
                    inside = _inside_polygon(_mid(text.box(), "x"), _mid(text.box(), "y"),
                                             slices[index].vertices)
                    drawn_positions.append("center" if inside else "outsideEnd")
                if chart_type == "line":
                    curves = [shape for shape in prims.shapes if shape.tag in {"polyline", "path"} and shape.stroke]
                    if len(curves) != 1 or len(curves[0].vertices) != len(series["values"]):
                        raise NativeRejection("native_chart_line_geometry_invalid")
                    px, py = curves[0].vertices[index]
                    if abs(_mid(categories[index].box(), "x") - px) > 30:
                        raise NativeRejection("native_chart_value_label_position_mismatch")
                    drawn_positions.append("above" if text.box().y1 < py else "center")
        if remaining:
            raise NativeRejection("native_chart_value_labels_mismatch")
        if (labels_decl.get("position") == "insideEnd"
                and set(drawn_positions) == {"center", "insideEnd"}
                and len(inside_end_proofs) == len(drawn_positions)
                and all(inside_end_proofs)):
            drawn_positions = ["insideEnd"] * len(drawn_positions)
        if drawn_positions:
            if len(set(drawn_positions)) != 1:
                raise NativeRejection("native_chart_value_label_position_mismatch")
            labels_decl["position"] = drawn_positions[0]
            if stacked and labels_decl["position"] not in _STACK_LABEL_POSITIONS:
                raise NativeRejection("native_chart_value_label_position_mismatch")
    if markers:
        circles = [shape for shape in prims.shapes if shape.tag in {"circle", "ellipse"}]
        if len(circles) < len(payload["categories"]):
            raise NativeRejection("native_chart_markers_mismatch")
    elif chart_type == "line" and any(shape.tag in {"circle", "ellipse"} for shape in prims.shapes):
        raise NativeRejection("native_chart_markers_mismatch")
    if payload.get("title"):
        title = next(text for text in prims.texts
                     if normalize_text(" ".join(text.lines)) == normalize_text(payload["title"]))
        plot_shapes = ([shape for series in matrix for shape in series] if matrix else
                       [shape for shape in prims.shapes if shape.tag in {"path", "polygon", "polyline"}])
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
