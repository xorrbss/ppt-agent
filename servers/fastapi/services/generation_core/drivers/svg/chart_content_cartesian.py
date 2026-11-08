"""Drawing/data geometry proof for the existing Cartesian SVG chart families.

Only v5 callers use this module. Text visibility, source/unit fidelity, SVG path
parsing and whole-page paint ownership belong to the caller. No appearance keys
or drawn numeric value labels are needed to solve the coordinate constraints.
"""
from __future__ import annotations

from copy import deepcopy
from itertools import combinations, product
import math
import re

from services.generation_core.drivers.svg.native_geometry import (
    Box, GroupPrimitives, NativeRejection, normalize_text, paint, parse_label_number,
)

_EPS = 1.0  # Canvas-pixel rounding, shared across series rather than relative percentages.


def _mid(box: Box, horizontal: bool) -> float:
    return (box.y0 + box.y1) / 2 if horizontal else (box.x0 + box.x1) / 2


def _box(box: Box) -> dict:
    return {"x0": box.x0, "y0": box.y0, "x1": box.x1, "y1": box.y1}


def _fail(reason: str) -> dict:
    return {"passed": False, "reason": "chart_content_" + reason, "points": [], "seriesColors": []}


def _color(shape):
    return (shape.fill if shape.fill and shape.fill_alpha > 0 else
            shape.stroke if shape.stroke and shape.stroke_alpha > 0 else None)


def _axis_number(text):
    value = parse_label_number(text)
    if value is not None:
        return value
    # General-format axes may use scientific notation for finite extremes.
    match = re.fullmatch(r"\s*([-+]?(?:\d+(?:\.\d*)?|\.\d+)[eE][-+]?\d+)\s*(?:[%％A-Za-z가-힣/²³]+)?\s*",
                         text.replace("−", "-"))
    return float(match[1]) if match else None


def _categories(chart: dict, prims: GroupPrimitives):
    horizontal = chart["chartType"] == "bar"
    expected = [normalize_text(item) for item in chart["categories"]]
    selected = []
    for label in dict.fromkeys(expected):
        matches = [(i, text) for i, text in enumerate(prims.texts)
                   if normalize_text(" ".join(text.lines)) == label]
        count = expected.count(label)
        if len(matches) < count:
            raise NativeRejection("category_labels_missing")
        # A same-word title or legend need not be a category label. Cartesian
        # category labels occupy the left or bottom edge of their plot.
        matches.sort(key=lambda pair: pair[1].box().x0 if horizontal else -pair[1].box().y1)
        selected.extend(matches[:count])
    selected.sort(key=lambda pair: _mid(pair[1].box(), horizontal))
    if [normalize_text(" ".join(text.lines)) for _, text in selected] != expected:
        raise NativeRejection("category_order_mismatch")
    coordinates = [_mid(text.box(), horizontal) for _, text in selected]
    if any(not math.isfinite(value) for value in coordinates):
        raise NativeRejection("category_geometry_nonfinite")
    if any(b - a <= _EPS for a, b in zip(coordinates, coordinates[1:])):
        raise NativeRejection("category_positions_ambiguous")
    return selected, coordinates


def _legend(chart: dict, prims: GroupPrimitives):
    colors, indices, positions, text_indices = {}, set(), {}, {}
    for series_index, series in enumerate(chart["series"]):
        labels = [(index, text) for index, text in enumerate(prims.texts)
                  if normalize_text(" ".join(text.lines)) == normalize_text(series["name"])]
        candidates = []
        for index, shape in enumerate(prims.shapes):
            color = _color(shape) if shape.tag in {"rect", "circle", "ellipse"} else shape.stroke
            if not color or shape.tag not in {"rect", "line", "polyline", "path", "circle", "ellipse"}:
                continue
            if shape.tag in {"circle", "ellipse"}:
                cx, cy = (shape.box.x0 + shape.box.x1) / 2, (shape.box.y0 + shape.box.y1) / 2
                if any(other is not shape and other.tag in {"line", "polyline", "path", "polygon"}
                       and _color(other) and any(abs(x - cx) <= _EPS and abs(y - cy) <= _EPS
                                                for x, y in other.vertices) for other in prims.shapes):
                    continue  # A datum marker beside an end-label is not a swatch.
            for text_index, text in labels:
                box = text.box()
                gap = box.x0 - shape.box.x1
                if (0 <= gap <= max(24, text.size * 2)
                        and abs((box.y0 + box.y1 - shape.box.y0 - shape.box.y1) / 2) <= text.size * .6
                        and shape.box.width <= text.size * 3 and shape.box.height <= text.size * 2):
                    candidates.append((index, color, box.x0, (box.y0 + box.y1) / 2, text_index))
        if len(candidates) == 1:
            index, color, x, y, text_index = candidates[0]
            colors[series_index] = color
            indices.add(index)
            positions[series_index] = (x, y)
            text_indices[series_index] = [text_index]
    # A colored series name can itself be a legend. Its actual paint must
    # identify data paint uniquely among numerically distinct named series;
    # repeated default-color names supply no visible series association.
    data_colors = {_color(shape) if chart["chartType"] != "line" else shape.stroke
                   for index, shape in enumerate(prims.shapes) if index not in indices}
    named = [(s, index, text) for s, series in enumerate(chart["series"])
             for index, text in enumerate(prims.texts)
             if normalize_text(" ".join(text.lines)) == normalize_text(series["name"])]
    for s, series in enumerate(chart["series"]):
        if s in colors:
            continue
        matches = [(index, text) for owner, index, text in named if owner == s and text.fill
                   and text.fill in data_colors and not any(
                       other != s and candidate.fill == text.fill
                       and chart["series"][other]["values"] != series["values"]
                       for other, _, candidate in named)]
        if len(matches) == 1:
            index, text = matches[0]
            box = text.box()
            colors[s], positions[s], text_indices[s] = text.fill, (box.x0, (box.y0 + box.y1) / 2), [index]
    return colors, indices, positions, text_indices


