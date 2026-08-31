from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from services.authored_vnext.deck_ir.canonical import (
    canonical_digest,
    canonical_json,
    derive_qid_registry,
    seal_contract,
)
from services.authored_vnext.deck_ir.contracts import (
    ArtDirectionIntentV1,
    CandidateSetSelectionReceiptV1,
    CompositionIntentGraphV1,
    DeckCriticPatchV1,
    DeckIRV1,
    GenerationIntentLineageV1,
    GenerationStrategyDecisionReceiptV1,
    QualityDriftReceiptV1,
    RequirementCoverageReceiptV1,
    RequirementGraphV1,
    SourceBindingV1,
    VisualNonRegressionReportV1,
    VisualQualityEvaluationPolicyV1,
)
from services.authored_vnext.deck_ir.validation import (
    DeckIRContractError,
    parse_contract,
    validate_critic_patch,
    validate_deck_ir,
    validate_requirement_coverage,
    validate_revision_immutability,
)


DIGEST = "sha256:" + "1" * 64
OTHER_DIGEST = "sha256:" + "2" * 64
OPERATION_ID = "12345678-1234-5678-1234-567812345678"


def _seal(model_type, payload, digest_field=None):
    return seal_contract(payload, digest_field, model_type=model_type)


def _contracts():
    source = SourceBindingV1.model_validate(
        {
            "schemaVersion": "source-binding/v1",
            "sourceBindingId": "src-001",
            "snapshotDigest": DIGEST,
            "blockId": "page-3.paragraph-2",
            "contentDigest": OTHER_DIGEST,
            "useMode": "verbatim",
            "allowedTransform": "none",
            "requiredOccurrences": 1,
            "normalizationPolicy": "unicode-nfc-whitespace-v1",
        }
    )
    requirement = _seal(
        RequirementGraphV1,
        {
            "schemaVersion": "requirement-graph/v1",
            "requestDigest": DIGEST,
            "requirements": [
                {
                    "requirementId": "req-001",
                    "kind": "claim",
                    "priority": "required",
                    "normalizedIntent": "통합 플랫폼의 리드타임 개선 근거를 제시",
                    "sourceBindingIds": ["src-001"],
                    "minimumOccurrences": 1,
                    "maximumOccurrences": 2,
                    "allowedSlideRoles": ["evidence"],
                }
            ],
        },
    )
    composition = _seal(
        CompositionIntentGraphV1,
        {
            "schemaVersion": "composition-intent-graph/v1",
            "slideKey": "s01",
            "zones": [
                {
                    "zoneKey": "claim-zone",
                    "semanticRole": "claim",
                    "contentRefs": ["title"],
                    "preferredColumnSpan": 5,
                    "minimumColumnSpan": 4,
                    "maximumColumnSpan": 7,
                    "visualWeight": "dominant",
                    "flow": "vertical",
                },
                {
                    "zoneKey": "evidence-zone",
                    "semanticRole": "evidence",
                    "contentRefs": ["metric.primary"],
                    "preferredColumnSpan": 7,
                    "minimumColumnSpan": 5,
                    "maximumColumnSpan": 8,
                    "visualWeight": "supporting",
                    "flow": "cluster",
                },
                {
                    "zoneKey": "decoration-zone",
                    "semanticRole": "decoration",
                    "contentRefs": ["decoration.accent"],
                    "preferredColumnSpan": 2,
                    "minimumColumnSpan": 1,
                    "maximumColumnSpan": 3,
                    "visualWeight": "ambient",
                    "flow": "stack",
                },
            ],
            "relations": [
                {"from": "claim-zone", "relation": "leftOf", "to": "evidence-zone", "strength": "hard"},
                {"from": "claim-zone", "relation": "alignTop", "to": "evidence-zone", "strength": "soft"},
            ],
            "readingOrder": ["claim-zone", "evidence-zone", "decoration-zone"],
        },
    )
    art = _seal(
        ArtDirectionIntentV1,
        {
            "schemaVersion": "art-direction-intent/v1",
            "slideKey": "s01",
            "compositionIntentGraphDigest": composition.graph_digest,
            "focalAnchor": {"zoneKey": "evidence-zone", "strength": "strong"},
            "negativeSpace": {"bias": "right", "amount": "generous"},
            "asymmetry": "moderate",
            "overlapPolicy": {
                "mode": "decorative-layering",
                "allowedZonePairs": [["evidence-zone", "decoration-zone"]],
                "textOcclusion": "forbidden",
                "sourceOcclusion": "forbidden",
            },
            "imageTreatment": "edge-crop",
            "cropIntent": "subject-emphasis",
            "typographyRhythm": "editorial-contrast",
            "depthIntent": "layered",
            "visualEnergy": "progressive",
        },
    )
    deck = DeckIRV1.model_validate(
        {
            "schemaVersion": "deck-ir/v1",
            "deckKey": "deck",
            "title": "서비스 제안",
            "language": "ko-KR",
            "story": {
                "audience": "enterprise decision makers",
                "objective": "decision",
                "thesis": "통합 플랫폼이 개발 리드타임과 품질을 함께 개선한다",
                "arc": ["evidence"],
            },
            "designLanguage": {
                "grammarId": "enterprise-modular",
                "mood": ["credible", "progressive"],
                "paletteId": "blue-neutral-accent",
                "typeScaleId": "business-korean-v1",
                "gridId": "wide-12",
                "motifId": "connected-modules",
                "density": "medium",
                "variationBudget": 2,
            },
            "slides": [
                {
                    "slideKey": "s01",
                    "role": "evidence",
                    "claim": "분절된 도구가 개발 리드타임을 늘린다",
                    "evidence": ["40%"],
                    "requirementIds": ["req-001"],
                    "sourceBindingIds": ["src-001"],
                    "compositionIntentGraphDigest": composition.graph_digest,
                    "artDirectionIntentDigest": art.intent_digest,
                    "repetitionPurpose": "none",
                    "layoutIntent": {
                        "composition": "asymmetric-split",
                        "emphasis": "right",
                        "readingOrder": "title-evidence",
                        "density": "medium",
                    },
                    "components": [
                        {
                            "elementKey": "title",
                            "kind": "text",
                            "semanticRole": "title",
                            "contentPriority": "must-keep",
                            "requirementIds": [],
                            "sourceBindingId": None,
                            "layoutSlot": "title",
                            "variant": "primary",
                            "payload": {"text": "리드타임 개선"},
                        },
                        {
                            "elementKey": "metric.primary",
                            "kind": "metric",
                            "semanticRole": "evidence",
                            "contentPriority": "must-keep",
                            "requirementIds": ["req-001"],
                            "sourceBindingId": "src-001",
                            "layoutSlot": "primary",
                            "variant": "emphasis",
                            "payload": {
                                "value": "40%",
                                "label": "리드타임 단축",
                                "sourceContentDigest": OTHER_DIGEST,
                            },
                        },
                        {
                            "elementKey": "decoration.accent",
                            "kind": "decoration",
                            "semanticRole": "decoration",
                            "contentPriority": "optional",
                            "requirementIds": [],
                            "sourceBindingId": None,
                            "layoutSlot": "ambient",
                            "variant": "subtle",
                            "payload": {"motif": "connected-modules"},
                        },
                    ],
                }
            ],
        }
    )
    return source, requirement, composition, art, deck


