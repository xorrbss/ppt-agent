"""Text, overflow, and connector geometry measurements."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from typing import Any

from models.slidespec_v1 import LayoutNode
from services.slidespec_repair_context.state import RepairContextState


def text_fragment_bounds(state: RepairContextState, metric: Mapping[str, Any]) -> dict[str, float] | None:
    finite_number = state.finite_number
    fragments = metric.get("text_fragments")
    if not isinstance(fragments, Sequence) or isinstance(fragments, (str, bytes)):
        return None
    rects = [item for item in fragments if isinstance(item, Mapping)]
    if not rects:
        return None
    left = min(finite_number(rect.get("x")) for rect in rects)
    top = min(finite_number(rect.get("y")) for rect in rects)
    right = max(finite_number(rect.get("x")) + finite_number(rect.get("width")) for rect in rects)
    bottom = max(finite_number(rect.get("y")) + finite_number(rect.get("height")) for rect in rects)
    if right <= left or bottom <= top:
        return None
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


def glyph_measurement(
    state: RepairContextState,
    metric: Mapping[str, Any],
    imported: Any,
) -> dict[str, Any] | None:
    normalized_metrics = state.normalized_metrics
    finite_number = state.finite_number
    fragment_bounds = text_fragment_bounds(state, metric)
    if fragment_bounds is None:
        return None
    measured_width = max(
        fragment_bounds["width"],
        finite_number(metric.get("scroll_width")),
    )
    measured_height = max(
        fragment_bounds["height"],
        finite_number(metric.get("scroll_height")),
    )
    available_width = finite_number(metric.get("client_width"))
    available_height = finite_number(metric.get("client_height"))
    if min(measured_width, measured_height, available_width, available_height) <= 0:
        return None
    return {
        "source": "chrome",
        "telemetryComplete": bool(normalized_metrics.get("fonts_ready")) and metric.get("font_loaded") is True,
        "fontsReady": bool(normalized_metrics.get("fonts_ready")) and metric.get("font_loaded") is True,
        "kind": "range-bounds",
        "measuredWidthPx": measured_width,
        "measuredHeightPx": measured_height,
        "availableWidthPx": available_width,
        "availableHeightPx": available_height,
        "fontSizePx": finite_number(metric.get("font_size"), finite_number(getattr(imported, "fontSizePx", 0))),
    }


def precise_overflow_bounds(
    state: RepairContextState,
    imported: Any,
    current: Mapping[str, float],
) -> dict[str, float]:
    """Expand only by the measured overflow plus a small render safety pad."""

    source = state.source
    metric_by_qid = state.metric_by_qid
    candidate = dict(current)
    if source is None or imported is None:
        candidate["height"] = min(1.0 - current["y"], current["height"] + 0.02)
        return candidate
    metric = metric_by_qid.get(imported.qid)
    if not isinstance(metric, Mapping):
        candidate["height"] = min(1.0 - current["y"], current["height"] + 0.02)
        return candidate
    horizontal_excess = max(
        0.0,
        float(metric.get("scroll_width", 0.0)) - float(metric.get("client_width", 0.0)),
    )
    vertical_excess = max(
        0.0,
        float(metric.get("scroll_height", 0.0)) - float(metric.get("client_height", 0.0)),
    )
    if horizontal_excess > 1.0:
        candidate["width"] = min(
            1.0 - current["x"],
            current["width"] + (horizontal_excess + 4.0) / source.viewportWidth,
        )
    if vertical_excess > 1.0:
        candidate["height"] = min(
            1.0 - current["y"],
            current["height"] + (vertical_excess + 4.0) / source.viewportHeight,
        )
    return candidate


def point_segment_distance(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length_sq = dx * dx + dy * dy
    if length_sq <= 1e-12:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    projection = max(
        0.0,
        min(
            1.0,
            ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_sq,
        ),
    )
    nearest = (start[0] + projection * dx, start[1] + projection * dy)
    return math.hypot(point[0] - nearest[0], point[1] - nearest[1])


def segment_intersects_rect(
    start: tuple[float, float],
    end: tuple[float, float],
    rect: tuple[float, float, float, float],
) -> bool:
    """Liang-Barsky intersection, inclusive of touching the rectangle."""

    left, top, right, bottom = rect
    dx, dy = end[0] - start[0], end[1] - start[1]
    lower, upper = 0.0, 1.0
    for p, q in (
        (-dx, start[0] - left),
        (dx, right - start[0]),
        (-dy, start[1] - top),
        (dy, bottom - start[1]),
    ):
        if abs(p) <= 1e-12:
            if q < 0:
                return False
            continue
        ratio = q / p
        if p < 0:
            lower = max(lower, ratio)
        else:
            upper = min(upper, ratio)
        if lower > upper:
            return False
    return True


def segment_rect_clearance(
    start: tuple[float, float],
    end: tuple[float, float],
    rect: tuple[float, float, float, float],
) -> float:
    if segment_intersects_rect(start, end, rect):
        return 0.0
    left, top, right, bottom = rect
    corners = ((left, top), (right, top), (right, bottom), (left, bottom))

    def point_rect_distance(point: tuple[float, float]) -> float:
        x_delta = max(left - point[0], 0.0, point[0] - right)
        y_delta = max(top - point[1], 0.0, point[1] - bottom)
        return math.hypot(x_delta, y_delta)

    return min(
        point_rect_distance(start),
        point_rect_distance(end),
        *(point_segment_distance(corner, start, end) for corner in corners),
    )


def connector_candidate(
    state: RepairContextState,
    node: LayoutNode,
    metric: Mapping[str, Any] | None,
    obstacle_metrics: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the shortest deterministic Chrome-grounded safe dogleg.

    The route is canonical in the connector node's normalized coordinate
    space.  Obstacles and endpoint observations come from the real Chrome
    frame, so a candidate is never labeled safe from schema geometry alone.
    """

    finite_number = state.finite_number
    current = node.connectorRoute
    if current is None:
        return {}, {"source": "chrome", "telemetryComplete": False}
    current_route = {"points": [point.model_dump(mode="json") for point in current.points]}
    box = metric.get("bbox") if metric is not None else None
    box = box if isinstance(box, Mapping) else {}
    box_x = finite_number(box.get("x"))
    box_y = finite_number(box.get("y"))
    box_width = finite_number(box.get("width"))
    box_height = finite_number(box.get("height"))
    geometry = metric.get("connector_geometry") if metric is not None else None
    geometry = geometry if isinstance(geometry, Mapping) else {}
    measured_points = geometry.get("pointsPx")
    if not isinstance(measured_points, Sequence) or isinstance(measured_points, (str, bytes)):
        measured_points = ()

    obstacle_rects: list[tuple[float, float, float, float]] = []
    for obstacle_metric in obstacle_metrics:
        obstacle_box = obstacle_metric.get("bbox")
        obstacle_box = obstacle_box if isinstance(obstacle_box, Mapping) else {}
        x = finite_number(obstacle_box.get("x"))
        y = finite_number(obstacle_box.get("y"))
        width = finite_number(obstacle_box.get("width"))
        height = finite_number(obstacle_box.get("height"))
        if width > 0 and height > 0:
            obstacle_rects.append((x, y, x + width, y + height))

    expected = [
        (
            box_x + point.x * box_width,
            box_y + point.y * box_height,
        )
        for point in (current.points[0], current.points[-1])
    ]
    actual: list[tuple[float, float]] = []
    if len(measured_points) >= 2:
        for raw in (measured_points[0], measured_points[-1]):
            if isinstance(raw, Mapping):
                actual.append((finite_number(raw.get("x")), finite_number(raw.get("y"))))
    endpoint_error = (
        max(
            abs(observed[axis] - desired[axis])
            for observed, desired in zip(actual, expected, strict=False)
            for axis in (0, 1)
        )
        if len(actual) == 2 and min(box_width, box_height) > 0
        else float("inf")
    )

    def route_pixels(points: Sequence[Mapping[str, float]]) -> list[tuple[float, float]]:
        return [
            (
                box_x + finite_number(point.get("x")) * box_width,
                box_y + finite_number(point.get("y")) * box_height,
            )
            for point in points
        ]

    def clearance(points: Sequence[Mapping[str, float]]) -> float:
        pixels = route_pixels(points)
        if len(pixels) < 2 or not obstacle_rects:
            return float("inf") if len(pixels) >= 2 else 0.0
        return min(
            segment_rect_clearance(start, end, rect)
            for start, end in zip(pixels, pixels[1:], strict=False)
            for rect in obstacle_rects
        )

    start = current_route["points"][0]
    end = current_route["points"][-1]
    candidates: list[list[dict[str, float]]] = []
    if obstacle_rects and min(box_width, box_height) > 0:
        clearance_px = 4.0
        top = (min(rect[1] for rect in obstacle_rects) - clearance_px - box_y) / box_height
        bottom = (max(rect[3] for rect in obstacle_rects) + clearance_px - box_y) / box_height
        left = (min(rect[0] for rect in obstacle_rects) - clearance_px - box_x) / box_width
        right = (max(rect[2] for rect in obstacle_rects) + clearance_px - box_x) / box_width
        if 0 <= top <= 1:
            candidates.append([start, {"x": start["x"], "y": top}, {"x": end["x"], "y": top}, end])
        if 0 <= bottom <= 1:
            candidates.append([start, {"x": start["x"], "y": bottom}, {"x": end["x"], "y": bottom}, end])
        if 0 <= left <= 1:
            candidates.append([start, {"x": left, "y": start["y"]}, {"x": left, "y": end["y"]}, end])
        if 0 <= right <= 1:
            candidates.append([start, {"x": right, "y": start["y"]}, {"x": right, "y": end["y"]}, end])

    def route_length(points: Sequence[Mapping[str, float]]) -> float:
        return sum(
            math.hypot(
                finite_number(right_point.get("x")) - finite_number(left_point.get("x")),
                finite_number(right_point.get("y")) - finite_number(left_point.get("y")),
            )
            for left_point, right_point in zip(points, points[1:], strict=False)
        )

    safe_candidates = [points for points in candidates if clearance(points) >= 1.0]
    safe_candidates.sort(
        key=lambda points: (
            round(route_length(points), 9),
            tuple((round(point["x"], 9), round(point["y"], 9)) for point in points),
        )
    )
    selected = safe_candidates[0] if safe_candidates else current_route["points"]
    minimum_clearance = clearance(selected)
    telemetry_complete = bool(
        metric is not None
        and geometry.get("measured") is True
        and len(actual) == 2
        and obstacle_rects
        and min(box_width, box_height) > 0
    )
    return {"points": selected}, {
        "source": "chrome",
        "telemetryComplete": telemetry_complete,
        "endpointAnchors": {
            "startResolved": len(actual) == 2,
            "endResolved": len(actual) == 2,
            "maxErrorPx": endpoint_error,
        },
        "obstacleFree": bool(safe_candidates and minimum_clearance >= 1.0),
        "minimumClearancePx": minimum_clearance,
        "obstacleCount": len(obstacle_rects),
    }
