"""Render the filled figure skeleton the model is asked to reproduce.

The infographic plan already holds every label, phrase and figure a page's
figure carries, so the contract does not ask the model to compose the figure:
it hands over the finished markup — classes, slots, stable QIDs and the plan's
own text — and the model's job is to place it in the slide and choose icons.
That is what makes the static contract check exact.
"""

from __future__ import annotations

from models.authored_infographic_plan import AuthoredSlideInfographic

from utils.authored_infographic import skeleton_data as data
from utils.authored_infographic import skeleton_flow as flow
from utils.authored_infographic.skeleton_parts import (
    DEFAULT_NODE_CLASS,
    HUB_ARCHETYPES,
    NODE_CLASS,
    ROOT_CLASS,
    TABLE_ARCHETYPES,
    UNORDERED_ARCHETYPES,
    Ctx,
    density_class,
    figure_qid,
    node_count,
    root,
    split_figure,
)

_BUILDERS = {
    "process": flow.process,
    "cycle": flow.cycle,
    "hub_spoke": flow.hub_spoke,
    "hierarchy": flow.hierarchy,
    "layers": flow.rows("l"),
    "funnel": flow.rows("f"),
    "pyramid": flow.rows("f"),
    "timeline": flow.timeline,
    "roadmap": flow.roadmap,
    "swimlane": flow.swimlane,
    "branch": flow.branch,
    "kpi": flow.cards,
    "icon_list": flow.cards,
    "comparison": data.comparison,
    "before_after": data.before_after,
    "gauge": data.gauge,
    "matrix": data.matrix,
    "checklist": data.checklist,
    "venn": data.venn,
    "scene": data.scene,
}


def render_skeleton(slide: AuthoredSlideInfographic) -> str:
    """The complete figure markup for a planned figure slide."""
    if not slide.has_figure:
        raise ValueError("a quiet page has no figure skeleton")
    ctx = Ctx(slide=slide, qid=figure_qid(slide))
    return root(ctx, _BUILDERS[slide.archetype](ctx))


__all__ = [
    "DEFAULT_NODE_CLASS",
    "HUB_ARCHETYPES",
    "NODE_CLASS",
    "ROOT_CLASS",
    "TABLE_ARCHETYPES",
    "UNORDERED_ARCHETYPES",
    "density_class",
    "figure_qid",
    "node_count",
    "render_skeleton",
    "split_figure",
]
