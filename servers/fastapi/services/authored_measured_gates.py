"""Post-render measured gates that ride the capacity repair road.

Both gates measure the first exact render and report a defect string the
existing ladder turns into one bounded repair round; neither blocks publish on
its own. They live here, beside each other, so the pipeline's quality
evaluation stays one call regardless of how many measured gates exist.
"""

from __future__ import annotations

from typing import Any, Mapping

from utils.authored_content_lock import content_lock_gate_issue
from utils.authored_fragmentation_gate import (
    FRAGMENTATION_CATEGORY,
    fragmentation_violations,
)


def measured_gate_issues(
    *,
    index: int,
    brief: str,
    browser_metrics: Mapping[str, Any] | None,
    capacity_plan: Any | None,
    fit_report: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Issues from every knob-gated measured gate; empty while the knobs are off."""

    issues: list[dict[str, Any]] = []
    role = capacity_plan.at(index).role if capacity_plan is not None else "SLIDE"
    lock_issue = content_lock_gate_issue(brief, browser_metrics, role=role)
    if lock_issue is not None:
        issues.append(lock_issue)
    issues.extend(
        {"category": FRAGMENTATION_CATEGORY, "severity": "error", "message": violation}
        for violation in fragmentation_violations(browser_metrics, fit_report=fit_report)
    )
    return issues
