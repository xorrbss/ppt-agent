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

Bounds. One remake per terminal slide, at most
``MAX_TERMINAL_REMAKES_PER_DECK`` per deck; a remake is admitted only when it
clears BOTH the capacity envelope and the safety gate, so the fallback can
never hand back a worse deck than it was given. A failed call flags the slide
rather than calling again.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from services.authored_composition_policy import (
    blocking_safety_issues,
    capacity_violations,
    ensure_authored_semantic_roles,
)
from services.authored_infographic_assets import (
    build_infographic_asset_context,
    deck_has_picture_source,
)
from services.authored_presentation_artifacts import (
    _complete_stage_after_external_success,
)
from services.authored_terminal_slide_policy import terminal_fallback_active
from services.authored_vnext.generation.scene_envelope import replace_deck_scene_slide
from services.image_generation_service import ImageGenerationService
from utils.asset_directory_utils import get_images_directory
from utils.authored_dom_quality import ensure_authored_qids, inspect_authored_dom
from utils.llm_calls.author_vision_qa import revise_authored_deck
from utils.llm_calls.critique_slide import CritiqueIssue, SlideCritique
from utils.slide_capture import (
    _placeholder_png,
    get_cached_browser_metrics,
    render_html_to_png_with_metrics,
)

LOGGER = logging.getLogger(__name__)

#: Remake attempts one deck may spend. Each one is a full authoring call
#: (~170-240 s on the worker lane), so a deck whose gate flagged a dozen
#: slides must not turn into a dozen sequential provider calls. Slides past
#: the budget are flagged with ``remakeAttempted: false`` rather than dropped.
MAX_TERMINAL_REMAKES_PER_DECK = 4

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


def _flagged_entry(
    *,
    index: int,
    reason: str,
    attempted: bool,
    outcome: str,
    repeated: bool | None = None,
) -> dict[str, object]:
    entry: dict[str, object] = {
        "index": int(index),
        "reason": str(reason)[:160],
        "remakeAttempted": bool(attempted),
        "remakeOutcome": str(outcome)[:64],
    }
    if repeated is not None:
        # Only the durable Scene repair claim knows whether the same digest and
        # error came back; an ordinary terminal slide has nothing to say here
        # and omits the key rather than inventing ``False``.
        entry["repeated"] = bool(repeated)
    return entry


def _terminal_reason(defects: Sequence[Any]) -> str:
    categories: list[str] = []
    for issue in defects:
        category = str(getattr(issue, "category", "") or "").strip()
        if category and category not in categories:
            categories.append(category)
    return "composition_safety:" + (",".join(categories) or "unknown")


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


def _remake_reference_kwargs(
    *,
    index: int, style: Any, brand: Any, chapter_plan: Any,
    illustration_plan: Any, infographic_plan: Any, instructions: object,
) -> dict[str, Any]:
    """The deck's own per-slide plan decisions, as the remake service passes them."""

    kwargs: dict[str, Any] = {}
    if chapter_plan is not None:
        kwargs["chapter_prefixes"] = [chapter_plan.eyebrow_prefix_at(index)]
    if illustration_plan is not None:
        kwargs["illustration_slots"] = [illustration_plan.allows(index)]
    if infographic_plan is not None:
        kwargs["infographic_briefs"] = [infographic_plan.at(index)]
        kwargs["infographic_assets"] = build_infographic_asset_context(
            style=style, brand=brand, instructions=instructions
        )
    if deck_has_picture_source(style):
        kwargs["image_service"] = ImageGenerationService(get_images_directory())
    return kwargs


