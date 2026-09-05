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
    # H3 re-renders, re-measures and admits a scaled slide through exactly the
    # helper an admitted remake goes through, so a rung that "passes" passes the
    # same two gates a remake has to pass. Deliberately the private one: a
    # second, independently written admission is how the two rounds drift.
    _admit_remade_slide,
)
from utils.authored_dom_fit_magnitude import build_fit_summary
from utils.authored_text_scale import scale_authored_text
from utils.get_env_programme import is_authored_text_scale_rung_enabled

LOGGER = logging.getLogger(__name__)

#: H3 (design 20260905, section 6): the rung shrinks type, it does not redesign.
#: Below 0.80 the page stops being the page that was authored, and above 0.98
#: the change is not worth a render.
TEXT_SCALE_MIN_FACTOR = 0.80
TEXT_SCALE_MAX_FACTOR = 0.98


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


def gate_issues_by_slide(
    gate_failures: Sequence[Mapping[str, object]],
) -> dict[int, tuple[Mapping[str, object], ...]]:
    """The gate's raw issue mappings per slide index.

    ``gate_defects_by_slide`` projects issues onto the three fields the remake
    brief reads; the text-scale rung needs the M1 ``magnitude`` those dicts also
    carry, so it reads the mappings themselves rather than widening ``GateDefect``.
    """

    issues: dict[int, list[Mapping[str, object]]] = {}
    for failure in gate_failures:
        if not isinstance(failure, Mapping):
            continue
        index = failure.get("slide_index")
        if isinstance(index, bool) or not isinstance(index, int):
            continue
        for issue in failure.get("issues", ()) or ():
            if isinstance(issue, Mapping):
                issues.setdefault(int(index), []).append(issue)
    return {index: tuple(items) for index, items in issues.items()}


@dataclass(frozen=True, slots=True)
class _IssueView:
    """The two attributes ``build_fit_summary`` reads off an issue object."""

    category: str
    magnitude: Mapping[str, Any] | None


def text_scale_factor(issues: Sequence[Mapping[str, object]]) -> float | None:
    """The scale a fit-only slide should be tried at, or ``None`` to skip the rung.

    ``None`` whenever the slide has a defect shrinking type cannot fix -- an
    overlap or an off-canvas element is a composition error, and a smaller font
    just makes a wrong layout smaller -- and whenever the measured overflow has
    no ratio to bound the scale with (a clipping-only slide). Otherwise the
    factor is ``1 / worst_overflow_ratio``, which is exactly enough to bring the
    worst element inside its box, clamped to the rung's range.
    """

    views: list[_IssueView] = []
    for issue in issues:
        category = _issue_field(issue, "category")
        if category in {"overlap", "off_canvas"}:
            return None
        magnitude = issue.get("magnitude") if isinstance(issue, Mapping) else None
        views.append(
            _IssueView(
                category=category,
                magnitude=magnitude if isinstance(magnitude, Mapping) else None,
            )
        )
    if not views:
        return None
    summary = build_fit_summary(views, content_area=0.0, viewport_area=0.0)
    try:
        ratio = float(summary.get("worst_overflow_ratio") or 1.0)
    except (TypeError, ValueError):
        return None
    if ratio <= 1.0:
        return None
    return max(TEXT_SCALE_MIN_FACTOR, min(TEXT_SCALE_MAX_FACTOR, 1.0 / ratio))


async def _apply_text_scale_rung(
    *,
    index: int, factor: float, htmls: list[str], pngs: list[bytes],
    metrics_by_slide: dict[int, object], scene_envelope: Any, capacity: object,
    profile: Any, reference_mode: str, generation_job: Any,
) -> tuple[Any, bool]:
    """Step 0: shrink this slide's type and keep it only if it now passes.

    Costs one render and no provider call. A slide the rung does not rescue is
    handed back completely untouched, so remake #1 sees the same bytes it would
    have seen without the rung.
    """

    scaled = scale_authored_text(htmls[index], factor)
    if scaled.html == htmls[index] or not scaled.scaled_declarations:
        return scene_envelope, False
    try:
        admitted = await _admit_remade_slide(
            candidate_html=scaled.html, capacity=capacity, profile=profile,
            reference_mode=reference_mode,
        )
    except Exception:
        LOGGER.warning(
            "[authored] text-scale rung capture failed slide=%d", index + 1,
            exc_info=True,
        )
        return scene_envelope, False
    if admitted is None:
        return scene_envelope, False
    resealed = await apply_admitted_remake(
        index=index, admitted=admitted, htmls=htmls, pngs=pngs,
        metrics_by_slide=metrics_by_slide, scene_envelope=scene_envelope,
        generation_job=generation_job,
    )
    LOGGER.info(
        "[authored] repair:text_scale_rung slide=%d factor=%.2f scaled=%d",
        index + 1, factor, scaled.scaled_declarations,
    )
    return resealed, True


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
    #: H3: slides the deterministic text-scale rung rescued before any provider
    #: call. They spend no remake budget, so they are reported separately from
    #: ``remade_slide_indices`` even though both are repaired slides.
    text_scaled_slide_indices: tuple[int, ...] = ()


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
    issues_by_index = gate_issues_by_slide(gate_failures)
    rung_enabled = is_authored_text_scale_rung_enabled()
    remaining: list[int] = []
    remade: list[int] = []
    text_scaled: list[int] = []
    counts: dict[int, int] = {}
    calls = 0
    for index in flagged:
        defects = defects_by_index.get(int(index), ())
        # H3 step 0: a slide whose only blocking defects are fit gets one
        # deterministic type-scale attempt before any provider call. It is a
        # rung, not a repair round: it either passes the same admission a remake
        # must pass, or the slide falls through to remake #1 untouched.
        factor = (
            text_scale_factor(issues_by_index.get(int(index), ()))
            if rung_enabled
            else None
        )
        if factor is not None:
            scene_envelope, rescued = await _apply_text_scale_rung(
                index=int(index), factor=factor, htmls=htmls, pngs=pngs,
                metrics_by_slide=metrics_by_slide, scene_envelope=scene_envelope,
                capacity=(capacity_plan.at(index) if capacity_plan is not None else {}),
                profile=profile, reference_mode=reference_mode,
                generation_job=generation_job,
            )
            if rescued:
                text_scaled.append(int(index))
                continue
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
            "[authored] remake-first flagged=%s text_scaled=%s remade=%s to_patch_round=%s",
            list(flagged),
            text_scaled,
            remade,
            remaining,
        )
    return RemakeFirstOutcome(
        scene_envelope=scene_envelope,
        remaining_flagged=remaining,
        remade_slide_indices=tuple(remade),
        remake_counts=counts,
        text_scaled_slide_indices=tuple(text_scaled),
    )


__all__ = [
    "TEXT_SCALE_MAX_FACTOR",
    "TEXT_SCALE_MIN_FACTOR",
    "GateDefect",
    "RemakeFirstOutcome",
    "gate_defects_by_slide",
    "gate_issues_by_slide",
    "remake_first_flagged_slides",
    "text_scale_factor",
]
