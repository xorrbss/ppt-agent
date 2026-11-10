"""D7: one automatic remake for a ladder-terminal slide, then publish flagged.

Until this module existed, a slide that still failed the composition safety
gate after the deterministic repair candidate and the bounded provider repair
failed the whole deck with ``authored_composition_safety_gate_failed``
(``docs/generation-programme-master-design-20260904.md`` section A4, D7). A
published deck with one flagged slide beats a failed deck, and one fresh
authoring call has a far better prior than prose repair.

Remake path. Every entry point of ``services/authored_page_remake_service.py``
starts from a PERSISTED deck (``_load_deck`` reads the presentation/slide
rows, publication is a CAS against ``authored_page_source_digest``), and at
the terminal gate the deck is not in the database yet. What that service does
to produce a candidate is one ``revise_authored_deck(...,
force_full_reauthor_indices=[0], max_cycles=1)`` call driven by a synthetic
``SlideCritique``; that mechanism is reused here with the deck's own plan
decisions, and the critique carries D7's lower-density hint instead.

Bounds. ``MAX_REMAKES_PER_SLIDE`` remakes per terminal slide and at most
``MAX_TERMINAL_REMAKES_PER_DECK`` per deck, counted across BOTH rounds that
may remake a slide: the remake-first round that now opens the bounded repair
(``services/authored_repair_round_order.py``) and this terminal round behind
it. Fragmentation-only slides are budgeted and scheduled separately in
``services/authored_remake_schedule.py`` and never reach a second remake
here. A remake is admitted only when it clears BOTH the capacity envelope and
the safety gate, so the fallback can never hand back a worse deck than it was
given. A failed call flags the slide rather than calling again; the wall
clock those two calls cost is documented in the ordering module named above.
"""

from __future__ import annotations

from services.authored_terminal_replay_plan import replayed_plan_kwargs as replayed_plan_kwargs

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from services.authored_admission_capture import capture_once
from services.authored_composition_policy import (
    blocking_safety_issues,
    capacity_violations,
)
from services.authored_semantic_role_repair import canonicalize_authored_roles
from services.authored_infographic_assets import (
    build_infographic_asset_context as build_infographic_asset_context,
    deck_has_picture_source as deck_has_picture_source,
)
from services.authored_presentation_artifacts import (
    _complete_stage_after_external_success,
)
from services.authored_measured_gates import remake_density_hint
from services.authored_remake_schedule import (
    are_repair_calls_enabled,
    flagged_slide_entry as _flagged_entry,
    settled_flag_entry,
    terminal_reason as _terminal_reason,
)
# The deck and per-slide remake budgets live in ``services.remake_caps`` so
# admission and the timing receipt can read them without this module.
from services.remake_caps import (
    MAX_REMAKES_PER_SLIDE as MAX_REMAKES_PER_SLIDE,
    MAX_TERMINAL_REMAKES_PER_DECK as MAX_TERMINAL_REMAKES_PER_DECK,
)
from services.authored_terminal_slide_flag_only import verbatim_flagged_slides
from services.authored_terminal_slide_policy import terminal_fallback_active
from services.authored_vnext.generation.scene_envelope import replace_deck_scene_slide
from services.image_generation_service import ImageGenerationService as ImageGenerationService
from utils.asset_directory_utils import get_images_directory as get_images_directory
from utils.authored_dom_quality import inspect_authored_dom
from services.generation_core.authoring.author_vision_qa import revise_authored_deck
from utils.llm_calls.critique_slide import CritiqueIssue, SlideCritique
from utils.get_env_programme import is_authored_in_render_fit_enabled
from utils.slide_capture import (
    _placeholder_png,
    get_cached_browser_metrics,
    render_html_to_png_with_metrics,
)

LOGGER = logging.getLogger(__name__)

#: The D7 lower-density hint. ``CritiqueIssue.detail`` is truncated to 200
#: characters by the remake service and by the reviser's prompt budget.
LOWER_DENSITY_HINT = (
    "Rebuild this slide as a fresh composition at a LOWER content density: "
    "keep every fact but carry fewer bullets and shorter lines so the "
    "composition fits its frame. Resolve overlap, occlusion and clipping."
)


@dataclass(frozen=True, slots=True)
class TerminalSlideFallback:
    """What the fallback did, for the call site and for the receipts."""

    scene_envelope: Any
    flagged_slides: tuple[dict[str, object], ...] = ()
    remade_slide_indices: tuple[int, ...] = ()
    remake_calls: int = 0


