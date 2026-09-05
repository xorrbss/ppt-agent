"""QUAL-04: visual-review evidence follows the VQA outcome, not the request flag."""

from __future__ import annotations

from services.authored_visual_review_evidence import (
    NOT_RUN_REASON_CODE,
    seal_visual_review_evidence,
    visual_review_not_assessed,
    visual_review_outcome,
)


def test_not_run_is_not_assessed_with_a_reason_code() -> None:
    evidence = visual_review_not_assessed()
    assert evidence["status"] == "not_assessed"
    assert evidence["reasonCode"] == NOT_RUN_REASON_CODE
    assert evidence["fullDeck"] is False


def test_run_without_residual_issues_passes_the_full_deck() -> None:
    evidence = visual_review_outcome(
        slide_count=4,
        fixed=[1, 3],
        accepted_fixed=[1, 3],
        repair_details=[
            {"slide_index": 1, "succeeded": True},
            {"slide_index": 3, "succeeded": True},
        ],
    )
    assert evidence["status"] == "passed"
    assert evidence["fullDeck"] is True
    assert evidence["reviewedSlideCount"] == 4
    assert evidence["fixedSlideCount"] == 2
    assert evidence["acceptedFixedSlideCount"] == 2
    assert evidence["residualSlideCount"] == 0
    assert evidence["residualSlideIndices"] == []


def test_failed_repair_or_rejected_candidate_is_a_residual_issue() -> None:
    evidence = visual_review_outcome(
        slide_count=5,
        fixed=[0, 2],
        # The capacity admission rejected slide 2's candidate.
        accepted_fixed=[0],
        repair_details=[
            {"slide_index": 0, "succeeded": True},
            {"slide_index": 2, "succeeded": True},
            # A critique needed a fix on slide 4 and none was applied.
            {"slide_index": 4, "succeeded": False},
        ],
    )
    assert evidence["status"] == "failed"
    assert evidence["fullDeck"] is False
    assert evidence["residualSlideIndices"] == [2, 4]
    assert evidence["residualSlideCount"] == 2
    assert evidence["acceptedFixedSlideCount"] == 1
    assert "reasonCode" not in evidence


def test_reviser_error_fails_with_the_error_type_as_reason() -> None:
    evidence = visual_review_outcome(
        slide_count=3, fixed=[], accepted_fixed=[], error=RuntimeError("boom")
    )
    assert evidence["status"] == "failed"
    assert evidence["reasonCode"] == "visual_review_error:RuntimeError"
    assert evidence["fullDeck"] is False


def test_seal_never_upgrades_an_unreviewed_deck_to_passed() -> None:
    assert seal_visual_review_evidence(None, tier="T0")["status"] == "not_required"
    unreviewed = seal_visual_review_evidence(visual_review_not_assessed(), tier="T1")
    assert unreviewed["status"] == "unavailable"
    assert unreviewed["fullDeck"] is False
    assert unreviewed["reasonCode"] == NOT_RUN_REASON_CODE
    # A caller cannot smuggle fullDeck past a non-passed status.
    forged = seal_visual_review_evidence({"status": "failed", "fullDeck": True}, tier="T2")
    assert forged["status"] == "failed" and forged["fullDeck"] is False
    passed = seal_visual_review_evidence(
        visual_review_outcome(slide_count=2, fixed=[], accepted_fixed=[]), tier="T2"
    )
    assert passed["status"] == "passed" and passed["fullDeck"] is True
