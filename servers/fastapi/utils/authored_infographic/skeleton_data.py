"""Skeleton builders for the data, list and scene archetypes."""

from __future__ import annotations

import re

from utils.authored_infographic.skeleton_parts import (
    MAX_VENN_NODES,
    NO_QID,
    Ctx,
    bg,
    card,
    desc_html,
    esc,
    icon,
    label_html,
    row,
    split_figure,
    table,
)


def comparison(ctx: Ctx) -> str:
    nodes = ctx.slide.nodes
    head_cells = [f"<th{NO_QID}></th>"]
    for i, node in enumerate(nodes):
        sub = (
            f'<span class="ig-th-sub"{NO_QID}>{esc(node.desc)}</span>'
            if node.desc and ctx.slide.density != "low"
            else ""
        )
        head_cells.append(
            f'<th class="{ctx.key(i).strip()}"{NO_QID}>{esc(node.label)}{sub}</th>'
        )
    rows = []
    # An option whose content states two values (current and future) gets a
    # pair of criteria rows; the model translates the two placeholder words.
    if any(node.before or node.after for node in nodes):
        for key, word in (("before", "BEFORE"), ("after", "AFTER")):
            cells = "".join(
                f'<td class="ig-td-value{ctx.key(i)}"{NO_QID}>'
                f"{esc(getattr(node, key))}</td>"
                for i, node in enumerate(nodes)
            )
            rows.append(
                f'<tr{NO_QID}><th scope="row"{NO_QID}>{word}</th>{cells}</tr>'
            )
    if ctx.slide.density == "high" and any(node.figure for node in nodes):
        cells = "".join(
            f'<td class="ig-td-num{ctx.key(i)}"{NO_QID}>{esc(node.figure)}</td>'
            for i, node in enumerate(nodes)
        )
        rows.append(
            f'<tr{NO_QID}><th scope="row"{NO_QID}>CRITERION</th>{cells}</tr>'
        )
    return table(ctx, f'<tr{NO_QID}>{"".join(head_cells)}</tr>', "".join(rows))


def before_after(ctx: Ctx) -> str:
    nodes = ctx.slide.nodes
    # The delta column exists only when the plan stated a delta for some
    # measure; ``figure`` on a before/after node is that delta, never the pair.
    with_delta = ctx.slide.density == "high" and any(node.figure for node in nodes)
    head = (
        f"<tr{NO_QID}><th{NO_QID}>ITEM</th><th{NO_QID}>BEFORE</th>"
        f"<th{NO_QID}></th><th{NO_QID}>AFTER</th>"
        + (f'<th class="ig-th-delta"{NO_QID}>DELTA</th>' if with_delta else "")
        + "</tr>"
    )
    body = "".join(
        f'<tr class="{ctx.key(i).strip()}"{NO_QID}>'
        f'<th scope="row"{NO_QID}>{esc(node.label)}</th>'
        f'<td class="ig-td-before"{NO_QID}>{esc(node.before or node.desc)}</td>'
        f'<td class="ig-td-arrow"{NO_QID}>→</td>'
        f'<td class="ig-td-after"{NO_QID}>{esc(node.after)}</td>'
        + (
            f'<td class="ig-td-delta"{NO_QID}>{esc(node.figure)}</td>'
            if with_delta
            else ""
        )
        + "</tr>"
        for i, node in enumerate(nodes)
    )
    return table(ctx, head, body)


def gauge(ctx: Ctx) -> str:
    nodes = ctx.slide.nodes
    first = nodes[0]
    num, _unit = split_figure(first.figure)
    digits = re.sub(r"[^\d.]", "", num)
    pct = int(float(digits)) if digits and "%" in (first.figure or "") else 50
    level = max(10, min(100, round(pct / 10) * 10))
    dial_node = card(ctx, 0, first, number=False).replace(icon(ctx, 0), "", 1)
    facts = "".join(row(ctx, i, node) for i, node in enumerate(nodes[1:], start=1))
    return (
        f'<div class="ig-dial"{ctx.q("dial")}>'
        f'<div class="ig-dial-ring v{level}"{ctx.q("dial", "ring")}></div>'
        f'<div class="ig-dial-hole"{ctx.q("dial", "hole")}></div>{dial_node}</div>'
        f'<div class="ig-facts"{ctx.q("facts")}>{facts}</div>'
    )


