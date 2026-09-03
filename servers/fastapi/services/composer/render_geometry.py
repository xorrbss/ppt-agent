"""Rung 5: every component set in its own source rectangle mapped onto the canvas.

The source page is scaled uniformly by ``deck.source_px_scale x
params.page_scale`` and centred; the canvas left over around it is painted
with the template's soft accent. Each component is absolutely positioned at
its ``source_rect`` and its text shrinks by ``params.box_text_scale``. No CSS
transform is used anywhere: every length is computed numerically.

Source rectangles come from the captured page and can sit close enough that
a neighbour's real (browser-measured) text height spills past its own box --
``.cx-text`` is deliberately ``overflow:visible`` so wrapped text is never
clipped. A component painted after the title in DOM order (``_placed``
always places title first) would then visually paint over the title's own
glyphs even though its declared box does not literally overlap the title's.
``inspect_authored_dom``'s title-edge occlusion check treats this as a
blocking defect regardless of how small the overlap is (unlike the general
overlap checks, it does not tolerate a percentage of harmless overlap,
because a title with even one occluded glyph reads as broken). The fix is
paint order, not geometry: the title and subtitle wrappers carry an explicit
``z-index`` so they are always the topmost element at any point they cover,
which is exactly what the browser's ``elementsFromPoint``-based occlusion
probe checks (``utils.slide_capture``) -- only elements painted *above* the
target in that stack count as occluders.

The z-index above only wins the browser's pixel-occlusion probe (paint
order); it does nothing about the plain bounding-box "overlap" check, which
still flags a box whose own source rectangle is simply *too short* for the
text it carries at the source's own font size -- including the title's,
captured as a thin strip above a much taller neighbour on a dense page (the
measured defect on every one of ``composer-offline-eval`` final2-43 slides
12-15/32: each slide's own ``text:3`` is that slide's *title* component id).
Since nothing here clips an overflowing child (``.cx-geo`` sets no
``overflow``), the box's declared height is not what stops that overflow
from painting over the next component; only the box's *real* rendered extent
staying within the gap before that neighbour does. ``_fit_growable_box``
predicts that real extent with the same font-size and line-wrap model the
renderer will actually use, and: leaves the box alone when it already fits;
grows it straight down -- never past a horizontally overlapping neighbour's
own top -- when the natural text fits in that headroom; and only shrinks the
text (``box_text_scale``-style) when even the full headroom is not enough.
This runs on every geometry-mode render, ahead of and independent of the
ladder's own per-box shrink step (``fit._next_terminal_step``), which only
ever applies a flat 10% cut to whatever the browser blamed on the *previous*
render.
"""

from __future__ import annotations

from services.composer.estimate_components import PARAGRAPH_GAP_PX, paragraph_height_px
from services.composer.plan import (
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    ComposerComponent,
    ComposerDeckPlan,
    ComposerSlidePlan,
    FitParameters,
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
from services.composer.render_text import TextRenderError, dominant_size_pt, open_tag

_EMU_PER_INCH = 914400.0
_PX_PER_INCH = 96.0

Box = tuple[float, float, float, float]

#: Stacking order of the title/subtitle geo wrapper: high enough that no other
#: geometry-mode component (default ``z-index:auto``, i.e. 0 in the paint
#: order) can paint over it, whatever its DOM position or overflow.
_PROTECTED_TEXT_Z_INDEX = 2
_PROTECTED_KINDS = frozenset({"title", "subtitle"})
#: Simple text-stack/list kinds a box can be grown or shrunk for. Excludes
#: process/comparison/table/image/furniture, whose own natural height is not
#: a single paragraph stack. Title and subtitle ARE growable here: the
#: ``_PROTECTED_TEXT_Z_INDEX`` above only wins the browser's pixel-occlusion
#: probe (paint order), not the plain bounding-box "overlap" check, which
#: still flags a title whose declared source box is shorter than its real
#: multi-line rendered text -- the measured defect on every one of
#: ``composer-offline-eval`` final2-43 slides 12-15/32 (each one's own
#: ``text:3`` is that slide's title component id, not a body kicker).
_GROWABLE_KINDS = frozenset(
    {"paragraph", "quote", "kpi", "caption", "bullets", "numbered", "title", "subtitle"}
)


def page_box(deck: ComposerDeckPlan, params: FitParameters) -> Box:
    """``(x, y, width, height)`` of the scaled source page, centred on the canvas."""

    if deck.source_page_emu:
        cx, cy = deck.source_page_emu
        scale = deck.source_px_scale * params.page_scale
        width = cx / _EMU_PER_INCH * _PX_PER_INCH * scale
        height = cy / _EMU_PER_INCH * _PX_PER_INCH * scale
    else:
        width = CANVAS_WIDTH * params.page_scale
        height = CANVAS_HEIGHT * params.page_scale
    return ((CANVAS_WIDTH - width) / 2.0, (CANVAS_HEIGHT - height) / 2.0, width, height)


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
            _band(ctx, (0.0, 0.0, x0, float(CANVAS_HEIGHT))),
            _band(ctx, (x1, 0.0, CANVAS_WIDTH - x1, float(CANVAS_HEIGHT))),
            _band(ctx, (x0, 0.0, width, y0)),
            _band(ctx, (x0, y1, width, CANVAS_HEIGHT - y1)),
        )
    )


