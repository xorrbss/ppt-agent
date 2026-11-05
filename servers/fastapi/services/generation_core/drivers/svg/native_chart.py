"""Validate one declared native chart against its drawing and derive the V1 payload.

Converted natively, the group's drawn children are replaced by a chart built
from the payload, so the payload may only say what the group already shows:

* The model JSON declares its category order, point colours, value labels and
  markers alongside the authored V1 values; SVG-only options live on the
  sidecar entry, leaving the V1 chart payload unchanged.
* Every category and series name (and the title, when declared) is drawn as a
  text in the group: the converter drops, and the pre-export validator
  rejects, a title or legend entry that is not drawn inside the group. Axis
  titles are not part of the model JSON. A pie has exactly one series. Chart
  texts are compared with their lines joined by a space, whitespace-normalized
  NFC.
* Every drawn text is a category, a series name, the title, or a value label:
  a number (``,`` separators, sign, currency prefix and short unit suffix
  allowed; the suffix is not interpreted) equal to one of the series values.
  Axis tick labels are therefore not accepted: the native chart draws its own.
* Anchor: the bounding box of every primitive (text boxes estimated from their
  font size), canvas px.
* Style: series colours only where unambiguous -- bar/column: exactly one rect
  fill per series drawn once per category (plus an optional legend swatch),
  assigned in drawing order; line: one stroked polyline/path per series. A
  font family shared by every drawn text becomes the chart font.
"""
from __future__ import annotations

import math
import re

from services.generation_core.drivers.svg.native_geometry import (
    Box, GroupPrimitives, NativeRejection, normalize_text, parse_label_number,
)
from services.generation_core.drivers.svg.native_chart_visual import chart_appearance
from services.generation_core.drivers.svg.native_chart_visual import _bar_matrix, _category_texts
from services.generation_core.native_objects.payload import STACKED_CHART_SCHEMA, STACKED_GROUPINGS

CHART_ELEMENTS = frozenset({"g", "rect", "line", "polyline", "polygon", "path", "circle", "ellipse",
                            "text", "tspan"})


_FIXED_AXIS_CONTRACT = "native-chart-fixed-value-axis/v1"
#: Declared ``grouping`` values: clustered is the V1 geometry, the other two
#: stack one rect per series on a shared category baseline (payload revision v3).
_GROUPINGS = ("clustered", *STACKED_GROUPINGS)
_COHERENT_GRID_V2 = "native-chart-coherent-grid/v2"


