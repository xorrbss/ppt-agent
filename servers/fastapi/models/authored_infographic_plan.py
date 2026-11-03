"""Deck-scoped authored infographic plan.

Which explanatory figure a slide is drawn as — a cycle, a funnel, a 2x2 matrix,
a stack of layers — is decided ONCE per deck, before any slide is authored, and
replayed. The decision has to be deck-scoped for the same reason the chapter
plan is: slides are authored independently and in parallel, and a deck whose
every page picked its own figure alone came back as forty near-identical card
grids. The plan also carries the deck's rhythm — which pages are the big
visual moments and which are deliberately quiet — because that too is a
comparison between pages that no single page can make.

The plan is prompt metadata for the authoring model. It decides WHAT figure a
slide is and which labels it carries; the archetype component library decides
how that figure is drawn. Nothing here renders.

Two shapes live here:

``AuthoredInfographicPlanDraft``
    What the planning model returns. Its entries may be incomplete, out of
    order or over budget; ``coerce_infographic_plan`` repairs them.

``AuthoredInfographicPlan``
    What the deck is authored and published against: exactly one entry per
    FINAL outline slide, in outline order. This is what gets persisted.

The old deck-wide illustration budget (a floor of three pictures, 15% of the
deck) is replaced by this plan: a slide carries a generated picture only when
its archetype is ``scene``, and the budget is now the plan's own rhythm plus
one hard ceiling in code.
"""

from __future__ import annotations

import re
from typing import Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

INFOGRAPHIC_PLAN_SCHEMA_VERSION = "authored-infographic-plan/v1"

#: Every figure the archetype component library draws, plus ``none`` for a
#: page that is set as type. Kept as a flat vocabulary so the plan, the prompt
#: and the component library all name the same twenty figures.
Archetype = Literal[
    "none",
    "process",
    "cycle",
    "hierarchy",
    "layers",
    "comparison",
    "before_after",
    "timeline",
    "roadmap",
    "funnel",
    "pyramid",
    "matrix",
    "kpi",
    "gauge",
    "icon_list",
    "checklist",
    "hub_spoke",
    "venn",
    "swimlane",
    "branch",
    "scene",
]

ARCHETYPES: tuple[str, ...] = (
    "none",
    "process",
    "cycle",
    "hierarchy",
    "layers",
    "comparison",
    "before_after",
    "timeline",
    "roadmap",
    "funnel",
    "pyramid",
    "matrix",
    "kpi",
    "gauge",
    "icon_list",
    "checklist",
    "hub_spoke",
    "venn",
    "swimlane",
    "branch",
    "scene",
)

#: How much of the canvas a slide's figure claims. ``hero`` is a page that IS
#: its figure, ``standard`` pairs a figure with supporting text, ``quiet`` is a
#: page of type — the rest a reader needs between two figures.
Weight = Literal["hero", "standard", "quiet"]

#: How much supporting text each node carries. The node COUNT is the length of
#: ``nodes``; density is the second knob the best-of-N stage varies without
#: another model call.
Density = Literal["low", "medium", "high"]

#: Roles whose slides are never drawn as a figure.
NON_FIGURE_ROLES = frozenset({"COVER", "CLOSING", "SECTION", "AGENDA"})

MAX_TAKEAWAY_CHARS = 12
MAX_NODE_LABEL_CHARS = 10
MAX_NODE_DESC_CHARS = 18
MAX_NODE_FIGURE_CHARS = 12
#: A before/after pair: the current value and the future value of one measure.
MAX_NODE_VALUE_CHARS = 12
MAX_ART_DIRECTION_CHARS = 120

_PAIR_SPLIT = re.compile(r"\s*(?:→|->|⇒|=>|>>)\s*")
MIN_NODES = 3
MAX_NODES = 6

#: The one budget that survives from the old illustration plan: a hard ceiling
#: on how many content slides may be a ``scene``, which is the only archetype
#: that spends an image-model call. It is a guard against a pathological
#: answer, not a target — the plan's own rhythm decides the ordinary count.
MAX_SCENE_SHARE = 0.25
MIN_SCENE_CEILING = 1
#: What a scene that lost its picture becomes: the closest figure that is pure
#: HTML — one icon and one label per node, arranged as the scene's callouts
#: would have been.
SCENE_FALLBACK_ARCHETYPE: Archetype = "icon_list"

