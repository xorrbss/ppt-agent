"""Font-metric fit estimate for the Composer ladder.

``estimate_fit`` runs before any Chromium call so most slides render once. It
must be conservative: a ``True`` the browser then rejects costs one extra
render, a ``False`` that would have fit costs one rung, so the estimate leans
toward "does not fit" and adds ``SAFETY_MARGIN`` on top.

Per-component height primitives (font sizing, wrapped line counts, the
process/comparison row math) live in ``estimate_components``; this module
composes them into the whole-slide estimate.

What is estimated
------------------
Only the content area of ``CHROME_CONTENT_AREA[chrome_level]`` is modelled.
Title, subtitle and furniture live in the chrome bands that this area already
excludes, so they are not summed. Images are mostly not summed either:
``render_chrome._content`` lays a slide's images out in their own region --
beside the text columns when both are present, or their own grid when a
slide carries only images -- sized to exactly ``content_height`` either way,
never stacked on top of the text flow. An image can shrink to the point of
being hard to read, but it structurally cannot be the reason a slide
overflows, so counting its natural height against the budget only ever
produces a false "does not fit": with four images on a slide that also
overflows on text, that alone is 4 x up to 0.35 of the content area, more
than the whole budget before a single line of text is measured.

The one exception is a slide that carries both images and a wide component
(comparison/process/table): there, ``render_chrome._flex_image_row`` gives
the image row a real ``min-height`` floor inside the same flex column as the
text, so unlike every other layout an image row *can* be the reason that
combination does not fit -- when the text already leaves less than the floor,
the floor still claims it and the text overflows. ``estimated_content_height``
mirrors exactly that: it adds the floor only when text-height-plus-floor
would exceed the area, never a fixed height for the row otherwise, so a page
whose text already fits comfortably alongside the images is never estimated
as "over" on the images' account.

``render_chrome`` never places a ``comparison``/``process``/``table`` component
(or any other kind outside ``FLOW_KINDS``) in a balanced column: it renders as
its own full-width row, in source order, and only splits the plan's flow
components -- paragraph/bullets/numbered/quote/kpi/caption -- into runs that
each get their own ``params.columns``-wide balanced grid between the wide
rows (``render_chrome._segments`` / ``_flow_region`` / ``_wide_row``).
Modelling every component as one pool bin-packed at a single ``column_width``
-- the model this estimate used before -- both undercounts a wide block's real
width (the renderer never shares a column with it; it gets the *full* content
width) and overcounts a narrow flow run's width (a two-paragraph run between
two wide rows still only competes with itself, not with the block's weight).
That mismatch is what made every rung 0-3 template step estimate as "over"
for a comparison/process slide: a 9-row, 3-column comparison squeezed to a
single column's width wraps far taller than the same comparison at the full
content width the renderer actually gives it.

The fix mirrors the split exactly: each wide component's height is measured
at the *full* content width and added to the running total in source order.
Each flow run's height is measured the same way the old model measured
everything -- every component in the run sized at that run's own
``column_width`` (the content width divided by ``params.columns``), combined
with the classic two-sided bin-packing bound: the taller of the single
heaviest item in the run (a column holding it alone can never be shorter than
that) and the run's mean column weight (``sum / columns``, columns of exactly
equal weight are the best case ``balanced_partition`` could ever achieve).
Neither term is exact, so this is not the true minimax partition, but both
terms are simple lower bounds, and their max is a good and cheap approximation
of ``balanced_partition``'s outcome without re-implementing its DP here. The
whole-slide estimate is the sum of every segment's height plus one inter-segment
gap between consecutive segments, in source order -- exactly what
``render_chrome``'s vertical sequence of rows and column grids stacks.
"""

from __future__ import annotations

from services.composer.estimate_components import (
    DEFAULT_IMAGE_ASPECT,
    GAP_PX,
    GROUP_CHROME_PX,
    IMAGE_MAX_AREA_SHARE,
    LINE_HEIGHT,
    MARKER_INDENT_EM,
    MAX_GROUPS_PER_ROW,
    PARAGRAPH_GAP_PX,
    TABLE_CELL_HORIZONTAL_PADDING_PX,
    TABLE_CELL_PADDING_PX,
    TABLE_LINE_HEIGHT,
    _BAND_ROLES,
    _row_grouped_height_px,
    comparison_height_px,
    group_component_height_px,
    image_height_px,
    paragraph_font_px,
    paragraph_height_px,
    paragraph_source_size_pt,
    paragraphs_height_px,
    process_height_px,
    process_per_row,
    table_height_px,
    template_font_px,
    type_scale_key,
    wide_sibling_image_floor_px,
)
from services.composer.plan import (
    CHROME_CONTENT_AREA,
    ComposerComponent,
    ComposerDeckPlan,
    ComposerSlidePlan,
    ComposerTokens,
    FitParameters,
)
from services.composer.render_components import FLOW_KINDS

#: The estimate must be conservative by this factor.
SAFETY_MARGIN = 1.05


def content_area(params: FitParameters) -> tuple[float, float]:
    """Content area (width, height) in px for the step's chrome level."""

    width, height = CHROME_CONTENT_AREA[int(params.chrome_level)]
    return float(width), float(height)


