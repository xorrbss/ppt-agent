"""S1/Q3: scheduling and budgets for the remake-first repair round.

``docs/authored-sol-quality-first-design-20260908.md``. On gpt-5.6-sol one
authoring call takes ~130 s and bench run 48 spent 2,607 s of a 4,325 s deck
in the repair stage because its six remakes ran one after another while the
authoring wave in front of them had run four calls at a time. This module is
the pure planning half of the fix: it decides, BEFORE any call is launched,
which flagged slides are remade, from which budget, in which order, and at
what width; the round in ``services/authored_repair_round_order.py`` then
runs the launches under that width and applies the admitted results in slide
order, so the deck state never depends on which call finished first.

Budgets (Q3). A slide whose every defect is ``fragmentation`` draws from
``MAX_FRAGMENTATION_REMAKES_PER_DECK``; every other flagged slide draws from
the shared ``MAX_TERMINAL_REMAKES_PER_DECK`` the terminal round also counts
against. A slide with both kinds of defect is an overflow slide. The split
exists so the fragmentation gate cannot starve a real overflow slide of the
shared eight.

Order (Q3). Candidates are sorted by DOM severity -- a publication-blocking
category first, other measured defects next, fragmentation-only last -- and
by slide index within a rank, so the slides a viewer would notice first take
the first budget slots and the first concurrent lanes.

Cost bound (Q3). A fragmentation-only slide the remake did not rescue is
handed straight to the flagged-publish policy: the bounded patch round returns
``full_reauthor`` for structural change and could not merge its boxes anyway,
and the terminal round does not remake it again. The gate therefore adds at
most one provider call per fragmentation-only slide.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeVar

from services.authored_provider_rate_profile import resolve_authored_rate_profile
from utils.get_env_programme import is_authored_repair_calls_enabled
from utils.runtime_settings import RuntimeSettings
from utils.authored_dom_categories import BLOCKING_SAFETY_CATEGORIES
from utils.authored_fragmentation_gate import FRAGMENTATION_CATEGORY

LOGGER = logging.getLogger(__name__)

#: Remake attempts one deck may spend on slides whose only defects are
#: fragmentation. Separate from ``MAX_TERMINAL_REMAKES_PER_DECK`` so the
#: fragmentation gate cannot consume the budget real overflow slides need.
MAX_FRAGMENTATION_REMAKES_PER_DECK = 6

OVERFLOW_BUDGET = "overflow"
FRAGMENTATION_BUDGET = "fragmentation"

#: Outcome of a fragmentation-only slide that never got a remake because the
#: fragmentation budget was already spent. Same token the terminal round uses
#: for the shared budget, so bench tooling reads one vocabulary.
REMAKE_BUDGET_EXHAUSTED_OUTCOME = "remake_budget_exhausted"

#: Severity ranks, lowest first: a publication-blocking category, any other
#: measured defect (``capacity``), fragmentation only.
SEVERITY_BLOCKING = 0
SEVERITY_OTHER = 1
SEVERITY_FRAGMENTATION_ONLY = 2

_T = TypeVar("_T")


def are_repair_calls_enabled(settings: RuntimeSettings | None = None) -> bool:
    """Whether a flagged slide may spend a provider call on repair.

    Off, every repair round becomes a no-op that leaves the first-pass
    slide in place; the deterministic, render-only steps (the text-scale
    rung) still run because they cost nothing. A slide the gate flagged
    then publishes flagged rather than being re-authored.
    """

    return is_authored_repair_calls_enabled(settings)


def remake_concurrency() -> int:
    """How many remakes may run at once: the authoring wave's steady width.

    Resolved from the same rate profile ``plan_authored_batch_wave`` reads, so
    ``AUTHORED_BATCH_WAVE_CONCURRENCY`` and the author-pool clamp apply here
    exactly as they apply to the authoring stage; there is no separate knob.
    """

    return max(1, int(resolve_authored_rate_profile().steady_wave_concurrency))


def flagged_slide_entry(
    *,
    index: int,
    reason: str,
    attempted: bool,
    outcome: str,
    repeated: bool | None = None,
    remake_count: int = 0,
) -> dict[str, object]:
    """One D7 flagged-slide receipt entry, in the shape the receipt validates."""

    entry: dict[str, object] = {
        "index": int(index),
        "reason": str(reason)[:160],
        "remakeAttempted": bool(attempted),
        "remakeOutcome": str(outcome)[:64],
    }
    if remake_count > 1:
        # Omitted for one remake: a receipt sealed before the remake-first
        # round existed carries no count, and one is what it means.
        entry["remakeCount"] = int(remake_count)
    if repeated is not None:
        # Only the durable Scene repair claim knows whether the same digest and
        # error came back; an ordinary terminal slide has nothing to say here
        # and omits the key rather than inventing ``False``.
        entry["repeated"] = bool(repeated)
    return entry


def defect_categories(defects: Sequence[Any]) -> tuple[str, ...]:
    """Distinct defect categories in first-seen order."""

    categories: list[str] = []
    for issue in defects:
        category = str(getattr(issue, "category", "") or "").strip()
        if category and category not in categories:
            categories.append(category)
    return tuple(categories)


def terminal_reason(defects: Sequence[Any]) -> str:
    return "composition_safety:" + (",".join(defect_categories(defects)) or "unknown")


def is_fragmentation_only(defects: Sequence[Any]) -> bool:
    """True when the slide has defects and every one of them is fragmentation."""

    categories = defect_categories(defects)
    return bool(categories) and all(c == FRAGMENTATION_CATEGORY for c in categories)


def severity_rank(defects: Sequence[Any]) -> int:
    categories = defect_categories(defects)
    if any(category in BLOCKING_SAFETY_CATEGORIES for category in categories):
        return SEVERITY_BLOCKING
    if is_fragmentation_only(defects):
        return SEVERITY_FRAGMENTATION_ONLY
    return SEVERITY_OTHER


def order_remake_candidates(
    candidates: Sequence[int], defects_by_index: Mapping[int, Sequence[Any]]
) -> list[int]:
    """Candidates by DOM severity, ties by slide index; duplicates dropped."""

    unique = sorted({int(index) for index in candidates})
    return sorted(
        unique, key=lambda index: (severity_rank(defects_by_index.get(index, ())), index)
    )


@dataclass(frozen=True, slots=True)
class RemakeLaunch:
    """One remake the plan decided to spend, and the budget it is charged to."""

    index: int
    defects: tuple[Any, ...]
    budget: str

    @property
    def fragmentation_only(self) -> bool:
        return self.budget == FRAGMENTATION_BUDGET


@dataclass(frozen=True, slots=True)
class RemakePlan:
    """Every budget decision of the round, taken before the first call."""

    #: In launch order (severity, then slide index).
    launches: tuple[RemakeLaunch, ...] = ()
    #: Slides that keep the patch-first path: nothing measured to brief a
    #: remake with, or the shared overflow budget is spent.
    to_patch_round: tuple[int, ...] = ()
    #: Fragmentation-only slides the fragmentation budget could not cover,
    #: already in the flagged-slide receipt shape.
    budget_exhausted: tuple[dict[str, object], ...] = ()

    def launch_for(self, index: int) -> RemakeLaunch | None:
        return next((launch for launch in self.launches if launch.index == index), None)

    @property
    def overflow_counts(self) -> dict[int, int]:
        """Per-slide spend against the shared deck budget, for the terminal round."""

        return {launch.index: 1 for launch in self.launches if not launch.fragmentation_only}

    @property
    def fragmentation_counts(self) -> dict[int, int]:
        return {launch.index: 1 for launch in self.launches if launch.fragmentation_only}


def plan_remakes(
    candidates: Sequence[int],
    defects_by_index: Mapping[int, Sequence[Any]],
    *,
    max_overflow_remakes: int,
    max_fragmentation_remakes: int = MAX_FRAGMENTATION_REMAKES_PER_DECK,
    overflow_spent: int = 0,
    fragmentation_spent: int = 0,
) -> RemakePlan:
    """Decide every launch and its budget up front, in severity order.

    Because the whole plan is fixed here, running the launches concurrently
    cannot overspend either budget: a budget is charged at planning time, not
    when a call happens to start.
    """

    launches: list[RemakeLaunch] = []
    to_patch_round: list[int] = []
    exhausted: list[dict[str, object]] = []
    overflow_calls = int(overflow_spent)
    fragmentation_calls = int(fragmentation_spent)
    for index in order_remake_candidates(candidates, defects_by_index):
        defects = tuple(defects_by_index.get(index, ()))
        if not defects:
            to_patch_round.append(index)
            continue
        if is_fragmentation_only(defects):
            if fragmentation_calls >= max_fragmentation_remakes:
                exhausted.append(
                    flagged_slide_entry(
                        index=index, reason=terminal_reason(defects), attempted=False,
                        outcome=REMAKE_BUDGET_EXHAUSTED_OUTCOME,
                    )
                )
                continue
            fragmentation_calls += 1
            launches.append(RemakeLaunch(index=index, defects=defects, budget=FRAGMENTATION_BUDGET))
            continue
        if overflow_calls >= max_overflow_remakes:
            to_patch_round.append(index)
            continue
        overflow_calls += 1
        launches.append(RemakeLaunch(index=index, defects=defects, budget=OVERFLOW_BUDGET))
    return RemakePlan(
        launches=tuple(launches),
        to_patch_round=tuple(to_patch_round),
        budget_exhausted=tuple(exhausted),
    )


async def run_bounded_remakes(
    launches: Sequence[RemakeLaunch],
    *,
    concurrency: int,
    run_one: Callable[[RemakeLaunch], Awaitable[_T]],
) -> dict[int, _T | BaseException]:
    """Run every launch with at most ``concurrency`` in flight; never cancel.

    Launches are started in plan order, so the severest slides take the first
    lanes. A launch that raises is recorded as its exception and the others
    finish; the caller decides what a failed launch means. Results are keyed
    by slide index so application order is the caller's, not completion's.
    """

    if not launches:
        return {}
    semaphore = asyncio.Semaphore(max(1, int(concurrency)))

    async def guarded(launch: RemakeLaunch) -> _T:
        async with semaphore:
            return await run_one(launch)

    results = await asyncio.gather(
        *(guarded(launch) for launch in launches), return_exceptions=True
    )
    return {
        launch.index: result for launch, result in zip(launches, results, strict=True)
    }


def settled_flag_entry(
    *,
    index: int,
    declaration: Mapping[str, object],
    reason: str,
    repeated: bool | None,
) -> dict[str, object] | None:
    """The flag for a predeclared slide whose remake outcome is already known.

    A declaration carrying ``remakeOutcome`` was settled by the remake-first
    round (a fragmentation-only slide it could not rescue, or could not
    afford); the terminal round publishes it as declared and spends nothing
    more on it. Any other declaration returns ``None`` and takes the terminal
    round's own remake path.
    """

    outcome = declaration.get("remakeOutcome")
    if not isinstance(outcome, str) or not outcome.strip():
        return None
    remake_count = declaration.get("remakeCount", 0)
    attempted = declaration.get("remakeAttempted")
    return flagged_slide_entry(
        index=index, reason=reason,
        attempted=bool(attempted) if isinstance(attempted, bool) else remake_count > 0,
        outcome=outcome, repeated=repeated,
        remake_count=int(remake_count) if isinstance(remake_count, int) else 0,
    )


def unresolved_fragmentation_entry(
    *, index: int, defects: Sequence[Any], outcome: str
) -> dict[str, object]:
    """Declaration for a fragmentation-only slide whose one remake did not rescue it."""

    return {
        "index": int(index),
        "reason": terminal_reason(defects),
        "remakeAttempted": True,
        "remakeOutcome": str(outcome)[:64],
        "remakeCount": 1,
    }


__all__ = [
    "FRAGMENTATION_BUDGET",
    "MAX_FRAGMENTATION_REMAKES_PER_DECK",
    "OVERFLOW_BUDGET",
    "REMAKE_BUDGET_EXHAUSTED_OUTCOME",
    "SEVERITY_BLOCKING",
    "SEVERITY_FRAGMENTATION_ONLY",
    "SEVERITY_OTHER",
    "RemakeLaunch",
    "RemakePlan",
    "defect_categories",
    "flagged_slide_entry",
    "is_fragmentation_only",
    "order_remake_candidates",
    "plan_remakes",
    "are_repair_calls_enabled",
    "remake_concurrency",
    "run_bounded_remakes",
    "settled_flag_entry",
    "severity_rank",
    "terminal_reason",
    "unresolved_fragmentation_entry",
]
