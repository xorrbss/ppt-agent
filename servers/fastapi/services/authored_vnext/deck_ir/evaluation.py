"""Preregistered blind visual evaluation and release-evidence derivation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from .quality import _canonical_digest, _plain, _read


@dataclass(frozen=True, slots=True)
class EvaluationPolicyDecision:
    valid: bool
    errors: tuple[str, ...]
    policy_digest: str


@dataclass(frozen=True, slots=True)
class VisualEvaluationReceipt:
    schema_version: str
    candidate_rc_digest: str | None
    evaluation_policy_digest: str
    statistical_validity: Literal["valid", "invalid"]
    absolute_quality_status: Literal["passed", "failed", "not-evaluated"]
    non_regression_status: Literal["passed", "failed", "not-evaluated"]
    failed_strata: tuple[str, ...]
    reasons: tuple[str, ...]
    status: Literal["passed", "failed", "invalid"]
    receipt_digest: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "candidateRcDigest": self.candidate_rc_digest,
            "evaluationPolicyDigest": self.evaluation_policy_digest,
            "statisticalValidity": self.statistical_validity,
            "absoluteQualityStatus": self.absolute_quality_status,
            "nonRegressionStatus": self.non_regression_status,
            "failedStrata": list(self.failed_strata),
            "reasons": list(self.reasons),
            "status": self.status,
            "receiptDigest": self.receipt_digest,
        }


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _digest_string(value: object) -> bool:
    return isinstance(value, str) and value.startswith("sha256:") and len(value) == 71


def _self_digest_matches(value: object, camel_field: str, snake_field: str) -> bool:
    payload = _plain(value)
    if not isinstance(payload, Mapping):
        return False
    supplied = payload.get(camel_field, payload.get(snake_field))
    body = dict(payload)
    body.pop(camel_field, None)
    body.pop(snake_field, None)
    return _digest_string(supplied) and supplied == _canonical_digest(body)


def validate_evaluation_policy(
    policy: object,
    *,
    policy_sealed_before_study: bool,
    results_already_observed: bool = False,
) -> EvaluationPolicyDecision:
    """Validate the frozen policy before any blind assignments are scored."""

    errors: list[str] = []
    if _read(policy, "schemaVersion", "schema_version", default=None) != (
        "visual-quality-evaluation-policy/v1"
    ):
        errors.append("evaluation_policy_schema_invalid")
    if not _self_digest_matches(policy, "policyDigest", "policy_digest"):
        errors.append("evaluation_policy_digest_invalid")
    if not policy_sealed_before_study or results_already_observed:
        errors.append("evaluation_policy_not_preregistered")
    if not _digest_string(_read(policy, "corpusDigest", "corpus_digest", default=None)):
        errors.append("evaluation_corpus_digest_invalid")
    if not _digest_string(
        _read(policy, "sealedHoldoutDigest", "sealed_holdout_digest", default=None)
    ):
        errors.append("evaluation_holdout_not_sealed")
    if _read(policy, "studyDesign", "study_design", default=None) != "paired-stratified-blind":
        errors.append("evaluation_study_design_invalid")

    power = _read(policy, "powerAnalysis", "power_analysis", default={})
    if _number(_read(power, "oneSidedAlpha", "one_sided_alpha", default=None)) != 0.025:
        errors.append("evaluation_alpha_invalid")
    if (_number(_read(power, "targetPower", "target_power", default=None)) or 0.0) < 0.8:
        errors.append("evaluation_power_invalid")
    if _number(
        _read(power, "nonInferiorityMargin", "non_inferiority_margin", default=None)
    ) != 0.25:
        errors.append("evaluation_non_inferiority_margin_invalid")
    if (_integer(_read(power, "minimumOverallPairs", "minimum_overall_pairs", default=None)) or 0) < 120:
        errors.append("evaluation_overall_sample_floor_invalid")
    if (
        _integer(
            _read(
                power,
                "minimumPairsPerRequiredMarginalStratum",
                "minimum_pairs_per_required_marginal_stratum",
                default=None,
            )
        )
        or 0
    ) < 30:
        errors.append("evaluation_stratum_sample_floor_invalid")

    protocol = _read(policy, "reviewProtocol", "review_protocol", default={})
    if (_integer(_read(protocol, "reviewersPerPair", "reviewers_per_pair", default=None)) or 0) < 3:
        errors.append("evaluation_reviewer_count_invalid")
    if _read(protocol, "ratingScale", "rating_scale", default=None) != "1..5":
        errors.append("evaluation_rating_scale_invalid")
    if _read(protocol, "calibrationRequired", "calibration_required", default=None) is not True:
        errors.append("evaluation_reviewer_calibration_missing")
    if _read(
        protocol,
        "pathRcAndFilenameBlinded",
        "path_rc_and_filename_blinded",
        default=None,
    ) is not True:
        errors.append("evaluation_blinding_invalid")
    if _read(protocol, "tieHandling", "tie_handling", default=None) != "explicit-tie":
        errors.append("evaluation_tie_policy_invalid")

    floors = _read(policy, "absoluteFloors", "absolute_floors", default={})
    expected_floors = (
        ("overallExpertMeanLowerBound", "overall_expert_mean_lower_bound", 4.0),
        ("requiredDimensionMeanLowerBound", "required_dimension_mean_lower_bound", 3.75),
        ("worstDecileMeanLowerBound", "worst_decile_mean_lower_bound", 3.5),
    )
    for camel, snake, minimum in expected_floors:
        if (_number(_read(floors, camel, snake, default=None)) or 0.0) < minimum:
            errors.append(f"evaluation_absolute_floor_invalid:{camel}")
    if _integer(_read(floors, "p0Maximum", "p0_maximum", default=None)) != 0:
        errors.append("evaluation_p0_floor_invalid")
    if _integer(_read(floors, "p1Maximum", "p1_maximum", default=None)) != 0:
        errors.append("evaluation_p1_floor_invalid")

    preference = _read(policy, "pairwisePreference", "pairwise_preference", default={})
    if (
        _number(
            _read(
                preference,
                "candidatePreferredOrTiedPointMinimum",
                "candidate_preferred_or_tied_point_minimum",
                default=None,
            )
        )
        or 0.0
    ) < 0.9:
        errors.append("evaluation_pairwise_point_floor_invalid")
    if (
        _number(
            _read(
                preference,
                "oneSided95LowerBoundMinimum",
                "one_sided_95_lower_bound_minimum",
                default=None,
            )
        )
        or 0.0
    ) < 0.85:
        errors.append("evaluation_pairwise_ci_floor_invalid")

    reliability = _read(policy, "reliability", default={})
    if _read(reliability, "metric", default=None) != "krippendorff-alpha-ordinal":
        errors.append("evaluation_reliability_metric_invalid")
    if (_number(_read(reliability, "minimum", default=None)) or 0.0) < 0.67:
        errors.append("evaluation_reliability_floor_invalid")
    if _read(
        reliability,
        "belowMinimumAction",
        "below_minimum_action",
        default=None,
    ) != "evaluation-invalid-recalibrate":
        errors.append("evaluation_reliability_action_invalid")
    if _read(policy, "confidenceMethod", "confidence_method", default=None) != (
        "stratified-bootstrap-one-sided-95-v1"
    ):
        errors.append("evaluation_confidence_method_invalid")
    if _read(
        policy,
        "multiplicityCorrection",
        "multiplicity_correction",
        default=None,
    ) != "holm-v1":
        errors.append("evaluation_multiplicity_correction_invalid")

    supplied_digest = _read(policy, "policyDigest", "policy_digest", default=None)
    policy_digest = supplied_digest if _digest_string(supplied_digest) else _canonical_digest(policy)
    return EvaluationPolicyDecision(
        valid=not errors,
        errors=tuple(dict.fromkeys(errors)),
        policy_digest=policy_digest,
    )


def _reported_strata(report: object) -> tuple[str, ...]:
    raw = _read(report, "strata", default=())
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        return ()
    return tuple(item for item in raw if isinstance(item, str) and item)


def _failed_strata_from_detailed_results(policy: object, report: object) -> set[str]:
    results = _read(report, "stratumResults", "stratum_results", default=None)
    if results is None:
        return set()
    if not isinstance(results, Mapping):
        return {"<invalid-stratum-results>"}

    floors = _read(policy, "absoluteFloors", "absolute_floors", default={})
    preference = _read(policy, "pairwisePreference", "pairwise_preference", default={})
    overall_floor = _number(
        _read(floors, "overallExpertMeanLowerBound", "overall_expert_mean_lower_bound")
    )
    dimension_floor = _number(
        _read(
            floors,
            "requiredDimensionMeanLowerBound",
            "required_dimension_mean_lower_bound",
        )
    )
    worst_floor = _number(
        _read(floors, "worstDecileMeanLowerBound", "worst_decile_mean_lower_bound")
    )
    point_floor = _number(
        _read(
            preference,
            "candidatePreferredOrTiedPointMinimum",
            "candidate_preferred_or_tied_point_minimum",
        )
    )
    ci_floor = _number(
        _read(
            preference,
            "oneSided95LowerBoundMinimum",
            "one_sided_95_lower_bound_minimum",
        )
    )
    failed: set[str] = set()
    for stratum in _reported_strata(report):
        metrics = results.get(stratum)
        if not isinstance(metrics, Mapping):
            failed.add(stratum)
            continue
        checks = (
            (_number(_read(metrics, "absoluteOverallLowerBound", default=None)), overall_floor, ">="),
            (_number(_read(metrics, "absoluteDimensionMinimumLowerBound", default=None)), dimension_floor, ">="),
            (_number(_read(metrics, "absoluteWorstDecileLowerBound", default=None)), worst_floor, ">="),
            (_number(_read(metrics, "preferredOrTiedRatio", default=None)), point_floor, ">="),
            (_number(_read(metrics, "preferredOrTiedLowerBound", default=None)), ci_floor, ">="),
            (_number(_read(metrics, "nonInferiorityLowerBound", default=None)), -0.25, ">="),
            (_number(_read(metrics, "compositionEntropyDelta", default=None)), -0.05, ">="),
            (_number(_read(metrics, "consecutiveRepeatRateDelta", default=None)), 0.02, "<="),
        )
        if any(
            value is None
            or floor is None
            or (comparison == ">=" and value < floor)
            or (comparison == "<=" and value > floor)
            for value, floor, comparison in checks
        ):
            failed.add(stratum)
        if _integer(_read(metrics, "p0Count", default=None)) != 0:
            failed.add(stratum)
        if _integer(_read(metrics, "p1Count", default=None)) != 0:
            failed.add(stratum)
    return failed


def evaluate_visual_non_regression(
    policy: object,
    report: object,
    *,
    policy_sealed_before_study: bool,
) -> VisualEvaluationReceipt:
    """Evaluate validity first, then absolute and non-regression floors.

    A reliability or study-design failure yields ``invalid`` rather than a
    candidate pass/fail.  Required-stratum failures are unioned and can never be
    averaged away by successful strata.
    """

    policy_decision = validate_evaluation_policy(
        policy,
        policy_sealed_before_study=policy_sealed_before_study,
    )
    invalid_reasons = list(policy_decision.errors)
    report_policy_digest = _read(
        report,
        "evaluationPolicyDigest",
        "evaluation_policy_digest",
        default=None,
    )
    if report_policy_digest != policy_decision.policy_digest:
        invalid_reasons.append("evaluation_report_policy_digest_mismatch")
    if not _self_digest_matches(report, "reportDigest", "report_digest"):
        invalid_reasons.append("evaluation_report_digest_invalid")
    if _read(report, "evaluationValidity", "evaluation_validity", default=None) != "valid":
        invalid_reasons.append("visual_evaluation_invalid")

    power = _read(policy, "powerAnalysis", "power_analysis", default={})
    required_overall = _integer(
        _read(power, "minimumOverallPairs", "minimum_overall_pairs", default=None)
    ) or 120
    required_stratum = _integer(
        _read(
            power,
            "minimumPairsPerRequiredMarginalStratum",
            "minimum_pairs_per_required_marginal_stratum",
            default=None,
        )
    ) or 30
    if (_integer(_read(report, "overallPairCount", "overall_pair_count", default=None)) or 0) < required_overall:
        invalid_reasons.append("evaluation_overall_sample_insufficient")
    if (
        _integer(
            _read(
                report,
                "minimumMarginalStratumPairCount",
                "minimum_marginal_stratum_pair_count",
                default=None,
            )
        )
        or 0
    ) < required_stratum:
        invalid_reasons.append("evaluation_stratum_sample_insufficient")
    reviewer_receipts = _read(
        report,
        "reviewerReceiptDigests",
        "reviewer_receipt_digests",
        default=(),
    )
    if (
        isinstance(reviewer_receipts, (str, bytes))
        or not isinstance(reviewer_receipts, Sequence)
        or len(reviewer_receipts) < 3
        or not all(_digest_string(item) for item in reviewer_receipts)
    ):
        invalid_reasons.append("evaluation_reviewer_receipts_insufficient")
    if not _digest_string(
        _read(report, "blindAssignmentDigest", "blind_assignment_digest", default=None)
    ):
        invalid_reasons.append("evaluation_blind_assignment_missing")
    reliability_floor = _number(
        _read(_read(policy, "reliability", default={}), "minimum", default=None)
    ) or 0.67
    reviewer_agreement = _number(
        _read(report, "reviewerAgreement", "reviewer_agreement", default=None)
    )
    if reviewer_agreement is None or reviewer_agreement < reliability_floor:
        invalid_reasons.append("evaluation_reliability_below_minimum")

    candidate_rc_digest = _read(
        report,
        "candidateRcDigest",
        "candidate_rc_digest",
        default=None,
    )
    failed_from_report = _read(report, "failedStrata", "failed_strata", default=())
    failed_strata = {
        item
        for item in failed_from_report
        if isinstance(item, str) and item
    } if isinstance(failed_from_report, Sequence) and not isinstance(failed_from_report, (str, bytes)) else {"<invalid-failed-strata>"}
    failed_strata.update(_failed_strata_from_detailed_results(policy, report))

    if invalid_reasons:
        reasons = tuple(dict.fromkeys(invalid_reasons))
        body = {
            "schemaVersion": "visual-evaluation-receipt/v1",
            "candidateRcDigest": candidate_rc_digest,
            "evaluationPolicyDigest": policy_decision.policy_digest,
            "statisticalValidity": "invalid",
            "reasons": list(reasons),
            "failedStrata": sorted(failed_strata),
            "status": "invalid",
        }
        return VisualEvaluationReceipt(
            schema_version="visual-evaluation-receipt/v1",
            candidate_rc_digest=(candidate_rc_digest if isinstance(candidate_rc_digest, str) else None),
            evaluation_policy_digest=policy_decision.policy_digest,
            statistical_validity="invalid",
            absolute_quality_status="not-evaluated",
            non_regression_status="not-evaluated",
            failed_strata=tuple(sorted(failed_strata)),
            reasons=reasons,
            status="invalid",
            receipt_digest=_canonical_digest(body),
        )

    floors = _read(policy, "absoluteFloors", "absolute_floors")
    quality_reasons: list[str] = []
    absolute_checks = (
        (
            _number(
                _read(
                    report,
                    "absoluteOverallMeanLowerBound",
                    "absolute_overall_mean_lower_bound",
                    default=None,
                )
            ),
            _number(
                _read(
                    floors,
                    "overallExpertMeanLowerBound",
                    "overall_expert_mean_lower_bound",
                )
            ),
            "absolute_overall_floor_failed",
        ),
        (
            _number(
                _read(
                    report,
                    "absoluteRequiredDimensionMinimumLowerBound",
                    "absolute_required_dimension_minimum_lower_bound",
                    default=None,
                )
            ),
            _number(
                _read(
                    floors,
                    "requiredDimensionMeanLowerBound",
                    "required_dimension_mean_lower_bound",
                )
            ),
            "absolute_required_dimension_floor_failed",
        ),
        (
            _number(
                _read(
                    report,
                    "absoluteWorstDecileMeanLowerBound",
                    "absolute_worst_decile_mean_lower_bound",
                    default=None,
                )
            ),
            _number(
                _read(
                    floors,
                    "worstDecileMeanLowerBound",
                    "worst_decile_mean_lower_bound",
                )
            ),
            "absolute_worst_decile_floor_failed",
        ),
    )
    for value, floor, reason in absolute_checks:
        if value is None or floor is None or value < floor:
            quality_reasons.append(reason)
    if _number(
        _read(report, "requiredCoverageRatio", "required_coverage_ratio", default=None)
    ) != 1.0:
        quality_reasons.append("required_coverage_incomplete")
    if _integer(_read(report, "p0Count", "p0_count", default=None)) != 0:
        quality_reasons.append("p0_present")
    if _integer(_read(report, "p1Count", "p1_count", default=None)) != 0:
        quality_reasons.append("p1_present")
    if (_integer(_read(report, "missingArtifactCount", "missing_artifact_count", default=0)) or 0) != 0:
        quality_reasons.append("candidate_artifact_missing")
    if failed_strata:
        quality_reasons.append("required_stratum_failed")

    non_regression_reasons: list[str] = []
    preference = _read(policy, "pairwisePreference", "pairwise_preference")
    preferred_ratio = _number(
        _read(
            report,
            "candidatePreferredOrTiedRatio",
            "candidate_preferred_or_tied_ratio",
            default=None,
        )
    )
    preferred_floor = _number(
        _read(
            preference,
            "candidatePreferredOrTiedPointMinimum",
            "candidate_preferred_or_tied_point_minimum",
        )
    )
    if preferred_ratio is None or preferred_floor is None or preferred_ratio < preferred_floor:
        non_regression_reasons.append("pairwise_preference_floor_failed")
    preferred_lower_bound = _number(
        _read(
            report,
            "candidatePreferredOrTiedLowerBound",
            "candidate_preferred_or_tied_lower_bound",
            default=None,
        )
    )
    if preferred_lower_bound is not None:
        ci_floor = _number(
            _read(
                preference,
                "oneSided95LowerBoundMinimum",
                "one_sided_95_lower_bound_minimum",
            )
        )
        if ci_floor is None or preferred_lower_bound < ci_floor:
            non_regression_reasons.append("pairwise_preference_ci_floor_failed")
    lower_bound_checks = (
        ("nonInferiorityLowerBound", "non_inferiority_lower_bound", -0.25, "non_inferiority_floor_failed"),
        ("worstDecileRatingDelta", "worst_decile_rating_delta", -0.25, "worst_decile_non_inferiority_failed"),
        ("compositionEntropyDelta", "composition_entropy_delta", -0.05, "composition_entropy_floor_failed"),
    )
    for camel, snake, floor, reason in lower_bound_checks:
        value = _number(_read(report, camel, snake, default=None))
        if value is None or value < floor:
            non_regression_reasons.append(reason)
    repeat_delta = _number(
        _read(
            report,
            "consecutiveRepeatRateDelta",
            "consecutive_repeat_rate_delta",
            default=None,
        )
    )
    if repeat_delta is None or repeat_delta > 0.02:
        non_regression_reasons.append("consecutive_repeat_rate_failed")
    if (_integer(_read(report, "unintendedThreeSlideExactRepeatCount", default=0)) or 0) != 0:
        non_regression_reasons.append("three_slide_exact_repeat_present")
    if _read(report, "nonInferiorityStatus", "non_inferiority_status", default=None) != "passed":
        non_regression_reasons.append("reported_non_inferiority_not_passed")

    absolute_status: Literal["passed", "failed"] = "failed" if quality_reasons else "passed"
    non_regression_status: Literal["passed", "failed"] = (
        "failed" if non_regression_reasons or failed_strata else "passed"
    )
    reasons = tuple(dict.fromkeys(quality_reasons + non_regression_reasons))
    status: Literal["passed", "failed"] = (
        "passed"
        if absolute_status == "passed" and non_regression_status == "passed"
        else "failed"
    )
    body = {
        "schemaVersion": "visual-evaluation-receipt/v1",
        "candidateRcDigest": candidate_rc_digest,
        "evaluationPolicyDigest": policy_decision.policy_digest,
        "statisticalValidity": "valid",
        "absoluteQualityStatus": absolute_status,
        "nonRegressionStatus": non_regression_status,
        "failedStrata": sorted(failed_strata),
        "reasons": list(reasons),
        "status": status,
    }
    return VisualEvaluationReceipt(
        schema_version="visual-evaluation-receipt/v1",
        candidate_rc_digest=(candidate_rc_digest if isinstance(candidate_rc_digest, str) else None),
        evaluation_policy_digest=policy_decision.policy_digest,
        statistical_validity="valid",
        absolute_quality_status=absolute_status,
        non_regression_status=non_regression_status,
        failed_strata=tuple(sorted(failed_strata)),
        reasons=reasons,
        status=status,
        receipt_digest=_canonical_digest(body),
    )


@dataclass(frozen=True, slots=True)
class ServicePromotionEvidenceBundle:
    schema_version: str
    rc_digest: str | None
    evidence_complete: bool
    missing_or_invalid_evidence: tuple[str, ...]
    evidence_digest: str
    # WP8 is code-only in this work package. These must remain literal false
    # until real Gate 5 evidence and a separate operator authority exist.
    promotion_ready: bool = field(default=False, init=False)
    deployment_authorized: bool = field(default=False, init=False)

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "rcDigest": self.rc_digest,
            "evidenceComplete": self.evidence_complete,
            "missingOrInvalidEvidence": list(self.missing_or_invalid_evidence),
            "evidenceDigest": self.evidence_digest,
            "promotionReady": False,
            "deploymentAuthorized": False,
        }


def derive_service_promotion_evidence_bundle(evidence: object) -> ServicePromotionEvidenceBundle:
    """Validate Gate 5 evidence without granting promotion or deployment."""

    errors: list[str] = []
    rc_digest = _read(evidence, "rcDigest", "rc_digest", default=None)
    if not _digest_string(rc_digest):
        errors.append("release_rc_digest_invalid")
    if _integer(_read(evidence, "matrixCellCount", "matrix_cell_count", default=None)) != 32:
        errors.append("release_same_rc_32_cell_matrix_missing")
    if _read(evidence, "matrixSameRc", "matrix_same_rc", default=None) is not True:
        errors.append("release_matrix_rc_binding_invalid")
    for field_name, code in (
        ("workerFaultPassed", "release_worker_fault_evidence_missing"),
        ("databaseFaultPassed", "release_database_fault_evidence_missing"),
        ("objectStoreFaultPassed", "release_object_store_fault_evidence_missing"),
        ("providerFaultPassed", "release_provider_fault_evidence_missing"),
        ("maliciousCorpusPassed", "release_security_evidence_missing"),
    ):
        if _read(evidence, field_name, default=None) is not True:
            errors.append(code)
    if (_integer(_read(evidence, "soakDurationSeconds", "soak_duration_seconds", default=None)) or 0) < 86_400:
        errors.append("release_soak_duration_insufficient")
    if (_integer(_read(evidence, "soakSampleCount", "soak_sample_count", default=None)) or 0) < 1_440:
        errors.append("release_soak_samples_insufficient")
    signatures = _read(
        evidence,
        "detachedCollectorSignatureDigests",
        "detached_collector_signature_digests",
        default=(),
    )
    if (
        isinstance(signatures, (str, bytes))
        or not isinstance(signatures, Sequence)
        or not signatures
        or not all(_digest_string(item) for item in signatures)
        or _read(evidence, "trustedCollector", "trusted_collector", default=None) is not True
    ):
        errors.append("release_trusted_collector_signature_missing")
    frozen_reports = _read(
        evidence,
        "consecutiveFrozenRcReports",
        "consecutive_frozen_rc_reports",
        default=(),
    )
    if isinstance(frozen_reports, (str, bytes)) or not isinstance(frozen_reports, Sequence):
        frozen_reports = ()
    if len(frozen_reports) < 2:
        errors.append("release_two_frozen_rc_reports_missing")
    else:
        selected = frozen_reports[-2:]
        policy_digests = set()
        rc_digests = set()
        for report in selected:
            if _read(report, "frozen", default=None) is not True:
                errors.append("release_rc_not_frozen")
            if _read(report, "absoluteQualityStatus", default=None) != "passed":
                errors.append("release_absolute_quality_not_passed")
            if _read(report, "statisticalValidity", default=None) != "valid":
                errors.append("release_statistical_evaluation_invalid")
            if _read(report, "nonRegressionStatus", default=None) != "passed":
                errors.append("release_non_regression_not_passed")
            if (_number(_read(report, "reviewerAgreement", default=None)) or 0.0) < 0.67:
                errors.append("release_reviewer_reliability_invalid")
            policy_digests.add(_read(report, "evaluationPolicyDigest", default=None))
            rc_digests.add(_read(report, "rcDigest", default=None))
        if len(policy_digests) != 1 or not all(_digest_string(item) for item in policy_digests):
            errors.append("release_frozen_rc_policy_mismatch")
        if len(rc_digests) != 2 or not all(_digest_string(item) for item in rc_digests):
            errors.append("release_frozen_rc_identity_invalid")

    unique_errors = tuple(dict.fromkeys(errors))
    body = {
        "schemaVersion": "service-promotion-evidence-bundle/v1",
        "rcDigest": rc_digest,
        "evidenceComplete": not unique_errors,
        "missingOrInvalidEvidence": list(unique_errors),
        "sourceEvidenceDigest": _canonical_digest(_plain(evidence)),
        "promotionReady": False,
        "deploymentAuthorized": False,
    }
    return ServicePromotionEvidenceBundle(
        schema_version="service-promotion-evidence-bundle/v1",
        rc_digest=rc_digest if isinstance(rc_digest, str) else None,
        evidence_complete=not unique_errors,
        missing_or_invalid_evidence=unique_errors,
        evidence_digest=_canonical_digest(body),
    )


__all__ = [
    "EvaluationPolicyDecision",
    "ServicePromotionEvidenceBundle",
    "VisualEvaluationReceipt",
    "derive_service_promotion_evidence_bundle",
    "evaluate_visual_non_regression",
    "validate_evaluation_policy",
]
