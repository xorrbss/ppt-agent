"""The generation stage's one call into the single repair loop.

Everything the loop needs is assembled here so the generation stage keeps one
switch point instead of a second copy of the repair plumbing. This module owns
no policy: the evaluator, the capture, the deterministic candidate, the
terminal decision and the admission gate all belong to their existing owners.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Awaitable, Callable

from services.authored_composition_policy import blocking_safety_issues
from services.authored_repair_loop import (
    RepairLoopDeps,
    loop_slides,
    repair_loop_model_calls,
    run_repair_loop,
)
from services.authored_repair_loop_evidence import evidence_for_slide
from services.authored_repair_loop_model import build_repair_author
from services.authored_structural_repair import SlideRepairResult
from utils.authored_dom_quality import inspect_authored_dom


async def run_verbatim_repair_loop(
    *,
    flagged: Sequence[int],
    htmls: Sequence[str],
    pngs: Sequence[bytes],
    roles: Sequence[str],
    contents: Sequence[str],
    brand: Any,
    design_system: str,
    capacity_briefs: Sequence[str],
    frozen_gate_inputs: Mapping[int, tuple[Any, ...]],
    deterministic_by_index: Mapping[int, str],
    provider_allowed: Sequence[bool],
    evaluators: Mapping[int, Callable[..., Awaitable[Any]]],
    capture: Callable[[str, int], Awaitable[tuple[str, bytes, Mapping[str, Any]]]],
    reference_mode: str,
    source_issues_by_index: Mapping[int, Sequence[Any]],
    vqa_evidence: Mapping[int, Any],
    plans: tuple[Any, Any, Any],
    page_number_mode: str,
    instructions: Any,
    source_images_by_slide: Sequence[Mapping[str, Any]] | None,
    generation_job: Any = None,
    details_sink: list[dict[str, Any]] | None = None,
) -> list[SlideRepairResult]:
    """Run the loop over the flagged slides and return their repair results."""

    chapter_plan, illustration_plan, infographic_plan = plans

    def measure(index: int, html: str, metrics: Mapping[str, Any] | None) -> tuple[str, ...]:
        return evidence_for_slide(
            html=html,
            metrics=metrics,
            inspect=inspect_authored_dom,
            blocking=blocking_safety_issues,
            reference_mode=reference_mode,
            strict_structural_off_canvas=True,
            source_issues=source_issues_by_index.get(index, ()),
            critique=vqa_evidence.get(index),
        )

    job_id = generation_job.id if generation_job is not None else None
    attempt_number = generation_job.attempt_number if generation_job is not None else 0
    return await run_repair_loop(
        loop_slides(
            flagged=flagged,
            htmls=htmls,
            pngs=pngs,
            metrics_by_index={index: frozen_gate_inputs[index][2] for index in flagged},
            deterministic_by_index=deterministic_by_index,
            provider_allowed=provider_allowed,
            evidence_by_index={
                index: measure(index, htmls[index], frozen_gate_inputs[index][2])
                for index in flagged
            },
        ),
        deps=RepairLoopDeps(
            evaluators=evaluators,
            capture=capture,
            propose=build_repair_author(
                contents=contents,
                roles=roles,
                brand=brand,
                design_system=design_system,
                capacity_briefs=capacity_briefs,
                page_number_mode=page_number_mode,
                instructions=instructions,
                source_images_by_slide=source_images_by_slide,
                reference_mode=reference_mode,
                chapter_plan=chapter_plan,
                illustration_plan=illustration_plan,
                infographic_plan=infographic_plan,
                job_id=job_id,
                attempt_number=attempt_number,
            ),
            measure=measure,
            model_calls=repair_loop_model_calls(),
            details_sink=details_sink,
        ),
    )


__all__ = ["run_verbatim_repair_loop"]
