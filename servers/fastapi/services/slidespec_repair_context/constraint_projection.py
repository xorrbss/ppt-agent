"""Projection of DOM defects and hard constraints into geometry issues."""

from __future__ import annotations

from functools import partial
from collections.abc import Mapping, Sequence
from typing import Any

from services.slidespec_repair_context.artifacts import RepairArtifacts
from services.slidespec_repair_context.numeric import off_canvas_fraction as _off_canvas_fraction
from services.slidespec_repair_context.owner_ancestors import bound_ancestor_candidates as _bound_ancestor_candidates
from services.slidespec_repair_context.owner_pairs import coordinated_owner_pairs as _coordinated_owner_pairs
from services.slidespec_repair_context.state import RepairContextState


def project_geometry_issues(state: RepairContextState, artifacts: RepairArtifacts) -> None:
    spec = state.spec
    source = state.source
    evidence = state.evidence
    constraint_report = state.constraint_report
    layout_by_semantic = state.layout_by_semantic
    binding_by_semantic = state.binding_by_semantic
    layout_by_id = state.layout_by_id
    parent_by_id = state.parent_by_id
    metric_by_qid = state.metric_by_qid
    finite_number = state.finite_number
    qid_for_semantic = state.qid_for_semantic
    metric_qids_related = state.metric_qids_related
    bound_semantic_for_metric_qid = state.bound_semantic_for_metric_qid
    bound_ancestor_candidates = partial(_bound_ancestor_candidates, state)
    coordinated_owner_pairs = partial(_coordinated_owner_pairs, state)
    geometry_issues = artifacts.geometry_issues
    for defect in evidence.defects:
        if defect.code == "dom.unintended_overlap":
            node = layout_by_semantic.get(defect.semantic_id)
            target_qid = defect.qid or qid_for_semantic(defect.semantic_id)
            metric = metric_by_qid.get(target_qid) if target_qid else None
            if node is None or node.bounds is None or target_qid is None or not isinstance(metric, Mapping):
                continue
            raw_occluders = metric.get("occlusion_occluder_qids", ())
            occluder_qids = (
                tuple(str(value) for value in raw_occluders)
                if isinstance(raw_occluders, Sequence) and not isinstance(raw_occluders, (str, bytes))
                else ()
            )
            physical_overlap = defect.diagnostics.get("physical_overlap", {})
            physical_overlap = physical_overlap if isinstance(physical_overlap, Mapping) else {}
            physical_target_qids = tuple(str(value) for value in physical_overlap.get("target_qids", ()) if value)
            physical_blocker_qids = tuple(str(value) for value in physical_overlap.get("occluder_qids", ()) if value)
            related_qids = tuple(
                (
                    semantic_id,
                    qid_for_semantic(semantic_id),
                )
                for semantic_id in defect.related_semantic_ids
            )
            proven_pair = next(
                (
                    (value, semantic_id, related_qid)
                    for value in occluder_qids
                    for semantic_id, related_qid in related_qids
                    if value != target_qid
                    and value in metric_by_qid
                    and related_qid is not None
                    and metric_qids_related(value, related_qid)
                ),
                None,
            )
            if proven_pair is None:
                proven_pair = next(
                    (
                        (value, bound[0], bound[1])
                        for value in occluder_qids
                        if value != target_qid and value in metric_by_qid
                        for bound in (bound_semantic_for_metric_qid(value),)
                        if bound is not None and bound[0] != defect.semantic_id and bound[0] in layout_by_semantic
                    ),
                    None,
                )
            occluder_qid = proven_pair[0] if proven_pair is not None else None
            occluder_semantic = proven_pair[1] if proven_pair is not None else None
            related_qid = proven_pair[2] if proven_pair is not None else None
            other_node = layout_by_semantic.get(occluder_semantic) if occluder_semantic is not None else None
            if (
                occluder_qid is None
                or other_node is None
                or other_node.bounds is None
                or other_node.layoutId == node.layoutId
            ):
                continue
            probe_version = str(metric.get("occlusion_probe_version", ""))
            magnitude = max(
                finite_number(metric.get("occluded_fraction")),
                finite_number(metric.get("continuous_occluded_fraction")),
            )
            imported_global_space = (
                source is not None
                and defect.semantic_id in binding_by_semantic
                and occluder_semantic in binding_by_semantic
            )
            same_space = imported_global_space or (
                parent_by_id.get(node.layoutId) == parent_by_id.get(other_node.layoutId)
            )
            target_bbox = metric.get("bbox")
            target_bbox = target_bbox if isinstance(target_bbox, Mapping) else {}
            occluder_metric = metric_by_qid.get(occluder_qid)
            occluder_bbox = occluder_metric.get("bbox") if isinstance(occluder_metric, Mapping) else {}
            occluder_bbox = occluder_bbox if isinstance(occluder_bbox, Mapping) else {}
            target_x = finite_number(target_bbox.get("x"))
            target_y = finite_number(target_bbox.get("y"))
            target_width = finite_number(target_bbox.get("width"))
            target_height = finite_number(target_bbox.get("height"))
            occluder_x = finite_number(occluder_bbox.get("x"))
            occluder_y = finite_number(occluder_bbox.get("y"))
            occluder_width = finite_number(occluder_bbox.get("width"))
            occluder_height = finite_number(occluder_bbox.get("height"))
            target_deltas_px = (
                (occluder_x - target_x - target_width, 0.0),
                (occluder_x + occluder_width - target_x, 0.0),
                (0.0, occluder_y - target_y - target_height),
                (0.0, occluder_y + occluder_height - target_y),
            )
            other_deltas_px = tuple((-dx, -dy) for dx, dy in target_deltas_px)
            horizontal_overlap = min(
                target_x + target_width,
                occluder_x + occluder_width,
            ) - max(target_x, occluder_x)
            vertical_overlap = min(
                target_y + target_height,
                occluder_y + occluder_height,
            ) - max(target_y, occluder_y)
            minimum_displacement = (
                min(value for value in (horizontal_overlap, vertical_overlap) if value >= 0)
                if max(horizontal_overlap, vertical_overlap) >= 0
                else 0.0
            )
            stable_qids = sorted((target_qid, related_qid or occluder_qid))
            target_bound_ancestors = bound_ancestor_candidates(
                target_qid=target_qid,
                blocked_qid=occluder_qid,
                candidate_deltas_px=target_deltas_px,
                physical_target_qids=physical_target_qids,
                physical_blocker_qids=physical_blocker_qids,
            )
            physical_owner_blockers = tuple(
                failure
                for ancestor in target_bound_ancestors
                if isinstance(ancestor, Mapping)
                for failure in (ancestor.get("physicalOwnerFailure"),)
                if isinstance(failure, Mapping)
            )
            geometry_issues.append(
                {
                    "code": defect.code,
                    "semanticId": defect.semantic_id,
                    "qid": target_qid,
                    "constraintId": (f"dom:{defect.code}:{':'.join(stable_qids)}"),
                    "kind": "nonOverlap",
                    "target": node.layoutId,
                    "currentBounds": node.bounds.model_dump(mode="json"),
                    "otherTarget": other_node.layoutId,
                    "otherBounds": other_node.bounds.model_dump(mode="json"),
                    "sameCoordinateSpace": same_space,
                    "containerBounds": {
                        "x": 0.0,
                        "y": 0.0,
                        "width": 1.0,
                        "height": 1.0,
                    },
                    "requiresEnvelope": source is not None,
                    "mutationEnvelope": (
                        node.constraints.model_dump(mode="json") if node.constraints is not None else {}
                    ),
                    "otherMutationEnvelope": (
                        other_node.constraints.model_dump(mode="json") if other_node.constraints is not None else {}
                    ),
                    "boundAncestors": target_bound_ancestors,
                    "otherBoundAncestors": bound_ancestor_candidates(
                        target_qid=related_qid or occluder_qid,
                        blocked_qid=target_qid,
                        candidate_deltas_px=other_deltas_px,
                    ),
                    "coordinatedAncestorMoves": (
                        coordinated_owner_pairs(
                            target_qid=target_qid,
                            occluder_qids=occluder_qids,
                        )
                        if occluder_qids
                        else ()
                    ),
                    "detectorEvidence": {
                        "confirmed": (magnitude > 0 and probe_version in {"owned-content-v2", "owned-paint-v3"}),
                        "independentElements": same_space,
                        "stage": "dom",
                        "metric": "owned_content_occluded_fraction",
                        "value": magnitude,
                        "threshold": 0.0,
                        "occluderQid": occluder_qid,
                        "occluderBindingQid": related_qid,
                        "browserIssueQidCount": 1 + len(defect.related_semantic_ids),
                        "targetBoundsPx": dict(target_bbox),
                        "occluderBoundsPx": dict(occluder_bbox),
                        "minimumDisplacementPx": minimum_displacement,
                        "probeVersion": probe_version,
                        "physicalOwnerBlockers": physical_owner_blockers,
                    },
                }
            )
            continue
        if defect.code not in {"dom.clipping", "dom.overflow", "dom.off_canvas"}:
            continue
        node = layout_by_semantic.get(defect.semantic_id)
        qid = defect.qid or qid_for_semantic(defect.semantic_id)
        metric = metric_by_qid.get(qid) if qid else None
        if node is None or node.bounds is None or qid is None or metric is None:
            continue
        width_basis = source.viewportWidth if source is not None else 1280.0
        height_basis = source.viewportHeight if source is not None else 720.0
        magnitude = max(
            max(
                0.0,
                finite_number(metric.get("scroll_width")) - finite_number(metric.get("client_width")),
            )
            / max(1.0, width_basis),
            max(
                0.0,
                finite_number(metric.get("scroll_height")) - finite_number(metric.get("client_height")),
            )
            / max(1.0, height_basis),
        )
        if defect.code == "dom.off_canvas":
            magnitude = _off_canvas_fraction(
                metric,
                canvas_width=width_basis,
                canvas_height=height_basis,
            )
        issue_kind = "containment" if defect.code == "dom.off_canvas" else "overflow"
        geometry_issues.append(
            {
                "code": defect.code,
                "semanticId": defect.semantic_id,
                "qid": qid,
                "constraintId": (f"dom:{defect.code}:{qid}" if defect.code == "dom.off_canvas" else None),
                "kind": issue_kind,
                "target": node.layoutId,
                "currentBounds": node.bounds.model_dump(mode="json"),
                "sameCoordinateSpace": True,
                "containerBounds": {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0},
                "requiresEnvelope": source is not None,
                "mutationEnvelope": (node.constraints.model_dump(mode="json") if node.constraints is not None else {}),
                "detectorEvidence": {
                    "confirmed": magnitude > 0,
                    "stage": "dom",
                    "metric": ("canvas_overflow_fraction" if defect.code == "dom.off_canvas" else "overflow_fraction"),
                    "value": magnitude,
                    "threshold": 0.0,
                },
            }
        )

    constraint_by_id = {constraint.constraintId: constraint.model_dump(mode="json") for constraint in spec.constraints}
    semantic_by_layout = {
        node.layoutId: node.contentSemanticId for node in layout_by_id.values() if node.contentSemanticId is not None
    }
    for violation in constraint_report.violations if constraint_report else ():
        if violation.strength != "hard" or not violation.targets:
            continue
        target_id = violation.targets[0]
        target_node = layout_by_id.get(target_id)
        semantic_id = semantic_by_layout.get(target_id)
        qid = qid_for_semantic(semantic_id) if semantic_id else None
        if target_node is None or target_node.bounds is None or not semantic_id or not qid:
            continue
        peer_id = next(
            (item for item in violation.targets[1:] if item in layout_by_id),
            None,
        )
        peer_node = layout_by_id.get(peer_id) if peer_id else None
        peer_semantic_id = semantic_by_layout.get(peer_id or "")
        imported_global_space = (
            source is not None and semantic_id in binding_by_semantic and peer_semantic_id in binding_by_semantic
        )
        same_space = (
            peer_node is None or imported_global_space or (parent_by_id.get(target_id) == parent_by_id.get(peer_id))
        )
        issue: dict[str, Any] = {
            **dict(constraint_by_id.get(violation.constraint_id, {})),
            "code": violation.code,
            "semanticId": semantic_id,
            "qid": qid,
            "constraintId": violation.constraint_id,
            "kind": violation.kind,
            "target": target_id,
            "currentBounds": target_node.bounds.model_dump(mode="json"),
            "sameCoordinateSpace": same_space,
            "requiresEnvelope": source is not None,
            "mutationEnvelope": (
                target_node.constraints.model_dump(mode="json") if target_node.constraints is not None else {}
            ),
            "detectorEvidence": {
                "confirmed": violation.magnitude > 0,
                "stage": "invariant",
                "metric": "constraint_residual",
                "value": violation.magnitude,
                "threshold": 0.0,
                "independentElements": violation.kind != "nonOverlap" or same_space,
            },
        }
        if peer_node is not None and peer_node.bounds is not None:
            issue.update(
                {
                    "otherTarget": peer_id,
                    "otherBounds": peer_node.bounds.model_dump(mode="json"),
                    "otherMutationEnvelope": (
                        peer_node.constraints.model_dump(mode="json") if peer_node.constraints is not None else {}
                    ),
                }
            )
        issue["containerBounds"] = {
            "x": 0.0,
            "y": 0.0,
            "width": 1.0,
            "height": 1.0,
        }
        geometry_issues.append(issue)
