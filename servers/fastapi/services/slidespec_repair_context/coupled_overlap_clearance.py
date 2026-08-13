"""Project measured sibling overlap-clearance repair inputs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from services.slidespec_repair_context.artifacts import RepairArtifacts
from services.slidespec_repair_context.coupled_projection import (
    _CLEARANCE_EPSILON_PX,
    _allowed,
    _hard_footprint,
    _issue_group,
    _mapping,
    _operation_proof,
    _padding_reclaim,
    _parent_context,
)
from services.slidespec_repair_context.state import RepairContextState

def _project_overlap_clearance(
    state: RepairContextState,
    artifacts: RepairArtifacts,
    issue: Mapping[str, Any],
) -> None:
    target = state.layout_by_id.get(str(issue.get("target", "")))
    other = state.layout_by_id.get(str(issue.get("otherTarget", "")))
    if (
        target is None
        or other is None
        or state.parent_by_id.get(target.layoutId)
        != state.parent_by_id.get(other.layoutId)
    ):
        return
    parent_context = _parent_context(state, target)
    if parent_context is None:
        return
    parent, parent_binding, parent_metric, sibling_ids = parent_context
    if state.parent_by_id.get(other.layoutId) != parent.layoutId:
        return
    binding_by_layout = {binding.layoutId: binding for binding in state.source.bindings}
    target_binding = binding_by_layout.get(target.layoutId)
    other_binding = binding_by_layout.get(other.layoutId)
    if (
        target_binding is None
        or other_binding is None
        or target_binding.position != "static"
        or other_binding.position != "static"
        or not _allowed(parent, "setSpacing", imported=True)
    ):
        return
    target_metric = state.metric_by_qid.get(target_binding.qid)
    other_metric = state.metric_by_qid.get(other_binding.qid)
    target_box = _mapping(target_metric.get("bbox")) if isinstance(target_metric, Mapping) else {}
    other_box = _mapping(other_metric.get("bbox")) if isinstance(other_metric, Mapping) else {}
    if not target_box or not other_box:
        return
    computed = _mapping(parent_metric.get("computed_style"))
    direction = str(computed.get("flexDirection", "row"))
    axis = "y" if direction.startswith("column") else "x"
    start = "y" if axis == "y" else "x"
    size = "height" if axis == "y" else "width"
    first, second = sorted((target_box, other_box), key=lambda box: state.finite_number(box.get(start)))
    actual_gap = state.finite_number(second.get(start)) - (
        state.finite_number(first.get(start)) + state.finite_number(first.get(size))
    )
    viewport = float(state.source.viewportHeight if axis == "y" else state.source.viewportWidth)
    required = max(1.0, state.finite_number(issue.get("clearance")) * viewport)
    deficit = required - actual_gap
    if deficit <= _CLEARANCE_EPSILON_PX:
        return
    parent_box = _mapping(parent_metric.get("bbox"))
    if not parent_box:
        return
    children = tuple(
        child
        for child in parent.children
        if (binding := binding_by_layout.get(child.layoutId)) is not None
        and isinstance(state.metric_by_qid.get(binding.qid), Mapping)
    )
    child_boxes = [
        _mapping(state.metric_by_qid[binding_by_layout[child.layoutId].qid].get("bbox"))
        for child in children
    ]
    child_boxes = [box for box in child_boxes if box]
    if len(child_boxes) < 2:
        return
    used_start = min(state.finite_number(box.get(start)) for box in child_boxes)
    used_end = max(
        state.finite_number(box.get(start)) + state.finite_number(box.get(size))
        for box in child_boxes
    )
    parent_size = state.finite_number(parent_box.get(size))
    slack = max(0.0, parent_size - (used_end - used_start))
    gap_count = max(1, len(child_boxes) - 1)
    needed_capacity = deficit * gap_count
    operations: list[dict[str, Any]] = []
    proofs: list[dict[str, Any]] = []
    reclaimed = 0.0
    if slack + _CLEARANCE_EPSILON_PX < needed_capacity and _allowed(parent, "setPadding", imported=True):
        operation, reclaimed = _padding_reclaim(
            parent,
            parent_binding,
            axis=axis,
            needed_px=needed_capacity - slack,
        )
        if operation is not None:
            operations.append(operation)
            proofs.append(_operation_proof(parent.layoutId, "setPadding"))
    available = slack + reclaimed
    if available + _CLEARANCE_EPSILON_PX < needed_capacity:
        return
    operations.append(
        {
            "op": "setSpacing",
            "target": parent.layoutId,
            "gap": round(float(parent.spacing) + deficit, 6),
            "precondition": {"currentGap": float(parent.spacing)},
        }
    )
    proofs.append(_operation_proof(parent.layoutId, "setSpacing"))
    hard_ids, footprint_ids = _hard_footprint(
        state, {parent.layoutId, *sibling_ids}
    )
    group_id = _issue_group(issue)
    blocking_core = tuple(dict.fromkeys((str(issue.get("constraintId") or group_id), *hard_ids)))
    artifacts.coupled_repairs.append(
        {
            "issueGroupId": group_id,
            "code": str(issue.get("code", "")),
            "semanticId": str(issue.get("semanticId", "")),
            "qid": str(issue.get("qid", "")),
            "constraintId": issue.get("constraintId"),
            "target": target.layoutId,
            "operations": operations,
            "operationProofs": proofs,
            "constraintFootprint": {
                "blockingCore": blocking_core,
                "layoutIds": footprint_ids,
                "parentSpaceId": parent.layoutId,
                "siblingIds": sibling_ids,
            },
            "proof": {
                "source": "chrome",
                "telemetryComplete": True,
                "hardGatePolicy": "preserve",
                "baselineDeficitPx": deficit,
                "predictedResidualPx": 0.0,
                "requiredClearancePx": required,
                "predictedClearancePx": actual_gap + deficit,
                "requiredTextFitPx": 0.0,
                "predictedTextFitPx": 0.0,
                "numericModel": {
                    "kind": "clearance",
                    "axis": axis,
                    "clearanceBeforePx": actual_gap,
                    "requiredPx": required,
                    "capacityBeforePx": slack,
                    "parentReclaimPx": reclaimed,
                    "clearanceAfterPx": actual_gap + deficit,
                    "residualAfterPx": 0.0,
                },
            },
        }
    )

