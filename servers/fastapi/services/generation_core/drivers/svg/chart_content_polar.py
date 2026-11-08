"""Data geometry of the existing single-series SVG pie/donut contract.

This proof consumes canonical linear SVG primitives; it does not render SVG or
change native projection. Angular partitions, not numeric text counters, bind
each category to its share. Zero parts have no invented painted extent.
"""
from __future__ import annotations

import math
from fractions import Fraction

from services.generation_core.drivers.svg.native_geometry import normalize_text

TAU = 2 * math.pi


def _linear_fill_paths(shape):
    paths = getattr(shape, "subpaths", ()) or (shape.vertices,)
    result = []
    for path in paths:
        points = []
        for x, y in path:
            point = (Fraction(x), Fraction(y))
            if not points or point != points[-1]:
                points.append(point)
        if len(points) > 1 and points[0] == points[-1]:
            points.pop()
        if len(points) < 3:
            if len(points) > 1:
                return None  # An extra drawn chord is not a sector boundary.
            continue
        result.append(tuple(points))
    return result if 0 < len(result) <= 2 else None


def _fill_edges(paths):
    return [(a, b) for path in paths for a, b in zip(path, (*path[1:], path[0]), strict=True) if a != b]


def _orientation(a, b, point):
    return (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])


def _proper_cross(first, second):
    a, b = first
    c, d = second
    return _orientation(a, b, c) * _orientation(a, b, d) < 0 and _orientation(c, d, a) * _orientation(c, d, b) < 0


def _filled_intervals(edges, x):
    """Exact nonzero-winding intervals in an open vertex-free vertical slab."""
    events = {}
    for a, b in edges:
        if not min(a[0], b[0]) < x < max(a[0], b[0]):
            continue
        y = a[1] + (x - a[0]) * (b[1] - a[1]) / (b[0] - a[0])
        events[y] = events.get(y, 0) + (1 if b[0] > a[0] else -1)
    winding, previous, intervals = 0, None, []
    for y, delta in sorted(events.items()):
        if winding and previous is not None and previous < y:
            intervals.append((previous, y))
        winding += delta
        previous = y
    return intervals


def _disjoint_fill_interiors(first, second):
    # A proper boundary crossing cannot satisfy this typed sector contract.
    # Without crossings, winding interval order changes only at vertex x's;
    # one exact slab representative therefore proves every open slab.
    if any(_proper_cross(a, b) for a in first for b in second):
        return False
    xs = sorted({point[0] for edge in (*first, *second) for point in edge})
    for left, right in zip(xs, xs[1:]):
        x = (left + right) / 2
        a, b = _filled_intervals(first, x), _filled_intervals(second, x)
        if any(max(lo, other_lo) < min(hi, other_hi) for lo, hi in a for other_lo, other_hi in b):
            return False
    return True


def _infinitesimal_winding(edges, point, normal, side):
    """Winding immediately beside an edge, without a finite sampling epsilon."""
    def sign(constant, coefficient):
        value = constant if constant else coefficient
        return (value > 0) - (value < 0)
    winding = 0
    for a, b in edges:
        ay = sign(a[1] - point[1], -side * normal[1])
        by = sign(b[1] - point[1], -side * normal[1])
        turn = sign(_orientation(a, b, point), side * ((b[0] - a[0]) * normal[1] - (b[1] - a[1]) * normal[0]))
        if ay <= 0 < by and turn > 0:
            winding += 1
        elif by <= 0 < ay and turn < 0:
            winding -= 1
    return winding != 0


