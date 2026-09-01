from __future__ import annotations

from copy import deepcopy

import pytest

from services.authored_vnext.slide_quality_acceptance import (
    canonical_digest,
    seal_slide_quality_acceptance,
    validate_slide_quality_acceptance_receipt,
)


def _digest(seed: str) -> str:
    return canonical_digest({"seed": seed})


def _observation(slide_index: int) -> dict[str, object]:
    chrome_digest = _digest("protected-chrome")
    return {
        "slideIndex": slide_index,
        "slideDigest": _digest(f"slide-{slide_index}"),
        "browserObserved": True,
        "browserEngine": "chromium",
        "viewport": {"width": 1600, "height": 900},
        "readabilityScore": 0.98,
        "titleBodyHierarchyScore": 0.98,
        "bodyTextElementCount": 4,
        "minimumBodyFontPt": 18.0,
        "templateMinimumBodyFontPt": 16.0,
        "wrappingViolationCount": 0,
        "clippedElementCount": 0,
        "prohibitedOverlapCount": 0,
        "offCanvasElementCount": 0,
        "balanceScore": 0.98,
        "whitespaceScore": 0.98,
        "meaningfulImageCount": 1,
        "meaningfulImageSizeScore": 0.98,
        "imageCropFidelityScore": 0.98,
        "undersizedMeaningfulImageCount": 0,
        "badImageCropCount": 0,
        "templateSlotFidelityScore": 0.98,
        "templateGeometryFidelityScore": 0.98,
        "expectedProtectedChromeDigest": chrome_digest,
        "observedProtectedChromeDigest": chrome_digest,
    }


def test_all_43_slides_must_individually_reach_90_and_receipt_is_digest_bound() -> None:
    subject_digest = _digest("scene-or-composition")
    observations = [_observation(index) for index in range(43)]

    receipt = seal_slide_quality_acceptance(
        subject_digest=subject_digest,
        browser_observations=observations,
    )
    payload = receipt.to_dict()

    assert receipt.document_quality_passed is True
    assert len(receipt.slides) == 43
    assert all(slide.passed and slide.score >= 90.0 for slide in receipt.slides)
    assert receipt.minimum_slide_score == pytest.approx(98.7)
    assert "averageSlideScore" not in payload
    assert payload["integrityGate"] == {
        "evaluated": False,
        "passed": None,
        "reason": "separate_receipt_required",
    }
    assert validate_slide_quality_acceptance_receipt(
        receipt,
        expected_subject_digest=subject_digest,
        expected_observation_set_digest=receipt.observation_set_digest,
    )

    tampered = deepcopy(payload)
    tampered["slides"][0]["score"] = 100.0
    assert not validate_slide_quality_acceptance_receipt(tampered)


def test_one_critical_browser_defect_blocks_the_document_without_deck_averaging() -> None:
    observations = [_observation(index) for index in range(43)]
    observations[17]["clippedElementCount"] = 1

    receipt = seal_slide_quality_acceptance(
        subject_digest=_digest("subject"),
        browser_observations=observations,
    )

    assert receipt.document_quality_passed is False
    assert receipt.failing_slide_indexes == (17,)
    assert receipt.slides[17].passed is False
    assert receipt.slides[17].score < 90.0
    assert "clipped_element" in receipt.blocker_codes
    assert all(receipt.slides[index].passed for index in range(43) if index != 17)


def test_missing_slide_and_protected_chrome_drift_fail_closed() -> None:
    observations = [_observation(index) for index in range(42)]
    observations[3]["observedProtectedChromeDigest"] = _digest("changed-chrome")

    receipt = seal_slide_quality_acceptance(
        subject_digest=_digest("subject"),
        browser_observations=observations,
    )

    assert receipt.document_quality_passed is False
    assert receipt.observed_slide_count == 42
    assert receipt.failing_slide_indexes == (3, 42)
    assert "protected_chrome_changed" in receipt.blocker_codes
    assert "browser_slide_observation_missing" in receipt.blocker_codes
    assert "slide_count_or_sequence_mismatch" in receipt.blocker_codes
    assert receipt.slides[42].score == 0.0
