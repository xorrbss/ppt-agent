"""Exact coupled repair for an adjacent bottom off-canvas stack."""

from __future__ import annotations

from collections.abc import Mapping
from functools import partial
import math
from typing import Any

from services.slidespec_pipeline_performance import digest_parts
from services.slidespec_repair_context.artifacts import RepairArtifacts
from services.slidespec_repair_context.exact_operations import exact_vertical_move as _exact_vertical_move
from services.slidespec_repair_context.state import RepairContextState


def build_exact_bottom_stack(
    state: RepairContextState,
    artifacts: RepairArtifacts,
    *,
    viewport_height: float,
    required_postconditions: Mapping[str, Any],
) -> None:
    metric_by_qid = state.metric_by_qid
    semantic_by_qid = state.semantic_by_qid
    finite_number = state.finite_number
    metric_qid_is_ancestor = state.metric_qid_is_ancestor
    exact_vertical_move = partial(
        _exact_vertical_move,
        state,
        viewport_height=viewport_height,
    )
    geometry_issues = artifacts.geometry_issues
    refs_by_target = artifacts.diagnostic_refs_by_target
    geometry_exact_compositions = artifacts.geometry_exact_compositions
    # Exact bottom-stack repair: an off-canvas bound node and its immediately
    # preceding bound sibling move together, preserving their measured gap.
    for issue in geometry_issues:
        if issue.get("code") != "dom.off_canvas":
            continue
        detector = issue.get("detectorEvidence")
        detector = detector if isinstance(detector, Mapping) else {}
        target_qid = str(issue.get("qid", ""))
        target_metric = metric_by_qid.get(target_qid)
        target_bbox = (
            target_metric.get("bbox")
            if isinstance(target_metric, Mapping) and isinstance(target_metric.get("bbox"), Mapping)
            else None
        )
        if detector.get("confirmed") is not True or target_bbox is None:
            continue
        overflow_px = finite_number(target_bbox.get("y")) + finite_number(target_bbox.get("height")) - viewport_height
        if overflow_px <= 0:
            continue
        parent_qid = str(target_metric.get("parent_qid", ""))
        target_top = finite_number(target_bbox.get("y"))
        target_width = finite_number(target_bbox.get("width"))
        predecessors: list[tuple[float, str, Mapping[str, Any]]] = []
        for candidate_qid, candidate_metric in metric_by_qid.items():
            if (
                candidate_qid == target_qid
                or str(candidate_metric.get("parent_qid", "")) != parent_qid
                or candidate_qid not in semantic_by_qid
            ):
                continue
            candidate_bbox = candidate_metric.get("bbox")
            if not isinstance(candidate_bbox, Mapping):
                continue
            candidate_bottom = finite_number(candidate_bbox.get("y")) + finite_number(candidate_bbox.get("height"))
            horizontal_overlap = min(
                finite_number(candidate_bbox.get("x")) + finite_number(candidate_bbox.get("width")),
                finite_number(target_bbox.get("x")) + target_width,
            ) - max(
                finite_number(candidate_bbox.get("x")),
                finite_number(target_bbox.get("x")),
            )
            if candidate_bottom <= target_top + 0.5 and horizontal_overlap >= 0.8 * min(
                finite_number(candidate_bbox.get("width")), target_width
            ):
                predecessors.append((candidate_bottom, candidate_qid, candidate_bbox))
        if not predecessors:
            continue
        predecessor_bottom, predecessor_qid, predecessor_bbox = max(predecessors)
        if target_top - predecessor_bottom > 2.0:
            continue
        delta_px = -float(math.ceil(overflow_px - 1e-9))
        moves = tuple(
            move
            for move in (
                exact_vertical_move(predecessor_qid, delta_px, "preceding-sibling"),
                exact_vertical_move(target_qid, delta_px, "off-canvas-target"),
            )
            if move is not None
        )
        if len(moves) != 2:
            continue
        candidate_target = moves[1]["candidateBoundsPx"]
        _candidate_predecessor = moves[0]["candidateBoundsPx"]
        if (
            finite_number(candidate_target.get("y")) + finite_number(candidate_target.get("height"))
            > viewport_height + 1e-6
        ):
            continue
        group_ids = tuple(
            sorted(
                {
                    str(candidate["issueGroupId"])
                    for candidate in geometry_issues
                    if candidate.get("issueGroupId")
                    and (
                        str(candidate.get("qid", "")) == target_qid
                        or metric_qid_is_ancestor(target_qid, str(candidate.get("qid", "")))
                    )
                }
            )
        )
        diagnostic_refs = tuple(
            dict.fromkeys(
                ref
                for candidate in geometry_issues
                if str(candidate.get("issueGroupId", "")) in group_ids
                for ref in refs_by_target.get(str(candidate.get("target", "")), ())
            )
        )
        composition_id = (
            "composition:exact:"
            + digest_parts(
                "geometry-exact-bottom-stack/1",
                target_qid,
                predecessor_qid,
                str(delta_px),
            ).removeprefix("sha256:")[:16]
        )
        geometry_exact_compositions.append(
            {
                "compositionId": composition_id,
                "issueGroupIds": group_ids,
                # Exact off-canvas containment is itself a required target
                # identity even though the legacy required-group list is
                # limited to non-overlap compositions.
                "required": True,
                "diagnosticRefs": diagnostic_refs,
                "operations": moves,
                "proof": {
                    "source": "chrome",
                    "telemetryComplete": True,
                    "candidateResolvesIssue": True,
                    "baselineBoundsPx": {
                        predecessor_qid: dict(predecessor_bbox),
                        target_qid: dict(target_bbox),
                    },
                    "candidateBoundsPx": {move["qid"]: move["candidateBoundsPx"] for move in moves},
                    "relationships": (
                        {
                            "kind": "adjacency",
                            "axis": "y",
                            "firstQid": predecessor_qid,
                            "secondQid": target_qid,
                            "baselineGapPx": target_top - predecessor_bottom,
                            "candidateGapPx": target_top - predecessor_bottom,
                        },
                        {
                            "kind": "canvasContainment",
                            "axis": "y",
                            "firstQid": target_qid,
                            "boundaryPx": viewport_height,
                        },
                    ),
                    "requiredPostconditions": required_postconditions,
                },
            }
        )