def _placed(plan: ComposerSlidePlan) -> list[ComposerComponent]:
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


def _natural_text_height_px(
    component: ComposerComponent, ctx: RenderContext, *, width_px: float, box_scale: float
) -> float:
    """Height ``component``'s paragraphs will really render at, at ``width_px``
    and the current font size -- the same per-paragraph sizing ``render_text_
    stack``/``render_list`` use (``ctx.size`` already implements source
    geometry's own ``page_scale``-based formula), so this prediction and the
    browser's real layout never disagree about how tall the text is."""

    paragraphs = component.paragraphs
    if not paragraphs:
        return 0.0
    density = 1 if int(ctx.params.density) >= 1 else 0
    total = 0.0
    for paragraph in paragraphs:
        dominant = dominant_size_pt(paragraph, component.source_size_pt)
        size = ctx.size(size_role(component, ctx.params), dominant, box_scale)
        total += paragraph_height_px(paragraph, width_px=width_px, font_px=size, line_height=ctx.density.line_height)
    return total + PARAGRAPH_GAP_PX[density] * (len(paragraphs) - 1)


def _x_overlaps(a: Box, b: Box) -> bool:
    ax0, _ay0, aw, _ah = a
    bx0, _by0, bw, _bh = b
    return ax0 < bx0 + bw and bx0 < ax0 + aw


def _growth_ceiling(box: Box, others: list[Box], *, page_bottom: float) -> float:
    """The lowest top edge, among boxes below and horizontally overlapping
    ``box``, that growth must stop at -- the "next box" of the module
    docstring -- or the page's own bottom edge when nothing is in the way."""

    _x, y, _width, _height = box
    ceiling = page_bottom
    for other in others:
        _ox, oy, _ow, _oh = other
        if oy <= y + 1e-6 or not _x_overlaps(box, other):
            continue
        ceiling = min(ceiling, oy)
    return ceiling


def _fit_growable_box(
    component: ComposerComponent, ctx: RenderContext, box: Box, others: list[Box], *, page_bottom: float
) -> tuple[Box, float]:
    """Grow ``box`` downward to hold its natural text height, capped at the
    next overlapping box below it; only shrink text when even that full
    headroom is not enough. Non-``_GROWABLE_KINDS`` boxes are returned
    unchanged (``box_scale`` 1.0) -- growing those is out of this fix's scope.

    The shrink is not floored at ``MIN_SOURCE_RELATIVE_SCALE`` (the "never
    below 90% of source size" policy ``FitParameters.source_factor``/
    ``page_scale`` apply to a whole *page*): this is a narrow, per-box last
    resort for one specific overlap, not a page-wide readability policy, so
    it shrinks as far as the headroom actually needs -- ``render_css.
    text_px``'s own ``MIN_FONT_PX`` absolute floor still applies underneath
    it regardless, exactly as it does for every other font size this renderer
    computes.
    """

    x, y, width, height = box
    if component.kind not in _GROWABLE_KINDS or not component.paragraphs:
        return box, 1.0
    natural = _natural_text_height_px(component, ctx, width_px=width, box_scale=1.0)
    if natural <= height:
        return box, 1.0
    ceiling = _growth_ceiling(box, others, page_bottom=page_bottom)
    available = max(height, ceiling - y)
    if natural <= available:
        return (x, y, width, natural), 1.0
    scale = available / natural if natural > 0 else 1.0
    return (x, y, width, available), scale


def layout_source_geometry(plan: ComposerSlidePlan, ctx: RenderContext) -> str:
    page = page_box(ctx.deck, ctx.params)
    placed = _placed(plan)
    boxes: list[Box] = []
    for component in placed:
        if component.source_rect is None:
            raise TextRenderError(
                f"component {component.component_id!r} has no source rectangle for source geometry"
            )
        boxes.append(rect_box(component.source_rect, page))
    page_bottom = page[1] + page[3]
    out = [side_bands(page, ctx)]
    for index, component in enumerate(placed):
        box = boxes[index]
        others = boxes[:index] + boxes[index + 1 :]
        grown, auto_scale = _fit_growable_box(component, ctx, box, others, page_bottom=page_bottom)
        x, y, width, height = grown
        ladder_scale = float(ctx.params.box_text_scale.get(component.component_id, 1.0))
        box_scale = ladder_scale * auto_scale
        inner = _render_placed(component, ctx, box_scale, width)
        if not inner:
            continue
        style = f"left:{px(x)};top:{px(y)};width:{px(width)};height:{px(height)}"
        if component.kind in _PROTECTED_KINDS:
            style += f";z-index:{_PROTECTED_TEXT_Z_INDEX}"
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
