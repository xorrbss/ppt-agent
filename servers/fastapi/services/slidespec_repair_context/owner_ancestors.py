"""Browser-proven movable ancestor discovery."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
from typing import Any

from services.slidespec_repair_context.state import RepairContextState


def bound_ancestor_candidates(
    state: RepairContextState,
    *,
    target_qid: str,
    blocked_qid: str,
    candidate_deltas_px: Sequence[tuple[float, float]],
    physical_target_qids: Sequence[str] = (),
    physical_blocker_qids: Sequence[str] = (),
) -> tuple[dict[str, Any], ...]:
    """Return browser-proven imported ancestors in nearest-first order."""

    source = state.source
    layout_by_semantic = state.layout_by_semantic
    metric_by_qid = state.metric_by_qid
    semantic_by_qid = state.semantic_by_qid
    finite_number = state.finite_number
    metric_qid_is_ancestor = state.metric_qid_is_ancestor

    if source is None:
        return ()
    width = max(1.0, float(source.viewportWidth))
    height = max(1.0, float(source.viewportHeight))
    current_metric = metric_by_qid.get(target_qid)
    if current_metric is None or current_metric.get("occlusion_probe_version") not in {
        "owned-content-v2",
        "owned-paint-v3",
    }:
        return ()
    current_qid = str(current_metric.get("parent_qid", "")) if current_metric is not None else ""
    parent_chain = [target_qid]
    visited: set[str] = set()
    result: list[dict[str, Any]] = []
    depth = 1
    while current_qid and current_qid not in visited:
        visited.add(current_qid)
        parent_chain.append(current_qid)
        semantic_id = semantic_by_qid.get(current_qid)
        node = layout_by_semantic.get(semantic_id) if semantic_id else None
        if (
            semantic_id is not None
            and node is not None
            and node.bounds is not None
            and not metric_qid_is_ancestor(current_qid, blocked_qid)
        ):
            envelope = node.constraints.model_dump(mode="json") if node.constraints is not None else {}
            deltas: list[dict[str, Any]] = []
            proved_deltas: list[tuple[float, float, Mapping[str, Any] | None]] = [
                (dx_px, dy_px, None) for dx_px, dy_px in candidate_deltas_px
            ]
            physical_owner_failure: dict[str, Any] | None = None
            owner_metric = metric_by_qid.get(current_qid)
            owner_bbox = owner_metric.get("bbox") if isinstance(owner_metric, Mapping) else None
            owned_targets = tuple(dict.fromkeys(str(qid) for qid in physical_target_qids if qid))
            blocker_metrics = tuple(
                (
                    str(qid),
                    metric_by_qid.get(str(qid), {}).get("bbox"),
                )
                for qid in physical_blocker_qids
                if isinstance(metric_by_qid.get(str(qid)), Mapping)
            )
            blocker_metrics = tuple((qid, bbox) for qid, bbox in blocker_metrics if isinstance(bbox, Mapping))
            common_physical_owner = (
                isinstance(owner_bbox, Mapping)
                and owned_targets
                and blocker_metrics
                and all(metric_qid_is_ancestor(current_qid, qid) for qid in owned_targets)
            )
            if common_physical_owner:
                # Leaf-separation deltas do not prove that moving this common
                # owner clears every raw finding in the physical cluster.
                proved_deltas = []
                owner_x = finite_number(owner_bbox.get("x"))
                owner_y = finite_number(owner_bbox.get("y"))
                owner_width = finite_number(owner_bbox.get("width"))
                owner_height = finite_number(owner_bbox.get("height"))
                blocker_bottom = max(
                    finite_number(bbox.get("y")) + finite_number(bbox.get("height")) for _, bbox in blocker_metrics
                )
                downward_px = blocker_bottom - owner_y
                lower_boundaries = []
                for qid, metric in metric_by_qid.items():
                    bbox = metric.get("bbox")
                    if (
                        not isinstance(bbox, Mapping)
                        or qid in owned_targets
                        or qid in physical_blocker_qids
                        or metric_qid_is_ancestor(current_qid, qid)
                        or metric_qid_is_ancestor(qid, current_qid)
                    ):
                        continue
                    x = finite_number(bbox.get("x"))
                    y = finite_number(bbox.get("y"))
                    item_width = finite_number(bbox.get("width"))
                    horizontal_overlap = min(owner_x + owner_width, x + item_width) - max(owner_x, x)
                    if y >= owner_y + owner_height - 1e-6 and horizontal_overlap > 0:
                        lower_boundaries.append((y, qid))
                boundary_y, boundary_qid = min(
                    lower_boundaries,
                    default=(height, "canvas-bottom"),
                )
                candidate_owner = {
                    "x": owner_x,
                    "y": owner_y + downward_px,
                    "width": owner_width,
                    "height": owner_height,
                }
                separates = all(
                    candidate_owner["y"] >= finite_number(bbox.get("y")) + finite_number(bbox.get("height")) - 1e-6
                    for _, bbox in blocker_metrics
                )
                contained = (
                    downward_px > 1e-6
                    and candidate_owner["y"] + owner_height <= boundary_y + 1e-6
                    and candidate_owner["y"] + owner_height <= height + 1e-6
                )
                if separates and contained:
                    proved_deltas.append(
                        (
                            0.0,
                            downward_px,
                            {
                                "ownerQid": current_qid,
                                "ownerBoundsPx": dict(owner_bbox),
                                "candidateOwnerBoundsPx": candidate_owner,
                                "ownedTargetQids": owned_targets,
                                "blockerBoundsPx": {qid: dict(bbox) for qid, bbox in blocker_metrics},
                                "lowerBoundaryQid": boundary_qid,
                                "lowerBoundaryYPx": boundary_y,
                                "allTargetsOwned": True,
                                "candidateSeparatesBlockers": True,
                                "candidateContained": True,
                            },
                        )
                    )
                else:
                    physical_owner_failure = {
                        "reasonCode": ("repair.geometry.ancestor.physical_owner_no_safe_translation"),
                        "ownerQid": current_qid,
                        "ownedTargetQids": owned_targets,
                        "blockerQids": tuple(qid for qid, _ in blocker_metrics),
                        "requiredDownPx": downward_px,
                        "availableDownPx": max(
                            0.0,
                            boundary_y - owner_y - owner_height,
                        ),
                        "lowerBoundaryQid": boundary_qid,
                        "lowerBoundaryYPx": boundary_y,
                        "candidateSeparatesBlockers": separates,
                        "candidateContained": contained,
                    }
            for dx_px, dy_px, physical_proof in sorted(
                set((dx, dy, json.dumps(proof, sort_keys=True) if proof else "") for dx, dy, proof in proved_deltas),
                key=lambda value: (
                    abs(value[0]) + abs(value[1]),
                    value[0],
                    value[1],
                ),
            ):
                physical_proof = json.loads(physical_proof) if physical_proof else None
                dx, dy = dx_px / width, dy_px / height
                if abs(dx) <= 1e-12 and abs(dy) <= 1e-12:
                    continue
                moved_x = node.bounds.x + dx
                moved_y = node.bounds.y + dy
                if (
                    moved_x < -1e-12
                    or moved_y < -1e-12
                    or moved_x + node.bounds.width > 1 + 1e-12
                    or moved_y + node.bounds.height > 1 + 1e-12
                ):
                    continue
                deltas.append(
                    {
                        "x": dx,
                        "y": dy,
                        "source": "chrome",
                        "telemetryComplete": True,
                        "resolvesIssue": True,
                        "deltaPx": {"x": dx_px, "y": dy_px},
                        **({"physicalOwnerProof": physical_proof} if physical_proof is not None else {}),
                    }
                )
            if deltas or source is not None:
                result.append(
                    {
                        "qid": current_qid,
                        "semanticId": semantic_id,
                        "layoutId": node.layoutId,
                        "bounds": node.bounds.model_dump(mode="json"),
                        "containerBounds": {
                            "x": 0.0,
                            "y": 0.0,
                            "width": 1.0,
                            "height": 1.0,
                        },
                        "mutationEnvelope": envelope,
                        "candidateDeltas": tuple(deltas),
                        **(
                            {"physicalOwnerFailure": physical_owner_failure}
                            if physical_owner_failure is not None
                            else {}
                        ),
                        "coordinatedOwnerOnly": not bool(deltas),
                        "depth": depth,
                        "sameCoordinateSpace": True,
                        "ownershipProven": True,
                        "browserProof": {
                            "source": "chrome",
                            "telemetryComplete": True,
                            "targetQid": target_qid,
                            "ancestorQid": current_qid,
                            "parentQidChain": tuple(parent_chain),
                        },
                    }
                )
        item = metric_by_qid.get(current_qid)
        current_qid = str(item.get("parent_qid", "")) if item is not None else ""
        depth += 1
    return tuple(result)
