"""Component archetypes: text stacks, lists, process, comparison, image.

Every function returns markup with no whitespace between elements, so a
container's rendered text is the concatenation of its children's characters
and never equals a multi-run block by accident (the readback matcher needs
the source's own separators between runs). Tables live in ``render_table``.
"""

from __future__ import annotations

import json

from collections.abc import Sequence

from services.composer.estimate_components import process_per_row
from services.composer.plan import ComposerComponent, Paragraph
from services.composer.render_context import RenderContext, component_role, size_role
from services.composer.render_css import px
from services.composer.render_table import render_table
from services.composer.render_text import (
    TextRenderError,
    dominant_size_pt,
    escape_attr,
    open_tag,
    render_bound_text,
    render_flow_paragraph,
)

#: Kinds whose paragraphs may be split across columns at paragraph boundaries.
FLOW_KINDS = frozenset(
    {"title", "subtitle", "paragraph", "quote", "kpi", "caption", "bullets", "numbered"}
)
#: Kinds that never share a balanced column with flow text: they render as their
#: own full-width row instead, at ``render_flow.content_segments``.
WIDE_KINDS = frozenset({"comparison", "process", "table"})
_LIST_KINDS = frozenset({"bullets", "numbered"})
_KIND_CSS = {
    "quote": "cx-quote",
    "kpi": "cx-kpi",
    "caption": "cx-caption",
    "subtitle": "cx-subtitle",
    "title": "cx-title",
}
_KIND_TAG = {"title": "h1"}
_EMPHASIS_CSS = {"accent": " cx-accent", "muted": " cx-muted"}


def render_component(
    component: ComposerComponent,
    ctx: RenderContext,
    *,
    selection: Sequence[int] | None = None,
    box_scale: float = 1.0,
    width_px: float | None = None,
) -> str:
    """``width_px`` is the component's *actual* available width: the full
    content width for a template-rung wide row (the default, when omitted),
    or the component's own -- usually narrower -- source-geometry box at
    rung 5. Only ``render_process`` uses it (its per-row wrap decision is
    width-sensitive); every other kind ignores it."""

    kind = component.kind
    if kind in _LIST_KINDS:
        return render_list(component, ctx, selection, box_scale=box_scale)
    if kind == "process":
        return render_process(component, ctx, box_scale=box_scale, width_px=width_px)
    if kind == "comparison":
        return render_comparison(component, ctx, box_scale=box_scale)
    if kind == "table":
        return render_table(component, ctx, box_scale=box_scale)
    if kind == "image":
        return render_image(component, ctx)
    if kind == "furniture":
        return "".join(render_furniture_elements(component, ctx, box_scale=box_scale))
    return render_text_stack(component, ctx, selection, box_scale=box_scale)


def _selected(component: ComposerComponent, selection: Sequence[int] | None) -> list[tuple[int, Paragraph]]:
    if selection is None:
        return list(enumerate(component.paragraphs))
    return [(index, component.paragraphs[index]) for index in selection]


def render_text_stack(
    component: ComposerComponent,
    ctx: RenderContext,
    selection: Sequence[int] | None = None,
    *,
    box_scale: float = 1.0,
) -> str:
    """Paragraph, quote, kpi, caption, subtitle and title components."""

    items = _selected(component, selection)
    if not items:
        return ""
    role = component_role(component)
    css = f"cx-text {_KIND_CSS.get(component.kind, 'cx-body')}{_EMPHASIS_CSS.get(component.emphasis, '')}"
    tag = _KIND_TAG.get(component.kind, "p")
    out = [open_tag("div", qid=ctx.qids.claim(component.component_id), role="decoration", css="cx-component cx-stack")]
    #: Title/subtitle fit their own band (``services.composer.render_title``),
    #: independent of the body's ``type_scale``, at the template rungs (0-3).
    #: Source-relative/geometry rungs (4, 5) still size each run from its own
    #: source point size, so they fall through to the ordinary ``ctx.size``.
    band_fit = component.kind in {"title", "subtitle"} and not (
        ctx.params.source_relative or ctx.params.source_geometry
    )
    for _index, paragraph in items:
        if band_fit:
            size = (ctx.title_px if component.kind == "title" else ctx.subtitle_px) * box_scale
        else:
            dominant = dominant_size_pt(paragraph, component.source_size_pt)
            size = ctx.size(size_role(component, ctx.params), dominant, box_scale)
        element, _glyph = render_bound_text(
            paragraph, tag=tag, role=role, css=css, font_px=size, registry=ctx.registry
        )
        out.append(element)
    out.append("</div>")
    return "".join(out)