def _shared_radial_fill_boundaries(first, second, geometry):
    cx, cy = map(Fraction, geometry["center"])
    rx, ry = geometry["radii"]
    tolerance = max(1e-4, .05 / min(rx, ry))  # Existing polar radial rounding contract.
    vertices = {point for edge in (*first, *second) for point in edge}
    result = set()
    for a, b in first:
        delta = (b[0] - a[0], b[1] - a[1])
        dimension = 0 if abs(delta[0]) >= abs(delta[1]) else 1
        for c, d in second:
            if _orientation(a, b, c) or _orientation(a, b, d):
                continue
            positions = sorted(((c[dimension] - a[dimension]) / delta[dimension],
                                (d[dimension] - a[dimension]) / delta[dimension]))
            lo, hi = max(Fraction(0), positions[0]), min(Fraction(1), positions[1])
            if lo >= hi:
                continue
            cuts = {lo, hi}
            for point in vertices:
                if _orientation(a, b, point) == 0:
                    amount = (point[dimension] - a[dimension]) / delta[dimension]
                    if lo < amount < hi:
                        cuts.add(amount)
            center_projection = ((cx - a[0]) * delta[0] + (cy - a[1]) * delta[1]) / (delta[0] ** 2 + delta[1] ** 2)
            if lo < center_projection < hi:
                cuts.add(center_projection)
            cuts = sorted(cuts)
            for start, end in zip(cuts, cuts[1:]):
                p, q = [(a[0] + amount * delta[0], a[1] + amount * delta[1]) for amount in (start, end)]
                midpoint, normal = ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2), (-delta[1], delta[0])
                left_a, right_a = [_infinitesimal_winding(first, midpoint, normal, side) for side in (1, -1)]
                left_b, right_b = [_infinitesimal_winding(second, midpoint, normal, side) for side in (1, -1)]
                if not (left_a != right_a and left_b != right_b and left_a != left_b):
                    continue
                normalized = [(float((point[0] - cx) / Fraction(rx)), float((point[1] - cy) / Fraction(ry))) for point in (p, q)]
                (px, py), (qx, qy) = normalized
                if abs(px * qy - py * qx) > tolerance or px * qx + py * qy < -tolerance ** 2:
                    continue
                if max(math.hypot(*point) for point in normalized) <= geometry.get("holeRatio", 0) + tolerance:
                    continue
                result.add(tuple(sorted((p, q))))
    return [[[float(value) for value in point] for point in segment] for segment in sorted(result)]


def prove_polar_fill_adjacencies(prims, geometry):
    """Prove actual disjoint sector fills sharing a positive radial segment.

    This follows successful whole-chart partition proof. Exact rational planar
    topology supplies the ownership precondition; angle or center proximity
    alone never gives another sector permission to cover native data.
    """
    if geometry.get("passed") is not True or geometry.get("reason") != "polar_partition":
        return []
    candidates = []
    ellipse = (*geometry["center"], *geometry["radii"])
    points = [point for point in geometry.get("points", []) if point.get("shapeIndex") is not None]
    for point in points:
        shape = prims.shapes[point["shapeIndex"]]
        if shape.tag not in {"path", "polygon"} or not shape.fill or shape.fill_alpha <= 0:
            continue
        paths = _linear_fill_paths(shape)
        if paths is None or _arc(shape, ellipse, full=len(points) == 1) is None:
            continue
        edges = _fill_edges(paths)
        if any(_proper_cross(a, b) for index, a in enumerate(edges) for b in edges[index + 1:]):
            continue
        candidates.append((point, edges))
    result = []
    for index, (point, edges) in enumerate(candidates):
        for other, other_edges in candidates[index + 1:]:
            if point["shapeIndex"] == other["shapeIndex"] or not _disjoint_fill_interiors(edges, other_edges):
                continue
            shared = _shared_radial_fill_boundaries(edges, other_edges, geometry)
            if shared:
                result.append({"shapeIndices": [point["shapeIndex"], other["shapeIndex"]],
                    "categoryIndices": [point["categoryIndex"], other["categoryIndex"]],
                    "sharedRadialSegments": shared, "fillInteriorsDisjoint": True,
                    "contourTopologyChecked": True, "center": geometry["center"], "radii": geometry["radii"],
                    "holeRatio": geometry.get("holeRatio", 0)})
    return result