async def _remake_candidate(
    *,
    index: int, html: str, png: bytes, content: str, role: str,
    brand: Any, design_system: str, style: Any, total_slides: int,
    defects: Sequence[Any], reference_kwargs: Mapping[str, Any],
) -> str | None:
    """One bounded full re-author of a single slide, or ``None`` on failure."""

    evidence = "; ".join(
        str(getattr(issue, "detail", "") or "") for issue in defects[:2]
    ).strip()
    detail = LOWER_DENSITY_HINT + (f" Measured defects: {evidence}" if evidence else "")
    overlap = any(getattr(issue, "category", "") == "overlap" for issue in defects)
    critique = SlideCritique(
        needs_fix=True,
        issues=[
            CritiqueIssue(
                type="overlap" if overlap else "other",
                severity="high",
                detail=detail[:200],
            )
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
    *, candidate_html: str, capacity: object, profile: Any, reference_mode: str
) -> tuple[str, bytes, object] | None:
    """Bind fresh pixels/telemetry and admit only a candidate that clears both gates."""

    canonical = ensure_authored_semantic_roles(ensure_authored_qids(candidate_html))
    capture = await render_html_to_png_with_metrics(canonical)
    metrics = capture.browser_metrics
    if metrics is None or capture.png == _placeholder_png():
        return None
    if capacity_violations(canonical, metrics, capacity):
        return None
    if _blocking_defects(
        html=canonical, metrics=metrics, profile=profile, reference_mode=reference_mode
    ):
        return None
    return canonical, capture.png, metrics


async def _remake_one_slide(
    *,
    index: int, html: str, png: bytes, content: str, role: str,
    defects: Sequence[Any], capacity: object, profile: Any,
    reference_mode: str, brand: Any, design_system: str, style: Any,
    total_slides: int, reference_kwargs: Mapping[str, Any],
) -> tuple[tuple[str, bytes, object] | None, str]:
    """One remake plus its admission, as ``(admitted, outcome)``."""

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
            reference_mode=reference_mode,
        )
    except Exception:
        LOGGER.warning(
            "[authored] terminal remake capture failed slide=%d",
            index + 1,
            exc_info=True,
        )
        return None, "remake_capture_failed"
    return admitted, ("remade" if admitted is not None else "remake_gate_failed")


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
) -> TerminalSlideFallback:
    """Remake every ladder-terminal slide once; flag the ones that stay terminal.

    ``htmls``, ``pngs`` and ``metrics_by_slide`` are updated in place for an
    admitted remake so the caller keeps one deck state; the resealed scene
    envelope is returned because it is immutable.

    ``predeclared`` carries slides the generation owner already knows are
    terminal -- today, the ones whose durable Scene repair claim was spent by
    an earlier attempt (``declare_exhausted_repair_budget``). They are treated
    as terminal on the strength of that declaration rather than rediscovered
    from the DOM, so the outcome cannot depend on whether a later gate happens
    to re-report the same defect.
    """

    if not terminal_fallback_active(
        profile=profile, reference_mode=reference_mode, source_reflow=source_reflow
    ):
        return TerminalSlideFallback(scene_envelope=scene_envelope)

    declared_by_index = {
        int(entry["index"]): dict(entry)
        for entry in predeclared
        if isinstance(entry, Mapping) and isinstance(entry.get("index"), int)
    }
    terminal: list[tuple[int, tuple[Any, ...], dict[str, object]]] = []
    for index, html in enumerate(htmls):
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

    flagged: list[dict[str, object]] = []
    remade: list[int] = []
    remake_calls = 0
    for index, defects, declaration in terminal:
        reason = str(declaration.get("reason") or _terminal_reason(defects))
        repeated = declaration.get("repeated")
        repeated = bool(repeated) if isinstance(repeated, bool) else None
        if remake_calls >= MAX_TERMINAL_REMAKES_PER_DECK:
            flagged.append(
                _flagged_entry(
                    index=index, reason=reason, attempted=False,
                    outcome="remake_budget_exhausted", repeated=repeated,
                )
            )
            continue
        remake_calls += 1
        admitted, outcome = await _remake_one_slide(
            index=index, html=htmls[index], png=pngs[index],
            content=str(contents[index]), role=str(roles[index]),
            defects=defects, profile=profile, reference_mode=reference_mode,
            capacity=(capacity_plan.at(index) if capacity_plan is not None else {}),
            brand=brand, design_system=design_system, style=style,
            total_slides=len(htmls),
            reference_kwargs=_remake_reference_kwargs(
                index=index, style=style, brand=brand, chapter_plan=chapter_plan,
                illustration_plan=illustration_plan,
                infographic_plan=infographic_plan, instructions=instructions,
            ),
        )
        if admitted is None:
            flagged.append(
                _flagged_entry(
                    index=index, reason=reason, attempted=True, outcome=outcome,
                    repeated=repeated,
                )
            )
            continue
        selected_html, selected_png, selected_metrics = admitted
        htmls[index] = selected_html
        pngs[index] = selected_png
        metrics_by_slide[index] = selected_metrics
        scene_envelope = replace_deck_scene_slide(
            scene_envelope, slide_index=index, html_projection=selected_html
        )
        remade.append(index)
        attempt_token = getattr(generation_job, "attempt_token", None)
        if generation_job is not None and attempt_token:
            # A resumed attempt must replay the admitted remake, not the
            # terminal draft it replaced. A workspace-linked job's checkpoint
            # write is refused without the lease's fencing token.
            await _complete_stage_after_external_success(
                job_id=generation_job.id, slide_index=index, stage="render",
                attempt_token=attempt_token, html=selected_html,
                png=selected_png, browser_metrics=selected_metrics,
                fencing_token=getattr(generation_job, "_workspace_fencing_token", None),
            )
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
    "MAX_TERMINAL_REMAKES_PER_DECK",
    "TerminalSlideFallback",
    "resolve_terminal_gate_slides",
]
