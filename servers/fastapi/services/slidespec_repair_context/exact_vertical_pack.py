"""Exact Chrome-proven vertical packing composition."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import partial
import math
from typing import Any

from services.slidespec_pipeline_performance import digest_parts
from services.slidespec_repair_context.artifacts import RepairArtifacts
from services.slidespec_repair_context.exact_operations import (
    exact_positioned_bounds as _exact_positioned_bounds,
    exact_vertical_move as _exact_vertical_move,
)
from services.slidespec_repair_context.owner_pairs import qid_chain_to_owner as _qid_chain_to_owner
from services.slidespec_repair_context.state import RepairContextState


def build_exact_vertical_pack(
    state: RepairContextState,
    artifacts: RepairArtifacts,
    *,
    viewport_width: float,
    viewport_height: float,
    required_postconditions: Mapping[str, Any],
) -> None:
    metric_by_qid = state.metric_by_qid
    semantic_by_qid = state.semantic_by_qid
    finite_number = state.finite_number
    metric_qid_is_ancestor = state.metric_qid_is_ancestor
    qid_chain_to_owner = partial(_qid_chain_to_owner, state)
    exact_vertical_move = partial(
        _exact_vertical_move,
        state,
        viewport_height=viewport_height,
    )
    exact_positioned_bounds = partial(
        _exact_positioned_bounds,
        state,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
    )
    geometry_issues = artifacts.geometry_issues
    refs_by_target = artifacts.diagnostic_refs_by_target
    required_geometry_issue_groups = artifacts.required_geometry_issue_groups
    geometry_exact_compositions = artifacts.geometry_exact_compositions
    geometry_exact_diagnostics = artifacts.geometry_exact_diagnostics
    # Exact vertical packing repair: two or more independently diagnosed
    # descendants share a bound warning owner and the same bound occluder.
    overlap_clusters: dict[str, list[Mapping[str, Any]]] = {}
    for issue in geometry_issues:
        detector = issue.get("detectorEvidence")
        detector = detector if isinstance(detector, Mapping) else {}
        occluder_qid = str(detector.get("occluderBindingQid") or detector.get("occluderQid") or "")
        raw_other_owners = issue.get("otherBoundAncestors")
        raw_other_owners = raw_other_owners if isinstance(raw_other_owners, Sequence) else ()
        occluder_qid = next(
            (
                str(owner.get("qid", ""))
                for owner in raw_other_owners
                if isinstance(owner, Mapping) and str(owner.get("qid", "")) in semantic_by_qid
            ),
            occluder_qid,
        )
        if issue.get("kind") == "nonOverlap" and detector.get("confirmed") is True and occluder_qid in semantic_by_qid:
            overlap_clusters.setdefault(occluder_qid, []).append(issue)
    for occluder_qid, cluster in overlap_clusters.items():
        target_qids = tuple(dict.fromkeys(str(item.get("qid", "")) for item in cluster if item.get("qid")))
        if len(target_qids) < 2:
            geometry_exact_diagnostics.append(
                {"stage": "cluster", "reason": "insufficient_targets", "occluderQid": occluder_qid}
            )
            continue
        common_owners = [
            candidate_qid
            for candidate_qid in semantic_by_qid
            if all(metric_qid_is_ancestor(candidate_qid, target_qid) for target_qid in target_qids)
            and not metric_qid_is_ancestor(candidate_qid, occluder_qid)
        ]
        common_owners.sort(
            key=lambda candidate_qid: max(
                len(qid_chain_to_owner(target_qid, candidate_qid)) for target_qid in target_qids
            )
        )
        warning_qid = next(
            (
                candidate_qid
                for candidate_qid in common_owners
                if isinstance(metric_by_qid.get(candidate_qid), Mapping)
                and metric_by_qid[candidate_qid].get("has_owned_paint") is True
            ),
            None,
        )
        warning_metric = metric_by_qid.get(warning_qid or "")
        occluder_metric = metric_by_qid.get(occluder_qid)
        if not isinstance(warning_metric, Mapping) or not isinstance(occluder_metric, Mapping):
            geometry_exact_diagnostics.append(
                {
                    "stage": "owners",
                    "reason": "bound_owner_missing",
                    "occluderQid": occluder_qid,
                    "warningQid": warning_qid,
                }
            )
            continue
        warning_bbox = warning_metric.get("bbox")
        occluder_bbox = occluder_metric.get("bbox")
        if not isinstance(warning_bbox, Mapping) or not isinstance(occluder_bbox, Mapping):
            geometry_exact_diagnostics.append(
                {"stage": "owners", "reason": "bbox_missing", "occluderQid": occluder_qid, "warningQid": warning_qid}
            )
            continue
        parent_qid = str(warning_metric.get("parent_qid", ""))
        siblings: list[tuple[float, str, Mapping[str, Any]]] = []
        for sibling_qid, sibling_metric in metric_by_qid.items():
            sibling_bbox = sibling_metric.get("bbox")
            if (
                str(sibling_metric.get("parent_qid", "")) != parent_qid
                or sibling_qid not in semantic_by_qid
                or not isinstance(sibling_bbox, Mapping)
            ):
                continue
            if (
                abs(finite_number(sibling_bbox.get("x")) - finite_number(warning_bbox.get("x"))) <= 1
                and abs(finite_number(sibling_bbox.get("width")) - finite_number(warning_bbox.get("width"))) <= 1
                and finite_number(sibling_bbox.get("y")) <= finite_number(warning_bbox.get("y"))
            ):
                siblings.append((finite_number(sibling_bbox.get("y")), sibling_qid, sibling_bbox))
        siblings.sort()
        sibling_qids = [item[1] for item in siblings]
        if warning_qid not in sibling_qids or sibling_qids.index(warning_qid) < 2:
            geometry_exact_diagnostics.append(
                {
                    "stage": "packing",
                    "reason": "preceding_stack_unproven",
                    "occluderQid": occluder_qid,
                    "warningQid": warning_qid,
                    "siblingQids": tuple(sibling_qids),
                }
            )
            continue
        stack_items = siblings[sibling_qids.index(warning_qid) - 2 : sibling_qids.index(warning_qid) + 1]
        probe = occluder_metric.get("static_translation_probe")
        probe = probe if isinstance(probe, Mapping) else {}
        containment = probe.get("containmentBoundsPx")
        containment = containment if isinstance(containment, Mapping) else {}
        interval = probe.get("safeTranslationIntervalPx")
        interval = interval if isinstance(interval, Mapping) else {}
        containment_source_ok = probe.get("source") == "chrome" and probe.get("telemetryComplete") is True
        if not containment:
            containing_block = occluder_metric.get("containing_block_proof")
            containing_block = containing_block if isinstance(containing_block, Mapping) else {}
            raw_bounds = containing_block.get("offsetParentBoundsPx")
            containment = raw_bounds if isinstance(raw_bounds, Mapping) else {}
            containment_source_ok = (
                containing_block.get("source") == "chrome"
                and containing_block.get("telemetryComplete") is True
                and containing_block.get("exactBoundOffsetParent") is True
            )
        boundary_bottom = finite_number(containment.get("y")) + finite_number(containment.get("height"))
        occluder_bottom = finite_number(occluder_bbox.get("y")) + finite_number(occluder_bbox.get("height"))
        occluder_width = finite_number(occluder_bbox.get("width"))
        occluder_height = finite_number(occluder_bbox.get("height"))
        # Wide responsibility bands can safely trade unused vertical chrome
        # for the hidden warning while preserving their original bottom
        # inset.  The exact candidate still has to pass Chrome DOM,
        # round-trip, OCR, and VQA; narrower/taller blocks remain fail closed.
        if occluder_height < 48.0 or occluder_height > 96.0 or occluder_width / max(1.0, occluder_height) < 8.0:
            geometry_exact_diagnostics.append(
                {
                    "stage": "capacity",
                    "reason": "compact_band_shape_unproven",
                    "occluderQid": occluder_qid,
                }
            )
            continue
        compact_height = max(
            40.0,
            float(math.ceil(occluder_height * 2.0 / 3.0)),
        )
        compact_height = min(compact_height, occluder_height - 1.0)
        occluder_candidate_top = occluder_bottom - compact_height
        occluder_delta = occluder_candidate_top - finite_number(occluder_bbox.get("y"))
        if (
            not containment_source_ok
            or occluder_delta <= 0
            or (interval and occluder_delta > finite_number(interval.get("maxY")) + 1e-6)
        ):
            geometry_exact_diagnostics.append(
                {
                    "stage": "capacity",
                    "reason": "occluder_capacity_unproven",
                    "occluderQid": occluder_qid,
                    "deltaPx": occluder_delta,
                    "containmentSourceOk": containment_source_ok,
                    "interval": dict(interval),
                }
            )
            continue
        warning_bottom = finite_number(warning_bbox.get("y")) + finite_number(warning_bbox.get("height"))
        minimum_stack_gap_px = 1.0
        deltas: dict[str, float] = {str(warning_qid): (occluder_candidate_top - minimum_stack_gap_px - warning_bottom)}
        below_candidate_top = finite_number(warning_bbox.get("y")) + deltas[str(warning_qid)]
        for _y, sibling_qid, sibling_bbox in reversed(stack_items[:-1]):
            sibling_bottom = finite_number(sibling_bbox.get("y")) + finite_number(sibling_bbox.get("height"))
            delta = -float(
                math.ceil(
                    max(
                        0.0,
                        sibling_bottom + minimum_stack_gap_px - below_candidate_top,
                    )
                )
            )
            deltas[sibling_qid] = delta
            below_candidate_top = finite_number(sibling_bbox.get("y")) + delta
        ordered_move_data = [
            (stack_items[0][1], deltas[stack_items[0][1]], "upper-stack"),
            (stack_items[1][1], deltas[stack_items[1][1]], "middle-stack"),
            (str(warning_qid), deltas[str(warning_qid)], "warning-owner"),
        ]
        translated_moves = tuple(
            move
            for qid, delta, role in ordered_move_data
            for move in (exact_vertical_move(qid, delta, role),)
            if move is not None
        )
        compact_band = exact_positioned_bounds(
            occluder_qid,
            {
                "x": finite_number(occluder_bbox.get("x")),
                "y": occluder_candidate_top,
                "width": occluder_width,
                "height": compact_height,
            },
            "occluder-band",
        )
        moves = (
            *translated_moves,
            *((compact_band,) if compact_band is not None else ()),
        )
        if len(moves) != 4:
            geometry_exact_diagnostics.append(
                {
                    "stage": "projection",
                    "reason": "move_proof_incomplete",
                    "occluderQid": occluder_qid,
                    "provedQids": tuple(move["qid"] for move in moves),
                    "requested": tuple((qid, delta) for qid, delta, _role in ordered_move_data),
                }
            )
            continue
        candidate_bounds = {move["qid"]: move["candidateBoundsPx"] for move in moves}
        relationships: list[dict[str, Any]] = []
        for first, second in zip(moves, moves[1:3], strict=False):
            first_box = first["candidateBoundsPx"]
            second_box = second["candidateBoundsPx"]
            gap = finite_number(second_box.get("y")) - (
                finite_number(first_box.get("y")) + finite_number(first_box.get("height"))
            )
            if gap < -1e-6:
                break
            relationships.append(
                {
                    "kind": "nonOverlap",
                    "axis": "y",
                    "firstQid": first["qid"],
                    "secondQid": second["qid"],
                    "candidateGapPx": gap,
                }
            )
        else:
            warning_candidate = moves[2]["candidateBoundsPx"]
            band_candidate = moves[3]["candidateBoundsPx"]
            warning_band_gap = finite_number(band_candidate.get("y")) - (
                finite_number(warning_candidate.get("y")) + finite_number(warning_candidate.get("height"))
            )
            if warning_band_gap < -1e-6:
                continue
            relationships.extend(
                (
                    {
                        "kind": "nonOverlap",
                        "axis": "y",
                        "firstQid": moves[2]["qid"],
                        "secondQid": moves[3]["qid"],
                        "candidateGapPx": warning_band_gap,
                    },
                    {
                        "kind": "canvasContainment",
                        "axis": "y",
                        "firstQid": moves[3]["qid"],
                        "boundaryPx": boundary_bottom,
                    },
                )
            )
            group_ids = tuple(sorted({str(item["issueGroupId"]) for item in cluster if item.get("issueGroupId")}))
            diagnostic_refs = tuple(
                dict.fromkeys(ref for item in cluster for ref in refs_by_target.get(str(item.get("target", "")), ()))
            )
            composition_id = (
                "composition:exact:"
                + digest_parts(
                    "geometry-exact-vertical-pack/1",
                    *group_ids,
                    *(f"{qid}:{delta}" for qid, delta, _role in ordered_move_data),
                    f"{occluder_qid}:{occluder_candidate_top}:{compact_height}",
                ).removeprefix("sha256:")[:16]
            )
            geometry_exact_compositions.append(
                {
                    "compositionId": composition_id,
                    "issueGroupIds": group_ids,
                    "required": bool(set(group_ids).intersection(required_geometry_issue_groups)),
                    "diagnosticRefs": diagnostic_refs,
                    "operations": moves,
                    "proof": {
                        "source": "chrome",
                        "telemetryComplete": True,
                        "candidateResolvesIssue": True,
                        "baselineBoundsPx": {move["qid"]: move["baselineBoundsPx"] for move in moves},
                        "candidateBoundsPx": candidate_bounds,
                        "relationships": tuple(relationships),
                        "requiredPostconditions": required_postconditions,
                        "preservedBottomInsetPx": boundary_bottom - occluder_bottom,
                        "minimumStackGapPx": minimum_stack_gap_px,
                    },
                }
            )
