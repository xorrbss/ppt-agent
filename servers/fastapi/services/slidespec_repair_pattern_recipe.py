"""Compile target-independent repair recipes from validated plans."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from models.slidespec_v1 import LayoutNode, RenderRequest, RepairPlan
from services.slidespec_repair_pattern_index import _layout_indexes, _target_selector
from services.slidespec_repair_patterns import (
    RepairRecipe,
    _RecipeOperation,
    _digest,
)

def _number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _compile_adjustment(
    node: LayoutNode, raw: Mapping[str, Any]
) -> tuple[str, Mapping[str, Any]] | None:
    operation = str(raw.get("op", ""))
    if operation == "setBounds":
        bounds = raw.get("bounds")
        if node.bounds is None or not isinstance(bounds, Mapping):
            return None
        values = tuple(_number(bounds.get(axis)) for axis in ("x", "y", "width", "height"))
        if any(value is None for value in values):
            return None
        source = node.bounds
        return operation, {
            "mode": "bounds_relative",
            "x_delta_widths": (float(values[0]) - source.x) / source.width,
            "y_delta_heights": (float(values[1]) - source.y) / source.height,
            "width_ratio": float(values[2]) / source.width,
            "height_ratio": float(values[3]) / source.height,
        }
    if operation == "translateWithinParent":
        point = raw.get("value")
        if node.bounds is None or not isinstance(point, Mapping):
            return None
        x = _number(point.get("x"))
        y = _number(point.get("y"))
        if x is None or y is None:
            return None

        def slack_fraction(delta: float, before: float, size: float) -> float:
            slack = 1.0 - before - size if delta >= 0 else before
            return delta / max(slack, 1e-9)

        return operation, {
            "mode": "slack_fraction",
            "x": slack_fraction(x, node.bounds.x, node.bounds.width),
            "y": slack_fraction(y, node.bounds.y, node.bounds.height),
        }
    if operation == "resizeWithinBounds":
        if node.bounds is None:
            return None
        width = _number(raw.get("width"))
        height = _number(raw.get("height"))
        if width is None or height is None:
            return None
        return operation, {
            "mode": "size_ratio",
            "width": width / node.bounds.width,
            "height": height / node.bounds.height,
        }
    if operation == "setFontScale":
        scale = _number(raw.get("scale"))
        if scale is None:
            return None
        payload: dict[str, Any] = {
            "mode": "font_ratio",
            "ratio": scale / node.fontScale,
        }
        if raw.get("bounds") is not None:
            payload["bounds"] = deepcopy(raw["bounds"])
        return operation, payload
    if operation == "setSpacing":
        gap = _number(raw.get("gap"))
        if gap is None:
            return None
        return operation, {"mode": "spacing_delta", "delta": gap - node.spacing}
    if operation == "setPadding":
        padding = raw.get("padding")
        if not isinstance(padding, Mapping):
            return None
        current = node.padding.model_dump(mode="json", by_alias=True)
        values = {key: _number(padding.get(key)) for key in current}
        if any(value is None for value in values.values()):
            return None
        return operation, {
            "mode": "padding_delta",
            "delta": {key: float(values[key]) - float(current[key]) for key in current},
        }
    if operation in {
        "setAlignment",
        "setJustification",
        "setTrackSize",
        "setColumnRatio",
        "setGridPlacement",
        "setLineHeightToken",
        "setTextFitPolicy",
        "setImageFit",
        "setCropFocalPoint",
    }:
        value = raw.get("value")
        if value is None:
            return None
        return operation, {"mode": "structural_value", "value": deepcopy(value)}
    # Connector points, replacement subtrees and unknown operations can embed
    # absolute geometry or slide-local IDs and are intentionally not reusable.
    return None


def _compile_recipe(request: RenderRequest, plan: RepairPlan) -> RepairRecipe | None:
    indexes = _layout_indexes(request)
    operations: list[_RecipeOperation] = []
    for operation in plan.operations:
        raw = operation.model_dump(mode="json", by_alias=True, exclude_none=True)
        target = str(raw.pop("target", ""))
        raw.pop("precondition", None)
        selector = _target_selector(indexes, target)
        node = indexes.by_id.get(target) or indexes.by_semantic.get(target)
        if selector is None or node is None:
            return None
        compiled = _compile_adjustment(node, raw)
        if compiled is None:
            return None
        operation_name, adjustment = compiled
        operations.append(_RecipeOperation(selector, operation_name, adjustment))
    if not operations:
        return None
    payload = {
        "strategy": plan.strategy,
        "operations": [
            {
                "selector": {
                    "target_kind": operation.selector.target_kind,
                    "path": operation.selector.path,
                    "shape": operation.selector.shape,
                },
                "operation": operation.operation,
                "adjustment": operation.adjustment,
            }
            for operation in operations
        ],
    }
    return RepairRecipe(
        recipe_id="recipe:" + _digest(payload)[:24],
        strategy=plan.strategy,
        operations=tuple(operations),
        budget=plan.budget.model_dump(mode="json", by_alias=True, exclude_none=True),
    )