_WHITESPACE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    return _WHITESPACE.sub("", (text or "").strip()).lower()


def _clip(text: Optional[str], limit: int) -> Optional[str]:
    """Whitespace-normalize and drop a string that is far beyond its budget.

    A string a little over budget is kept: the component's slot clips it and
    the vision critic can shorten it. One that is more than twice the budget is
    not a label the model shortened but a sentence it failed to, and it is
    dropped so the slot is empty rather than overflowing.
    """
    if text is None:
        return None
    cleaned = _WHITESPACE.sub(" ", text).strip()
    if not cleaned or len(cleaned) > limit * 2:
        return None
    return cleaned


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InfographicNode(_Spec):
    """One labelled element of a figure: a step, a layer, a KPI, a quadrant."""

    #: Copied verbatim from the slide's own content, never paraphrased. The
    #: plan enforces this by dropping any label the content does not contain.
    label: str = Field(min_length=1, max_length=MAX_NODE_LABEL_CHARS * 2)
    #: One short supporting phrase; absent for a low-density figure.
    desc: Optional[str] = Field(default=None, max_length=MAX_NODE_DESC_CHARS * 2)
    #: A number with its unit, when the node carries one ("72시간 전", "4.6%").
    #: For a before/after measure this is the stated delta or summary only.
    figure: Optional[str] = Field(default=None, max_length=MAX_NODE_FIGURE_CHARS * 2)
    #: The current and the future value of one measure ("4.6%" → "2.2%"), each
    #: verbatim with its unit. Only before_after and comparison figures use them.
    before: Optional[str] = Field(default=None, max_length=MAX_NODE_VALUE_CHARS * 2)
    after: Optional[str] = Field(default=None, max_length=MAX_NODE_VALUE_CHARS * 2)


class AuthoredSlideInfographic(_Spec):
    """One slide's frozen figure: the archetype, its weight and its labels."""

    index: int = Field(ge=0)
    archetype: Archetype = "none"
    weight: Weight = "quiet"
    #: The one sentence the figure exists to make; the headline of the figure.
    takeaway: Optional[str] = Field(default=None, max_length=MAX_TAKEAWAY_CHARS * 2)
    nodes: tuple[InfographicNode, ...] = ()
    #: Which node the figure singles out, as an index into ``nodes``.
    emphasis: Optional[int] = Field(default=None, ge=0)
    density: Density = "medium"
    #: For a scene: whether its picture is best a photograph of a real thing
    #: (``photo``) or a drawn concept (``concept``); None for every other figure.
    depiction: Optional[Literal["photo", "concept"]] = None

    @model_validator(mode="after")
    def _figure_is_consistent(self) -> "AuthoredSlideInfographic":
        if self.archetype == "none":
            if self.nodes:
                raise ValueError("a slide without a figure carries no nodes")
            if self.weight != "quiet":
                raise ValueError("a slide without a figure is a quiet page")
            if self.emphasis is not None:
                raise ValueError("a slide without a figure has no emphasis")
            return self
        if not MIN_NODES <= len(self.nodes) <= MAX_NODES:
            raise ValueError(
                f"a figure carries between {MIN_NODES} and {MAX_NODES} nodes"
            )
        if self.emphasis is not None and self.emphasis >= len(self.nodes):
            raise ValueError("emphasis must point at one of the figure's nodes")
        return self

    @property
    def has_figure(self) -> bool:
        return self.archetype != "none"


class AuthoredInfographicPlan(_Spec):
    """The frozen, deck-wide figure and rhythm decisions, one per slide."""

    schema_version: Literal["authored-infographic-plan/v1"] = (
        INFOGRAPHIC_PLAN_SCHEMA_VERSION
    )
    #: One line of deck-wide art direction: the deck's visual voice, which the
    #: hero pages set and the quiet pages keep.
    art_direction: Optional[str] = Field(
        default=None, max_length=MAX_ART_DIRECTION_CHARS * 2
    )
    slides: tuple[AuthoredSlideInfographic, ...] = ()

    @model_validator(mode="after")
    def _one_entry_per_slide(self) -> "AuthoredInfographicPlan":
        for position, slide in enumerate(self.slides):
            if slide.index != position:
                raise ValueError("infographic plan entries must be contiguous")
        return self

    def at(self, index: int) -> Optional[AuthoredSlideInfographic]:
        """The figure at this final-outline index, or None past the deck."""
        if 0 <= index < len(self.slides):
            return self.slides[index]
        return None

    def scene_indices(self) -> tuple[int, ...]:
        return tuple(s.index for s in self.slides if s.archetype == "scene")

    @property
    def figure_count(self) -> int:
        return sum(1 for slide in self.slides if slide.has_figure)