def test_deck_ir_positive_contract_graph_and_canonical_fixture() -> None:
    source, requirement, composition, art, deck = _contracts()
    deck_digest = validate_deck_ir(
        deck,
        [composition],
        [art],
        requirement,
        [source],
        admitted_slide_count=1,
    )
    assert deck_digest == canonical_digest(deck)
    assert derive_qid_registry(deck) == {
        "s01.title": "s01:title",
        "s01.metric.primary": "s01:metric.primary",
        "s01.decoration.accent": "s01:decoration.accent",
    }
    assert canonical_json({"z": -0.0, "a": "e\u0301"}) == '{"a":"é","z":0}'
    fixture_path = (
        Path(__file__).parents[4]
        / "contracts"
        / "authored_vnext"
        / "deck_ir"
        / "v1"
        / "canonical-fixture.json"
    )
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert canonical_json(fixture["input"]) == fixture["canonicalJson"]
    assert canonical_digest(fixture["input"]) == fixture["digest"]

    coverage = _seal(
        RequirementCoverageReceiptV1,
        {
            "schemaVersion": "requirement-coverage-receipt/v1",
            "requirementGraphDigest": requirement.graph_digest,
            "deckIrDigest": deck_digest,
            "requiredTotal": 1,
            "requiredCovered": 1,
            "optionalTotal": 0,
            "optionalCovered": 0,
            "duplicateViolations": [],
            "uncoveredRequirementIds": [],
            "occurrences": [
                {
                    "requirementId": "req-001",
                    "slideKey": "s01",
                    "elementKey": "metric.primary",
                    "sourceBindingId": "src-001",
                    "visibility": "visible",
                }
            ],
            "status": "passed",
        },
    )
    validate_requirement_coverage(coverage, requirement, deck)


