"""Content flow: source-order segments, balanced columns and wide rows.

Extracted from ``render_chrome`` unchanged. The content area is a vertical
sequence of segments in source order: a ``comparison``/``process``/``table``
component (or any other kind outside ``FLOW_KINDS``) never shares a balanced
column with flow text -- it renders as its own full-width row -- while runs of
flow components (paragraph/bullets/numbered/quote/kpi/caption) between the
wide rows are balanced across ``params.columns`` columns by character weight
at paragraph boundaries.

``render_chrome`` owns the bands around this sequence and decides what the
content section itself looks like; everything between the bands is built here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from services.composer.plan import ComposerComponent, ComposerSlidePlan
from services.composer.render_components import FLOW_KINDS, RenderContext, render_component
from services.composer.render_text import open_tag


@dataclass(frozen=True)
class FlowUnit:
    """One paragraph of a flow component, or a whole component (``index`` None)."""

    component: ComposerComponent
    index: int | None
    weight: float


def _paragraph_weight(text: str) -> float:
    return max(len(text), 8) + 20.0


#: One content segment: a run of balanced flow units, or one standalone wide row.
Segment = tuple[str, "list[FlowUnit] | ComposerComponent"]


def content_segments(plan: ComposerSlidePlan, ctx: RenderContext) -> list[Segment]:
    """Split ``plan.components`` into flow runs and standalone wide rows, in
    source order. Only ``FLOW_KINDS`` ever share a balanced column; a
    ``comparison``/``process``/``table`` component -- or any other kind, as a
    defensive default -- always gets its own full-width row instead, so a
    large block is never squeezed into half a column (the defect this
    module's docstring and ``docs/`` evidence describe)."""

    segments: list[Segment] = []
    current: list[FlowUnit] = []
    if plan.subtitle is not None and not ctx.geometry.subtitle_in_band:
        current.extend(
            FlowUnit(plan.subtitle, index, _paragraph_weight(paragraph.text))
            for index, paragraph in enumerate(plan.subtitle.paragraphs)
        )
    for component in plan.components:
        if component.kind in FLOW_KINDS:
            current.extend(
                FlowUnit(component, index, _paragraph_weight(paragraph.text))
                for index, paragraph in enumerate(component.paragraphs)
            )
            continue
        if current:
            segments.append(("flow", current))
            current = []
        segments.append(("wide", component))
    if current:
        segments.append(("flow", current))
    return segments


def has_wide_row(segments: list[Segment]) -> bool:
    return any(kind == "wide" for kind, _payload in segments)


def balanced_partition(weights: list[float], columns: int) -> list[tuple[int, int]]:
    """Contiguous ``[start, end)`` ranges minimizing the heaviest column."""

    count = len(weights)
    columns = max(1, min(int(columns), count)) if count else 1
    if count == 0:
        return [(0, 0)]
    prefix = [0.0]
    for weight in weights:
        prefix.append(prefix[-1] + weight)
    best = [[math.inf] * (columns + 1) for _ in range(count + 1)]
    cut = [[0] * (columns + 1) for _ in range(count + 1)]
    best[0][0] = 0.0
    for end in range(1, count + 1):
        for parts in range(1, min(columns, end) + 1):
            for start in range(parts - 1, end):
                candidate = max(best[start][parts - 1], prefix[end] - prefix[start])
                if candidate < best[end][parts]:
                    best[end][parts] = candidate
                    cut[end][parts] = start
    ranges: list[tuple[int, int]] = []
    end = count
    for parts in range(columns, 0, -1):
        start = cut[end][parts]
        ranges.append((start, end))
        end = start
    ranges.reverse()
    return ranges


def _render_units(units: list[FlowUnit], ctx: RenderContext) -> str:
    """Render consecutive units, merging a component's paragraphs into one container."""

    out: list[str] = []
    position = 0
    while position < len(units):
        unit = units[position]
        if unit.index is None:
            out.append(render_component(unit.component, ctx))
            position += 1
            continue
        selection = [unit.index]
        while position + 1 < len(units) and units[position + 1].component is unit.component:
            position += 1
            selection.append(units[position].index or 0)
        out.append(render_component(unit.component, ctx, selection=selection))
        position += 1
    return "".join(out)


def _columns(units: list[FlowUnit], ctx: RenderContext, columns: int) -> str:
    ranges = balanced_partition([unit.weight for unit in units], columns)
    out: list[str] = []
    for start, end in ranges:
        out.append(open_tag("div", qid=ctx.qids.claim("col"), role="decoration", css="cx-col"))
        out.append(_render_units(units[start:end], ctx))
        out.append("</div>")
    return "".join(out)


def _flow_region(units: list[FlowUnit], ctx: RenderContext, columns: int) -> str:
    """One balanced-column grid for a run of flow units between wide rows."""

    span = max(1, min(columns, len(units))) if units else 1
    style = f"grid-template-columns:repeat({span},minmax(0,1fr))"
    out = [open_tag("div", qid=ctx.qids.claim("flow"), role="decoration", css="cx-region", style=style)]
    out.append(_columns(units, ctx, span))
    out.append("</div>")
    return "".join(out)


def _wide_row(component: ComposerComponent, ctx: RenderContext) -> str:
    """A ``comparison``/``process``/``table`` (or other non-flow) component,
    full width, never squeezed into a balanced column."""

    out = [
        open_tag(
            "div",
            qid=ctx.qids.claim(f"{component.component_id}-row"),
            role="decoration",
            css="cx-row",
            style="grid-column:1 / -1",
        )
    ]
    out.append(render_component(component, ctx))
    out.append("</div>")
    return "".join(out)


def render_segments(segments: list[Segment], ctx: RenderContext, columns: int) -> str:
    out: list[str] = []
    for kind, payload in segments:
        if kind == "wide":
            out.append(_wide_row(payload, ctx))
        else:
            out.append(_flow_region(payload, ctx, columns))
    return "".join(out)


__all__ = [
    "FlowUnit",
    "Segment",
    "balanced_partition",
    "content_segments",
    "has_wide_row",
    "render_segments",
]
