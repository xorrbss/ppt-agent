"""D0-D8 Deck IR rollout decisions and D6 retirement guard."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import IntEnum
from typing import Literal

from .quality import _canonical_digest, _read


class RolloutPhase(IntEnum):
    D0_CONTRACT = 0
    D1_COMPILER_SHADOW = 1
    D2_DIRECTOR_SHADOW = 2
    D3_INTERNAL_CANARY = 3
    D4_DUAL_EVIDENCE = 4
    D5_DEFAULT_CANDIDATE = 5
    D6_LEGACY_RETIREMENT = 6
    D7_HYBRID_EXTENSION = 7
    D8_CONTINUOUS_GOVERNANCE = 8

    @property
    def label(self) -> str:
        return f"D{int(self)}"


def parse_rollout_phase(value: RolloutPhase | int | str) -> RolloutPhase:
    if isinstance(value, RolloutPhase):
        return value
    if isinstance(value, str):
        normalized = value.strip().upper()
        if normalized.startswith("D") and normalized[1:].isdigit():
            value = int(normalized[1:])
    try:
        return RolloutPhase(int(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("rollout phase must be D0 through D8") from exc


@dataclass(frozen=True, slots=True)
class OperationRolloutDecision:
    schema_version: str
    operation_id: str
    phase: RolloutPhase
    strategy: Literal["deck-ir", "legacy-authored", "reject"]
    slide_keys: tuple[str, ...]
    publication_allowed: bool
    shadow_only: bool
    retention_class: Literal["ephemeral", "checkpoint"]
    maximum_retention_hours: int | None
    release_evidence_eligible: bool
    reason: str
    receipt_digest: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "operationId": self.operation_id,
            "rolloutPhase": self.phase.label,
            "selectedStrategy": self.strategy,
            "slideKeys": list(self.slide_keys),
            "publicationAllowed": self.publication_allowed,
            "shadowOnly": self.shadow_only,
            "retentionClass": self.retention_class,
            "maximumRetentionHours": self.maximum_retention_hours,
            "releaseEvidenceEligible": self.release_evidence_eligible,
            "reason": self.reason,
            "receiptDigest": self.receipt_digest,
        }


def decide_operation_rollout(
    *,
    operation_id: str,
    slide_keys: Sequence[str],
    phase: RolloutPhase | int | str,
    required_capabilities_supported: bool,
    pre_d6_legacy_allowed: bool,
) -> OperationRolloutDecision:
    """Seal one strategy for every slide in the operation.

    Unsupported capability may route the whole operation to legacy only through
    D5. D6 and later fail closed. No per-slide strategy input is accepted.
    """

    parsed_phase = parse_rollout_phase(phase)
    if not isinstance(operation_id, str) or not operation_id:
        raise ValueError("operation_id must be non-empty")
    normalized_slides = tuple(slide_keys)
    if not normalized_slides or any(not isinstance(item, str) or not item for item in normalized_slides):
        raise ValueError("slide_keys must contain non-empty strings")
    if len(set(normalized_slides)) != len(normalized_slides):
        raise ValueError("slide_keys must be unique")

    if required_capabilities_supported:
        strategy: Literal["deck-ir", "legacy-authored", "reject"] = "deck-ir"
        reason = "all-required-capabilities-supported"
    elif parsed_phase < RolloutPhase.D6_LEGACY_RETIREMENT and pre_d6_legacy_allowed:
        strategy = "legacy-authored"
        reason = "whole-operation-pre-d6-legacy-routing"
    else:
        strategy = "reject"
        reason = "generation_strategy_capability_missing"

    shadow_only = strategy == "deck-ir" and parsed_phase <= RolloutPhase.D2_DIRECTOR_SHADOW
    publication_allowed = strategy != "reject" and not shadow_only
    retention_class: Literal["ephemeral", "checkpoint"] = (
        "ephemeral" if parsed_phase == RolloutPhase.D2_DIRECTOR_SHADOW else "checkpoint"
    )
    maximum_retention_hours = 24 if retention_class == "ephemeral" else None
    release_evidence_eligible = (
        strategy == "deck-ir"
        and publication_allowed
        and parsed_phase >= RolloutPhase.D4_DUAL_EVIDENCE
    )
    body = {
        "schemaVersion": "deck-ir-operation-rollout-decision/v1",
        "operationId": operation_id,
        "rolloutPhase": parsed_phase.label,
        "selectedStrategy": strategy,
        "slideKeys": list(normalized_slides),
        "publicationAllowed": publication_allowed,
        "shadowOnly": shadow_only,
        "retentionClass": retention_class,
        "maximumRetentionHours": maximum_retention_hours,
        "releaseEvidenceEligible": release_evidence_eligible,
        "reason": reason,
    }
    return OperationRolloutDecision(
        schema_version="deck-ir-operation-rollout-decision/v1",
        operation_id=operation_id,
        phase=parsed_phase,
        strategy=strategy,
        slide_keys=normalized_slides,
        publication_allowed=publication_allowed,
        shadow_only=shadow_only,
        retention_class=retention_class,
        maximum_retention_hours=maximum_retention_hours,
        release_evidence_eligible=release_evidence_eligible,
        reason=reason,
        receipt_digest=_canonical_digest(body),
    )


def validate_whole_operation_strategy(receipt: object) -> bool:
    """Reject any receipt attempting per-slide mixed routing."""

    selected = _read(receipt, "selectedStrategy", "selected_strategy", "strategy", default=None)
    if selected not in {"deck-ir", "legacy-authored", "reject"}:
        return False
    per_slide = _read(receipt, "slideStrategies", "slide_strategies", default=None)
    if per_slide is None:
        return True
    if not isinstance(per_slide, Mapping) or not per_slide:
        return False
    return all(strategy == selected for strategy in per_slide.values())


@dataclass(frozen=True, slots=True)
class ShadowArtifactDecision:
    valid: bool
    errors: tuple[str, ...]
    publication_allowed: bool = False
    release_evidence_eligible: bool = False


def validate_d2_shadow_artifact(artifact: object) -> ShadowArtifactDecision:
    errors: list[str] = []
    if _read(artifact, "retentionClass", "retention_class", default=None) != "ephemeral":
        errors.append("shadow_retention_class_invalid")
    retention_hours = _read(
        artifact,
        "retentionHours",
        "retention_hours",
        default=None,
    )
    if (
        not isinstance(retention_hours, (int, float))
        or isinstance(retention_hours, bool)
        or retention_hours <= 0
        or retention_hours > 24
    ):
        errors.append("shadow_retention_exceeds_24_hours")
    if not isinstance(_read(artifact, "ownerScopeDigest", "owner_scope_digest", default=None), str):
        errors.append("shadow_owner_scope_missing")
    if _read(artifact, "publicationAllowed", "publication_allowed", default=False) is not False:
        errors.append("shadow_publication_forbidden")
    if _read(
        artifact,
        "releaseEvidenceEligible",
        "release_evidence_eligible",
        default=False,
    ) is not False:
        errors.append("shadow_release_evidence_forbidden")
    return ShadowArtifactDecision(valid=not errors, errors=tuple(errors))


@dataclass(frozen=True, slots=True)
class D6RetirementGuardDecision:
    allowed: bool
    trailing_qualified_count: int
    evaluation_policy_digest: str | None
    qualified_rc_digests: tuple[str, ...]
    reasons: tuple[str, ...]
    receipt_digest: str


def _rc_passed(record: object) -> bool:
    failed_strata = _read(record, "failedStrata", "failed_strata", default=())
    return (
        _read(record, "frozen", default=None) is True
        and _read(record, "absoluteQualityStatus", "absolute_quality_status", default=None)
        == "passed"
        and _read(record, "statisticalValidity", "statistical_validity", default=None)
        == "valid"
        and _read(record, "nonRegressionStatus", "non_regression_status", default=None)
        == "passed"
        and _read(record, "status", default=None) == "passed"
        and isinstance(failed_strata, Sequence)
        and not isinstance(failed_strata, (str, bytes))
        and len(failed_strata) == 0
    )


def evaluate_d6_retirement_guard(rc_history: Sequence[object]) -> D6RetirementGuardDecision:
    """Require two trailing, distinct frozen RC passes under one frozen policy."""

    trailing: list[object] = []
    for record in reversed(tuple(rc_history)):
        if not _rc_passed(record):
            break
        trailing.append(record)
    trailing.reverse()
    reasons: list[str] = []
    selected = trailing[-2:] if len(trailing) >= 2 else trailing
    if len(selected) < 2:
        reasons.append("d6_requires_two_consecutive_frozen_rcs")
    rc_digests = tuple(
        item
        for item in (
            _read(record, "rcDigest", "rc_digest", default=None) for record in selected
        )
        if isinstance(item, str) and item
    )
    if len(rc_digests) != len(selected) or len(set(rc_digests)) != len(rc_digests):
        reasons.append("d6_rc_identity_invalid")
    policy_digests = {
        _read(record, "evaluationPolicyDigest", "evaluation_policy_digest", default=None)
        for record in selected
    }
    policy_digest = next(iter(policy_digests)) if len(policy_digests) == 1 else None
    if len(selected) == 2 and (len(policy_digests) != 1 or not isinstance(policy_digest, str)):
        reasons.append("d6_evaluation_policy_mismatch")
    ordinals = [
        _read(record, "sequence", "rcSequence", "rc_sequence", default=None)
        for record in selected
    ]
    if len(ordinals) == 2 and all(isinstance(item, int) for item in ordinals):
        if ordinals[1] != ordinals[0] + 1:
            reasons.append("d6_rc_sequence_not_consecutive")

    allowed = len(selected) == 2 and not reasons
    body = {
        "schemaVersion": "d6-retirement-guard-decision/v1",
        "allowed": allowed,
        "trailingQualifiedCount": len(trailing),
        "evaluationPolicyDigest": policy_digest,
        "qualifiedRcDigests": list(rc_digests),
        "reasons": reasons,
    }
    return D6RetirementGuardDecision(
        allowed=allowed,
        trailing_qualified_count=len(trailing),
        evaluation_policy_digest=policy_digest if isinstance(policy_digest, str) else None,
        qualified_rc_digests=rc_digests,
        reasons=tuple(reasons),
        receipt_digest=_canonical_digest(body),
    )


__all__ = [
    "D6RetirementGuardDecision",
    "OperationRolloutDecision",
    "RolloutPhase",
    "ShadowArtifactDecision",
    "decide_operation_rollout",
    "evaluate_d6_retirement_guard",
    "parse_rollout_phase",
    "validate_d2_shadow_artifact",
    "validate_whole_operation_strategy",
]
