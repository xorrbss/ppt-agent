"""Deterministic geometry alternatives and exact no-candidate evidence."""

from copy import deepcopy
from dataclasses import asdict

import pytest

from models.slidespec_v1 import (
    SLIDESPEC_SCHEMA,
    Bounds,
    ContentNode,
    LayoutConstraints,
    LayoutNode,
    Provenance,
    SlideDigests,
    SlideSpec,
)
from services.slidespec_geometry_candidates import generate_geometry_candidates
from services.slidespec_repair import (
    build_deterministic_cascade_report,
    repair_cost,
)
from utils.slidespec_core import ZERO_DIGEST, apply_repair_plan, seal_slidespec


_BASE_HASH = "sha256:" + "a" * 64
_CONTENT_HASH = "sha256:" + "c" * 64


def _context(issue):
    return {
        "slideId": "slide-5",
        "baseSpecRevision": 3,
        "baseSpecHash": _BASE_HASH,
        "expectedContentDigest": _CONTENT_HASH,
        "geometryIssues": [issue],
        "limits": {"maxOperations": 8, "maxAffectedBlocks": 4},
    }


def _identity(**values):
    result = {
        "code": "dom.unintended_overlap",
        "semanticId": "sem:card-a",
        "qid": "card-a",
        "constraintId": "constraint:cards:no-overlap",
        "kind": "nonOverlap",
        "target": "lay:card-a",
        "sameCoordinateSpace": True,
        "detectorEvidence": {
            "confirmed": True,
            "independentElements": True,
            "stage": "dom",
            "metric": "overlap_area_ratio",
            "value": 0.12,
            "threshold": 0.0,
        },
    }
    result.update(values)
    return result


def test_non_overlap_generates_bounded_target_peer_and_local_reflow_alternatives():
    issue = _identity(
        currentBounds={"x": 0.2, "y": 0.2, "width": 0.3, "height": 0.3},
        otherTarget="lay:card-b",
        otherBounds={"x": 0.4, "y": 0.2, "width": 0.3, "height": 0.3},
        clearance=0.02,
        mutationEnvelope={
            "allowedOperations": ["translateWithinParent"],
            "maxTranslation": 0.4,
        },
        otherMutationEnvelope={
            "allowedOperations": ["translateWithinParent"],
            "maxTranslation": 0.4,
        },
    )

    first = generate_geometry_candidates(_context(issue))
    second = generate_geometry_candidates(_context(issue))

    assert first == second
    assert len(first.candidates) >= 3
    assert any(len(candidate.operations) == 2 for candidate in first.candidates)
    assert {
        operation["target"]
        for candidate in first.candidates
        for operation in candidate.operations
    } == {"lay:card-a", "lay:card-b"}
    assert first.rejections == ()


def test_candidate_budget_preserves_one_minimum_change_option_per_issue_first():
    common = {
        "currentBounds": {"x": 0.2, "y": 0.2, "width": 0.3, "height": 0.3},
        "otherBounds": {"x": 0.4, "y": 0.2, "width": 0.3, "height": 0.3},
        "clearance": 0.02,
        "mutationEnvelope": {
            "allowedOperations": ["translateWithinParent"],
            "maxTranslation": 0.4,
        },
    }
    first = _identity(otherTarget="lay:card-b", **common)
    second = _identity(
        semanticId="sem:card-c",
        qid="card-c",
        target="lay:card-c",
        otherTarget="lay:card-d",
        constraintId="constraint:cards-cd:no-overlap",
        **common,
    )
    context = _context(first)
    context["geometryIssues"] = [first, second]
    context["maxGeometryCandidates"] = 2

    result = generate_geometry_candidates(context)

    assert len(result.candidates) == 2
    assert {candidate.semantic_id for candidate in result.candidates} == {
        "sem:card-a",
        "sem:card-c",
    }


def test_cascade_preserves_exact_detector_identity_in_each_geometry_plan():
    issue = _identity(
        currentBounds={"x": 0.65, "y": 0.65, "width": 0.3, "height": 0.3},
        kind="containment",
        code="constraint.containment.violated",
        containerBounds={"x": 0.1, "y": 0.1, "width": 0.6, "height": 0.6},
        mutationEnvelope={
            "allowedOperations": ["translateWithinParent"],
            "maxTranslation": 0.3,
        },
    )

    report = build_deterministic_cascade_report(_context(issue))

    assert len(report.plans) == 1
    plan = report.plans[0]
    assert plan.diagnostic_refs == (
        "issue-group:constraint:cards:no-overlap:constraint.containment.violated:sem:card-a:card-a",
        "code:constraint.containment.violated",
        "semantic:sem:card-a",
        "qid:card-a",
        "constraint:constraint:cards:no-overlap",
        "target:lay:card-a",
        "stage:dom",
        "metric:overlap_area_ratio",
    )
    assert plan.operations[0].op == "translateWithinParent"
    assert plan.operations[0].value.x == -0.25
    assert plan.operations[0].value.y == -0.25


def test_unconfirmed_continuous_occlusion_never_creates_a_movement_candidate():
    issue = _identity(
        currentBounds={"x": 0.2, "y": 0.2, "width": 0.3, "height": 0.3},
        otherTarget="lay:card-b",
        otherBounds={"x": 0.4, "y": 0.2, "width": 0.3, "height": 0.3},
        detectorEvidence={
            "confirmed": False,
            "stage": "dom",
            "metric": "continuous_occlusion_ratio",
            "value": 0.34,
            "threshold": 0.2,
        },
    )

    report = build_deterministic_cascade_report(_context(issue))

    assert report.plans == ()
    assert report.rejections == ()
    assert len(report.geometry_rejections) == 1
    rejection = asdict(report.geometry_rejections[0])
    assert rejection == {
        "issue_code": "dom.unintended_overlap",
        "element": {
            "semantic_id": "sem:card-a",
            "qid": "card-a",
            "layout_id": "lay:card-a",
        },
        "constraint_id": "constraint:cards:no-overlap",
        "kind": "nonOverlap",
        "operation": "candidate_generation",
        "reason_code": "repair.geometry.detector_evidence_unconfirmed",
        "stage": "candidate_generation",
        "metric": "continuous_occlusion_ratio",
        "detector_evidence": issue["detectorEvidence"],
    }


def test_nested_card_label_occlusion_is_not_treated_as_non_overlap():
    issue = _identity(
        currentBounds={"x": 0.2, "y": 0.2, "width": 0.3, "height": 0.3},
        otherTarget="lay:label",
        otherBounds={"x": 0.22, "y": 0.22, "width": 0.1, "height": 0.05},
        detectorEvidence={
            "confirmed": True,
            "independentElements": False,
            "stage": "dom",
            "metric": "continuous_occlusion_ratio",
            "value": 0.25,
            "threshold": 0.12,
        },
    )

    result = generate_geometry_candidates(_context(issue))

    assert result.candidates == ()
    assert result.rejections[0].reason_code == (
        "repair.geometry.non_overlap_independence_unproven"
    )
    assert result.rejections[0].element["qid"] == "card-a"