def _ticks(chart: dict, prims: GroupPrimitives, coordinates: list[float], canvas_texts):
    """Read only numeric furniture geometrically attached to value gridlines."""
    horizontal = chart["chartType"] == "bar"
    grid, ticks, text_indices, canvas_indices, bindings = set(), [], set(), set(), []
    texts = [(False, index, text) for index, text in enumerate(prims.texts)] + [
        (True, index, text) for index, text in enumerate(canvas_texts)]
    for index, shape in enumerate(prims.shapes):
        if shape.tag not in {"line", "polyline", "path"} or not shape.stroke or shape.stroke_alpha <= 0:
            continue
        box = shape.box
        narrow, span = (box.width, box.height) if horizontal else (box.height, box.width)
        lo, hi = (box.y0, box.y1) if horizontal else (box.x0, box.x1)
        position = (box.x0 + box.x1) / 2 if horizontal else (box.y0 + box.y1) / 2
        if narrow > _EPS or span < 20 or lo > coordinates[0] + _EPS or hi < coordinates[-1] - _EPS:
            continue
        if not _axis_coverage(shape, horizontal=not horizontal, lo=lo, hi=hi, position=position):
            continue
        matches = []
        for external, text_index, text in texts:
            value = _axis_number(" ".join(text.lines))
            if value is None or not math.isfinite(value):
                continue
            label = text.box()
            outside = label.y0 >= box.y1 + 1 if horizontal else label.x1 <= box.x0 - 1
            coordinate = (label.x0 + label.x1) / 2 if horizontal else (label.y0 + label.y1) / 2
            if outside and abs(coordinate - position) <= _EPS:
                matches.append((external, text_index, value, position))
        if len(matches) == 1:
            external, text_index, value, position = matches[0]
            grid.add(index)
            ticks.append((value, position))
            (canvas_indices if external else text_indices).add(text_index)
            bindings.append({"value": value, "position": position,
                             "scope": "canvas" if external else "local", "textIndex": text_index})
        elif len(matches) > 1:
            raise NativeRejection("tick_geometry_ambiguous")
    return ticks, grid, text_indices, canvas_indices, bindings


def _normalizer(chart: dict, ticks) -> float:
    return max([abs(value) for series in chart["series"] for value in series["values"]]
               + [abs(value) for value, _ in ticks] + [0]) or 1.0


def _fit(pairs, *, direction=-1, baseline=None):
    """Fit one normalized affine value map; never rescale individual series."""
    if not pairs:
        return {"baseline": baseline, "scale": None, "degenerate": True}
    if baseline is not None:
        nonzero = [(value, position) for value, position in pairs if value != 0]
        if nonzero:
            value, position = max(nonzero, key=lambda pair: abs(pair[0]))
            scale = direction * (position - baseline) / value
            if not math.isfinite(scale) or scale <= 0:
                return None
        else:
            scale = None
    else:
        low, high = min(pairs), max(pairs)
        if high[0] != low[0]:
            signed_scale = (high[1] - low[1]) / (high[0] - low[0])
            scale = signed_scale * direction
            baseline = low[1] - signed_scale * low[0]
            if not math.isfinite(scale) or scale <= 0 or not math.isfinite(baseline):
                return None
        else:
            if max(position for _, position in pairs) - min(position for _, position in pairs) > _EPS:
                return None
            return {"baseline": low[1] if low[0] == 0 else None,
                    "scale": None, "constantPosition": low[1], "degenerate": True}
    if any(abs(position - (baseline + direction * (scale or 0) * value)) > _EPS
           for value, position in pairs):
        return None
    return {"baseline": baseline, "scale": scale, "degenerate": scale is None}


def _fixed_axis(raw, chart, prims, canvas_texts):
    """Keep the complete fixed grid/tick/title contract without requiring fill.

    Native placement has additional style/rectangle restrictions. Those cannot
    turn an accurately calibrated outline/subpixel chart into false content.
    The common datum solve below independently checks every actual bar.
    """
    if raw.get("axisContract") != "native-chart-fixed-value-axis/v1":
        return [], set(), set(), set(), set(), set(), []
    from services.generation_core.drivers.svg.native_chart import (
        _category_footer_frame, _prove_external_value_axis_title, _prove_fixed_axis, _source_plot_geometry,
    )
    payload = deepcopy(chart)
    horizontal = chart["chartType"] == "bar"
    categories, centers = _categories(chart, prims)
    axis = payload["valueAxis"]
    intervals = round((axis["maximum"] - axis["minimum"]) / axis["majorUnit"])
    families = []
    for index, shape in enumerate(prims.shapes):
        if shape.tag not in {"line", "path", "polyline"} or not shape.stroke or shape.stroke_alpha <= 0:
            continue
        box = shape.box
        narrow, span = (box.width, box.height) if horizontal else (box.height, box.width)
        lo, hi = (box.y0, box.y1) if horizontal else (box.x0, box.x1)
        position = (box.x0 + box.x1) / 2 if horizontal else (box.y0 + box.y1) / 2
        if narrow > _EPS or span <= 0:
            continue
        if not _axis_coverage(shape, horizontal=not horizontal, lo=lo, hi=hi, position=position):
            continue
        slot = span / len(centers)
        if any(abs(center - (lo + (c + .5) * slot)) > max(_EPS, slot * .01)
               for c, center in enumerate(centers)):
            continue
        family = next((items for items in families if abs(items[0][2] - lo) <= _EPS
                       and abs(items[0][3] - hi) <= _EPS), None)
        if family is None:
            family = []
            families.append(family)
        family.append((index, position, lo, hi))
    complete = [family for family in families if len(family) == intervals + 1]
    if len(complete) != 1:
        raise NativeRejection("fixed_axis_grid_incomplete")
    family = sorted(complete[0], key=lambda item: item[1])
    low, high = family[0][1], family[-1][1]
    pitch = (high - low) / intervals
    tolerance = max(_EPS, pitch * .01)
    if pitch <= 0 or any(abs(item[1] - (low + i * pitch)) > tolerance for i, item in enumerate(family)):
        raise NativeRejection("fixed_axis_grid_spacing_mismatch")
    ticks, text_indices, canvas_indices, bindings = [], set(), set(), []
    title_indices, canvas_title_indices = set(), set()
    texts = [(False, index, text) for index, text in enumerate(prims.texts)] + [
        (True, index, text) for index, text in enumerate(canvas_texts)]
    for i, (_, position, cat_lo, cat_hi) in enumerate(family):
        expected = axis["minimum"] + i * axis["majorUnit"] if horizontal else axis["maximum"] - i * axis["majorUnit"]
        matches = []
        for external, text_index, text in texts:
            value = _axis_number(" ".join(text.lines))
            box = text.box()
            outside = box.y0 >= cat_hi + 1 if horizontal else box.x1 <= cat_lo - 1
            coordinate = (box.x0 + box.x1) / 2 if horizontal else (box.y0 + box.y1) / 2
            if (value is not None and math.isfinite(value) and outside
                    and abs(value - expected) <= 1e-9 * max(abs(value), abs(expected))
                    and abs(coordinate - position) <= tolerance):
                matches.append((external, text_index, value))
        if len(matches) != 1:
            raise NativeRejection("fixed_axis_tick_incomplete")
        external, text_index, value = matches[0]
        (canvas_indices if external else text_indices).add(text_index)
        bindings.append({"value": value, "position": position,
                         "scope": "canvas" if external else "local", "textIndex": text_index})
        ticks.append((expected, position))
    boxes = [shape.box for shape in prims.shapes] + [text.box() for text in prims.texts]
    if not boxes:
        raise NativeRejection("fixed_axis_geometry_missing")
    frame = boxes[0]
    for box in boxes[1:]:
        frame = frame.union(box)
    frame = _category_footer_frame(payload, prims, frame)
    title = payload.get("valueAxis", {}).get("title")
    if title:
        inside = [index for index, text in enumerate(prims.texts)
                  if normalize_text(" ".join(text.lines)) == normalize_text(title)]
        outside = [index for index, text in enumerate(canvas_texts)
                   if normalize_text(" ".join(text.lines)) == normalize_text(title)]
        if not inside:
            _prove_external_value_axis_title(payload, prims, canvas_texts)
            canvas_title_indices.update(outside)
        elif len(inside) != 1 or outside:
            raise NativeRejection("fixed_axis_title_ambiguous")
        else:
            title_indices.update(inside)
    # Reuse the existing full native verifier when its placement/paint language
    # is applicable. Its extra editability constraints are not data failures;
    # the independent common-scale solve checks actual outline/missing ink.
    try:
        geometry = _source_plot_geometry(payload, prims, frame, coherent_grid=True)
        if geometry is not None:
            payload["plot_layout"] = geometry[0]
            _prove_fixed_axis(payload, prims, frame, tuple(canvas_texts) + prims.texts)
    except NativeRejection:
        pass
    return ticks, {item[0] for item in family}, text_indices, canvas_indices, title_indices, canvas_title_indices, bindings


