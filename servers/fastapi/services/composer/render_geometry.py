"""Rung 5 source placements on the fixed canvas.

New source-fit/v2 plans restore original blocks before classification, retain
paragraph/run boundaries and draw the source boxes without estimated growth.
The measured ladder then shrinks only identified overflowing text boxes. Rigid
rotations preserve source orientation without changing computed font sizes;
image crop/reflection is resolved into derivative pixels before capture.

Historical plans keep the previous component geometry correction: grow down to
an overlapping neighbour, lift into available headroom, then estimate a bounded
text shrink. Remaining clipping and overlap stay visible to the same QA gate.
No final HTML is rewritten after measurement.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from typing import NamedTuple

from services.composer.estimate_components import (
    GROUP_CHROME_PX,
    PARAGRAPH_GAP_PX,
    paragraph_height_px,
    process_per_row,
)
from services.composer.plan import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    ComposerComponent,
    ComposerDeckPlan,
    ComposerSlidePlan,
    FitParameters,
    Paragraph,
    SourceRect,
)
from services.composer.render_components import (
    RenderContext,
    render_component,
    render_furniture_elements,
    render_text_stack,
)
from services.composer.render_context import size_role
from services.composer.render_css import px
from services.composer.render_text import TextRenderError, dominant_size_pt, open_tag, render_source_text

LOGGER = logging.getLogger(__name__)

_EMU_PER_INCH = 914400.0
_PX_PER_INCH = 96.0

Box = tuple[float, float, float, float]

#: Stacking order of the title/subtitle geo wrapper: high enough that no other
#: geometry-mode component (default ``z-index:auto``, i.e. 0 in the paint
#: order) can paint over it, whatever its DOM position or overflow.
_PROTECTED_TEXT_Z_INDEX = 2
_PROTECTED_KINDS = frozenset({"title", "subtitle"})
#: Kinds ``_natural_height_px`` can predict a real rendered height for, and so
#: the kinds a box is grown, lifted or shrunk for. Title and subtitle ARE
#: corrected here: ``_PROTECTED_TEXT_Z_INDEX`` wins only the pixel-occlusion
#: probe, not the bounding-box "overlap" check, which still flags a title
#: whose declared source box is shorter than its real multi-line text.
#: ``process`` is here too: ``render_process`` sets each step in a padded,
#: bordered card on a wrapped grid, so a wide, one-line-tall source rectangle
#: carrying five runs renders several times its declared height and paints
#: straight over the title below it (deck-43 slides 14/15/16, each one's
#: ``text:2``/``text:52`` a kicker strip promoted to a process block).
#: ``comparison``/``table``/``image``/``furniture`` stay excluded: their
#: natural height is not modelled here yet.
_GROWABLE_KINDS = frozenset(
    {"paragraph", "quote", "kpi", "caption", "bullets", "numbered", "title", "subtitle", "process"}
)
#: Auto-scale at or below which every paragraph of a component is already
#: pinned to ``render_css.MIN_FONT_PX``, so the predicted height stops
#: changing: the lower bound of the shrink search, and the scale a component
#: keeps when even the whole headroom cannot hold its smallest rendering.
_MIN_AUTO_SCALE = 0.01
#: Bisection budget for that search; 1/2**18 of a scale unit is far finer than
#: a pixel of predicted height.
_SCALE_SEARCH_STEPS = 18
_FIT_EPS = 1e-6
#: Sub-pixel remainders are rounding, not a defect worth a log line.
_RESIDUAL_REPORT_PX = 0.5


class _FittedBox(NamedTuple):
    """One component's corrected geometry: the box it is actually set in, the
    auto text scale that box assumes, and the height that still did not fit."""

    box: Box
    scale: float
    residual_px: float


def page_box(deck: ComposerDeckPlan, params: FitParameters) -> Box:
    """``(x, y, width, height)`` of the scaled source page, centred on the canvas."""

    if deck.source_page_emu:
        cx, cy = deck.source_page_emu
        scale = deck.source_px_scale * params.page_scale
        width = cx / _EMU_PER_INCH * _PX_PER_INCH * scale
        height = cy / _EMU_PER_INCH * _PX_PER_INCH * scale
    else:
        width = deck.canvas.width_px * params.page_scale
        height = deck.canvas.height_px * params.page_scale
    return ((deck.canvas.width_px - width) / 2.0, (deck.canvas.height_px - height) / 2.0, width, height)


def rect_box(rect: SourceRect, page: Box) -> Box:
    """Canvas box of a normalized source rectangle on the scaled page."""

    x0, y0, width, height = page
    return (x0 + rect.x * width, y0 + rect.y * height, rect.width * width, rect.height * height)


def _band(ctx: RenderContext, box: Box) -> str:
    x, y, width, height = box
    if width < 0.5 or height < 0.5:
        return ""
    return (
        open_tag(
            "div",
            qid=ctx.qids.claim("side-band"),
            role="decoration",
            css="cx-side-band",
            style=f"left:{px(x)};top:{px(y)};width:{px(width)};height:{px(height)}",
            extra='aria-hidden="true"',
        )
        + "</div>"
    )


def side_bands(page: Box, ctx: RenderContext) -> str:
    """Paint the canvas outside the scaled page (sides of a 4:3 page, or all four edges)."""

    x0, y0, width, height = page
    x1, y1 = x0 + width, y0 + height
    return "".join(
        (
            _band(ctx, (0.0, 0.0, x0, float(ctx.deck.canvas.height_px))),
            _band(ctx, (x1, 0.0, ctx.deck.canvas.width_px - x1, float(ctx.deck.canvas.height_px))),
            _band(ctx, (x0, 0.0, width, y0)),
            _band(ctx, (x0, y1, width, ctx.deck.canvas.height_px - y1)),
        )
    )


def _placed(plan: ComposerSlidePlan, *, source_placements: bool = False) -> list[ComposerComponent]:
    if source_placements and plan.source_placements:
        return plan.source_placements
    components: list[ComposerComponent] = []
    if plan.title is not None:
        components.append(plan.title)
    if plan.subtitle is not None:
        components.append(plan.subtitle)
    components.extend(plan.components)
    components.extend(plan.images)
    components.extend(plan.furniture)
    return components


def _render_placed(component: ComposerComponent, ctx: RenderContext, box_scale: float, width_px: float) -> str:
    if component.kind in _PROTECTED_KINDS:
        return render_text_stack(component, ctx, box_scale=box_scale)
    if component.kind == "furniture":
        return "".join(render_furniture_elements(component, ctx, box_scale=box_scale))
    return render_component(component, ctx, box_scale=box_scale, width_px=width_px)


def _stack_height_px(
    paragraphs: Sequence[Paragraph],
    component: ComposerComponent,
    ctx: RenderContext,
    *,
    width_px: float,
    box_scale: float,
) -> float:
    """Height one paragraph stack will really render at, at ``width_px`` and the
    current font size -- the same per-paragraph sizing ``render_text_stack``/
    ``render_list``/``render_components._group_body`` use (``ctx.size`` already
    implements source geometry's own ``page_scale``-based formula *and* the
    ``render_css.MIN_FONT_PX`` floor), so this prediction and the browser's
    real layout never disagree about how tall the text is.

    ``estimate_components.paragraphs_height_px`` answers the same question for
    the fit estimate, but it sizes text through ``paragraph_font_px``, which
    knows only ``source_relative``/``source_factor`` -- not ``source_geometry``
    and not the absolute font floor -- so at rung 5 it reports the template
    size for text the renderer sets from the source. It cannot be reused for a
    prediction that has to match the rendered pixels."""

    if not paragraphs:
        return 0.0
    density = 1 if int(ctx.params.density) >= 1 else 0
    total = 0.0
    for paragraph in paragraphs:
        dominant = dominant_size_pt(paragraph, component.source_size_pt)
        size = ctx.size(size_role(component, ctx.params), dominant, box_scale)
        total += paragraph_height_px(paragraph, width_px=width_px, font_px=size, line_height=ctx.density.line_height)
    return total + PARAGRAPH_GAP_PX[density] * (len(paragraphs) - 1)


def _process_height_px(
    component: ComposerComponent, ctx: RenderContext, *, width_px: float, box_scale: float
) -> float:
    """Height ``render_process`` will really render a process component at.

    Mirrors that renderer exactly: its groups (``render_components._groups``
    falls back to one step per paragraph, and the component's own intro
    paragraphs are *not* drawn when it carries groups), its row split
    (``estimate_components.process_per_row``, the same call with the same
    width, so the estimate that decided the wrap and the renderer can never
    disagree), each step's card chrome (``GROUP_CHROME_PX``: padding plus
    border, subtracted from a step's width and added to its height) and the
    ``.cx-process`` grid gap, which is ``DensitySpec.gap`` -- the CSS value,
    not ``estimate_components.GAP_PX``'s looser content-grid figure."""

    groups = component.groups or [[paragraph] for paragraph in component.paragraphs]
    if not groups:
        return 0.0
    per_row = max(1, min(process_per_row(component, ctx.params, ctx.deck, width_px=width_px), len(groups)))
    density = 1 if int(ctx.params.density) >= 1 else 0
    chrome = GROUP_CHROME_PX[density]
    gap = float(ctx.density.gap)
    step_width = max(1.0, (width_px - gap * (per_row - 1)) / per_row - chrome)
    heights: list[float] = []
    for index, group in enumerate(groups):
        header = component.group_headers[index] if index < len(component.group_headers) else None
        stack = ([header] if header is not None else []) + list(group)
        heights.append(
            _stack_height_px(stack, component, ctx, width_px=step_width, box_scale=box_scale) + chrome
        )
    rows = math.ceil(len(groups) / per_row)
    row_heights = [max(heights[row * per_row : (row + 1) * per_row]) for row in range(rows)]
    return sum(row_heights) + gap * (rows - 1)


