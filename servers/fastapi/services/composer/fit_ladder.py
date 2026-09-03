"""Pure step generation for the Composer fit ladder.

Nothing here renders or measures. ``ladder_steps`` yields the rung sequence
from the design record section 4.3 as ``FitParameters`` so tests can inspect
it; ``fit.fit_slide`` walks it. The terminal rung's per-box shrink depends on
what the browser blamed, so its refinement steps are built by ``fit`` from the
helpers at the bottom of this module.

Rung summary (verbatim restyle):

0  template archetype, chrome 0, density 0, type_scale 1.0, archetype columns
1  dense variant, one more column (max 3)
2  type_scale 0.95 .. 0.75 (body 20 px -> 15 px, the design floor)
3  chrome 1, 2, 3 at the floor
4  source-relative type: one uniform ``source_factor`` from the design floor
   down to 1.0 in four equal steps; skipped when the plan carries no sizes
5  source geometry (terminal): page_scale 1.0, then one per-box text shrink
   for the blamed components, then page_scale 0.90 -- three renders total,
   however many rungs 0-4 already spent (see ``BOX_TEXT_SCALE_STEPS`` and
   ``TERMINAL_PAGE_SCALES`` below)
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Iterable, Iterator, Sequence

from services.composer.estimate import paragraph_source_size_pt
from services.composer.plan import (
    BODY_FLOOR_PX,
    PX_PER_PT,
    ComposerComponent,
    ComposerDeckPlan,
    ComposerSlidePlan,
    FitParameters,
)
from services.source_content.source_stamp_dom import deterministic_source_qid

TYPE_SCALE_STEPS: tuple[float, ...] = (0.90, 0.82, 0.75)
TYPE_SCALE_FLOOR = 0.75
CHROME_STEPS: tuple[int, ...] = (1, 2, 3)
MAX_CHROME_LEVEL = 3
SOURCE_FACTOR_STEP_COUNT = 4
TERMINAL_PAGE_SCALE = 0.90
#: Rung 5's own render budget: the page_scale 1.0 entry, one box-text shrink
#: for the components the browser blamed, then the floor page_scale -- three
#: renders, not the finer-grained 0.95/0.90 box steps x 0.98..0.90 page steps
#: ladder this used to walk (up to 8 renders for one slide). The terminal
#: rung's whole point is a cheap last resort once rungs 0-4 already spent
#: their own budget; a slide that still doesn't fit at page_scale 0.90 with
#: its blamed boxes shrunk is accepted anyway with the residual issues
#: recorded (see ``fit._terminal_rung``), so finer steps only bought more
#: renders, not more slides fitting.
BOX_TEXT_SCALE_STEPS: tuple[float, ...] = (0.90,)
TERMINAL_PAGE_SCALES: tuple[float, ...] = (TERMINAL_PAGE_SCALE,)
MAX_COLUMNS = 3

#: ``text``/``list`` archetypes: (max verbatim characters, columns); more -> 3.
TEXT_COLUMN_THRESHOLDS: tuple[tuple[int, int], ...] = ((600, 1), (1400, 2))
_COLUMN_RULE_ARCHETYPES = frozenset({"text", "list"})
_BAND_ROLES = frozenset({"title", "subtitle"})
_NON_TEXT_KINDS = frozenset({"image", "furniture"})
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*")

#: Decoration QIDs that name only the title band / cover stack, never body content.
_TITLE_MARKERS = frozenset({"cx-title-stack", "cx-title-band", "cx-cover-stack"})


def component_paragraphs(component: ComposerComponent) -> Iterator:
    """Every paragraph a component carries, in source order."""

    yield from component.paragraphs
    for header in component.group_headers:
        if header is not None:
            yield header
    for group in component.groups:
        yield from group
    for row in component.table_rows:
        yield from row


def text_components(plan: ComposerSlidePlan) -> list[ComposerComponent]:
    """Components that own text, title and subtitle included, furniture excluded."""

    found = [c for c in (plan.title, plan.subtitle) if c is not None]
    found.extend(c for c in plan.components if c.kind not in _NON_TEXT_KINDS)
    return found


def plan_text_chars(plan: ComposerSlidePlan) -> int:
    if plan.text_chars:
        return plan.text_chars
    return sum(len(p.text) for c in plan.components for p in component_paragraphs(c))


def archetype_columns(plan: ComposerSlidePlan) -> int:
    """Rung-0 column count. Text and list flows split by character count; other
    archetypes lay out their own groups or grid and start at one column."""

    if plan.archetype not in _COLUMN_RULE_ARCHETYPES:
        return 1
    chars = plan_text_chars(plan)
    for limit, columns in TEXT_COLUMN_THRESHOLDS:
        if chars <= limit:
            return columns
    return MAX_COLUMNS


def body_source_size_pt(plan: ComposerSlidePlan) -> float | None:
    """Median source size of the body paragraphs; falls back to component sizes."""

    sizes: list[float] = []
    for component in plan.components:
        if component.kind in _NON_TEXT_KINDS or component.semantic_role in _BAND_ROLES:
            continue
        for paragraph in component_paragraphs(component):
            size = paragraph_source_size_pt(paragraph) or component.source_size_pt
            if size:
                sizes.append(size)
    if not sizes:
        sizes = [c.source_size_pt for c in text_components(plan) if c.source_size_pt]
    return statistics.median(sizes) if sizes else None


def source_factor_steps(body_source_px: float) -> tuple[float, ...]:
    """From ``max(1.0, floor / body)`` down to 1.0 in four equal steps."""

    if body_source_px <= 0:
        return (1.0,)
    start = max(1.0, BODY_FLOOR_PX / body_source_px)
    if start <= 1.0 + 1e-9:
        return (1.0,)
    delta = (start - 1.0) / (SOURCE_FACTOR_STEP_COUNT - 1)
    return tuple(round(start - index * delta, 4) for index in range(SOURCE_FACTOR_STEP_COUNT))


def terminal_entry(columns: int) -> FitParameters:
    return FitParameters(
        rung=5,
        chrome_level=MAX_CHROME_LEVEL,
        density=1,
        type_scale=TYPE_SCALE_FLOOR,
        source_geometry=True,
        page_scale=1.0,
        columns=columns,
    )


def ladder_steps(plan: ComposerSlidePlan, deck: ComposerDeckPlan) -> Iterator[FitParameters]:
    """Rungs 0-4 step by step, then the rung-5 entry (page_scale 1.0)."""

    columns = archetype_columns(plan)
    yield FitParameters(rung=0, chrome_level=0, density=0, type_scale=1.0, columns=columns)
    dense_columns = min(MAX_COLUMNS, columns + 1)
    yield FitParameters(rung=1, chrome_level=0, density=1, type_scale=1.0, columns=dense_columns)
    for type_scale in TYPE_SCALE_STEPS:
        yield FitParameters(rung=2, chrome_level=0, density=1, type_scale=type_scale, columns=dense_columns)
    for chrome in CHROME_STEPS:
        yield FitParameters(
            rung=3, chrome_level=chrome, density=1, type_scale=TYPE_SCALE_FLOOR, columns=dense_columns
        )
    body_pt = body_source_size_pt(plan)
    if body_pt is not None:
        body_px = body_pt * PX_PER_PT * deck.source_px_scale
        for factor in source_factor_steps(body_px):
            yield FitParameters(
                rung=4,
                chrome_level=MAX_CHROME_LEVEL,
                density=1,
                type_scale=TYPE_SCALE_FLOOR,
                source_relative=True,
                source_factor=factor,
                columns=dense_columns,
            )
    yield terminal_entry(dense_columns)


def component_identifiers(component: ComposerComponent) -> frozenset[str]:
    """Tokens by which a blocking issue can name this component: its id, the
    binding ids of its paragraphs and the QIDs the renderer derives from them."""

    identifiers = {component.component_id}
    if component.occurrence_id:
        identifiers.add(component.occurrence_id)
    if component.asset_id:
        identifiers.add(component.asset_id)
    for paragraph in component_paragraphs(component):
        identifiers.add(paragraph.source.binding_id)
        identifiers.add(deterministic_source_qid(paragraph.source.binding_id))
    return frozenset(identifiers)


def issue_tokens(issues: Iterable[str]) -> frozenset[str]:
    tokens: set[str] = set()
    for issue in issues:
        for token in _TOKEN.findall(issue):
            tokens.add(token.rstrip(".:-"))
    return frozenset(tokens)


def blamed_components(plan: ComposerSlidePlan, issues: Sequence[str]) -> tuple[str, ...]:
    """Component ids named by the issues; every text component when none is named."""

    tokens = issue_tokens(issues)
    everything = [c for c in (plan.title, plan.subtitle) if c is not None]
    everything.extend(plan.components)
    everything.extend(plan.images)
    everything.extend(plan.furniture)
    blamed = tuple(c.component_id for c in everything if component_identifiers(c) & tokens)
    if blamed:
        return blamed
    return tuple(c.component_id for c in text_components(plan))


def title_only_blocking(plan: ComposerSlidePlan, issues: Sequence[str]) -> bool:
    """True when every blocking issue names only the title band / cover stack or
    the title's own source block, never body content.

    Body shrinking (type_scale at rung 2, source_factor at rung 4) cannot fix a
    title problem, so ``fit_slide`` uses this to skip the remaining steps of the
    current rung and move on once it sees a purely title-shaped rejection. Kept
    as a safeguard now that the renderer sizes titles on their own axis
    (``render_title``); a genuinely title-only overflow should be rare.
    """

    if not issues:
        return False
    title_tokens = set(_TITLE_MARKERS)
    if plan.title is not None:
        title_tokens |= component_identifiers(plan.title)
    for issue in issues:
        if not (issue_tokens((issue,)) & title_tokens):
            return False
    return True


def step_changes_title(params: FitParameters) -> bool:
    """Whether the knob this step turns can change the title's own fitted size.

    Mirrors ``render_title.fitted_title_px``: ``type_scale`` only reaches the
    title at rung >= 3, and source-relative/source-geometry rungs size every
    run from its own source point, ignoring this knob entirely. A step that
    cannot move the title is not worth retrying after a title-only blocking
    issue.
    """

    if params.source_relative or params.source_geometry:
        return False
    return params.rung >= 3


def step_label(params: FitParameters) -> str:
    """Short trail label: the rung and the knob this step turned."""

    rung = int(params.rung)
    if rung == 0:
        return f"rung 0 columns {params.columns}"
    if rung == 1:
        return f"rung 1 density 1 columns {params.columns}"
    if rung == 2:
        return f"rung 2 type_scale {params.type_scale:.2f}"
    if rung == 3:
        return f"rung 3 chrome {params.chrome_level}"
    if rung == 4:
        return f"rung 4 source_factor {params.source_factor:.4f}"
    if params.box_text_scale and params.page_scale >= 1.0:
        return f"rung 5 box_text_scale {min(params.box_text_scale.values()):.2f}"
    return f"rung 5 page_scale {params.page_scale:.2f}"


__all__ = [
    "BOX_TEXT_SCALE_STEPS",
    "CHROME_STEPS",
    "TERMINAL_PAGE_SCALE",
    "TERMINAL_PAGE_SCALES",
    "TEXT_COLUMN_THRESHOLDS",
    "TYPE_SCALE_STEPS",
    "archetype_columns",
    "blamed_components",
    "body_source_size_pt",
    "component_identifiers",
    "ladder_steps",
    "source_factor_steps",
    "step_changes_title",
    "step_label",
    "terminal_entry",
    "text_components",
    "title_only_blocking",
]
