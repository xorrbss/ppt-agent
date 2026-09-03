from __future__ import annotations

from services.authored_vnext.deck_ir.canonical import canonical_digest
from services.authored_vnext.deck_ir.contracts import QualityDriftReceiptV1
from services.authored_vnext.deck_ir.evaluation import (
    derive_service_promotion_evidence_bundle,
    evaluate_visual_non_regression,
)
from services.authored_vnext.deck_ir.governance import (
    decide_canary_progression,
    derive_quality_drift_receipt,
    detect_post_d6_change_set,
    evaluate_drift_rules,
)
from services.authored_vnext.deck_ir.migration import (
    RolloutPhase,
    decide_operation_rollout,
    evaluate_d6_retirement_guard,
    validate_d2_shadow_artifact,
    validate_whole_operation_strategy,
)
from services.authored_vnext.deck_ir.quality import (
    BROWSER_REQUIRED_CHECKS,
    DECK_REQUIRED_CHECKS,
    STATIC_REQUIRED_CHECKS,
    SharedRecoveryBudget,
    consume_shared_recovery_budget,
    evaluate_deck_quality,
    validate_bounded_critic_patch,
)
from services.authored_vnext.deck_ir.rollback import (
    AtomicAdmissionResult,
    apply_atomic_admission_decision,
    derive_kill_switch_decision,
)


def _digest(seed: str) -> str:
    return "sha256:" + seed * 64


def _seal(payload: dict[str, object], field: str) -> dict[str, object]:
    return {**payload, field: canonical_digest(payload)}


def _policy() -> dict[str, object]:
    return _seal(
        {
            "schemaVersion": "visual-quality-evaluation-policy/v1",
            "corpusDigest": _digest("1"),
            "sealedHoldoutDigest": _digest("2"),
            "studyDesign": "paired-stratified-blind",
            "powerAnalysis": {
                "oneSidedAlpha": 0.025,
                "targetPower": 0.8,
                "nonInferiorityMargin": 0.25,
                "minimumOverallPairs": 120,
                "minimumPairsPerRequiredMarginalStratum": 30,
            },
            "reviewProtocol": {
                "reviewersPerPair": 3,
                "ratingScale": "1..5",
                "calibrationRequired": True,
                "pathRcAndFilenameBlinded": True,
                "tieHandling": "explicit-tie",
            },
            "absoluteFloors": {
                "overallExpertMeanLowerBound": 4.0,
                "requiredDimensionMeanLowerBound": 3.75,
                "worstDecileMeanLowerBound": 3.5,
                "p0Maximum": 0,
                "p1Maximum": 0,
            },
            "pairwisePreference": {
                "candidatePreferredOrTiedPointMinimum": 0.9,
                "oneSided95LowerBoundMinimum": 0.85,
            },
            "reliability": {
                "metric": "krippendorff-alpha-ordinal",
                "minimum": 0.67,
                "belowMinimumAction": "evaluation-invalid-recalibrate",
            },
            "confidenceMethod": "stratified-bootstrap-one-sided-95-v1",
            "multiplicityCorrection": "holm-v1",
        },
        "policyDigest",
    )


def _report(policy_digest: str, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "schemaVersion": "visual-non-regression-report/v1",
        "baselineRcDigest": _digest("3"),
        "candidateRcDigest": _digest("4"),
        "corpusDigest": _digest("1"),
        "evaluationPolicyDigest": policy_digest,
        "strata": ["language:ko-KR", "density:high"],
        "blindAssignmentDigest": _digest("5"),
        "reviewerReceiptDigests": [_digest("6"), _digest("7"), _digest("8")],
        "evaluationValidity": "valid",
        "overallPairCount": 120,
        "minimumMarginalStratumPairCount": 30,
        "reviewerAgreement": 0.74,
        "requiredCoverageRatio": 1.0,
        "p0Count": 0,
        "p1Count": 0,
        "absoluteOverallMeanLowerBound": 4.08,
        "absoluteRequiredDimensionMinimumLowerBound": 3.82,
        "absoluteWorstDecileMeanLowerBound": 3.57,
        "candidatePreferredOrTiedRatio": 0.92,
        "nonInferiorityLowerBound": -0.12,
        "worstDecileRatingDelta": -0.10,
        "compositionEntropyDelta": -0.01,
        "consecutiveRepeatRateDelta": 0.0,
        "absoluteQualityStatus": "passed",
        "nonInferiorityStatus": "passed",
        "failedStrata": [],
        "status": "passed",
    }
    body.update(overrides)
    return _seal(body, "reportDigest")


def _fingerprint(rc_seed: str, grammar_seed: str) -> dict[str, object]:
    return {
        "rcDigest": _digest(rc_seed),
        "grammarDigest": _digest(grammar_seed),
        "compilerDigest": _digest("a"),
        "modelFingerprint": _digest("b"),
        "fontManifestDigest": _digest("c"),
        "assetManifestDigest": _digest("d"),
        "candidatePolicyDigest": _digest("e"),
        "evaluationPolicyDigest": _digest("f"),
        "qualified": True,
        "immutable": True,
    }


