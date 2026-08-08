"""Pure, fail-closed acceptance policy for SlideSpec repair candidates.

This module intentionally performs no rendering, model calls, persistence, or
publishing.  Callers collect typed evidence first and persist the returned decision
without re-deciding it at the publication boundary.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from models.slidespec_v1 import (
    ACCEPTANCE_SCHEMA,
    AcceptanceDecision,
    StageDecision,
)


POLICY_VERSION = "slidespec-acceptance/1.1"
TriggerType = Literal["geometry", "content_visibility", "visual_quality"]
GateStatus = Literal["passed", "failed", "unavailable", "timeout", "incomplete"]
Outcome = Literal["accepted", "rejected", "inconclusive"]
TargetIssueStage = Literal["invariant", "dom"]

_INCONCLUSIVE_STATUSES = frozenset({"unavailable", "timeout", "incomplete"})
_REQUIRED_GATES = ("schema", "invariant", "render", "dom", "ocr", "vqa")


@dataclass(frozen=True)
class GateEvidence:
    """Evidence summary for one gate.

    Sensor-specific fields are optional at construction time, but a trigger policy
    treats a missing required value as inconclusive rather than guessing.
    """

    status: GateStatus
    reason_codes: tuple[str, ...] = ()
    evidence_ref: str | None = None
    target_resolved: bool | None = None
    improved: bool | None = None
    non_regression: bool | None = None
    important_token_recall: float | None = None
    impacted_recall: float | None = None
    coverage: float | None = None
    new_high_medium_defects: int | None = None


@dataclass(frozen=True, slots=True)
class TargetIssueIdentity:
    """Stable identity shared by detector baseline and candidate evidence."""

    stage: TargetIssueStage
    code: str
    semantic_id: str
    qid: str
    constraint_id: str | None = None

    def __post_init__(self) -> None:
        if not self.code or not self.semantic_id or not self.qid:
            raise ValueError("target issue identity requires code, semantic_id, and qid")
        if self.stage == "invariant" and not self.constraint_id:
            raise ValueError("invariant target issue identity requires constraint_id")


@dataclass(frozen=True, slots=True)
class TargetIssueDelta:
    """Before/after severity for exactly one detector target issue.

    Geometry and constraint metrics use ``decrease`` (overlap area, overflow,
    constraint residual).  ``increase`` supports recall/visibility metrics while
    retaining the same exact identity gate.
    """

    identity: TargetIssueIdentity
    before: float | None
    after: float | None
    resolution_threshold: float = 0.0
    tolerance: float = 1e-6
    direction: Literal["decrease", "increase"] = "decrease"

    def __post_init__(self) -> None:
        if self.direction not in {"decrease", "increase"}:
            raise ValueError("target issue delta direction is unsupported")
        if not math.isfinite(self.resolution_threshold):
            raise ValueError("target issue resolution threshold must be finite")
        if not math.isfinite(self.tolerance) or self.tolerance < 0:
            raise ValueError("target issue tolerance must be finite and non-negative")


@dataclass(frozen=True)
class AcceptanceEvidence:
    schema: GateEvidence
    invariant: GateEvidence
    render: GateEvidence
    dom: GateEvidence
    ocr: GateEvidence
    vqa: GateEvidence
    target_issue_deltas: tuple[TargetIssueDelta, ...] | None = None


@dataclass(frozen=True)
class _PolicyResult:
    outcome: Outcome
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    decisive_stage: str | None = None


def _unique(*codes: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(code for code in codes if code))


def _stage_decisions(
    evidence: AcceptanceEvidence, result: _PolicyResult
) -> tuple[StageDecision, ...]:
    stages = []
    decisive_seen = False
    for name, gate in (
        ("schema", evidence.schema),
        ("invariant", evidence.invariant),
        ("render", evidence.render),
        ("dom", evidence.dom),
        ("ocr", evidence.ocr),
        ("vqa", evidence.vqa),
    ):
        if decisive_seen:
            stages.append(StageDecision(stage=name, status="not_started"))
            continue
        status = {
            "passed": "pass",
            "failed": "fail",
            "unavailable": "inconclusive",
            "timeout": "inconclusive",
            "incomplete": "inconclusive",
        }[gate.status]
        reasons = gate.reason_codes
        if name == result.decisive_stage:
            status = "fail" if result.outcome == "rejected" else "inconclusive"
            reasons = result.reason_codes[:-1]
            decisive_seen = True
        refs = (gate.evidence_ref,) if gate.evidence_ref else ()
        stages.append(
            StageDecision(
                stage=name,
                status=status,
                reasonCodes=reasons,
                evidenceRefs=refs,
            )
        )
    return tuple(stages)


def _inconclusive(stage: str, *codes: str) -> _PolicyResult:
    return _PolicyResult(
        "inconclusive", _unique(*codes, "acceptance.inconclusive"), stage
    )


def _rejected(stage: str, *codes: str) -> _PolicyResult:
    return _PolicyResult(
        "rejected", _unique(*codes, "acceptance.required_gate_failed"), stage
    )


def _required_value(
    value: object, stage: str, missing_code: str
) -> _PolicyResult | None:
    return None if value is not None else _inconclusive(stage, missing_code)


def _base_gate_result(name: str, gate: GateEvidence) -> _PolicyResult | None:
    if gate.status in _INCONCLUSIVE_STATUSES:
        default = {
            "render": "render.timeout" if gate.status == "timeout" else "render.failed",
            "dom": "dom.telemetry_incomplete",
            "ocr": "ocr.timeout" if gate.status == "timeout" else "ocr.unavailable",
            "vqa": "vqa.timeout" if gate.status == "timeout" else "vqa.unavailable",
        }.get(name, "schema.invalid_value")
        return _inconclusive(name, *gate.reason_codes, default)
    if gate.status != "passed":
        default = {
            "schema": "schema.invalid_value",
            "invariant": "invariant.content_digest_changed",
            "render": "render.failed",
            "dom": "dom.telemetry_incomplete",
            "ocr": "ocr.recall_regressed",
            "vqa": "vqa.visual_regression",
        }[name]
        return _rejected(name, *gate.reason_codes, default)
    return None


def _target_issue_result(
    stage: TargetIssueStage,
    evidence: AcceptanceEvidence,
    required: tuple[TargetIssueIdentity, ...],
) -> _PolicyResult | None:
    """Require an exact target identity to resolve, not an aggregate score.

    ``required`` is normally derived from the selected plan's diagnostic refs.
    When a caller supplies deltas without a separate requirement list, every
    supplied delta becomes required.  Legacy callers that supply neither retain
    the 1.1 aggregate gate until their detector bridge is migrated.
    """

    deltas = evidence.target_issue_deltas
    stage_required = tuple(identity for identity in required if identity.stage == stage)
    if not stage_required and deltas is not None:
        stage_required = tuple(
            delta.identity for delta in deltas if delta.identity.stage == stage
        )
    if not stage_required:
        return None
    if deltas is None:
        return _inconclusive(stage, "acceptance.target_issue_delta_unavailable")

    by_identity: dict[TargetIssueIdentity, TargetIssueDelta] = {}
    for delta in deltas:
        if delta.identity.stage != stage:
            continue
        if delta.identity in by_identity:
            return _inconclusive(stage, "acceptance.target_issue_delta_ambiguous")
        by_identity[delta.identity] = delta

    for identity in stage_required:
        delta = by_identity.get(identity)
        if delta is None:
            return _inconclusive(stage, "acceptance.target_issue_identity_mismatch")
        if (
            delta.before is None
            or delta.after is None
            or not math.isfinite(delta.before)
            or not math.isfinite(delta.after)
            or not math.isfinite(delta.resolution_threshold)
            or not math.isfinite(delta.tolerance)
            or delta.tolerance < 0
        ):
            return _inconclusive(stage, "acceptance.target_issue_delta_unavailable")

        threshold = delta.resolution_threshold
        tolerance = delta.tolerance
        if delta.direction == "decrease":
            if delta.before <= threshold + tolerance:
                return _inconclusive(
                    stage, "acceptance.target_issue_baseline_unproven"
                )
            if delta.after >= delta.before - tolerance:
                return _rejected(stage, "acceptance.target_issue_not_improved")
            if delta.after > threshold + tolerance:
                return _rejected(stage, "acceptance.target_issue_not_resolved")
        else:
            if delta.before >= threshold - tolerance:
                return _inconclusive(
                    stage, "acceptance.target_issue_baseline_unproven"
                )
            if delta.after <= delta.before + tolerance:
                return _rejected(stage, "acceptance.target_issue_not_improved")
            if delta.after < threshold - tolerance:
                return _rejected(stage, "acceptance.target_issue_not_resolved")
    return None


def _evaluate_policy(
    trigger_type: TriggerType,
    evidence: AcceptanceEvidence,
    required_target_issues: tuple[TargetIssueIdentity, ...],
) -> _PolicyResult:
    # Cheap hard gates are evaluated in execution order.  A caller may omit later
    # expensive evidence after an earlier rejection without changing that rejection.
    for name, gate in (
        ("schema", evidence.schema),
        ("invariant", evidence.invariant),
    ):
        if result := _base_gate_result(name, gate):
            return result
    if trigger_type == "geometry" and (
        result := _target_issue_result(
            "invariant", evidence, required_target_issues
        )
    ):
        return result

    for name, gate in (("render", evidence.render), ("dom", evidence.dom)):
        if result := _base_gate_result(name, gate):
            return result

    dom = evidence.dom
    if dom.coverage is None or dom.coverage < 1.0:
        return _inconclusive("dom", *dom.reason_codes, "dom.telemetry_incomplete")
    if dom.new_high_medium_defects is None:
        return _inconclusive("dom", "dom.telemetry_incomplete")
    if dom.new_high_medium_defects > 0 or dom.non_regression is False:
        return _rejected("dom", *dom.reason_codes, "dom.unintended_overlap")
    if dom.non_regression is None:
        return _inconclusive("dom", "dom.telemetry_incomplete")
    if trigger_type == "geometry" and (
        result := _target_issue_result("dom", evidence, required_target_issues)
    ):
        return result

    if result := _base_gate_result("ocr", evidence.ocr):
        return result
    ocr = evidence.ocr
    if ocr.important_token_recall is None:
        return _inconclusive("ocr", "ocr.unavailable")
    if ocr.important_token_recall < 1.0:
        return _rejected("ocr", *ocr.reason_codes, "ocr.important_token_missing")
    if ocr.non_regression is None:
        return _inconclusive("ocr", "ocr.unavailable")
    if not ocr.non_regression:
        return _rejected("ocr", *ocr.reason_codes, "ocr.recall_regressed")

    if result := _base_gate_result("vqa", evidence.vqa):
        return result
    vqa = evidence.vqa
    if vqa.non_regression is None:
        return _inconclusive("vqa", "vqa.unavailable")
    if not vqa.non_regression:
        return _rejected("vqa", *vqa.reason_codes, "vqa.visual_regression")
    if missing := _required_value(vqa.improved, "vqa", "vqa.improvement_unavailable"):
        return missing
    if not vqa.improved:
        return _rejected("vqa", *vqa.reason_codes, "vqa.not_improved")

    if trigger_type == "geometry":
        if missing := _required_value(
            dom.target_resolved, "dom", "dom.telemetry_incomplete"
        ):
            return missing
        if not dom.target_resolved:
            return _rejected(
                "dom", *(dom.reason_codes or ("dom.target_not_resolved",))
            )
        if missing := _required_value(
            dom.improved, "dom", "dom.improvement_unavailable"
        ):
            return missing
        if not dom.improved:
            return _rejected("dom", *dom.reason_codes, "dom.not_improved")
    elif trigger_type == "content_visibility":
        if missing := _required_value(ocr.impacted_recall, "ocr", "ocr.unavailable"):
            return missing
        if missing := _required_value(ocr.improved, "ocr", "ocr.unavailable"):
            return missing
        if ocr.impacted_recall < 0.98 or not ocr.improved:
            return _rejected("ocr", *ocr.reason_codes, "ocr.not_improved")
    elif trigger_type == "visual_quality":
        pass
    else:
        raise ValueError(f"unsupported trigger type: {trigger_type!r}")

    return _PolicyResult("accepted", ("acceptance.all_required_gates_passed",))


def decide_acceptance(
    trigger_type: TriggerType,
    evidence: AcceptanceEvidence,
    *,
    base_spec_hash: str,
    candidate_spec_hash: str | None = None,
    decision_id: str | None = None,
    plan_id: str | None = None,
    base_spec_revision: int | None = None,
    required_target_issues: tuple[TargetIssueIdentity, ...] = (),
) -> AcceptanceDecision:
    """Return a deterministic, DB-write-free decision for one candidate."""

    result = _evaluate_policy(trigger_type, evidence, required_target_issues)
    return AcceptanceDecision(
        **{"$schema": ACCEPTANCE_SCHEMA},
        decisionId=decision_id,
        planId=plan_id,
        baseSpecRevision=base_spec_revision,
        baseSpecHash=base_spec_hash,
        candidateSpecHash=candidate_spec_hash,
        trigger_type=trigger_type,
        outcome=result.outcome,
        reasonCodes=result.reason_codes,
        stages=_stage_decisions(evidence, result),
    )


__all__ = [
    "POLICY_VERSION",
    "AcceptanceDecision",
    "AcceptanceEvidence",
    "GateEvidence",
    "StageDecision",
    "TargetIssueDelta",
    "TargetIssueIdentity",
    "decide_acceptance",
]