def _natural_height_px(
    component: ComposerComponent, ctx: RenderContext, *, width_px: float, box_scale: float
) -> float:
    """Real rendered height of any ``_GROWABLE_KINDS`` component at ``box_scale``."""

    if component.kind == "process":
        return _process_height_px(component, ctx, width_px=width_px, box_scale=box_scale)
    return _stack_height_px(
        component.paragraphs, component, ctx, width_px=width_px, box_scale=box_scale
    )


def _x_overlaps(a: Box, b: Box) -> bool:
    ax0, _ay0, aw, _ah = a
    bx0, _by0, bw, _bh = b
    return ax0 < bx0 + bw and bx0 < ax0 + aw


def _growth_ceiling(box: Box, others: list[Box], canvas_height: float = CANVAS_HEIGHT) -> float:
    """The lowest top edge, among boxes below and horizontally overlapping
    ``box``, that growth must stop at -- the "next box" of the module
    docstring -- or the canvas's own bottom edge when nothing is in the way.

    The bound is the canvas, not the scaled page: ``body{overflow:hidden}``
    makes the canvas the one hard boundary, the margin outside the page
    carries only the decorative side bands, and rung 5's own definition
    (design record section 4.3) is that a box which still overflows "grows
    into free canvas"."""

    _x, y, _width, _height = box
    ceiling = float(canvas_height)
    for other in others:
        _ox, oy, _ow, _oh = other
        if oy <= y + _FIT_EPS or not _x_overlaps(box, other):
            continue
        ceiling = min(ceiling, oy)
    return ceiling