def test_static_browser_deck_gates_and_bounded_shared_recovery() -> None:
    def clean(names):
        return {"checks": {name: True for name in names}, "issues": []}

    receipt = evaluate_deck_quality(
        static_evidence=clean(STATIC_REQUIRED_CHECKS),
        browser_evidence=clean(BROWSER_REQUIRED_CHECKS),
        deck_evidence=clean(DECK_REQUIRED_CHECKS),
    )
    assert receipt.passed is True
    assert receipt.p0_count == receipt.p1_count == 0

    blocked_browser = clean(BROWSER_REQUIRED_CHECKS)
    blocked_browser["issues"] = [
        {"severity": "P1", "code": "visible-overflow", "path": "slides.s02"}
    ]
    assert evaluate_deck_quality(
        static_evidence=clean(STATIC_REQUIRED_CHECKS),
        browser_evidence=blocked_browser,
        deck_evidence=clean(DECK_REQUIRED_CHECKS),
    ).passed is False

    patch = {
        "schemaVersion": "deck-critic-patch/v1",
        "deckIrDigest": _digest("1"),
        "observationDigest": _digest("2"),
        "targets": [
            {
                "slideKey": "s02",
                "allowedOperations": [{"op": "setDensity", "value": "low"}],
            }
        ],
    }
    assert validate_bounded_critic_patch(
        patch,
        expected_deck_ir_digest=_digest("1"),
        expected_observation_digest=_digest("2"),
        blocking_slide_keys=("s02",),
    ).valid
    assert not validate_bounded_critic_patch(
        {**patch, "title": "mutated"},
        expected_deck_ir_digest=_digest("1"),
        expected_observation_digest=_digest("2"),
        blocking_slide_keys=("s02",),
    ).valid

    first = consume_shared_recovery_budget(SharedRecoveryBudget(), "critic")
    assert first.accepted is True
    second = consume_shared_recovery_budget(first.next_budget, "director-correction")
    assert second.accepted is False


def test_preregistered_blind_evaluation_never_averages_failed_strata() -> None:
    policy = _policy()
    policy_digest = policy["policyDigest"]
    assert isinstance(policy_digest, str)
    passed = evaluate_visual_non_regression(
        policy,
        _report(policy_digest),
        policy_sealed_before_study=True,
    )
    assert passed.status == "passed"
    assert passed.statistical_validity == "valid"

    failed = evaluate_visual_non_regression(
        policy,
        _report(
            policy_digest,
            failedStrata=["density:high"],
            status="failed",
            nonInferiorityStatus="failed",
        ),
        policy_sealed_before_study=True,
    )
    assert failed.status == "failed"
    assert failed.failed_strata == ("density:high",)

    invalid = evaluate_visual_non_regression(
        policy,
        _report(
            policy_digest,
            reviewerAgreement=0.66,
            evaluationValidity="invalid",
            status="evaluation-invalid",
            absoluteQualityStatus="evaluation-invalid",
            nonInferiorityStatus="evaluation-invalid",
        ),
        policy_sealed_before_study=True,
    )
    assert invalid.status == "invalid"
    assert invalid.absolute_quality_status == "not-evaluated"


def test_whole_operation_rollout_shadow_and_two_frozen_rc_d6_guard() -> None:
    pre_d6 = decide_operation_rollout(
        operation_id="op-1",
        slide_keys=("s01", "s02"),
        phase="D5",
        required_capabilities_supported=False,
        pre_d6_legacy_allowed=True,
    )
    assert pre_d6.strategy == "legacy-authored"
    assert validate_whole_operation_strategy(
        {"selectedStrategy": "legacy-authored", "slideStrategies": {"s01": "legacy-authored", "s02": "legacy-authored"}}
    )
    assert not validate_whole_operation_strategy(
        {"selectedStrategy": "deck-ir", "slideStrategies": {"s01": "deck-ir", "s02": "legacy-authored"}}
    )
    assert decide_operation_rollout(
        operation_id="op-2",
        slide_keys=("s01",),
        phase="D6",
        required_capabilities_supported=False,
        pre_d6_legacy_allowed=True,
    ).strategy == "reject"
    assert validate_d2_shadow_artifact(
        {
            "retentionClass": "ephemeral",
            "retentionHours": 24,
            "ownerScopeDigest": _digest("9"),
            "publicationAllowed": False,
            "releaseEvidenceEligible": False,
        }
    ).valid

    policy_digest = _digest("f")
    history = [
        {
            "sequence": sequence,
            "rcDigest": _digest(seed),
            "frozen": True,
            "evaluationPolicyDigest": policy_digest,
            "absoluteQualityStatus": "passed",
            "statisticalValidity": "valid",
            "nonRegressionStatus": "passed",
            "failedStrata": [],
            "status": "passed",
        }
        for sequence, seed in ((10, "a"), (11, "b"))
    ]
    assert evaluate_d6_retirement_guard(history).allowed
    history[-1]["failedStrata"] = ["language:ko-KR"]
    history[-1]["status"] = "failed"
    assert not evaluate_d6_retirement_guard(history).allowed