def _category_bucket(coordinate, centers):
    index = min(range(len(centers)), key=lambda i: abs(coordinate - centers[i]))
    if len(centers) > 1:
        pitch = min(b - a for a, b in zip(centers, centers[1:]))
        if abs(coordinate - centers[index]) >= pitch / 2:
            return None
    return index


def _point_color(raw, series_index, category_index):
    colors = raw["series"][series_index].get("pointColors")
    if colors is None:
        return None
    if not isinstance(colors, list) or len(colors) != len(raw["categories"]):
        raise NativeRejection("point_color_contract_invalid")
    color = paint(colors[category_index])
    if color is None:
        raise NativeRejection("point_color_contract_invalid")
    return color


def _bar_color(raw, legends, series_index, category_index, shape):
    expected = _point_color(raw, series_index, category_index)
    declared = raw["series"][series_index].get("pointColors") or []
    palette = {paint(color) for color in declared}
    actual = _color(shape)
    if len(palette) > 1:
        return expected == actual and (series_index not in legends or legends[series_index] in palette)
    if series_index in legends:
        return legends[series_index] == actual
    # In the absence of a visible legend, spatial cluster order and the shared
    # value map bind series; optional styling metadata cannot rename them.
    return True


def _bar_proof(raw, chart, prims, centers, legends, legend_indices, ticks, normalizer):
    horizontal = chart["chartType"] == "bar"
    stacked = chart.get("grouping") in {"stacked", "percentStacked"}
    series_count, category_count = len(chart["series"]), len(centers)
    rows = [[] for _ in centers]
    unclaimed = set()
    for index, shape in enumerate(prims.shapes):
        if index in legend_indices or shape.tag != "rect" or not _color(shape):
            continue
        thick = shape.box.height if horizontal else shape.box.width
        length = shape.box.width if horizontal else shape.box.height
        bucket = _category_bucket(_mid(shape.box, horizontal), centers)
        if thick <= 0 or length <= 0:
            continue  # Zero values need no positive-area rectangle.
        if bucket is None or (len(centers) > 1 and thick >= min(
                b - a for a, b in zip(centers, centers[1:])) * .9):
            unclaimed.add(index)
            continue
        rows[bucket].append((index, shape))
    if any(len(row) > series_count for row in rows):
        raise NativeRejection("extra_bar_geometry")
    for row in rows:
        row.sort(key=(lambda item: item[1].box.x0 if horizontal else -item[1].box.y1)
                 if stacked else lambda item: _mid(item[1].box, horizontal))
    if stacked:
        return _stack_proof(raw, chart, prims, rows, centers, legends, ticks, normalizer, unclaimed)
    matrix = [[None] * category_count for _ in range(series_count)]
    assignment = []
    for category_index, row in enumerate(rows):
        eligible = [s for s in range(series_count) if chart["series"][s]["values"][category_index] != 0]
        choices = [choice for choice in combinations(eligible, len(row))
                   if all(_bar_color(raw, legends, s, category_index, shape)
                          for s, (_, shape) in zip(choice, row))]
        if not choices:
            raise NativeRejection("bar_series_association")
        assignment.append(choices)

    def evaluate():
        origins, pairs = [], [(value / normalizer, position) for value, position in ticks]
        for s, series in enumerate(chart["series"]):
            for c, value in enumerate(series["values"]):
                if matrix[s][c] is None:
                    continue
                box = matrix[s][c][1].box
                origin = (box.x0 if value > 0 else box.x1) if horizontal else (box.y1 if value > 0 else box.y0)
                endpoint = (box.x1 if value > 0 else box.x0) if horizontal else (box.y0 if value > 0 else box.y1)
                origins.append(origin)
                pairs.append((value / normalizer, endpoint))
        zeros = [position for value, position in ticks if value == 0]
        if not origins:
            if any(value != 0 for series in chart["series"] for value in series["values"]):
                # A calibrated fixed/automatic scale may legitimately round
                # every finite value below one canvas pixel.
                fitted = _fit(pairs, direction=1 if horizontal else -1)
                if fitted is None or fitted["scale"] is None:
                    return None
                baseline = fitted["baseline"]
            else:
                baseline = zeros[0] if zeros else None
                fitted = _fit(pairs, direction=1 if horizontal else -1, baseline=baseline)
        else:
            baseline = zeros[0] if zeros else origins[0]
            if any(abs(origin - baseline) > _EPS for origin in origins):
                return None
            fitted = _fit(pairs, direction=1 if horizontal else -1, baseline=baseline)
        if fitted is None:
            return None
        scale = fitted["scale"]
        for s, series in enumerate(chart["series"]):
            for c, value in enumerate(series["values"]):
                if matrix[s][c] is None and value != 0 and (
                        scale is None or abs(scale * (value / normalizer)) > _EPS):
                    return None
        return fitted

    fitted = None
    for layout in product(*assignment):
        matrix = [[None] * category_count for _ in range(series_count)]
        for c, choice in enumerate(layout):
            for s, item in zip(choice, rows[c]):
                matrix[s][c] = item
        fitted = evaluate()
        if fitted is not None:
            break
    if fitted is None:
        raise NativeRejection("bar_common_scale_or_baseline_mismatch")
    offsets = []
    pitches = []
    for s in range(series_count):
        observed = [_mid(item[1].box, horizontal) - centers[c]
                    for c, item in enumerate(matrix[s]) if item is not None]
        offset = observed[0] if observed and max(observed) - min(observed) <= _EPS else None
        offsets.append(offset)
        ordinal = s - (series_count - 1) / 2
        if offset is not None and ordinal and offset / ordinal > 0:
            pitches.append(offset / ordinal)
    pitch = pitches[0] if pitches and max(pitches) - min(pitches) <= _EPS else None
    points, used, colors = [], set(), []
    for s, series in enumerate(chart["series"]):
        observed = []
        for c, value in enumerate(series["values"]):
            item = matrix[s][c]
            origin = fitted["baseline"]
            if item is not None:
                index, shape = item
                used.add(index)
                observed.append(_color(shape))
                coordinate = _mid(shape.box, horizontal)
                endpoint = (shape.box.x1 if value > 0 else shape.box.x0) if horizontal else (
                    shape.box.y0 if value > 0 else shape.box.y1)
            else:
                offset = offsets[s] if offsets[s] is not None else (s - (series_count - 1) / 2) * pitch if pitch is not None else 0
                index, shape, coordinate = None, None, centers[c] + offset
                endpoint = None if origin is None else origin + (1 if horizontal else -1) * (
                    fitted["scale"] or 0) * (value / normalizer)
            point = {"seriesIndex": s, "categoryIndex": c, "value": value,
                           "x": endpoint if horizontal else coordinate,
                           "y": coordinate if horizontal else endpoint, "shapeIndex": index,
                           "box": _box(shape.box) if shape else None, "baseline": origin,
                           "color": _color(shape) if shape else legends.get(s)}
            if item is None:
                point["absentMark"] = True
                point["categoryCoordinateInferred"] = offsets[s] is not None or pitch is not None or series_count == 1
                point["categoryRange"] = [
                    (centers[c - 1] + centers[c]) / 2 if c else centers[0] - (centers[1] - centers[0]) / 2 if category_count > 1 else None,
                    (centers[c] + centers[c + 1]) / 2 if c + 1 < category_count else centers[-1] + (centers[-1] - centers[-2]) / 2 if category_count > 1 else None]
            points.append(point)
        colors.append(observed[0] if observed else legends.get(s))
    return points, colors, fitted, used, unclaimed