def _hull(points):
    points = sorted(set(points))
    if len(points) < 3:
        return points
    def cross(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    lower, upper = [], []
    for point in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    for point in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def _solve(matrix, right):
    rows = [list(row) + [value] for row, value in zip(matrix, right, strict=True)]
    for column in range(len(rows)):
        pivot = max(range(column, len(rows)), key=lambda i: abs(rows[i][column]))
        if abs(rows[pivot][column]) <= 1e-12:
            return None
        rows[column], rows[pivot] = rows[pivot], rows[column]
        divisor = rows[column][column]
        rows[column] = [value / divisor for value in rows[column]]
        for index in range(len(rows)):
            if index != column:
                factor = rows[index][column]
                rows[index] = [a - factor * b for a, b in zip(rows[index], rows[column], strict=True)]
    return [row[-1] for row in rows]


def _ellipse(points):
    """Fit the common axis-aligned outer ellipse from its convex hull."""
    hull = _hull(points)
    if len(hull) < 3:
        return None
    x0, x1 = min(x for x, _ in hull), max(x for x, _ in hull)
    y0, y1 = min(y for _, y in hull), max(y for _, y in hull)
    scale = max(x1 - x0, y1 - y0)
    if scale <= 0 or not math.isfinite(scale):
        return None
    ox, oy = (x0 + x1) / 2, (y0 + y1) / 2
    rows = [(x * x, y * y, x, y) for px, py in hull
            for x, y in [((px - ox) / scale, (py - oy) / scale)]]
    matrix = [[sum(row[a] * row[b] for row in rows) for b in range(4)] for a in range(4)]
    fitted = _solve(matrix, [sum(row[a] for row in rows) for a in range(4)])
    if fitted is None:
        # Three circular samples still define a circle; no extra chart family.
        rows3 = [(2 * (x - ox) / scale, 2 * (y - oy) / scale, 1) for x, y in hull]
        fitted3 = _solve([[sum(row[a] * row[b] for row in rows3) for b in range(3)] for a in range(3)],
                         [sum(row[a] * ((row[0] / 2) ** 2 + (row[1] / 2) ** 2) for row in rows3)
                          for a in range(3)])
        if fitted3 is None:
            return None
        cx, cy, constant = fitted3
        radius2 = constant + cx * cx + cy * cy
        if radius2 <= 0:
            return None
        return ox + cx * scale, oy + cy * scale, math.sqrt(radius2) * scale, math.sqrt(radius2) * scale
    a, b, c, d = fitted
    if a <= 0 or b <= 0:
        return None
    cx, cy = -c / (2 * a), -d / (2 * b)
    constant = 1 + a * cx * cx + b * cy * cy
    if constant <= 0:
        return None
    ellipse = (ox + cx * scale, oy + cy * scale, math.sqrt(constant / a) * scale, math.sqrt(constant / b) * scale)
    cx, cy, rx, ry = ellipse
    tolerance = max(1e-4, .05 / min(rx, ry))
    if any(abs(math.hypot((x - cx) / rx, (y - cy) / ry) - 1) > tolerance for x, y in hull):
        return None
    return ellipse


def _arc(shape, ellipse, *, full=False):
    cx, cy, rx, ry = ellipse
    points = [((x - cx) / rx, (y - cy) / ry) for x, y in shape.vertices]
    tolerance = max(1e-4, .05 / min(rx, ry))
    outer = [(index, math.atan2(y, x) % TAU) for index, (x, y) in enumerate(points)
             if abs(math.hypot(x, y) - 1) <= tolerance]
    if len(outer) < 2:
        return None
    angles = [angle for _, angle in outer]
    deltas = [(b - a + math.pi) % TAU - math.pi for a, b in zip(angles, angles[1:])]
    nonzero = [value for value in deltas if abs(value) > 1e-10]
    if nonzero and not (all(value > 0 for value in nonzero) or all(value < 0 for value in nonzero)):
        return None
    if any(max(rx, ry) * (1 - math.cos(abs(delta) / 2)) > .5 for delta in deltas):
        return None
    if full:
        closing = (angles[0] - angles[-1] + math.pi) % TAU - math.pi
        cycle = [*deltas, closing]
        if abs(abs(sum(cycle)) - TAU) > tolerance:
            return None
        # The admitted linear path approximates a curved polar boundary. Its
        # chord error must stay within a half rendered SVG pixel, including a
        # singleton; an arbitrary triangle is not a full circular partition.
        if any(max(rx, ry) * (1 - math.cos(abs(delta) / 2)) > .5 for delta in cycle):
            return None
    span = abs(sum([*deltas, closing])) if full else abs(sum(deltas))
    if not 0 < span <= TAU + tolerance:
        return None
    start = (angles[0] if sum(deltas) >= 0 else angles[-1]) % TAU
    inner = [math.hypot(x, y) for x, y in points if math.hypot(x, y) < 1 - tolerance]
    hole = min(inner, default=0.0)
    if not full and hole <= tolerance:
        has_center = any(math.hypot(x, y) <= tolerance for x, y in points)
        # A semicircle may close along its diameter without spelling its
        # midpoint. Every other proper pie sector needs its radial center.
        if not has_center and abs(span - math.pi) > tolerance:
            return None
    elif hole > tolerance:
        inner_angles = [math.atan2(y, x) for x, y in points if abs(math.hypot(x, y) - hole) <= tolerance]
        inner_deltas = [(b - a + math.pi) % TAU - math.pi for a, b in zip(inner_angles, inner_angles[1:])]
        if len(inner_angles) < 2 or (not full and abs(abs(sum(inner_deltas)) - span) > tolerance):
            return None
        if any(max(rx, ry) * hole * (1 - math.cos(abs(delta) / 2)) > .5 for delta in inner_deltas):
            return None
        if sum(inner_deltas) * sum(deltas) > 0:
            return None
    # Every edge follows a common ring or a radial boundary. Interior chords,
    # disconnected islands and differently scaled wedges are not data sectors.
    for index, (x, y) in enumerate(points):
        radius, angle = math.hypot(x, y), math.atan2(y, x) % TAU
        on_radius = abs(radius - 1) <= tolerance or abs(radius - hole) <= tolerance
        on_boundary = radius <= tolerance or min((angle - start) % TAU, (start - angle) % TAU,
            (angle - start - span) % TAU, (start + span - angle) % TAU) <= tolerance
        if not on_radius and not on_boundary:
            return None
    paths = getattr(shape, "subpaths", ()) or (shape.vertices,)
    normalized_paths = [tuple(((x - cx) / rx, (y - cy) / ry) for x, y in path) for path in paths]
    for path in normalized_paths:
        outer_on_path = [(x, y) for x, y in path if abs(math.hypot(x, y) - 1) <= tolerance]
        if (not full and hole <= tolerance and len(outer_on_path) >= 2
                and not any(math.hypot(x, y) <= tolerance for x, y in path)
                and abs(span - math.pi) > tolerance):
            return None
    for (x, y), (nx, ny) in [(a, b) for path in normalized_paths for a, b in zip(path, (*path[1:], path[0]), strict=True)]:
        radius = math.hypot(x, y)
        nr = math.hypot(nx, ny)
        if abs(radius - nr) <= tolerance and radius > tolerance:
            delta = abs((math.atan2(ny, nx) - math.atan2(y, x) + math.pi) % TAU - math.pi)
            diameter = not full and hole <= tolerance and abs(span - math.pi) <= tolerance and abs(delta - math.pi) <= tolerance
            if not diameter and max(rx, ry) * radius * (1 - math.cos(delta / 2)) > .5:
                return None
        if abs(radius - nr) > tolerance and radius > tolerance and nr > tolerance:
            if abs(x * ny - y * nx) > tolerance:
                return None
    actual_perimeter = math.fsum(math.hypot(b[0] - a[0], b[1] - a[1])
        for path in paths for a, b in zip(path, (*path[1:], path[0]), strict=True))
    expected_area = rx * ry * span * (1 - hole * hole) / 2
    if abs(shape.area - expected_area) > actual_perimeter * .5 + 1e-6:
        return None
    return {"start": start, "span": span, "hole": hole}


def _legend_color(label, prims):
    box = label.box()
    candidates = [shape for shape in prims.shapes if shape.fill and shape.tag in {"rect", "circle", "ellipse"}
                  and 0 < shape.box.width <= label.size * 2 and 0 < shape.box.height <= label.size * 2
                  and shape.box.x1 <= box.x0 and box.x0 - shape.box.x1 <= label.size * 2
                  and abs((shape.box.y0 + shape.box.y1 - box.y0 - box.y1) / 2) <= label.size]
    return candidates[0].fill if len(candidates) == 1 else None


def prove_polar(raw: dict, chart: dict, prims) -> dict:
    """Prove one existing pie/donut data partition, returning datum witnesses."""
    def reject(reason):
        return {"passed": False, "reason": reason}
    if chart.get("chartType") not in {"pie", "donut"} or len(chart.get("series", [])) != 1:
        return reject("native_chart_polar_series_invalid")
    values = chart["series"][0]["values"]
    if not values or any(not math.isfinite(value) or value < 0 for value in values) or max(values) <= 0:
        return reject("native_chart_polar_values_invalid")
    maximum = max(values)
    scaled = [value / maximum for value in values]
    total = math.fsum(scaled)
    shares = [value / total for value in scaled]
    positive = [index for index, share in enumerate(shares) if share > 0]
    indexed = [(index, shape) for index, shape in enumerate(prims.shapes)
               if shape.tag in {"path", "polygon"} and shape.fill and shape.fill_alpha > 0 and shape.area > 0]
    if len(positive) == 1 and not indexed:
        circles = [(index, shape) for index, shape in enumerate(prims.shapes)
                   if shape.tag in {"circle", "ellipse"} and
                   ((shape.fill and shape.fill_alpha > 0) or (chart["chartType"] == "donut"
                    and shape.stroke and shape.stroke_alpha > 0 and shape.stroke_width > 0))]
        singleton = circles[0] if len(circles) == 1 else None
        hole_index = None
        hole_ratio = 0.0
        if chart["chartType"] == "donut" and len(circles) == 2:
            (outer_index, outer), (inner_index, inner) = circles
            outer_box, inner_box = outer.box, inner.box
            ratios = (inner_box.width / outer_box.width, inner_box.height / outer_box.height)
            if (outer.fill and inner.fill and inner.fill_alpha == 1 and inner_index > outer_index
                    and not (outer.fill_alpha == 1 and inner.fill == outer.fill)
                    and abs(outer_box.x0 + outer_box.x1 - inner_box.x0 - inner_box.x1) <= .2
                    and abs(outer_box.y0 + outer_box.y1 - inner_box.y0 - inner_box.y1) <= .2
                    and 0 < min(ratios) <= max(ratios) < 1 and abs(ratios[0] - ratios[1]) <= 1e-6):
                singleton, hole_index, hole_ratio = (outer_index, outer), inner_index, ratios[0]
        if singleton is not None:
            index, shape = singleton
            box = shape.box
            cx, cy = (box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2
            rx, ry = box.width / 2, box.height / 2
            anchor_radius = rx * (1 + hole_ratio) / 2
            inner_radii = [rx * hole_ratio, ry * hole_ratio]
            if chart["chartType"] == "donut" and not shape.fill:
                half_stroke = shape.stroke_width / 2
                inner_radii = [rx - half_stroke, ry - half_stroke]
                anchor_radius = rx
                rx, ry = rx + half_stroke, ry + half_stroke
                hole_ratio = min(inner_radii[0] / rx, inner_radii[1] / ry)
            points = [{"seriesIndex": 0, "categoryIndex": i, "value": value,
                       "x": cx + anchor_radius if i in positive else None,
                       "y": cy if i in positive else None, "shapeIndex": index if i in positive else None,
                       "startAngle": 0.0, "sweepAngle": TAU if i in positive else 0.0}
                      for i, value in enumerate(values)]
            if chart["chartType"] == "donut" and shape.fill and hole_index is None:
                return reject("native_chart_donut_hole_missing")
            if chart["chartType"] == "donut" and shape.stroke_width >= min(box.width, box.height):
                return reject("native_chart_donut_hole_missing")
            return {"passed": True, "reason": "polar_partition", "points": points,
                    "seriesColors": [shape.fill or shape.stroke], "center": [cx, cy],
                    "radii": [rx, ry], "holeRadii": inner_radii, "usedShapeIndices": [index],
                    "holeRatio": hole_ratio, "furnitureShapeIndices": [] if hole_index is None else [hole_index],
                    "categoryTextIndices": [next((i for i, text in enumerate(prims.texts)
                        if normalize_text(text.text) == normalize_text(category)), None) for category in chart["categories"]]}
    if len(indexed) != len(positive):
        return reject("native_chart_polar_slice_count_mismatch")
    ellipse = _ellipse([point for _, shape in indexed for point in shape.vertices])
    if ellipse is None:
        return reject("native_chart_polar_common_geometry_mismatch")
    cx, cy, rx, ry = ellipse
    arcs = []
    for index, shape in indexed:
        arc = _arc(shape, ellipse, full=len(indexed) == 1)
        if arc is None:
            return reject("native_chart_polar_sector_invalid")
        arcs.append({**arc, "shapeIndex": index, "shape": shape})
    ordered = sorted(arcs, key=lambda arc: arc["start"])
    tolerance = max(1e-5, .1 / min(rx, ry))
    for arc, following in zip(ordered, ordered[1:] + ordered[:1], strict=True):
        gap = ((following["start"] - arc["start"]) % TAU) if len(arcs) > 1 else TAU
        if abs(gap - arc["span"]) > tolerance:
            return reject("native_chart_polar_partition_gap_or_overlap")
    holes = [arc["hole"] for arc in arcs]
    # A common overpainted center is the existing ordinary-SVG donut spelling.
    hole_indices = []
    if chart["chartType"] == "donut" and max(holes) <= tolerance:
        centers = [(index, shape) for index, shape in enumerate(prims.shapes)
                   if index > max(index for index, _ in indexed) and shape.tag in {"circle", "ellipse"} and shape.fill
                   and shape.fill_alpha == 1 and abs((shape.box.x0 + shape.box.x1) / 2 - cx) <= .1
                   and abs((shape.box.y0 + shape.box.y1) / 2 - cy) <= .1]
        if len(centers) == 1:
            if any(shape.fill_alpha == 1 and shape.fill == centers[0][1].fill for _, shape in indexed):
                return reject("native_chart_donut_hole_not_distinguishable")
            hole_indices = [centers[0][0]]
            holes = [centers[0][1].box.width / (2 * rx), centers[0][1].box.height / (2 * ry)]
    if (max(holes) - min(holes) > tolerance or (chart["chartType"] == "pie" and max(holes) > tolerance)
            or (chart["chartType"] == "donut" and not tolerance < min(holes) <= max(holes) < 1)):
        return reject("native_chart_polar_hole_mismatch")
    remaining = list(arcs)
    points, assigned = [], []
    category_text_indices = [None] * len(values)
    labels_used = set()
    for category_index, (category, value, share) in enumerate(zip(chart["categories"], values, shares, strict=True)):
        labels = [(index, text) for index, text in enumerate(prims.texts)
                  if index not in labels_used and normalize_text(" ".join(text.lines)) == normalize_text(category)]
        if not labels:
            return reject("native_chart_polar_category_missing")
        if share == 0:
            index, label = labels[0]
            labels_used.add(index)
            category_text_indices[category_index] = index
            points.append({"seriesIndex": 0, "categoryIndex": category_index, "value": value,
                           "x": None, "y": None, "shapeIndex": None})
            continue
        candidates = []
        for index, label in labels:
            box = label.box()
            angle = math.atan2(((box.y0 + box.y1) / 2 - cy) / ry,
                               ((box.x0 + box.x1) / 2 - cx) / rx) % TAU
            legend = _legend_color(label, prims)
            visible_arcs = [arc for arc in arcs if arc["shape"].fill == legend] if legend else list(arcs)
            if legend and len(visible_arcs) == 1:
                candidates.append((index, visible_arcs[0]))
                continue
            for arc in visible_arcs:
                if (angle - arc["start"]) % TAU <= arc["span"] + tolerance:
                    candidates.append((index, arc))
        if len(candidates) != 1:
            return reject("native_chart_polar_category_association_mismatch")
        label_index, arc = candidates[0]
        if arc not in remaining:
            return reject("native_chart_polar_category_association_mismatch")
        if abs(arc["span"] / TAU - share) > tolerance / TAU:
            return reject("native_chart_polar_value_geometry_mismatch")
        labels_used.add(label_index)
        category_text_indices[category_index] = label_index
        remaining.remove(arc)
        assigned.append(arc)
        angle, radius = arc["start"] + arc["span"] / 2, (1 + max(holes)) / 2
        points.append({"seriesIndex": 0, "categoryIndex": category_index, "value": value,
                       "x": cx + rx * radius * math.cos(angle), "y": cy + ry * radius * math.sin(angle),
                       "shapeIndex": arc["shapeIndex"], "startAngle": arc["start"], "sweepAngle": arc["span"]})
    if remaining or any(abs((((b["start"] - a["start"]) % TAU) if len(assigned) > 1 else TAU) - a["span"]) > tolerance
                        for a, b in zip(assigned, assigned[1:] + assigned[:1], strict=True)):
        return reject("native_chart_polar_category_order_mismatch")
    # Compare actual painted measures as well as angular intervals. The bound
    # follows one half-pixel of boundary rounding, not a fixed percentage score.
    total_area = math.fsum(arc["shape"].area for arc in arcs)
    for point in points:
        if point["shapeIndex"] is None:
            continue
        shape = prims.shapes[point["shapeIndex"]]
        perimeter = math.fsum(math.hypot(b[0] - a[0], b[1] - a[1])
            for a, b in zip(shape.vertices, shape.vertices[1:] + shape.vertices[:1], strict=True))
        if abs(shape.area / total_area - shares[point["categoryIndex"]]) > max(1e-7, perimeter * .5 / total_area):
            return reject("native_chart_polar_painted_share_mismatch")
    return {"passed": True, "reason": "polar_partition", "points": points,
            "seriesColors": [arcs[0]["shape"].fill], "center": [cx, cy], "radii": [rx, ry],
            "holeRatio": max(holes), "usedShapeIndices": [index for index, _ in indexed],
            "furnitureShapeIndices": hole_indices, "categoryTextIndices": category_text_indices}
