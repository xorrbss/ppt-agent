from __future__ import annotations

from copy import deepcopy

from services.authored_vnext.deck_ir.visual_evaluator import (
    ARTIFACT_BINDING_SCHEMA_VERSION,
    EVIDENCE_SCHEMA_VERSION,
    HUMAN_CORRELATION_SCHEMA_VERSION,
    METRIC_VERSION,
    REQUIRED_DIMENSIONS,
    evaluate_visual_evidence,
    visual_evidence_digest,
)


def _digest(seed: str) -> str:
    return "sha256:" + seed * 64


def _seal(value: dict[str, object], field: str) -> dict[str, object]:
    result = deepcopy(value)
    result[field] = visual_evidence_digest(result)
    return result


def _artifact(*, seed: str, roles: tuple[str, ...], revision: int = 7) -> dict[str, object]:
    return {
        "schemaVersion": ARTIFACT_BINDING_SCHEMA_VERSION,
        "presentationId": f"00000000-0000-4000-8000-0000000000{seed}",
        "artifactDigest": _digest(seed[0]),
        "operationId": f"10000000-0000-4000-8000-0000000000{seed}",
        "revision": revision,
        "orderedSlides": [
            {"pageNumber": index, "slideDigest": _digest(str(index)), "role": role}
            for index, role in enumerate(roles, start=1)
        ],
    }


def _correlation() -> dict[str, object]:
    return _seal(
        {
            "schemaVersion": HUMAN_CORRELATION_SCHEMA_VERSION,
            "metricVersion": METRIC_VERSION,
            "goldSetDigest": _digest("a"),
            "sealedHoldoutDigest": _digest("b"),
            "evaluationPolicyDigest": _digest("c"),
            "sealedBeforeScoring": True,
            "overallPairCount": 120,
            "minimumMarginalStratumPairCount": 30,
            "reviewerReceiptDigests": [_digest("d"), _digest("e"), _digest("f")],
            "blindAssignmentDigest": _digest("9"),
            "reviewerAgreement": 0.74,
            "correlationMethod": "spearman",
            "rankCorrelation": 0.81,
            "worstPageRecall": 0.94,
            "criticalFalseAcceptRate": 0.03,
            "status": "passed",
        },
        "receiptDigest",
    )


def _dimensions(overall: float) -> dict[str, float]:
    return {
        dimension: overall if dimension == "overall" else 4.2
        for dimension in REQUIRED_DIMENSIONS
    }


def _evidence(*, correlation: object = None) -> dict[str, object]:
    roles = ("cover", "problem")
    candidate = _artifact(seed="11", roles=roles)
    reference = _artifact(seed="22", roles=roles, revision=3)
    pages = []
    for index, overall in enumerate((4.5, 4.7), start=1):
        candidate_slide = candidate["orderedSlides"][index - 1]
        reference_slide = reference["orderedSlides"][index - 1]
        pages.append(
            {
                "pageNumber": index,
                "candidateSlideDigest": candidate_slide["slideDigest"],
                "referenceSlideDigest": reference_slide["slideDigest"],
                "candidateRole": candidate_slide["role"],
                "referenceRole": reference_slide["role"],
                "dimensions": _dimensions(overall),
                "automaticStatus": "passed",
                "humanStatus": "not-reviewed",
                "visualContradiction": False,
                "issues": [],
            }
        )
    return _seal(
        {
            "schemaVersion": EVIDENCE_SCHEMA_VERSION,
            "candidate": candidate,
            "reference": reference,
            "pages": pages,
            "humanCorrelation": correlation,
            "informativeMetrics": {},
        },
        "evidenceDigest",
    )


def test_artifact_bound_worst_page_visual_acceptance() -> None:
    passed = evaluate_visual_evidence(_evidence(correlation=_correlation()))
    assert passed.acceptance is True
    assert passed.percentage == 92.0
    assert passed.worst_page_number == 1
    assert passed.worst_page_score == 4.5
    assert passed.human_correlation_status == "verified"

    uncorrelated = evaluate_visual_evidence(_evidence())
    assert uncorrelated.acceptance is False
    assert uncorrelated.percentage is None
    assert uncorrelated.reasons == ("human_correlation_unverified",)

    informative = _evidence(correlation=_correlation())
    informative["informativeMetrics"] = {
        "score": 96.47,
        "meanStyleScore": 96.45,
        "geometryScore": 100.0,
    }
    informative["evidenceDigest"] = visual_evidence_digest(informative)
    informative_decision = evaluate_visual_evidence(informative)
    assert informative_decision.acceptance is True
    assert informative_decision.to_dict()["informativeMetricsUsedForAcceptance"] is False

    forbidden = _evidence(correlation=_correlation())
    forbidden["score"] = 96.47
    forbidden["geometryScore"] = 100.0
    forbidden["evidenceDigest"] = visual_evidence_digest(forbidden)
    forbidden_decision = evaluate_visual_evidence(forbidden)
    assert forbidden_decision.acceptance is False
    assert "acceptance_metric_not_allowed:score" in forbidden_decision.reasons
    assert "acceptance_metric_not_allowed:geometryScore" in forbidden_decision.reasons

    failed = _evidence(correlation=_correlation())
    failed_page = failed["pages"][1]
    failed_page["dimensions"]["typography"] = 3.4
    failed_page["dimensions"]["overall"] = 3.2
    failed_page["humanStatus"] = "failed"
    failed["evidenceDigest"] = visual_evidence_digest(failed)
    failed_decision = evaluate_visual_evidence(failed)
    assert failed_decision.acceptance is False
    assert failed_decision.worst_page_number == 2
    assert failed_decision.worst_page_score == 3.2
    assert "page_dimension_floor_failed:2:typography" in failed_decision.reasons
    assert "worst_page_floor_failed:2" in failed_decision.reasons
    assert "visual_evidence_contradiction:2" in failed_decision.reasons

    rebound = _evidence(correlation=_correlation())
    rebound["candidate"]["revision"] = 8
    rebound_decision = evaluate_visual_evidence(rebound)
    assert rebound_decision.acceptance is False
    assert rebound_decision.percentage is None
    assert "visual_evidence_digest_invalid" in rebound_decision.reasons

    role_mismatch = _evidence(correlation=_correlation())
    role_mismatch["pages"][0]["referenceRole"] = "closing"
    role_mismatch["evidenceDigest"] = visual_evidence_digest(role_mismatch)
    mismatch_decision = evaluate_visual_evidence(role_mismatch)
    assert mismatch_decision.acceptance is False
    assert "reference_page_role_binding_invalid:1" in mismatch_decision.reasons
    assert "page_role_mismatch:1" in mismatch_decision.reasons
