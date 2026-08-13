"""Fail-closed deterministic plans for coupled layout defects.

This module is deliberately downstream of browser measurement and upstream of
lazy validation/rendering.  It accepts only closed ``coupledRepairs`` proofs:
the producer must identify the blocking core, complete constraint footprint,
exact source-round-trip capability for every operation, and a conservative
post-mutation clearance/text-fit prediction.  A candidate that cannot prove it
will clear the measured deficit is rejected here and never consumes a render.

The hard constraint graph and acceptance gates are not interpreted here.  The
proof merely binds the plan to those dependencies; the normal solver, DOM, OCR,
VQA and source round-trip gates remain authoritative after application.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


_EPSILON_PX = 0.5
_SAFE_OPERATIONS = frozenset(
    {
        "setSpacing",
        "setPadding",
        "setTrackSize",
        "setColumnRatio",
        "setBounds",
        "resizeWithinBounds",
        "translateWithinParent",
        "setFontScale",
    }
)
_PARENT_FIRST_OPERATIONS = frozenset(
    {"setSpacing", "setPadding", "setTrackSize", "setColumnRatio", "setBounds"}
)
_MAX_OPERATIONS = 8
_MAX_FOOTPRINT = 8
_MIN_OCR_SAFE_FONT_SCALE = 0.92


@dataclass(frozen=True, slots=True)
class CoupledRepairCandidate:
    issue_group_id: str
    issue_code: str
    semantic_id: str
    qid: str
    constraint_id: str | None
    target: str
    scope: tuple[str, ...]
    operations: tuple[dict[str, Any], ...]
    diagnostic_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CoupledRepairRejection:
    issue_group_id: str
    issue_code: str
    target: str
    reason_code: str
    stage: str = "coupled_preflight"


@dataclass(frozen=True, slots=True)
class CoupledRepairBuildResult:
    candidates: tuple[CoupledRepairCandidate, ...]
    rejections: tuple[CoupledRepairRejection, ...] = ()


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> tuple[object, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(value)
    return ()


def _number(value: object, default: float = math.nan) -> float:
    if isinstance(value, bool):
        return default
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _strings(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in _sequence(value) if str(item))


def _operation_signature(operations: Sequence[Mapping[str, Any]]) -> str:
    return json.dumps(operations, sort_keys=True, separators=(",", ":"))


def _close(first: float, second: float) -> bool:
    return math.isfinite(first) and math.isfinite(second) and abs(first - second) <= _EPSILON_PX


def _validate_numeric_model(proof: Mapping[str, Any]) -> str | None:
    """Recompute the claimed post-mutation result from Chrome pixel inputs."""

    model = _mapping(proof.get("numericModel"))
    if not model:
        # Sanitized historical fixtures predate the explicit arithmetic model.
        # Newly projected candidates always include one; retain fixture replay
        # compatibility while checking every model that reaches this boundary.
        return None
    kind = str(model.get("kind", ""))
    required = _number(model.get("requiredPx"))
    residual = _number(model.get("residualAfterPx"))
    parent_reclaim = _number(model.get("parentReclaimPx"), 0.0)
    if (
        not math.isfinite(required)
        or not math.isfinite(residual)
        or parent_reclaim <= _EPSILON_PX
    ):
        return "repair.coupled.numeric_prediction_inconsistent"
    if kind == "text_fit":
        before = _number(model.get("clientBeforePx"))
        after = _number(model.get("clientAfterPx"))
        target_resize = _number(model.get("targetResizePx"), 0.0)
        effective_gain = target_resize if target_resize > 0 else parent_reclaim
        calculated_after = before + effective_gain
        calculated_deficit = max(0.0, required - before)
        calculated_residual = max(0.0, required - calculated_after)
        claims = (
            (after, calculated_after),
            (residual, calculated_residual),
            (_number(proof.get("baselineDeficitPx")), calculated_deficit),
            (_number(proof.get("requiredTextFitPx")), required),
            (_number(proof.get("predictedTextFitPx")), calculated_after),
            (_number(proof.get("predictedResidualPx")), calculated_residual),
        )
    elif kind == "clearance":
        before = _number(model.get("clearanceBeforePx"))
        after = _number(model.get("clearanceAfterPx"))
        calculated_deficit = max(0.0, required - before)
        calculated_after = before + calculated_deficit
        calculated_residual = max(0.0, required - calculated_after)
        claims = (
            (after, calculated_after),
            (residual, calculated_residual),
            (_number(proof.get("baselineDeficitPx")), calculated_deficit),
            (_number(proof.get("requiredClearancePx")), required),
            (_number(proof.get("predictedClearancePx")), calculated_after),
            (_number(proof.get("predictedResidualPx")), calculated_residual),
        )
    else:
        return "repair.coupled.numeric_prediction_inconsistent"
    if any(not _close(claimed, calculated) for claimed, calculated in claims):
        return "repair.coupled.numeric_prediction_inconsistent"
    return None


def _validate_proof(item: Mapping[str, Any]) -> str | None:
    proof = _mapping(item.get("proof"))
    if proof.get("source") != "chrome" or proof.get("telemetryComplete") is not True:
        return "repair.coupled.chrome_proof_incomplete"
    if proof.get("hardGatePolicy") != "preserve":
        return "repair.coupled.hard_gate_proof_missing"
    numeric_reason = _validate_numeric_model(proof)
    if numeric_reason is not None:
        return numeric_reason

    baseline_deficit = _number(proof.get("baselineDeficitPx"))
    predicted_residual = _number(proof.get("predictedResidualPx"))
    if not math.isfinite(baseline_deficit) or baseline_deficit <= _EPSILON_PX:
        return "repair.coupled.baseline_deficit_unproven"
    if not math.isfinite(predicted_residual) or predicted_residual > _EPSILON_PX:
        return "repair.coupled.predicted_resolution_insufficient"

    required_clearance = _number(proof.get("requiredClearancePx"), 0.0)
    predicted_clearance = _number(proof.get("predictedClearancePx"), 0.0)
    if predicted_clearance + _EPSILON_PX < required_clearance:
        return "repair.coupled.predicted_clearance_insufficient"

    required_text_fit = _number(proof.get("requiredTextFitPx"), 0.0)
    predicted_text_fit = _number(proof.get("predictedTextFitPx"), 0.0)
    if required_text_fit > 0 and predicted_text_fit + _EPSILON_PX < required_text_fit:
        return "repair.coupled.predicted_text_fit_insufficient"
    return None


def _validate_footprint(
    item: Mapping[str, Any], operations: Sequence[Mapping[str, Any]]
) -> str | None:
    footprint = _mapping(item.get("constraintFootprint"))
    layout_ids = frozenset(_strings(footprint.get("layoutIds")))
    blocking_core = frozenset(_strings(footprint.get("blockingCore")))
    parent_space = str(footprint.get("parentSpaceId", ""))
    target = str(item.get("target", ""))
    operation_targets = frozenset(str(operation.get("target", "")) for operation in operations)
    if (
        not target
        or target not in layout_ids
        or not parent_space
        or parent_space not in layout_ids
        or not blocking_core
        or not operation_targets
        or not operation_targets.issubset(layout_ids)
        or len(layout_ids) > _MAX_FOOTPRINT
    ):
        return "repair.coupled.constraint_footprint_incomplete"
    # A coupled candidate must modify parent/sibling capacity, not merely repeat
    # the historical target-only move that failed on the observed deck.
    if operation_targets == {target}:
        return "repair.coupled.target_only_disallowed"
    constraint_id = str(item.get("constraintId") or "")
    issue_group_id = str(item.get("issueGroupId", ""))
    if constraint_id and constraint_id not in blocking_core:
        return "repair.coupled.blocking_core_mismatch"
    if not constraint_id and issue_group_id not in blocking_core:
        return "repair.coupled.blocking_core_mismatch"
    return None


def _validate_operations(
    item: Mapping[str, Any], operations: Sequence[Mapping[str, Any]]
) -> str | None:
    if not operations or len(operations) > _MAX_OPERATIONS:
        return "repair.coupled.operation_budget_exceeded"
    proofs = _sequence(item.get("operationProofs"))
    proof_by_key = {
        (str(proof.get("target", "")), str(proof.get("op", ""))): proof
        for value in proofs
        if (proof := _mapping(value))
    }
    seen: set[tuple[str, str]] = set()
    for operation in operations:
        op = str(operation.get("op", ""))
        target = str(operation.get("target", ""))
        key = (target, op)
        if op not in _SAFE_OPERATIONS or not target or key in seen:
            return "repair.coupled.operation_unsupported"
        seen.add(key)
        operation_proof = _mapping(proof_by_key.get(key))
        if (
            operation_proof.get("mutationAllowed") is not True
            or operation_proof.get("roundTripSupported") is not True
            or operation_proof.get("baselineMatched") is not True
        ):
            return "repair.coupled.operation_projection_unproven"

    font_operations = [operation for operation in operations if operation.get("op") == "setFontScale"]
    if font_operations:
        if not any(operation.get("op") in _PARENT_FIRST_OPERATIONS for operation in operations):
            return "repair.coupled.font_shrink_without_parent_reclaim"
        ocr = _mapping(item.get("ocrSafety"))
        if (
            ocr.get("textNodeMappingComplete") is not True
            or ocr.get("tokenMappingComplete") is not True
        ):
            return "repair.coupled.ocr_mapping_incomplete"
        retries = tuple(_number(value) for value in _sequence(ocr.get("retryScales")))
        if not retries or len(retries) > 3 or any(
            not math.isfinite(value) or value < 1.0 or value > 2.0 for value in retries
        ):
            return "repair.coupled.ocr_retry_budget_invalid"
        for operation in font_operations:
            scale = _number(operation.get("scale"))
            bounds = _mapping(operation.get("bounds"))
            minimum = max(
                _MIN_OCR_SAFE_FONT_SCALE,
                _number(bounds.get("minScale"), _MIN_OCR_SAFE_FONT_SCALE),
            )
            if not math.isfinite(scale) or scale < minimum or scale >= 1.0:
                return "repair.coupled.font_shrink_out_of_bounds"
    return None


def build_coupled_repair_candidates(
    context: Mapping[str, Any],
) -> CoupledRepairBuildResult:
    """Promote only fully proved coupled candidates before lazy validation."""

    candidates: list[CoupledRepairCandidate] = []
    rejections: list[CoupledRepairRejection] = []
    raw_items = tuple(_mapping(value) for value in _sequence(context.get("coupledRepairs")))
    ordered = sorted(
        raw_items,
        key=lambda item: (
            str(item.get("issueGroupId", "")),
            str(item.get("code", "")),
            str(item.get("target", "")),
        ),
    )
    for item in ordered:
        issue_group_id = str(item.get("issueGroupId", ""))
        issue_code = str(item.get("code", ""))
        semantic_id = str(item.get("semanticId", ""))
        qid = str(item.get("qid", ""))
        target = str(item.get("target", ""))
        operations = tuple(_mapping(value) for value in _sequence(item.get("operations")))

        reason: str | None = None
        if not all((issue_group_id, issue_code, semantic_id, qid, target)):
            reason = "repair.coupled.identity_missing"
        if reason is None:
            reason = _validate_footprint(item, operations)
        if reason is None:
            reason = _validate_operations(item, operations)
        if reason is None:
            reason = _validate_proof(item)
        if reason is not None:
            rejections.append(
                CoupledRepairRejection(
                    issue_group_id=issue_group_id,
                    issue_code=issue_code,
                    target=target,
                    reason_code=reason,
                )
            )
            continue

        footprint = _mapping(item.get("constraintFootprint"))
        refs = tuple(
            dict.fromkeys(
                (
                    f"issue-group:{issue_group_id}",
                    f"code:{issue_code}",
                    f"semantic:{semantic_id}",
                    f"qid:{qid}",
                    *(
                        (f"constraint:{item['constraintId']}",)
                        if item.get("constraintId")
                        else ()
                    ),
                    *(f"footprint:{value}" for value in _strings(footprint.get("layoutIds"))),
                    "coupled-proof:chrome",
                    "hard-gates:preserved",
                )
            )
        )
        candidates.append(
            CoupledRepairCandidate(
                issue_group_id=issue_group_id,
                issue_code=issue_code,
                semantic_id=semantic_id,
                qid=qid,
                constraint_id=(str(item["constraintId"]) if item.get("constraintId") else None),
                target=target,
                scope=_strings(footprint.get("layoutIds")),
                operations=tuple(dict(operation) for operation in operations),
                diagnostic_refs=refs,
            )
        )

    unique = {
        (candidate.issue_group_id, _operation_signature(candidate.operations)): candidate
        for candidate in candidates
    }
    return CoupledRepairBuildResult(
        candidates=tuple(
            sorted(
                unique.values(),
                key=lambda candidate: (
                    len(candidate.operations),
                    len({operation["target"] for operation in candidate.operations}),
                    _operation_signature(candidate.operations),
                    candidate.issue_group_id,
                ),
            )
        ),
        rejections=tuple(rejections),
    )


__all__ = [
    "CoupledRepairBuildResult",
    "CoupledRepairCandidate",
    "CoupledRepairRejection",
    "build_coupled_repair_candidates",
]
