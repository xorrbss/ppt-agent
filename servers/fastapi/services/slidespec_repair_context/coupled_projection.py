"""Build Chrome-proved parent-space repair inputs for the coupled planner."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from models.slidespec_v1 import LayoutNode
from services.slidespec_repair_context.artifacts import RepairArtifacts
from services.slidespec_repair_context.state import RepairContextState
from utils.slidespec_constraints import constraint_targets


_FLOW_DISPLAYS = frozenset({"flex", "inline-flex", "grid", "inline-grid"})
_GRID_DISPLAYS = frozenset({"grid", "inline-grid"})
_CLEARANCE_EPSILON_PX = 0.5


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _allowed(node: LayoutNode, operation: str, *, imported: bool) -> bool:
    constraints = node.constraints
    if constraints is None:
        return not imported
    if constraints.locked:
        return False
    allowed = constraints.allowedOperations
    return allowed is None or operation in allowed


def _operation_proof(target: str, op: str) -> dict[str, Any]:
    return {
        "target": target,
        "op": op,
        "mutationAllowed": True,
        "roundTripSupported": True,
        "baselineMatched": True,
    }


def _padding_reclaim(
    node: LayoutNode,
    binding: Any,
    *,
    axis: str,
    needed_px: float,
) -> tuple[dict[str, Any] | None, float]:
    if needed_px <= _CLEARANCE_EPSILON_PX:
        return None, 0.0
    if axis == "x":
        raw = [float(binding.paddingLeftPx), float(binding.paddingRightPx)]
        basis = max(1.0, float(binding.sourceBoundsPx.width))
        fields = ("left", "right")
    else:
        raw = [float(binding.paddingTopPx), float(binding.paddingBottomPx)]
        basis = max(1.0, float(binding.sourceBoundsPx.height))
        fields = ("top", "bottom")
    available = sum(raw)
    reclaimed = min(available, needed_px)
    if reclaimed <= _CLEARANCE_EPSILON_PX:
        return None, 0.0
    remaining = reclaimed
    # Reduce the larger inset first.  This is deterministic and minimizes the
    # chance of changing the visually intentional anchored edge.
    for index in sorted(range(2), key=lambda value: (-raw[value], value)):
        delta = min(raw[index], remaining)
        raw[index] -= delta
        remaining -= delta
    padding = node.padding.model_dump(mode="json")
    for field, value in zip(fields, raw, strict=True):
        padding[field] = round(max(0.0, value / basis), 9)
    return (
        {
            "op": "setPadding",
            "target": node.layoutId,
            "padding": padding,
            "precondition": {
                "currentPadding": node.padding.model_dump(mode="json")
            },
        },
        reclaimed,
    )


def _hard_footprint(
    state: RepairContextState, layout_ids: set[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    constraint_ids: list[str] = []
    # Keep the mutation footprint local.  Imported specs are intentionally flat
    # and their order constraints can connect most of the slide; transitively
    # expanding through those constraints turned one DOM parent into the entire
    # 100+ node page.  Full hard-gate evaluation still runs after application,
    # while this footprint records the exact parent/sibling capacity pool.
    for constraint in state.spec.constraints:
        targets = set(constraint_targets(constraint))
        if constraint.strength == "hard" and targets.intersection(layout_ids):
            constraint_ids.append(constraint.constraintId)
    return tuple(sorted(constraint_ids)), tuple(sorted(layout_ids))


def _issue_group(issue: Mapping[str, Any]) -> str:
    existing = str(issue.get("issueGroupId", ""))
    if existing:
        return existing
    return ":".join(
        (
            str(issue.get("constraintId") or "dom"),
            str(issue.get("code", "")),
            str(issue.get("semanticId", "")),
            str(issue.get("qid", "")),
        )
    )


def _parent_context(
    state: RepairContextState, target: LayoutNode
) -> tuple[LayoutNode, Any, Mapping[str, Any], tuple[str, ...]] | None:
    if state.source is None:
        return None
    binding_by_layout = {binding.layoutId: binding for binding in state.source.bindings}
    binding_by_qid = {binding.qid: binding for binding in state.source.bindings}
    target_binding = binding_by_layout.get(target.layoutId)
    if target_binding is None:
        return None
    target_metric = state.metric_by_qid.get(target_binding.qid)
    if not isinstance(target_metric, Mapping):
        return None

    # Imported SlideSpecs deliberately keep a flat canonical layout tree.  The
    # Chrome parent_qid chain is therefore the authoritative, measured ownership
    # proof for a local flow container; do not reparent the canonical tree.
    parent_qid = str(target_metric.get("parent_qid", ""))
    visited: set[str] = set()
    while parent_qid and parent_qid not in visited:
        visited.add(parent_qid)
        parent_binding = binding_by_qid.get(parent_qid)
        parent_metric = state.metric_by_qid.get(parent_qid)
        if parent_binding is not None and isinstance(parent_metric, Mapping):
            parent = state.layout_by_id.get(parent_binding.layoutId)
            if (
                parent is not None
                and parent.bounds is not None
                and parent_binding.display in _FLOW_DISPLAYS
            ):
                sibling_ids = tuple(
                    sorted(
                        binding.layoutId
                        for binding in state.source.bindings
                        if (
                            (metric := state.metric_by_qid.get(binding.qid))
                            is not None
                            and str(metric.get("parent_qid", "")) == parent_qid
                        )
                    )
                )
                return parent, parent_binding, parent_metric, sibling_ids
        parent_qid = (
            str(parent_metric.get("parent_qid", ""))
            if isinstance(parent_metric, Mapping)
            else ""
        )
    return None


def _absolute_target_resize(
    state: RepairContextState,
    target: LayoutNode,
    target_binding: Any,
    *,
    axis: str,
    needed_px: float,
) -> tuple[dict[str, Any] | None, float]:
    """Grow a fixed-size positioned child by a measured pixel deficit.

    Parent capacity changes do not resize an absolutely positioned child.  A
    bounded target resize is therefore required in addition to the parent-first
    reclaim; otherwise the pre-render prediction would claim space the target
    cannot consume.
    """

    if (
        needed_px <= _CLEARANCE_EPSILON_PX
        or target.bounds is None
        or target_binding.position not in {"absolute", "fixed"}
        or not _allowed(target, "resizeWithinBounds", imported=True)
    ):
        return None, 0.0
    width = float(target.bounds.width)
    height = float(target.bounds.height)
    if axis == "x":
        width += needed_px / max(1.0, float(state.source.viewportWidth))
    else:
        height += needed_px / max(1.0, float(state.source.viewportHeight))
    return (
        {
            "op": "resizeWithinBounds",
            "target": target.layoutId,
            "width": round(width, 9),
            "height": round(height, 9),
            "precondition": {
                "expectedBounds": target.bounds.model_dump(mode="json")
            },
        },
        needed_px,
    )


def _measured_text_deficits(
    state: RepairContextState,
    target_binding: Any,
    target_metric: Mapping[str, Any],
) -> tuple[float, float]:
    """Return axis deficits from scroll metrics plus Chrome text-rect escape."""

    width_deficit = max(
        0.0,
        state.finite_number(target_metric.get("scroll_width"))
        - state.finite_number(target_metric.get("client_width")),
    )
    height_deficit = max(
        0.0,
        state.finite_number(target_metric.get("scroll_height"))
        - state.finite_number(target_metric.get("client_height")),
    )
    fragments = tuple(
        _mapping(value)
        for value in target_metric.get("text_fragments", ())
        if isinstance(value, Mapping)
    )
    if not fragments:
        return width_deficit, height_deficit
    clip = {
        "left": 0.0,
        "top": 0.0,
        "right": float(state.source.viewportWidth),
        "bottom": float(state.source.viewportHeight),
    }
    current_qid = target_binding.qid
    visited: set[str] = set()
    while current_qid and current_qid not in visited:
        visited.add(current_qid)
        metric = state.metric_by_qid.get(current_qid)
        if not isinstance(metric, Mapping):
            break
        style = _mapping(metric.get("computed_style"))
        if any(
            str(style.get(name, "visible")) in {"hidden", "clip", "scroll", "auto"}
            for name in ("overflowX", "overflowY")
        ):
            box = _mapping(metric.get("bbox"))
            left = state.finite_number(box.get("x"))
            top = state.finite_number(box.get("y"))
            clip["left"] = max(clip["left"], left)
            clip["top"] = max(clip["top"], top)
            clip["right"] = min(
                clip["right"], left + state.finite_number(box.get("width"))
            )
            clip["bottom"] = min(
                clip["bottom"], top + state.finite_number(box.get("height"))
            )
        current_qid = str(metric.get("parent_qid", ""))
    fragment_left = min(state.finite_number(value.get("x")) for value in fragments)
    fragment_top = min(state.finite_number(value.get("y")) for value in fragments)
    fragment_right = max(
        state.finite_number(value.get("x")) + state.finite_number(value.get("width"))
        for value in fragments
    )
    fragment_bottom = max(
        state.finite_number(value.get("y")) + state.finite_number(value.get("height"))
        for value in fragments
    )
    horizontal_escape = max(
        0.0, clip["left"] - fragment_left, fragment_right - clip["right"]
    )
    vertical_escape = max(
        0.0, clip["top"] - fragment_top, fragment_bottom - clip["bottom"]
    )
    # Centered content moves only half the size delta away from the clipped
    # edge, so it needs twice the measured escape to clear that edge.
    horizontal_factor = 2.0 if target_binding.justifyContent == "center" else 1.0
    vertical_factor = 2.0 if target_binding.alignItems == "center" else 1.0
    return (
        max(width_deficit, horizontal_escape * horizontal_factor),
        max(height_deficit, vertical_escape * vertical_factor),
    )


def _grid_track_reclaim(
    parent: LayoutNode,
    parent_binding: Any,
    target: LayoutNode,
    *,
    deficit_px: float,
) -> tuple[dict[str, Any] | None, float]:
    ratios = list(parent.columnRatio)
    placement = target.gridPlacement
    if (
        parent_binding.display not in _GRID_DISPLAYS
        or placement is None
        or not ratios
        or placement.column >= len(ratios)
        or len(ratios) < 2
    ):
        return None, 0.0
    total = sum(ratios)
    parent_width = max(1.0, float(parent_binding.sourceBoundsPx.width))
    required_ratio = deficit_px / parent_width * total
    donor = max(
        (index for index in range(len(ratios)) if index != placement.column),
        key=lambda index: (ratios[index], -index),
    )
    transferable = max(0.0, ratios[donor] - max(0.1, total * 0.05))
    transferred = min(required_ratio, transferable)
    if transferred * parent_width / total <= _CLEARANCE_EPSILON_PX:
        return None, 0.0
    ratios[placement.column] += transferred
    ratios[donor] -= transferred
    return (
        {
            "op": "setColumnRatio",
            "target": parent.layoutId,
            "value": [round(value, 9) for value in ratios],
            "precondition": {"currentRatio": list(parent.columnRatio)},
        },
        transferred * parent_width / total,
    )


from services.slidespec_repair_context.coupled_text_capacity import _project_text_capacity



from services.slidespec_repair_context.coupled_overlap_clearance import _project_overlap_clearance


def project_coupled_repairs(
    state: RepairContextState, artifacts: RepairArtifacts
) -> None:
    """Project parent-first plans from already confirmed geometry issues."""

    for issue in tuple(artifacts.geometry_issues):
        kind = str(issue.get("kind", ""))
        code = str(issue.get("code", ""))
        if kind in {"overflow", "readability"} or code in {
            "dom.clipping",
            "dom.overflow",
            "constraint.readability.violated",
        }:
            _project_text_capacity(state, artifacts, issue)
        elif kind == "nonOverlap" or code in {
            "dom.unintended_overlap",
            "constraint.non_overlap.violated",
        }:
            _project_overlap_clearance(state, artifacts, issue)


__all__ = ["project_coupled_repairs"]
# ruff: noqa: E402, F401