def _finite_number(value: object) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _model_chart(raw: dict) -> dict:
    if not set(raw) <= {"chartType", "categories", "series", "title", "categoryOrder", "valueLabels", "markers", "categoryAxis", "valueAxis", "grouping", "geometryContract", "axisContract"}:
        raise NativeRejection("native_chart_json_invalid")
    if "geometryContract" in raw and raw["geometryContract"] not in {"native-chart-coherent-grid/v1", _COHERENT_GRID_V2}:
        raise NativeRejection("native_chart_json_invalid")
    # Clustered is the existing V1 bar/column geometry; stacked and
    # percentStacked are proven against the drawn stack (see ``_bar_matrix``).
    # A grouping is never silently discarded when projecting a declaration.
    if "grouping" in raw and (raw.get("chartType") not in ("bar", "column")
                             or raw["grouping"] not in _GROUPINGS
                             or not {"categoryOrder", "valueLabels", "markers"} <= set(raw)):
        raise NativeRejection("native_chart_json_invalid")
    categories, series = raw.get("categories"), raw.get("series")
    if (not isinstance(categories, list) or not all(isinstance(c, str) for c in categories)
            or not isinstance(series, list) or not series
            or not all(isinstance(s, dict) and set(s) <= {"name", "values", "pointColors"}
                       and isinstance(s.get("name"), str)
                       for s in series)
            or (raw.get("title") is not None and not isinstance(raw["title"], str))):
        raise NativeRejection("native_chart_json_invalid")
    if raw.get("grouping") in STACKED_GROUPINGS and (
            len(series) < 2 or "geometryContract" in raw or "axisContract" in raw
            or any("values" in s and isinstance(s["values"], list) and any(
                isinstance(v, (int, float)) and v <= 0 for v in s["values"]) for s in series)):
        # A stack needs 2+ series; the fixed-axis and coherent-grid contracts
        # are clustered-only; a zero or negative segment has no drawn extent.
        raise NativeRejection("native_chart_stack_invalid")
    if "geometryContract" in raw and (raw.get("chartType") not in ("bar", "column")
                                      or raw.get("grouping") != "clustered"
                                      or len(categories) < 2 or len(series) < 2):
        raise NativeRejection("native_chart_json_invalid")
    fixed = raw.get("axisContract") == _FIXED_AXIS_CONTRACT
    if "axisContract" in raw and not fixed:
        raise NativeRejection("native_chart_json_invalid")
    if fixed and (raw.get("geometryContract") != _COHERENT_GRID_V2
                  or "valueAxis" not in raw):
        raise NativeRejection("native_chart_fixed_axis_contract_invalid")
    # Reject invalid numbers before the drawing proof performs float arithmetic.
    # Huge JSON integers can overflow conversion even though their type is numeric.
    try:
        valid_values = all(
            isinstance(s.get("values"), list) and len(s["values"]) == len(categories)
            and all(type(value) in (int, float) and math.isfinite(value) for value in s["values"])
            for s in series
        )
    except OverflowError:
        valid_values = False
    if not valid_values:
        raise NativeRejection("native_chart_json_invalid")
    payload = {"chartType": raw.get("chartType"), "categories": list(categories),
               "series": [{"name": s["name"], "values": s.get("values"),
                           **({"pointColors": s["pointColors"]} if "pointColors" in s else {})}
                          for s in series],
               **({"grouping": raw["grouping"]} if "grouping" in raw else {})}
    if raw.get("grouping") in STACKED_GROUPINGS:
        payload["schemaVersion"] = STACKED_CHART_SCHEMA
    if raw.get("title"):
        payload["title"] = raw["title"]
    for key in ("categoryAxis", "valueAxis"):
        if key in raw:
            axis = raw[key]
            if (not isinstance(axis, dict)
                    or not set(axis) <= {"title", "minimum", "maximum", "majorUnit", "numberFormat"}
                    or not isinstance(axis.get("title", ""), str)
                    or axis.get("numberFormat", "General") != "General"):
                raise NativeRejection("native_chart_json_invalid")
            bound_keys = ("minimum", "maximum", "majorUnit")
            if key == "valueAxis" and fixed:
                if (not all(bound in axis for bound in bound_keys)
                        or not all(_finite_number(axis[bound]) for bound in bound_keys)
                        or axis["minimum"] != 0 or axis["maximum"] <= axis["minimum"]
                        or axis["majorUnit"] <= 0):
                    raise NativeRejection("native_chart_fixed_axis_invalid")
                intervals = (axis["maximum"] - axis["minimum"]) / axis["majorUnit"]
                if (not math.isfinite(intervals) or not 2 <= intervals <= 32
                        or abs(intervals - round(intervals)) > 1e-9
                        or any(value <= 0 or value > axis["maximum"]
                               for item in payload["series"] for value in item["values"])):
                    raise NativeRejection("native_chart_fixed_axis_invalid")
                payload[key] = {**({"title": axis["title"]} if axis.get("title") else {}),
                                **{bound: axis[bound] for bound in bound_keys}}
                payload["schemaVersion"] = "authored-native-chart/v2"
                payload["axisContract"] = _FIXED_AXIS_CONTRACT
                continue
            if any(axis.get(bound) is not None for bound in bound_keys):
                raise NativeRejection("native_chart_json_invalid")
            # Null bounds and General are automatic-axis spelling only. Empty
            # titles own no source text and must not become phantom labels.
            if axis.get("title"):
                payload[key] = {"title": axis["title"]}
    return payload


