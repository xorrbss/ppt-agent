"""Chrome-proven coordinated capacity for two movable owners."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from models.slidespec_v1 import LayoutNode
from services.slidespec_repair_context.state import RepairContextState


def coordinated_pair_for_owners(
    state: RepairContextState,
    *,
    issue_target_qid: str,
    issue_occluder_qids: Sequence[str],
    target_qid: str,
    peer_qid: str,
    target_node: LayoutNode,
    peer_node: LayoutNode,
    target_bbox: Mapping[str, Any],
    peer_bbox: Mapping[str, Any],
    target_depth: int,
    peer_depth: int,
) -> tuple[dict[str, Any], ...]:
    """Prove bounded two-owner separation using Chrome descendant capacity."""

    source = state.source
    metric_by_qid = state.metric_by_qid
    bound_semantic_for_metric_qid = state.bound_semantic_for_metric_qid
    finite_number = state.finite_number
    envelope_can_change = state.envelope_can_change
    metric_qid_is_ancestor = state.metric_qid_is_ancestor

    if source is None:
        return ()
    target_binding = bound_semantic_for_metric_qid(target_qid)
    peer_binding = bound_semantic_for_metric_qid(peer_qid)
    if target_binding is None or peer_binding is None:
        return ()
    target_metric = metric_by_qid.get(target_qid)
    peer_metric = metric_by_qid.get(peer_qid)
    if (
        target_metric is None
        or peer_metric is None
        or target_metric.get("occlusion_probe_version") not in {"owned-content-v2", "owned-paint-v3"}
        or peer_metric.get("occlusion_probe_version") not in {"owned-content-v2", "owned-paint-v3"}
    ):
        return ()
    viewport = {
        "x": max(1.0, float(source.viewportWidth)),
        "y": max(1.0, float(source.viewportHeight)),
    }

    def envelope_for(node: LayoutNode) -> dict[str, Any]:
        return node.constraints.model_dump(mode="json") if node.constraints is not None else {}

    def capacity(
        owner_qid: str,
        node: LayoutNode,
        axis: str,
        direction: int,
    ) -> tuple[float, dict[str, Any]]:
        envelope = envelope_for(node)
        if not envelope_can_change(envelope):
            return 0.0, {}
        owner_metric = metric_by_qid.get(owner_qid)
        content_boxes: list[tuple[str, str, tuple[float, float, float, float]]] = []
        for qid, item in metric_by_qid.items():
            if not metric_qid_is_ancestor(owner_qid, qid):
                continue
            tag = str(item.get("tag", "")).lower()
            content_bearing = item.get("has_owned_text") is True or tag in {
                "img",
                "picture",
                "svg",
                "video",
                "canvas",
            }
            if not content_bearing:
                continue
            bbox = item.get("bbox")
            bbox = bbox if isinstance(bbox, Mapping) else {}
            box = (
                finite_number(bbox.get("x")),
                finite_number(bbox.get("y")),
                finite_number(bbox.get("width")),
                finite_number(bbox.get("height")),
            )
            if box[2] > 0 and box[3] > 0:
                content_boxes.append(
                    (
                        qid,
                        "owned-text" if item.get("has_owned_text") is True else "replaced-media",
                        box,
                    )
                )
        if not content_boxes and owner_metric is not None:
            fallback = owner_metric.get("bbox")
            fallback = fallback if isinstance(fallback, Mapping) else {}
            fallback_box = (
                finite_number(fallback.get("x")),
                finite_number(fallback.get("y")),
                finite_number(fallback.get("width")),
                finite_number(fallback.get("height")),
            )
            if fallback_box[2] > 0 and fallback_box[3] > 0:
                content_boxes.append((owner_qid, "owner-bbox-fallback", fallback_box))
        boxes = [item[2] for item in content_boxes]
        if not boxes:
            return 0.0, {}
        parent_qid = str(owner_metric.get("parent_qid", "")) if owner_metric is not None else ""
        parent_metric = metric_by_qid.get(parent_qid)
        parent_bbox = (
            parent_metric.get("bbox")
            if parent_metric is not None and isinstance(parent_metric.get("bbox"), Mapping)
            else None
        )
        parent_style = (
            parent_metric.get("computed_style", parent_metric.get("style", {})) if parent_metric is not None else {}
        )
        parent_style = parent_style if isinstance(parent_style, Mapping) else {}
        overflow_value = str(
            parent_style.get(
                "overflowX" if axis == "x" else "overflowY",
                parent_style.get("overflow", "visible"),
            )
        ).lower()
        clips_to_parent = overflow_value in {
            "hidden",
            "clip",
            "scroll",
            "auto",
        }
        if axis == "x":
            container_start = (
                finite_number(parent_bbox.get("x")) if parent_bbox is not None and clips_to_parent else 0.0
            )
            container_end = (
                container_start + finite_number(parent_bbox.get("width"))
                if parent_bbox is not None and clips_to_parent
                else viewport["x"]
            )
            raw_capacity = (
                min(box[0] for box in boxes) - container_start
                if direction < 0
                else container_end - max(box[0] + box[2] for box in boxes)
            )
        else:
            container_start = (
                finite_number(parent_bbox.get("y")) if parent_bbox is not None and clips_to_parent else 0.0
            )
            container_end = (
                container_start + finite_number(parent_bbox.get("height"))
                if parent_bbox is not None and clips_to_parent
                else viewport["y"]
            )
            raw_capacity = (
                min(box[1] for box in boxes) - container_start
                if direction < 0
                else container_end - max(box[1] + box[3] for box in boxes)
            )
        envelope_capacity = finite_number(envelope.get("maxTranslation"), 0.0) * viewport[axis]
        resolved_capacity = max(0.0, min(raw_capacity, envelope_capacity))
        subtree_bounds = {
            "x": min(box[0] for box in boxes),
            "y": min(box[1] for box in boxes),
            "width": max(box[0] + box[2] for box in boxes) - min(box[0] for box in boxes),
            "height": max(box[1] + box[3] for box in boxes) - min(box[1] for box in boxes),
        }
        boundary_bounds = (
            dict(parent_bbox)
            if parent_bbox is not None and clips_to_parent
            else {
                "x": 0.0,
                "y": 0.0,
                "width": viewport["x"],
                "height": viewport["y"],
            }
        )
        return resolved_capacity, {
            "ownerQid": owner_qid,
            "axis": axis,
            "direction": direction,
            "boundary": {
                "source": "chrome",
                "kind": "clip-ancestor" if clips_to_parent else "canvas",
                "qid": parent_qid if clips_to_parent else None,
                "boundsPx": boundary_bounds,
                "overflowX": str(parent_style.get("overflowX", overflow_value)),
                "overflowY": str(parent_style.get("overflowY", overflow_value)),
            },
            "subtreeContentBoundsPx": subtree_bounds,
            "contentQids": tuple(item[0] for item in content_boxes),
            "contentKinds": {item[0]: item[1] for item in content_boxes},
            "envelopeMaxTranslationPx": envelope_capacity,
            "capacityPx": resolved_capacity,
        }

    target_x = finite_number(target_bbox.get("x"))
    target_y = finite_number(target_bbox.get("y"))
    target_width = finite_number(target_bbox.get("width"))
    target_height = finite_number(target_bbox.get("height"))
    peer_x = finite_number(peer_bbox.get("x"))
    peer_y = finite_number(peer_bbox.get("y"))
    peer_width = finite_number(peer_bbox.get("width"))
    peer_height = finite_number(peer_bbox.get("height"))
    separations = (
        ("x", -1, target_x + target_width - peer_x),
        ("x", 1, peer_x + peer_width - target_x),
        ("y", -1, target_y + target_height - peer_y),
        ("y", 1, peer_y + peer_height - target_y),
    )
    pairs: list[dict[str, Any]] = []
    for axis, direction, separation in sorted(
        separations,
        key=lambda value: (value[2], value[0], value[1]),
    ):
        if separation <= 1e-9:
            continue
        target_capacity, target_capacity_evidence = capacity(target_qid, target_node, axis, direction)
        peer_capacity, peer_capacity_evidence = capacity(peer_qid, peer_node, axis, -direction)
        if target_capacity + peer_capacity + 1e-6 < separation:
            continue
        target_amount = min(separation / 2, target_capacity)
        peer_amount = separation - target_amount
        if peer_amount > peer_capacity:
            peer_amount = peer_capacity
            target_amount = separation - peer_amount
        if (
            target_amount <= 1e-9
            or peer_amount <= 1e-9
            or target_amount > target_capacity + 1e-6
            or peer_amount > peer_capacity + 1e-6
        ):
            continue
        target_delta_px = direction * target_amount
        peer_delta_px = -direction * peer_amount
        target_delta = target_delta_px / viewport[axis]
        peer_delta = peer_delta_px / viewport[axis]
        target_xy = {
            "x": target_delta if axis == "x" else 0.0,
            "y": target_delta if axis == "y" else 0.0,
        }
        peer_xy = {
            "x": peer_delta if axis == "x" else 0.0,
            "y": peer_delta if axis == "y" else 0.0,
        }
        pairs.append(
            {
                "moves": (
                    {
                        "qid": target_qid,
                        "semanticId": target_binding[0],
                        "layoutId": target_node.layoutId,
                        "bounds": target_node.bounds.model_dump(mode="json"),
                        "mutationEnvelope": envelope_for(target_node),
                        "delta": target_xy,
                        "deltaPx": {
                            "x": target_delta_px if axis == "x" else 0.0,
                            "y": target_delta_px if axis == "y" else 0.0,
                        },
                    },
                    {
                        "qid": peer_qid,
                        "semanticId": peer_binding[0],
                        "layoutId": peer_node.layoutId,
                        "bounds": peer_node.bounds.model_dump(mode="json"),
                        "mutationEnvelope": envelope_for(peer_node),
                        "delta": peer_xy,
                        "deltaPx": {
                            "x": peer_delta_px if axis == "x" else 0.0,
                            "y": peer_delta_px if axis == "y" else 0.0,
                        },
                    },
                ),
                "proof": {
                    "axis": axis,
                    "direction": direction,
                    "separationPx": separation,
                    "capacityPx": {
                        "target": target_capacity,
                        "peer": peer_capacity,
                    },
                    "capacityEvidence": {
                        "target": target_capacity_evidence,
                        "peer": peer_capacity_evidence,
                    },
                    "source": "chrome",
                    "telemetryComplete": True,
                    "pairResolvesIssue": True,
                    "targetQid": target_qid,
                    "peerQid": peer_qid,
                    "issueTargetQid": issue_target_qid,
                    "issueOccluderQids": tuple(issue_occluder_qids),
                    "ownerDepth": {
                        "target": target_depth,
                        "peer": peer_depth,
                    },
                },
            }
        )
    return tuple(pairs)
