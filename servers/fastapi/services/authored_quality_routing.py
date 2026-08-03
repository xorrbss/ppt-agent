"""Hierarchical full-deck VQA routing for authored quality reviews."""

import io
import math
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Any

from PIL import Image, ImageDraw

from utils.authored_dom_quality import DomQualityReport
from utils.authored_review_routing import (
    ReviewScope,
    apply_audit_results,
    plan_authored_review_routing,
)
from utils.llm_calls.critique_slide import SlideCritique

CritiqueBatch = Callable[..., Awaitable[Iterable[SlideCritique | None]]]
ObserveCache = Callable[..., tuple[int, int]]
UnusableReason = Callable[[SlideCritique | None], str | None]


def render_review_montage(
    slide_pngs: Mapping[int, bytes], slide_indices: Iterable[int]
) -> bytes:
    """Build one labelled 1280x720 contact sheet for global consistency VQA."""
    selected = tuple(slide_indices)
    if not 1 <= len(selected) <= 9:
        raise ValueError("A review montage must contain between 1 and 9 slides")
    columns = 3 if len(selected) > 4 else 2
    rows = math.ceil(len(selected) / columns)
    cell_width = 1280 // columns
    cell_height = 720 // rows
    canvas = Image.new("RGB", (1280, 720), "#E2E8F0")
    draw = ImageDraw.Draw(canvas)
    for offset, slide_index in enumerate(selected):
        source = Image.open(io.BytesIO(slide_pngs[slide_index])).convert("RGB")
        source.thumbnail((cell_width - 10, cell_height - 10))
        column, row = offset % columns, offset // columns
        left = column * cell_width + (cell_width - source.width) // 2
        top = row * cell_height + (cell_height - source.height) // 2
        canvas.paste(source, (left, top))
        draw.rectangle((left, top, left + 82, top + 24), fill="#0F172A")
        draw.text((left + 7, top + 5), f"Slide {slide_index + 1}", fill="white")
    output = io.BytesIO()
    canvas.save(output, format="PNG")
    return output.getvalue()


async def hybrid_style_initial_review(
    *,
    pngs: list[bytes],
    contexts: list[str],
    positions: list[int],
    slide_count: int,
    local_reports: list[DomQualityReport],
    sampling_seed: str,
    scope: ReviewScope = "all",
    critique_batch: CritiqueBatch,
    observe_cache: ObserveCache,
    unusable_reason: UnusableReason,
) -> tuple[list[SlideCritique | None], dict[str, Any], int, int, float, float]:
    """Run montage + suspect/audit detail VQA and promote failed audits."""
    plan = plan_authored_review_routing(
        scope=scope,
        slide_count=slide_count,
        local_issue_indices=[
            positions[index]
            for index, report in enumerate(local_reports)
            if report.needs_attention
        ],
        target_indices=positions,
        sampling_seed=sampling_seed,
    )
    local_by_position = {position: index for index, position in enumerate(positions)}
    png_by_position = {
        position: pngs[index] for index, position in enumerate(positions)
    }
    montage_pngs = [
        render_review_montage(png_by_position, batch.slide_indices)
        for batch in plan.montage_batches
    ]
    montage_contexts = [
        "Review this labelled 6-9 slide contact sheet for global visual consistency. "
        "Set needs_fix=true for any visible slide defect or cross-slide inconsistency; "
        "a detail full-resolution review will locate it."
        for _ in montage_pngs
    ]
    montage_hits, montage_misses = observe_cache(montage_pngs, montage_contexts)
    montage_started = time.perf_counter()
    montage_critiques = list(
        await critique_batch(montage_pngs, contexts=montage_contexts)
    )
    montage_ms = (time.perf_counter() - montage_started) * 1000
    if len(montage_critiques) != len(plan.montage_batches):
        montage_critiques = (montage_critiques + [None] * len(plan.montage_batches))[
            : len(plan.montage_batches)
        ]

    detail_positions = set(plan.detail_vqa_indices)
    montage_promoted_batches: list[int] = []
    for batch, critique in zip(plan.montage_batches, montage_critiques):
        if unusable_reason(critique) is not None or critique.needs_fix:
            detail_positions.update(batch.slide_indices)
            montage_promoted_batches.append(batch.batch_index)

    critiques_by_position: dict[int, SlideCritique | None] = {}
    detail_hits = detail_misses = 0
    detail_batch_count = 0
    detail_ms = 0.0

    async def review_detail(selected_positions: Iterable[int]) -> None:
        nonlocal detail_hits, detail_misses, detail_batch_count, detail_ms
        selected = tuple(
            position
            for position in sorted(set(selected_positions))
            if position not in critiques_by_position
        )
        if not selected:
            return
        detail_batch_count += 1
        selected_pngs = [pngs[local_by_position[position]] for position in selected]
        selected_contexts = [
            contexts[local_by_position[position]] for position in selected
        ]
        hits, misses = observe_cache(selected_pngs, selected_contexts)
        detail_hits += hits
        detail_misses += misses
        started = time.perf_counter()
        results = list(await critique_batch(selected_pngs, contexts=selected_contexts))
        detail_ms += (time.perf_counter() - started) * 1000
        results = (results + [None] * len(selected))[: len(selected)]
        critiques_by_position.update(zip(selected, results))

    await review_detail(detail_positions)
    failed_audits = [
        position
        for position in plan.clean_audit_indices
        if unusable_reason(critiques_by_position.get(position)) is not None
        or bool(critiques_by_position[position].needs_fix)
    ]
    escalation = apply_audit_results(plan, failed_audit_indices=failed_audits)
    detail_positions.update(escalation.detail_vqa_indices)
    await review_detail(detail_positions)

    clean = SlideCritique(needs_fix=False, issues=[])
    initial = [critiques_by_position.get(position, clean) for position in positions]
    routing = {
        "plan": plan.as_dict(),
        "escalation": escalation.as_dict(),
        "montage_promoted_batches": montage_promoted_batches,
        "montage_vqa_count": len(montage_pngs),
        "detail_vqa_count": len(critiques_by_position),
        "montage_vqa_batch_count": 1 if montage_pngs else 0,
        "detail_vqa_batch_count": detail_batch_count,
        "vqa_batch_count": (1 if montage_pngs else 0) + detail_batch_count,
    }
    return (
        initial,
        routing,
        montage_hits + detail_hits,
        montage_misses + detail_misses,
        montage_ms,
        detail_ms,
    )
