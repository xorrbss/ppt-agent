"""Bound-owner lookup and coordinated two-owner candidates."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from services.slidespec_repair_context.owner_pair_capacity import (
    coordinated_pair_for_owners,
)
from services.slidespec_repair_context.state import RepairContextState


def qid_chain_to_owner(state: RepairContextState, qid: str, owner_qid: str) -> tuple[str, ...]:
    metric_by_qid = state.metric_by_qid
    chain: list[str] = []
    current = qid
    visited: set[str] = set()
    while current and current not in visited:
        chain.append(current)
        if current == owner_qid:
            return tuple(chain)
        visited.add(current)
        item = metric_by_qid.get(current)
        current = str(item.get("parent_qid", "")) if item is not None else ""
    return ()


def bound_owner_candidates(state: RepairContextState, qid: str) -> tuple[dict[str, Any], ...]:
    semantic_by_qid = state.semantic_by_qid
    layout_by_semantic = state.layout_by_semantic
    metric_by_qid = state.metric_by_qid
    result: list[dict[str, Any]] = []
    current = qid
    visited: set[str] = set()
    depth = 0
    while current and current not in visited:
        visited.add(current)
        semantic_id = semantic_by_qid.get(current)
        node = layout_by_semantic.get(semantic_id) if semantic_id else None
        metric = metric_by_qid.get(current)
        bbox = metric.get("bbox") if metric is not None else None
        if semantic_id is not None and node is not None and node.bounds is not None and isinstance(bbox, Mapping):
            result.append(
                {
                    "qid": current,
                    "semanticId": semantic_id,
                    "node": node,
                    "bbox": bbox,
                    "depth": depth,
                    "chain": qid_chain_to_owner(state, qid, current),
                }
            )
        current = str(metric.get("parent_qid", "")) if metric is not None else ""
        depth += 1
    return tuple(result)


def bbox_intersection_area(
    state: RepairContextState,
    first: Mapping[str, Any],
    second: Mapping[str, Any],
    *,
    first_delta: tuple[float, float] = (0.0, 0.0),
    second_delta: tuple[float, float] = (0.0, 0.0),
) -> float:
    finite_number = state.finite_number
    first_x = finite_number(first.get("x")) + first_delta[0]
    first_y = finite_number(first.get("y")) + first_delta[1]
    second_x = finite_number(second.get("x")) + second_delta[0]
    second_y = finite_number(second.get("y")) + second_delta[1]
    width = min(
        first_x + finite_number(first.get("width")),
        second_x + finite_number(second.get("width")),
    ) - max(first_x, second_x)
    height = min(
        first_y + finite_number(first.get("height")),
        second_y + finite_number(second.get("height")),
    ) - max(first_y, second_y)
    return max(0.0, width) * max(0.0, height)


def coordinated_owner_pairs(
    state: RepairContextState,
    *,
    target_qid: str,
    occluder_qids: Sequence[str],
) -> tuple[dict[str, Any], ...]:
    metric_by_qid = state.metric_by_qid
    metric_qid_is_ancestor = state.metric_qid_is_ancestor
    finite_number = state.finite_number
    target_metric = metric_by_qid.get(target_qid)
    target_paint_bbox = target_metric.get("bbox") if target_metric is not None else None
    if not isinstance(target_paint_bbox, Mapping):
        return ()
    occluders = tuple(
        qid
        for qid in dict.fromkeys(occluder_qids)
        if qid != target_qid and qid in metric_by_qid and isinstance(metric_by_qid[qid].get("bbox"), Mapping)
    )
    if not occluders:
        return ()
    output: list[dict[str, Any]] = []
    seen_owner_pairs: set[tuple[str, str]] = set()
    for target_owner in bound_owner_candidates(state, target_qid):
        for paint_qid in occluders:
            for peer_owner in bound_owner_candidates(state, paint_qid):
                owner_pair = (
                    str(target_owner["qid"]),
                    str(peer_owner["qid"]),
                )
                if owner_pair in seen_owner_pairs:
                    continue
                seen_owner_pairs.add(owner_pair)
                if metric_qid_is_ancestor(owner_pair[0], owner_pair[1]) or metric_qid_is_ancestor(
                    owner_pair[1], owner_pair[0]
                ):
                    continue
                pairs = coordinated_pair_for_owners(
                    state,
                    issue_target_qid=target_qid,
                    issue_occluder_qids=occluders,
                    target_qid=owner_pair[0],
                    peer_qid=owner_pair[1],
                    target_node=target_owner["node"],
                    peer_node=peer_owner["node"],
                    target_bbox=target_owner["bbox"],
                    peer_bbox=peer_owner["bbox"],
                    target_depth=int(target_owner["depth"]),
                    peer_depth=int(peer_owner["depth"]),
                )
                for pair in pairs:
                    first_delta = pair["moves"][0]["deltaPx"]
                    second_delta = pair["moves"][1]["deltaPx"]
                    target_delta = (
                        finite_number(first_delta.get("x")),
                        finite_number(first_delta.get("y")),
                    )
                    peer_delta = (
                        finite_number(second_delta.get("x")),
                        finite_number(second_delta.get("y")),
                    )
                    peer_owned = tuple(qid for qid in occluders if metric_qid_is_ancestor(owner_pair[1], qid))
                    blocked = tuple(
                        qid
                        for qid in occluders
                        if bbox_intersection_area(
                            state,
                            target_paint_bbox,
                            metric_by_qid[qid]["bbox"],
                            first_delta=target_delta,
                            second_delta=(peer_delta if qid in peer_owned else (0.0, 0.0)),
                        )
                        > 1e-6
                    )
                    if blocked:
                        continue
                    pair["proof"].update(
                        {
                            "ownerCoverage": {
                                "target": {
                                    "ownerQid": owner_pair[0],
                                    "ownedQids": (target_qid,),
                                    "ancestorQidChains": {target_qid: target_owner["chain"]},
                                },
                                "peer": {
                                    "ownerQid": owner_pair[1],
                                    "ownedQids": peer_owned,
                                    "ancestorQidChains": {
                                        qid: qid_chain_to_owner(state, qid, owner_pair[1]) for qid in peer_owned
                                    },
                                },
                                "blockedPaintOwnerQids": (),
                            },
                            "coversBlockedPaint": True,
                            "coveredIssueGroupIds": (),
                        }
                    )
                    output.append(pair)
    return tuple(
        sorted(
            output,
            key=lambda pair: (
                -len(pair["proof"]["ownerCoverage"]["peer"]["ownedQids"]),
                pair["proof"]["ownerDepth"]["target"] + pair["proof"]["ownerDepth"]["peer"],
                sum(abs(finite_number(move["deltaPx"].get(axis))) for move in pair["moves"] for axis in ("x", "y")),
                pair["moves"][0]["qid"],
                pair["moves"][1]["qid"],
            ),
        )
    )
