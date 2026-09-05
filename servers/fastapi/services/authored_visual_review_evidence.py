"""Visual-review (VQA) outcome evidence for the authored quality receipts.

QUAL-04: the V3 receipt used to record ``visualReview: passed / fullDeck``
from the request's ``vision_qa`` flag, i.e. from what was asked for, not from
what happened. This module derives the evidence from the actual outcome of
``revise_authored_deck``:

* VQA did not run            -> ``not_assessed`` with a reason code, in the
                                same style the V2 evidence already uses;
* ran, no residual issue     -> ``passed`` (``fullDeck`` when every slide was
                                reviewed);
* ran, residual issues left  -> ``failed``: a critique that needed a fix which
                                was not applied, a fix the capacity admission
                                rejected, or the reviser raising.

Every payload carries a short count summary so a reader can tell how many
slides were reviewed, fixed and left with residual issues.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

NOT_RUN_REASON_CODE = "visual_checker_not_run"


def visual_review_not_assessed() -> dict[str, object]:
    """The evidence for a deck whose caller did not request VQA."""
    return {"status": "not_assessed", "reasonCode": NOT_RUN_REASON_CODE, "fullDeck": False}


def visual_review_outcome(
    *,
    slide_count: int,
    fixed: Iterable[int],
    accepted_fixed: Iterable[int],
    repair_details: Iterable[Mapping[str, object]] = (),
    error: BaseException | None = None,
) -> dict[str, object]:
    """Fold one ``revise_authored_deck`` run into receipt evidence.

    ``fixed`` is what the reviser changed, ``accepted_fixed`` what the capacity
    admission kept, ``repair_details`` the per-slide correction records the
    reviser produced for every critique that needed a fix.
    """
    fixed_set = {int(index) for index in fixed}
    accepted = {int(index) for index in accepted_fixed}
    residual: set[int] = set(fixed_set - accepted)
    for detail in repair_details:
        if not isinstance(detail, Mapping) or detail.get("succeeded") is True:
            continue
        index = detail.get("slide_index", detail.get("local_index"))
        if isinstance(index, int) and not isinstance(index, bool):
            residual.add(index)
    status = "failed" if error is not None or residual else "passed"
    payload: dict[str, object] = {
        "status": status,
        "fullDeck": status == "passed" and slide_count > 0,
        "reviewedSlideCount": int(slide_count),
        "fixedSlideCount": len(fixed_set),
        "acceptedFixedSlideCount": len(accepted & fixed_set),
        "residualSlideCount": len(residual),
        "residualSlideIndices": sorted(residual),
    }
    if error is not None:
        payload["reasonCode"] = f"visual_review_error:{type(error).__name__}"
    return payload


def seal_visual_review_evidence(
    visual_review: Mapping[str, object] | None, *, tier: str
) -> dict[str, object]:
    """Project outcome evidence onto the V3 receipt's ``visualReview`` slot.

    ``passed`` and ``fullDeck`` can only come from a run that actually passed.
    A deck that was never reviewed is ``not_required`` for a T0 evaluation and
    ``unavailable`` for the risk-triggered tiers, so the V3 gate blocks rather
    than silently opting out.
    """
    review = dict(visual_review or visual_review_not_assessed())
    status = str(review.get("status") or "not_assessed")
    if status not in {"passed", "failed"}:
        status = "not_required" if tier == "T0" else "unavailable"
    review["status"] = status
    review["fullDeck"] = status == "passed" and review.get("fullDeck") is True
    return review


__all__ = [
    "NOT_RUN_REASON_CODE",
    "seal_visual_review_evidence",
    "visual_review_not_assessed",
    "visual_review_outcome",
]