def _growth_floor(box: Box, others: list[Box]) -> float:
    """The lowest edge a box may be lifted to: the greatest bottom edge among
    boxes wholly above and horizontally overlapping ``box``, or the canvas's
    own top edge. The mirror of ``_growth_ceiling``, for the free canvas a
    component can claim *above* itself once the gap below it is spent."""

    _x, y, _width, _height = box
    floor = 0.0
    for other in others:
        _ox, oy, _ow, oh = other
        if oy + oh > y + _FIT_EPS or not _x_overlaps(box, other):
            continue
        floor = max(floor, oy + oh)
    return floor


def _fitting_scale(
    component: ComposerComponent,
    ctx: RenderContext,
    *,
    width_px: float,
    ladder_scale: float,
    available: float,
) -> tuple[float, float]:
    """Largest auto text scale whose *real* rendered height fits ``available``,
    and that height.

    The height is measured, never inferred: ``available / natural`` is only
    the height a box would take if the font shrank linearly, and it does not
    -- ``render_css.text_px`` clamps every size at ``MIN_FONT_PX``, so past
    the floor the text stops shrinking while the caller keeps pinning the box
    to the smaller figure. That mismatch is the defect this search replaces.
    When even ``_MIN_AUTO_SCALE`` (everything already at the floor) does not
    fit, that smallest rendering and its own height are returned and the
    caller records the remainder rather than pretending it went away.
    """

    def height_at(scale: float) -> float:
        return _natural_height_px(component, ctx, width_px=width_px, box_scale=ladder_scale * scale)

    smallest = height_at(_MIN_AUTO_SCALE)
    if smallest > available:
        return _MIN_AUTO_SCALE, smallest
    low, high = _MIN_AUTO_SCALE, 1.0
    for _step in range(_SCALE_SEARCH_STEPS):
        mid = (low + high) / 2.0
        if height_at(mid) <= available:
            low = mid
        else:
            high = mid
    return low, height_at(low)