def _stack_proof(raw, chart, prims, rows, centers, legends, ticks, normalizer, unclaimed):
    horizontal = chart["chartType"] == "bar"
    percent = chart["grouping"] == "percentStacked"
    count = len(chart["series"])
    if not any(rows) or (percent and any(not row for row in rows)):
        raise NativeRejection("stack_geometry_missing")
    near = lambda shape: shape.box.x0 if horizontal else shape.box.y1
    far = lambda shape: shape.box.x1 if horizontal else shape.box.y0
    length = lambda shape: shape.box.width if horizontal else shape.box.height
    origins = [near(row[0][1]) for row in rows if row]
    baseline = origins[0]
    if any(abs(value - baseline) > _EPS for value in origins):
        raise NativeRejection("stack_baseline_mismatch")
    extents = []
    fractions = []
    for c, row in enumerate(rows):
        if any(abs(_mid(shape.box, horizontal) - centers[c]) > _EPS for _, shape in row):
            raise NativeRejection("stack_category_association")
        if any(abs(far(a[1]) - near(b[1])) > _EPS for a, b in zip(row, row[1:])):
            raise NativeRejection("stack_chain_mismatch")
        extents.append(sum(length(shape) for _, shape in row))
        values = [series["values"][c] for series in chart["series"]]
        if any(value <= 0 for value in values):
            raise NativeRejection("stack_value_contract_invalid")
        local_max = max(values)
        normalized = [value / local_max for value in values]
        total = math.fsum(normalized)
        fractions.append([value / total for value in normalized])
    if percent:
        scale = extents[0]
        if any(abs(extent - scale) > _EPS for extent in extents):
            raise NativeRejection("percent_stack_extent_mismatch")
    else:
        totals = [math.fsum(series["values"][c] / normalizer for series in chart["series"])
                  for c in range(len(centers))]
        reference = max(range(len(totals)), key=totals.__getitem__)
        scale = extents[reference] / totals[reference]
    points, used, colors = [], set(), [[] for _ in range(count)]
    for c, row in enumerate(rows):
        expected = [fractions[c][s] * scale if percent else chart["series"][s]["values"][c] / normalizer * scale
                    for s in range(count)]
        choice = next((choice for choice in combinations(range(count), len(row))
                       if all(_bar_color(raw, legends, s, c, shape) and abs(length(shape) - expected[s]) <= _EPS
                              for s, (_, shape) in zip(choice, row))
                       and all(expected[s] <= _EPS for s in range(count) if s not in choice)), None)
        if choice is None:
            raise NativeRejection("stack_common_scale_or_series_mismatch")
        mapped = dict(zip(choice, row))
        cumulative = 0.0
        for s, series in enumerate(chart["series"]):
            start = baseline + (1 if horizontal else -1) * cumulative
            cumulative += expected[s]
            end = baseline + (1 if horizontal else -1) * cumulative
            item = mapped.get(s)
            if item is not None:
                index, shape = item
                if abs(near(shape) - start) > _EPS or abs(far(shape) - end) > _EPS:
                    raise NativeRejection("stack_cumulative_geometry_mismatch")
                used.add(index)
                colors[s].append(_color(shape))
            else:
                index, shape = None, None
            points.append({"seriesIndex": s, "categoryIndex": c, "value": series["values"][c],
                           "x": end if horizontal else centers[c], "y": centers[c] if horizontal else end,
                           "shapeIndex": index, "box": _box(shape.box) if shape else None,
                           "baseline": start, "zeroBaseline": baseline,
                           "color": _color(shape) if shape else legends.get(s)})
    fitted = {"baseline": baseline, "scale": scale, "degenerate": False, "percentStacked": percent}
    # Labelled automatic axes constrain the same ordinary/percentage scale.
    if ticks:
        denominator = (100 if max(abs(value) for value, _ in ticks) > 1 else 1) if percent else normalizer
        if any(abs(position - (baseline + (1 if horizontal else -1) * scale * (value / denominator))) > _EPS
               for value, position in ticks):
            raise NativeRejection("stack_tick_scale_mismatch")
    return points, [items[0] if items else legends.get(s) for s, items in enumerate(colors)], fitted, used, unclaimed


