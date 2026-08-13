"""Iteration guards and deterministic accepted-candidate ranking."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json
from typing import Any

from models.slidespec_v1 import RepairPlan
from services.slidespec_repair_pipeline import ReasonCode, RenderedCandidate

def _operation_payloads(plan: RepairPlan) -> tuple[dict[str, Any], ...]:
    return tuple(
        operation.model_dump(mode="json", by_alias=True, exclude_none=True)
        for operation in plan.operations
    )


def _plan_signature(plan: RepairPlan) -> str:
    payload = {
        "slide": plan.slide_id,
        "scope": sorted(plan.scope),
        "operations": _operation_payloads(plan),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _operation_state_signature(plan: RepairPlan) -> str:
    """Signature for A->B->A oscillation detection, independent of plan id."""

    encoded = json.dumps(
        _operation_payloads(plan), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class RepairIterationGuard:
    """Request/deck-scoped monotonic repair history for repeated slide passes."""

    max_incremental_applies: int = 12
    _applied_signatures: dict[str, set[str]] = field(default_factory=dict)
    _operation_history: dict[str, list[str]] = field(default_factory=dict)
    _apply_counts: dict[str, int] = field(default_factory=dict)

    def rejection_reason(self, plan: RepairPlan) -> ReasonCode | None:
        slide_id = plan.slide_id
        if self._apply_counts.get(slide_id, 0) >= self.max_incremental_applies:
            return "repair.iteration_limit"
        operation_state = _operation_state_signature(plan)
        history = self._operation_history.get(slide_id, ())
        if len(history) >= 2 and operation_state == history[-2]:
            return "repair.oscillation_detected"
        signature = _plan_signature(plan)
        if signature in self._applied_signatures.get(slide_id, set()):
            return "repair.plan_signature_repeated"
        return None

    def record_applied(self, plan: RepairPlan) -> None:
        slide_id = plan.slide_id
        self._applied_signatures.setdefault(slide_id, set()).add(
            _plan_signature(plan)
        )
        self._operation_history.setdefault(slide_id, []).append(
            _operation_state_signature(plan)
        )
        self._apply_counts[slide_id] = self._apply_counts.get(slide_id, 0) + 1


@dataclass(frozen=True, slots=True)
class _AcceptedCandidate:
    plan: RepairPlan
    artifact: RenderedCandidate
    quality_gain: float
    quality_gain_interval: tuple[float, float]
    constraint_cost: float
    realized_cost: tuple[int, int, float, float, int, str]
    resolved_issue_count: int = 0
    resolved_issue_severity: float = 0
    remaining_issue_count: int | None = None
    quality_margin: float | None = None
    diagnostics: Mapping[str, object] = field(default_factory=dict)


def _rank_accepted_candidates(
    candidates: Sequence[_AcceptedCandidate],
) -> tuple[_AcceptedCandidate, ...]:
    """Rank safe candidates for the single maximum-repair behavior.

    Every item has already passed the invariant, DOM, OCR and VQA gates.  Within
    that safe set, prefer the candidate with the greatest measured improvement;
    change cost is only a tie-break.  Keeping the complete order also lets the
    publisher try the next validated candidate when a candidate-specific final
    gate rejects the first choice.
    """

    return tuple(
        sorted(
            candidates,
            key=lambda item: (
                -item.resolved_issue_count,
                -item.resolved_issue_severity,
                -item.quality_gain,
                -item.quality_gain_interval[0],
                -item.quality_gain_interval[1],
                item.constraint_cost,
                item.realized_cost,
                item.plan.plan_id,
            ),
        )
    )


def _select_accepted_candidate(
    candidates: Sequence[_AcceptedCandidate],
) -> _AcceptedCandidate:
    return _rank_accepted_candidates(candidates)[0]