def _slide_metrics(
    index: int, html: str, metrics_by_slide: Mapping[int, object] | None
) -> object | None:
    if metrics_by_slide is not None and index in metrics_by_slide:
        return metrics_by_slide[index]
    return get_cached_browser_metrics(html)


def _blocking_defects(
    *, html: str, metrics: object, profile: Any, reference_mode: str
) -> tuple[Any, ...]:
    """The safety-gate defects ``_final_authored_quality_gate`` would report.

    Telemetry that is not ``available`` is deliberately NOT treated as a
    terminal slide: that state is the gate's retryable branch and stays with
    the gate, because a remake cannot compensate for a missing Chromium probe.
    """

    report = inspect_authored_dom(
        html,
        metrics,
        reference_mode=reference_mode,
        strict_structural_off_canvas=(profile.profile_id != "ordinary"),
    )
    if report.telemetry_status != "available":
        return ()
    return tuple(blocking_safety_issues(report))




async def _remake_candidate(
    *,
    index: int, html: str, png: bytes, content: str, role: str,
    brand: Any, design_system: str, style: Any, total_slides: int,
    defects: Sequence[Any], reference_kwargs: Mapping[str, Any],
) -> str | None:
    """One bounded full re-author of a single slide, or ``None`` on failure."""

    # ``CritiqueIssue.detail`` caps at 200 characters and ``LOWER_DENSITY_HINT``
    # is 203, so measured defects appended to the hint were silently truncated
    # away every time and no remake was ever told what the gate had found. They
    # travel as their own issue, first: ``_feedback`` joins every issue on a
    # critique, so the brief now names the offending qid and its card before it
    # asks for a lower density.
    evidence = "; ".join(
        str(getattr(issue, "detail", "") or "") for issue in defects[:2]
    ).strip()
    issue_type = (
        "overlap"
        if any(getattr(issue, "category", "") == "overlap" for issue in defects)
        else "other"
    )
    hint = remake_density_hint(defects, default=LOWER_DENSITY_HINT)
    details = ([f"Measured defects: {evidence}"] if evidence else []) + [hint]
    critique = SlideCritique(
        needs_fix=True,
        issues=[
            CritiqueIssue(type=issue_type, severity="high", detail=detail[:200])
            for detail in details
        ],
    )
    result = await revise_authored_deck(
        [html], [png], [content], [role], brand, design_system,
        max_cycles=1, style=style, slide_indices=[index],
        total_slides=total_slides, initial_critiques=[critique],
        return_details=True, force_full_reauthor_indices=[0],
        **dict(reference_kwargs),
    )
    if not isinstance(result, (list, tuple)) or len(result) != 4:
        return None
    htmls, _pngs, changed, details = result
    detail_payload = details[0] if details and isinstance(details[0], dict) else {}
    # The reviser swallows model and transport failures and hands the source
    # back unchanged, so an unchanged result is a failed attempt here, not a
    # candidate: the slide it was handed already fails the gate.
    if list(changed) != [0] or (
        bool(detail_payload.get("attempted")) and not detail_payload.get("succeeded")
    ):
        return None
    candidate = str(htmls[0]) if htmls else ""
    return candidate or None


async def _admit_remade_slide(
    *, candidate_html: str, capacity: object, profile: Any, reference_mode: str,
    slide_index: int | None = None,
) -> tuple[str, bytes, object] | None:
    """Bind fresh pixels/telemetry and admit only a candidate that clears both gates."""

    canonical = canonicalize_authored_roles(candidate_html)
    # Local-fit is the active fitter; the in-render fit is opt-in and passes no
    # ``fit=`` kwarg at all when off.
    fit_kwargs = {"fit": True} if is_authored_in_render_fit_enabled() else {}
    capture = await capture_once(
        canonical,
        index=slide_index,
        capture=render_html_to_png_with_metrics,
        **fit_kwargs,
    )
    metrics = getattr(capture, "browser_metrics", None)
    # Only a capture the owner bound to its prepared bytes is admissible; an
    # unbound payload carries no ``prepared_html`` and is refused below.
    prepared_html = getattr(capture, "prepared_html", None)
    if prepared_html is None or metrics is None or capture.png == _placeholder_png():
        return None
    candidate = getattr(capture, "fitted_html", None) or prepared_html
    if capacity_violations(candidate, metrics, capacity):
        return None
    if _blocking_defects(
        html=candidate,
        metrics=metrics,
        profile=profile,
        reference_mode=reference_mode,
    ):
        return None
    return candidate, capture.png, metrics


