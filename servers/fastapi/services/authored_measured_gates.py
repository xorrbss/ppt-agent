"""Post-render measured gates that ride the capacity repair road.

A measured gate inspects the first exact render and reports a defect string
the existing ladder turns into one bounded repair round; it never blocks
publish on its own. They live here so the pipeline's quality evaluation stays
one call regardless of how many measured gates exist.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from utils.authored_fragmentation_gate import (
    FRAGMENTATION_CATEGORY,
    fit_font_floor_px,
    fragmentation_repair_instruction,
    fragmentation_violations,
)

#: What a remake is told when every defect on the slide is fragmentation.
#: The generic lower-density hint asks for fewer bullets, which is the one
#: move the gate exists to forbid.
FRAGMENTATION_REMAKE_HINT = (
    "Rebuild this slide as a fresh composition with FEWER, LARGER text blocks: "
    "merge labels, kickers, chips and small boxes; keep every fact of CONTENT; "
    "do not shrink type below the floor."
)


def measured_font_floor_px() -> float:
    """The type floor the measured gates resolve; the pipeline clamps to it too."""

    return fit_font_floor_px()


def measured_gate_issues(
    *,
    browser_metrics: Mapping[str, Any] | None,
    fit_report: Mapping[str, Any] | None,
    role: str = "SLIDE",
) -> list[dict[str, Any]]:
    """Issues from every knob-gated measured gate; empty while the knobs are off.

    ``detail`` carries the repair instruction because the remake brief quotes
    an issue's ``detail`` (falling back to the raw ``message`` token), and the
    remake is the only round that can merge boxes: the local patch round
    returns ``full_reauthor`` for structural change.
    """

    return [
        {
            "category": FRAGMENTATION_CATEGORY,
            "severity": "error",
            "message": violation,
            "detail": fragmentation_repair_instruction(role, violation) or violation,
        }
        for violation in fragmentation_violations(browser_metrics, fit_report=fit_report)
    ]


def remake_density_hint(defects: Sequence[Any], *, default: str) -> str:
    """The composition hint a remake gets: merge boxes when only fragmentation is wrong."""

    categories = [str(getattr(defect, "category", "") or "") for defect in defects]
    if categories and all(category == FRAGMENTATION_CATEGORY for category in categories):
        return FRAGMENTATION_REMAKE_HINT
    return default
