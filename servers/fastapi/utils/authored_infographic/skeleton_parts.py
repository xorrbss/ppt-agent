"""Shared pieces of the figure skeleton: node markup, figures, hubs, tables.

Every element the skeleton emits carries a stable ``data-qid`` derived from
the slide index and the node position, so the declared-QID validator accepts
the figure as authored and a re-authored page keeps the same identities.
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass

from models.authored_infographic_plan import AuthoredSlideInfographic, InfographicNode

from utils.authored_infographic.icons import ICON_NAMES, icon_svg

_FIGURE_SPLIT = re.compile(r"^\s*([+\-~≈]?[\d][\d.,]*\s*[%배xX×]?)\s*(.*)$")

#: The archetype-root CSS class for every plan archetype.
ROOT_CLASS: dict[str, str] = {
    "process": "ig-process",
    "cycle": "ig-cycle",
    "hierarchy": "ig-hierarchy",
    "layers": "ig-layers",
    "comparison": "ig-compare",
    "before_after": "ig-before-after",
    # Not "ig-timeline": the hybrid exporter reads a whole-word ``timeline``
    # class token on a wrapper as a raster island.
    "timeline": "ig-milestones",
    "roadmap": "ig-roadmap",
    "funnel": "ig-funnel",
    "pyramid": "ig-pyramid",
    "matrix": "ig-matrix",
    "kpi": "ig-kpi",
    "gauge": "ig-gauge",
    "icon_list": "ig-icon-list",
    "checklist": "ig-checklist",
    "hub_spoke": "ig-hub-spoke",
    "venn": "ig-venn",
    "swimlane": "ig-swimlane",
    "branch": "ig-branch",
    "scene": "ig-scene",
}

#: Archetypes whose labels live in table header cells rather than ``.ig-node``.
TABLE_ARCHETYPES = frozenset({"comparison", "before_after"})
#: Archetypes whose first node is the hub/root and is not an orbit node.
HUB_ARCHETYPES = frozenset({"hub_spoke"})
#: Archetypes whose nodes the model may reorder (placement carries the meaning).
UNORDERED_ARCHETYPES = frozenset({"matrix"})
#: The per-node element and its decorative sibling, where not the default pair.
NODE_CLASS: dict[str, tuple[str, str]] = {"scene": ("ig-co", "ig-co-bg")}
DEFAULT_NODE_CLASS = ("ig-node", "ig-node-bg")
MAX_VENN_NODES = 4

#: What every element inside a native table carries instead of a ``data-qid``.
#: ``ensure_authored_qids`` would otherwise mint one per row and cell, and a
#: declared table that owns declared children is refused by the exporter.
NO_QID = ' data-qid-ignore=""'


def esc(text: str | None) -> str:
    return _html.escape((text or "").strip(), quote=True)


def split_figure(figure: str | None) -> tuple[str, str]:
    """``"72시간 전"`` → ``("72", "시간 전")``; a figure with no number is all unit."""
    if not figure:
        return "", ""
    match = _FIGURE_SPLIT.match(figure)
    if match is None or not match.group(1):
        return "", figure.strip()
    return match.group(1).strip(), match.group(2).strip()


def node_count(slide: AuthoredSlideInfographic) -> int:
    """How many per-node elements the figure carries."""
    n = len(slide.nodes)
    if slide.archetype in TABLE_ARCHETYPES:
        return 0
    if slide.archetype in HUB_ARCHETYPES:
        return max(0, n - 1)
    if slide.archetype == "venn":
        return min(n, MAX_VENN_NODES)
    return n


def density_class(slide: AuthoredSlideInfographic) -> str:
    """The ``n3``..``n6`` size class; tables scale by their row count."""
    count = node_count(slide) or len(slide.nodes)
    return f"n{max(3, min(6, count))}"


def figure_qid(slide: AuthoredSlideInfographic) -> str:
    return f"ig.s{slide.index + 1}"


@dataclass(frozen=True)
class Ctx:
    slide: AuthoredSlideInfographic
    qid: str

    def q(self, *parts: str) -> str:
        return f' data-qid="{self.qid}.' + ".".join(parts) + '"'

    def nq(self, i: int, *parts: str) -> str:
        return self.q(f"n{i + 1}", *parts)

    def key(self, i: int) -> str:
        return " is-key" if self.slide.emphasis == i else ""


def icon(ctx: Ctx, i: int) -> str:
    name = ICON_NAMES[i % len(ICON_NAMES)]
    return f'<div class="ig-ic"{ctx.nq(i, "ic")}>{icon_svg(name)}</div>'


def figure_html(ctx: Ctx, i: int, node: InfographicNode) -> str:
    if ctx.slide.density != "high" or not node.figure:
        return ""
    num, unit = split_figure(node.figure)
    unit_html = (
        f'<span class="ig-unit"{ctx.nq(i, "unit")}>{esc(unit)}</span>' if num and unit else ""
    )
    return (
        f'<div class="ig-figure"{ctx.nq(i, "figure")}>'
        f'<span class="ig-num"{ctx.nq(i, "num")}>{esc(num) or esc(unit)}</span>'
        f"{unit_html}</div>"
    )


def tail_html(ctx: Ctx, i: int, node: InfographicNode) -> str:
    figure = figure_html(ctx, i, node)
    return f'<div class="ig-tail"{ctx.nq(i, "tail")}>{figure}</div>' if figure else ""


def desc_html(ctx: Ctx, i: int, node: InfographicNode) -> str:
    if ctx.slide.density == "low" or not node.desc:
        return ""
    return f'<div class="ig-desc"{ctx.nq(i, "desc")}>{esc(node.desc)}</div>'


def label_html(ctx: Ctx, i: int, node: InfographicNode) -> str:
    return f'<div class="ig-label"{ctx.nq(i, "label")}>{esc(node.label)}</div>'


def bg(ctx: Ctx, i: int) -> str:
    return f'<div class="ig-node-bg"{ctx.nq(i, "bg")}></div>'


def card(
    ctx: Ctx, i: int, node: InfographicNode, *, extra: str = "", number: bool = True
) -> str:
    """A column node: icon, number, label, phrase, figure."""
    no = f'<div class="ig-no"{ctx.nq(i, "no")}>{i + 1}</div>' if number else ""
    return (
        f'<div class="ig-node{extra}{ctx.key(i)}"{ctx.nq(i)}>{bg(ctx, i)}'
        f"{icon(ctx, i)}{no}{label_html(ctx, i, node)}{desc_html(ctx, i, node)}"
        f"{figure_html(ctx, i, node)}</div>"
    )


def row(
    ctx: Ctx,
    i: int,
    node: InfographicNode,
    *,
    extra: str = "",
    lead: str = "",
    with_icon: bool = True,
) -> str:
    """A row node: optional lead, icon, text column, figure tail."""
    return (
        f'<div class="ig-node{extra}{ctx.key(i)}"{ctx.nq(i)}>{bg(ctx, i)}{lead}'
        f"{icon(ctx, i) if with_icon else ''}"
        f'<div class="ig-text"{ctx.nq(i, "text")}>{label_html(ctx, i, node)}'
        f"{desc_html(ctx, i, node)}</div>{tail_html(ctx, i, node)}</div>"
    )


def hub(ctx: Ctx, title: str | None, sub: str | None, key: str) -> str:
    sub_html = f'<div class="ig-hub-sub"{ctx.q(key, "sub")}>{esc(sub)}</div>' if sub else ""
    return (
        f'<div class="ig-hub"{ctx.q(key)}><div class="ig-hub-bg"{ctx.q(key, "bg")}></div>'
        f'<div class="ig-hub-title"{ctx.q(key, "title")}>{esc(title)}</div>{sub_html}</div>'
    )


def table(ctx: Ctx, head: str, body: str) -> str:
    """A table the hybrid exporter rebuilds as one editable DrawingML table.

    ``data-native="table"`` is what admits it, and the table element is the
    only QID inside it: the exporter rejects a declared table that owns
    declared children, so every row, cell and inline span below it is marked
    ``data-qid-ignore`` and stays out of the declared namespace.
    """
    return (
        f'<table class="ig-table" data-native="table"{ctx.q("table")}>'
        f"<thead{NO_QID}>{head}</thead><tbody{NO_QID}>{body}</tbody></table>"
    )


def root(ctx: Ctx, inner: str) -> str:
    slide = ctx.slide
    return (
        f'<section class="ig {ROOT_CLASS[slide.archetype]} {density_class(slide)}"'
        f'{ctx.q("root")}>{inner}</section>'
    )


__all__ = [
    "Ctx",
    "DEFAULT_NODE_CLASS",
    "HUB_ARCHETYPES",
    "MAX_VENN_NODES",
    "NODE_CLASS",
    "NO_QID",
    "ROOT_CLASS",
    "TABLE_ARCHETYPES",
    "UNORDERED_ARCHETYPES",
    "bg",
    "card",
    "density_class",
    "desc_html",
    "esc",
    "figure_html",
    "figure_qid",
    "hub",
    "icon",
    "label_html",
    "node_count",
    "root",
    "row",
    "split_figure",
    "table",
    "tail_html",
]
