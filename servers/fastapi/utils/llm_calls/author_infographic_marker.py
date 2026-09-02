"""This slide's half of the deck-scoped infographic plan, as prompt text.

The plan decides once, per deck, which figure every page is and which pages
are the deck's hero moments. Each slide is then simply told its own answer
here. ``None`` means no plan ran — say nothing at all, so decks without a plan
keep the pre-plan behaviour where the model composes for itself.
"""

from __future__ import annotations

from models.authored_infographic_plan import AuthoredSlideInfographic
from utils.authored_infographic.contracts import slot_contract

_ARCHETYPE_SHAPES: dict[str, str] = {
    "process": "ordered steps on one directional rail, first to last",
    "cycle": "steps around a closed loop with arrows returning to the first",
    "hierarchy": "a tree of boxes with connectors from parent to children",
    "layers": "stacked horizontal tiers, the foundation at the bottom",
    "comparison": "the options as parallel columns sharing the same criteria rows",
    "before_after": "each measure as a current value and a future value with an arrow between",
    "timeline": "a horizontal line with dated markers in sequence",
    "roadmap": "phases as horizontal bars over a shared time axis",
    "funnel": "stacked bands that narrow from the widest set to the selection",
    "pyramid": "stacked bands that narrow upward from the base",
    "matrix": "a 2x2 grid with both axes labelled and items placed in quadrants",
    "kpi": "a row of large numbers, each with its unit and label directly beside it",
    "gauge": "one large number against its target or range, shown as an arc or bar",
    "icon_list": "parallel items of equal weight, each with one icon and one label",
    "checklist": "criteria as a vertical list, each with a check mark state",
    "hub_spoke": "one central element with the others connected around it",
    "venn": "overlapping circles with the shared part named",
    "swimlane": "parallel horizontal tracks, each with its own steps",
    "branch": "a decision node splitting into labelled alternatives",
    "scene": "one real object drawn large with numbered callouts pointing at its parts",
}

_DENSITY = {
    "low": "labels only; no supporting phrase on the nodes",
    "medium": "each node carries its label and its one supporting phrase",
    "high": "each node carries its label, its supporting phrase and its figure",
}

_WEIGHT = {
    "hero": (
        "This is one of the deck's HERO pages: the figure IS the page. Give it "
        "the whole canvas below the header, with the takeaway as the only "
        "headline and no bullet list beside it."
    ),
    "standard": (
        "This is a STANDARD figure page: the figure takes the larger part of "
        "the canvas and the supporting text sits beside or below it, never on it."
    ),
}


def _node_lines(slide: AuthoredSlideInfographic) -> str:
    lines = []
    for position, node in enumerate(slide.nodes):
        parts = [f'label="{node.label}"']
        if node.desc and slide.density != "low":
            parts.append(f'desc="{node.desc}"')
        if node.before or node.after:
            # The pair is the whole point of a before/after measure, so it is
            # shown whatever the density says about supporting text.
            parts.append(f'before="{node.before or ""}" after="{node.after or ""}"')
        if node.figure and slide.density == "high":
            parts.append(f'figure="{node.figure}"')
        marker = " (EMPHASIS)" if slide.emphasis == position else ""
        lines.append(f"  {position + 1}. " + " ".join(parts) + marker)
    return "\n".join(lines)


def infographic_marker(slide: AuthoredSlideInfographic | None) -> str:
    """Render one slide's figure brief, or nothing when no plan ran."""
    if slide is None:
        return ""
    if not slide.has_figure:
        return (
            "FIGURE FOR THIS SLIDE: NONE. This deck's infographic plan set this "
            "page as a QUIET page of type: no diagram, no card grid, no icon "
            "row. Set the content with a decisive hierarchy and generous "
            "whitespace. That is the expected result — the deck's figures sit "
            "on other pages, and this page is the rest between them."
        )
    takeaway = (
        f'- takeaway (the figure\'s headline, verbatim): "{slide.takeaway}"\n'
        if slide.takeaway
        else ""
    )
    emphasis = (
        "- Single out the node marked EMPHASIS: filled with the primary colour "
        "or accent-outlined, and echo it in the headline.\n"
        if slide.emphasis is not None
        else "- No node is singled out; give the nodes equal visual weight.\n"
    )
    return (
        "FIGURE FOR THIS SLIDE (frozen before parallel authoring; the decision "
        "is made, do not re-argue it):\n"
        f"- archetype: {slide.archetype} — {_ARCHETYPE_SHAPES[slide.archetype]}.\n"
        f"{takeaway}"
        f"- nodes, in this order, labels VERBATIM (never reword, add or drop one):\n"
        f"{_node_lines(slide)}\n"
        f"- density: {slide.density} — {_DENSITY[slide.density]}.\n"
        f"{emphasis}"
        f"- {_WEIGHT[slide.weight]}\n"
        "- Where this brief and the VISUAL STORY PLAN disagree on the figure's "
        "shape, this brief wins. The figure itself is drawn by the archetype "
        "component library under the contract below; the server rejects a "
        "slide that departs from it.\n"
        f"{slot_contract(slide)}"
    )


__all__ = ["infographic_marker"]
