"""The two image regions of a template-rung slide.

Extracted from ``render_chrome`` unchanged. Which one a slide gets is
``render_chrome._content``'s decision, and the difference between them is
where the region's height comes from: ``image_grid`` is given a height Python
computed, ``flex_image_row`` is given none and takes whatever the browser
leaves after the text above it.
"""

from __future__ import annotations

from services.composer.estimate_components import wide_sibling_image_floor_px
from services.composer.plan import ComposerComponent
from services.composer.render_components import RenderContext, render_image
from services.composer.render_css import px
from services.composer.render_text import open_tag

#: Chrome level at which a still-cramped image row collapses to a single
#: horizontal strip (one row, every image side by side) instead of the
#: multi-row grid ``flex_image_row`` otherwise picks by count: multiple rows
#: divide the same height budget further, shrinking every image again.
STRIP_CHROME_LEVEL = 3


def image_grid(images: list[ComposerComponent], ctx: RenderContext, *, height: float, wide: bool) -> str:
    """Images sized to a known, Python-computed ``height``: used for a slide
    with no wide component, where the image region owns the *whole* content
    height (or shares it evenly with flow text) rather than competing with a
    wide row for it -- unlike ``flex_image_row``, an estimate can never be
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


def flex_image_row(images: list[ComposerComponent], ctx: RenderContext, *, strip: bool) -> str:
    """The image row when it shares the content area with a wide component:
    a flex-grow child of ``.cx-content``'s flex column (see
    ``render_chrome._content``), so its height comes from whatever the browser
    leaves after the text segments above it -- never from a Python estimate of
    that text's height.

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


__all__ = ["STRIP_CHROME_LEVEL", "flex_image_row", "image_grid"]