def _sample_chain(vertices, centers):
    vertices = [point for i, point in enumerate(vertices) if i == 0 or point != vertices[i - 1]]
    if not vertices:
        return None
    if len(centers) == 1:
        if any(abs(x - centers[0]) > _EPS for x, _ in vertices) or max(y for _, y in vertices) - min(
                y for _, y in vertices) > _EPS:
            return None
        return [(centers[0], vertices[0][1])]
    if vertices[0][0] > vertices[-1][0]:
        vertices.reverse()
    if (abs(vertices[0][0] - centers[0]) > _EPS or abs(vertices[-1][0] - centers[-1]) > _EPS
            or any(b[0] <= a[0] for a, b in zip(vertices, vertices[1:]))):
        return None
    data = []
    for center in centers:
        exact = [point for point in vertices if abs(point[0] - center) <= _EPS]
        if len(exact) > 1:
            return None
        if exact:
            data.append((center, exact[0][1]))
            continue
        segment = next(((a, b) for a, b in zip(vertices, vertices[1:]) if a[0] <= center <= b[0]), None)
        if segment is None:
            return None
        a, b = segment
        data.append((center, a[1] + (b[1] - a[1]) * (center - a[0]) / (b[0] - a[0])))
    # Redundant vertices are fine, invented intermediate extrema are not.
    for x, y in vertices:
        if x <= centers[0] or x >= centers[-1]:
            continue
        for a, b in zip(data, data[1:]):
            if a[0] <= x <= b[0]:
                expected = a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0])
                if abs(y - expected) > _EPS:
                    return None
                break
    return data


def _drawn_subpaths(shape):
    """Keep path moves separate; legacy flattened vertices lose real topology."""
    if shape.tag == "path":
        paths = getattr(shape, "subpaths", ())
        closed = getattr(shape, "closedSubpaths", ())
        if len(paths) != len(closed):
            return []
        return [(list(points), bool(flag)) for points, flag in zip(paths, closed)]
    return [(list(shape.vertices), shape.tag == "polygon")]


def _edges(points, closed=False):
    edges = list(zip(points, points[1:]))
    if closed and len(points) > 1 and points[-1] != points[0]:
        edges.append((points[-1], points[0]))
    return edges


def _axis_coverage(shape, *, horizontal, lo, hi, position):
    """A grid's bounding box cannot stand in for its actually drawn span."""
    intervals = []
    for points, closed in _drawn_subpaths(shape):
        for a, b in _edges(points, closed):
            fixed = (a[1], b[1]) if horizontal else (a[0], b[0])
            if any(abs(value - position) > _EPS for value in fixed):
                return False
            varying = (a[0], b[0]) if horizontal else (a[1], b[1])
            intervals.append(tuple(sorted(varying)))
    reach = lo
    for left, right in sorted(intervals):
        if left > reach + _EPS:
            return False
        reach = max(reach, right)
    return bool(intervals) and reach >= hi - _EPS


def _sample_segments(segments, centers, *, missing_baseline=None):
    """Sample actual edges only, then exclude extra slopes or invented extrema."""
    if not segments:
        return ([(x, missing_baseline) for x in centers]
                if missing_baseline is not None else None)
    data = []
    for x in centers:
        samples = []
        for a, b in segments:
            if min(a[0], b[0]) - _EPS <= x <= max(a[0], b[0]) + _EPS:
                if abs(a[0] - b[0]) <= 1e-9:
                    if abs(x - a[0]) <= _EPS:
                        samples.extend((a[1], b[1]))
                else:
                    fraction = min(1, max(0, (x - a[0]) / (b[0] - a[0])))
                    samples.append(a[1] + (b[1] - a[1]) * fraction)
        if not samples:
            if missing_baseline is None:
                return None
            samples = [missing_baseline]
        if max(samples) - min(samples) > _EPS:
            return None
        data.append((x, sum(samples) / len(samples)))
    for a, b in segments:
        # Segment endpoints alone are insufficient when an edge crosses a
        # category bend. Every crossed anchor must lie on the real edge too.
        if abs(a[0] - b[0]) <= 1e-9 and abs(a[1] - b[1]) > _EPS:
            return None
        probes = [a, b]
        if a[0] != b[0]:
            probes += [(x, a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0]))
                       for x in centers if min(a[0], b[0]) < x < max(a[0], b[0])]
        for x, y in probes:
            if x < centers[0] - _EPS or x > centers[-1] + _EPS:
                return None
            if len(data) == 1:
                expected = data[0][1]
            else:
                pair = next(((p, q) for p, q in zip(data, data[1:]) if p[0] - _EPS <= x <= q[0] + _EPS), None)
                if pair is None:
                    return None
                p, q = pair
                expected = p[1] + (q[1] - p[1]) * (x - p[0]) / (q[0] - p[0])
            if abs(y - expected) > _EPS:
                return None
    return data