def test_strict_unknown_fields_and_raw_authoring_inputs_are_rejected() -> None:
    _, _, _, _, deck = _contracts()
    raw = deck.model_dump(mode="json", by_alias=True)
    raw["slides"][0]["bogus"] = True
    with pytest.raises(DeckIRContractError, match="deck_ir_schema_invalid"):
        parse_contract(DeckIRV1, raw)

    raw = deck.model_dump(mode="json", by_alias=True)
    raw["slides"][0]["components"][0]["payload"]["text"] = "<div style='left:1px'>unsafe</div>"
    with pytest.raises(DeckIRContractError, match="deck_ir_raw_geometry_or_style_forbidden"):
        parse_contract(DeckIRV1, raw)


def test_composition_cycle_span_ownership_and_overlap_negative_cases() -> None:
    source, requirement, composition, art, deck = _contracts()
    graph_payload = composition.model_dump(mode="json", by_alias=True, exclude={"graph_digest"})
    graph_payload["relations"].append(
        {"from": "evidence-zone", "relation": "leftOf", "to": "claim-zone", "strength": "hard"}
    )
    cycle = _seal(CompositionIntentGraphV1, graph_payload)
    cycle_deck = deck.model_copy(
        update={
            "slides": [
                deck.slides[0].model_copy(update={"composition_intent_graph_digest": cycle.graph_digest})
            ]
        }
    )
    with pytest.raises(DeckIRContractError, match="composition_intent_graph_invalid"):
        validate_deck_ir(cycle_deck, [cycle], [art], requirement, [source])

    graph_payload = composition.model_dump(mode="json", by_alias=True, exclude={"graph_digest"})
    graph_payload["zones"][1]["contentRefs"] = ["title", "metric.primary"]
    duplicate_owner = _seal(CompositionIntentGraphV1, graph_payload)
    owner_deck = deck.model_copy(
        update={
            "slides": [deck.slides[0].model_copy(update={"composition_intent_graph_digest": duplicate_owner.graph_digest})]
        }
    )
    with pytest.raises(DeckIRContractError, match="composition_intent_content_owner_invalid"):
        validate_deck_ir(owner_deck, [duplicate_owner], [art], requirement, [source])

    graph_payload = composition.model_dump(mode="json", by_alias=True, exclude={"graph_digest"})
    graph_payload["zones"][0]["minimumColumnSpan"] = 8
    with pytest.raises(ValidationError):
        _seal(CompositionIntentGraphV1, graph_payload)

    art_payload = art.model_dump(mode="json", by_alias=True, exclude={"intent_digest"})
    art_payload["overlapPolicy"]["allowedZonePairs"] = [["claim-zone", "evidence-zone"]]
    unsafe_art = _seal(ArtDirectionIntentV1, art_payload)
    unsafe_deck = deck.model_copy(
        update={"slides": [deck.slides[0].model_copy(update={"art_direction_intent_digest": unsafe_art.intent_digest})]}
    )
    with pytest.raises(DeckIRContractError, match="art_direction_overlap_unsafe"):
        validate_deck_ir(unsafe_deck, [composition], [unsafe_art], requirement, [source])