class AuthoredSlideInfographicDraft(_Spec):
    """One entry as the planning model wrote it, before repair."""

    index: int
    archetype: str = "none"
    weight: str = "quiet"
    takeaway: Optional[str] = None
    nodes: tuple[InfographicNode, ...] = ()
    emphasis: Optional[int] = None
    density: str = "medium"
    depiction: Optional[str] = None


class AuthoredInfographicPlanDraft(_Spec):
    """The planning model's answer. Repaired, never trusted, by the coercer."""

    art_direction: Optional[str] = None
    slides: tuple[AuthoredSlideInfographicDraft, ...] = ()


def scene_ceiling(content_slide_count: int) -> int:
    """How many scenes a deck with this many figure-eligible slides may carry."""
    if content_slide_count <= 0:
        return 0
    return max(MIN_SCENE_CEILING, round(MAX_SCENE_SHARE * content_slide_count))


def _repair_nodes(
    draft: AuthoredSlideInfographicDraft, content: str
) -> tuple[InfographicNode, ...]:
    """Keep the nodes whose labels the slide's own content actually contains."""
    haystack = _normalize(content)
    kept: list[InfographicNode] = []
    seen: set[str] = set()
    for node in draft.nodes:
        label = _clip(node.label, MAX_NODE_LABEL_CHARS)
        if label is None:
            continue
        key = _normalize(label)
        if not key or key in seen or key not in haystack:
            continue
        seen.add(key)
        figure, before, after = _repair_values(node.figure, node.before, node.after)
        kept.append(
            InfographicNode(
                label=label,
                desc=_clip(node.desc, MAX_NODE_DESC_CHARS),
                figure=figure,
                before=before,
                after=after,
            )
        )
        if len(kept) == MAX_NODES:
            break
    return tuple(kept)