def _area_contour_options(points, closed):
    edges = _edges(points, closed)
    if not edges:
        return []  # A move-only subpath has neither a boundary nor a baseline.
    lo, hi = min(x for x, _ in points), max(x for x, _ in points)
    levels = sorted({y for _, y in points})
    if hi - lo <= _EPS:
        # A redundant vertex on a singleton's vertical extent cannot become
        # its data endpoint. Calibrated axes must see the actual full extent.
        ends = [(levels[0], levels[-1]), (levels[-1], levels[0])]
        return [([], baseline, [(lo, top)]) for baseline, top in dict.fromkeys(ends)]
    options = []
    for baseline in levels:
        flat = sorted((min(a[0], b[0]), max(a[0], b[0])) for a, b in edges
                      if abs(a[1] - baseline) <= _EPS and abs(b[1] - baseline) <= _EPS)
        reach = lo
        for left, right in flat:
            if left > reach + _EPS:
                break
            reach = max(reach, right)
        if reach < hi - _EPS:
            continue
        top = [(a, b) for a, b in edges if not (
            abs(a[1] - baseline) <= _EPS and abs(b[1] - baseline) <= _EPS) and not (
            abs(a[0] - b[0]) <= _EPS and (abs(a[0] - lo) <= _EPS or abs(a[0] - hi) <= _EPS))]
        # An area contour may split at a zero crossing. Each independent
        # contour still needs its own actual full-width zero-baseline edge.
        knots = sorted({lo, hi, *(x for edge in top for x, _ in edge)})
        sampled = _sample_segments(top, knots, missing_baseline=baseline)
        if sampled is not None:
            options.append((top, baseline, sampled))
    return options


def _area_domain_covered(segments, data, baseline):
    """Separate nonzero islands cannot substitute for the area between them."""
    if len(data) < 2:
        return True
    intervals = sorted((min(a[0], b[0]), max(a[0], b[0])) for a, b in segments)
    reach, gaps = data[0][0], []
    for left, right in intervals:
        if left > reach + _EPS:
            gaps.append((reach, left))
        reach = max(reach, right)
    if reach < data[-1][0] - _EPS:
        gaps.append((reach, data[-1][0]))
    for left, right in gaps:
        probes = [left, right, *(x for x, _ in data if left < x < right)]
        for x in probes:
            pair = next(((a, b) for a, b in zip(data, data[1:]) if a[0] - _EPS <= x <= b[0] + _EPS), None)
            if pair is None:
                return False
            a, b = pair
            y = a[1] + (b[1] - a[1]) * (x - a[0]) / (b[0] - a[0])
            if abs(y - baseline) > _EPS:
                return False
    return True


def _curve_options(shape, centers, area=False):
    paths = _drawn_subpaths(shape)
    if not area:
        segments = [edge for points, closed in paths for edge in _edges(points, closed)]
        if (not segments and shape.tag != "path" and len(centers) == 1
                and len(shape.vertices) == 1 and abs(shape.vertices[0][0] - centers[0]) <= _EPS):
            # Existing singleton line charts may have no segment or marker.
            # Preserve their coordinate anchor, without claiming stroke ink.
            return [([(centers[0], shape.vertices[0][1])], None)]
        data = _sample_segments(segments, centers)
        return [(data, None)] if data is not None else []
    contours = []
    for points, closed in paths:
        # SVG fills close each contour implicitly. Outline-only paths retain
        # their actual closure rather than borrowing a missing closing edge.
        options = _area_contour_options(points, closed or bool(shape.fill and shape.fill_alpha > 0))
        if options:
            contours.append(options)
        elif len(points) > 1:
            return []
    if not contours:
        return []
    result = []
    for selection in product(*contours):
        baseline = selection[0][1]
        if any(abs(option[1] - baseline) > _EPS for option in selection):
            continue
        segments = [edge for option in selection for edge in option[0]]
        if len(centers) == 1 and all(not option[0] for option in selection):
            tops = [point for option in selection for point in option[2]]
            if (tops and all(abs(x - centers[0]) <= _EPS for x, _ in tops)
                    and max(y for _, y in tops) - min(y for _, y in tops) <= _EPS):
                result.append(([(centers[0], tops[0][1])], baseline))
            continue
        data = _sample_segments(segments, centers, missing_baseline=baseline)
        if data is not None and _area_domain_covered(segments, data, baseline):
            result.append((data, baseline))
    return result


def _curve(shape, centers, area=False):
    options = _curve_options(shape, centers, area)
    return options[0] if options else None


