"""Artifact-bound, worst-page visual acceptance for Deck IR output.

This evaluator intentionally does not accept deck-wide palette averages or an
aspect-ratio-only geometry score as quality evidence.  Acceptance is derived
from ordered, artifact-bound page observations and remains unavailable until a
sealed human-correlation receipt validates the automatic metric on a holdout.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
import re
from types import MappingProxyType
from typing import Any
from uuid import UUID

from .grammar import digest


EVIDENCE_SCHEMA_VERSION = "deck-ir-visual-evidence/v1"
DECISION_SCHEMA_VERSION = "deck-ir-visual-evaluation-decision/v1"
ARTIFACT_BINDING_SCHEMA_VERSION = "deck-ir-visual-artifact-binding/v1"
HUMAN_CORRELATION_SCHEMA_VERSION = "deck-ir-human-correlation-receipt/v1"
METRIC_VERSION = "deck-ir-page-visual-evaluator/v1"

REQUIRED_DIMENSIONS = (
    "role",
    "typography",
    "alignmentSpacingLineHeight",
    "density",
    "blockGeometry",
    "paletteUsage",
    "chromeBottomBand",
    "diagramChartProcess",
    "repetition",
    "whitespace",
    "overlapClipWrap",
    "overall",
)

DEFAULT_DIMENSION_FLOOR = 3.75
DEFAULT_WORST_PAGE_FLOOR = 3.5
MINIMUM_GOLD_SET_PAIRS = 120
MINIMUM_STRATUM_PAIRS = 30
MINIMUM_REVIEWER_AGREEMENT = 0.67
MINIMUM_RANK_CORRELATION = 0.67
MINIMUM_WORST_PAGE_RECALL = 0.90
MAXIMUM_CRITICAL_FALSE_ACCEPT_RATE = 0.05

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ROLE_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_FORBIDDEN_ACCEPTANCE_FIELDS = frozenset(
    {
        "score",
        "meanStyleScore",
        "mean_style_score",
        "geometryScore",
        "geometry_score",
        "globalColorScore",
        "global_color_score",
        "aspectRatioGeometry",
        "aspect_ratio_geometry",
        "lightBase",
        "light_base",
        "inkDensity",
        "ink_density",
        "edgeDensity",
        "edge_density",
    }
)
_TOP_LEVEL_FIELDS = frozenset(
    {
        "schemaVersion",
        "candidate",
        "reference",
        "pages",
        "humanCorrelation",
        "informativeMetrics",
        "evidenceDigest",
    }
)
_ARTIFACT_FIELDS = frozenset(
    {
        "schemaVersion",
        "presentationId",
        "artifactDigest",
        "operationId",
        "revision",
        "orderedSlides",
    }
)
_SLIDE_FIELDS = frozenset({"pageNumber", "slideDigest", "role"})
_PAGE_FIELDS = frozenset(
    {
        "pageNumber",
        "candidateSlideDigest",
        "referenceSlideDigest",
        "candidateRole",
        "referenceRole",
        "dimensions",
        "automaticStatus",
        "humanStatus",
        "visualContradiction",
        "issues",
    }
)
_CORRELATION_FIELDS = frozenset(
    {
        "schemaVersion",
        "metricVersion",
        "goldSetDigest",
        "sealedHoldoutDigest",
        "evaluationPolicyDigest",
        "sealedBeforeScoring",
        "overallPairCount",
        "minimumMarginalStratumPairCount",
        "reviewerReceiptDigests",
        "blindAssignmentDigest",
        "reviewerAgreement",
        "correlationMethod",
        "rankCorrelation",
        "worstPageRecall",
        "criticalFalseAcceptRate",
        "status",
        "receiptDigest",
    }
)


@dataclass(frozen=True, slots=True)
class _BoundSlide:
    page_number: int
    slide_digest: str
    role: str


@dataclass(frozen=True, slots=True)
class _ArtifactBinding:
    presentation_id: str
    artifact_digest: str
    operation_id: str
    revision: int | str
    slides: tuple[_BoundSlide, ...]


@dataclass(frozen=True, slots=True)
class VisualEvaluationDecision:
    acceptance: bool
    percentage: float | None
    reasons: tuple[str, ...]
    page_scores: Mapping[int, float]
    worst_page_number: int | None
    worst_page_score: float | None
    human_correlation_status: str
    human_correlation_errors: tuple[str, ...]
    decision_digest: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schemaVersion": DECISION_SCHEMA_VERSION,
            "acceptance": self.acceptance,
            "percentage": self.percentage,
            "reasons": list(self.reasons),
            "pageScores": {str(key): value for key, value in self.page_scores.items()},
            "worstPageNumber": self.worst_page_number,
            "worstPageScore": self.worst_page_score,
            "humanCorrelationStatus": self.human_correlation_status,
            "humanCorrelationErrors": list(self.human_correlation_errors),
            "informativeMetricsUsedForAcceptance": False,
            "decisionDigest": self.decision_digest,
        }


def visual_evidence_digest(value: Mapping[str, object]) -> str:
    """Seal a visual-evidence or human-correlation mapping by self digest."""

    payload = dict(value)
    payload.pop("evidenceDigest", None)
    payload.pop("receiptDigest", None)
    return digest(payload)


def _append(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _sequence(value: object) -> Sequence[object] | None:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return None


def _digest_string(value: object) -> bool:
    return isinstance(value, str) and _DIGEST_RE.fullmatch(value) is not None


def _uuid_string(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(UUID(value)) == value.lower()
    except ValueError:
        return False


def _valid_revision(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
    ) or (isinstance(value, str) and bool(value.strip()))


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _unknown_fields(
    value: Mapping[str, Any],
    allowed: frozenset[str],
    *,
    prefix: str,
    reasons: list[str],
) -> bool:
    valid = True
    for key in sorted(str(item) for item in value if str(item) not in allowed):
        _append(reasons, f"{prefix}_unknown_field:{key}")
        valid = False
    return valid


def _validate_artifact(
    value: object,
    *,
    label: str,
    reasons: list[str],
) -> tuple[_ArtifactBinding | None, bool]:
    raw = _mapping(value)
    if raw is None:
        _append(reasons, f"{label}_artifact_binding_invalid")
        return None, False
    valid = _unknown_fields(raw, _ARTIFACT_FIELDS, prefix=f"{label}_artifact", reasons=reasons)
    if raw.get("schemaVersion") != ARTIFACT_BINDING_SCHEMA_VERSION:
        _append(reasons, f"{label}_artifact_schema_invalid")
        valid = False
    presentation_id = raw.get("presentationId")
    artifact_digest = raw.get("artifactDigest")
    operation_id = raw.get("operationId")
    revision = raw.get("revision")
    if not _uuid_string(presentation_id):
        _append(reasons, f"{label}_presentation_binding_invalid")
        valid = False
    if not _digest_string(artifact_digest):
        _append(reasons, f"{label}_artifact_digest_invalid")
        valid = False
    if not _uuid_string(operation_id):
        _append(reasons, f"{label}_operation_binding_invalid")
        valid = False
    if not _valid_revision(revision):
        _append(reasons, f"{label}_revision_binding_invalid")
        valid = False

    slide_rows = _sequence(raw.get("orderedSlides"))
    if not slide_rows:
        _append(reasons, f"{label}_ordered_slides_invalid")
        return None, False
    slides: list[_BoundSlide] = []
    for index, item in enumerate(slide_rows, start=1):
        slide = _mapping(item)
        if slide is None:
            _append(reasons, f"{label}_slide_binding_invalid:{index}")
            valid = False
            continue
        valid = _unknown_fields(
            slide,
            _SLIDE_FIELDS,
            prefix=f"{label}_slide_{index}",
            reasons=reasons,
        ) and valid
        page_number = slide.get("pageNumber")
        slide_digest = slide.get("slideDigest")
        role = slide.get("role")
        if page_number != index:
            _append(reasons, f"{label}_slide_order_invalid:{index}")
            valid = False
        if not _digest_string(slide_digest):
            _append(reasons, f"{label}_slide_digest_invalid:{index}")
            valid = False
        if not isinstance(role, str) or _ROLE_RE.fullmatch(role) is None:
            _append(reasons, f"{label}_page_role_invalid:{index}")
            valid = False
        if page_number == index and _digest_string(slide_digest) and isinstance(role, str) and _ROLE_RE.fullmatch(role):
            slides.append(_BoundSlide(index, slide_digest, role))
    if len(slides) != len(slide_rows):
        return None, False
    return (
        _ArtifactBinding(
            presentation_id=str(presentation_id),
            artifact_digest=str(artifact_digest),
            operation_id=str(operation_id),
            revision=revision if isinstance(revision, (int, str)) else "",
            slides=tuple(slides),
        ),
        valid,
    )


def _validate_human_correlation(value: object) -> tuple[bool, tuple[str, ...]]:
    errors: list[str] = []
    raw = _mapping(value)
    if raw is None:
        return False, ("human_correlation_missing",)
    _unknown_fields(raw, _CORRELATION_FIELDS, prefix="human_correlation", reasons=errors)
    if raw.get("schemaVersion") != HUMAN_CORRELATION_SCHEMA_VERSION:
        _append(errors, "human_correlation_schema_invalid")
    if raw.get("metricVersion") != METRIC_VERSION:
        _append(errors, "human_correlation_metric_version_invalid")
    for field in (
        "goldSetDigest",
        "sealedHoldoutDigest",
        "evaluationPolicyDigest",
        "blindAssignmentDigest",
    ):
        if not _digest_string(raw.get(field)):
            _append(errors, f"human_correlation_{field}_invalid")
    if raw.get("sealedBeforeScoring") is not True:
        _append(errors, "human_correlation_holdout_not_sealed")
    if raw.get("status") != "passed":
        _append(errors, "human_correlation_status_not_passed")
    if raw.get("correlationMethod") not in {"spearman", "kendall"}:
        _append(errors, "human_correlation_method_invalid")
    overall_pairs = raw.get("overallPairCount")
    stratum_pairs = raw.get("minimumMarginalStratumPairCount")
    if not isinstance(overall_pairs, int) or isinstance(overall_pairs, bool) or overall_pairs < MINIMUM_GOLD_SET_PAIRS:
        _append(errors, "human_correlation_sample_insufficient")
    if not isinstance(stratum_pairs, int) or isinstance(stratum_pairs, bool) or stratum_pairs < MINIMUM_STRATUM_PAIRS:
        _append(errors, "human_correlation_stratum_insufficient")
    reviewer_receipts = _sequence(raw.get("reviewerReceiptDigests"))
    if (
        reviewer_receipts is None
        or len(reviewer_receipts) < 3
        or not all(_digest_string(item) for item in reviewer_receipts)
    ):
        _append(errors, "human_correlation_reviewer_receipts_insufficient")
    numeric_floors = (
        ("reviewerAgreement", MINIMUM_REVIEWER_AGREEMENT, None),
        ("rankCorrelation", MINIMUM_RANK_CORRELATION, None),
        ("worstPageRecall", MINIMUM_WORST_PAGE_RECALL, None),
        ("criticalFalseAcceptRate", None, MAXIMUM_CRITICAL_FALSE_ACCEPT_RATE),
    )
    for field, minimum, maximum in numeric_floors:
        number = _number(raw.get(field))
        if (
            number is None
            or number < 0
            or number > 1
            or (minimum is not None and number < minimum)
            or (maximum is not None and number > maximum)
        ):
            _append(errors, f"human_correlation_{field}_failed")
    receipt_digest = raw.get("receiptDigest")
    if not _digest_string(receipt_digest) or receipt_digest != visual_evidence_digest(raw):
        _append(errors, "human_correlation_receipt_digest_invalid")
    return not errors, tuple(errors)


def evaluate_visual_evidence(
    evidence: object,
    *,
    dimension_floor: float = DEFAULT_DIMENSION_FLOOR,
    worst_page_floor: float = DEFAULT_WORST_PAGE_FLOOR,
) -> VisualEvaluationDecision:
    """Evaluate artifact-bound page evidence and fail closed on the worst page."""

    reasons: list[str] = []
    page_scores: dict[int, float] = {}
    page_overalls: list[tuple[int, float]] = []
    measurement_valid = True
    if (
        _number(dimension_floor) is None
        or _number(worst_page_floor) is None
        or not 0 <= dimension_floor <= 5
        or not 0 <= worst_page_floor <= 5
    ):
        raise ValueError("visual_evaluation_floor_invalid")

    raw = _mapping(evidence)
    if raw is None:
        raw = {}
        _append(reasons, "visual_evidence_mapping_invalid")
        measurement_valid = False
    if raw.get("schemaVersion") != EVIDENCE_SCHEMA_VERSION:
        _append(reasons, "visual_evidence_schema_invalid")
        measurement_valid = False
    for field in sorted(_FORBIDDEN_ACCEPTANCE_FIELDS.intersection(str(key) for key in raw)):
        _append(reasons, f"acceptance_metric_not_allowed:{field}")
        measurement_valid = False
    if not _unknown_fields(raw, _TOP_LEVEL_FIELDS, prefix="visual_evidence", reasons=reasons):
        measurement_valid = False
    informative = raw.get("informativeMetrics", {})
    if not isinstance(informative, Mapping):
        _append(reasons, "informative_metrics_mapping_invalid")
        measurement_valid = False

    candidate, candidate_valid = _validate_artifact(raw.get("candidate"), label="candidate", reasons=reasons)
    reference, reference_valid = _validate_artifact(raw.get("reference"), label="reference", reasons=reasons)
    measurement_valid = measurement_valid and candidate_valid and reference_valid
    if candidate is not None and reference is not None and len(candidate.slides) != len(reference.slides):
        _append(reasons, "ordered_slide_count_mismatch")
        measurement_valid = False

    page_rows = _sequence(raw.get("pages"))
    expected_count = len(candidate.slides) if candidate is not None else 0
    if page_rows is None or len(page_rows) != expected_count or expected_count == 0:
        _append(reasons, "page_evidence_coverage_invalid")
        measurement_valid = False
        page_rows = ()

    for index, item in enumerate(page_rows, start=1):
        page = _mapping(item)
        if page is None:
            _append(reasons, f"page_evidence_invalid:{index}")
            measurement_valid = False
            continue
        if not _unknown_fields(page, _PAGE_FIELDS, prefix=f"page_{index}", reasons=reasons):
            measurement_valid = False
        if page.get("pageNumber") != index:
            _append(reasons, f"page_order_invalid:{index}")
            measurement_valid = False
        candidate_slide = candidate.slides[index - 1] if candidate is not None and index <= len(candidate.slides) else None
        reference_slide = reference.slides[index - 1] if reference is not None and index <= len(reference.slides) else None
        if candidate_slide is None or page.get("candidateSlideDigest") != candidate_slide.slide_digest:
            _append(reasons, f"candidate_page_digest_binding_invalid:{index}")
            measurement_valid = False
        if reference_slide is None or page.get("referenceSlideDigest") != reference_slide.slide_digest:
            _append(reasons, f"reference_page_digest_binding_invalid:{index}")
            measurement_valid = False
        if candidate_slide is None or page.get("candidateRole") != candidate_slide.role:
            _append(reasons, f"candidate_page_role_binding_invalid:{index}")
            measurement_valid = False
        if reference_slide is None or page.get("referenceRole") != reference_slide.role:
            _append(reasons, f"reference_page_role_binding_invalid:{index}")
            measurement_valid = False
        if page.get("candidateRole") != page.get("referenceRole"):
            _append(reasons, f"page_role_mismatch:{index}")
            measurement_valid = False

        dimensions = _mapping(page.get("dimensions"))
        values: dict[str, float] = {}
        if dimensions is None or set(dimensions) != set(REQUIRED_DIMENSIONS):
            _append(reasons, f"page_dimensions_invalid:{index}")
            measurement_valid = False
        else:
            for dimension in REQUIRED_DIMENSIONS:
                score = _number(dimensions.get(dimension))
                if score is None or not 0 <= score <= 5:
                    _append(reasons, f"page_dimension_score_invalid:{index}:{dimension}")
                    measurement_valid = False
                    continue
                values[dimension] = score
                if dimension != "overall" and score < dimension_floor:
                    _append(reasons, f"page_dimension_floor_failed:{index}:{dimension}")
            if "overall" in values:
                overall = values["overall"]
                page_scores[index] = round(overall, 4)
                page_overalls.append((index, overall))
                if overall < worst_page_floor:
                    _append(reasons, f"worst_page_floor_failed:{index}")

        automatic_status = page.get("automaticStatus")
        human_status = page.get("humanStatus", "not-reviewed")
        if automatic_status not in {"passed", "failed"}:
            _append(reasons, f"automatic_visual_status_invalid:{index}")
            measurement_valid = False
        elif automatic_status == "failed":
            _append(reasons, f"automatic_visual_failed:{index}")
        if human_status not in {"passed", "failed", "not-reviewed"}:
            _append(reasons, f"human_visual_status_invalid:{index}")
            measurement_valid = False
        elif human_status == "failed":
            _append(reasons, f"human_visual_failed:{index}")
        contradiction = page.get("visualContradiction", False)
        if not isinstance(contradiction, bool):
            _append(reasons, f"visual_contradiction_flag_invalid:{index}")
            measurement_valid = False
        if contradiction is True or (
            automatic_status in {"passed", "failed"}
            and human_status in {"passed", "failed"}
            and automatic_status != human_status
        ):
            _append(reasons, f"visual_evidence_contradiction:{index}")
            measurement_valid = False

        issues = _sequence(page.get("issues", ()))
        if issues is None:
            _append(reasons, f"page_issues_invalid:{index}")
            measurement_valid = False
        else:
            for issue_index, issue_value in enumerate(issues):
                issue = _mapping(issue_value)
                if issue is None:
                    _append(reasons, f"page_issue_invalid:{index}:{issue_index}")
                    measurement_valid = False
                    continue
                severity = issue.get("severity")
                code = issue.get("code")
                if severity not in {"P0", "P1", "P2"} or not isinstance(code, str) or not code:
                    _append(reasons, f"page_issue_invalid:{index}:{issue_index}")
                    measurement_valid = False
                elif severity in {"P0", "P1"}:
                    _append(reasons, f"visual_issue_present:{index}:{severity}:{code}")

    correlation_valid, correlation_errors = _validate_human_correlation(raw.get("humanCorrelation"))
    if not correlation_valid:
        _append(reasons, "human_correlation_unverified")

    evidence_digest = raw.get("evidenceDigest")
    if not _digest_string(evidence_digest) or evidence_digest != visual_evidence_digest(raw):
        _append(reasons, "visual_evidence_digest_invalid")
        measurement_valid = False

    worst_page_number: int | None = None
    worst_page_score: float | None = None
    if page_overalls:
        worst_page_number, raw_worst_score = min(page_overalls, key=lambda item: (item[1], item[0]))
        worst_page_score = round(raw_worst_score, 4)
    percentage = None
    if correlation_valid and measurement_valid and len(page_overalls) == expected_count and expected_count:
        percentage = round(sum(score for _, score in page_overalls) / expected_count * 20.0, 2)

    reasons_tuple = tuple(reasons)
    acceptance = not reasons_tuple
    body = {
        "schemaVersion": DECISION_SCHEMA_VERSION,
        "acceptance": acceptance,
        "percentage": percentage,
        "reasons": list(reasons_tuple),
        "pageScores": {str(key): value for key, value in sorted(page_scores.items())},
        "worstPageNumber": worst_page_number,
        "worstPageScore": worst_page_score,
        "humanCorrelationStatus": "verified" if correlation_valid else "unverified",
        "humanCorrelationErrors": list(correlation_errors),
        "informativeMetricsUsedForAcceptance": False,
    }
    return VisualEvaluationDecision(
        acceptance=acceptance,
        percentage=percentage,
        reasons=reasons_tuple,
        page_scores=MappingProxyType(dict(sorted(page_scores.items()))),
        worst_page_number=worst_page_number,
        worst_page_score=worst_page_score,
        human_correlation_status="verified" if correlation_valid else "unverified",
        human_correlation_errors=correlation_errors,
        decision_digest=digest(body),
    )


__all__ = [
    "ARTIFACT_BINDING_SCHEMA_VERSION",
    "DECISION_SCHEMA_VERSION",
    "EVIDENCE_SCHEMA_VERSION",
    "HUMAN_CORRELATION_SCHEMA_VERSION",
    "METRIC_VERSION",
    "REQUIRED_DIMENSIONS",
    "VisualEvaluationDecision",
    "evaluate_visual_evidence",
    "visual_evidence_digest",
]