def _repair_values(
    figure: Optional[str], before: Optional[str], after: Optional[str]
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Clip a node's values, and unpack an ``a → b`` figure into a pair.

    A model that was asked for two values sometimes still writes both into
    ``figure`` with an arrow between them. That is the pair, not a figure, so
    it is split rather than dropped for being over budget.
    """
    before = _clip(before, MAX_NODE_VALUE_CHARS)
    after = _clip(after, MAX_NODE_VALUE_CHARS)
    if figure and before is None and after is None:
        halves = [half for half in _PAIR_SPLIT.split(figure) if half.strip()]
        if len(halves) == 2:
            before = _clip(halves[0], MAX_NODE_VALUE_CHARS)
            after = _clip(halves[1], MAX_NODE_VALUE_CHARS)
            figure = None
    return _clip(figure, MAX_NODE_FIGURE_CHARS), before, after


def _repair_slide(
    draft: Optional[AuthoredSlideInfographicDraft],
    *,
    index: int,
    content: str,
    role: str,
) -> AuthoredSlideInfographic:
    quiet = AuthoredSlideInfographic(index=index)
    if draft is None or (role or "").strip().upper() in NON_FIGURE_ROLES:
        return quiet
    if draft.archetype not in ARCHETYPES or draft.archetype == "none":
        return quiet
    nodes = _repair_nodes(draft, content)
    if len(nodes) < MIN_NODES:
        return quiet
    emphasis = (
        draft.emphasis
        if draft.emphasis is not None and 0 <= draft.emphasis < len(nodes)
        else None
    )
    return AuthoredSlideInfographic(
        index=index,
        archetype=draft.archetype,  # type: ignore[arg-type]
        weight=draft.weight if draft.weight in ("hero", "standard") else "standard",  # type: ignore[arg-type]
        takeaway=_clip(draft.takeaway, MAX_TAKEAWAY_CHARS),
        nodes=nodes,
        emphasis=emphasis,
        density=draft.density if draft.density in ("low", "medium", "high") else "medium",  # type: ignore[arg-type]
        depiction=(draft.depiction if draft.archetype == "scene" and draft.depiction in ("photo", "concept")
                   else None),  # type: ignore[arg-type]
    )


def _demote_scene(slide: AuthoredSlideInfographic) -> AuthoredSlideInfographic:
    return slide.model_copy(
        update={
            "archetype": SCENE_FALLBACK_ARCHETYPE,
            "weight": "standard" if slide.weight == "hero" else slide.weight,
            "depiction": None,
        }
    )


def _enforce_scene_ceiling(
    slides: list[AuthoredSlideInfographic],
) -> list[AuthoredSlideInfographic]:
    """Demote the scenes past the ceiling, keeping the ones the plan weighted."""
    eligible = sum(1 for s in slides if s.has_figure)
    ceiling = scene_ceiling(eligible)
    scenes = [s.index for s in slides if s.archetype == "scene"]
    if len(scenes) <= ceiling:
        return slides
    rank = {"hero": 0, "standard": 1, "quiet": 2}
    ordered = sorted(scenes, key=lambda i: (rank[slides[i].weight], i))
    demoted = set(ordered[ceiling:])
    return [_demote_scene(s) if s.index in demoted else s for s in slides]


def coerce_infographic_plan(
    payload: object,
    *,
    slide_contents: Sequence[str],
    roles: Sequence[str],
    scene_ceiling: bool = True,
) -> AuthoredInfographicPlan:
    """Validate a raw plan payload and repair it into one entry per slide.

    Repair rather than rejection: a hallucinated label or a missing entry must
    not cost the deck its whole plan. Labels the content does not contain are
    dropped, a figure left with too few nodes becomes a quiet page, cover and
    agenda pages are quiet whatever the model said, and scenes beyond the
    ceiling are demoted to the HTML-only fallback figure.
    """
    if not isinstance(payload, dict):
        raise ValueError("infographic plan payload must be an object")
    if len(slide_contents) != len(roles):
        raise ValueError("slide contents and roles must have matching lengths")
    draft = AuthoredInfographicPlanDraft.model_validate(payload)
    by_index: dict[int, AuthoredSlideInfographicDraft] = {}
    for entry in draft.slides:
        # First mention wins so a duplicated index cannot overwrite a decision.
        by_index.setdefault(entry.index, entry)
    slides = [
        _repair_slide(
            by_index.get(index), index=index, content=content, role=roles[index]
        )
        for index, content in enumerate(slide_contents)
    ]
    if scene_ceiling:
        # The ceiling guards the image-provider spend of a scene; a deck whose
        # scenes are drawn as vector SVG passes scene_ceiling=False.
        slides = _enforce_scene_ceiling(slides)
    return AuthoredInfographicPlan(
        art_direction=_clip(draft.art_direction, MAX_ART_DIRECTION_CHARS),
        slides=tuple(slides),
    )


def without_scenes(plan: AuthoredInfographicPlan) -> AuthoredInfographicPlan:
    """The same plan with every scene demoted to the HTML-only fallback.

    Used when the deck cannot produce a picture at all — a style with no image
    prompt, or a request that refused images — so the persisted plan describes
    the figures that were actually drawn.
    """
    if not plan.scene_indices():
        return plan
    return plan.model_copy(
        update={
            "slides": tuple(
                _demote_scene(s) if s.archetype == "scene" else s for s in plan.slides
            )
        }
    )


__all__ = [
    "ARCHETYPES",
    "Archetype",
    "AuthoredInfographicPlan",
    "AuthoredInfographicPlanDraft",
    "AuthoredSlideInfographic",
    "AuthoredSlideInfographicDraft",
    "Density",
    "INFOGRAPHIC_PLAN_SCHEMA_VERSION",
    "InfographicNode",
    "MAX_NODES",
    "MAX_NODE_DESC_CHARS",
    "MAX_NODE_LABEL_CHARS",
    "MAX_SCENE_SHARE",
    "MAX_TAKEAWAY_CHARS",
    "MIN_NODES",
    "NON_FIGURE_ROLES",
    "SCENE_FALLBACK_ARCHETYPE",
    "Weight",
    "coerce_infographic_plan",
    "scene_ceiling",
    "without_scenes",
]
