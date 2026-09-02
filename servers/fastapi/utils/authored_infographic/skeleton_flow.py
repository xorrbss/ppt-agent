"""Skeleton builders for the flow and structure archetypes."""

from __future__ import annotations

import re

from utils.authored_infographic.skeleton_parts import (
    Ctx,
    bg,
    card,
    desc_html,
    esc,
    hub,
    icon,
    label_html,
    row,
    tail_html,
)

_STEP_SPLIT = re.compile(r"\s*(?:,|·|/|→|->|>|\|)\s*")


def process(ctx: Ctx) -> str:
    parts = []
    for i, node in enumerate(ctx.slide.nodes):
        if i:
            parts.append(
                f'<div class="ig-arrow"{ctx.q(f"a{i}")}>'
                f'<div class="ig-arrow-line"{ctx.q(f"a{i}", "line")}></div>'
                f'<div class="ig-arrow-head"{ctx.q(f"a{i}", "head")}></div></div>'
            )
        parts.append(card(ctx, i, node))
    return "".join(parts)


def _orbit(ctx: Ctx, nodes, offset: int, spokes: bool) -> str:
    parts = []
    if spokes:
        parts.extend(
            f'<div class="ig-spoke k{i + 1}"{ctx.q(f"s{i + 1}")}></div>' for i in range(len(nodes))
        )
    else:
        # One arrowhead per segment, sitting on the ring midway between two
        # consecutive stations; the library places it by its h<i> class.
        parts.append(f'<div class="ig-ring"{ctx.q("ring")}></div>')
        parts.extend(
            f'<div class="ig-ring-head h{i + 1}"{ctx.q("ring", f"head{i + 1}")}></div>'
            for i in range(len(nodes))
        )
    for i, node in enumerate(nodes):
        parts.append(card(ctx, i + offset, node, extra=f" p{i + 1}", number=not spokes))
    return "".join(parts)


def cycle(ctx: Ctx) -> str:
    return _orbit(ctx, list(ctx.slide.nodes), 0, False) + hub(ctx, ctx.slide.takeaway, None, "hub")


def hub_spoke(ctx: Ctx) -> str:
    first = ctx.slide.nodes[0]
    # The hub comes first so the label slots read in the plan's node order.
    return hub(ctx, first.label, first.desc, "n1") + _orbit(ctx, list(ctx.slide.nodes[1:]), 1, True)


def hierarchy(ctx: Ctx) -> str:
    nodes = ctx.slide.nodes
    children = "".join(
        f'<div class="ig-child"{ctx.q(f"c{i}")}><div class="ig-stem"{ctx.q(f"c{i}", "stem")}></div>'
        f"{card(ctx, i, node, number=False)}</div>"
        for i, node in enumerate(nodes[1:], start=1)
    )
    return (
        card(ctx, 0, nodes[0], extra=" ig-root", number=False)
        + f'<div class="ig-stem"{ctx.q("stem")}></div><div class="ig-bar"{ctx.q("bar")}></div>'
        + f'<div class="ig-children"{ctx.q("children")}>{children}</div>'
    )


def rows(prefix: str):
    """Row nodes carrying ``<prefix>1``..``<prefix>N`` position classes."""

    def build(ctx: Ctx) -> str:
        return "".join(
            row(ctx, i, node, extra=f" {prefix}{i + 1}") for i, node in enumerate(ctx.slide.nodes)
        )

    return build


def timeline(ctx: Ctx) -> str:
    items = []
    for i, node in enumerate(ctx.slide.nodes):
        when = node.figure or f"{i + 1}"
        items.append(
            f'<div class="ig-node{ctx.key(i)}"{ctx.nq(i)}>{bg(ctx, i)}'
            f'<div class="ig-dot"{ctx.nq(i, "dot")}></div>'
            f'<div class="ig-when"{ctx.nq(i, "when")}>{esc(when)}</div>'
            f"{label_html(ctx, i, node)}{desc_html(ctx, i, node)}</div>"
        )
    return (
        f'<div class="ig-rail"{ctx.q("rail")}></div>'
        f'<div class="ig-track"{ctx.q("track")}>{"".join(items)}</div>'
    )


def roadmap_bar(i: int, n: int) -> tuple[int, int]:
    """The (offset, width) percentage classes of phase ``i`` of ``n``.

    Phases are spread across the track in order, and the bar always ends
    inside it: offset + width never exceeds 100, whatever the count.
    """
    width = 40 if n <= 4 else 20
    span = 100 - width
    raw = (span * i) / max(1, n - 1)
    offset = min(80, int(round(raw / 20)) * 20)
    return offset, min(width, 100 - offset)


def roadmap(ctx: Ctx) -> str:
    n = len(ctx.slide.nodes)
    parts = []
    for i, node in enumerate(ctx.slide.nodes):
        offset, width = roadmap_bar(i, n)
        parts.append(
            f'<div class="ig-node{ctx.key(i)}"{ctx.nq(i)}>{bg(ctx, i)}'
            f'<div class="ig-head"{ctx.nq(i, "head")}>{label_html(ctx, i, node)}'
            f"{desc_html(ctx, i, node)}</div>"
            f'<div class="ig-track"{ctx.nq(i, "track")}>'
            f'<div class="ig-fill o{offset} w{width}"{ctx.nq(i, "fill")}></div></div>'
            f"{tail_html(ctx, i, node)}</div>"
        )
    return "".join(parts)


def swimlane(ctx: Ctx) -> str:
    parts = []
    for i, node in enumerate(ctx.slide.nodes):
        steps = [s for s in _STEP_SPLIT.split(node.desc or "") if s] or [node.label]
        chips = []
        for j, step in enumerate(steps[:5]):
            if j:
                chips.append(f'<div class="ig-step-arrow"{ctx.nq(i, f"arrow{j}")}></div>')
            chips.append(f'<div class="ig-step"{ctx.nq(i, f"step{j + 1}")}>{esc(step)}</div>')
        parts.append(
            f'<div class="ig-node{ctx.key(i)}"{ctx.nq(i)}>{bg(ctx, i)}{icon(ctx, i)}'
            f'<div class="ig-head"{ctx.nq(i, "head")}>{label_html(ctx, i, node)}</div>'
            f'<div class="ig-steps"{ctx.nq(i, "steps")}>{"".join(chips)}</div></div>'
        )
    return "".join(parts)


def branch(ctx: Ctx) -> str:
    nodes = ctx.slide.nodes
    outcomes = "".join(row(ctx, i, node) for i, node in enumerate(nodes[1:], start=1))
    return (
        card(ctx, 0, nodes[0], extra=" ig-root", number=False)
        + f'<div class="ig-fork"{ctx.q("fork")}><div class="ig-fork-in"{ctx.q("fork", "in")}></div>'
        f'<div class="ig-fork-v"{ctx.q("fork", "v")}></div></div>'
        + f'<div class="ig-outcomes"{ctx.q("outcomes")}>{outcomes}</div>'
    )


def cards(ctx: Ctx) -> str:
    return "".join(card(ctx, i, node, number=False) for i, node in enumerate(ctx.slide.nodes))


__all__ = [
    "branch",
    "cards",
    "cycle",
    "hierarchy",
    "hub_spoke",
    "process",
    "roadmap",
    "roadmap_bar",
    "rows",
    "swimlane",
    "timeline",
]