def test_post_d6_canary_drift_rollback_and_wp8_truth_boundary() -> None:
    previous = _fingerprint("1", "2")
    candidate = _fingerprint("3", "4")
    change = detect_post_d6_change_set(previous, candidate, post_d6=True)
    assert change.valid and change.requires_new_rc
    assert "grammar" in change.changed_fields
    assert not detect_post_d6_change_set(
        previous,
        {**candidate, "rcDigest": previous["rcDigest"]},
        post_d6=True,
    ).valid

    shadow = {
        "holdoutSealed": True,
        "absoluteQualityStatus": "passed",
        "statisticalValidity": "valid",
        "nonRegressionStatus": "passed",
        "failedStrata": [],
        "reportDigest": _digest("5"),
    }
    canaries = [
        {"cohortPercent": 1, "operationCount": 100, "durationHours": 24, "status": "passed"},
        {"cohortPercent": 5, "operationCount": 500, "durationHours": 24, "status": "passed"},
        {"cohortPercent": 25, "operationCount": 1_000, "durationHours": 48, "status": "passed"},
    ]
    assert decide_canary_progression(shadow_report=shadow, completed_windows=[]).next_percent == 1
    assert decide_canary_progression(shadow_report=shadow, completed_windows=canaries).next_percent == 100

    repeated_windows = [
        {
            "failureRateDelta": 0.03,
            "p95LatencyMs": 181_000,
            "compositionEntropyDelta": -0.06,
            "consecutiveRepeatRateDelta": 0.03,
        },
        {
            "failureRateDelta": 0.03,
            "p95LatencyMs": 181_000,
            "compositionEntropyDelta": -0.06,
            "consecutiveRepeatRateDelta": 0.03,
        },
    ]
    drift_rule = evaluate_drift_rules(
        immediate_observation={"p0Count": 0, "p1Count": 0},
        rolling_windows=repeated_windows,
        previous_qualified_rc_digest=previous["rcDigest"],
    )
    assert drift_rule.action == "pause-and-rollback"
    drift = derive_quality_drift_receipt(
        previous_qualified=previous,
        candidate=candidate,
        shadow_report=shadow,
        canary_windows=canaries[:1],
        immediate_observation={"p0Count": 1, "p1Count": 0},
        rolling_windows=[],
    )
    assert drift.action == "pause-and-rollback"
    assert drift.rollback_target_rc_digest == previous["rcDigest"]
    drift_wire = drift.to_dict()
    drift_digest = drift_wire.pop("receiptDigest")
    assert drift_digest == canonical_digest(drift_wire)
    QualityDriftReceiptV1.model_validate({**drift_wire, "receiptDigest": drift_digest})

    rollback = derive_kill_switch_decision(
        phase=RolloutPhase.D8_CONTINUOUS_GOVERNANCE,
        previous_qualified_revision=previous,
        pre_d6_legacy_available=True,
    )
    assert rollback.target_strategy == "deck-ir"
    assert rollback.affects_in_flight_operations is False

    class Repository:
        def compare_and_swap_admission(self, *, expected_revision: int, decision):
            return AtomicAdmissionResult(
                committed=True,
                revision=expected_revision + 1,
                active_strategy=decision.target_strategy,
                active_rc_digest=decision.target_rc_digest,
                reason="committed",
            )

    assert apply_atomic_admission_decision(
        Repository(), expected_revision=7, decision=rollback
    ).committed

    complete_wp8_evidence = {
        "rcDigest": candidate["rcDigest"],
        "matrixCellCount": 32,
        "matrixSameRc": True,
        "workerFaultPassed": True,
        "databaseFaultPassed": True,
        "objectStoreFaultPassed": True,
        "providerFaultPassed": True,
        "maliciousCorpusPassed": True,
        "soakDurationSeconds": 86_400,
        "soakSampleCount": 1_440,
        "detachedCollectorSignatureDigests": [_digest("6")],
        "trustedCollector": True,
        "consecutiveFrozenRcReports": [
            {
                "rcDigest": _digest(seed),
                "frozen": True,
                "absoluteQualityStatus": "passed",
                "statisticalValidity": "valid",
                "nonRegressionStatus": "passed",
                "reviewerAgreement": 0.7,
                "evaluationPolicyDigest": _digest("f"),
            }
            for seed in ("7", "8")
        ],
    }
    bundle = derive_service_promotion_evidence_bundle(complete_wp8_evidence)
    assert bundle.evidence_complete is True
    assert bundle.promotion_ready is False
    assert bundle.deployment_authorized is False
    assert bundle.to_dict()["promotionReady"] is False
    assert bundle.to_dict()["deploymentAuthorized"] is False