def test_source_title_and_qid_are_immutable() -> None:
    _, _, _, _, deck = _contracts()
    with pytest.raises(DeckIRContractError, match="deck_ir_title_immutable"):
        validate_revision_immutability(deck, deck.model_copy(update={"title": "changed"}))

    slide = deck.slides[0]
    components = list(slide.components)
    components[1] = components[1].model_copy(
        update={"payload": components[1].payload.model_copy(update={"value": "41%"})}
    )
    changed_source = deck.model_copy(update={"slides": [slide.model_copy(update={"components": components})]})
    with pytest.raises(DeckIRContractError, match="deck_ir_source_immutable"):
        validate_revision_immutability(deck, changed_source)

    components = list(slide.components)
    components[0] = components[0].model_copy(update={"element_key": "title.changed"})
    changed_qid = deck.model_copy(update={"slides": [slide.model_copy(update={"components": components})]})
    with pytest.raises(DeckIRContractError, match="deck_ir_qid_immutable"):
        validate_revision_immutability(deck, changed_qid)


def test_candidate_strategy_critic_and_lineage_contracts() -> None:
    _, _, _, _, deck = _contracts()
    candidate_payload = {
        "schemaVersion": "candidate-set-selection-receipt/v1",
        "operationId": OPERATION_ID,
        "deckIrDigest": canonical_digest(deck),
        "grammarDigest": DIGEST,
        "compilerVersion": "constraint-compiler/v1",
        "candidatePolicyDigest": OTHER_DIGEST,
        "slides": [
            {
                "slideKey": "s01",
                "requestedK": 6,
                "enumeratedCount": 1,
                "feasibleCount": 1,
                "candidates": [
                    {
                        "candidateId": "c01",
                        "layoutId": "asymmetric-split/2",
                        "hardConstraintStatus": "passed",
                        "qualityVector": {"hierarchy": 0.9, "whitespace": 0.8, "roleFitness": 0.9},
                        "diversitySignature": DIGEST,
                        "candidateDigest": OTHER_DIGEST,
                    }
                ],
                "selectedCandidateId": "c01",
            }
        ],
        "deckSelection": {
            "algorithm": "pareto-beam-v1",
            "beamWidth": 8,
            "objectivePolicyDigest": DIGEST,
            "selectedCombinationDigest": OTHER_DIGEST,
        },
        "providerCallsAdded": 0,
    }
    receipt = _seal(CandidateSetSelectionReceiptV1, candidate_payload)
    assert receipt.provider_calls_added == 0
    candidate_payload["slides"][0]["candidates"][0]["hardConstraintStatus"] = "failed"
    candidate_payload["slides"][0]["feasibleCount"] = 0
    with pytest.raises(ValidationError, match="deck_ir_hard_failure_candidate_selected"):
        _seal(CandidateSetSelectionReceiptV1, candidate_payload)

    strategy = {
        "schemaVersion": "generation-strategy-decision-receipt/v1",
        "operationId": OPERATION_ID,
        "requirementGraphDigest": DIGEST,
        "requestedProfile": "balanced-quality-v1",
        "grammarCapabilityDigest": DIGEST,
        "compilerCapabilityDigest": OTHER_DIGEST,
        "rolloutPhase": "D6",
        "selectedStrategy": "legacy-authored",
        "missingCapabilities": ["chart"],
        "decisionReason": "unsupported-capability",
        "routingPolicyDigest": DIGEST,
    }
    with pytest.raises(ValidationError, match="deck_ir_post_d6_unsupported_strategy_invalid"):
        _seal(GenerationStrategyDecisionReceiptV1, strategy)

    patch = DeckCriticPatchV1.model_validate(
        {
            "schemaVersion": "deck-critic-patch/v1",
            "deckIrDigest": canonical_digest(deck),
            "observationDigest": DIGEST,
            "targets": [
                {
                    "slideKey": "s01",
                    "allowedOperations": [{"op": "replaceLayoutVariant", "value": "asymmetric-split/3"}],
                }
            ],
        }
    )
    validate_critic_patch(patch, deck)
    with pytest.raises(ValidationError):
        DeckCriticPatchV1.model_validate(
            {
                **patch.model_dump(mode="json", by_alias=True),
                "targets": [{"slideKey": "s01", "allowedOperations": [{"op": "setTitle", "value": "changed"}]}],
            }
        )

    exact = GenerationIntentLineageV1.model_validate(
        {
            "schemaVersion": "generation-intent-lineage/v1",
            "intentConsistencyState": "exact",
            "generationIntentDigest": DIGEST,
            "compileReceiptDigest": OTHER_DIGEST,
            "parentBundleDigest": None,
            "editCommandReceiptDigest": None,
        }
    )
    assert exact.intent_consistency_state == "exact"
    with pytest.raises(ValidationError, match="deck_ir_derived_lineage_invalid"):
        GenerationIntentLineageV1.model_validate(
            {
                "schemaVersion": "generation-intent-lineage/v1",
                "intentConsistencyState": "derived-by-command",
                "originGenerationIntentDigest": DIGEST,
                "compileReceiptDigest": OTHER_DIGEST,
                "parentBundleDigest": DIGEST,
            }
        )


