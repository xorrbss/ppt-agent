"""Template chrome: title band, footer band, running header and the content flow.

Band heights and margins come from ``render_css.chrome_geometry``; the title
keeps its size and left edge at every level. Furniture lives in the bands as
bound text. The content area is a vertical sequence of segments in source
order: a ``comparison``/``process``/``table`` component (or any other kind
outside ``FLOW_KINDS``) never shares a balanced column with flow text -- it
renders as its own full-width row -- while runs of flow components
(paragraph/bullets/numbered/quote/kpi/caption) between the wide rows are
balanced across ``params.columns`` columns by character weight at paragraph
boundaries. An image region sits beside the whole sequence when the plan
carries pictures.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from services.composer.estimate_components import wide_sibling_image_floor_px
from services.composer.plan import ComposerComponent, ComposerSlidePlan
from services.composer.render_components import (
    FLOW_KINDS,
    RenderContext,
    render_component,
    render_furniture_elements,
    render_image,
    render_text_stack,
)
from services.composer.render_css import px
from services.composer.render_cover import render_cover_body
from services.composer.render_text import open_tag

_HEADER_FURNITURE = frozenset({"running_header"})
_RIGHT_FURNITURE = frozenset({"page_number"})

#: Chrome level at which a still-cramped image row collapses to a single
#: horizontal strip (one row, every image side by side) instead of the
#: multi-row grid ``_flex_image_row`` otherwise picks by count: multiple rows
#: divide the same height budget further, shrinking every image again.
_STRIP_CHROME_LEVEL = 3


def render_document(body: str, css: str) -> str:
    """The complete slide document; ``body`` must already be whitespace-free."""

    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<style>{css}</style></head><body>{body}</body></html>"
    )


def layout_chrome(plan: ComposerSlidePlan, ctx: RenderContext) -> str:
    if plan.archetype == "cover":
        return render_cover_body(plan, ctx) + _footer_band(plan, ctx)
    return _title_band(plan, ctx) + _content(plan, ctx) + _footer_band(plan, ctx)


def _furniture(plan: ComposerSlidePlan, kinds: frozenset[str] | None) -> list[ComposerComponent]:
    """Furniture of the given kinds; ``None`` selects footer-band leftovers."""

    selected: list[ComposerComponent] = []
    for component in plan.furniture:
        kind = component.furniture_kind or ""
        if kinds is None:
            if kind not in _HEADER_FURNITURE and kind not in _RIGHT_FURNITURE:
                selected.append(component)
        elif kind in kinds:
            selected.append(component)
    return selected


def _title_band(plan: ComposerSlidePlan, ctx: RenderContext) -> str:
    out = [open_tag("header", qid=ctx.qids.claim("title-band"), role="decoration", css="cx-title-band")]
    stack: list[str] = []
    if plan.title is not None:
        stack.append(render_text_stack(plan.title, ctx))
    if plan.subtitle is not None and ctx.geometry.subtitle_in_band:
        stack.append(render_text_stack(plan.subtitle, ctx))
    stack = [part for part in stack if part]
    if stack:
        out.append(open_tag("div", qid=ctx.qids.claim("title-stack"), role="decoration", css="cx-title-stack"))
        out.extend(stack)
        out.append("</div>")
    header_elements = [
        element
        for component in _furniture(plan, _HEADER_FURNITURE)
        for element in render_furniture_elements(component, ctx)
    ]
    if header_elements:
        out.append(open_tag("div", qid=ctx.qids.claim("running-header"), role="decoration", css="cx-running-header"))
        out.extend(header_elements)
        out.append("</div>")
    out.append("</header>")
    return "".join(out)


def _footer_band(plan: ComposerSlidePlan, ctx: RenderContext) -> str:
    out = [open_tag("footer", qid=ctx.qids.claim("footer-band"), role="decoration", css="cx-footer-band")]
    left = [
        element for component in _furniture(plan, None) for element in render_furniture_elements(component, ctx)
    ]
    right = [
        element
        for component in _furniture(plan, _RIGHT_FURNITURE)
        for element in render_furniture_elements(component, ctx)
    ]
    for group, hint in ((left, "footer-left"), (right, "footer-right")):
        if not group and hint == "footer-right":
            continue
        out.append(open_tag("div", qid=ctx.qids.claim(hint), role="decoration", css="cx-footer-group"))
        out.extend(group)
        out.append("</div>")
    out.append("</footer>")
    return "".join(out)


@dataclass(frozen=True)
class _Unit:
    component: ComposerComponent
    index: int | None
    weight: float


def _paragraph_weight(text: str) -> float:
    return max(len(text), 8) + 20.0


#: One content segment: a run of balanced flow units, or one standalone wide row.
_Segment = tuple[str, "list[_Unit] | ComposerComponent"]


def _segments(plan: ComposerSlidePlan, ctx: RenderContext) -> list[_Segment]:
    """Split ``plan.components`` into flow runs and standalone wide rows, in
    source order. Only ``FLOW_KINDS`` ever share a balanced column; a
    ``comparison``/``process``/``table`` component -- or any other kind, as a
    defensive default -- always gets its own full-width row instead, so a
    large block is never squeezed into half a column (the defect this
    module's docstring and ``docs/`` evidence describe)."""

    segments: list[_Segment] = []
    current: list[_Unit] = []
    if plan.subtitle is not None and not ctx.geometry.subtitle_in_band:
        current.extend(
            _Unit(plan.subtitle, index, _paragraph_weight(paragraph.text))
            for index, paragraph in enumerate(plan.subtitle.paragraphs)
        )
    for component in plan.components:
        if component.kind in FLOW_KINDS:
            current.extend(
                _Unit(component, index, _paragraph_weight(paragraph.text))
                for index, paragraph in enumerate(component.paragraphs)
            )
            continue
        if current:
            segments.append(("flow", current))
            current = []
        segments.append(("wide", component))
    if current:
        segments.append(("flow", current))
    return segments


def balanced_partition(weights: list[float], columns: int) -> list[tuple[int, int]]:
    """Contiguous ``[start, end)`` ranges minimizing the heaviest column."""

    count = len(weights)
    columns = max(1, min(int(columns), count)) if count else 1
    if count == 0:
        return [(0, 0)]
    prefix = [0.0]
    for weight in weights:
        prefix.append(prefix[-1] + weight)
    best = [[math.inf] * (columns + 1) for _ in range(count + 1)]
    cut = [[0] * (columns + 1) for _ in range(count + 1)]
    best[0][0] = 0.0
    for end in range(1, count + 1):
        for parts in range(1, min(columns, end) + 1):
            for start in range(parts - 1, end):
                candidate = max(best[start][parts - 1], prefix[end] - prefix[start])
                if candidate < best[end][parts]:
                    best[end][parts] = candidate
                    cut[end][parts] = start
    ranges: list[tuple[int, int]] = []
    end = count
    for parts in range(columns, 0, -1):
        start = cut[end][parts]
        ranges.append((start, end))
        end = start
    ranges.reverse()
    return ranges


def _render_units(units: list[_Unit], ctx: RenderContext) -> str:
    """Render consecutive units, merging a component's paragraphs into one container."""

    out: list[str] = []
    position = 0
    while position < len(units):
        unit = units[position]
        if unit.index is None:
            out.append(render_component(unit.component, ctx))
            position += 1
            continue
        selection = [unit.index]
        while position + 1 < len(units) and units[position + 1].component is unit.component:
            position += 1
            selection.append(units[position].index or 0)
        out.append(render_component(unit.component, ctx, selection=selection))
        position += 1
    return "".join(out)


def _columns(units: list[_Unit], ctx: RenderContext, columns: int) -> str:
    ranges = balanced_partition([unit.weight for unit in units], columns)
    out: list[str] = []
    for start, end in ranges:
        out.append(open_tag("div", qid=ctx.qids.claim("col"), role="decoration", css="cx-col"))
        out.append(_render_units(units[start:end], ctx))
        out.append("</div>")
    return "".join(out)


def _image_grid(images: list[ComposerComponent], ctx: RenderContext, *, height: float, wide: bool) -> str:
    """Images sized to a known, Python-computed ``height``: used for a slide
    with no wide component, where the image region owns the *whole* content
    height (or shares it evenly with flow text) rather than competing with a
    wide row for it -- unlike ``_flex_image_row``, an estimate can never be
    the reason these overflow, so a fixed pixel height per image is safe.
    """

    count = len(images)
    per_row = 1 if count == 1 or not wide else (2 if count <= 4 else 3)
    rows = -(-count // per_row)
    figure_height = (height - ctx.density.gap * (rows - 1)) / rows
    style = f"grid-template-columns:repeat({per_row},minmax(0,1fr))"
    out = [open_tag("div", qid=ctx.qids.claim("images"), role="decoration", css="cx-images", style=style)]
    out.extend(render_image(image, ctx, height_px=figure_height) for image in images)
    out.append("</div>")
    return "".join(out)


def _flow_region(units: list[_Unit], ctx: RenderContext, columns: int) -> str:
    """One balanced-column grid for a run of flow units between wide rows."""

    span = max(1, min(columns, len(units))) if units else 1
    style = f"grid-template-columns:repeat({span},minmax(0,1fr))"
    out = [open_tag("div", qid=ctx.qids.claim("flow"), role="decoration", css="cx-region", style=style)]
    out.append(_columns(units, ctx, span))
    out.append("</div>")
    return "".join(out)


def _wide_row(component: ComposerComponent, ctx: RenderContext) -> str:
    """A ``comparison``/``process``/``table`` (or other non-flow) component,
    full width, never squeezed into a balanced column."""

    out = [
        open_tag(
            "div",
            qid=ctx.qids.claim(f"{component.component_id}-row"),
            role="decoration",
            css="cx-row",
            style="grid-column:1 / -1",
        )
    ]
    out.append(render_component(component, ctx))
    out.append("</div>")
    return "".join(out)


def _flex_image_row(images: list[ComposerComponent], ctx: RenderContext, *, strip: bool) -> str:
    """The image row when it shares the content area with a wide component:
    a flex-grow child of ``.cx-content``'s flex column (see ``_content``), so
    its height comes from whatever the browser leaves after the text
    segments above it -- never from a Python estimate of that text's height.

    ``min-height`` is the floor a thumbnail never shrinks below
    (``wide_sibling_image_floor_px``); ``flex-grow`` lets it take more when
    the text above leaves more. The row's own grid uses fixed-fraction
    (``1fr``) row *and* column tracks -- never ``auto`` -- so every image's
    cell is a definite size the moment the row's own height is resolved,
    with ``overflow:hidden`` on the row and ``cx-image-flex``'s
    ``max-height``/``max-width:100%`` on each image as the backstop: an
    image can shrink past its own cell's content size, but it can never grow
    past it, so it can never leave the canvas or paint over the text above
    it -- unlike the estimate-derived fixed height this replaces, which
    could ask for more room than either the text or the canvas actually had.
    When even the floor does not fit, ``min-height`` still wins the row its
    floor and the *text* overflows instead, which the fit gate then reports
    honestly and sends the ladder to the next rung.
    """

    count = len(images)
    per_row = count if strip else (1 if count == 1 else (2 if count <= 4 else 3))
    rows = -(-count // per_row)
    floor = wide_sibling_image_floor_px(ctx.params)
    style = (
        f"grid-template-columns:repeat({per_row},minmax(0,1fr));"
        f"grid-template-rows:repeat({rows},minmax(0,1fr));"
        f"flex:1 1 {px(floor)};min-height:{px(floor)};overflow:hidden;"
    )
    out = [open_tag("div", qid=ctx.qids.claim("images"), role="decoration", css="cx-images", style=style)]
    out.extend(render_image(image, ctx, flex=True) for image in images)
    out.append("</div>")
    return "".join(out)


def _render_segments(segments: list[_Segment], ctx: RenderContext, columns: int) -> str:
    out: list[str] = []
    for kind, payload in segments:
        if kind == "wide":
            out.append(_wide_row(payload, ctx))
        else:
            out.append(_flow_region(payload, ctx, columns))
    return "".join(out)


def _content(plan: ComposerSlidePlan, ctx: RenderContext) -> str:
    segments = _segments(plan, ctx)
    images = [image for image in plan.images if image.kind == "image"]
    columns = max(1, ctx.params.columns)
    height = float(ctx.geometry.content_height)
    style = ""
    body: list[str] = []
    has_wide = any(kind == "wide" for kind, _payload in segments)
    if segments and images and has_wide:
        #: A wide row needs the *full* content width, so it cannot sit beside
        #: the images in a 7fr text track the way pure flow text can (the
        #: ``elif`` branch below). Stack every segment, then the image row,
        #: as one flex column instead: every text segment is ``flex:0 0 auto``
        #: (``.cx-row``/``.cx-region``, ``render_css.stylesheet``) so it never
        #: shrinks, and the image row is the sole flexible child, so the
        #: *browser* -- never a Python estimate of the text's height --
        #: decides how much room is actually left for the images. See
        #: ``_flex_image_row`` for why that can never push an image off
        #: canvas or under the text the way an estimate-derived height could.
        style = "display:flex;flex-direction:column;min-height:0"
        strip = ctx.geometry.level >= _STRIP_CHROME_LEVEL
        body.append(_render_segments(segments, ctx, columns))
        body.append(_flex_image_row(images, ctx, strip=strip))
    elif segments and images:
        style = "grid-template-columns:minmax(0,7fr) minmax(0,5fr)"
        body.append(_render_segments(segments, ctx, min(columns, 2)))
        body.append(_image_grid(images, ctx, height=height, wide=False))
    elif segments:
        #: No inline ``grid-template-columns``: ``.cx-content`` then has an
        #: implicit single column, so each segment (a flow run's own column
        #: grid, or a wide row) stacks full width instead of being squeezed
        #: into one of ``columns`` outer tracks.
        body.append(_render_segments(segments, ctx, columns))
    elif images:
        body.append(_image_grid(images, ctx, height=height, wide=True))
    out = [open_tag("section", qid=ctx.qids.claim("content"), role="decoration", css="cx-content", style=style)]
    out.extend(body)
    out.append("</section>")
    return "".join(out)


__all__ = ["balanced_partition", "layout_chrome", "render_document"]
