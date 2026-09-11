"""One bounded repair loop: the only producer of slide HTML besides authoring.

Design ``docs/authored-verbatim-placeholder-authoring-design-20260911.md`` 2.5,
behind ``AUTHORED_VERBATIM_SINGLE_REPAIR_LOOP`` (default off). With the knob off
the four existing post-author repair paths run exactly as they do today. With it
on, on the verbatim (source-reflow) path only, they are replaced by this loop,
whose order is fixed and bounded:

1. the deterministic repairs that need no model
   (``services/authored_repair_loop_deterministic.py``);
2. at most ``N`` model repair calls (``AUTHORED_VERBATIM_REPAIR_LOOP_MODEL_CALLS``,
   default 1) through the one authoring prompt, bound by the placeholder token
   contract (``services/authored_repair_loop_model.py``);
3. the existing terminal policy, unchanged -- the caller declares an unadmitted
   repair and the terminal fallback decides flag-only publication or a coded
   failure, exactly as before.

Every candidate is assessed by the same evaluator the gate builds for the slide,
so no candidate is admitted on a weaker check than authoring, and a rejected
candidate leaves the frozen previous admission in place: the loop's selection
only ever moves forward onto an assessed pair of bytes and metrics.

The vision review is an evidence source for step 2's prompt; it never writes
HTML here. The ``setText``-class patch contracts are not reachable from this
loop at all.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from services.authored_repair_loop_deterministic import deterministic_candidate, digest
from services.authored_repair_loop_evidence import merge_evidence, reason_evidence
from services.authored_structural_repair import SlideRepairResult, bind_repair_candidate
from utils.get_env_programme import (
    get_authored_verbatim_repair_loop_model_calls,
    is_authored_verbatim_single_repair_loop_enabled,
)
from utils.llm_calls.author_structural_repair_evidence import _advance_repair_evaluator

LOGGER = logging.getLogger(__name__)

#: Receipt path recorded for every cycle this loop produces, so a reader can
#: tell a single-loop deck from a structural-patch one without guessing.
REPAIR_LOOP_PATH = "single_repair_loop"


def single_repair_loop_enabled(*, source_reflow: bool) -> bool:
    """The loop owns the verbatim path only; ordinary decks are untouched."""

    return bool(source_reflow) and is_authored_verbatim_single_repair_loop_enabled()


def repair_loop_model_calls() -> int:
    return get_authored_verbatim_repair_loop_model_calls()


@dataclass(frozen=True, slots=True)
class RepairLoopSlide:
    """One flagged slide's frozen input to the loop."""

    slide_index: int
    local_index: int
    html: str
    png: bytes
    metrics: Mapping[str, Any]
    deterministic_candidate: str
    provider_allowed: bool
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RepairLoopDeps:
    """Everything the loop calls, supplied by the generation stage."""

    evaluators: Mapping[int, Callable[[int, str, Mapping[str, Any]], Awaitable[Any]]]
    capture: Callable[[str, int], Awaitable[tuple[str, bytes, Mapping[str, Any]]]]
    propose: Callable[[int, tuple[str, ...]], Awaitable[str | None]]
    #: Bounded evidence for the current bytes; the loop re-reads it before each
    #: model call so the second call is briefed on what the first left behind.
    measure: Callable[[int, str, Mapping[str, Any]], tuple[str, ...]] | None = None
    model_calls: int = 1
    details_sink: list[dict[str, Any]] | None = None


@dataclass
class _Selection:
    html: str
    png: bytes
    metrics: dict[str, Any]
    assessment: Any
    history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def admitted(self) -> bool:
        return self.assessment is not None and self.assessment.admitted