async def remake_slide_once(
    *,
    index: int, html: str, png: bytes, content: str, role: str,
    defects: Sequence[Any], capacity: object, profile: Any,
    reference_mode: str, brand: Any, design_system: str, style: Any,
    total_slides: int, reference_kwargs: Mapping[str, Any],
) -> tuple[tuple[str, bytes, object] | None, str]:
    """One remake plus its admission, as ``(admitted, outcome)``.

    Shared with the remake-first round, so a slide is briefed, captured and
    admitted identically whichever of the two rounds pays for it.
    """

    try:
        candidate = await _remake_candidate(
            index=index, html=html, png=png, content=content, role=role,
            brand=brand, design_system=design_system, style=style,
            total_slides=total_slides, defects=defects,
            reference_kwargs=reference_kwargs,
        )
    except Exception:
        LOGGER.warning(
            "[authored] terminal remake provider call failed slide=%d",
            index + 1,
            exc_info=True,
        )
        return None, "remake_provider_failed"
    if candidate is None:
        return None, "remake_generation_failed"
    try:
        admitted = await _admit_remade_slide(
            candidate_html=candidate, capacity=capacity, profile=profile,
            reference_mode=reference_mode, slide_index=index,
        )
    except Exception:
        LOGGER.warning(
            "[authored] terminal remake capture failed slide=%d",
            index + 1,
            exc_info=True,
        )
        return None, "remake_capture_failed"
    return admitted, ("remade" if admitted is not None else "remake_gate_failed")


async def apply_admitted_remake(
    *,
    index: int,
    admitted: tuple[str, bytes, object],
    htmls: list[str],
    pngs: list[bytes],
    metrics_by_slide: dict[int, object],
    scene_envelope: Any,
    generation_job: Any = None,
) -> Any:
    """Install one admitted remake into the deck state; return a resealed envelope.

    Shared with the remake-first round so both write one whole
    (html, png, metrics) triple and the same durable render checkpoint.
    """

    selected_html, selected_png, selected_metrics = admitted
    htmls[index] = selected_html
    pngs[index] = selected_png
    metrics_by_slide[index] = selected_metrics
    resealed = replace_deck_scene_slide(
        scene_envelope, slide_index=index, html_projection=selected_html
    )
    attempt_token = getattr(generation_job, "attempt_token", None)
    if generation_job is not None and attempt_token:
        # A resumed attempt must replay the admitted remake, not the terminal
        # draft it replaced. A workspace-linked job's checkpoint write is
        # refused without the lease's fencing token.
        await _complete_stage_after_external_success(
            job_id=generation_job.id, slide_index=index, stage="render",
            attempt_token=attempt_token, html=selected_html,
            png=selected_png, browser_metrics=selected_metrics,
            fencing_token=getattr(generation_job, "_workspace_fencing_token", None),
        )
    return resealed


