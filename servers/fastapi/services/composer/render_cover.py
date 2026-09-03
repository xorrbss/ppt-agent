"""The cover archetype: a centred title/subtitle stack, no title band.

Furniture (page number, footer, running header) still lives in the footer
band, rendered by ``render_chrome`` exactly as for every other archetype;
only the title band and the ordinary content grid are replaced by the
centred stack here. A small accent rule sits above the title as a pure
decoration (``aria-hidden``, so it needs no source binding).
"""

from __future__ import annotations

from services.composer.plan import ComposerComponent, ComposerSlidePlan, TITLE_FLOOR_PX
from services.composer.render_components import RenderContext, component_role, render_component
from services.composer.render_text import open_tag, render_bound_text
from services.composer.render_title import fit_title_px, title_base_px

#: Cap on the enlarged cover title, whatever the deck/template size is.
COVER_TITLE_MAX_PX = 72.0
#: Cover subtitle / paragraph lines, before density scaling.
COVER_PARAGRAPH_PX = 28.0
#: Kinds simple enough to render as plain centred lines at the cover paragraph size.
_COVER_LINE_KINDS = frozenset({"subtitle", "paragraph", "quote", "caption", "kpi"})


def _cover_title_px(plan: ComposerSlidePlan, ctx: RenderContext, width_px: float) -> float:
    """``min(2x the deck's title size, 72px)``, stepped down until <= 3 lines."""

    if plan.title is None:
        return COVER_TITLE_MAX_PX
    base = min(2.0 * title_base_px(ctx.deck, ctx.params), COVER_TITLE_MAX_PX)
    texts = [paragraph.text for paragraph in plan.title.paragraphs]
    return fit_title_px(texts, base, width_px, max_lines=3, floor=TITLE_FLOOR_PX)


def _cover_paragraph_px(ctx: RenderContext) -> float:
    """28px, density-scaled the same way the dense variant tightens line-height."""

    return COVER_PARAGRAPH_PX * (ctx.density.line_height / 1.6)


def _line_component(component: ComposerComponent, ctx: RenderContext, font_px: float) -> str:
    """One component's paragraphs as plain centred ``<p>`` lines."""

    role = component_role(component)
    out = [open_tag("div", qid=ctx.qids.claim(component.component_id), role="decoration", css="cx-component cx-stack")]
    for paragraph in component.paragraphs:
        element, _glyph = render_bound_text(
            paragraph, tag="p", role=role, css="cx-text cx-subtitle", font_px=font_px, registry=ctx.registry
        )
        out.append(element)
    out.append("</div>")
    return "".join(out)


def render_cover_body(plan: ComposerSlidePlan, ctx: RenderContext) -> str:
    """The centred stack that replaces the title band and content grid."""

    width = float(ctx.geometry.content_width)
    title_px = _cover_title_px(plan, ctx, width)
    para_px = _cover_paragraph_px(ctx)
    out = [open_tag("section", qid=ctx.qids.claim("cover"), role="decoration", css="cx-cover")]
    out.append(open_tag("div", qid=ctx.qids.claim("cover-stack"), role="decoration", css="cx-cover-stack"))
    out.append(
        open_tag(
            "div", qid=ctx.qids.claim("cover-rule"), role="decoration", css="cx-cover-rule",
            extra='aria-hidden="true"',
        )
        + "</div>"
    )
    if plan.title is not None:
        out.append(open_tag("div", qid=ctx.qids.claim("cover-title"), role="decoration", css="cx-component cx-stack"))
        for paragraph in plan.title.paragraphs:
            element, _glyph = render_bound_text(
                paragraph, tag="h1", role="title", css="cx-text cx-title", font_px=title_px, registry=ctx.registry
            )
            out.append(element)
        out.append("</div>")
    if plan.subtitle is not None:
        out.append(_line_component(plan.subtitle, ctx, para_px))
    for component in plan.components:
        if component.kind in _COVER_LINE_KINDS:
            out.append(_line_component(component, ctx, para_px))
        else:
            out.append(render_component(component, ctx))
    out.append("</div></section>")
    return "".join(out)


__all__ = ["COVER_PARAGRAPH_PX", "COVER_TITLE_MAX_PX", "render_cover_body"]