def _prove_fixed_axis(payload: dict, prims: GroupPrimitives, frame: Box,
                      canvas_texts: tuple) -> None:
    """Match the complete value grid and every positive bar to one fixed scale."""
    layout = payload.get("plot_layout")
    if layout is None:
        raise NativeRejection("native_chart_fixed_axis_geometry_unproven")
    axis = payload["valueAxis"]
    horizontal = payload["chartType"] == "bar"
    baseline_position = (frame.x0 + layout["x"] * frame.width if horizontal else
                         frame.y0 + (layout["y"] + layout["h"]) * frame.height)
    span = layout["w"] * frame.width if horizontal else layout["h"] * frame.height
    if not math.isfinite(span) or span <= 0:
        raise NativeRejection("native_chart_fixed_axis_geometry_unproven")
    intervals = round((axis["maximum"] - axis["minimum"]) / axis["majorUnit"])
    pitch = span / intervals
    grid = []
    for shape in prims.shapes:
        if shape.tag not in {"line", "path", "polyline"} or not shape.stroke or shape.stroke_alpha <= 0:
            continue
        box = shape.box
        narrow = box.width if horizontal else box.height
        coordinate = box.x0 if horizontal else box.y0
        grid_min = baseline_position if horizontal else baseline_position - span
        grid_max = baseline_position + span if horizontal else baseline_position
        if narrow > max(1.0, shape.stroke_width * 2) or not grid_min - 1 <= coordinate <= grid_max + 1:
            continue
        if (box.height if horizontal else box.width) < (
                layout["h"] * frame.height if horizontal else layout["w"] * frame.width) * .98:
            continue
        grid.append(coordinate)
    grid.sort()
    tolerance = max(1.0, pitch * .01)
    if (len(grid) != intervals + 1 or any(abs(value - (grid_min + i * pitch)) > tolerance
                                          for i, value in enumerate(grid))):
        raise NativeRejection("native_chart_fixed_axis_grid_mismatch")
    # Tick labels can be siblings of the declared native group. Their numeric
    # content and placement must independently confirm every grid coordinate.
    category_end = frame.y0 + (layout["y"] + layout["h"]) * frame.height
    value_start = frame.x0 + layout["x"] * frame.width
    for index in range(intervals + 1):
        expected = (axis["minimum"] + index * axis["majorUnit"] if horizontal
                    else axis["maximum"] - index * axis["majorUnit"])
        position = grid_min + index * pitch
        matches = []
        for item in canvas_texts:
            number = parse_label_number(" ".join(item.lines))
            if number is None or abs(number - expected) > 1e-9 * max(1.0, abs(expected)):
                continue
            box = item.box()
            center = (box.x0 + box.x1) / 2 if horizontal else (box.y0 + box.y1) / 2
            outside = (box.y0 >= category_end + 1 if horizontal
                       else box.x1 <= value_start - 1)
            if outside and abs(center - position) <= tolerance:
                matches.append(item)
        if len(matches) != 1:
            raise NativeRejection("native_chart_fixed_axis_tick_mismatch")
    try:
        matrix = _bar_matrix(payload, prims, _category_texts(payload, prims))
    except NativeRejection as error:
        raise NativeRejection("native_chart_fixed_axis_bar_mismatch") from error
    for series, shapes in zip(payload["series"], matrix, strict=True):
        for value, shape in zip(series["values"], shapes, strict=True):
            box = shape.box
            baseline = box.x0 if horizontal else box.y1
            length = box.width if horizontal else box.height
            expected = value / axis["maximum"] * span
            if abs(baseline - baseline_position) > 1 or abs(length - expected) > max(1.0, span * .01):
                raise NativeRejection("native_chart_fixed_axis_bar_mismatch")


