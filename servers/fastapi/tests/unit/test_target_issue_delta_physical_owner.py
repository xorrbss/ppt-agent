from copy import deepcopy

from services.slidespec_geometry_candidates import generate_geometry_candidates


def _context() -> dict:
    dy_px = 37.36
    dy = dy_px / 720
    return {
        "maxGeometryCandidates": 8,
        "geometryIssues": [
            {
                "code": "dom.unintended_overlap",
                "semanticId": "sem:implication-text",
                "qid": "q-div-655134fc7990",
                "constraintId": "dom:overlap:physical-band",
                "issueGroupId": "geometry:physical-band",
                "kind": "nonOverlap",
                "target": "lay:implication-text",
                "currentBounds": {
                    "x": 0.3,
                    "y": 0.78,
                    "width": 0.5,
                    "height": 0.04,
                },
                "otherTarget": "lay:blocking-card",
                "otherBounds": {
                    "x": 0.28,
                    "y": 0.70,
                    "width": 0.67,
                    "height": 0.114,
                },
                "sameCoordinateSpace": True,
                "requiresEnvelope": True,
                "mutationEnvelope": {"locked": True},
                "detectorEvidence": {
                    "confirmed": True,
                    "independentElements": True,
                    "stage": "dom",
                    "metric": "owned_content_occluded_fraction",
                    "value": 1.0,
                    "threshold": 0.0,
                    "occluderQid": "q-div-377b136f8629",
                },
                "boundAncestors": [
                    {
                        "qid": "q-div-f17e611a8d0f",
                        "semanticId": "sem:summary-band",
                        "layoutId": "lay:summary-band",
                        "bounds": {
                            "x": 355 / 1280,
                            "y": 548.64 / 720,
                            "width": 861 / 1280,
                            "height": 104 / 720,
                        },
                        "containerBounds": {
                            "x": 0,
                            "y": 0,
                            "width": 1,
                            "height": 1,
                        },
                        "mutationEnvelope": {
                            "locked": False,
                            "lockSize": True,
                            "allowedOperations": ["translateWithinParent"],
                            "maxTranslation": 0.1,
                        },
                        "candidateDeltas": [
                            {
                                "x": 0,
                                "y": dy,
                                "source": "chrome",
                                "telemetryComplete": True,
                                "resolvesIssue": True,
                                "deltaPx": {"x": 0, "y": dy_px},
                                "physicalOwnerProof": {
                                    "ownerQid": "q-div-f17e611a8d0f",
                                    "ownerBoundsPx": {
                                        "x": 355,
                                        "y": 548.64,
                                        "width": 861,
                                        "height": 104,
                                    },
                                    "candidateOwnerBoundsPx": {
                                        "x": 355,
                                        "y": 586,
                                        "width": 861,
                                        "height": 104,
                                    },
                                    "ownedTargetQids": [
                                        "q-div-63a4d3c2e6c7",
                                        "q-div-655134fc7990",
                                        "q-strong-f213b54d7db8",
                                    ],
                                    "blockerBoundsPx": {
                                        "q-div-377b136f8629": {
                                            "x": 355,
                                            "y": 500,
                                            "width": 861,
                                            "height": 86,
                                        }
                                    },
                                    "lowerBoundaryQid": "q-div-c6f2820b2605",
                                    "lowerBoundaryYPx": 690,
                                    "allTargetsOwned": True,
                                    "candidateSeparatesBlockers": True,
                                    "candidateContained": True,
                                },
                            }
                        ],
                        "coordinatedOwnerOnly": False,
                        "depth": 1,
                        "sameCoordinateSpace": True,
                        "ownershipProven": True,
                        "browserProof": {
                            "source": "chrome",
                            "telemetryComplete": True,
                            "targetQid": "q-div-655134fc7990",
                            "ancestorQid": "q-div-f17e611a8d0f",
                            "parentQidChain": [
                                "q-div-655134fc7990",
                                "q-div-f17e611a8d0f",
                            ],
                        },
                    }
                ],
            }
        ],
    }


def test_physical_owner_translation_is_a_single_minimal_change_candidate():
    result = generate_geometry_candidates(_context())

    candidate = next(
        item
        for item in result.candidates
        if item.operations[0]["target"] == "lay:summary-band"
    )
    assert len(candidate.operations) == 1
    assert candidate.operations[0]["op"] == "translateWithinParent"
    assert candidate.operations[0]["value"] == {
        "x": 0.0,
        "y": round(37.36 / 720, 9),
    }
    assert "owner-qid:q-div-f17e611a8d0f" in candidate.diagnostic_refs


def test_physical_owner_translation_fails_closed_when_proof_is_inconsistent():
    context = deepcopy(_context())
    proof = context["geometryIssues"][0]["boundAncestors"][0][
        "candidateDeltas"
    ][0]["physicalOwnerProof"]
    proof["candidateSeparatesBlockers"] = False

    result = generate_geometry_candidates(context)

    assert result.candidates == ()
    assert any(
        rejection.reason_code
        == "repair.geometry.ancestor.physical_owner_proof_invalid"
        for rejection in result.rejections
    )


def test_no_safe_owner_translation_retains_capacity_blocker():
    context = deepcopy(_context())
    ancestor = context["geometryIssues"][0]["boundAncestors"][0]
    ancestor["candidateDeltas"] = []
    ancestor["coordinatedOwnerOnly"] = True
    failure = {
        "reasonCode": "repair.geometry.ancestor.physical_owner_no_safe_translation",
        "ownerQid": "q-div-f17e611a8d0f",
        "requiredDownPx": 73.65625,
        "availableDownPx": 37.359375,
        "lowerBoundaryQid": "q-div-c6f2820b2605",
    }
    ancestor["physicalOwnerFailure"] = failure
    context["geometryIssues"][0]["detectorEvidence"][
        "physicalOwnerBlockers"
    ] = [failure]

    result = generate_geometry_candidates(context)

    assert result.candidates == ()
    rejection = next(
        rejection
        for rejection in result.rejections
        if rejection.reason_code
        == "repair.geometry.ancestor.physical_owner_no_safe_translation"
    )
    assert (
        rejection.detector_evidence["physicalOwnerBlockers"][0][
            "requiredDownPx"
        ]
        == 73.65625
    )
    assert any(
        rejection.reason_code
        == "repair.geometry.ancestor.physical_owner_no_safe_translation"
        for rejection in result.rejections
    )