def matrix(ctx: Ctx) -> str:
    quadrants = ("q-tr", "q-tl", "q-br", "q-bl")
    placed: dict[str, list[str]] = {q: [] for q in quadrants}
    for i, node in enumerate(ctx.slide.nodes):
        placed[quadrants[i % 4]].append(row(ctx, i, node))
    cells = "".join(
        f'<div class="ig-q {q}"{ctx.q(q)}><div class="ig-q-bg"{ctx.q(q, "bg")}></div>'
        f'<div class="ig-qtag"{ctx.q(q, "tag")}>QUADRANT</div>{"".join(placed[q])}</div>'
        for q in ("q-tl", "q-tr", "q-bl", "q-br")
    )
    return (
        f'<div class="ig-ax-y"{ctx.q("ax", "y")}>AXIS Y</div>'
        f'<div class="ig-ax-y-low"{ctx.q("ax", "ylow")}>LOW</div>{cells}'
        f'<div class="ig-ax-x-low"{ctx.q("ax", "xlow")}>LOW</div>'
        f'<div class="ig-ax-x"{ctx.q("ax", "x")}>AXIS X</div>'
    )


def checklist(ctx: Ctx) -> str:
    return "".join(
        row(
            ctx,
            i,
            node,
            extra=" is-done",
            with_icon=False,
            lead=(
                f'<div class="ig-check"{ctx.nq(i, "check")}>'
                f'<div class="ig-check-mark"{ctx.nq(i, "mark")}></div></div>'
            ),
        )
        for i, node in enumerate(ctx.slide.nodes)
    )


def venn(ctx: Ctx) -> str:
    nodes = list(ctx.slide.nodes[:MAX_VENN_NODES])
    sets = "".join(
        f'<div class="ig-set s{i + 1}"{ctx.q(f"s{i + 1}")}></div>' for i in range(min(3, len(nodes)))
    )
    labels = "".join(
        f'<div class="ig-node v{i + 1}{ctx.key(i)}"{ctx.nq(i)}>{bg(ctx, i)}'
        f"{label_html(ctx, i, node)}{desc_html(ctx, i, node)}</div>"
        for i, node in enumerate(nodes)
    )
    core = (
        ""
        if len(nodes) > 3
        else f'<div class="ig-core"{ctx.q("core")}>{esc(ctx.slide.takeaway)}</div>'
    )
    return sets + labels + core


def scene(ctx: Ctx) -> str:
    """The picture, its callouts down either margin, and one pin each.

    Callouts are placed in percentages of the figure, not in pixels: the box a
    scene is given is the authoring model's decision and has been observed as
    narrow as 758px, where a callout pinned at 892px was simply off the slide.
    """
    n = len(ctx.slide.nodes)
    rows = max(1, (n + 1) // 2)
    parts = [
        f'<div class="ig-art"{ctx.q("art")}><img data-illust-prompt="SCENE" alt=""'
        f' style="width:720px;height:405px"{ctx.q("art", "img")}></div>'
    ]
    for i, node in enumerate(ctx.slide.nodes):
        left = 0 if i % 2 == 0 else 74
        top = 5 + (i // 2) * (90 // rows)
        parts.append(
            f'<div class="ig-co{ctx.key(i)}" style="left:{left}%;top:{top}%"{ctx.nq(i)}>'
            f'<div class="ig-co-bg"{ctx.nq(i, "bg")}></div>'
            f'<div class="ig-no"{ctx.nq(i, "no")}>{i + 1}</div>'
            f"{label_html(ctx, i, node)}{desc_html(ctx, i, node)}</div>"
        )
        pin_left = 30 if left == 0 else 66
        parts.append(
            f'<div class="ig-pin" style="left:{pin_left}%;top:{top + 11}%"{ctx.nq(i, "pin")}></div>'
        )
    return "".join(parts)


__all__ = [
    "before_after",
    "checklist",
    "comparison",
    "gauge",
    "matrix",
    "scene",
    "venn",
]
