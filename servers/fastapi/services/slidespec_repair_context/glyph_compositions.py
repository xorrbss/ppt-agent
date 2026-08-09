"""Chrome-measured glyph and owner-move compositions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import partial
import math
from typing import Any

from models.slidespec_v1 import LayoutConstraints
from services.slidespec_pipeline_performance import digest_parts
from services.slidespec_repair_context.artifacts import RepairArtifacts
from services.slidespec_repair_context.geometry import text_fragment_bounds as _text_fragment_bounds
from services.slidespec_repair_context.state import RepairContextState


def build_glyph_compositions(state: RepairContextState, artifacts: RepairArtifacts) -> None:
    spec = state.spec
    source = state.source
    constraint_report = state.constraint_report
    binding_by_semantic = state.binding_by_semantic
    layout_by_semantic = state.layout_by_semantic
    layout_by_id = state.layout_by_id
    metric_by_qid = state.metric_by_qid
    finite_number = state.finite_number
    metric_qid_is_ancestor = state.metric_qid_is_ancestor
    text_fragment_bounds = partial(_text_fragment_bounds, state)
    geometry_issues = artifacts.geometry_issues
    required_geometry_issue_groups = artifacts.required_geometry_issue_groups
    geometry_glyph_compositions = artifacts.geometry_glyph_compositions
    if source is not None:
        for issue in geometry_issues:
            if issue.get("kind") != "nonOverlap":
                continue
            group_id = str(issue.get("issueGroupId", ""))
            detector = issue.get("detectorEvidence")
            detector = detector if isinstance(detector, Mapping) else {}
            if not group_id or detector.get("confirmed") is not True:
                continue
            owner_qids = tuple(
                str(ancestor.get("qid", ""))
                for key in ("boundAncestors", "otherBoundAncestors")
                for ancestor in (issue.get(key) if isinstance(issue.get(key), Sequence) else ())
                if isinstance(ancestor, Mapping) and ancestor.get("qid")
            )
            if not owner_qids:
                continue
            for content in spec.content:
                if content.kind != "title":
                    continue
                node = layout_by_semantic.get(content.semanticId)
                imported = binding_by_semantic.get(content.semanticId)
                if node is None or imported is None:
                    continue
                qid = imported.qid
                metric = metric_by_qid.get(qid)
                if (
                    not isinstance(metric, Mapping)
                    or metric.get("font_loaded") is not True
                    or not any(metric_qid_is_ancestor(owner_qid, qid) for owner_qid in owner_qids)
                ):
                    continue
                envelope = node.constraints or LayoutConstraints()
                allowed = set(envelope.allowedOperations or ())
                current_scale = node.fontScale
                minimum_scale = max(0.5, envelope.minFontScale)
                candidate_scale = round(max(minimum_scale, current_scale * 0.975), 6)
                fragments = tuple(
                    fragment for fragment in metric.get("text_fragments", ()) if isinstance(fragment, Mapping)
                )
                text_bounds = text_fragment_bounds(metric)
                bbox = metric.get("bbox")
                bbox = bbox if isinstance(bbox, Mapping) else {}
                if (
                    "setFontScale" not in allowed
                    or getattr(imported, "fontSizePx", None) is None
                    or not minimum_scale <= candidate_scale < current_scale
                    or text_bounds is None
                    or not fragments
                ):
                    continue
                issue_qid = str(issue.get("qid", ""))
                peer_qid = str(detector.get("occluderBindingQid") or detector.get("occluderQid") or "")
                issue_node = layout_by_id.get(str(issue.get("target", "")))
                issue_semantic = str(issue.get("semanticId", ""))
                issue_binding = binding_by_semantic.get(issue_semantic)
                issue_metric = metric_by_qid.get(issue_qid)
                peer_metric = metric_by_qid.get(peer_qid)
                raw_ancestors = issue.get("boundAncestors")
                raw_ancestors = raw_ancestors if isinstance(raw_ancestors, Sequence) else ()
                owner = next(
                    (
                        ancestor
                        for ancestor in raw_ancestors
                        if isinstance(ancestor, Mapping)
                        and metric_qid_is_ancestor(str(ancestor.get("qid", "")), qid)
                        and metric_qid_is_ancestor(str(ancestor.get("qid", "")), issue_qid)
                    ),
                    None,
                )
                owner_layout_id = str(owner.get("layoutId", "")) if owner else ""
                owner_semantic = str(owner.get("semanticId", "")) if owner else ""
                owner_qid = str(owner.get("qid", "")) if owner else ""
                owner_node = layout_by_id.get(owner_layout_id)
                owner_binding = binding_by_semantic.get(owner_semantic)
                owner_metric = metric_by_qid.get(owner_qid)
                target_bounds = detector.get("targetBoundsPx")
                target_bounds = target_bounds if isinstance(target_bounds, Mapping) else {}
                peer_bounds = detector.get("occluderBoundsPx")
                peer_bounds = peer_bounds if isinstance(peer_bounds, Mapping) else {}
                font_size_px = finite_number(metric.get("font_size"), getattr(imported, "fontSizePx", 0))
                target_bottom = finite_number(target_bounds.get("y")) + finite_number(target_bounds.get("height"))
                peer_top = finite_number(peer_bounds.get("y"))
                direct_delta_px = -float(max(1, math.ceil(font_size_px * 0.25)))
                total_delta_px = -float(max(1, math.ceil(max(0.0, target_bottom - peer_top) + 3.0)))
                owner_delta_px = total_delta_px - direct_delta_px
                issue_envelope = issue_node.constraints if issue_node is not None else None
                owner_envelope = owner_node.constraints if owner_node is not None else None
                issue_text_bounds = text_fragment_bounds(issue_metric) if isinstance(issue_metric, Mapping) else None
                peer_text_bounds = text_fragment_bounds(peer_metric) if isinstance(peer_metric, Mapping) else None
                owner_bbox = (
                    owner_metric.get("bbox")
                    if isinstance(owner_metric, Mapping) and isinstance(owner_metric.get("bbox"), Mapping)
                    else {}
                )
                moves_proven = (
                    owner is not None
                    and issue_node is not None
                    and issue_node.bounds is not None
                    and owner_node is not None
                    and owner_node.bounds is not None
                    and issue_binding is not None
                    and owner_binding is not None
                    and issue_binding.staticTranslationSafe is True
                    and owner_binding.staticTranslationSafe is True
                    and issue_envelope is not None
                    and owner_envelope is not None
                    and "translateWithinParent" in set(issue_envelope.allowedOperations or ())
                    and "translateWithinParent" in set(owner_envelope.allowedOperations or ())
                    and abs(direct_delta_px) / max(1.0, float(source.viewportHeight))
                    <= issue_envelope.maxTranslation + 1e-9
                    and abs(owner_delta_px) / max(1.0, float(source.viewportHeight))
                    <= owner_envelope.maxTranslation + 1e-9
                    and target_bottom > peer_top
                    and target_bottom + total_delta_px <= peer_top - 2.5
                    and finite_number(owner_bbox.get("y")) + owner_delta_px >= 0
                    and finite_number(owner_bbox.get("y")) + owner_delta_px + finite_number(owner_bbox.get("height"))
                    <= float(source.viewportHeight)
                )
                moves: tuple[dict[str, Any], ...] = ()
                move_proof: dict[str, Any] = {}
                if moves_proven:
                    moves = (
                        {
                            "role": "owner",
                            "target": owner_layout_id,
                            "layoutId": owner_layout_id,
                            "semanticId": owner_semantic,
                            "qid": owner_qid,
                            "currentBounds": owner_node.bounds.model_dump(mode="json"),
                            "mutationEnvelope": owner_envelope.model_dump(mode="json"),
                            "precondition": {"expectedBounds": owner_node.bounds.model_dump(mode="json")},
                            "delta": {
                                "x": 0.0,
                                "y": owner_delta_px / float(source.viewportHeight),
                            },
                            "deltaPx": {"x": 0.0, "y": owner_delta_px},
                            "staticTranslationSafe": True,
                            "roundTrip": {
                                "supported": True,
                                "verifiedProperties": ["bounds"],
                            },
                        },
                        {
                            "role": "subtitle",
                            "target": issue_node.layoutId,
                            "layoutId": issue_node.layoutId,
                            "semanticId": issue_semantic,
                            "qid": issue_qid,
                            "currentBounds": issue_node.bounds.model_dump(mode="json"),
                            "mutationEnvelope": issue_envelope.model_dump(mode="json"),
                            "precondition": {"expectedBounds": issue_node.bounds.model_dump(mode="json")},
                            "delta": {
                                "x": 0.0,
                                "y": direct_delta_px / float(source.viewportHeight),
                            },
                            "deltaPx": {"x": 0.0, "y": direct_delta_px},
                            "staticTranslationSafe": True,
                            "roundTrip": {
                                "supported": True,
                                "verifiedProperties": ["bounds"],
                            },
                        },
                    )
                    move_proof = {
                        "source": "chrome",
                        "telemetryComplete": True,
                        "candidateResolvesIssue": True,
                        "baselineSubtitleTextBoundsPx": issue_text_bounds,
                        "candidateSubtitleTextBoundsPx": (
                            {
                                **issue_text_bounds,
                                "y": issue_text_bounds["y"] + total_delta_px,
                            }
                            if issue_text_bounds is not None
                            else None
                        ),
                        "occluderTextBoundsPx": peer_text_bounds,
                        "occluderPaintBoundaryPx": {
                            "axis": "y",
                            "value": peer_top,
                        },
                        "ownerSubtreeBoundsPx": {
                            "baseline": dict(owner_bbox),
                            "candidate": {
                                **dict(owner_bbox),
                                "y": finite_number(owner_bbox.get("y")) + owner_delta_px,
                            },
                        },
                        "canvasCapacity": {
                            "viewportHeightPx": float(source.viewportHeight),
                            "topClearancePx": finite_number(owner_bbox.get("y")) + owner_delta_px,
                            "bottomClearancePx": float(source.viewportHeight)
                            - (
                                finite_number(owner_bbox.get("y"))
                                + owner_delta_px
                                + finite_number(owner_bbox.get("height"))
                            ),
                        },
                        "staticTranslationSafe": True,
                        "roundTrip": {
                            "supported": True,
                            "verifiedProperties": ["bounds", "font-size"],
                        },
                        # These are required candidate-stage observations, not
                        # baseline assertions.  The executor's rendered DOM,
                        # constraint, and source-roundtrip gates must populate
                        # and prove them before acceptance.
                        "requiredPostconditions": {
                            "domNoNewIssue": True,
                            "hardFailure": False,
                            "softCostMaximum": (constraint_report.soft_cost if constraint_report is not None else 0.0),
                            "unknownHard": [],
                            "roundTripErrors": [],
                        },
                    }
                composition_id = (
                    "composition:glyph:"
                    + digest_parts(
                        "geometry-glyph-composition/1",
                        group_id,
                        node.layoutId,
                        str(candidate_scale),
                    ).removeprefix("sha256:")[:16]
                )
                geometry_glyph_compositions.append(
                    {
                        "compositionId": composition_id,
                        "issueGroupIds": (group_id,),
                        "required": group_id in required_geometry_issue_groups,
                        "moves": moves,
                        "proof": move_proof,
                        "glyph": {
                            "target": node.layoutId,
                            "layoutId": node.layoutId,
                            "semanticId": content.semanticId,
                            "qid": qid,
                            "currentScale": current_scale,
                            "minimumScale": minimum_scale,
                            "candidateScale": candidate_scale,
                            "mutationEnvelope": envelope.model_dump(mode="json"),
                            "precondition": {
                                "expectedBounds": (
                                    node.bounds.model_dump(mode="json") if node.bounds is not None else None
                                ),
                                "expectedScale": current_scale,
                            },
                            "measurement": {
                                "source": "chrome",
                                "telemetryComplete": True,
                                "fontsReady": True,
                                "kind": "range-bounds",
                                "fontSizePx": finite_number(
                                    metric.get("font_size"),
                                    getattr(imported, "fontSizePx", 0),
                                ),
                                "measuredWidthPx": text_bounds["width"],
                                "measuredHeightPx": text_bounds["height"],
                                "availableWidthPx": finite_number(bbox.get("width"), text_bounds["width"]),
                                "availableHeightPx": finite_number(bbox.get("height"), text_bounds["height"]),
                                "textBoundsPx": text_bounds,
                                "lineBoundsPx": tuple(dict(item) for item in fragments),
                            },
                            "roundTrip": {
                                "supported": True,
                                "verifiedProperties": ["font-size"],
                            },
                        },
                    }
                )