def _prove_external_value_axis_title(payload: dict, prims: GroupPrimitives,
                                     canvas_texts: tuple) -> None:
    """Prove one sibling unit title belongs to a fixed column value axis."""
    if payload["chartType"] != "column":
        raise NativeRejection("native_chart_axis_title_not_drawn")
    wanted = normalize_text(payload["valueAxis"]["title"])
    matches = [text for text in canvas_texts
               if normalize_text(" ".join(text.lines)) == wanted]
    if (len(matches) != 1 or not matches[0].uniform or matches[0].fill is None
            or not math.isfinite(matches[0].size) or matches[0].size <= 0
            or any(run.fill is None or not math.isfinite(run.size) or run.size <= 0
                   for line in matches[0].run_lines for run in line)):
        raise NativeRejection("native_chart_axis_title_ambiguous")
    grid = [shape.box for shape in prims.shapes
            if shape.tag in {"line", "path", "polyline"} and shape.stroke
            and shape.stroke_alpha > 0 and shape.box.height <= max(1.0, shape.stroke_width * 2)
            and shape.box.width > 10]
    if len(grid) < 3:
        raise NativeRejection("native_chart_axis_title_geometry_unproven")
    plot_x0, plot_x1 = min(box.x0 for box in grid), max(box.x1 for box in grid)
    plot_y0, plot_y1 = min(box.y0 for box in grid), max(box.y1 for box in grid)
    if (not all(abs(box.x0 - plot_x0) <= 1 and abs(box.x1 - plot_x1) <= 1 for box in grid)
            or plot_x1 <= plot_x0 or plot_y1 <= plot_y0):
        raise NativeRejection("native_chart_axis_title_geometry_unproven")
    box = matches[0].box()
    x_gap, y_gap = plot_x0 - box.x1, plot_y0 - box.y1
    if (not 1 <= x_gap <= max(48, (plot_x1 - plot_x0) * .10)
            or not 0 <= y_gap <= max(48, (plot_y1 - plot_y0) * .25)):
        raise NativeRejection("native_chart_axis_title_geometry_unproven")


def _series_colors(payload: dict, prims: GroupPrimitives) -> list[str] | None:
    count, series = len(payload["categories"]), len(payload["series"])
    if payload["chartType"] in ("bar", "column"):
        order: list[str] = []
        tally: dict[str, int] = {}
        for shape in prims.shapes:
            if shape.tag == "rect" and shape.fill:
                tally[shape.fill] = tally.get(shape.fill, 0) + 1
                if shape.fill not in order:
                    order.append(shape.fill)
        colors = [fill for fill in order if tally[fill] in (count, count + 1)]
        return colors if len(colors) == series else None
    if payload["chartType"] == "line":
        strokes = [s.stroke for s in prims.shapes
                   if s.tag in ("polyline", "path") and s.stroke and s.point_count >= count]
        return strokes if len(strokes) == series else None
    return None


def _drawn_series_name(name: str, drawn: set[str], *, declared_title: str | None = None) -> str:
    exact = normalize_text(name)
    if exact in drawn:
        return name
    # Axis ranges sometimes qualify a declared series name while the legend
    # draws only its short name. The native legend must keep the drawn wording.
    short = normalize_text(re.split(r"\s*\(", name, maxsplit=1)[0])
    if short and short in drawn:
        return short
    # Candidate name proof only; requested_native_quality still checks the
    # exact source unit against this declared and visibly drawn title.
    if declared_title and normalize_text(declared_title) in drawn:
        match = re.fullmatch(
            re.escape(name) + r"\s*·\s*(?:단위|unit)\s*[:：]\s*([^:：·()]+)",
            declared_title.strip(),
        )
        if match:
            from services.generation_core.drivers.svg.native_requirements import _series_name_unit

            if _series_name_unit(f"({match.group(1).strip()})") is not None:
                return name
    raise NativeRejection("native_chart_label_not_drawn")


def _chart_furniture(text: str) -> bool:
    return text.startswith(("단위", "좌축", "우축", "축 ", "Axis ")) or "계열별 축" in text


