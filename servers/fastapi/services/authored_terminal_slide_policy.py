"""D7 policy predicates for the terminal-slide fallback.

Pure, synchronous decisions the generation owner takes at three different
points of one attempt -- the durable Scene repair claim, the bounded repair
round, and the final gate -- kept in one module so those points cannot
disagree about a deck. The remake machinery itself lives in
``services/authored_terminal_slide_fallback.py``; this module never renders,
never calls a provider, and never touches a deck.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from services.authored_generation_preflight import over_budget_violations
from utils.get_env import is_authored_publish_flagged_slides_enabled
from utils.llm_calls.author_slide import EXACT_RECONSTRUCTION

LOGGER = logging.getLogger(__name__)

#: Reason recorded for a slide whose durable Scene repair claim was already
#: spent by an earlier attempt (``SceneRepairBudgetExhausted``).
REPAIR_BUDGET_EXHAUSTED_REASON = "composition_safety:repair_budget_exhausted"

#: Reason recorded for a slide whose one bounded gate repair produced a
#: candidate that could not be admitted, with nothing measured over budget.
GATE_REPAIR_NOT_ADMITTED_REASON = "composition_safety:gate_repair_not_admitted"


def terminal_fallback_active(
    *, profile: Any, reference_mode: str, source_reflow: bool
) -> bool:
    """Whether this deck may publish a flagged slide instead of failing.

    One predicate for every entry point, so the claim loop, the repair round
    and the fallback itself can never disagree about a deck: dropping a slide
    from the bounded round on one rule and then refusing to rescue it on
    another would publish a deck with an unrepaired, unflagged slide.

    A verbatim/exact deck may not lose a word, and a profile whose strict DOM
    gate is off has no terminal slide to rescue.
    """

    return (
        is_authored_publish_flagged_slides_enabled()
        and not source_reflow
        and reference_mode != EXACT_RECONSTRUCTION
        and bool(getattr(profile, "strict_dom_gate", False))
        and bool(getattr(profile, "allow_summarization", False))
    )


def declare_exhausted_repair_budget(
    slide_index: int,
    error: Any,
    *,
    profile: Any,
    reference_mode: str,
    source_reflow: bool,
) -> dict[str, object] | None:
    """D7: a slide whose bounded repair is already spent IS a terminal slide.

    ``claim_scene_repair`` raises ``SceneRepairBudgetExhausted`` when an earlier
    attempt already consumed this slide's single durable repair -- the ordinary
    shape of a resume after a lost lease, where every checkpoint is reused and
    no provider call has happened yet. Failing the deck there contradicts D7 for
    the same reason the final gate did: the slide is out of repairs, which is
    exactly the state the remake-then-flag fallback exists for.

    Returns the pre-declared terminal entry, or ``None`` when the caller must
    keep today's fail-closed raise.
    """

    if not terminal_fallback_active(
        profile=profile, reference_mode=reference_mode, source_reflow=source_reflow
    ):
        return None
    declared: dict[str, object] = {
        "index": int(slide_index),
        "reason": REPAIR_BUDGET_EXHAUSTED_REASON,
        "repeated": bool(getattr(error, "repeated", False)),
    }
    LOGGER.warning(
        "[authored] repair budget exhausted, declaring terminal slide=%d repeated=%s",
        int(slide_index) + 1,
        declared["repeated"],
    )
    return declared


def gate_repair_rejection_is_terminal_slide(
    *,
    enabled: bool,
    candidate_violations: Sequence[str],
    original_violations: Sequence[str],
) -> bool:
    """D7: whether an unadmitted bounded gate repair is a terminal slide.

    The bounded round rejects a candidate whenever it still carries a capacity
    violation, and the original it would fall back to already failed. That is a
    genuine dead end only when something is measured over budget: text that
    cannot be shortened to fit. A violation that names an element -- a wrapper
    collapsed to width 0 takes every child of it with it -- is a layout defect,
    and one more layout is exactly what the remake-then-flag fallback does.

    ``over_budget_violations`` counts an unmeasured violation as over budget,
    so a slide with no browser metrics stays fail-closed.
    """

    if not enabled:
        return False
    return not over_budget_violations((*candidate_violations, *original_violations))


def declare_unadmitted_gate_repair(slide_index: int) -> dict[str, object]:
    """The terminal entry for a slide whose bounded gate repair was rejected."""

    LOGGER.warning(
        "[authored] gate repair not admitted, declaring terminal slide=%d",
        int(slide_index) + 1,
    )
    return {"index": int(slide_index), "reason": GATE_REPAIR_NOT_ADMITTED_REASON}


def flagged_slide_absorbs_capacity(
    slide_index: int, violations: Sequence[str], *, flagged: Collection[int]
) -> bool:
    """Whether the final gate may publish this slide's capacity violations.

    Only for a slide the terminal fallback already flagged, and only for
    violations that name an element: the deck carries the flag and its evidence
    instead of dying on the layout defect the fallback was unable to remake.
    """

    return int(slide_index) in {int(index) for index in flagged} and not (
        over_budget_violations(violations)
    )


@dataclass(frozen=True, slots=True)
class RepairClaimOutcome:
    """What claiming one durable repair per flagged slide produced."""

    claims: dict[int, object] = field(default_factory=dict)
    #: Slides that still enter the bounded deterministic/provider round.
    flagged: list[int] = field(default_factory=list)
    #: Slides declared terminal because their repair was already spent.
    predeclared: list[dict[str, object]] = field(default_factory=list)
    #: ``(slide_index, error)`` the caller must fail the deck on, because this
    #: deck may not publish a flagged slide. ``None`` when nothing blocks.
    blocked: tuple[int, Any] | None = None


def claim_bounded_repairs(
    flagged: Sequence[int],
    *,
    claim: Callable[[int], object],
    budget_exhausted: type[BaseException],
    profile: Any,
    reference_mode: str,
    source_reflow: bool,
) -> RepairClaimOutcome:
    """Claim the single durable Scene repair for each flagged slide.

    A slide whose claim is already spent (the resume-after-lost-lease shape) is
    declared terminal instead of killing the deck, and is removed from the
    bounded round: it has no claim, so nothing may rewrite it and its
    html/png/metrics stay the frozen pre-repair triple. On a deck that may not
    publish flagged, the first exhausted claim is returned as ``blocked`` and
    the caller keeps today's fail-closed raise.
    """

    claims: dict[int, object] = {}
    remaining: list[int] = []
    predeclared: list[dict[str, object]] = []
    for index in flagged:
        try:
            claims[index] = claim(index)
        except budget_exhausted as error:
            declared = declare_exhausted_repair_budget(
                index,
                error,
                profile=profile,
                reference_mode=reference_mode,
                source_reflow=source_reflow,
            )
            if declared is None:
                return RepairClaimOutcome(blocked=(index, error))
            predeclared.append(declared)
            continue
        remaining.append(index)
    return RepairClaimOutcome(
        claims=claims, flagged=remaining, predeclared=predeclared
    )


def recover_residual_repair_round(
    error: Any, *, enabled: bool
) -> tuple[list[str], list[bytes], list[int]] | None:
    """D7: the bounded repair round's own bound state, or ``None`` to fail closed.

    ``AuthoredVqaResidualDomIssuesError`` is raised AFTER the round has already
    written every successful repair into its own ``htmls``/``pngs`` lists (see
    ``utils/llm_calls/author_vision_qa.py``: ``htmls[i] = new_html`` happens only
    on the success branch, so a residual slide still carries the exact input it
    was handed). Recovering that state is what lets the deck-level outcome stop
    depending on which of two code paths reported the same residual defect: the
    round's residual and the final gate's residual now both reach
    ``resolve_terminal_gate_slides``.

    No triple is mixed here. The returned HTML never travels with these pixels:
    the caller re-renders every candidate through ``_bind_bounded_repair_evidence``
    and admits one whole (html, png, metrics) capture, or keeps the frozen
    pre-repair triple. The pngs are returned only so the round's existing
    length invariant can still be checked.

    ``enabled`` is ``terminal_fallback_active`` for this deck, resolved by the
    caller. Source reflow is fail-closed inside it: a verbatim deck may not
    publish with a flagged slide, and its partial repairs already have their own
    checkpoint path (``_checkpoint_source_reflow_vqa_repairs``).
    """

    if not enabled:
        return None
    htmls = [str(html) for html in getattr(error, "htmls", ())]
    pngs = [bytes(png) for png in getattr(error, "pngs", ())]
    fixed = [int(index) for index in getattr(error, "fixed", ())]
    if not htmls:
        # Nothing was bound; there is no partial state to keep.
        return None
    LOGGER.warning(
        "[authored] recovering residual gate-repair round slides=%d fixed=%s",
        len(htmls),
        fixed,
    )
    return htmls, pngs, fixed


def partition_residual_failures(
    residual_gate_failures: Sequence[Mapping[str, object]],
    flagged_slides: Sequence[Mapping[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Split final-gate residuals into ``(blocking, accepted_as_flagged)``.

    Only the blocking half fails the deck. The accepted half keeps its measured
    issue rows so the published quality-gate receipt still carries the evidence
    behind every flag rather than only the flag's reason string.
    """

    accepted = {
        int(entry["index"])
        for entry in flagged_slides
        if isinstance(entry, Mapping) and isinstance(entry.get("index"), int)
    }
    blocking: list[dict[str, object]] = []
    flagged: list[dict[str, object]] = []
    for item in residual_gate_failures:
        if not isinstance(item, Mapping):
            continue
        target = flagged if int(item.get("slide_index", -1)) in accepted else blocking
        target.append(dict(item))
    return blocking, flagged


__all__ = [
    "GATE_REPAIR_NOT_ADMITTED_REASON",
    "REPAIR_BUDGET_EXHAUSTED_REASON",
    "RepairClaimOutcome",
    "claim_bounded_repairs",
    "declare_exhausted_repair_budget",
    "declare_unadmitted_gate_repair",
    "flagged_slide_absorbs_capacity",
    "gate_repair_rejection_is_terminal_slide",
    "partition_residual_failures",
    "recover_residual_repair_round",
    "terminal_fallback_active",
]
