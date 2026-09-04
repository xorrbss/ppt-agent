"""Template chrome: title band, footer band, running header and the content area.

Band heights and margins come from ``render_css.chrome_geometry``; the title
keeps its size and left edge at every level. Furniture lives in the bands as
bound text.

Everything between the bands is built by two modules this one composes:

- ``render_flow``          source-order segments, balanced columns, wide rows
- ``render_image_region``  the fixed-height image grid and the flex image row

``_content`` is the only place that decides which of them a slide gets, and
whether ``params.content_centered`` (the ladder's grow step) centres the
sequence in the content area rather than anchoring it to the top.
"""

from __future__ import annotations

from services.composer.plan import ComposerComponent, ComposerSlidePlan
from services.composer.render_components import (
    RenderContext,
    render_furniture_elements,
    render_text_stack,
)
from services.composer.render_cover import render_cover_body
from services.composer.render_flow import (
    balanced_partition,
    content_segments,
    has_wide_row,
    render_segments,
)
from services.composer.render_image_region import STRIP_CHROME_LEVEL, flex_image_row, image_grid
from services.composer.render_text import open_tag

_HEADER_FURNITURE = frozenset({"running_header"})
_RIGHT_FURNITURE = frozenset({"page_number"})


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


def _content(plan: ComposerSlidePlan, ctx: RenderContext) -> str:
    segments = content_segments(plan, ctx)
    images = [image for image in plan.images if image.kind == "image"]
    columns = max(1, ctx.params.columns)
    height = float(ctx.geometry.content_height)
    style = ""
    body: list[str] = []
    wide = has_wide_row(segments)
    if segments and images and wide:
        #: A wide row needs the *full* content width, so it cannot sit beside
        #: the images in a 7fr text track the way pure flow text can (the
        #: ``elif`` branch below). Stack every segment, then the image row,
        #: as one flex column instead: every text segment is ``flex:0 0 auto``
        #: (``.cx-row``/``.cx-region``, ``render_css.stylesheet``) so it never
        #: shrinks, and the image row is the sole flexible child, so the
        #: *browser* -- never a Python estimate of the text's height --
        #: decides how much room is actually left for the images. See
        #: ``render_image_region.flex_image_row`` for why that can never push
        #: an image off canvas or under the text the way an estimate-derived
        #: height could.
        style = "display:flex;flex-direction:column;min-height:0"
        body.append(render_segments(segments, ctx, columns))
        body.append(flex_image_row(images, ctx, strip=ctx.geometry.level >= STRIP_CHROME_LEVEL))
    elif segments and images:
        style = "grid-template-columns:minmax(0,7fr) minmax(0,5fr)"
        body.append(render_segments(segments, ctx, min(columns, 2)))
        body.append(image_grid(images, ctx, height=height, wide=False))
    elif segments:
        #: No inline ``grid-template-columns``: ``.cx-content`` then has an
        #: implicit single column, so each segment (a flow run's own column
        #: grid, or a wide row) stacks full width instead of being squeezed
        #: into one of ``columns`` outer tracks.
        body.append(render_segments(segments, ctx, columns))
    elif images:
        body.append(image_grid(images, ctx, height=height, wide=True))
    if ctx.params.content_centered and not (segments and images and wide):
        #: Grow step (``fit_ladder.grow_step``): a page whose measured content
        #: stops well short of the area is centred in it instead of hanging
        #: from the top edge, overriding ``.cx-content{align-content:start}``.
        #: The wide-row-plus-images branch above is excluded: its image row is
        #: the flexible child and already takes whatever the text leaves, so
        #: that layout has no unused area to centre in.
        style = f"{style};align-content:center" if style else "align-content:center"
    out = [open_tag("section", qid=ctx.qids.claim("content"), role="decoration", css="cx-content", style=style)]
    out.extend(body)
    out.append("</section>")
    return "".join(out)


__all__ = ["balanced_partition", "layout_chrome", "render_document"]