def _content_components(plan: ComposerSlidePlan) -> list[ComposerComponent]:
    """``plan.components`` minus furniture, images and band (title/subtitle) roles."""

    return [
        component
        for component in plan.components
        if component.kind not in ("furniture", "image") and component.semantic_role not in _BAND_ROLES
    ]


def _segments(components: list[ComposerComponent]) -> list[tuple[str, list[ComposerComponent]]]:
    """``components`` split into flow runs and standalone wide rows, in source
    order -- mirrors ``render_chrome._segments`` exactly, at component (not
    paragraph) granularity, matching this estimate's existing per-component
    approximation."""

    segments: list[tuple[str, list[ComposerComponent]]] = []
    current: list[ComposerComponent] = []
    for component in components:
        if component.kind in FLOW_KINDS:
            current.append(component)
            continue
        if current:
            segments.append(("flow", current))
            current = []
        segments.append(("wide", [component]))
    if current:
        segments.append(("flow", current))
    return segments


def _wide_height_px(
    component: ComposerComponent, params: FitParameters, deck: ComposerDeckPlan, *, width_px: float
) -> float:
    """A wide row's height at the full content width it actually renders at."""

    if component.kind == "table":
        return table_height_px(component, params, deck, width_px=width_px)
    if component.kind == "comparison":
        return comparison_height_px(component, params, deck, width_px=width_px)
    if component.kind == "process":
        return process_height_px(component, params, deck, width_px=width_px)
    return group_component_height_px(component, params, deck, width_px=width_px)


def _flow_height_px(
    components: list[ComposerComponent],
    params: FitParameters,
    deck: ComposerDeckPlan,
    *,
    column_width: float,
    columns: int,
    gap: float,
) -> float:
    """A flow run's height under the two-sided bin-packing bound, mirroring
    ``render_chrome._flow_region``'s balanced-column grid for that run alone."""

    items = [
        paragraphs_height_px(component.paragraphs, component, params, deck, width_px=column_width)
        for component in components
    ]
    if not items:
        return 0.0
    mean_column = (sum(items) + gap * (len(items) - 1)) / max(1, columns)
    return max(max(items), mean_column)


def estimated_content_height(plan: ComposerSlidePlan, params: FitParameters, deck: ComposerDeckPlan) -> float:
    """Height the plan needs inside the content area at ``params``, in px, before the margin.

    See the module docstring for why images are mostly excluded (and the one
    exception, a wide component sharing the area with images) and why wide
    rows (full content width) and flow runs (bin-packed over
    ``params.columns`` at their own ``column_width``) are estimated as
    separate, sequential segments rather than one pool.
    """

    area_width, area_height = content_area(params)
    density = int(params.density)
    gap = GAP_PX[density]
    columns = int(params.columns)
    column_width = (area_width - gap * (columns - 1)) / columns
    segments = _segments(_content_components(plan))
    if not segments:
        return 0.0
    heights = [
        _wide_height_px(components[0], params, deck, width_px=area_width)
        if kind == "wide"
        else _flow_height_px(components, params, deck, column_width=column_width, columns=columns, gap=gap)
        for kind, components in segments
    ]
    text_height = sum(heights) + gap * (len(heights) - 1)
    has_wide = any(kind == "wide" for kind, _components in segments)
    images = [component for component in plan.images if component.kind == "image"]
    if not images or not has_wide:
        return text_height
    with_floor = text_height + gap + wide_sibling_image_floor_px(params)
    return with_floor if with_floor > area_height else text_height


def estimate_fit(
    plan: ComposerSlidePlan,
    tokens: ComposerTokens,
    params: FitParameters,
    deck: ComposerDeckPlan,
) -> bool:
    """``Estimator`` implementation: does the plan fit the content area at ``params``?"""

    del tokens  # the look does not change the metrics this estimate models
    if params.source_geometry:
        return True
    _, area_height = content_area(params)
    return estimated_content_height(plan, params, deck) * SAFETY_MARGIN <= area_height


__all__ = [
    "DEFAULT_IMAGE_ASPECT",
    "GAP_PX",
    "GROUP_CHROME_PX",
    "IMAGE_MAX_AREA_SHARE",
    "LINE_HEIGHT",
    "MARKER_INDENT_EM",
    "MAX_GROUPS_PER_ROW",
    "PARAGRAPH_GAP_PX",
    "SAFETY_MARGIN",
    "TABLE_CELL_HORIZONTAL_PADDING_PX",
    "TABLE_CELL_PADDING_PX",
    "TABLE_LINE_HEIGHT",
    "_row_grouped_height_px",
    "comparison_height_px",
    "content_area",
    "estimate_fit",
    "estimated_content_height",
    "group_component_height_px",
    "image_height_px",
    "paragraph_font_px",
    "paragraph_height_px",
    "paragraph_source_size_pt",
    "paragraphs_height_px",
    "process_height_px",
    "process_per_row",
    "table_height_px",
    "template_font_px",
    "type_scale_key",
    "wide_sibling_image_floor_px",
]
