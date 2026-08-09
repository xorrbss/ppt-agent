"""Stable geometry issue identities, references, and measured glyph signals."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from functools import partial

from models.slidespec_v1 import LayoutConstraints
from services.slidespec_pipeline_performance import digest_parts
from services.slidespec_repair_context.artifacts import RepairArtifacts
from services.slidespec_repair_context.geometry import text_fragment_bounds as _text_fragment_bounds
from services.slidespec_repair_context.policy import TEXT_KINDS
from services.slidespec_repair_context.state import RepairContextState


def build_issue_groups(state: RepairContextState, artifacts: RepairArtifacts) -> None:
    content_by_semantic = state.content_by_semantic
    binding_by_semantic = state.binding_by_semantic
    layout_by_id = state.layout_by_id
    metric_by_qid = state.metric_by_qid
    normalized_metrics = state.normalized_metrics
    finite_number = state.finite_number
    text_fragment_bounds = partial(_text_fragment_bounds, state)
    refs = artifacts.diagnostic_refs
    refs_by_target = artifacts.diagnostic_refs_by_target
    glyph_fits = artifacts.glyph_fit
    geometry_issues = artifacts.geometry_issues
    geometry_issue_groups = artifacts.geometry_issue_groups
    composition_qids = artifacts.composition_qids
    for issue in geometry_issues:
        detector = issue.get("detectorEvidence")
        detector = detector if isinstance(detector, Mapping) else {}
        group_digest = digest_parts(
            "geometry-issue-group/1",
            issue.get("code"),
            issue.get("qid"),
            issue.get("constraintId"),
            detector.get("occluderQid"),
        ).removeprefix("sha256:")[:16]
        group_id = f"geometry:{group_digest}"
        issue["issueGroupId"] = group_id
        constraint_id = issue.get("constraintId")
        geometry_issue_groups[group_id] = [str(constraint_id)] if constraint_id else []
        raw_ancestors = (
            *(issue.get("boundAncestors", ()) if isinstance(issue.get("boundAncestors"), Sequence) else ()),
            *(issue.get("otherBoundAncestors", ()) if isinstance(issue.get("otherBoundAncestors"), Sequence) else ()),
        )
        composition_qids[group_id] = tuple(
            dict.fromkeys(
                str(value)
                for value in (
                    issue.get("qid"),
                    detector.get("occluderQid"),
                    detector.get("occluderBindingQid"),
                    *(ancestor.get("qid") for ancestor in raw_ancestors if isinstance(ancestor, Mapping)),
                )
                if value
            )
        )
        target = str(issue.get("target", ""))
        peer_qid = str(detector.get("occluderBindingQid") or detector.get("occluderQid") or "")
        peer_target = str(issue.get("otherTarget", ""))
        issue_refs = tuple(
            str(value)
            for value in (
                f"issue-group:{group_id}",
                f"code:{issue.get('code')}",
                f"semantic:{issue.get('semanticId')}",
                f"qid:{issue.get('qid')}",
                f"qid:{peer_qid}" if peer_qid else None,
                f"constraint:{constraint_id}" if constraint_id else None,
                f"target:{target}" if target else None,
                f"target:{peer_target}" if peer_target else None,
                f"stage:{detector.get('stage')}",
                f"metric:{detector.get('metric')}",
            )
            if value is not None
        )
        refs.extend(issue_refs)
        if target:
            refs_by_target.setdefault(target, []).extend(issue_refs)

    # A Chrome-proved text-to-text overlap may have no clipping/overflow flag,
    # yet a bounded font probe can still be the smallest applicable mutation.
    # Emit only the envelope floor when measured range bounds prove that exact
    # scale separates the two text paints.  The rendered candidate DOM gate is
    # still authoritative for final resolution.
    glyph_targets = {str(item.get("target", "")) for item in glyph_fits}
    for issue in geometry_issues:
        detector = issue.get("detectorEvidence")
        detector = detector if isinstance(detector, Mapping) else {}
        if issue.get("kind") != "nonOverlap" or detector.get("confirmed") is not True:
            continue
        semantic_id = str(issue.get("semanticId", ""))
        target = str(issue.get("target", ""))
        qid = str(issue.get("qid", ""))
        occluder_qid = str(detector.get("occluderBindingQid") or detector.get("occluderQid") or "")
        node = layout_by_id.get(target)
        content = content_by_semantic.get(semantic_id)
        imported = binding_by_semantic.get(semantic_id)
        target_metric = metric_by_qid.get(qid)
        occluder_metric = metric_by_qid.get(occluder_qid)
        if (
            not target
            or target in glyph_targets
            or node is None
            or content is None
            or content.kind not in TEXT_KINDS
            or imported is None
            or getattr(imported, "fontSizePx", None) is None
            or not isinstance(target_metric, Mapping)
            or not isinstance(occluder_metric, Mapping)
        ):
            continue
        envelope = node.constraints or LayoutConstraints()
        allowed = set(envelope.allowedOperations or ())
        current_scale = node.fontScale
        minimum_scale = max(0.5, envelope.minFontScale)
        probe_scale = round(
            max(minimum_scale, current_scale * (1.0 - envelope.maxFontShrink)),
            6,
        )
        if "setFontScale" not in allowed or not minimum_scale <= probe_scale < current_scale:
            continue
        target_text = text_fragment_bounds(target_metric)
        occluder_text = text_fragment_bounds(occluder_metric)
        if target_text is None or occluder_text is None:
            continue
        target_right = target_text["x"] + target_text["width"]
        occluder_left = occluder_text["x"]
        projected_right = target_text["x"] + target_text["width"] * (probe_scale / current_scale)
        vertical_overlap = min(
            target_text["y"] + target_text["height"],
            occluder_text["y"] + occluder_text["height"],
        ) - max(target_text["y"], occluder_text["y"])
        fonts_ready = bool(normalized_metrics.get("fonts_ready")) and (
            target_metric.get("font_loaded") is True and occluder_metric.get("font_loaded") is True
        )
        candidate_separates = (
            vertical_overlap > 0.5 and target_right > occluder_left + 0.5 and projected_right <= occluder_left + 0.5
        )
        if not fonts_ready or not candidate_separates:
            continue
        glyph_fits.append(
            {
                "target": target,
                "currentScale": current_scale,
                "candidateScale": probe_scale,
                "minimumScale": minimum_scale,
                "mutationEnvelope": envelope.model_dump(mode="json"),
                "measurement": {
                    "source": "chrome",
                    "telemetryComplete": True,
                    "fontsReady": True,
                    "kind": "owned-text-separation",
                    "fontSizePx": finite_number(
                        target_metric.get("font_size"),
                        finite_number(getattr(imported, "fontSizePx", 0)),
                    ),
                    "measuredWidthPx": target_text["width"],
                    "measuredHeightPx": target_text["height"],
                    "availableWidthPx": occluder_left - target_text["x"],
                    "availableHeightPx": target_text["height"],
                    "targetTextBoundsPx": target_text,
                    "occluderTextBoundsPx": occluder_text,
                    "projectedTargetRightPx": projected_right,
                    "candidateSeparatesTextPaint": True,
                    "targetQid": qid,
                    "occluderQid": occluder_qid,
                    "overlapFraction": finite_number(detector.get("value")),
                },
                "roundTrip": {
                    "supported": True,
                    "verifiedProperties": ["font-size"],
                },
            }
        )
        glyph_targets.add(target)