def test_evaluation_non_regression_and_drift_receipts_are_fail_closed() -> None:
    policy = _seal(
        VisualQualityEvaluationPolicyV1,
        {
            "schemaVersion": "visual-quality-evaluation-policy/v1",
            "corpusDigest": DIGEST,
            "sealedHoldoutDigest": OTHER_DIGEST,
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
    )
    assert parse_contract(
        VisualQualityEvaluationPolicyV1,
        policy.model_dump(mode="json", by_alias=True),
    ) == policy

    report_payload = {
        "schemaVersion": "visual-non-regression-report/v1",
        "baselineRcDigest": DIGEST,
        "candidateRcDigest": OTHER_DIGEST,
        "corpusDigest": DIGEST,
        "evaluationPolicyDigest": policy.policy_digest,
        "strata": ["language:ko-KR"],
        "blindAssignmentDigest": DIGEST,
        "reviewerReceiptDigests": [DIGEST, OTHER_DIGEST, DIGEST],
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
        "worstDecileRatingDelta": -0.1,
        "compositionEntropyDelta": -0.01,
        "consecutiveRepeatRateDelta": 0.0,
        "absoluteQualityStatus": "passed",
        "nonInferiorityStatus": "passed",
        "failedStrata": [],
        "status": "passed",
    }
    report = _seal(VisualNonRegressionReportV1, report_payload)
    assert report.status == "passed"
    report_payload["reviewerAgreement"] = 0.66
    with pytest.raises(ValidationError, match="deck_ir_non_regression_status_invalid"):
        _seal(VisualNonRegressionReportV1, report_payload)

    drift_payload = {
        "schemaVersion": "quality-drift-receipt/v1",
        "candidateRcDigest": OTHER_DIGEST,
        "previousQualifiedRcDigest": DIGEST,
        "changedDigests": {
            "grammar": DIGEST,
            "compiler": OTHER_DIGEST,
            "modelFingerprint": DIGEST,
            "font": OTHER_DIGEST,
            "asset": DIGEST,
            "candidatePolicy": OTHER_DIGEST,
            "evaluationPolicy": DIGEST,
        },
        "shadowVisualReportDigest": report.report_digest,
        "canaryWindows": [{"cohortPercent": 1, "minimumOperations": 100, "minimumHours": 24, "status": "passed"}],
        "observedDrift": {
            "p0": 0,
            "p1": 1,
            "repeatRateDelta": 0,
            "compositionEntropyDelta": 0,
            "p95LatencyDeltaMs": 0,
            "generationFailureRateDeltaPoints": 0,
        },
        "triggeredRules": ["confirmed-p1"],
        "action": "advance-canary",
        "rollbackTargetRcDigest": DIGEST,
    }
    with pytest.raises(ValidationError, match="deck_ir_quality_drift_immediate_stop_required"):
        _seal(QualityDriftReceiptV1, drift_payload)

    drift_payload["action"] = "pause-and-rollback"
    drift = _seal(QualityDriftReceiptV1, drift_payload)
    assert parse_contract(
        QualityDriftReceiptV1,
        drift.model_dump(mode="json", by_alias=True),
    ) == drift

    assert UUID(OPERATION_ID)