def render_list(
    component: ComposerComponent,
    ctx: RenderContext,
    selection: Sequence[int] | None = None,
    *,
    box_scale: float = 1.0,
) -> str:
    items = _selected(component, selection)
    if not items:
        return ""
    role = component_role(component)
    numbered = component.kind == "numbered"
    css = "cx-component cx-list cx-stack" + (" cx-panel" if ctx.density.panels else "")
    hint = component.component_id
    out = [open_tag("div", qid=ctx.qids.claim(hint), role="decoration", css=css)]
    for index, paragraph in items:
        dominant = dominant_size_pt(paragraph, component.source_size_pt)
        size = ctx.size(size_role(component, ctx.params), dominant, box_scale)
        out.append(
            render_flow_paragraph(
                paragraph,
                role=role,
                css="cx-text cx-body",
                font_px=size,
                registry=ctx.registry,
                index=index,
                numbered=numbered,
                item_hint=f"{hint}-item",
            )
        )
    out.append("</div>")
    return "".join(out)


def _groups(component: ComposerComponent) -> list[list[Paragraph]]:
    if component.groups:
        return component.groups
    return [[paragraph] for paragraph in component.paragraphs]


def _group_body(
    component: ComposerComponent,
    ctx: RenderContext,
    group_index: int,
    group: list[Paragraph],
    *,
    header_css: str,
    box_scale: float,
) -> str:
    role = component_role(component)
    out: list[str] = []
    headers = component.group_headers
    header = headers[group_index] if group_index < len(headers) else None
    if header is not None:
        dominant = dominant_size_pt(header, component.source_size_pt)
        size = ctx.size(size_role(component, ctx.params), dominant, box_scale)
        element, _glyph = render_bound_text(
            header, tag="p", role=role, css=f"cx-text cx-body {header_css}", font_px=size, registry=ctx.registry
        )
        out.append(element)
    for index, paragraph in enumerate(group):
        dominant = dominant_size_pt(paragraph, component.source_size_pt)
        size = ctx.size(size_role(component, ctx.params), dominant, box_scale)
        out.append(
            render_flow_paragraph(
                paragraph,
                role=role,
                css="cx-text cx-body",
                font_px=size,
                registry=ctx.registry,
                index=index,
                item_hint=f"{component.component_id}-g{group_index}",
            )
        )
    return "".join(out)


def render_process(
    component: ComposerComponent, ctx: RenderContext, *, box_scale: float = 1.0, width_px: float | None = None
) -> str:
    """Horizontal steps with CSS arrow decorations; wraps to two rows past four
    steps, or earlier when ``estimate_components.process_per_row`` finds a
    single row would render taller than two (see its own docstring)."""

    groups = _groups(component)
    count = len(groups)
    if count == 0:
        return ""
    width = float(width_px) if width_px is not None else float(ctx.geometry.content_width)
    per_row = process_per_row(component, ctx.params, ctx.deck, width_px=width)
    hint = component.component_id
    out = [
        open_tag(
            "div",
            qid=ctx.qids.claim(hint),
            role="decoration",
            css="cx-component cx-process",
            style=f"grid-template-columns:repeat({per_row},minmax(0,1fr))",
        )
    ]
    for index, group in enumerate(groups):
        out.append(open_tag("div", qid=ctx.qids.claim(f"{hint}-step{index}"), role="decoration", css="cx-step"))
        out.append(_group_body(component, ctx, index, group, header_css="cx-step-head", box_scale=box_scale))
        last_in_row = index % per_row == per_row - 1 or index == count - 1
        if not last_in_row:
            arrow_qid = ctx.qids.claim(f"{hint}-arrow{index}")
            out.append(
                f'<i class="cx-arrow" data-qid="{arrow_qid}" data-semantic-role="decoration" aria-hidden="true"></i>'
            )
        out.append("</div>")
    out.append("</div>")
    return "".join(out)


