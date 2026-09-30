"""Which half of the authored repair loop reviews a slide, and how the two meet.

The repair round in ``services.generation_core.authoring.author_vision_qa`` has two reviewers: a
deterministic critique projected from the measured DOM, and a multimodal
vision-QA model call over the rendered image.  The model call is the expensive
one, so it runs only when the measured DOM has nothing actionable to say.

That cascade was unreachable.  Replayed offline over 905 stored bench slides
(41 runs under ``artifacts/``), the ``contrast`` detector fired on 97.79% of
them, so the deterministic critique was never empty; on the 24-slide run 48
deck 0 of 24 slides could reach the model, and 20 of the 24 were held by
``contrast`` alone -- a category that no other consumer treats as a blocker
(it is absent from ``DETECTED_GEOMETRY_CATEGORIES``).  The "VQA fixed N slides"
figures from bench runs 10-16 were therefore the deterministic critique alone.

This module states the rule the registry already implies: only a category that
can supersede the model call does, and a non-blocking finding travels to the
model as briefing material instead of consuming the repair budget on its own.
"""

from __future__ import annotations

from typing import Any

from utils.authored_dom_categories import deterministic_findings_supersede_vqa
from utils.llm_calls.critique_slide import SlideCritique


def deterministic_critique_supersedes_vqa(report: Any) -> bool:
    """Whether this DOM report is reason enough to skip the vision-QA call.

    Unavailable telemetry never is: an unmeasured slide is exactly the case the
    model exists for, and ``DomQualityReport.needs_attention`` already routes it
    there.  Otherwise the category registry decides.
    """

    if report.telemetry_status != "available":
        return False
    return deterministic_findings_supersede_vqa(
        issue.category for issue in report.issues
    )


def with_advisory_findings(
    critique: SlideCritique | None, advisory: SlideCritique | None
) -> SlideCritique | None:
    """Carry non-superseding DOM findings into a critique the model just made.

    They are briefing material, not a trigger: a slide the model calls clean is
    not re-authored because its accent colour measures 3.97:1 against white.
    """

    if critique is None or not critique.needs_fix or advisory is None:
        return critique
    seen = {(issue.type, issue.severity, issue.detail) for issue in critique.issues}
    issues = list(critique.issues)
    for issue in advisory.issues:
        key = (issue.type, issue.severity, issue.detail)
        if key not in seen:
            issues.append(issue)
            seen.add(key)
    return SlideCritique(needs_fix=True, issues=issues[:8])


__all__ = [
    "deterministic_critique_supersedes_vqa",
    "with_advisory_findings",
]