def _axis_ticks(payload: dict, prims: GroupPrimitives, indexes: list[int]) -> bool:
    """Numeric furniture sits together outside the category plot positions."""
    if len(indexes) < 2:
        return False
    categories = {normalize_text(value) for value in payload["categories"]}
    category_texts = [item for item in prims.texts
                      if normalize_text(" ".join(item.lines)) in categories]
    if len(category_texts) != len(categories):
        return False
    horizontal = payload["chartType"] == "bar"
    def coordinate(item):
        box = item.box()
        return (box.y0 + box.y1) / 2 if horizontal else (box.x0 + box.x1) / 2
    positions = [coordinate(prims.texts[index]) for index in indexes]
    return (max(positions) - min(positions) <= 16
            and (min(positions) > max(map(coordinate, category_texts)) + 10 if horizontal
                 else max(positions) < min(map(coordinate, category_texts)) - 10))


def _source_plot_geometry(payload: dict, prims: GroupPrimitives, frame, *,
                          coherent_grid: bool = False) -> tuple[dict, int, int | None] | None:
    """Return source plot layout, clustered overlap, and gap width when geometry proves them."""
    chart_type = payload.get("chartType")
    categories, series = payload["categories"], payload["series"]
    if (chart_type not in {"bar", "column"} or payload.get("grouping") not in _GROUPINGS
            or len(categories) < 2 or len(series) < 2):
        return None
    stacked = payload["grouping"] in STACKED_GROUPINGS
    expected = [normalize_text(value) for value in categories]
    category_texts = [text for text in prims.texts if normalize_text(" ".join(text.lines)) in expected]
    if (len(category_texts) != len(categories)
            or [normalize_text(" ".join(text.lines)) for text in category_texts] != expected):
        return None
    horizontal = chart_type == "bar"
    centers = [((text.box().y0 + text.box().y1) / 2 if horizontal
                else (text.box().x0 + text.box().x1) / 2) for text in category_texts]
    if any(not math.isfinite(value) for value in centers) or any(
        b <= a for a, b in zip(centers, centers[1:], strict=False)
    ):
        return None
    category_pitches = [b - a for a, b in zip(centers, centers[1:], strict=False)]
    category_pitch = sum(category_pitches) / len(category_pitches)
    category_pitch_proven = (math.isfinite(category_pitch) and category_pitch > 0
                             and max(category_pitches) - min(category_pitches)
                             <= max(1.0, category_pitch * 0.01))
    colors = _series_colors(payload, prims)
    if not colors or len(colors) != len(series):
        return None

    bars_by_category: list[list] = [[] for _ in categories]
    thicknesses: list[float] = []
    if payload.get("axisContract") == _FIXED_AXIS_CONTRACT or stacked:
        # A legend swatch can share a series colour and sit near a category
        # center. Reuse the stricter bar/data matcher for fixed-axis charts;
        # ordinary V1 automatic-axis geometry keeps its established path.
        try:
            matrix = _bar_matrix(payload, prims, category_texts)
        except NativeRejection:
            return None
        if any(any(shape.fill != color or shape.fill_alpha <= 0 for shape in shapes)
               for color, shapes in zip(colors, matrix, strict=True)):
            return None
        for index in range(len(categories)):
            for shapes in matrix:
                box = shapes[index].box
                bars_by_category[index].append(box)
                thicknesses.append(box.height if horizontal else box.width)
    else:
        for color in colors:
            candidates = [shape for shape in prims.shapes
                          if shape.tag == "rect" and shape.fill == color and shape.fill_alpha > 0
                          and shape.box.width > 0 and shape.box.height > 0]
            matched = []
            for center in centers:
                def category_center(shape):
                    box = shape.box
                    return (box.y0 + box.y1) / 2 if horizontal else (box.x0 + box.x1) / 2
                nearby = [shape for shape in candidates
                          if abs(category_center(shape) - center) <= 0.30 * min(
                              [abs(b - a) for a, b in zip(centers, centers[1:], strict=False)] or [float("inf")])]
                if len(nearby) != 1:
                    return None
                matched.append(nearby[0])
            if len({id(shape) for shape in matched}) != len(categories):
                return None
            for index, shape in enumerate(matched):
                box = shape.box
                bars_by_category[index].append(box)
                thicknesses.append(box.height if horizontal else box.width)
    if not thicknesses or max(thicknesses) - min(thicknesses) > 0.5:
        return None
    thickness = sum(thicknesses) / len(thicknesses)
    overlaps = []
    for bars in bars_by_category:
        if len(bars) != len(series):
            return None
        if stacked:
            continue  # Segments share one category slot; the converter forces overlap 100.
        ordered = sorted(bars, key=lambda box: box.y0 if horizontal else box.x0)
        for first, second in zip(ordered, ordered[1:], strict=False):
            gap = (second.y0 - first.y1) if horizontal else (second.x0 - first.x1)
            overlaps.append(-100.0 * gap / thickness)
    overlap = None
    if not stacked:
        if (not overlaps or max(overlaps) - min(overlaps) > 1.0
                or any(not math.isfinite(value) or not -100 <= value <= 100 for value in overlaps)):
            return None
        overlap = round(sum(overlaps) / len(overlaps))
    group_bounds = [
        (min((box.y0 if horizontal else box.x0) for box in bars),
         max((box.y1 if horizontal else box.x1) for box in bars))
        for bars in bars_by_category
    ]
    group_spans = [high - low for low, high in group_bounds]
    group_span = sum(group_spans) / len(group_spans)
    gap_width = None
    if category_pitch_proven:
        center_tolerance = max(1.0, category_pitch * 0.03)
        geometry_proven = (
            max(group_spans) - min(group_spans) <= max(1.0, group_span * 0.01)
            and all(abs((low + high) / 2 - center) <= center_tolerance
                    for (low, high), center in zip(group_bounds, centers, strict=True))
        )
        if geometry_proven:
            gap_value = 100.0 * (category_pitch - group_span) / thickness
            if math.isfinite(gap_value) and 0 <= gap_value <= 500:
                gap_width = round(gap_value)

    # Repeated axis-aligned gridlines bound the plot. A single baseline cannot
    # establish the missing edge, so it deliberately leaves automatic layout.
    axis_lines = []
    for shape in prims.shapes:
        box = shape.box
        if shape.tag not in {"line", "path", "polyline"} or not shape.stroke or shape.stroke_alpha <= 0:
            continue
        if horizontal:
            if box.height <= 1 or box.width > max(1.0, shape.stroke_width * 2):
                continue
            if (box.y0 > min(b.y0 for group in bars_by_category for b in group) + 1
                    or box.y1 < max(b.y1 for group in bars_by_category for b in group) - 1):
                continue
            if box.y0 <= min(centers) and box.y1 >= max(centers):
                axis_lines.append(box)
        else:
            if box.width <= 1 or box.height > max(1.0, shape.stroke_width * 2):
                continue
            if (box.x0 > min(b.x0 for group in bars_by_category for b in group) + 1
                    or box.x1 < max(b.x1 for group in bars_by_category for b in group) - 1):
                continue
            if box.x0 <= min(centers) and box.x1 >= max(centers):
                axis_lines.append(box)
    axis_coord = (lambda box: (box.y0, box.y1)) if horizontal else (lambda box: (box.x0, box.x1))
    families: list[list] = []
    for box in axis_lines:
        key = axis_coord(box)
        family = next((group for group in families
                       if abs(axis_coord(group[0])[0] - key[0]) <= 1
                       and abs(axis_coord(group[0])[1] - key[1]) <= 1), None)
        if family is None:
            families.append([box])
        else:
            family.append(box)
    proven = [family for family in families if len(family) >= 2]
    if len(proven) != 1:
        return None
    lines = proven[0]
    plot = (min(box.x0 for box in lines), min(box.y0 for box in lines),
            max(box.x1 for box in lines), max(box.y1 for box in lines))
    x0, y0, x1, y1 = plot
    # A native category plot has N equal slots across one grid span. Its
    # category centers must therefore sit at the midpoints of those slots.
    # The SVG may draw a longer grid, but that cannot be encoded in the same
    # editable chart without moving its bars or shortening the grid.
    grid_low, grid_high = (y0, y1) if horizontal else (x0, x1)
    slot = (grid_high - grid_low) / len(categories)
    tolerance = max(1.0, category_pitch * 0.01)
    if coherent_grid and (not category_pitch_proven or not math.isfinite(slot) or slot <= 0
                          or any(abs(center - (grid_low + (index + 0.5) * slot)) > tolerance
                                 for index, center in enumerate(centers))):
        raise NativeRejection("native_chart_grid_category_geometry_conflict")
    # The outermost gridline can sit inside the source value-axis range. If
    # its crossing axis line proves an additional edge, preserve that margin
    # so PowerPoint has the same plot extent (and label reserve) as the SVG.
    axis_edges = []
    for shape in prims.shapes:
        box = shape.box
        if shape.tag not in {"line", "path", "polyline"} or not shape.stroke or shape.stroke_alpha <= 0:
            continue
        tolerance = max(1.0, shape.stroke_width * 2)
        if horizontal:
            if box.width <= 1 or box.height > tolerance:
                continue
            if min(abs(box.y0 - y0), abs(box.y1 - y1)) > tolerance:
                continue
            if box.x0 <= x0 + tolerance and box.x1 >= x1 - tolerance:
                axis_edges.append(box)
        else:
            if box.height <= 1 or box.width > tolerance:
                continue
            if min(abs(box.x0 - x0), abs(box.x1 - x1)) > tolerance:
                continue
            if box.y0 <= y0 + tolerance and box.y1 >= y1 - tolerance:
                axis_edges.append(box)
    if len(axis_edges) == 1:
        edge = axis_edges[0]
        if horizontal:
            x0, x1 = min(x0, edge.x0), max(x1, edge.x1)
        else:
            y0, y1 = min(y0, edge.y0), max(y1, edge.y1)
    if (not all(math.isfinite(value) for value in (x0, y0, x1, y1)) or x0 < frame.x0 or y0 < frame.y0
            or x1 > frame.x1 or y1 > frame.y1 or x1 <= x0 or y1 <= y0):
        return None
    return ({"x": (x0 - frame.x0) / frame.width,
             "y": (y0 - frame.y0) / frame.height,
             "w": (x1 - x0) / frame.width,
             "h": (y1 - y0) / frame.height}, overlap, gap_width)


