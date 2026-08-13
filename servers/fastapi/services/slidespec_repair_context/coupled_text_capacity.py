"""Project measured text-capacity repair inputs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from services.slidespec_repair_context.artifacts import RepairArtifacts
from services.slidespec_repair_context.coupled_projection import (
    _CLEARANCE_EPSILON_PX,
    _absolute_target_resize,
    _allowed,
    _grid_track_reclaim,
    _hard_footprint,
    _issue_group,
    _measured_text_deficits,
    _operation_proof,
    _padding_reclaim,
    _parent_context,
)
from services.slidespec_repair_context.state import RepairContextState

def _project_text_capacity(
    state: RepairContextState,
    artifacts: RepairArtifacts,
    issue: Mapping[str, Any],
) -> None:
    target = state.layout_by_id.get(str(issue.get("target", "")))
    if target is None:
        return
    parent_context = _parent_context(state, target)
    if parent_context is None:
        return
    parent, parent_binding, _parent_metric, sibling_ids = parent_context
    target_binding = state.binding_by_semantic.get(str(issue.get("semanticId", "")))
    if target_binding is None:
        return
    target_metric = state.metric_by_qid.get(str(issue.get("qid", "")))
    if not isinstance(target_metric, Mapping):
        return
    width_deficit, height_deficit = _measured_text_deficits(
        state, target_binding, target_metric
    )
    axis = "x" if width_deficit > height_deficit else "y"
    deficit = max(width_deficit, height_deficit)
    if deficit <= _CLEARANCE_EPSILON_PX:
        return

    operations: list[dict[str, Any]] = []
    proofs: list[dict[str, Any]] = []
    parent_reclaimed = 0.0
    if _allowed(parent, "setPadding", imported=True):
        operation, amount = _padding_reclaim(
            parent,
            parent_binding,
            axis=axis,
            needed_px=deficit - parent_reclaimed,
        )
        if operation is not None:
            operations.append(operation)
            proofs.append(_operation_proof(parent.layoutId, "setPadding"))
            parent_reclaimed += amount

    if (
        parent_reclaimed + _CLEARANCE_EPSILON_PX < deficit
        and axis == "x"
        and _allowed(parent, "setColumnRatio", imported=True)
    ):
        operation, amount = _grid_track_reclaim(
            parent,
            parent_binding,
            target,
            deficit_px=deficit - parent_reclaimed,
        )
        if operation is not None:
            operations.append(operation)
            proofs.append(_operation_proof(parent.layoutId, "setColumnRatio"))
            parent_reclaimed += amount

    # Reducing flow gap is safe only when there is at least one sibling and no
    # hard gap floor would be crossed.  Each reduced gap contributes capacity.
    sibling_count = max(0, len(sibling_ids) - 1)
    hard_ids, footprint_ids = _hard_footprint(
        state, {target.layoutId, parent.layoutId, *sibling_ids}
    )
    hard_gap_floor_px = 0.0
    for constraint in state.spec.constraints:
        raw = constraint.model_dump(mode="json", by_alias=True, exclude_none=True)
        if constraint.strength == "hard" and raw.get("kind") == "gap":
            hard_gap_floor_px = max(
                hard_gap_floor_px,
                state.finite_number(raw.get("minimum"))
                * (
                    float(state.source.viewportWidth)
                    if raw.get("axis") == "horizontal"
                    else float(state.source.viewportHeight)
                ),
            )
    if (
        parent_reclaimed + _CLEARANCE_EPSILON_PX < deficit
        and sibling_count > 0
        and _allowed(parent, "setSpacing", imported=True)
    ):
        current_gap = float(parent.spacing)
        available_per_gap = max(0.0, current_gap - hard_gap_floor_px)
        needed_per_gap = (deficit - parent_reclaimed) / sibling_count
        reduction = min(available_per_gap, needed_per_gap)
        if reduction > _CLEARANCE_EPSILON_PX:
            operations.append(
                {
                    "op": "setSpacing",
                    "target": parent.layoutId,
                    "gap": round(current_gap - reduction, 6),
                    "precondition": {"currentGap": current_gap},
                }
            )
            proofs.append(_operation_proof(parent.layoutId, "setSpacing"))
            parent_reclaimed += reduction * sibling_count

    target_reclaimed = 0.0
    if target_binding.position in {"absolute", "fixed"}:
        operation, target_reclaimed = _absolute_target_resize(
            state,
            target,
            target_binding,
            axis=axis,
            needed_px=deficit,
        )
        if operation is None or parent_reclaimed <= _CLEARANCE_EPSILON_PX:
            return
        operations.append(operation)
        proofs.append(_operation_proof(target.layoutId, "resizeWithinBounds"))

    if not operations:
        return
    group_id = _issue_group(issue)
    blocking_core = tuple(dict.fromkeys((str(issue.get("constraintId") or group_id), *hard_ids)))
    required_fit = max(
        state.finite_number(target_metric.get("scroll_width")),
        state.finite_number(target_metric.get("scroll_height")),
    )
    client_fit = state.finite_number(
        target_metric.get("client_width" if axis == "x" else "client_height")
    )
    required_fit = client_fit + deficit
    predicted_fit = client_fit + (
        target_reclaimed if target_binding.position in {"absolute", "fixed"} else parent_reclaimed
    )
    predicted_residual = max(0.0, required_fit - predicted_fit)
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
                "predictedResidualPx": predicted_residual,
                "requiredClearancePx": 0.0,
                "predictedClearancePx": 0.0,
                "requiredTextFitPx": required_fit,
                "predictedTextFitPx": predicted_fit,
                "numericModel": {
                    "kind": "text_fit",
                    "axis": axis,
                    "clientBeforePx": client_fit,
                    "requiredPx": required_fit,
                    "parentReclaimPx": parent_reclaimed,
                    "targetResizePx": target_reclaimed,
                    "clientAfterPx": predicted_fit,
                    "residualAfterPx": predicted_residual,
                },
            },
        }
    )
