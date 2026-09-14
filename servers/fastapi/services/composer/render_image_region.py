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


_OVERLAP_FRACTION = 0.10
_COMPARABLE_DIMENSION_FRACTION = 0.75


def _overlapping_image_groups(
    images: list[ComposerComponent],
) -> list[list[ComposerComponent]]:
    """Keep layered source pictures together when template rungs reflow images."""

    count = len(images)
    parent = list(range(count))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    def should_group(left: ComposerComponent, right: ComposerComponent) -> bool:
        left_rect = left.source_rect
        right_rect = right.source_rect
        if left_rect is None or right_rect is None:
            return False
        if min(left_rect.width, left_rect.height, right_rect.width, right_rect.height) <= 0:
            return False
        intersection_width = max(
            0.0,
            min(left_rect.x + left_rect.width, right_rect.x + right_rect.width)
            - max(left_rect.x, right_rect.x),
        )
        intersection_height = max(
            0.0,
            min(left_rect.y + left_rect.height, right_rect.y + right_rect.height)
            - max(left_rect.y, right_rect.y),
        )
        intersection_area = intersection_width * intersection_height
        smaller_area = min(
            left_rect.width * left_rect.height,
            right_rect.width * right_rect.height,
        )
        if intersection_area < smaller_area * _OVERLAP_FRACTION:
            return False
        comparable_width = min(left_rect.width, right_rect.width) / max(
            left_rect.width, right_rect.width
        )
        comparable_height = min(left_rect.height, right_rect.height) / max(
            left_rect.height, right_rect.height
        )
        return max(comparable_width, comparable_height) >= _COMPARABLE_DIMENSION_FRACTION

    for left_index, left in enumerate(images):
        for right_index in range(left_index + 1, count):
            if should_group(left, images[right_index]):
                union(left_index, right_index)

    grouped: dict[int, list[tuple[int, ComposerComponent]]] = {}
    for index, image in enumerate(images):
        grouped.setdefault(find(index), []).append((index, image))
    ordered = sorted(grouped.values(), key=lambda group: min(index for index, _ in group))
    return [
        [image for _, image in sorted(group, key=lambda item: (item[1].source_order, item[0]))]
        for group in ordered
    ]