def _fit_growable_box(
    component: ComposerComponent,
    ctx: RenderContext,
    box: Box,
    others: list[Box],
    *,
    ladder_scale: float,
) -> _FittedBox:
    """Reconcile ``box`` with the height its text will really render at.

    In order: leave the box alone when it already holds its natural height;
    grow it straight down into the gap before the next overlapping box; lift
    it into the free canvas above when that gap is not enough (its bottom
    edge stays on the ceiling, so the neighbour below is never encroached on);
    and only then shrink the text, as far as the font floor actually allows.
    Whatever is left over after all three is reported as ``residual_px`` and
    the box is still given the text's *real* height, so the declared geometry
    never claims text fits where it does not -- nothing is clipped, hidden or
    reordered to make the box's own number come out right.

    Every step is measured at ``ladder_scale`` -- the per-box shrink rung 5
    already applied (``FitParameters.box_text_scale``) -- times the candidate
    auto scale, which is exactly the ``box_scale`` the renderer will use, so
    the correction is right at every step of the ladder rather than only at
    the terminal rung's entry.

    The shrink is not floored at ``MIN_SOURCE_RELATIVE_SCALE`` (the "never
    below 90% of source size" policy ``FitParameters.source_factor``/
    ``page_scale`` apply to a whole *page*): this is a narrow, per-box last
    resort for one overlap, not a page-wide readability policy, so it shrinks
    as far as the headroom needs -- ``render_css.text_px``'s ``MIN_FONT_PX``
    absolute floor still applies underneath it, as it does everywhere else.
    """

    x, y, width, height = box
    if component.kind not in _GROWABLE_KINDS:
        return _FittedBox(box, 1.0, 0.0)
    natural = _natural_height_px(component, ctx, width_px=width, box_scale=ladder_scale)
    if natural <= height + _FIT_EPS:
        return _FittedBox(box, 1.0, 0.0)
    headroom = max(height, _growth_ceiling(box, others, canvas_height=ctx.deck.canvas.height_px) - y)
    if natural <= headroom:
        return _FittedBox((x, y, width, natural), 1.0, 0.0)
    lift_limit = max(0.0, y - _growth_floor(box, others))
    if natural <= headroom + lift_limit:
        return _FittedBox((x, y - (natural - headroom), width, natural), 1.0, 0.0)
    scale, real = _fitting_scale(
        component, ctx, width_px=width, ladder_scale=ladder_scale, available=headroom + lift_limit
    )
    #: Never *below* the source rectangle: the correction only ever grows or
    #: lifts a box, so a component whose shrunk text now needs less room than
    #: the source gave it still keeps the rectangle the source drew for it.
    fitted_height = max(height, real)
    lift = min(lift_limit, max(0.0, fitted_height - headroom))
    residual = max(0.0, real - (headroom + lift_limit))
    return _FittedBox((x, y - lift, width, fitted_height), scale, residual)