def _curve_proof(raw, chart, prims, centers, legends, legend_indices, ticks, normalizer):
    area = chart["chartType"] == "area"
    candidates, marker_indices, unclaimed = [], [], set()
    for index, shape in enumerate(prims.shapes):
        if index in legend_indices:
            continue
        if (shape.tag in {"circle", "ellipse"} and _color(shape)
                and shape.box.width > 0 and shape.box.height > 0):
            marker_indices.append(index)
            continue
        eligible = (shape.tag in {"path", "polygon", "polyline"} and _color(shape)
                    if area else shape.tag in {"path", "polyline", "line"} and shape.stroke and shape.stroke_alpha > 0)
        parsed = _curve_options(shape, centers, area) if eligible else []
        if parsed:
            candidates.extend((index, shape, *option) for option in parsed)
        elif eligible:
            unclaimed.add(index)
    if not candidates and len(centers) == 1 and not area:
        marker_indices.extend(index for index in legend_indices if prims.shapes[index].tag in {"circle", "ellipse"}
                              and abs((prims.shapes[index].box.x0 + prims.shapes[index].box.x1) / 2 - centers[0]) <= _EPS)
        for index in marker_indices:
            shape = prims.shapes[index]
            x, y = (shape.box.x0 + shape.box.x1) / 2, (shape.box.y0 + shape.box.y1) / 2
            if abs(x - centers[0]) <= _EPS:
                candidates.append((index, shape, [(x, y)], None))
    series_count = len(chart["series"])
    if len(candidates) < len({tuple(series["values"]) for series in chart["series"]}):
        raise NativeRejection("curve_geometry_missing")
    assigned = [None] * series_count
    tick_pairs = [(value / normalizer, position) for value, position in ticks]
    direct = []
    for series in chart["series"]:
        matches = {}
        for index, _, data, _ in candidates:
            labels = []
            for text_index, text in enumerate(prims.texts):
                if normalize_text(" ".join(text.lines)) != normalize_text(series["name"]):
                    continue
                box = text.box()
                center_y = (box.y0 + box.y1) / 2
                left, right = data[0], data[-1]
                near_left = 0 <= left[0] - box.x1 <= max(32, text.size * 2) and abs(center_y - left[1]) <= text.size * .6
                near_right = 0 <= box.x0 - right[0] <= max(32, text.size * 2) and abs(center_y - right[1]) <= text.size * .6
                if near_left or near_right:
                    labels.append(text_index)
            if labels:
                matches[(index, tuple(data))] = labels
        direct.append(matches)

    allowed = []
    for s in range(series_count):
        choices = []
        for item in candidates:
            index, shape, data, baseline = item
            if direct[s] and (index, tuple(data)) not in direct[s]:
                continue
            color = _color(shape) if area else shape.stroke or shape.fill
            if s in legends and legends[s] != color:
                continue
            pairs = [(value / normalizer, point[1]) for value, point in zip(chart["series"][s]["values"], data)]
            if _fit([*tick_pairs, *pairs], baseline=baseline if area else None) is None:
                continue
            choices.append(item)
        allowed.append(choices)
    # Iterative matching avoids imposing Python's recursion limit on a valid
    # chart's category/series counts. One shared affine map validates a layout.
    choice_indices, occupied, s, fitted = [0] * series_count, {}, 0, None
    ordered = [list(items) for items in allowed]

    def release(index):
        occupied[index] -= 1
        if occupied[index] == 0:
            del occupied[index]
    while s >= 0:
        if s == series_count:
            baseline = assigned[0][3] if area else None
            consistent = not area or all(abs(item[3] - baseline) <= _EPS for item in assigned)
            if consistent:
                pairs = list(tick_pairs)
                for series, item in zip(chart["series"], assigned):
                    pairs.extend((value / normalizer, point[1]) for value, point in zip(series["values"], item[2]))
                fitted = _fit(pairs, baseline=baseline)
            if fitted is not None:
                break
            s -= 1
            release(assigned[s][0])
            continue
        while choice_indices[s] < len(ordered[s]):
            item = ordered[s][choice_indices[s]]
            choice_indices[s] += 1
            if item[0] in occupied and not any(
                    assigned[earlier][0] == item[0]
                    and chart["series"][earlier]["values"] == chart["series"][s]["values"]
                    for earlier in range(s)):
                continue
            assigned[s] = item
            occupied[item[0]] = occupied.get(item[0], 0) + 1
            s += 1
            if s < series_count:
                choice_indices[s] = 0
                ordered[s] = sorted(allowed[s], key=lambda candidate: candidate[0] in occupied)
            break
        else:
            choice_indices[s] = 0
            assigned[s] = None
            s -= 1
            if s >= 0:
                release(assigned[s][0])
    if fitted is None:
        raise NativeRejection("curve_common_affine_or_series_mismatch")
    used = {item[0] for item in assigned}
    for item in candidates:
        if item[0] not in used:
            unclaimed.add(item[0])
    points, colors = [], []
    marker_used, marker_values = set(), {}
    for s, (series, item) in enumerate(zip(chart["series"], assigned)):
        index, shape, data, _ = item
        colors.append(_color(shape) if area else shape.stroke or shape.fill)
        for c, (value, (x, y)) in enumerate(zip(series["values"], data)):
            point = {"seriesIndex": s, "categoryIndex": c, "value": value, "x": x, "y": y,
                     "shapeIndex": index, "box": _box(shape.box), "baseline": fitted["baseline"],
                     "color": colors[-1], "seriesBinding": "visible-legend" if s in legends else
                     "direct-end-label" if len(direct[s]) == 1 else "common-affine-pattern"}
            if not area and shape.tag in {"line", "polyline", "path"} and not any(
                    _edges(path, closed) for path, closed in _drawn_subpaths(shape)):
                point["geometryAnchorOnly"] = True
            if len(direct[s]) == 1:
                point["seriesLabelTextIndices"] = direct[s][(index, tuple(data))]
            markers = [m for m in marker_indices if (m not in marker_used or marker_values.get(m) == (c, value))
                       and abs((prims.shapes[m].box.x0 + prims.shapes[m].box.x1) / 2 - x) <= _EPS
                       and abs((prims.shapes[m].box.y0 + prims.shapes[m].box.y1) / 2 - y) <= _EPS
                       and (_point_color(raw, s, c) is None
                            or _color(prims.shapes[m]) == _point_color(raw, s, c))]
            markers.sort(key=lambda marker: marker != index)
            if markers:
                point["markerShapeIndex"] = markers[0]
                marker_used.add(markers[0])
                marker_values[markers[0]] = (c, value)
                used.add(markers[0])
            elif raw.get("markers") is True:
                raise NativeRejection("marker_geometry_missing")
            points.append(point)
    if any(index not in marker_used and index not in used for index in marker_indices):
        raise NativeRejection("extra_marker_geometry")
    if area:
        # An authored area may use a separate stroked boundary over its fill.
        for index, shape in enumerate(prims.shapes):
            if index in used or shape.fill is not None or not shape.stroke:
                continue
            parsed = _curve(shape, centers)
            if parsed is not None and any(all(abs(a[1] - b[1]) <= _EPS for a, b in zip(parsed[0], item[2]))
                                          for item in assigned):
                used.add(index)
                unclaimed.discard(index)
    return points, colors, fitted, used, unclaimed


