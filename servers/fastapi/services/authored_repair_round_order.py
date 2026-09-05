"""Remake-first ordering for the bounded quality-gate repair round (D7).

``docs/generation-programme-master-design-20260904.md`` section A4 resolved D7
as ONE automatic remake behind the bounded patch repair. This module is the
ordering that two benchmark runs since then measured as the better one.

Measured motivation, bench run 10 (Codex gpt-6-astra, 24 slides, free
composition): the DOM safety gate flagged 6 slides, the bounded PATCH repair
-- one provider call that edits the failing slide's existing HTML -- fixed 0
of them, and the D7 fallback's fresh composition at a lower density, handed
the same measured defects, then passed 4 of 8 terminal slides. Bench run 8
(gpt-5.6-sol) measured the same shape: patch ~0, remake 1 of 9. For these
models a fresh composition briefed with the defect list beats patching the
old HTML, and the single remake was the binding limit.

So on a deck that may publish a flagged slide (``terminal_fallback_active``)
the order per flagged slide becomes:

1. remake #1, here, before the patch round is built, with the gate's own
   defects in the brief;
2. the existing bounded patch repair, for the slides #1 did not rescue;
3. remake #2, in ``resolve_terminal_gate_slides``, for the slides #2 did not
   rescue either; whatever survives that is published flagged with
   ``remakeOutcome: remake_budget_exhausted`` and ``remakeCount: 2``.

Time. One remake is one provider authoring call plus one Chromium recapture
(~10 s measured on gpt-6-astra; ~170-240 s on the slower worker lane the
fallback module was first written against). The worst case ADDED per flagged
slide is therefore one such call -- the second remake -- on top of the one D7
already spent, and only for a slide that was going to be published flagged
anyway. A clean slide adds nothing, and the deck-wide ceiling
(``MAX_TERMINAL_REMAKES_PER_DECK``) is shared by both rounds, so a deck with
a dozen flagged slides still cannot turn into two dozen sequential calls.

Nothing here runs on an exact-reconstruction deck, a source-verbatim deck, or
with the publish-flagged knob off: those keep today's order -- patch first,
then one remake -- unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from services.authored_terminal_slide_fallback import (
    MAX_TERMINAL_REMAKES_PER_DECK,
    apply_admitted_remake,
    remake_slide_once,
    replayed_plan_kwargs,
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GateDefect:
    """One gate finding in the attribute shape the remake brief reads.

    The first-pass gate reports its findings as mappings (they travel into
    receipts as JSON), while the remake brief builder reads ``.category`` and
    ``.detail`` off an issue object. This adapter is the whole difference, so
    a remake is briefed with the defects the gate actually measured rather
    than with a second, independently re-derived opinion of the same slide.
    """

    category: str
    detail: str
    severity: str


def _issue_field(issue: object, name: str) -> str:
    value = (
        issue.get(name) if isinstance(issue, Mapping) else getattr(issue, name, None)
    )
    return str(value or "").strip()


def gate_defects_by_slide(
    gate_failures: Sequence[Mapping[str, object]],
) -> dict[int, tuple[GateDefect, ...]]:
    """The defects the first-pass safety gate reported, per slide index."""

    defects: dict[int, list[GateDefect]] = {}
    for failure in gate_failures:
        if not isinstance(failure, Mapping):
            continue
        index = failure.get("slide_index")
        if isinstance(index, bool) or not isinstance(index, int):
            continue
        for issue in failure.get("issues", ()) or ():
            defects.setdefault(int(index), []).append(
                GateDefect(
                    category=_issue_field(issue, "category"),
                    detail=(
                        _issue_field(issue, "detail")
                        or _issue_field(issue, "message")
                    ),
                    severity=_issue_field(issue, "severity") or "high",
                )
            )
    return {index: tuple(items) for index, items in defects.items()}


@dataclass(frozen=True, slots=True)
class RemakeFirstOutcome:
    """What the remake-first round did, for the round behind it."""

    scene_envelope: Any
    #: The flagged slides the bounded patch round must still run for, in the
    #: order it was given them.
    remaining_flagged: list[int] = field(default_factory=list)
    remade_slide_indices: tuple[int, ...] = ()
    #: Remakes spent per slide index, carried to ``resolve_terminal_gate_slides``
    #: so the second remake and the deck ceiling are counted once, not twice.
    remake_counts: dict[int, int] = field(default_factory=dict)


async def remake_first_flagged_slides(
    *,
    flagged: Sequence[int], gate_failures: Sequence[Mapping[str, object]],
    htmls: list[str], pngs: list[bytes], contents: Sequence[str],
    roles: Sequence[str], brand: Any, design_system: str, style: Any,
    capacity_plan: Any, profile: Any, reference_mode: str, scene_envelope: Any,
    metrics_by_slide: dict[int, object], chapter_plan: Any = None,
    illustration_plan: Any = None, infographic_plan: Any = None,
    instructions: object = None, generation_job: Any = None,
) -> RemakeFirstOutcome:
    """Remake every flagged slide once, before the bounded patch round is built.

    Call this only on a deck the terminal fallback is active for, and only
    after the durable Scene repair claims are taken: a slide with no claim was
    already declared terminal and nothing may rewrite it here.

    ``htmls``, ``pngs`` and ``metrics_by_slide`` are updated in place for an
    admitted remake so the caller keeps one deck state; the resealed scene
    envelope is returned because it is immutable. A slide whose remake is not
    admitted comes back in ``remaining_flagged`` completely untouched -- its
    original html/png/metrics triple is the one the patch round then repairs.
    """

    defects_by_index = gate_defects_by_slide(gate_failures)
    remaining: list[int] = []
    remade: list[int] = []
    counts: dict[int, int] = {}
    calls = 0
    for index in flagged:
        defects = defects_by_index.get(int(index), ())
        if not defects or calls >= MAX_TERMINAL_REMAKES_PER_DECK:
            # Nothing measured to brief a fresh composition with, or the deck's
            # shared remake budget is spent. Either way this slide keeps the
            # patch-first path and the terminal round's remake behind it.
            remaining.append(index)
            continue
        calls += 1
        counts[int(index)] = 1
        admitted, outcome = await remake_slide_once(
            index=index, html=htmls[index], png=pngs[index],
            content=str(contents[index]), role=str(roles[index]),
            defects=defects, profile=profile, reference_mode=reference_mode,
            capacity=(capacity_plan.at(index) if capacity_plan is not None else {}),
            brand=brand, design_system=design_system, style=style,
            total_slides=len(htmls),
            reference_kwargs=replayed_plan_kwargs(
                [index], style=style, brand=brand, chapter_plan=chapter_plan,
                illustration_plan=illustration_plan, with_assets=True,
                infographic_plan=infographic_plan, instructions=instructions,
            ),
        )
        if admitted is None:
            LOGGER.info(
                "[authored] remake-first not admitted slide=%d outcome=%s",
                int(index) + 1,
                outcome,
            )
            remaining.append(index)
            continue
        scene_envelope = await apply_admitted_remake(
            index=index, admitted=admitted, htmls=htmls, pngs=pngs,
            metrics_by_slide=metrics_by_slide, scene_envelope=scene_envelope,
            generation_job=generation_job,
        )
        remade.append(int(index))
    if flagged:
        LOGGER.info(
            "[authored] remake-first flagged=%s remade=%s to_patch_round=%s",
            list(flagged),
            remade,
            remaining,
        )
    return RemakeFirstOutcome(
        scene_envelope=scene_envelope,
        remaining_flagged=remaining,
        remade_slide_indices=tuple(remade),
        remake_counts=counts,
    )


__all__ = [
    "GateDefect",
    "RemakeFirstOutcome",
    "gate_defects_by_slide",
    "remake_first_flagged_slides",
]
