"""Exact Chrome-anchored operation constructors."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from models.slidespec_v1 import LayoutConstraints
from services.slidespec_repair_context.state import RepairContextState


def exact_vertical_move(
    state: RepairContextState,
    qid: str,
    delta_px: float,
    role: str,
    *,
    viewport_height: float,
) -> dict[str, Any] | None:
    semantic_by_qid = state.semantic_by_qid
    layout_by_semantic = state.layout_by_semantic
    binding_by_semantic = state.binding_by_semantic
    metric_by_qid = state.metric_by_qid
    finite_number = state.finite_number
    semantic_id = semantic_by_qid.get(qid)
    node = layout_by_semantic.get(semantic_id) if semantic_id else None
    binding = binding_by_semantic.get(semantic_id) if semantic_id else None
    metric = metric_by_qid.get(qid)
    if (
        node is None
        or node.bounds is None
        or binding is None
        or not isinstance(metric, Mapping)
        or abs(delta_px) <= 1e-9
    ):
        return None
    envelope = node.constraints or LayoutConstraints()
    if (
        envelope.locked
        or "translateWithinParent" not in set(envelope.allowedOperations or ())
        or abs(delta_px) / viewport_height > envelope.maxTranslation + 1e-9
    ):
        return None
    projection: dict[str, Any]
    if binding.position == "static":
        probe = metric.get("static_translation_probe")
        probe = probe if isinstance(probe, Mapping) else {}
        interval = probe.get("safeTranslationIntervalPx")
        interval = interval if isinstance(interval, Mapping) else {}
        if (
            binding.transform != "none"
            or binding.staticTranslationSafe is not True
            or probe.get("source") != "chrome"
            or probe.get("telemetryComplete") is not True
            or probe.get("stackingStable") is not True
            or delta_px < finite_number(interval.get("minY")) - 1e-6
            or delta_px > finite_number(interval.get("maxY")) + 1e-6
        ):
            return None
        projection = {
            "source": "chrome",
            "telemetryComplete": True,
            "mechanism": "static-transform",
            "staticTranslationSafe": True,
            "safeTranslationIntervalPx": dict(interval),
        }
    elif binding.position in {"relative", "absolute", "fixed", "sticky"}:
        proof = metric.get("containing_block_proof")
        proof = proof if isinstance(proof, Mapping) else {}
        vertical_anchor = "top" if binding.topPx is not None else "bottom" if binding.bottomPx is not None else None
        if (
            vertical_anchor is None
            or proof.get("source") != "chrome"
            or proof.get("telemetryComplete") is not True
            or proof.get("exactBoundOffsetParent") is not True
        ):
            return None
        projection = {
            "source": "chrome",
            "telemetryComplete": True,
            "mechanism": "position-anchor",
            "position": binding.position,
            "verticalAnchor": vertical_anchor,
            "offsetParentQid": proof.get("offsetParentQid"),
        }
    else:
        return None
    bbox = metric.get("bbox")
    if not isinstance(bbox, Mapping):
        return None
    return {
        "op": "translateWithinParent",
        "role": role,
        "target": node.layoutId,
        "layoutId": node.layoutId,
        "semanticId": semantic_id,
        "qid": qid,
        "currentBounds": node.bounds.model_dump(mode="json"),
        "precondition": {"expectedBounds": node.bounds.model_dump(mode="json")},
        "value": {"x": 0.0, "y": delta_px / viewport_height},
        "valuePx": {"x": 0.0, "y": delta_px},
        "mutationEnvelope": envelope.model_dump(mode="json"),
        "projectionProof": projection,
        "roundTrip": {
            "supported": True,
            "verifiedProperties": ["bounds"],
        },
        "baselineBoundsPx": dict(bbox),
        "candidateBoundsPx": {
            **dict(bbox),
            "y": finite_number(bbox.get("y")) + delta_px,
        },
    }


def exact_positioned_bounds(
    state: RepairContextState,
    qid: str,
    candidate_bounds_px: Mapping[str, Any],
    role: str,
    *,
    viewport_width: float,
    viewport_height: float,
) -> dict[str, Any] | None:
    """Build a Chrome-anchored exact setBounds operation."""

    semantic_by_qid = state.semantic_by_qid
    layout_by_semantic = state.layout_by_semantic
    binding_by_semantic = state.binding_by_semantic
    metric_by_qid = state.metric_by_qid
    finite_number = state.finite_number
    semantic_id = semantic_by_qid.get(qid)
    node = layout_by_semantic.get(semantic_id) if semantic_id else None
    binding = binding_by_semantic.get(semantic_id) if semantic_id else None
    metric = metric_by_qid.get(qid)
    if (
        node is None
        or node.bounds is None
        or binding is None
        or not isinstance(metric, Mapping)
        or binding.position not in {"relative", "absolute", "fixed", "sticky"}
    ):
        return None
    envelope = node.constraints or LayoutConstraints()
    allowed = set(envelope.allowedOperations or ())
    if envelope.locked or envelope.lockSize or "setBounds" not in allowed:
        return None
    proof = metric.get("containing_block_proof")
    proof = proof if isinstance(proof, Mapping) else {}
    vertical_anchor = "top" if binding.topPx is not None else "bottom" if binding.bottomPx is not None else None
    horizontal_anchor = "left" if binding.leftPx is not None else "right" if binding.rightPx is not None else None
    if (
        vertical_anchor is None
        or horizontal_anchor is None
        or proof.get("source") != "chrome"
        or proof.get("telemetryComplete") is not True
        or proof.get("exactBoundOffsetParent") is not True
    ):
        return None
    bbox = metric.get("bbox")
    if not isinstance(bbox, Mapping):
        return None
    candidate = {
        "x": finite_number(candidate_bounds_px.get("x")) / viewport_width,
        "y": finite_number(candidate_bounds_px.get("y")) / viewport_height,
        "width": finite_number(candidate_bounds_px.get("width")) / viewport_width,
        "height": finite_number(candidate_bounds_px.get("height")) / viewport_height,
    }
    current = node.bounds.model_dump(mode="json")
    position_delta = max(
        abs(candidate["x"] - current["x"]),
        abs(candidate["y"] - current["y"]),
    )
    size_delta = max(
        abs(candidate["width"] - current["width"]),
        abs(candidate["height"] - current["height"]),
    )
    if (
        position_delta > envelope.maxTranslation + 1e-9
        or size_delta > envelope.maxResizeDelta + 1e-9
        or candidate["x"] < 0
        or candidate["y"] < 0
        or candidate["x"] + candidate["width"] > 1 + 1e-9
        or candidate["y"] + candidate["height"] > 1 + 1e-9
    ):
        return None
    return {
        "op": "setBounds",
        "role": role,
        "target": node.layoutId,
        "layoutId": node.layoutId,
        "semanticId": semantic_id,
        "qid": qid,
        "currentBounds": current,
        "precondition": {"expectedBounds": current},
        "bounds": candidate,
        "boundsPx": dict(candidate_bounds_px),
        "mutationEnvelope": envelope.model_dump(mode="json"),
        "projectionProof": {
            "source": "chrome",
            "telemetryComplete": True,
            "mechanism": "position-anchor",
            "position": binding.position,
            "verticalAnchor": vertical_anchor,
            "horizontalAnchor": horizontal_anchor,
            "offsetParentQid": proof.get("offsetParentQid"),
        },
        "roundTrip": {
            "supported": True,
            "verifiedProperties": ["bounds"],
        },
        "baselineBoundsPx": dict(bbox),
        "candidateBoundsPx": dict(candidate_bounds_px),
    }
