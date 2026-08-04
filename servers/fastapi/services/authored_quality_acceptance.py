"""Conservative acceptance policy for authored quality-review candidates."""

from utils.authored_dom_quality import DomQualityReport, has_dom_quality_regression
from utils.llm_calls import author_vision_qa
from utils.llm_calls.critique_slide import SlideCritique, StyleComparisonCritique

_INDETERMINATE_PREFIX = "INDETERMINATE:"


def critique_unusable_reason(critique: SlideCritique | None) -> str | None:
    if critique is None:
        return "unavailable"
    if critique.needs_fix != bool(critique.issues):
        return "inconsistent"
    if any(
        issue.detail.lstrip().upper().startswith(_INDETERMINATE_PREFIX)
        for issue in critique.issues
    ):
        return "indeterminate"
    return None


def critique_score(critique: SlideCritique | None) -> tuple[int, int, int] | None:
    """Return a deterministic defect score, or ``None`` when comparison is unsafe."""
    if critique_unusable_reason(critique) is not None:
        return None
    severity = {"high": 3, "medium": 2, "low": 1}
    return (
        int(critique.needs_fix),
        sum(severity.get(issue.severity, 3) for issue in critique.issues),
        len(critique.issues),
    )


def acceptance_decision(
    before: SlideCritique | None, after: SlideCritique | None
) -> tuple[bool, str]:
    """Accept exact HTML only after a strictly better, determinate comparison."""
    before_error = critique_unusable_reason(before)
    if before_error is not None:
        return False, f"initial_{before_error}"
    after_error = critique_unusable_reason(after)
    if after_error is not None:
        return False, f"final_{after_error}"

    before_score = critique_score(before)
    after_score = critique_score(after)
    assert before_score is not None and after_score is not None
    if after_score < before_score:
        return True, "improved"
    if after_score == before_score:
        return False, "unchanged"
    return False, "worsened"


def style_comparison_has_regression(critique: SlideCritique | None) -> bool:
    """Return whether a STYLE_REFERENCE comparison identified a new regression."""
    if critique is None:
        return False
    prefix = str(
        getattr(
            author_vision_qa,
            "STYLE_COMPARISON_REGRESSION_PREFIX",
            "REGRESSION:",
        )
    ).upper()
    return any(
        issue.detail.lstrip().upper().startswith(prefix) for issue in critique.issues
    )


def style_comparison_improvement_decision(
    critique: SlideCritique | None,
) -> tuple[bool, str, bool]:
    """Return (accepted, reason, conclusive) for paired visual evidence.

    Legacy comparison payloads prove only absence of a named regression. They do not
    prove that the candidate is better, so SlideSpec publication treats them as
    inconclusive instead of silently accepting a tie.
    """
    unusable = critique_unusable_reason(critique)
    if unusable is not None:
        return False, f"vqa.{unusable}", False
    if not isinstance(critique, StyleComparisonCritique):
        return False, "vqa.improvement_unavailable", False

    required = {
        "target-defect",
        "content",
        "readability",
        "hierarchy",
        "alignment",
        "spacing",
        "style",
    }
    dimensions = {item.dimension: item.verdict for item in critique.dimensions}
    if set(dimensions) != required or len(critique.dimensions) != len(required):
        return False, "vqa.improvement_unavailable", False
    if style_comparison_has_regression(critique) or critique.preference == "baseline":
        return False, "vqa.visual_regression", True
    if any(verdict == "worse" for verdict in dimensions.values()):
        return False, "vqa.visual_regression", True
    if (
        critique.preference == "indeterminate"
        or critique.confidence < 0.75
        or any(verdict == "indeterminate" for verdict in dimensions.values())
    ):
        return False, "vqa.indeterminate", False
    if critique.preference != "candidate" or not critique.target_improved:
        return False, "vqa.not_improved", True
    if dimensions["target-defect"] != "better":
        return False, "vqa.not_improved", True
    return True, "vqa.improved", True


def style_comparison_quality_gain(critique: SlideCritique | None) -> float | None:
    """Return a conservative, normalized gain for an accepted paired comparison.

    The score is deliberately unavailable for a tie, regression, incomplete
    evidence, or a legacy payload.  This prevents candidate selection from turning
    uncertainty into an apparent improvement.
    """
    accepted, _reason, _conclusive = style_comparison_improvement_decision(critique)
    if not accepted or not isinstance(critique, StyleComparisonCritique):
        return None
    weights = {
        "target-defect": 0.40,
        "content": 0.05,
        "readability": 0.15,
        "hierarchy": 0.10,
        "alignment": 0.10,
        "spacing": 0.10,
        "style": 0.10,
    }
    verdicts = {item.dimension: item.verdict for item in critique.dimensions}
    raw = sum(weight for name, weight in weights.items() if verdicts[name] == "better")
    return round(raw * critique.confidence, 6)


def style_comparison_quality_interval(
    critique: SlideCritique | None,
) -> tuple[float, float] | None:
    """Return a deterministic confidence interval for paired-VQA quality gain."""

    gain = style_comparison_quality_gain(critique)
    if gain is None or not isinstance(critique, StyleComparisonCritique):
        return None
    raw = gain / critique.confidence if critique.confidence > 0 else 0
    uncertainty = raw * (1 - critique.confidence)
    return round(max(0, gain - uncertainty), 6), round(min(1, gain + uncertainty), 6)


def style_acceptance_decision(
    before: SlideCritique | None, after: SlideCritique | None
) -> tuple[bool, str]:
    """Accept a style repair only when it improves without a new regression."""
    before_error = critique_unusable_reason(before)
    if before_error is not None:
        return False, f"initial_{before_error}"
    after_error = critique_unusable_reason(after)
    if after_error is not None:
        return False, f"final_{after_error}"
    if style_comparison_has_regression(after):
        return False, "regression"
    return acceptance_decision(before, after)


def is_improved(before: SlideCritique | None, after: SlideCritique | None) -> bool:
    return acceptance_decision(before, after)[0]


def local_acceptance_decision(
    baseline: DomQualityReport,
    candidate: DomQualityReport,
) -> tuple[bool, str]:
    """Fail closed unless deterministic defects strictly improve without regression.

    Image VQA is necessary for visual truth, but it cannot override browser-observed
    clipping, overflow, or other deterministic defects.  The candidate therefore
    has to prove complete telemetry, no new/worse issue bucket, and a strict
    reduction in the severity profile.  A high-severity issue present in the
    baseline may never survive acceptance.
    """
    if baseline.telemetry_status != "available":
        return False, "local_baseline_unavailable"
    if candidate.telemetry_status != "available":
        return False, "local_candidate_unavailable"
    if has_dom_quality_regression(baseline, candidate):
        return False, "local_regression"
    if not baseline.issues:
        # VQA-only defects can still be repaired.  With no deterministic baseline
        # defect, an available clean candidate satisfies the local no-regression
        # gate; visual strict improvement is decided by the image comparison.
        return True, "local_clean"

    baseline_high_keys = {
        (issue.category, issue.qids)
        for issue in baseline.issues
        if issue.severity == "high"
    }
    if any(
        issue.severity == "high" and (issue.category, issue.qids) in baseline_high_keys
        for issue in candidate.issues
    ):
        return False, "local_high_remaining"

    def severity_profile(report: DomQualityReport) -> tuple[int, int, int, int]:
        counts = {"high": 0, "medium": 0, "low": 0}
        for issue in report.issues:
            counts[issue.severity] += 1
        return counts["high"], counts["medium"], counts["low"], len(report.issues)

    if severity_profile(candidate) >= severity_profile(baseline):
        return False, "local_not_improved"
    return True, "improved"
