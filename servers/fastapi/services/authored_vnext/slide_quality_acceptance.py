"""Template-neutral, per-slide document-quality acceptance.

This module consumes measurements made from the rendered browser document.  It
does not inspect template identities, compare background colours, or average a
deck into a passing score.  A document passes only when every expected slide
has a complete observation, no critical defect, and a score at or above the
policy threshold.

Content/source integrity is deliberately outside this contract.  Callers must
evaluate and bind their integrity receipt independently.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal


SCHEMA_VERSION = "slide-document-quality-acceptance-receipt/v1"
DEFAULT_EXPECTED_SLIDE_COUNT = 43
DEFAULT_SLIDE_SCORE_THRESHOLD = 90.0
DEFAULT_MINIMUM_BODY_FONT_PT = 16.0

_DIGEST_PREFIX = "sha256:"
_COMPONENT_WEIGHTS: Mapping[str, float] = {
    "readability": 15.0,
    "titleBodyHierarchy": 15.0,
    "minimumFont": 10.0,
    "flowSafety": 20.0,
    "balanceWhitespace": 10.0,
    "meaningfulImages": 10.0,
    "templateFidelity": 15.0,
    "protectedChrome": 5.0,
}
_QUALITY_FLOORS: Mapping[str, float] = {
    "readabilityScore": 0.85,
    "titleBodyHierarchyScore": 0.80,
    "meaningfulImageSizeScore": 0.80,
    "imageCropFidelityScore": 0.80,
    "templateSlotFidelityScore": 0.85,
    "templateGeometryFidelityScore": 0.85,
}


def canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _DIGEST_PREFIX + hashlib.sha256(encoded).hexdigest()


def _is_digest(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith(_DIGEST_PREFIX):
        return False
    suffix = value.removeprefix(_DIGEST_PREFIX)
    return len(suffix) == 64 and all(character in "0123456789abcdef" for character in suffix)


def _plain(value: object) -> object:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="python", by_alias=True)
    return value


@dataclass(frozen=True, slots=True)
class SlideQualityIssue:
    code: str
    path: str
    severity: Literal["critical"] = "critical"

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "severity": self.severity}


@dataclass(frozen=True, slots=True)
class SlideQualityDecision:
    slide_index: int
    slide_digest: str | None
    observation_digest: str
    component_scores: Mapping[str, float]
    score: float
    passed: bool
    issues: tuple[SlideQualityIssue, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "slideIndex": self.slide_index,
            "slideDigest": self.slide_digest,
            "observationDigest": self.observation_digest,
            "componentScores": dict(self.component_scores),
            "score": self.score,
            "passed": self.passed,
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True, slots=True)
class SlideQualityAcceptanceReceipt:
    subject_digest: str
    observation_set_digest: str
    policy_digest: str
    expected_slide_count: int
    observed_slide_count: int
    slide_score_threshold: float
    minimum_slide_score: float
    document_quality_passed: bool
    failing_slide_indexes: tuple[int, ...]
    blocker_codes: tuple[str, ...]
    slides: tuple[SlideQualityDecision, ...]
    receipt_digest: str
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "subjectDigest": self.subject_digest,
            "observationSetDigest": self.observation_set_digest,
            "policyDigest": self.policy_digest,
            "expectedSlideCount": self.expected_slide_count,
            "observedSlideCount": self.observed_slide_count,
            "slideScoreThreshold": self.slide_score_threshold,
            "minimumSlideScore": self.minimum_slide_score,
            "documentQualityPassed": self.document_quality_passed,
            "failingSlideIndexes": list(self.failing_slide_indexes),
            "blockerCodes": list(self.blocker_codes),
            "integrityGate": {
                "evaluated": False,
                "passed": None,
                "reason": "separate_receipt_required",
            },
            "slides": [slide.to_dict() for slide in self.slides],
            "receiptDigest": self.receipt_digest,
        }


def _issue(issues: list[SlideQualityIssue], *, code: str, path: str) -> None:
    issues.append(SlideQualityIssue(code=code, path=path))


def _unit_score(
    observation: Mapping[str, object],
    name: str,
    *,
    issues: list[SlideQualityIssue],
    floor: float | None = None,
) -> float:
    value = observation.get(name)
    path = f"observation.{name}"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _issue(issues, code="browser_quality_metric_missing", path=path)
        return 0.0
    score = float(value)
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        _issue(issues, code="browser_quality_metric_invalid", path=path)
        return 0.0
    if floor is not None and score < floor:
        _issue(issues, code=f"{name}_below_floor", path=path)
    return score


def _count(
    observation: Mapping[str, object],
    name: str,
    *,
    issues: list[SlideQualityIssue],
) -> int:
    value = observation.get(name)
    path = f"observation.{name}"
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _issue(issues, code="browser_count_metric_invalid", path=path)
        return 1
    return value


def _positive_number(
    observation: Mapping[str, object],
    name: str,
    *,
    issues: list[SlideQualityIssue],
) -> float:
    value = observation.get(name)
    path = f"observation.{name}"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _issue(issues, code="browser_measurement_missing", path=path)
        return 0.0
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        _issue(issues, code="browser_measurement_invalid", path=path)
        return 0.0
    return number


def _normal_observation(observation: Mapping[str, object]) -> dict[str, object]:
    """Drop upstream seals so this module binds the measurement payload itself."""

    return {
        str(key): _plain(value)
        for key, value in observation.items()
        if key not in {"observationDigest", "receiptDigest"}
    }


def _evaluate_slide(
    *,
    slide_index: int,
    observation: Mapping[str, object] | None,
    threshold: float,
    minimum_body_font_pt: float,
    unexpected: bool = False,
) -> SlideQualityDecision:
    if observation is None:
        missing = {"slideIndex": slide_index, "missing": True}
        issue = SlideQualityIssue(
            code="browser_slide_observation_missing",
            path=f"slides[{slide_index}]",
        )
        return SlideQualityDecision(
            slide_index=slide_index,
            slide_digest=None,
            observation_digest=canonical_digest(missing),
            component_scores={name: 0.0 for name in _COMPONENT_WEIGHTS},
            score=0.0,
            passed=False,
            issues=(issue,),
        )

    normalized = _normal_observation(observation)
    issues: list[SlideQualityIssue] = []
    if observation.get("browserObserved") is not True:
        _issue(
            issues,
            code="browser_observation_not_confirmed",
            path="observation.browserObserved",
        )
    engine = observation.get("browserEngine")
    if not isinstance(engine, str) or not engine.strip():
        _issue(issues, code="browser_engine_missing", path="observation.browserEngine")
    viewport = observation.get("viewport")
    if not isinstance(viewport, Mapping):
        _issue(issues, code="browser_viewport_missing", path="observation.viewport")
    else:
        _positive_number(viewport, "width", issues=issues)
        _positive_number(viewport, "height", issues=issues)

    slide_digest = observation.get("slideDigest")
    if not _is_digest(slide_digest):
        _issue(issues, code="slide_digest_invalid", path="observation.slideDigest")
        slide_digest = None

    readability = _unit_score(
        observation,
        "readabilityScore",
        issues=issues,
        floor=_QUALITY_FLOORS["readabilityScore"],
    )
    hierarchy = _unit_score(
        observation,
        "titleBodyHierarchyScore",
        issues=issues,
        floor=_QUALITY_FLOORS["titleBodyHierarchyScore"],
    )

    body_count = _count(observation, "bodyTextElementCount", issues=issues)
    template_floor = observation.get("templateMinimumBodyFontPt", minimum_body_font_pt)
    if isinstance(template_floor, bool) or not isinstance(template_floor, (int, float)):
        _issue(
            issues,
            code="template_minimum_font_invalid",
            path="observation.templateMinimumBodyFontPt",
        )
        template_floor = minimum_body_font_pt
    effective_font_floor = max(minimum_body_font_pt, float(template_floor))
    minimum_font_score = 1.0
    if body_count > 0:
        observed_font = _positive_number(observation, "minimumBodyFontPt", issues=issues)
        if observed_font < effective_font_floor:
            minimum_font_score = 0.0
            _issue(
                issues,
                code="minimum_body_font_below_floor",
                path="observation.minimumBodyFontPt",
            )

    flow_metric_codes = {
        "wrappingViolationCount": "unexpected_text_wrapping",
        "clippedElementCount": "clipped_element",
        "prohibitedOverlapCount": "prohibited_overlap",
        "offCanvasElementCount": "off_canvas_element",
    }
    flow_safe = True
    for metric, code in flow_metric_codes.items():
        if _count(observation, metric, issues=issues) > 0:
            flow_safe = False
            _issue(issues, code=code, path=f"observation.{metric}")

    balance = _unit_score(observation, "balanceScore", issues=issues)
    whitespace = _unit_score(observation, "whitespaceScore", issues=issues)

    image_count = _count(observation, "meaningfulImageCount", issues=issues)
    image_score = 1.0
    if image_count > 0:
        image_size = _unit_score(
            observation,
            "meaningfulImageSizeScore",
            issues=issues,
            floor=_QUALITY_FLOORS["meaningfulImageSizeScore"],
        )
        image_crop = _unit_score(
            observation,
            "imageCropFidelityScore",
            issues=issues,
            floor=_QUALITY_FLOORS["imageCropFidelityScore"],
        )
        image_score = (image_size + image_crop) / 2.0
        if _count(observation, "undersizedMeaningfulImageCount", issues=issues) > 0:
            _issue(
                issues,
                code="meaningful_image_undersized",
                path="observation.undersizedMeaningfulImageCount",
            )
        if _count(observation, "badImageCropCount", issues=issues) > 0:
            _issue(
                issues,
                code="meaningful_image_bad_crop",
                path="observation.badImageCropCount",
            )

    slot_fidelity = _unit_score(
        observation,
        "templateSlotFidelityScore",
        issues=issues,
        floor=_QUALITY_FLOORS["templateSlotFidelityScore"],
    )
    geometry_fidelity = _unit_score(
        observation,
        "templateGeometryFidelityScore",
        issues=issues,
        floor=_QUALITY_FLOORS["templateGeometryFidelityScore"],
    )

    expected_chrome = observation.get("expectedProtectedChromeDigest")
    observed_chrome = observation.get("observedProtectedChromeDigest")
    chrome_matches = _is_digest(expected_chrome) and expected_chrome == observed_chrome
    if not _is_digest(expected_chrome) or not _is_digest(observed_chrome):
        _issue(
            issues,
            code="protected_chrome_digest_missing",
            path="observation.protectedChromeDigest",
        )
    elif not chrome_matches:
        _issue(
            issues,
            code="protected_chrome_changed",
            path="observation.observedProtectedChromeDigest",
        )

    if unexpected:
        _issue(issues, code="unexpected_slide_observation", path="observation.slideIndex")

    component_scores = {
        "readability": readability,
        "titleBodyHierarchy": hierarchy,
        "minimumFont": minimum_font_score,
        "flowSafety": 1.0 if flow_safe else 0.0,
        "balanceWhitespace": (balance + whitespace) / 2.0,
        "meaningfulImages": image_score,
        "templateFidelity": (slot_fidelity + geometry_fidelity) / 2.0,
        "protectedChrome": 1.0 if chrome_matches else 0.0,
    }
    score = round(
        sum(component_scores[name] * weight for name, weight in _COMPONENT_WEIGHTS.items()),
        2,
    )
    passed = not issues and score >= threshold
    return SlideQualityDecision(
        slide_index=slide_index,
        slide_digest=slide_digest if isinstance(slide_digest, str) else None,
        observation_digest=canonical_digest(normalized),
        component_scores={name: round(value, 4) for name, value in component_scores.items()},
        score=score,
        passed=passed,
        issues=tuple(issues),
    )


def seal_slide_quality_acceptance(
    *,
    subject_digest: str,
    browser_observations: Sequence[Mapping[str, object]],
    expected_slide_count: int = DEFAULT_EXPECTED_SLIDE_COUNT,
    slide_score_threshold: float = DEFAULT_SLIDE_SCORE_THRESHOLD,
    minimum_body_font_pt: float = DEFAULT_MINIMUM_BODY_FONT_PT,
) -> SlideQualityAcceptanceReceipt:
    """Evaluate every slide and seal a digest-bound document-quality receipt."""

    if not _is_digest(subject_digest):
        raise ValueError("slide_quality_subject_digest_invalid")
    if isinstance(expected_slide_count, bool) or expected_slide_count <= 0:
        raise ValueError("slide_quality_expected_slide_count_invalid")
    if not 0.0 <= slide_score_threshold <= 100.0:
        raise ValueError("slide_quality_threshold_invalid")
    if minimum_body_font_pt <= 0:
        raise ValueError("slide_quality_minimum_font_invalid")
    if isinstance(browser_observations, (str, bytes)) or not isinstance(
        browser_observations, Sequence
    ):
        raise ValueError("slide_quality_browser_observations_invalid")

    by_index: dict[int, Mapping[str, object]] = {}
    for observation in browser_observations:
        if not isinstance(observation, Mapping):
            raise ValueError("slide_quality_browser_observation_invalid")
        index = observation.get("slideIndex")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError("slide_quality_slide_index_invalid")
        if index in by_index:
            raise ValueError("slide_quality_duplicate_slide_index")
        by_index[index] = observation

    expected_indexes = set(range(expected_slide_count))
    all_indexes = sorted(expected_indexes.union(by_index))
    slides = tuple(
        _evaluate_slide(
            slide_index=index,
            observation=by_index.get(index),
            threshold=slide_score_threshold,
            minimum_body_font_pt=minimum_body_font_pt,
            unexpected=index not in expected_indexes,
        )
        for index in all_indexes
    )
    count_matches = len(by_index) == expected_slide_count and set(by_index) == expected_indexes
    failing_indexes = tuple(slide.slide_index for slide in slides if not slide.passed)
    blocker_codes = sorted({issue.code for slide in slides for issue in slide.issues})
    if not count_matches:
        blocker_codes.append("slide_count_or_sequence_mismatch")
    blocker_codes = sorted(set(blocker_codes))
    minimum_score = min((slide.score for slide in slides), default=0.0)
    passed = count_matches and not failing_indexes and minimum_score >= slide_score_threshold

    policy = {
        "schemaVersion": "slide-document-quality-policy/v1",
        "expectedSlideCount": expected_slide_count,
        "slideScoreThreshold": slide_score_threshold,
        "minimumBodyFontPt": minimum_body_font_pt,
        "componentWeights": dict(_COMPONENT_WEIGHTS),
        "qualityFloors": dict(_QUALITY_FLOORS),
        "aggregation": "all-slides-must-pass",
        "integrityGate": "separate",
    }
    policy_digest = canonical_digest(policy)
    observation_set_digest = canonical_digest(
        [slide.observation_digest for slide in slides]
    )
    unsigned: dict[str, object] = {
        "schemaVersion": SCHEMA_VERSION,
        "subjectDigest": subject_digest,
        "observationSetDigest": observation_set_digest,
        "policyDigest": policy_digest,
        "expectedSlideCount": expected_slide_count,
        "observedSlideCount": len(by_index),
        "slideScoreThreshold": slide_score_threshold,
        "minimumSlideScore": minimum_score,
        "documentQualityPassed": passed,
        "failingSlideIndexes": list(failing_indexes),
        "blockerCodes": blocker_codes,
        "integrityGate": {
            "evaluated": False,
            "passed": None,
            "reason": "separate_receipt_required",
        },
        "slides": [slide.to_dict() for slide in slides],
    }
    return SlideQualityAcceptanceReceipt(
        subject_digest=subject_digest,
        observation_set_digest=observation_set_digest,
        policy_digest=policy_digest,
        expected_slide_count=expected_slide_count,
        observed_slide_count=len(by_index),
        slide_score_threshold=slide_score_threshold,
        minimum_slide_score=minimum_score,
        document_quality_passed=passed,
        failing_slide_indexes=failing_indexes,
        blocker_codes=tuple(blocker_codes),
        slides=slides,
        receipt_digest=canonical_digest(unsigned),
    )


def validate_slide_quality_acceptance_receipt(
    receipt: SlideQualityAcceptanceReceipt | Mapping[str, object],
    *,
    expected_subject_digest: str | None = None,
    expected_observation_set_digest: str | None = None,
) -> bool:
    """Validate the seal and optional integration-bound input digests."""

    payload = receipt.to_dict() if isinstance(receipt, SlideQualityAcceptanceReceipt) else dict(receipt)
    supplied = payload.pop("receiptDigest", None)
    if not _is_digest(supplied) or supplied != canonical_digest(payload):
        return False
    if payload.get("schemaVersion") != SCHEMA_VERSION:
        return False
    if expected_subject_digest is not None and payload.get("subjectDigest") != expected_subject_digest:
        return False
    slide_payloads = payload.get("slides")
    if not isinstance(slide_payloads, Sequence) or isinstance(slide_payloads, (str, bytes)):
        return False
    observation_digests = []
    for slide in slide_payloads:
        if not isinstance(slide, Mapping) or not _is_digest(slide.get("observationDigest")):
            return False
        observation_digests.append(slide["observationDigest"])
    derived_observation_set_digest = canonical_digest(observation_digests)
    if payload.get("observationSetDigest") != derived_observation_set_digest:
        return False
    if (
        expected_observation_set_digest is not None
        and derived_observation_set_digest != expected_observation_set_digest
    ):
        return False
    integrity = payload.get("integrityGate")
    return isinstance(integrity, Mapping) and integrity == {
        "evaluated": False,
        "passed": None,
        "reason": "separate_receipt_required",
    }


__all__ = [
    "DEFAULT_EXPECTED_SLIDE_COUNT",
    "DEFAULT_MINIMUM_BODY_FONT_PT",
    "DEFAULT_SLIDE_SCORE_THRESHOLD",
    "SCHEMA_VERSION",
    "SlideQualityAcceptanceReceipt",
    "SlideQualityDecision",
    "SlideQualityIssue",
    "canonical_digest",
    "seal_slide_quality_acceptance",
    "validate_slide_quality_acceptance_receipt",
]