def test_confirmed_flag_cannot_override_geometry_that_has_no_target_issue():
    issue = _identity(
        currentBounds={"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2},
        otherTarget="lay:card-b",
        otherBounds={"x": 0.6, "y": 0.6, "width": 0.2, "height": 0.2},
        clearance=0.02,
    )

    result = generate_geometry_candidates(_context(issue))

    assert result.candidates == ()
    assert result.rejections[0].reason_code == (
        "repair.geometry.baseline_issue_not_present"
    )


def test_overflow_generates_resize_and_text_shrink_as_separate_candidates():
    issue = _identity(
        constraintId=None,
        kind="overflow",
        code="dom.overflow",
        currentBounds={"x": 0.1, "y": 0.1, "width": 0.3, "height": 0.1},
        requiredBounds={"x": 0.1, "y": 0.1, "width": 0.4, "height": 0.15},
        fontScale={"current": 1.0, "required": 0.95, "minimum": 0.85},
        mutationEnvelope={
            "allowedOperations": ["setBounds", "setFontScale"],
            "maxResizeDelta": 0.2,
            "maxFontShrink": 0.15,
        },
    )

    result = generate_geometry_candidates(_context(issue))

    assert [candidate.operations[0]["op"] for candidate in result.candidates] == [
        "setFontScale",
        "setBounds",
    ]


def test_imported_flow_overflow_rejects_resize_without_isolation_proof():
    issue = _identity(
        constraintId=None,
        kind="overflow",
        code="dom.overflow",
        currentBounds={"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.3},
        requiredBounds={"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.4},
        requiresEnvelope=True,
        mutationEnvelope={
            "allowedOperations": ["translateWithinParent", "setPadding"],
            "lockSize": True,
            "maxTranslation": 0.08,
        },
        detectorEvidence={
            "confirmed": True,
            "stage": "dom",
            "metric": "overflow_fraction",
            "value": 0.055,
            "threshold": 0.0,
        },
    )

    context = _context(issue)
    context["containerBounds"] = [
        {
            "target": "lay:card-a",
            "currentBounds": issue["currentBounds"],
            "bounds": issue["requiredBounds"],
        }
    ]

    report = build_deterministic_cascade_report(context)

    assert report.plans == ()
    assert report.rejections == ()
    assert [asdict(item) for item in report.geometry_rejections] == [
        {
            "issue_code": "dom.overflow",
            "element": {
                "semantic_id": "sem:card-a",
                "qid": "card-a",
                "layout_id": "lay:card-a",
            },
            "constraint_id": None,
            "kind": "overflow",
            "operation": "candidate_generation",
            "reason_code": "repair.mutation_disallowed.resize_flow_unproven",
            "stage": "candidate_generation",
            "metric": "overflow_fraction",
            "detector_evidence": issue["detectorEvidence"],
        }
    ]


@pytest.mark.parametrize(
    ("kind", "issue_values", "expected"),
    (
        ("gap", {"axis": "horizontal", "minimum": 0.1}, {"x": 0.05, "y": 0.0}),
        (
            "order",
            {"orderBy": "x", "minimumSeparation": 0.1},
            {"x": 0.05, "y": 0.0},
        ),
        (
            "alignment",
            {"edge": "left"},
            {"x": -0.25, "y": 0.0},
        ),
        (
            "anchor",
            {"targetEdge": "top", "containerEdge": "top", "offset": 0.05},
            {"x": 0.0, "y": -0.05},
        ),
    ),
)
def test_relational_kinds_generate_the_exact_minimum_translation(
    kind, issue_values, expected
):
    issue = _identity(
        kind=kind,
        code=f"constraint.{kind}.violated",
        currentBounds={"x": 0.35, "y": 0.2, "width": 0.2, "height": 0.2},
        otherTarget="lay:reference",
        otherBounds={"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2},
        mutationEnvelope={
            "allowedOperations": ["translateWithinParent"],
            "maxTranslation": 0.4,
        },
        otherMutationEnvelope={"locked": True},
        **issue_values,
    )

    result = generate_geometry_candidates(_context(issue))

    assert result.rejections == ()
    assert len(result.candidates) == 1
    assert result.candidates[0].operations[0]["value"] == expected


def test_repair_cost_measures_normalized_translation_and_bounds_payloads():
    translated = {
        "planId": "translate",
        "operations": [{
            "op": "translateWithinParent",
            "target": "layout:a",
            "value": {"x": 0.03, "y": -0.02},
            "precondition": {
                "expectedBounds": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2}
            },
        }],
    }
    resized = {
        "planId": "resize",
        "operations": [{
            "op": "setBounds",
            "target": "layout:a",
            "bounds": {"x": 0.1, "y": 0.1, "width": 0.25, "height": 0.22},
            "precondition": {
                "expectedBounds": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2}
            },
        }],
    }

    assert repair_cost(translated).normalized_geometry_delta == 0.05
    assert repair_cost(resized).normalized_geometry_delta == 0.07


@pytest.mark.parametrize(
    ("allowed_operation", "expected_operation"),
    (
        ("translateWithinParent", "translateWithinParent"),
        ("setBounds", "setBounds"),
    ),
)
def test_high_precision_expected_bounds_survive_plan_validation_and_apply(
    allowed_operation, expected_operation
):
    exact = {
        "x": 0.050000000000123,
        "y": 0.200000000000456,
        "width": 0.24722900390625,
        "height": 0.200000000000789,
    }
    other = {
        "x": 0.200000000000321,
        "y": 0.200000000000654,
        "width": 0.25,
        "height": 0.2,
    }
    draft = SlideSpec(
        schemaUri=SLIDESPEC_SCHEMA,
        specRevision=1,
        slideId="slide-precision",
        content=(
            ContentNode(
                semanticId="sem:card-a", kind="text", text="A", readingOrder=0
            ),
            ContentNode(
                semanticId="sem:card-b", kind="text", text="B", readingOrder=1
            ),
        ),
        layout=LayoutNode(
            layoutId="lay:root",
            kind="canvas",
            children=(
                LayoutNode(
                    layoutId="lay:card-a",
                    kind="content",
                    contentSemanticId="sem:card-a",
                    bounds=Bounds(**exact),
                    constraints=LayoutConstraints(
                        allowedOperations=(allowed_operation,),
                        maxTranslation=0.5,
                    ),
                ),
                LayoutNode(
                    layoutId="lay:card-b",
                    kind="content",
                    contentSemanticId="sem:card-b",
                    bounds=Bounds(**other),
                ),
            ),
        ),
        provenance=Provenance(source="native"),
        digests=SlideDigests(
            content=ZERO_DIGEST,
            hierarchy=ZERO_DIGEST,
            importantTokens=ZERO_DIGEST,
            spec=ZERO_DIGEST,
        ),
    )
    spec = seal_slidespec(draft)
    issue = _identity(
        currentBounds=exact,
        otherTarget="lay:card-b",
        otherBounds=other,
        mutationEnvelope={
            "allowedOperations": [allowed_operation],
            "maxTranslation": 0.5,
        },
        otherMutationEnvelope={"locked": True},
    )
    context = _context(issue)
    context.update(
        {
            "slideId": spec.slideId,
            "baseSpecRevision": spec.specRevision,
            "baseSpecHash": spec.digests.spec,
            "expectedContentDigest": spec.digests.content,
        }
    )

    report = build_deterministic_cascade_report(context)

    assert report.plans
    for plan in report.plans:
        operation = plan.operations[0]
        assert operation.op == expected_operation
        assert operation.precondition is not None
        assert operation.precondition.expectedBounds is not None
        assert operation.precondition.expectedBounds.width == exact["width"]
        applied = apply_repair_plan(spec, plan)
        assert applied.spec.specRevision == spec.specRevision + 1


def _ancestor_issue(group_id="issue-overlap"):
    ancestor_bounds = {
        "x": 0.100000000000123,
        "y": 0.300000000000456,
        "width": 0.74722900390625,
        "height": 0.300000000000789,
    }
    return _identity(
        issueGroupId=group_id,
        currentBounds={"x": 0.2, "y": 0.2, "width": 0.3, "height": 0.3},
        otherTarget="lay:occluder",
        otherBounds={"x": 0.4, "y": 0.2, "width": 0.3, "height": 0.3},
        mutationEnvelope={"locked": True},
        boundAncestors=[
            {
                "qid": "owner-title-wrap",
                "semanticId": "sem:owner-title-wrap",
                "layoutId": "lay:owner-title-wrap",
                "bounds": ancestor_bounds,
                "depth": 1,
                "sameCoordinateSpace": True,
                "ownershipProven": True,
                "mutationEnvelope": {
                    "allowedOperations": ["translateWithinParent"],
                    "maxTranslation": 0.2,
                },
                "browserProof": {
                    "source": "chrome",
                    "telemetryComplete": True,
                    "targetQid": "card-a",
                },
                "candidateDeltas": [
                    {
                        "x": 0.0,
                        "y": -0.1,
                        "source": "chrome",
                        "telemetryComplete": True,
                        "resolvesIssue": True,
                        "deltaPx": {"x": 0.0, "y": -33.0},
                    }
                ],
            }
        ],
    )


def test_browser_proven_ancestor_promotion_preserves_owner_precision_and_target_qid():
    context = _context(_ancestor_issue())

    first = build_deterministic_cascade_report(context)
    second = build_deterministic_cascade_report(context)

    assert first.plans == second.plans
    promoted = [
        plan
        for plan in first.plans
        if plan.operations[0].target == "lay:owner-title-wrap"
    ]
    assert len(promoted) == 1
    operation = promoted[0].operations[0]
    assert operation.precondition is not None
    assert operation.precondition.expectedBounds is not None
    assert operation.precondition.expectedBounds.width == 0.74722900390625
    assert operation.value.y == -0.1
    assert "qid:card-a" in promoted[0].diagnostic_refs
    assert "owner-qid:owner-title-wrap" in promoted[0].diagnostic_refs


def test_ancestor_promotion_fails_closed_without_chrome_ownership_proof():
    issue = _ancestor_issue()
    issue["boundAncestors"][0]["ownershipProven"] = False

    result = generate_geometry_candidates(_context(issue))

    assert result.candidates == ()
    assert {
        rejection.reason_code for rejection in result.rejections
    } >= {"repair.geometry.ancestor.ownership_unproven"}


def test_peer_ancestor_promotion_uses_exact_occluder_binding_and_bounds():
    issue = _ancestor_issue()
    issue["boundAncestors"] = []
    issue["detectorEvidence"]["occluderQid"] = "cover"
    issue["otherBoundAncestors"] = [
        {
            "qid": "peer-section",
            "semanticId": "sem:peer-section",
            "layoutId": "lay:peer-section",
            "bounds": {
                "x": 0.100000000000321,
                "y": 0.600000000000654,
                "width": 0.7,
                "height": 0.2,
            },
            "depth": 2,
            "sameCoordinateSpace": True,
            "ownershipProven": True,
            "mutationEnvelope": {
                "allowedOperations": ["translateWithinParent"],
                "maxTranslation": 0.08,
            },
            "browserProof": {
                "source": "chrome",
                "telemetryComplete": True,
                "targetQid": "cover",
            },
            "candidateDeltas": [
                {
                    "x": 0.0,
                    "y": 0.02,
                    "source": "chrome",
                    "telemetryComplete": True,
                    "resolvesIssue": True,
                    "deltaPx": {"x": 0.0, "y": 14.4},
                }
            ],
        }
    ]

    result = generate_geometry_candidates(_context(issue))

    assert len(result.candidates) == 1
    operation = result.candidates[0].operations[0]
    assert operation["target"] == "lay:peer-section"
    assert operation["value"] == {"x": 0.0, "y": 0.02}
    assert operation["precondition"]["expectedBounds"]["x"] == 0.100000000000321
    assert "owner-role:otherBoundAncestors" in result.candidates[0].diagnostic_refs


def test_coordinated_only_owner_inventory_never_emits_an_atomic_move():
    issue = _ancestor_issue()
    owner = issue["boundAncestors"][0]
    owner["coordinatedOwnerOnly"] = True
    owner["candidateDeltas"] = []
    owner["browserProof"]["parentQidChain"] = ["card-a", owner["qid"]]

    result = generate_geometry_candidates(_context(issue))

    assert result.candidates == ()
    assert not any(
        rejection.reason_code == "repair.geometry.ancestor.delta_unproven"
        for rejection in result.rejections
    )


def test_invalid_pair_does_not_suppress_independently_resolving_owner_move():
    issue = _coordinated_issue()
    issue["boundAncestors"] = _ancestor_issue()["boundAncestors"]
    issue["coordinatedAncestorMoves"][0]["proof"]["pairResolvesIssue"] = False

    result = generate_geometry_candidates(_context(issue))

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.kind == "nonOverlap"
    assert [operation["target"] for operation in candidate.operations] == [
        "lay:owner-title-wrap"
    ]
    assert not any(
        operation["target"] in {"lay:card-a", "lay:occluder"}
        for operation in candidate.operations
    )


def test_invalid_pair_and_partial_owner_remain_fail_closed():
    issue = _coordinated_issue()
    issue["coordinatedAncestorMoves"][0]["proof"]["pairResolvesIssue"] = False

    result = generate_geometry_candidates(_context(issue))

    assert result.candidates == ()


def test_single_group_pair_keeps_proved_single_owner_as_first_alternative():
    issue = _coordinated_issue()
    issue["coordinatedAncestorMoves"][0]["proof"]["coveredIssueGroupIds"] = [
        "issue-coordinated"
    ]
    issue["boundAncestors"] = _ancestor_issue()["boundAncestors"]

    result = generate_geometry_candidates(_context(issue))

    assert len(result.candidates) == 2
    assert result.candidates[0].operations[0]["target"] == "lay:owner-title-wrap"
    assert result.candidates[1].kind == "coordinatedAncestorMoves"


def test_multi_group_pair_remains_first_when_partial_owner_is_also_available():
    issue = _coordinated_issue()
    issue["boundAncestors"] = _ancestor_issue()["boundAncestors"]

    result = generate_geometry_candidates(_context(issue))

    assert len(result.candidates) == 2
    assert result.candidates[0].kind == "coordinatedAncestorMoves"
    assert result.candidates[1].operations[0]["target"] == "lay:owner-title-wrap"


def _coordinated_issue():
    issue = _ancestor_issue("issue-coordinated")
    issue["detectorEvidence"]["occluderQid"] = "execution-paint"
    issue["boundAncestors"] = [
        {
            "qid": "partial-owner",
            "semanticId": "sem:partial-owner",
            "layoutId": "lay:partial-owner",
            "bounds": {"x": 0.2, "y": 0.25, "width": 0.4, "height": 0.5},
            "sameCoordinateSpace": True,
            "ownershipProven": True,
            "mutationEnvelope": {
                "allowedOperations": ["translateWithinParent"],
                "maxTranslation": 0.08,
            },
            "browserProof": {
                "source": "chrome",
                "telemetryComplete": True,
                "targetQid": "card-a",
            },
            "candidateDeltas": [
                {
                    "x": 0,
                    "y": -0.06,
                    "source": "chrome",
                    "telemetryComplete": True,
                    "resolvesIssue": False,
                    "deltaPx": {"x": 0, "y": -43.2},
                }
            ],
        }
    ]
    issue["coordinatedAncestorMoves"] = [
        {
            "moves": [
                {
                    "qid": "center-owner",
                    "semanticId": "sem:center-owner",
                    "layoutId": "lay:center-owner",
                    "bounds": {
                        "x": 0.300000000000123,
                        "y": 0.250000000000456,
                        "width": 0.4,
                        "height": 0.5,
                    },
                    "mutationEnvelope": {
                        "allowedOperations": ["translateWithinParent"],
                        "maxTranslation": 0.08,
                    },
                    "delta": {"x": 0, "y": -0.06},
                    "deltaPx": {"x": 0, "y": -43.2},
                },
                {
                    "qid": "execution-owner",
                    "semanticId": "sem:execution-owner",
                    "layoutId": "lay:execution-owner",
                    "bounds": {
                        "x": 0.100000000000321,
                        "y": 0.650000000000654,
                        "width": 0.8,
                        "height": 0.1,
                    },
                    "mutationEnvelope": {
                        "allowedOperations": ["translateWithinParent"],
                        "maxTranslation": 0.08,
                    },
                    "delta": {"x": 0, "y": 0.04},
                    "deltaPx": {"x": 0, "y": 28.8},
                },
            ],
            "proof": {
                "axis": "y",
                "direction": -1,
                "separationPx": 72,
                "capacityPx": {"target": 43.2, "peer": 28.8},
                "source": "chrome",
                "telemetryComplete": True,
                "pairResolvesIssue": True,
                "targetQid": "center-owner",
                "peerQid": "execution-owner",
                "ownerCoverage": {
                    "target": {
                        "ownerQid": "center-owner",
                        "ownedQids": ["card-a"],
                        "ancestorQidChains": {
                            "card-a": ["card-a", "center-owner"]
                        },
                    },
                    "peer": {
                        "ownerQid": "execution-owner",
                        "ownedQids": ["execution-paint"],
                        "ancestorQidChains": {
                            "execution-paint": [
                                "execution-paint",
                                "execution-owner",
                            ]
                        },
                    },
                    "blockedPaintOwnerQids": [],
                    "connectedIssueQids": ["card-a", "execution-paint"],
                    "unownedConnectedIssueQids": [],
                },
                "coversBlockedPaint": True,
                "coveredIssueGroupIds": ["issue-coordinated", "issue-peer"],
                "ownerDepth": {"target": 1, "peer": 1},
                "capacityEvidence": {
                    "target": {
                        "ownerQid": "center-owner",
                        "axis": "y",
                        "direction": -1,
                        "boundary": {
                            "source": "chrome",
                            "kind": "canvas",
                            "qid": None,
                            "boundsPx": {
                                "x": 0,
                                "y": 0,
                                "width": 1280,
                                "height": 720,
                            },
                            "overflowX": "visible",
                            "overflowY": "visible",
                        },
                        "subtreeContentBoundsPx": {
                            "x": 384,
                            "y": 43.2,
                            "width": 512,
                            "height": 500,
                        },
                        "contentQids": ["center-copy"],
                        "contentKinds": {"center-copy": "owned-text"},
                        "envelopeMaxTranslationPx": 57.6,
                        "capacityPx": 43.2,
                    },
                    "peer": {
                        "ownerQid": "execution-owner",
                        "axis": "y",
                        "direction": 1,
                        "boundary": {
                            "source": "chrome",
                            "kind": "canvas",
                            "qid": None,
                            "boundsPx": {
                                "x": 0,
                                "y": 0,
                                "width": 1280,
                                "height": 720,
                            },
                            "overflowX": "visible",
                            "overflowY": "visible",
                        },
                        "subtreeContentBoundsPx": {
                            "x": 128,
                            "y": 500,
                            "width": 1024,
                            "height": 191.2,
                        },
                        "contentQids": ["execution-copy"],
                        "contentKinds": {"execution-copy": "owned-text"},
                        "envelopeMaxTranslationPx": 57.6,
                        "capacityPx": 28.8,
                    },
                },
            },
        }
    ]
    return issue


def _glyph_composition_context():
    issue = _coordinated_issue()
    issue["mutationEnvelope"] = {
        "allowedOperations": ["translateWithinParent"],
        "maxTranslation": 0.08,
    }
    issue["detectorEvidence"].update(
        targetBoundsPx={"x": 58, "y": 144.625, "width": 561, "height": 22.5}
    )
    owner = issue["boundAncestors"][0]
    owner["browserProof"].update(
        ancestorQid="partial-owner",
        parentQidChain=["card-a", "partial-owner"],
    )
    glyph = {
        "target": "lay:title",
        "layoutId": "lay:title",
        "semanticId": "sem:title",
        "qid": "title",
        "currentScale": 1,
        "minimumScale": 0.9,
        "candidateScale": 0.975,
        "mutationEnvelope": {
            "allowedOperations": ["setFontScale"],
            "minFontScale": 0.9,
            "maxFontShrink": 0.1,
        },
        "precondition": {
            "expectedBounds": {"x": 0.1, "y": 0.1, "width": 0.4, "height": 0.1},
            "expectedScale": 1,
        },
        "measurement": {
            "source": "chrome",
            "telemetryComplete": True,
            "fontsReady": True,
            "kind": "range-bounds",
            "fontSizePx": 31,
            "measuredWidthPx": 459.359375,
            "measuredHeightPx": 82.8125,
            "availableWidthPx": 561,
            "availableHeightPx": 75.625,
            "textBoundsPx": {
                "x": 58,
                "y": 57,
                "width": 459.359375,
                "height": 82.8125,
            },
            "lineBoundsPx": [
                {"x": 58, "y": 57, "width": 459.359375, "height": 45},
                {"x": 58, "y": 102, "width": 441, "height": 37.8125},
            ],
        },
        "roundTrip": {"supported": True, "verifiedProperties": ["font-size"]},
    }
    context = _context(issue)
    context["requiredGeometryIssueGroups"] = ["issue-coordinated"]
    context["geometryGlyphCompositions"] = [
        {
            "compositionId": "composition:glyph:test",
            "issueGroupIds": ["issue-coordinated"],
            "required": True,
            "moves": [
                {
                    "role": "owner",
                    "target": "lay:partial-owner",
                    "layoutId": "lay:partial-owner",
                    "semanticId": "sem:partial-owner",
                    "qid": "partial-owner",
                    "currentBounds": deepcopy(owner["bounds"]),
                    "mutationEnvelope": deepcopy(owner["mutationEnvelope"]),
                    "precondition": {"expectedBounds": deepcopy(owner["bounds"])},
                    "delta": {"x": 0, "y": -0.025},
                    "deltaPx": {"x": 0, "y": -18},
                    "staticTranslationSafe": True,
                    "roundTrip": {
                        "supported": True,
                        "verifiedProperties": ["bounds"],
                    },
                },
                {
                    "role": "subtitle",
                    "target": "lay:card-a",
                    "layoutId": "lay:card-a",
                    "semanticId": "sem:card-a",
                    "qid": "card-a",
                    "currentBounds": deepcopy(issue["currentBounds"]),
                    "mutationEnvelope": deepcopy(issue["mutationEnvelope"]),
                    "precondition": {
                        "expectedBounds": deepcopy(issue["currentBounds"])
                    },
                    "delta": {"x": 0, "y": -8 / 720},
                    "deltaPx": {"x": 0, "y": -8},
                    "staticTranslationSafe": True,
                    "roundTrip": {
                        "supported": True,
                        "verifiedProperties": ["bounds"],
                    },
                },
            ],
            "proof": {
                "source": "chrome",
                "telemetryComplete": True,
                "candidateResolvesIssue": True,
                "baselineSubtitleTextBoundsPx": {
                    "x": 58,
                    "y": 144.625,
                    "width": 561,
                    "height": 21,
                },
                "candidateSubtitleTextBoundsPx": {
                    "x": 58,
                    "y": 118.625,
                    "width": 561,
                    "height": 21,
                },
                "occluderTextBoundsPx": {
                    "x": 76,
                    "y": 145,
                    "width": 98,
                    "height": 20,
                },
                "occluderPaintBoundaryPx": {"axis": "y", "value": 145},
                "ownerSubtreeBoundsPx": {
                    "baseline": {"x": 58, "y": 33, "width": 561, "height": 100},
                    "candidate": {"x": 58, "y": 15, "width": 561, "height": 100},
                },
                "canvasCapacity": {
                    "viewportHeightPx": 720,
                    "topClearancePx": 15,
                    "bottomClearancePx": 605,
                },
                "staticTranslationSafe": True,
                "roundTrip": {
                    "supported": True,
                    "verifiedProperties": ["bounds", "font-size"],
                },
                "requiredPostconditions": {
                    "domNoNewIssue": True,
                    "hardFailure": False,
                    "softCostMaximum": 0,
                    "unknownHard": [],
                    "roundTripErrors": [],
                },
            },
            "glyph": glyph,
        }
    ]
    return context


def test_geometry_glyph_composition_emits_exact_three_op_candidate_first():
    context = _glyph_composition_context()

    first = generate_geometry_candidates(context)
    second = generate_geometry_candidates(context)

    assert first == second
    candidate = first.candidates[0]
    assert candidate.kind == "geometryGlyphComposition"
    assert [(operation["op"], operation["target"]) for operation in candidate.operations] == [
        ("setFontScale", "lay:title"),
        ("translateWithinParent", "lay:partial-owner"),
        ("translateWithinParent", "lay:card-a"),
    ]
    assert candidate.operations[0]["scale"] == 0.975
    assert candidate.operations[1]["value"] == {"x": 0.0, "y": -0.025}
    assert candidate.operations[2]["value"] == {
        "x": 0.0,
        "y": -0.011111111,
    }
    assert "geometry-glyph-composition:true" in candidate.diagnostic_refs


def test_geometry_glyph_composition_fails_closed_without_postconditions():
    context = _glyph_composition_context()
    del context["geometryGlyphCompositions"][0]["proof"]["requiredPostconditions"]

    result = generate_geometry_candidates(context)

    assert not any(
        candidate.kind == "geometryGlyphComposition"
        for candidate in result.candidates
    )
    assert "repair.geometry.glyph_composition.chrome_proof_invalid" in {
        rejection.reason_code for rejection in result.rejections
    }


def _exact_composition_context():
    envelope = {
        "allowedOperations": ["translateWithinParent"],
        "maxTranslation": 0.08,
    }
    issue = _identity(
        issueGroupId="issue-exact",
        currentBounds={"x": 0.2, "y": 0.2, "width": 0.3, "height": 0.1},
        otherTarget="lay:card-b",
        otherBounds={"x": 0.2, "y": 0.28, "width": 0.3, "height": 0.1},
        mutationEnvelope=deepcopy(envelope),
        otherMutationEnvelope=deepcopy(envelope),
    )
    context = _context(issue)
    context["requiredGeometryIssueGroups"] = ["issue-exact"]
    context["geometryExactCompositions"] = [
        {
            "compositionId": "composition:exact:test",
            "issueGroupIds": ["issue-exact"],
            "required": True,
            "diagnosticRefs": [
                "issue-group:issue-exact",
                "qid:card-a",
                "target:lay:card-a",
            ],
            "operations": [
                {
                    "op": "translateWithinParent",
                    "role": "first",
                    "target": "lay:card-a",
                    "layoutId": "lay:card-a",
                    "semanticId": "sem:card-a",
                    "qid": "card-a",
                    "currentBounds": deepcopy(issue["currentBounds"]),
                    "precondition": {
                        "expectedBounds": deepcopy(issue["currentBounds"])
                    },
                    "value": {"x": 0, "y": -14 / 720},
                    "valuePx": {"x": 0, "y": -14},
                    "mutationEnvelope": deepcopy(envelope),
                    "projectionProof": {
                        "source": "chrome",
                        "telemetryComplete": True,
                        "mechanism": "static-transform",
                        "staticTranslationSafe": True,
                    },
                    "roundTrip": {
                        "supported": True,
                        "verifiedProperties": ["bounds"],
                    },
                },
                {
                    "op": "translateWithinParent",
                    "role": "second",
                    "target": "lay:card-b",
                    "layoutId": "lay:card-b",
                    "semanticId": "sem:card-b",
                    "qid": "card-b",
                    "currentBounds": deepcopy(issue["otherBounds"]),
                    "precondition": {
                        "expectedBounds": deepcopy(issue["otherBounds"])
                    },
                    "value": {"x": 0, "y": 14 / 720},
                    "valuePx": {"x": 0, "y": 14},
                    "mutationEnvelope": deepcopy(envelope),
                    "projectionProof": {
                        "source": "chrome",
                        "telemetryComplete": True,
                        "mechanism": "position-anchor",
                        "position": "absolute",
                        "verticalAnchor": "top",
                    },
                    "roundTrip": {
                        "supported": True,
                        "verifiedProperties": ["bounds"],
                    },
                },
            ],
            "proof": {
                "source": "chrome",
                "telemetryComplete": True,
                "candidateResolvesIssue": True,
                "baselineBoundsPx": {
                    "card-a": {"x": 100, "y": 100, "width": 200, "height": 50},
                    "card-b": {"x": 100, "y": 140, "width": 200, "height": 50},
                },
                "candidateBoundsPx": {
                    "card-a": {"x": 100, "y": 86, "width": 200, "height": 50},
                    "card-b": {"x": 100, "y": 154, "width": 200, "height": 50},
                },
                "relationships": [
                    {
                        "kind": "nonOverlap",
                        "axis": "y",
                        "firstQid": "card-a",
                        "secondQid": "card-b",
                        "baselineGapPx": -10,
                        "candidateGapPx": 18,
                    }
                ],
                "requiredPostconditions": {
                    "domNoNewIssue": True,
                    "hardFailure": False,
                    "softCostMaximum": 0,
                    "unknownHard": [],
                    "roundTripErrors": [],
                },
            },
        }
    ]
    return context


def test_exact_geometry_composition_emits_producer_operations_first():
    context = _exact_composition_context()

    first = generate_geometry_candidates(context)
    second = generate_geometry_candidates(context)

    assert first == second
    candidate = first.candidates[0]
    assert candidate.kind == "geometryExactComposition"
    assert [operation["target"] for operation in candidate.operations] == [
        "lay:card-a",
        "lay:card-b",
    ]
    assert [operation["value"] for operation in candidate.operations] == [
        {"x": 0.0, "y": -0.019444444},
        {"x": 0.0, "y": 0.019444444},
    ]
    assert "geometry-exact-composition:true" in candidate.diagnostic_refs


def test_exact_geometry_composition_fails_closed_on_bbox_delta_mismatch():
    context = _exact_composition_context()
    context["geometryExactCompositions"][0]["proof"]["candidateBoundsPx"][
        "card-a"
    ]["y"] = 87

    result = generate_geometry_candidates(context)

    assert not any(
        candidate.kind == "geometryExactComposition"
        for candidate in result.candidates
    )
    assert "repair.geometry.exact_composition.operation_proof_invalid" in {
        rejection.reason_code for rejection in result.rejections
    }


def test_exact_geometry_composition_accepts_chrome_proved_positioned_set_bounds():
    context = _exact_composition_context()
    item = context["geometryExactCompositions"][0]
    operation = item["operations"][1]
    operation.update(
        {
            "op": "setBounds",
            "bounds": {
                "x": 0.2,
                "y": 0.3,
                "width": 0.3,
                "height": 0.08,
            },
            "boundsPx": {
                "x": 100,
                "y": 154.4,
                "width": 200,
                "height": 35.6,
            },
            "mutationEnvelope": {
                "allowedOperations": ["setBounds"],
                "lockSize": False,
                "maxTranslation": 0.08,
                "maxResizeDelta": 0.25,
            },
        }
    )
    operation.pop("value")
    operation.pop("valuePx")
    item["proof"]["candidateBoundsPx"]["card-b"] = {
        "x": 100,
        "y": 154.4,
        "width": 200,
        "height": 35.6,
    }
    item["proof"]["relationships"][0]["candidateGapPx"] = 18.4

    result = generate_geometry_candidates(context)

    candidate = next(
        candidate
        for candidate in result.candidates
        if candidate.kind == "geometryExactComposition"
    )
    assert candidate.operations[1]["op"] == "setBounds"
    assert candidate.operations[1]["bounds"]["height"] == 0.08


def test_exact_geometry_composition_uses_required_group_intersection():
    context = _exact_composition_context()
    unrelated_issue = deepcopy(context["geometryIssues"][0])
    unrelated_issue["issueGroupId"] = "issue-associated"
    context["geometryIssues"].append(unrelated_issue)
    composition = context["geometryExactCompositions"][0]
    composition["issueGroupIds"].append("issue-associated")
    composition["diagnosticRefs"].append("issue-group:issue-associated")

    result = generate_geometry_candidates(context)

    assert any(
        candidate.kind == "geometryExactComposition"
        for candidate in result.candidates
    )


def test_exact_geometry_composition_skips_non_required_item():
    context = _exact_composition_context()
    context["geometryExactCompositions"][0]["required"] = False

    result = generate_geometry_candidates(context)

    assert not any(
        candidate.kind == "geometryExactComposition"
        for candidate in result.candidates
    )


def test_coordinated_ancestor_pair_is_atomic_exact_and_deterministic():
    issue = _coordinated_issue()
    context = _context(issue)
    first = generate_geometry_candidates(context)
    second = generate_geometry_candidates(context)

    assert first == second
    assert len(first.candidates) == 1
    candidate = first.candidates[0]
    assert candidate.kind == "coordinatedAncestorMoves"
    assert [(item["op"], item["target"]) for item in candidate.operations] == [
        ("translateWithinParent", "lay:center-owner"),
        ("translateWithinParent", "lay:execution-owner"),
    ]
    assert [item["value"] for item in candidate.operations] == [
        {"x": 0.0, "y": -0.06},
        {"x": 0.0, "y": 0.04},
    ]
    assert (
        candidate.operations[0]["precondition"]["expectedBounds"]["x"]
        == 0.300000000000123
    )
    assert "coordinated-ancestor:true" in candidate.diagnostic_refs
    assert "issue-group:issue-peer" in candidate.diagnostic_refs
    assert not any(
        operation["target"] == "lay:partial-owner"
        for operation in candidate.operations
    )
    report = build_deterministic_cascade_report(context)
    coordinated_plans = [
        plan
        for plan in report.plans
        if "coordinated-ancestor:true" in plan.diagnostic_refs
    ]
    assert len(coordinated_plans) == 1
    assert len(coordinated_plans[0].operations) == 2


@pytest.mark.parametrize(
    ("mutate", "expected_reason"),
    (
        (
            lambda pair: pair["proof"].update(pairResolvesIssue=False),
            "repair.geometry.coordinated_ancestor.proof_invalid",
        ),
        (
            lambda pair: pair["proof"].update(targetQid="wrong-owner"),
            "repair.geometry.coordinated_ancestor.identity_mismatch",
        ),
        (
            lambda pair: pair["proof"].update(separationPx=72.1),
            "repair.geometry.coordinated_ancestor.split_invalid",
        ),
        (
            lambda pair: pair["proof"]["capacityPx"].update(target=43.1),
            "repair.geometry.coordinated_ancestor.capacity_unproven",
        ),
        (
            lambda pair: pair["moves"][1]["delta"].update(y=0.02),
            "repair.geometry.coordinated_ancestor.capacity_unproven",
        ),
        (
            lambda pair: pair["moves"][0]["mutationEnvelope"].update(locked=True),
            "repair.geometry.coordinated_ancestor.envelope_rejected",
        ),
        (
            lambda pair: pair["proof"]["ownerCoverage"]["peer"].update(
                ownedQids=["execution-leaf"],
                ancestorQidChains={
                    "execution-leaf": ["execution-leaf", "execution-owner"]
                },
            ),
            "repair.geometry.coordinated_ancestor.owner_coverage_unproven",
        ),
        (
            lambda pair: pair["proof"]["capacityEvidence"]["peer"].update(
                subtreeContentBoundsPx={
                    "x": 128,
                    "y": 500,
                    "width": 1024,
                    "height": 180,
                }
            ),
            "repair.geometry.coordinated_ancestor.capacity_unproven",
        ),
        (
            lambda pair: pair["proof"]["capacityEvidence"]["target"].update(
                contentKinds={"center-copy": "owner-bbox-fallback"}
            ),
            "repair.geometry.coordinated_ancestor.capacity_unproven",
        ),
        (
            lambda pair: pair["proof"]["ownerCoverage"].update(
                unownedConnectedIssueQids=["center-sibling-paint"]
            ),
            "repair.geometry.coordinated_ancestor.owner_coverage_unproven",
        ),
    ),
)
def test_coordinated_ancestor_pair_fails_closed_on_invalid_proof(
    mutate, expected_reason
):
    issue = _coordinated_issue()
    mutate(issue["coordinatedAncestorMoves"][0])

    result = generate_geometry_candidates(_context(issue))

    assert result.candidates == ()
    assert expected_reason in {
        rejection.reason_code for rejection in result.rejections
    }


def test_coordinated_ancestor_pair_respects_plan_limits():
    issue = _coordinated_issue()
    context = _context(issue)
    context["limits"] = {"maxOperations": 1, "maxAffectedBlocks": 2}

    result = generate_geometry_candidates(context)

    assert result.candidates == ()
    assert "repair.geometry.coordinated_ancestor.limits_exceeded" in {
        rejection.reason_code for rejection in result.rejections
    }


def test_coordinated_pair_selection_prioritizes_issue_coverage_before_depth():
    issue = _coordinated_issue()
    broad = issue["coordinatedAncestorMoves"][0]
    narrow = deepcopy(broad)
    narrow["proof"]["coveredIssueGroupIds"] = ["issue-coordinated"]
    narrow["proof"]["ownerDepth"] = {"target": 0, "peer": 0}
    issue["coordinatedAncestorMoves"] = [narrow, broad]

    result = generate_geometry_candidates(_context(issue))

    assert len(result.candidates) == 1
    assert "issue-group:issue-peer" in result.candidates[0].diagnostic_refs


def test_connected_coverage_allows_only_the_two_exact_owner_self_endpoints():
    issue = _coordinated_issue()
    coverage = issue["coordinatedAncestorMoves"][0]["proof"]["ownerCoverage"]
    coverage["connectedIssueQids"].extend(["center-owner", "execution-owner"])
    coverage["target"]["ownedQids"].append("center-owner")
    coverage["target"]["ancestorQidChains"]["center-owner"] = ["center-owner"]
    coverage["peer"]["ownedQids"].append("execution-owner")
    coverage["peer"]["ancestorQidChains"]["execution-owner"] = [
        "execution-owner"
    ]

    accepted = generate_geometry_candidates(_context(issue))

    assert any(
        candidate.kind == "coordinatedAncestorMoves"
        for candidate in accepted.candidates
    )
    coverage["connectedIssueQids"].append("forged-third-owner")
    coverage["target"]["ownedQids"].append("forged-third-owner")
    coverage["target"]["ancestorQidChains"]["forged-third-owner"] = [
        "forged-third-owner",
        "center-owner",
    ]

    rejected = generate_geometry_candidates(_context(issue))

    assert not any(
        candidate.kind == "coordinatedAncestorMoves"
        for candidate in rejected.candidates
    )


def test_aggregate_section_endpoints_are_mandatory_ownership_coverage():
    issue = _coordinated_issue()
    issue["boundAncestors"][0]["qid"] = "center-owner"
    issue["boundAncestors"][0]["browserProof"]["parentQidChain"] = [
        "card-a",
        "center-owner",
    ]
    issue["otherBoundAncestors"] = [
        {
            "qid": "execution-owner",
            "browserProof": {
                "targetQid": "execution-paint",
                "parentQidChain": ["execution-paint", "execution-owner"],
            },
        }
    ]
    aggregate = _identity(
        issueGroupId="issue-aggregate",
        semanticId="sem:center-owner",
        qid="center-owner",
        target="lay:center-owner",
        currentBounds={"x": 0.3, "y": 0.25, "width": 0.4, "height": 0.5},
        otherTarget="lay:execution-owner",
        otherBounds={"x": 0.1, "y": 0.65, "width": 0.8, "height": 0.1},
        detectorEvidence={
            "confirmed": True,
            "independentElements": True,
            "stage": "dom",
            "metric": "owned_content_occluded_fraction",
            "occluderQid": "execution-owner",
        },
        mutationEnvelope={"locked": True},
    )
    context = _context(issue)
    context["geometryIssues"] = [issue, aggregate]

    rejected = generate_geometry_candidates(context)

    assert not any(
        candidate.kind == "coordinatedAncestorMoves"
        for candidate in rejected.candidates
    )
    pair_coverage = issue["coordinatedAncestorMoves"][0]["proof"]["ownerCoverage"]
    pair_coverage["connectedIssueQids"] = [
        "card-a",
        "execution-paint",
        "center-owner",
        "execution-owner",
    ]
    pair_coverage["target"]["ownedQids"].append("center-owner")
    pair_coverage["target"]["ancestorQidChains"]["center-owner"] = [
        "center-owner"
    ]
    pair_coverage["peer"]["ownedQids"].append("execution-owner")
    pair_coverage["peer"]["ancestorQidChains"]["execution-owner"] = [
        "execution-owner"
    ]

    accepted = generate_geometry_candidates(context)

    assert any(
        candidate.kind == "coordinatedAncestorMoves"
        for candidate in accepted.candidates
    )


def test_explicit_cross_issue_group_composes_ancestor_move_and_font_repair():
    overlap = _ancestor_issue()
    overflow = _identity(
        issueGroupId="issue-font",
        constraintId=None,
        kind="overflow",
        code="dom.overflow",
        semanticId="sem:h3",
        qid="heading-h3",
        target="lay:h3",
        currentBounds={"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.2},
        fontScale={"current": 1.0, "required": 0.9, "minimum": 0.85},
        mutationEnvelope={
            "allowedOperations": ["setFontScale"],
            "maxFontShrink": 0.15,
        },
    )
    context = _context(overlap)
    context.update(
        {
            "geometryIssues": [overlap, overflow],
            "geometryCompositionGroups": {
                "global-clean": ["issue-overlap", "issue-font"]
            },
            "requiredGeometryIssueGroups": ["issue-overlap", "issue-font"],
            "limits": {"maxOperations": 2, "maxAffectedBlocks": 2},
        }
    )

    first = generate_geometry_candidates(context)
    second = generate_geometry_candidates(context)
    composed = [candidate for candidate in first.candidates if candidate.kind == "composition"]

    assert first == second
    assert len(composed) == 1
    assert [(operation["op"], operation["target"]) for operation in composed[0].operations] == [
        ("translateWithinParent", "lay:owner-title-wrap"),
        ("setFontScale", "lay:h3"),
    ]
    assert "qid:card-a" in composed[0].diagnostic_refs
    assert "qid:heading-h3" in composed[0].diagnostic_refs
    assert composed[0].diagnostic_refs[0] == "composition:global-clean"


def test_cross_issue_composition_respects_affected_block_limit():
    overlap = _ancestor_issue()
    overflow = _identity(
        issueGroupId="issue-font",
        constraintId=None,
        kind="overflow",
        code="dom.overflow",
        semanticId="sem:h3",
        qid="heading-h3",
        target="lay:h3",
        currentBounds={"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.2},
        fontScale={"current": 1.0, "required": 0.9, "minimum": 0.85},
        mutationEnvelope={"allowedOperations": ["setFontScale"]},
    )
    context = _context(overlap)
    context.update(
        {
            "geometryIssues": [overlap, overflow],
            "geometryCompositionGroups": {
                "too-broad": ["issue-overlap", "issue-font"]
            },
            "requiredGeometryIssueGroups": ["issue-overlap", "issue-font"],
            "limits": {"maxOperations": 2, "maxAffectedBlocks": 1},
        }
    )

    result = generate_geometry_candidates(context)

    assert not any(candidate.kind == "composition" for candidate in result.candidates)


def test_geometry_composition_fails_closed_without_required_issue_authorization():
    overlap = _ancestor_issue()
    overflow = _identity(
        issueGroupId="issue-font",
        constraintId=None,
        kind="overflow",
        code="dom.overflow",
        semanticId="sem:h3",
        qid="heading-h3",
        target="lay:h3",
        currentBounds={"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.2},
        fontScale={"current": 1.0, "required": 0.9, "minimum": 0.85},
        mutationEnvelope={"allowedOperations": ["setFontScale"]},
    )
    context = _context(overlap)
    context.update(
        {
            "geometryIssues": [overlap, overflow],
            "geometryCompositionGroups": {
                "unauthorized": ["issue-overlap", "issue-font"]
            },
        }
    )

    result = generate_geometry_candidates(context)

    assert not any(candidate.kind == "composition" for candidate in result.candidates)


def test_total_candidate_cap_reserves_required_clean_composition_first():
    overlap = _ancestor_issue()
    overflow = _identity(
        issueGroupId="issue-font",
        constraintId=None,
        kind="overflow",
        code="dom.overflow",
        semanticId="sem:h3",
        qid="heading-h3",
        target="lay:h3",
        currentBounds={"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.2},
        fontScale={"current": 1.0, "required": 0.9, "minimum": 0.85},
        mutationEnvelope={"allowedOperations": ["setFontScale"]},
    )
    context = _context(overlap)
    context.update(
        {
            "geometryIssues": [overlap, overflow],
            "geometryCompositionGroups": {
                "required-clean": ["issue-overlap", "issue-font"]
            },
            "requiredGeometryIssueGroups": ["issue-overlap", "issue-font"],
            "maxGeometryCandidates": 1,
            "limits": {"maxOperations": 2, "maxAffectedBlocks": 2},
        }
    )

    result = generate_geometry_candidates(context)

    assert len(result.candidates) == 1
    assert result.candidates[0].kind == "composition"
    assert result.candidates[0].diagnostic_refs[0] == "composition:required-clean"


def test_cross_issue_composition_joins_ancestor_move_with_existing_glyph_plan():
    overlap = _ancestor_issue()
    context = _context(overlap)
    context.update(
        {
            "glyphFit": {
                "target": "lay:h3",
                "currentScale": 1.0,
                "minimumScale": 0.9,
                "mutationEnvelope": {
                    "allowedOperations": ["setFontScale"],
                    "minFontScale": 0.9,
                    "maxFontShrink": 0.1,
                },
                "measurement": {
                    "source": "chrome",
                    "telemetryComplete": True,
                    "kind": "range-bounds",
                    "fontsReady": True,
                    "fontSizePx": 20,
                    "measuredWidthPx": 100,
                    "measuredHeightPx": 20,
                    "availableWidthPx": 90,
                    "availableHeightPx": 20,
                },
                "roundTrip": {
                    "supported": True,
                    "verifiedProperties": ["font-size"],
                },
            },
            "diagnosticRefsByTarget": {
                "lay:h3": [
                    "issue-group:issue-font",
                    "code:dom.unintended_overlap",
                    "semantic:sem:h3",
                    "qid:heading-h3",
                    "target:lay:h3",
                ]
            },
            "geometryCompositionGroups": {
                "global-clean": ["issue-overlap", "issue-font"]
            },
            "requiredGeometryIssueGroups": ["issue-overlap", "issue-font"],
            "limits": {"maxOperations": 2, "maxAffectedBlocks": 2},
        }
    )

    first = build_deterministic_cascade_report(context)
    second = build_deterministic_cascade_report(context)
    composed = [
        plan
        for plan in first.plans
        if "composition:global-clean" in plan.diagnostic_refs
    ]

    assert first == second
    assert len(composed) == 1
    assert [(operation.op, operation.target) for operation in composed[0].operations] == [
        ("translateWithinParent", "lay:owner-title-wrap"),
        ("setFontScale", "lay:h3"),
    ]
    assert composed[0].operations[1].scale == 0.9
    assert "issue-group:issue-overlap" in composed[0].diagnostic_refs
    assert "issue-group:issue-font" in composed[0].diagnostic_refs