def _fit_boxes(
    placed: list[ComposerComponent], boxes: list[Box], ctx: RenderContext
) -> list[_FittedBox]:
    """Correct every box once, topmost source rectangle first.

    Order matters only because a box may now be lifted: resolving from the top
    down means the space a box claims above itself is bounded by the
    *corrected* extent of the box above it, never by a declared source
    rectangle that box has already outgrown, so two neighbours can never claim
    the same gap. ``_placed``'s own (paint) order is kept for rendering."""

    resolved = [_FittedBox(box, 1.0, 0.0) for box in boxes]
    for index in sorted(range(len(boxes)), key=lambda position: (boxes[position][1], position)):
        component = placed[index]
        others = [fitted.box for position, fitted in enumerate(resolved) if position != index]
        ladder_scale = float(ctx.params.box_text_scale.get(component.component_id, 1.0))
        resolved[index] = _fit_growable_box(
            component, ctx, boxes[index], others, ladder_scale=ladder_scale
        )
    return resolved


def layout_source_geometry(plan: ComposerSlidePlan, ctx: RenderContext) -> str:
    page = page_box(ctx.deck, ctx.params)
    original = bool(plan.source_placements) and ctx.deck.fit_policy_revision == "composer-source-fit/v2"
    placed = _placed(plan, source_placements=original)
    boxes: list[Box] = []
    for component in placed:
        if component.source_rect is None:
            raise TextRenderError(
                f"component {component.component_id!r} has no source rectangle for source geometry"
            )
        boxes.append(rect_box(component.source_rect, page))
    fitted = [_FittedBox(box, 1.0, 0.0) for box in boxes] if original else _fit_boxes(placed, boxes, ctx)
    out = [side_bands(page, ctx)]
    for index, component in enumerate(placed):
        x, y, width, height = fitted[index].box
        ladder_scale = float(ctx.params.box_text_scale.get(component.component_id, 1.0))
        box_scale = ladder_scale * fitted[index].scale
        inner = (render_source_text(component, ctx, box_scale=box_scale)
                 if original and component.paragraphs else _render_placed(component, ctx, box_scale, width))
        if not inner:
            continue
        style = f"left:{px(x)};top:{px(y)};width:{px(width)};height:{px(height)}"
        if original:
            style += f";z-index:{index}"
            # Shape reflection does not reflect its text. Image reflections are
            # resolved into derivative pixels before capture; only rigid rotation
            # reaches the browser, preserving the effective font-size magnitude.
            rotation = float(component.source_transform.get("rotationDegrees") or 0)
            if component.paragraphs:
                rotation = 0.0 if component.source_text_body.get("upright") else rotation + float(component.source_text_body.get("rotationDegrees") or 0)
            if rotation % 360:
                style += f";transform:rotate({rotation:g}deg);transform-origin:center center"
        elif component.kind in _PROTECTED_KINDS:
            style += f";z-index:{_PROTECTED_TEXT_Z_INDEX}"
        #: A component whose smallest rendering still does not fit between its
        #: neighbours is named here rather than overflowing in silence. It
        #: cannot be recorded on the element itself: ``utils.renderer_isolation``
        #: admits a fixed attribute allowlist, and the residual is a render
        #: observation, not slide content. The geometry is still honest -- the
        #: box above carries the text's *real* height -- so the DOM inspector
        #: sees, and the slide's own receipt records, the overlap that is left.
        if fitted[index].residual_px > _RESIDUAL_REPORT_PX:
            LOGGER.warning(
                "composer source geometry: %s on slide %s has %.1fpx of text "
                "with nowhere to go (box %.1fpx at y=%.1f, text scale %.3f)",
                component.component_id,
                plan.source_unit_id,
                fitted[index].residual_px,
                height,
                y,
                box_scale,
            )
        out.append(
            open_tag(
                "div",
                qid=ctx.qids.claim(f"{component.component_id}-geo"),
                role="decoration",
                css="cx-geo",
                style=style,
            )
        )
        out.append(inner)
        out.append("</div>")
    return "".join(out)


__all__ = ["Box", "layout_source_geometry", "page_box", "rect_box", "side_bands"]