def _percentage(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _image_layout_groups(
    images: list[ComposerComponent], ctx: RenderContext
) -> list[list[ComposerComponent]]:
    if ctx.deck.fit_policy_revision == "composer-source-fit/v2":
        return _overlapping_image_groups(images)
    return [[image] for image in images]


def _render_image_group(
    group: list[ComposerComponent],
    ctx: RenderContext,
    *,
    width_px: float | None = None,
    height_px: float | None = None,
    flex: bool = False,
) -> str:
    if len(group) == 1:
        return render_image(group[0], ctx, height_px=height_px, flex=flex)

    rects = [image.source_rect for image in group]
    if any(rect is None for rect in rects):
        return "".join(
            render_image(image, ctx, height_px=height_px, flex=flex) for image in group
        )
    typed_rects = [rect for rect in rects if rect is not None]
    left = min(rect.x for rect in typed_rects)
    top = min(rect.y for rect in typed_rects)
    right = max(rect.x + rect.width for rect in typed_rects)
    bottom = max(rect.y + rect.height for rect in typed_rects)
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        return "".join(
            render_image(image, ctx, height_px=height_px, flex=flex) for image in group
        )

    source_page = ctx.deck.source_page_emu
    physical_aspect = (
        width * float(source_page[0]) / (height * float(source_page[1]))
        if source_page is not None
        else width / height
    )
    if width_px is not None and height_px is not None:
        fitted_width = min(width_px, height_px * physical_aspect)
        fitted_height = fitted_width / physical_aspect
        sizing = f"width:{px(fitted_width)};height:{px(fitted_height)};"
    elif width_px is not None:
        sizing = (
            f"width:{px(width_px)};height:auto;aspect-ratio:{_percentage(physical_aspect)};"
            "max-width:100%;max-height:100%;"
        )
    else:
        sizing = "height:100%;width:100%;"
    out = [
        open_tag(
            "div",
            qid=ctx.qids.claim(f"image-cluster-{group[0].component_id}"),
            role="decoration",
            css="cx-image-cluster",
            style=(
                f"position:relative;{sizing}min-width:0;min-height:0;overflow:hidden;"
                "justify-self:center;align-self:center;"
            ),
        )
    ]
    for layer_index, (image, rect) in enumerate(zip(group, typed_rects, strict=True)):
        layer_style = (
            "position:absolute;"
            f"left:{_percentage((rect.x - left) * 100.0 / width)}%;"
            f"top:{_percentage((rect.y - top) * 100.0 / height)}%;"
            f"width:{_percentage(rect.width * 100.0 / width)}%;"
            f"height:{_percentage(rect.height * 100.0 / height)}%;"
            f"z-index:{layer_index};"
        )
        rendered = render_image(image, ctx).replace(
            "<figure ",
            '<figure style="height:100%;width:100%" ',
            1,
        )
        out.extend(
            (
                open_tag(
                    "div",
                    qid=ctx.qids.claim(f"image-layer-{image.component_id}"),
                    role="decoration",
                    style=layer_style,
                ),
                rendered,
                "</div>",
            )
        )
    out.append("</div>")
    return "".join(out)


def image_grid(images: list[ComposerComponent], ctx: RenderContext, *, height: float, wide: bool) -> str:
    """Images sized to a known, Python-computed ``height``: used for a slide
    with no wide component, where the image region owns the *whole* content
    height (or shares it evenly with flow text) rather than competing with a
    wide row for it -- unlike ``flex_image_row``, an estimate can never be
    the reason these overflow, so a fixed pixel height per image is safe.
    """

    groups = _image_layout_groups(images, ctx)
    count = len(groups)
    layered = any(len(group) > 1 for group in groups)
    per_row = (
        count
        if layered and count <= 3
        else (1 if count == 1 or not wide else (2 if count <= 4 else 3))
    )
    rows = -(-count // per_row)
    figure_height = (height - ctx.density.gap * (rows - 1)) / rows
    region_width = (
        float(ctx.geometry.content_width)
        if wide
        else (float(ctx.geometry.content_width) - ctx.density.gap) * 5.0 / 12.0
    )
    figure_width = (region_width - ctx.density.gap * (per_row - 1)) / per_row
    style = f"grid-template-columns:repeat({per_row},minmax(0,1fr))"
    out = [open_tag("div", qid=ctx.qids.claim("images"), role="decoration", css="cx-images", style=style)]
    out.extend(
        _render_image_group(
            group,
            ctx,
            width_px=figure_width,
            height_px=figure_height,
        )
        for group in groups
    )
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

    groups = _image_layout_groups(images, ctx)
    count = len(groups)
    layered = any(len(group) > 1 for group in groups)
    per_row = (
        count
        if strip or (layered and count <= 3)
        else (1 if count == 1 else (2 if count <= 4 else 3))
    )
    rows = -(-count // per_row)
    floor = wide_sibling_image_floor_px(ctx.params)
    figure_width = (
        float(ctx.geometry.content_width) - ctx.density.gap * (per_row - 1)
    ) / per_row
    style = (
        f"grid-template-columns:repeat({per_row},minmax(0,1fr));"
        f"grid-template-rows:repeat({rows},minmax(0,1fr));"
        f"flex:1 1 {px(floor)};min-height:{px(floor)};overflow:hidden;"
    )
    out = [open_tag("div", qid=ctx.qids.claim("images"), role="decoration", css="cx-images", style=style)]
    out.extend(
        _render_image_group(group, ctx, width_px=figure_width, flex=True)
        for group in groups
    )
    out.append("</div>")
    return "".join(out)


__all__ = ["STRIP_CHROME_LEVEL", "flex_image_row", "image_grid"]
