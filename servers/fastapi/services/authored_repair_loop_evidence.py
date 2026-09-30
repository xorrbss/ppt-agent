"""Bounded evidence for the single repair loop's one model prompt.

Nothing here judges a slide. Every line is a projection of a finding some
existing predicate already produced -- the measured DOM report, the source
visibility inspection, the candidate evaluator's own reasons, or the vision
review -- folded into the bounded brief the loop's single prompt family
carries. The loop adds no judge and no predicate of its own.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

#: Evidence lines carried into one repair prompt. The authoring prompt already
#: carries the block table; the brief stays short enough to read beside it.
MAX_EVIDENCE_ITEMS = 8

#: Per-line cap, matching ``CritiqueIssue.detail`` so nothing is silently
#: truncated away after the fact (the defect that lost every remake brief).
MAX_EVIDENCE_CHARS = 200


def _line(prefix: str, detail: str) -> str:
    text = " ".join(str(detail or "").split())
    return f"{prefix}: {text}"[:MAX_EVIDENCE_CHARS]


def issue_evidence(issues: Iterable[Any]) -> tuple[str, ...]:
    """Project measured DOM/source issues into bounded evidence lines."""

    lines: list[str] = []
    for issue in issues:
        category = str(getattr(issue, "category", "") or "issue")
        detail = str(getattr(issue, "detail", "") or "")
        qids = tuple(str(qid) for qid in getattr(issue, "qids", ()) or ())
        target = f" targets: {', '.join(qids[:4])}" if qids else ""
        lines.append(_line(category, f"{detail}{target}"))
    return tuple(lines)


def critique_evidence(critique: Any) -> tuple[str, ...]:
    """Project one vision-review critique into the same bounded lines."""

    if critique is None or not getattr(critique, "needs_fix", False):
        return ()
    return tuple(
        _line(str(getattr(issue, "type", "") or "visual"), str(getattr(issue, "detail", "") or ""))
        for issue in getattr(critique, "issues", ()) or ()
    )


def reason_evidence(reasons: Iterable[Any]) -> tuple[str, ...]:
    return tuple(_line("rejected", str(reason)) for reason in reasons)


def merge_evidence(*groups: Sequence[str]) -> tuple[str, ...]:
    """Stable, deduplicated and bounded; the first mention of a line wins."""

    merged: dict[str, None] = {}
    for group in groups:
        for line in group:
            if line:
                merged.setdefault(line, None)
    return tuple(merged)[:MAX_EVIDENCE_ITEMS]


#: Restated for a slide whose authoring prompt carries the placeholder block
#: table: the model never writes source text, it places the table's tokens.
TOKEN_TEXT_RULE = (
    "You MAY NOT write, retype, shorten, summarize, translate or invent any "
    "source text: place every source occurrence through its placeholder token "
    "exactly once, exactly as the source contract above requires."
)

#: Restated for a slide whose authoring prompt carries the source text itself
#: (``AUTHORED_VERBATIM_PLACEHOLDER_AUTHORING`` off). There is no token contract
#: to refer to, so the boundary is the one the source-locked author brief uses.
VERBATIM_TEXT_RULE = (
    "You MAY NOT shorten, summarize, translate, merge or invent any source text: "
    "reproduce every source text block exactly as it was given to you, including "
    "punctuation and ordering, and give every source block exactly one visible owner."
)


def source_text_rule(index: int) -> str:
    """Derive the repair's text boundary from the authoring prompt's own contract.

    ``placeholder_prompt_for_slide`` is the single source that decides whether
    this slide's authoring prompt carries a placeholder block table, so asking
    it here keeps the repair brief and the authoring brief consistent by
    construction under every combination of the rollout knobs.  Telling a model
    to place tokens that were never defined, while handing it the source text,
    is the contradiction this removes.
    """

    from services.source_content.placeholder.prompt import placeholder_prompt_for_slide

    return TOKEN_TEXT_RULE if placeholder_prompt_for_slide(index) else VERBATIM_TEXT_RULE


def repair_correction_hint(evidence: Sequence[str], *, role: str, index: int) -> str:
    """The loop's single correction brief: layout may change, text may not.

    The source contract (the block table and the placeholder token rules, when
    they exist) is appended to the authoring prompt by the gate's own prompt
    path, so this brief only has to state what this call is for and restate the
    one boundary the repair may not cross -- in the same terms that prompt uses.
    """

    measured = "; ".join(evidence[:MAX_EVIDENCE_ITEMS]) or "measured layout defects"
    return (
        "This slide was rendered and measured, and it was rejected. Measured "
        f"defects: {measured}.\n"
        f"Re-author THIS {role or 'content'} slide to remove them. You MAY move, "
        "resize, restyle, regroup and restructure the layout, and you may change "
        "typography and spacing. Keep every element inside the 1280x720 frame with "
        "no overflow, clipping or overlap, and keep the same role, language, header "
        "contract and design system as the rest of the deck.\n"
        f"{source_text_rule(index)} Keep every source picture the slide was offered."
    )


async def vision_evidence_for_flagged(
    *,
    flagged: Sequence[int],
    pngs: Sequence[bytes],
    contents: Sequence[str],
    reference_mode: str,
    job_id: Any = None,
    attempt_number: int = 0,
) -> dict[int, Any]:
    """Run the visual review over the flagged slides, for evidence only.

    A review that fails produces no evidence and no failure: the loop still has
    the measured findings, and a missing critique may not end a deck.
    """

    if not flagged:
        return {}
    from services.provider_usage_instrumentation import provider_usage_scope
    from services.generation_core.authoring.author_vision_qa import review_slides_for_evidence

    try:
        with provider_usage_scope(
            job_id=job_id,
            operation="authored_generation",
            stage="vqa",
            attempt_number=attempt_number,
        ):
            return await review_slides_for_evidence(
                [pngs[index] for index in flagged],
                slide_indices=list(flagged),
                contexts=[contents[index] for index in flagged],
                reference_mode=reference_mode,
            )
    except Exception as error:  # noqa: BLE001 - evidence is optional input
        import logging

        logging.getLogger(__name__).warning(
            "[authored-repair-loop] vision evidence unavailable (%s)",
            type(error).__name__,
        )
        return {}


def visual_review_evidence_only(
    *,
    reviewed_indices: Sequence[int],
    model_reviewed: int,
    repair_loop_model_calls: int = 0,
    repair_loop_admitted_indices: Sequence[int] = (),
) -> dict[str, object]:
    """Receipt evidence for a vision review that produced no HTML.

    The review reports only what it reviewed (B9): it writes no HTML and
    decides no slide's fate, so a detail's own ``succeeded`` means "this
    slide's evidence pass completed", never "the slide was fixed" -- there is
    nothing here for the review to have fixed. Whatever the repair loop
    admitted is attributed to the loop alone, under ``repairLoop``, never
    folded into the review's own accounting; that conflation was the
    over-report this removes (evidence-only VQA receipts previously reported
    a slide the *loop* fixed as a vision-review success).
    """

    from services.authored_visual_review_evidence import visual_review_outcome

    details = [
        {
            "slide_index": int(index),
            "path": "evidence_only",
            "attempted": True,
            "succeeded": True,
            "critique_source": "vision_review",
        }
        for index in reviewed_indices
    ]
    outcome = visual_review_outcome(
        # Slides that entered the review stage, not the deck: with the loop on
        # only the flagged slides are reviewed, and claiming the whole deck was
        # is exactly the over-report this programme is removing.
        slide_count=len(details),
        fixed=(),
        accepted_fixed=(),
        repair_details=details,
        review_stats={"vqaReviewedSlideCount": int(model_reviewed)},
    )
    outcome["repairLoop"] = {
        "modelCalls": int(repair_loop_model_calls),
        "admitted": sorted({int(index) for index in repair_loop_admitted_indices}),
    }
    outcome["visionReview"] = {"slidesReviewed": len(details)}
    return outcome


def evidence_for_slide(
    *,
    html: str,
    metrics: Mapping[str, Any] | None,
    inspect: Any,
    blocking: Any,
    reference_mode: str,
    strict_structural_off_canvas: bool,
    source_issues: Iterable[Any] = (),
    critique: Any = None,
    reasons: Iterable[Any] = (),
) -> tuple[str, ...]:
    """Measure the current bytes once and fold every finding into one brief."""

    measured: tuple[str, ...] = ()
    if metrics is not None:
        report = inspect(
            html,
            metrics,
            reference_mode=reference_mode,
            strict_structural_off_canvas=strict_structural_off_canvas,
        )
        if getattr(report, "telemetry_status", "") == "available":
            measured = issue_evidence(blocking(report))
    return merge_evidence(
        measured,
        issue_evidence(source_issues),
        critique_evidence(critique),
        reason_evidence(reasons),
    )


__all__ = [
    "MAX_EVIDENCE_CHARS",
    "MAX_EVIDENCE_ITEMS",
    "TOKEN_TEXT_RULE",
    "VERBATIM_TEXT_RULE",
    "critique_evidence",
    "evidence_for_slide",
    "issue_evidence",
    "merge_evidence",
    "reason_evidence",
    "repair_correction_hint",
    "source_text_rule",
    "vision_evidence_for_flagged",
    "visual_review_evidence_only",
]
