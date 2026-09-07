"""Post-render measured gates that ride the capacity repair road.

A measured gate inspects the first exact render and reports a defect string
the existing ladder turns into one bounded repair round; it never blocks
publish on its own. They live here so the pipeline's quality evaluation stays
one call regardless of how many measured gates exist.
"""

from __future__ import annotations

from typing import Any, Mapping

from utils.authored_fragmentation_gate import (
    FRAGMENTATION_CATEGORY,
    fit_font_floor_px,
    fragmentation_violations,
)


def measured_font_floor_px() -> float:
    """The type floor the measured gates resolve; the pipeline clamps to it too."""

    return fit_font_floor_px()


def measured_gate_issues(
    *,
    browser_metrics: Mapping[str, Any] | None,
    fit_report: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Issues from every knob-gated measured gate; empty while the knobs are off."""

    return [
        {"category": FRAGMENTATION_CATEGORY, "severity": "error", "message": violation}
        for violation in fragmentation_violations(browser_metrics, fit_report=fit_report)
    ]