def _axis_furniture(prims, centers, horizontal, baseline, used):
    result = set()
    span = centers[-1] - centers[0]
    for index, shape in enumerate(prims.shapes):
        if index in used or shape.tag not in {"line", "path", "polyline"} or not shape.stroke:
            continue
        box = shape.box
        vertical, flat = box.width <= _EPS, box.height <= _EPS
        if horizontal:
            category_axis = flat and box.y0 > centers[-1] + _EPS
            grid = vertical and box.y0 <= centers[0] + _EPS and box.y1 >= centers[-1] - _EPS
            grid = grid and (box.height > span + 2 * _EPS or baseline is not None and abs(box.x0 - baseline) <= _EPS)
        else:
            category_axis = vertical and box.x0 < centers[0] - _EPS
            grid = flat and box.x0 <= centers[0] + _EPS and box.x1 >= centers[-1] - _EPS
            grid = grid and (box.width > span + 2 * _EPS or baseline is not None and abs(box.y0 - baseline) <= _EPS)
        if category_axis or grid:
            if _axis_coverage(shape, horizontal=flat, lo=box.x0 if flat else box.y0,
                              hi=box.x1 if flat else box.y1, position=box.y0 if flat else box.x0):
                result.add(index)
    return result


def prove_cartesian(raw: dict, chart: dict, prims: GroupPrimitives, *, canvas_texts=()) -> dict:
    """Prove the existing Cartesian data contract against final SVG primitives.

    ``shapeIndex`` always addresses the original ``prims.shapes`` sequence.
    Absent zero/subpixel bars have no shape, and all-zero no-ink charts expose
    an unknown baseline rather than inventing one. Value-axis normalizers keep
    finite large/subnormal data safe during common-scale arithmetic.
    """
    try:
        chart_type = chart["chartType"]
        if chart_type not in {"bar", "column", "line", "area"}:
            return _fail("cartesian_type_invalid")
        expected_order = "topToBottom" if chart_type == "bar" else "leftToRight"
        if raw.get("categoryOrder", expected_order) != expected_order:
            return _fail("category_order_mismatch")
        if (not chart["categories"] or not chart["series"] or any(
                len(series["values"]) != len(chart["categories"])
                or any(type(value) not in (int, float) or not math.isfinite(value) for value in series["values"])
                for series in chart["series"])):
            return _fail("data_contract_invalid")
        for shape in prims.shapes:
            if not all(math.isfinite(value) for value in (shape.box.x0, shape.box.y0, shape.box.x1, shape.box.y1,
                                                         *(value for point in shape.vertices for value in point),
                                                         *(value for path in getattr(shape, "subpaths", ())
                                                           for point in path for value in point))):
                return _fail("nonfinite_geometry")
        categories, centers = _categories(chart, prims)
        legends, legend_indices, legend_positions, legend_texts = _legend(chart, prims)
        legend_order = sorted(legend_positions, key=lambda s: (legend_positions[s][1], legend_positions[s][0]))
        if legend_positions and max(y for _, y in legend_positions.values()) - min(
                y for _, y in legend_positions.values()) <= _EPS:
            legend_order.sort(key=lambda s: legend_positions[s][0])
        if chart_type in {"bar", "column"} and len(legends) > 1:
            palettes = [{paint(color) for color in series.get("pointColors", [])}
                        for series in raw["series"]]
            ambiguous = len(set(legends.values())) < len(legends) or any(
                len(palettes[s]) > 1 and any(color in palettes[s] for other, color in legends.items() if other != s)
                for s in legends)
            equivalent = all(chart["series"][s]["values"] == chart["series"][legend_order[0]]["values"]
                             for s in legend_order)
            if ambiguous and not equivalent and legend_order != sorted(legends):
                return _fail("legend_series_order_ambiguous")
        ticks, grid_indices, tick_texts, canvas_tick_texts, tick_bindings = _ticks(chart, prims, centers, canvas_texts)
        fixed_ticks, fixed_grid, fixed_texts, fixed_canvas, title_texts, canvas_title_texts, fixed_bindings = _fixed_axis(
            raw, chart, prims, canvas_texts)
        if fixed_ticks:
            ticks = fixed_ticks
            grid_indices |= fixed_grid
            tick_texts |= fixed_texts
            canvas_tick_texts |= fixed_canvas
            tick_bindings = fixed_bindings
        normalizer = _normalizer(chart, ticks)
        proof = _bar_proof if chart_type in {"bar", "column"} else _curve_proof
        points, colors, fitted, used, unclaimed = proof(
            raw, chart, prims, centers, legends, legend_indices, ticks, normalizer)
        # Axes/grid furniture is separate from actual datum/marker shapes.
        grid_indices |= _axis_furniture(prims, centers, chart_type == "bar", fitted["baseline"], used)
        unclaimed -= grid_indices
        if unclaimed and chart_type in {"line", "area"}:
            return {**_fail("unaccounted_data_geometry"), "unclaimedShapeIndices": sorted(unclaimed)}
        known = used | legend_indices | grid_indices
        return {"passed": True, "reason": "chart_content_common_cartesian_geometry",
                "points": points, "seriesColors": colors, "baseline": fitted["baseline"],
                "valueScale": {"normalizer": normalizer, "normalizedScale": fitted["scale"]},
                "degenerate": fitted["degenerate"], "calibrated": len(ticks) >= 2,
                "categoryTextIndices": [index for index, _ in categories],
                "seriesBindings": ["visible-legend" if s in legends else
                                   "spatial-cluster-order" if chart_type in {"bar", "column"} else
                                   next(point["seriesBinding"] for point in points if point["seriesIndex"] == s)
                                   for s in range(len(chart["series"]))],
                "seriesTextIndices": [sorted(set(legend_texts.get(s, []) + [index
                                      for point in points if point["seriesIndex"] == s
                                      for index in point.get("seriesLabelTextIndices", [])]))
                                      for s in range(len(chart["series"]))],
                "visibleLegendOrder": legend_order,
                "canvasTextIndices": sorted(canvas_tick_texts | canvas_title_texts),
                "canvasTickTextIndices": sorted(canvas_tick_texts),
                "axisTitleTextIndices": sorted(title_texts),
                "canvasAxisTitleTextIndices": sorted(canvas_title_texts),
                "tickSourceBindings": tick_bindings,
                "tickTextIndices": sorted(tick_texts), "usedShapeIndices": sorted(used),
                "furnitureShapeIndices": sorted((legend_indices | grid_indices) - used),
                "unclaimedShapeIndices": [index for index in range(len(prims.shapes)) if index not in known]}
    except (NativeRejection, KeyError, TypeError, ValueError, OverflowError, ZeroDivisionError) as error:
        return _fail(str(error) if isinstance(error, NativeRejection) else "geometry_unproven")