def render_comparison(component: ComposerComponent, ctx: RenderContext, *, box_scale: float = 1.0) -> str:
    """Two or three columns, each with a header cell and its paragraphs."""

    groups = _groups(component)
    count = len(groups)
    if count == 0:
        return ""
    hint = component.component_id
    out = [
        open_tag(
            "div",
            qid=ctx.qids.claim(hint),
            role="decoration",
            css="cx-component cx-comparison",
            style=f"grid-template-columns:repeat({count},minmax(0,1fr))",
        )
    ]
    for index, group in enumerate(groups):
        css = "cx-compare-col" + (" cx-panel" if ctx.density.panels else "")
        out.append(open_tag("div", qid=ctx.qids.claim(f"{hint}-col{index}"), role="decoration", css=css))
        out.append(_group_body(component, ctx, index, group, header_css="cx-compare-head", box_scale=box_scale))
        out.append("</div>")
    out.append("</div>")
    return "".join(out)


def render_image(
    component: ComposerComponent, ctx: RenderContext, *, height_px: float | None = None, flex: bool = False
) -> str:
    """An image slot addressed by asset id; the substitution stage writes ``src``.

    The marker follows ``utils.authored_image_slots`` (``<img data-asset-id>``),
    which the source-image stage resolves to a data URL and strips before final
    admission. The visibility seal replaces the QID with the occurrence's own.

    ``flex=True`` selects ``cx-image-flex`` instead of the ordinary
    ``cx-image``: used by ``render_image_region.flex_image_row``, whose grid cell
    is stretched to a definite size by fixed-fraction row/column tracks
    (never ``auto``), so the image is capped with ``max-height``/
    ``max-width`` instead of forced to fill it via ``width``/``height``.
    ``height_px`` and ``flex`` are never both set: the two are the
    renderer's two ways of giving an image a definite box, one from a
    Python-computed pixel height, the other from the browser's own layout.
    """

    if not component.asset_id:
        raise TextRenderError(f"image component {component.component_id!r} has no asset id")
    figure_qid = ctx.qids.claim(f"{component.component_id}-figure")
    source_policy = ctx.deck.fit_policy_revision == "composer-source-fit/v2"
    image_qid = ctx.registry.bind(component.component_id) if source_policy else ctx.qids.claim(component.component_id)
    style = f"height:{px(height_px)}" if height_px else ""
    image_css = "cx-image-flex" if flex else "cx-image"
    source_policy = ctx.deck.fit_policy_revision == "composer-source-fit/v2"
    image_extra = f' data-source-block-id="{escape_attr(component.component_id)}"' if source_policy else ""
    if source_policy and component.image_color_transforms:
        transforms = json.dumps(component.image_color_transforms, separators=(",", ":"), ensure_ascii=True)
        image_extra += f' data-source-image-color-transforms="{escape_attr(transforms)}"'
    if source_policy and component.image_crop:
        crop = ",".join(str(float(component.image_crop.get(key, 0))) for key in ("left", "top", "right", "bottom"))
        image_extra += f' data-source-image-crop="{crop}"'
    if source_policy and (component.source_transform.get("flipH") or component.source_transform.get("flipV")):
        flip = f'{int(bool(component.source_transform.get("flipH")))},{int(bool(component.source_transform.get("flipV")))}'
        image_extra += f' data-source-image-flip="{flip}"'
    if source_policy and ctx.params.source_geometry:
        style = "height:100%;width:100%"
        image_extra += ' style="object-fit:fill"'
    return (
        open_tag("figure", qid=figure_qid, role="decoration", css="cx-component cx-figure", style=style)
        + f'<img class="{image_css}" data-asset-id="{escape_attr(component.asset_id)}" '
        f'data-qid="{image_qid}" data-semantic-role="image"{image_extra}>'
        + "</figure>"
    )


def render_furniture_elements(
    component: ComposerComponent, ctx: RenderContext, *, box_scale: float = 1.0
) -> list[str]:
    """Footer text, page numbers and running headers as bound spans."""

    role = component.semantic_role or "furniture"
    elements: list[str] = []
    for paragraph in component.paragraphs:
        size = ctx.size("furniture", dominant_size_pt(paragraph, component.source_size_pt), box_scale)
        element, _glyph = render_bound_text(
            paragraph, tag="span", role=role, css="cx-text cx-furniture", font_px=size, registry=ctx.registry
        )
        elements.append(element)
    return elements


__all__ = [
    "FLOW_KINDS",
    "WIDE_KINDS",
    "RenderContext",
    "component_role",
    "render_comparison",
    "render_component",
    "render_furniture_elements",
    "render_image",
    "render_list",
    "render_process",
    "render_table",
    "render_text_stack",
    "size_role",
]