def _category_footer_frame(payload: dict, prims: GroupPrimitives, frame) -> Box:
    """Reserve one source category line box when column labels touch the frame."""
    if payload.get("chartType") != "column" or payload.get("grouping") not in _GROUPINGS:
        return frame
    expected = [normalize_text(value) for value in payload["categories"]]
    labels = [text for text in prims.texts if normalize_text(" ".join(text.lines)) in expected]
    if (len(labels) != len(expected)
            or [normalize_text(" ".join(text.lines)) for text in labels] != expected):
        return frame
    boxes = [text.box() for text in labels]
    # Expand only when the source category row itself establishes the chart's
    # lower edge; other designs keep their existing frame unchanged.
    if max(box.y1 for box in boxes) < frame.y1 - 1.0:
        return frame
    line_box = max(text.size * 1.25 for text in labels)
    if not math.isfinite(line_box) or line_box <= 0:
        return frame
    return Box(frame.x0, frame.y0, frame.x1, frame.y1 + line_box)


def chart_object(raw: dict, prims: GroupPrimitives, *,
                 canvas_texts: tuple = ()) -> tuple[dict, dict, dict | None]:
    """``(anchor, payload, SVG appearance)`` for one declared chart."""
    if not prims.tags <= CHART_ELEMENTS:
        raise NativeRejection("native_group_element_unsupported")
    payload = _model_chart(raw)
    if payload["chartType"] == "pie" and len(payload["series"]) != 1:
        raise NativeRejection("native_chart_pie_series_invalid")
    drawn = [normalize_text(" ".join(text.lines)) for text in prims.texts]
    drawn_set = set(drawn)
    if payload.get("axisContract") == _FIXED_AXIS_CONTRACT and payload.get("valueAxis", {}).get("title"):
        axis_title = normalize_text(payload["valueAxis"]["title"])
        sibling_matches = [text for text in canvas_texts
                           if normalize_text(" ".join(text.lines)) == axis_title]
        if axis_title in drawn_set and sibling_matches:
            raise NativeRejection("native_chart_axis_title_ambiguous")
        if axis_title not in drawn_set:
            _prove_external_value_axis_title(payload, prims, canvas_texts)
            payload["retainedAxisTitle"] = {
                "axis": "value", "source": "svg-sibling/v1",
                "text": payload["valueAxis"].pop("title"),
            }
    labels = {normalize_text(item) for item in payload["categories"]}
    for item in payload["series"]:
        item["name"] = _drawn_series_name(
            item["name"], drawn_set,
            declared_title=(
                raw.get("title")
                if len(payload["series"]) == 1 and raw.get("title")
                and drawn.count(normalize_text(raw["title"])) == 1
                else None
            ),
        )
    names = {normalize_text(item["name"]) for item in payload["series"]}
    if len(payload["series"]) == 1 and not names <= drawn_set:
        # The preceding name proof can borrow only the unique declared unit title.
        names = {normalize_text(raw["title"])}
    title = {normalize_text(payload["title"])} if payload.get("title") else set()
    title.update(normalize_text(payload[key]["title"]) for key in ("categoryAxis", "valueAxis")
                 if payload.get(key, {}).get("title"))
    values = [value for item in payload["series"] if isinstance(item["values"], list)
              for value in item["values"] if type(value) in (int, float)]
    if not (labels | names | title) <= drawn_set:
        raise NativeRejection("native_chart_label_not_drawn")
    unknown_numbers = [index for index, text in enumerate(drawn)
                       if text not in labels | names | title and not _chart_furniture(text)
                       and (number := parse_label_number(text)) is not None
                       and not any(abs(number - value) <= 1e-9 * max(1.0, abs(value))
                                   for value in values)]
    if unknown_numbers and not _axis_ticks(payload, prims, unknown_numbers):
        raise NativeRejection("native_chart_text_unaccounted")
    for index, text in enumerate(drawn):
        if text in labels or text in names or text in title:
            continue
        number = parse_label_number(text)
        if _chart_furniture(text) or index in unknown_numbers:
            continue
        if number is None or not any(abs(number - v) <= 1e-9 * max(1.0, abs(v)) for v in values):
            raise NativeRejection("native_chart_text_unaccounted")
    boxes = [shape.box for shape in prims.shapes] + [text.box() for text in prims.texts]
    if not boxes:
        raise NativeRejection("native_chart_empty")
    frame = boxes[0]
    for box in boxes[1:]:
        frame = frame.union(box)
    if frame.width <= 0 or frame.height <= 0:
        raise NativeRejection("native_chart_empty")
    if payload.get("grouping") in _GROUPINGS:
        frame = _category_footer_frame(payload, prims, frame)
        geometry = _source_plot_geometry(
            payload, prims, frame,
            coherent_grid=raw.get("geometryContract") in {"native-chart-coherent-grid/v1", _COHERENT_GRID_V2},
        )
        if geometry is not None:
            payload["plot_layout"], overlap, gap_width = geometry
            if overlap is not None:
                payload["overlap"] = overlap
            if gap_width is not None:
                payload["gap_width"] = gap_width
    if payload.get("axisContract") == _FIXED_AXIS_CONTRACT:
        _prove_fixed_axis(payload, prims, frame, canvas_texts)
    colors = _series_colors(payload, prims) if isinstance(payload["chartType"], str) else None
    if colors:
        for item, color in zip(payload["series"], colors, strict=True):
            item["color"] = color
    families = {text.family.split(",")[0].strip().strip("'\"") for text in prims.texts}
    if len(families) == 1 and "" not in families:
        payload["fontFamily"] = families.pop()
    appearance = chart_appearance(raw, payload, prims)
    return frame.anchor(), payload, appearance