async def resolve_terminal_gate_slides(
    *,
    htmls: list[str], pngs: list[bytes], contents: Sequence[str],
    roles: Sequence[str], brand: Any, design_system: str, style: Any,
    capacity_plan: Any, profile: Any, reference_mode: str,
    scene_envelope: Any, metrics_by_slide: dict[int, object],
    source_reflow: bool = False, chapter_plan: Any = None,
    illustration_plan: Any = None, infographic_plan: Any = None,
    instructions: object = None, generation_job: Any = None,
    predeclared: Sequence[Mapping[str, object]] = (),
    remake_counts: Mapping[int, int] | None = None,
    preserved_flagged_indices: Sequence[int] = (),
    prepare_admitted: Any = None,
) -> TerminalSlideFallback:
    """Resolve ladder-terminal slides by remake or verbatim flag-only fallback.

    ``htmls``, ``pngs`` and ``metrics_by_slide`` are updated in place for an
    admitted remake so the caller keeps one deck state; the resealed scene
    envelope is returned because it is immutable.

    ``predeclared`` carries slides the generation owner already knows are
    terminal -- today, the ones whose durable Scene repair claim was spent by
    an earlier attempt (``declare_exhausted_repair_budget``). They are treated
    as terminal on the strength of that declaration rather than rediscovered
    from the DOM, so the outcome cannot depend on whether a later gate happens
    to re-report the same defect.

    ``remake_counts`` carries what the remake-first round already spent, per
    slide index. It is both budgets at once: its total is the deck's spent
    share of ``MAX_TERMINAL_REMAKES_PER_DECK``, and a slide already at
    ``MAX_REMAKES_PER_SLIDE`` is flagged here without another call.
    """

    if not terminal_fallback_active(
        profile=profile, reference_mode=reference_mode, source_reflow=source_reflow
    ):
        return TerminalSlideFallback(scene_envelope=scene_envelope)

    preserved_indices = {int(index) for index in preserved_flagged_indices}
    declared_by_index = {
        int(entry["index"]): dict(entry)
        for entry in predeclared
        if isinstance(entry, Mapping) and isinstance(entry.get("index"), int)
    }
    terminal: list[tuple[int, tuple[Any, ...], dict[str, object]]] = []
    for index, html in enumerate(htmls):
        if index in preserved_indices:
            continue
        metrics = _slide_metrics(index, html, metrics_by_slide)
        defects = (
            _blocking_defects(
                html=html,
                metrics=metrics,
                profile=profile,
                reference_mode=reference_mode,
            )
            if metrics is not None
            else ()
        )
        declared = declared_by_index.get(index)
        if declared is not None:
            terminal.append((index, defects, declared))
        elif defects:
            terminal.append((index, defects, {"reason": _terminal_reason(defects)}))
    if not terminal:
        return TerminalSlideFallback(scene_envelope=scene_envelope)

    if source_reflow:
        flagged = verbatim_flagged_slides(terminal)
        LOGGER.info(
            "[authored] terminal source-reflow slides flagged without remake slides=%s",
            [entry["index"] for entry in flagged],
        )
        return TerminalSlideFallback(
            scene_envelope=scene_envelope,
            flagged_slides=flagged,
        )

    repair_calls = are_repair_calls_enabled()
    prior_counts = {int(key): int(value) for key, value in (remake_counts or {}).items()}
    flagged: list[dict[str, object]] = []
    remade: list[int] = []
    remake_calls = sum(prior_counts.values())
    for index, defects, declaration in terminal:
        reason = str(declaration.get("reason") or _terminal_reason(defects))
        repeated = declaration.get("repeated")
        repeated = bool(repeated) if isinstance(repeated, bool) else None
        spent = prior_counts.get(index, 0)
        # Q3: a fragmentation-only slide the remake-first round settled is
        # published as declared; the patch round could not merge its boxes and
        # a second remake is not in its budget.
        settled = settled_flag_entry(
            index=index, declaration=declaration, reason=reason, repeated=repeated
        )
        if settled is not None:
            flagged.append(settled)
            continue
        if (
            not repair_calls
            or remake_calls >= MAX_TERMINAL_REMAKES_PER_DECK
            or spent >= MAX_REMAKES_PER_SLIDE
        ):
            flagged.append(
                _flagged_entry(
                    index=index, reason=reason, attempted=spent > 0,
                    outcome=("repair_calls_disabled" if not repair_calls
                             else "remake_budget_exhausted"),
                    repeated=repeated, remake_count=spent,
                )
            )
            continue
        remake_calls += 1
        spent += 1
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
            if spent >= MAX_REMAKES_PER_SLIDE:
                # Nothing is left to try for this slide, which is the fact a
                # benchmark reads off the flag; the failing call's own outcome
                # stays in this module's log.
                outcome = "remake_budget_exhausted"
            flagged.append(
                _flagged_entry(
                    index=index, reason=reason, attempted=True, outcome=outcome,
                    repeated=repeated, remake_count=spent,
                )
            )
            continue
        if prepare_admitted is not None:
            admitted = await prepare_admitted(index, admitted)
            if admitted is None:
                flagged.append(
                    _flagged_entry(
                        index=index, reason=reason, attempted=True,
                        outcome="remake_local_fit_rejected", repeated=repeated,
                        remake_count=spent,
                    )
                )
                continue
        scene_envelope = await apply_admitted_remake(
            index=index, admitted=admitted, htmls=htmls, pngs=pngs,
            metrics_by_slide=metrics_by_slide, scene_envelope=scene_envelope,
            generation_job=generation_job,
        )
        remade.append(index)
    LOGGER.info(
        "[authored] terminal slide fallback terminal=%d remade=%s flagged=%s",
        len(terminal),
        remade,
        [entry["index"] for entry in flagged],
    )
    return TerminalSlideFallback(
        scene_envelope=scene_envelope,
        flagged_slides=tuple(flagged),
        remade_slide_indices=tuple(remade),
        remake_calls=remake_calls,
    )


__all__ = [
    "LOWER_DENSITY_HINT",
    "MAX_REMAKES_PER_SLIDE",
    "MAX_TERMINAL_REMAKES_PER_DECK",
    "TerminalSlideFallback",
    "apply_admitted_remake",
    "remake_slide_once",
    "replayed_plan_kwargs",
    "resolve_terminal_gate_slides",
]
