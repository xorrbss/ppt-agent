"""Geometry mutations for strict Template V2 element trees."""

from __future__ import annotations

import math
from typing import Any


def _number(value: Any, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name}_must_be_numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name}_must_be_finite")
    return result


def normalized_position(value: dict[str, Any]) -> dict[str, float]:
    if not isinstance(value, dict) or set(value) != {"x", "y"}:
        raise ValueError("position_must_contain_only_x_and_y")
    return {
        "x": _number(value["x"], name="position_x"),
        "y": _number(value["y"], name="position_y"),
    }


def normalized_size(value: dict[str, Any]) -> dict[str, float]:
    if not isinstance(value, dict) or set(value) != {"width", "height"}:
        raise ValueError("size_must_contain_only_width_and_height")
    width = _number(value["width"], name="size_width")
    height = _number(value["height"], name="size_height")
    if width <= 0 or height <= 0:
        raise ValueError("size_must_be_positive")
    if width > 100000 or height > 100000:
        raise ValueError("size_exceeds_safe_limit")
    return {"width": width, "height": height}


def apply_geometry(
    element: dict[str, Any],
    *,
    position: dict[str, Any] | None = None,
    size: dict[str, Any] | None = None,
    rotation: float | None = None,
) -> None:
    if position is not None:
        element["position"] = normalized_position(position)
    if size is not None:
        element["size"] = normalized_size(size)
    if rotation is not None:
        element["rotation"] = _number(rotation, name="rotation")


def element_bounds(element: dict[str, Any]) -> tuple[float, float, float, float] | None:
    position = element.get("position")
    size = element.get("size")
    if not isinstance(position, dict) or not isinstance(size, dict):
        return None
    try:
        normalized_pos = normalized_position(position)
        normalized_sz = normalized_size(size)
    except ValueError:
        return None
    return (
        normalized_pos["x"],
        normalized_pos["y"],
        normalized_sz["width"],
        normalized_sz["height"],
    )


def scale_component_elements(
    component: dict[str, Any],
    requested_size: dict[str, Any],
) -> None:
    """Resize a component by scaling its positioned elements.

    ``Component`` has no persisted size field in the local canonical schema, so
    writing an upstream-style ``size`` key would be lossy/invalid.  Scaling the
    child geometry preserves the local renderer/export contract.
    """

    target = normalized_size(requested_size)
    elements = component.get("elements")
    if not isinstance(elements, list) or not elements:
        raise ValueError("component_has_no_resizable_elements")
    bounds = [
        bound
        for element in elements
        if isinstance(element, dict)
        and (bound := element_bounds(element)) is not None
    ]
    if not bounds:
        raise ValueError("component_has_no_explicit_geometry")
    left = min(bound[0] for bound in bounds)
    top = min(bound[1] for bound in bounds)
    right = max(bound[0] + bound[2] for bound in bounds)
    bottom = max(bound[1] + bound[3] for bound in bounds)
    source_width = right - left
    source_height = bottom - top
    if source_width <= 0 or source_height <= 0:
        raise ValueError("component_geometry_is_degenerate")
    scale_x = target["width"] / source_width
    scale_y = target["height"] / source_height
    for element in elements:
        if not isinstance(element, dict):
            continue
        bound = element_bounds(element)
        if bound is None:
            continue
        x, y, width, height = bound
        element["position"] = {
            "x": left + (x - left) * scale_x,
            "y": top + (y - top) * scale_y,
        }
        element["size"] = {
            "width": width * scale_x,
            "height": height * scale_y,
        }