async def _model_cycle(
    slide: RepairLoopSlide,
    selection: _Selection,
    *,
    cycle: int,
    deps: RepairLoopDeps,
    evaluator: Callable[[int, str, Mapping[str, Any]], Awaitable[Any]],
    seen: set[str],
) -> bool:
    """One model repair call. ``True`` when the loop may spend another."""

    index = slide.slide_index
    started = time.monotonic()
    receipt: dict[str, Any] = {
        "cycle": cycle,
        "path": REPAIR_LOOP_PATH,
        "proposal_calls": 1,
        "capture_calls": 0,
        "selected_html_digest": digest(selection.html),
    }
    try:
        measured = (
            deps.measure(index, selection.html, selection.metrics)
            if deps.measure is not None
            else ()
        )
        evidence = merge_evidence(
            slide.evidence,
            measured,
            reason_evidence(getattr(selection.assessment, "reasons", ()) or ()),
        )
        candidate = await deps.propose(index, evidence)
        if not candidate:
            receipt["stop_reason"] = "model_candidate_unavailable"
            return False
        if digest(candidate) in seen:
            receipt["stop_reason"] = "duplicate_candidate_digest"
            return False
        seen.add(digest(candidate))
        html, png, metrics = await deps.capture(candidate, index)
        receipt["capture_calls"] = 1
        if digest(html) in seen and digest(html) != digest(candidate):
            receipt["stop_reason"] = "duplicate_candidate_digest"
            return False
        seen.add(digest(html))
        assessment = await evaluator(index, html, metrics)
        receipt["decision"] = assessment.decision
        receipt["reasons"] = list(getattr(assessment, "reasons", ()))
        receipt["html_digest"] = digest(html)
        if assessment.decision not in {"provisional", "admitted"}:
            # The frozen previous admission stays selected.
            receipt["stop_reason"] = "model_candidate_rejected"
            return True
        selection.html, selection.png = html, png
        selection.metrics = dict(metrics)
        selection.assessment = assessment
        _advance_repair_evaluator(evaluator, assessment, selection.html, selection.metrics)
        receipt["stop_reason"] = (
            "admitted" if assessment.admitted else "provisional_selected"
        )
        return not assessment.admitted
    except Exception as error:
        LOGGER.warning(
            "[authored-repair-loop] slide %d model cycle %d failed (%s)",
            index + 1,
            cycle,
            type(error).__name__,
        )
        receipt["stop_reason"] = "repair_candidate_failed"
        receipt["error_type"] = type(error).__name__
        return False
    finally:
        receipt["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
        selection.history.append(receipt)


async def _repair_slide(slide: RepairLoopSlide, *, deps: RepairLoopDeps) -> SlideRepairResult:
    index = slide.slide_index
    evaluator = deps.evaluators[index]
    metrics = dict(slide.metrics or {})
    assessment = await evaluator(index, slide.html, metrics)
    baseline_digest = str(
        assessment.evidence.get("baseline_html_digest") or digest(slide.html)
    )
    selection = _Selection(slide.html, slide.png, metrics, assessment)
    seen = {digest(slide.html)}
    if not selection.admitted:
        accepted, receipt = await deterministic_candidate(
            slide_index=index,
            current_html=selection.html,
            candidate_html=slide.deterministic_candidate,
            evaluator=evaluator,
            capture=deps.capture,
        )
        selection.history.append(receipt)
        if accepted is not None:
            html, png, candidate_metrics, candidate_assessment = accepted
            selection.html, selection.png = html, png
            selection.metrics = candidate_metrics
            selection.assessment = candidate_assessment
            seen.add(digest(html))
            _advance_repair_evaluator(
                evaluator, candidate_assessment, selection.html, selection.metrics
            )
    budget = max(0, int(deps.model_calls)) if slide.provider_allowed else 0
    if not selection.admitted and budget == 0:
        selection.history.append(
            {
                "cycle": 0,
                "path": REPAIR_LOOP_PATH,
                "stop_reason": (
                    "provider_recovery_blocked"
                    if not slide.provider_allowed
                    else "model_budget_disabled"
                ),
            }
        )
    for cycle in range(budget):
        if selection.admitted:
            break
        if not await _model_cycle(
            slide, selection, cycle=cycle, deps=deps, evaluator=evaluator, seen=seen
        ):
            break
    bound = bind_repair_candidate(
        selection.assessment, selection.html, selection.png, selection.metrics
    )
    return SlideRepairResult(
        local_index=slide.local_index,
        slide_index=index,
        baseline_html_digest=baseline_digest,
        html=selection.html,
        png=selection.png,
        metrics=selection.metrics,
        assessment=bound,
        changed=digest(selection.html) != baseline_digest,
        cycle_history=selection.history,
    )


def loop_slides(
    *,
    flagged: Sequence[int],
    htmls: Sequence[str],
    pngs: Sequence[bytes],
    metrics_by_index: Mapping[int, Mapping[str, Any]],
    deterministic_by_index: Mapping[int, str],
    provider_allowed: Sequence[bool],
    evidence_by_index: Mapping[int, Sequence[str]] | None = None,
) -> list[RepairLoopSlide]:
    """The flagged slides' frozen inputs, in the caller's own order."""

    evidence_by_index = evidence_by_index or {}
    return [
        RepairLoopSlide(
            slide_index=index,
            local_index=local_index,
            html=htmls[index],
            png=pngs[index],
            metrics=metrics_by_index[index],
            deterministic_candidate=deterministic_by_index[index],
            provider_allowed=bool(provider_allowed[local_index]),
            evidence=tuple(evidence_by_index.get(index, ())),
        )
        for local_index, index in enumerate(flagged)
    ]


async def run_repair_loop(
    slides: Sequence[RepairLoopSlide], *, deps: RepairLoopDeps
) -> list[SlideRepairResult]:
    """Repair every flagged slide once, in the fixed order, bounded by ``N``."""

    results = list(await asyncio.gather(*(_repair_slide(slide, deps=deps) for slide in slides)))
    LOGGER.info(
        "[authored-repair-loop] slides=%d admitted=%d model_calls=%d budget=%d",
        len(results),
        sum(1 for result in results if result.final_status == "admitted"),
        sum(
            int(entry.get("proposal_calls") or 0)
            for result in results
            for entry in result.cycle_history
        ),
        max(0, int(deps.model_calls)),
    )
    if deps.details_sink is not None:
        deps.details_sink.extend(
            {
                "local_index": result.local_index,
                "slide_index": result.slide_index,
                "path": REPAIR_LOOP_PATH,
                "attempted": bool(result.cycle_history),
                "succeeded": result.final_status == "admitted",
                "critique_source": "measured",
                "candidate_status": (
                    result.assessment.decision if result.assessment else "reject"
                ),
                "cycle_history": result.cycle_history,
                "model_calls": sum(
                    int(entry.get("proposal_calls") or 0) for entry in result.cycle_history
                ),
            }
            for result in results
        )
    return results


__all__ = [
    "REPAIR_LOOP_PATH",
    "RepairLoopDeps",
    "RepairLoopSlide",
    "loop_slides",
    "repair_loop_model_calls",
    "run_repair_loop",
    "single_repair_loop_enabled",
]
