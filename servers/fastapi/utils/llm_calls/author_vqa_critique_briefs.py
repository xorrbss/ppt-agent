"""The critique text the authored vision-QA loop hands a re-author or repair.

Pure projections, split out of ``author_vision_qa`` so that module's growth
stays in check: the re-author feedback paragraph for one critique, and the
measured DOM report expressed as the critique the repair round reads.
"""

from __future__ import annotations

from typing import Any

from utils.authored_dom_categories import critique_type_for_category
from utils.llm_calls.critique_slide import CritiqueIssue, SlideCritique


def _feedback(critique: SlideCritique, *, exact_reconstruction: bool = False, planned_images: bool = False) -> str:
    issues = (
        "; ".join(f"{x.type} — {x.detail}" for x in critique.issues) or "visual problems"
    )
    if exact_reconstruction:
        return (
            "A rendered preview of THIS exact reconstruction had these visual problems: "
            f"{issues}. Re-author it against the attached source image and fix only "
            "unintended implementation defects. Preserve source text character-for-"
            "character, including line breaks; preserve exact typography and geometry; "
            "and do not remove intentional overlap/cropping, restructure the layout, "
            "shorten content, or make discretionary design improvements."
        )
    return (
        "A rendered preview of THIS slide had these visual problems: "
        f"{issues}. Re-author the slide to fix them: keep ALL content within the "
        "declared canvas frame (no unintended overflow or clipping), "
        + ("preserve planned image-group overlap while protecting text and critical image regions, ensure "
           if planned_images else "remove unintended element overlap, ensure ") +
        "strong contrast, delete any placeholder text, avoid unnecessarily shrinking text "
        "until it becomes difficult to read, and rebalance the layout — while "
        "keeping the same role, language and shared design system."
    )


_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


def deterministic_dom_critique(report: Any) -> SlideCritique | None:
    """Project a measured DOM report into the critique the repair round reads.

    Bench run 36 (2026-09-05) lost a whole deck here: a dict literal mapped the
    category, and the two detectors ``f0d2b126c`` registered were not in it, so
    slides 3..17 raised ``KeyError`` instead of getting a brief.  Both lookups
    are total now (``utils.authored_dom_categories``).
    """

    if report.telemetry_status != "available" or not report.issues:
        return None
    ranked = sorted(
        report.issues, key=lambda item: _SEVERITY_RANK.get(item.severity, 1)
    )[:8]
    return SlideCritique(
        needs_fix=True,
        issues=[
            CritiqueIssue(
                type=critique_type_for_category(item.category),
                severity=item.severity if item.severity in _SEVERITY_RANK else "medium",
                detail=(
                    f"DOM {item.category}: {item.detail}; targets: "
                    f"{', '.join(item.qids) or 'slide'}"
                )[:200],
            )
            for item in ranked
        ],
    )


__all__ = ["_feedback", "deterministic_dom_critique"]
