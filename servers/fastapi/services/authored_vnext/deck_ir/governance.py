"""Post-D6 immutable change-set, shadow, canary, and drift governance."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from .evaluation import (
    ServicePromotionEvidenceBundle,
    derive_service_promotion_evidence_bundle,
)
from .quality import _canonical_digest, _read


QUALITY_FINGERPRINT_FIELDS = MappingProxyType(
    {
        "grammar": ("grammarDigest", "grammar_digest"),
        "compiler": ("compilerDigest", "compiler_digest"),
        "modelFingerprint": ("modelFingerprint", "model_fingerprint"),
        "font": ("fontManifestDigest", "font_manifest_digest"),
        "asset": ("assetManifestDigest", "asset_manifest_digest"),
        "candidatePolicy": ("candidatePolicyDigest", "candidate_policy_digest"),
        "evaluationPolicy": ("evaluationPolicyDigest", "evaluation_policy_digest"),
    }
)

CANARY_MINIMA = MappingProxyType(
    {
        1: (100, 24.0),
        5: (500, 24.0),
        25: (1_000, 48.0),
    }
)


def _fingerprint(value: object) -> dict[str, str | None]:
    return {
        key: (
            item
            if isinstance((item := _read(value, *names, default=None)), str) and item
            else None
        )
        for key, names in QUALITY_FINGERPRINT_FIELDS.items()
    }


@dataclass(frozen=True, slots=True)
class QualityChangeSetDecision:
    post_d6: bool
    changed_fields: tuple[str, ...]
    requires_new_rc: bool
    valid: bool
    reason: str
    candidate_rc_digest: str | None
    change_set_digest: str


def detect_post_d6_change_set(
    previous_qualified: object,
    candidate: object,
    *,
    post_d6: bool,
) -> QualityChangeSetDecision:
    """Treat every quality-impacting digest change as a new immutable RC."""

    previous = _fingerprint(previous_qualified)
    current = _fingerprint(candidate)
    changed = tuple(
        key for key in QUALITY_FINGERPRINT_FIELDS if previous[key] != current[key]
    )
    candidate_rc = _read(candidate, "rcDigest", "rc_digest", default=None)
    previous_rc = _read(
        previous_qualified,
        "rcDigest",
        "rc_digest",
        default=None,
    )
    required_values_present = all(current.values())
    requires_new_rc = post_d6 and bool(changed)
    if not post_d6:
        valid = required_values_present
        reason = "pre-d6-change-set-observed"
    elif not changed:
        valid = candidate_rc == previous_rc and required_values_present
        reason = "no-quality-impacting-digest-change"
    elif not isinstance(candidate_rc, str) or not candidate_rc or candidate_rc == previous_rc:
        valid = False
        reason = "post-d6-change-reused-mutable-rc-identity"
    elif not required_values_present:
        valid = False
        reason = "post-d6-quality-fingerprint-incomplete"
    else:
        valid = True
        reason = "post-d6-new-immutable-rc-required"
    body = {
        "schemaVersion": "quality-change-set-decision/v1",
        "postD6": post_d6,
        "previousRcDigest": previous_rc,
        "candidateRcDigest": candidate_rc,
        "changedFields": list(changed),
        "currentFingerprint": current,
        "requiresNewRc": requires_new_rc,
        "valid": valid,
        "reason": reason,
    }
    return QualityChangeSetDecision(
        post_d6=post_d6,
        changed_fields=changed,
        requires_new_rc=requires_new_rc,
        valid=valid,
        reason=reason,
        candidate_rc_digest=candidate_rc if isinstance(candidate_rc, str) else None,
        change_set_digest=_canonical_digest(body),
    )


@dataclass(frozen=True, slots=True)
class SealedShadowDecision:
    passed: bool
    reasons: tuple[str, ...]
    report_digest: str | None


def validate_sealed_shadow_report(report: object) -> SealedShadowDecision:
    reasons: list[str] = []
    if _read(report, "holdoutSealed", "holdout_sealed", default=None) is not True:
        reasons.append("shadow_holdout_not_sealed")
    if _read(report, "absoluteQualityStatus", "absolute_quality_status", default=None) != "passed":
        reasons.append("shadow_absolute_quality_failed")
    if _read(report, "statisticalValidity", "statistical_validity", default=None) != "valid":
        reasons.append("shadow_statistical_evaluation_invalid")
    if _read(report, "nonRegressionStatus", "non_regression_status", default=None) != "passed":
        reasons.append("shadow_non_regression_failed")
    failed_strata = _read(report, "failedStrata", "failed_strata", default=())
    if (
        isinstance(failed_strata, (str, bytes))
        or not isinstance(failed_strata, Sequence)
        or len(failed_strata) != 0
    ):
        reasons.append("shadow_required_stratum_failed")
    report_digest = _read(report, "reportDigest", "report_digest", default=None)
    if not isinstance(report_digest, str) or not report_digest:
        reasons.append("shadow_report_digest_missing")
        report_digest = None
    return SealedShadowDecision(
        passed=not reasons,
        reasons=tuple(reasons),
        report_digest=report_digest,
    )


@dataclass(frozen=True, slots=True)
class CanaryStageDecision:
    cohort_percent: int
    passed: bool
    minimum_operations: int
    minimum_hours: float
    observed_operations: int
    observed_hours: float
    next_percent: int | None
    reasons: tuple[str, ...]


def evaluate_canary_stage(window: object) -> CanaryStageDecision:
    percent = _read(window, "cohortPercent", "cohort_percent", default=None)
    if percent not in CANARY_MINIMA:
        raise ValueError("canary cohort percent must be 1, 5, or 25")
    minimum_operations, minimum_hours = CANARY_MINIMA[percent]
    operations = _read(
        window,
        "operationCount",
        "operation_count",
        "minimumOperations",
        "minimum_operations",
        default=0,
    )
    hours = _read(
        window,
        "durationHours",
        "duration_hours",
        "minimumHours",
        "minimum_hours",
        default=0.0,
    )
    observed_operations = operations if isinstance(operations, int) and not isinstance(operations, bool) else 0
    observed_hours = float(hours) if isinstance(hours, (int, float)) and not isinstance(hours, bool) else 0.0
    reasons: list[str] = []
    if observed_operations < minimum_operations:
        reasons.append("canary_operation_minimum_not_met")
    if observed_hours < minimum_hours:
        reasons.append("canary_duration_minimum_not_met")
    if _read(window, "status", default=None) != "passed":
        reasons.append("canary_window_not_passed")
    next_percent = {1: 5, 5: 25, 25: 100}[percent] if not reasons else None
    return CanaryStageDecision(
        cohort_percent=percent,
        passed=not reasons,
        minimum_operations=minimum_operations,
        minimum_hours=minimum_hours,
        observed_operations=observed_operations,
        observed_hours=observed_hours,
        next_percent=next_percent,
        reasons=tuple(reasons),
    )


@dataclass(frozen=True, slots=True)
class CanaryProgressionDecision:
    allowed: bool
    next_percent: int | None
    reasons: tuple[str, ...]


def decide_canary_progression(
    *,
    shadow_report: object,
    completed_windows: Sequence[object],
) -> CanaryProgressionDecision:
    """Allow only shadow -> 1 -> 5 -> 25 -> 100 progression."""

    shadow = validate_sealed_shadow_report(shadow_report)
    if not shadow.passed:
        return CanaryProgressionDecision(False, None, shadow.reasons)
    if not completed_windows:
        return CanaryProgressionDecision(True, 1, ())
    expected = (1, 5, 25, 100)
    reasons: list[str] = []
    decisions: list[CanaryStageDecision] = []
    for index, window in enumerate(completed_windows):
        if index >= len(expected):
            reasons.append("canary_stage_sequence_too_long")
            break
        percent = _read(window, "cohortPercent", "cohort_percent", default=None)
        if percent != expected[index]:
            reasons.append("canary_stage_sequence_invalid")
            break
        if percent == 100:
            if _read(window, "status", default=None) != "passed":
                reasons.append("canary_window_not_passed")
            continue
        decision = evaluate_canary_stage(window)
        decisions.append(decision)
        reasons.extend(decision.reasons)
        if not decision.passed:
            break
    if reasons:
        return CanaryProgressionDecision(False, None, tuple(dict.fromkeys(reasons)))
    return CanaryProgressionDecision(
        True,
        (None if len(completed_windows) == 4 else decisions[-1].next_percent)
        if decisions
        else 1,
        (),
    )


@dataclass(frozen=True, slots=True)
class DriftRuleDecision:
    action: Literal["continue", "pause-and-rollback", "pause-admission"]
    triggered_rules: tuple[str, ...]
    rollback_target_rc_digest: str | None


def _window_rules(window: object) -> set[str]:
    rules: set[str] = set()
    failure_delta = _read(window, "failureRateDelta", "failure_rate_delta", default=None)
    if isinstance(failure_delta, (int, float)) and not isinstance(failure_delta, bool) and failure_delta > 0.02:
        rules.add("failure-rate-drift")
    p95 = _read(window, "p95LatencyMs", "p95_latency_ms", default=None)
    if isinstance(p95, (int, float)) and not isinstance(p95, bool) and p95 > 180_000:
        rules.add("p95-latency-drift")
    entropy = _read(window, "compositionEntropyDelta", "composition_entropy_delta", default=None)
    if isinstance(entropy, (int, float)) and not isinstance(entropy, bool) and entropy < -0.05:
        rules.add("composition-entropy-drift")
    repeat = _read(window, "consecutiveRepeatRateDelta", "consecutive_repeat_rate_delta", default=None)
    if isinstance(repeat, (int, float)) and not isinstance(repeat, bool) and repeat > 0.02:
        rules.add("consecutive-repeat-rate-drift")
    return rules


def evaluate_drift_rules(
    *,
    immediate_observation: object,
    rolling_windows: Sequence[object],
    previous_qualified_rc_digest: str | None,
) -> DriftRuleDecision:
    """Apply immediate corruption/floor stops and repeated two-window drift."""

    triggered: list[str] = []
    if (_read(immediate_observation, "p0Count", "p0_count", default=0) or 0) > 0:
        triggered.append("confirmed-p0")
    if (_read(immediate_observation, "p1Count", "p1_count", default=0) or 0) > 0:
        triggered.append("confirmed-p1")
    if _read(immediate_observation, "sourceCorruption", "source_corruption", default=False) is True:
        triggered.append("source-corruption")
    if _read(
        immediate_observation,
        "crossRendererCorruption",
        "cross_renderer_corruption",
        default=False,
    ) is True:
        triggered.append("cross-renderer-corruption")
    if _read(
        immediate_observation,
        "absoluteQualityStatus",
        "absolute_quality_status",
        default="passed",
    ) != "passed":
        triggered.append("absolute-quality-floor-failed")
    if _read(
        immediate_observation,
        "nonRegressionStatus",
        "non_regression_status",
        default="passed",
    ) != "passed":
        triggered.append("non-regression-floor-failed")

    if not triggered and len(rolling_windows) >= 2:
        previous_rules = _window_rules(rolling_windows[-2])
        current_rules = _window_rules(rolling_windows[-1])
        triggered.extend(sorted(previous_rules.intersection(current_rules)))
    if not triggered:
        return DriftRuleDecision("continue", (), previous_qualified_rc_digest)
    if isinstance(previous_qualified_rc_digest, str) and previous_qualified_rc_digest:
        return DriftRuleDecision(
            "pause-and-rollback",
            tuple(dict.fromkeys(triggered)),
            previous_qualified_rc_digest,
        )
    return DriftRuleDecision(
        "pause-admission",
        tuple(dict.fromkeys(triggered)),
        None,
    )


@dataclass(frozen=True, slots=True)
class QualityDriftDecisionReceipt:
    schema_version: str
    candidate_rc_digest: str
    previous_qualified_rc_digest: str
    changed_digests: Mapping[str, str]
    shadow_visual_report_digest: str
    canary_windows: tuple[Mapping[str, object], ...]
    observed_drift: Mapping[str, object]
    triggered_rules: tuple[str, ...]
    action: str
    rollback_target_rc_digest: str
    receipt_digest: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "candidateRcDigest": self.candidate_rc_digest,
            "previousQualifiedRcDigest": self.previous_qualified_rc_digest,
            "changedDigests": dict(self.changed_digests),
            "shadowVisualReportDigest": self.shadow_visual_report_digest,
            "canaryWindows": [dict(item) for item in self.canary_windows],
            "observedDrift": dict(self.observed_drift),
            "triggeredRules": list(self.triggered_rules),
            "action": self.action,
            "rollbackTargetRcDigest": self.rollback_target_rc_digest,
            "receiptDigest": self.receipt_digest,
        }


def derive_quality_drift_receipt(
    *,
    previous_qualified: object,
    candidate: object,
    shadow_report: object,
    canary_windows: Sequence[object],
    immediate_observation: object,
    rolling_windows: Sequence[object],
) -> QualityDriftDecisionReceipt:
    change_set = detect_post_d6_change_set(
        previous_qualified,
        candidate,
        post_d6=True,
    )
    if not change_set.valid or not change_set.requires_new_rc:
        raise ValueError("post-D6 drift receipt requires a valid immutable changed RC")
    shadow = validate_sealed_shadow_report(shadow_report)
    previous_rc = _read(previous_qualified, "rcDigest", "rc_digest")
    candidate_rc = _read(candidate, "rcDigest", "rc_digest")
    if not shadow.passed or shadow.report_digest is None:
        immediate = {
            "absoluteQualityStatus": "failed",
            "nonRegressionStatus": "failed",
        }
    else:
        immediate = immediate_observation
    drift = evaluate_drift_rules(
        immediate_observation=immediate,
        rolling_windows=rolling_windows,
        previous_qualified_rc_digest=previous_rc,
    )
    progression = decide_canary_progression(
        shadow_report=shadow_report,
        completed_windows=canary_windows,
    )
    if drift.action != "continue":
        action = drift.action
    elif progression.allowed:
        action = (
            "qualified"
            if progression.next_percent is None and len(canary_windows) == 4
            else "advance-canary"
        )
    else:
        action = "hold-canary"

    previous_fp = _fingerprint(previous_qualified)
    current_fp = _fingerprint(candidate)
    changed_digests = {
        key: value
        for key, value in current_fp.items()
        if isinstance(value, str)
    }
    normalized_canary_windows = tuple(
        MappingProxyType(
            {
                "cohortPercent": _read(window, "cohortPercent", "cohort_percent", default=None),
                "minimumOperations": _read(
                    window,
                    "operationCount",
                    "operation_count",
                    "minimumOperations",
                    "minimum_operations",
                    default=None,
                ),
                "minimumHours": _read(
                    window,
                    "durationHours",
                    "duration_hours",
                    "minimumHours",
                    "minimum_hours",
                    default=None,
                ),
                "status": _read(window, "status", default=None),
            }
        )
        for window in canary_windows
    )
    latest_window = rolling_windows[-1] if rolling_windows else {}
    observed_drift = {
        "p0": _read(immediate_observation, "p0Count", "p0_count", default=0),
        "p1": _read(immediate_observation, "p1Count", "p1_count", default=0),
        "repeatRateDelta": _read(
            latest_window,
            "consecutiveRepeatRateDelta",
            "consecutive_repeat_rate_delta",
            default=0.0,
        ),
        "compositionEntropyDelta": _read(
            latest_window,
            "compositionEntropyDelta",
            "composition_entropy_delta",
            default=0.0,
        ),
        "p95LatencyDeltaMs": _read(
            latest_window,
            "p95LatencyDeltaMs",
            "p95_latency_delta_ms",
            default=0,
        ),
        "generationFailureRateDeltaPoints": _read(
            latest_window,
            "failureRateDelta",
            "failure_rate_delta",
            default=0.0,
        ),
    }
    body = {
        "schemaVersion": "quality-drift-receipt/v1",
        "candidateRcDigest": candidate_rc,
        "previousQualifiedRcDigest": previous_rc,
        "changedDigests": changed_digests,
        "shadowVisualReportDigest": shadow.report_digest,
        "canaryWindows": [dict(item) for item in normalized_canary_windows],
        "observedDrift": observed_drift,
        "triggeredRules": list(drift.triggered_rules),
        "action": action,
        "rollbackTargetRcDigest": previous_rc,
    }
    return QualityDriftDecisionReceipt(
        schema_version="quality-drift-receipt/v1",
        candidate_rc_digest=candidate_rc,
        previous_qualified_rc_digest=previous_rc,
        changed_digests=MappingProxyType(changed_digests),
        shadow_visual_report_digest=shadow.report_digest or "",
        canary_windows=normalized_canary_windows,
        observed_drift=MappingProxyType(observed_drift),
        triggered_rules=drift.triggered_rules,
        action=action,
        rollback_target_rc_digest=previous_rc,
        receipt_digest=_canonical_digest(body),
    )


__all__ = [
    "CANARY_MINIMA",
    "QUALITY_FINGERPRINT_FIELDS",
    "CanaryProgressionDecision",
    "CanaryStageDecision",
    "DriftRuleDecision",
    "QualityChangeSetDecision",
    "QualityDriftDecisionReceipt",
    "SealedShadowDecision",
    "ServicePromotionEvidenceBundle",
    "decide_canary_progression",
    "derive_quality_drift_receipt",
    "derive_service_promotion_evidence_bundle",
    "detect_post_d6_change_set",
    "evaluate_canary_stage",
    "evaluate_drift_rules",
    "validate_sealed_shadow_report",
]
