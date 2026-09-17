"""Bound text elements: exact source characters, run formatting and markers.

The element bound to a paragraph carries that paragraph's projected characters
(``services.source_content.source_runs.project_source_runs``), so the readback
matcher accepts it by construction: the leading marker glyph may be drawn by a
sibling decoration, source lines become ``<br>``, and nothing else is added,
dropped or reordered. QIDs of bound elements are
``deterministic_source_qid(binding_id)``; every other QID is minted here with a
``cx-`` prefix so the two namespaces can never collide.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from services.composer.plan import Paragraph, TextRun

if TYPE_CHECKING:
    from services.composer.render_context import RenderContext
from services.composer.render_css import px
from services.source_content.source_runs import normalize_source_text, project_source_runs
from services.source_content.source_stamp_dom import (
    deterministic_source_qid,
    source_block_data_attribute,
)

_QID_UNSAFE = re.compile(r"[^A-Za-z0-9._:-]+")
_CIRCLED_DIGITS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
_INDENT_PX = 24


class TextRenderError(ValueError):
    """The plan asks for markup the renderer cannot bind by construction."""


def escape_text(value: str) -> str:
    return html.escape(str(value), quote=False)


def escape_attr(value: str) -> str:
    return html.escape(str(value), quote=True)


class QidFactory:
    """Unique renderer-owned QIDs; source-bound QIDs are only *reserved* here."""

    def __init__(self) -> None:
        self._used: set[str] = set()

    def reserve(self, qid: str) -> str:
        if qid in self._used:
            raise TextRenderError(f"duplicate qid {qid!r}")
        self._used.add(qid)
        return qid

    def claim(self, hint: str) -> str:
        base = "cx-" + (_QID_UNSAFE.sub("-", str(hint)).strip("-") or "node")[:96]
        qid, suffix = base, 2
        while qid in self._used:
            qid = f"{base}.{suffix}"
            suffix += 1
        self._used.add(qid)
        return qid


@dataclass
class BindingRegistry:
    """Every source binding rendered on the slide, each exactly once."""

    qids: QidFactory = field(default_factory=QidFactory)
    bound: set[str] = field(default_factory=set)
    source_context: RenderContext | None = field(default=None, repr=False)
    source_marker_counters: dict[str, dict[int, int]] = field(default_factory=dict)

    def bind(self, binding_id: str) -> str:
        if binding_id in self.bound:
            raise TextRenderError(f"source run {binding_id!r} rendered twice")
        self.bound.add(binding_id)
        return self.qids.reserve(deterministic_source_qid(binding_id))


def open_tag(tag: str, *, qid: str, role: str, css: str = "", style: str = "", extra: str = "") -> str:
    parts = [tag, f'data-qid="{escape_attr(qid)}"', f'data-semantic-role="{escape_attr(role)}"']
    if css:
        parts.append(f'class="{css}"')
    if style:
        parts.append(f'style="{style}"')
    if extra:
        parts.append(extra)
    return "<" + " ".join(parts) + ">"


@dataclass(frozen=True)
class _Line:
    marker: str
    body: str
    full: str


def projected_lines(text: str) -> list[_Line]:
    """Visible lines of a paragraph as the readback projects them."""

    try:
        projection = project_source_runs(text)
    except ValueError as error:
        raise TextRenderError("paragraph has no visible source text") from error
    lines: list[_Line] = []
    for first, last in projection.lines:
        head, tail = projection.runs[first], projection.runs[last]
        lines.append(
            _Line(
                marker=head.marker.strip(),
                body=projection.text[head.body_begin : tail.end],
                full=projection.text[head.begin : tail.end],
            )
        )
    return lines


def marker_glyph(paragraph: Paragraph, lines: list[_Line], *, numbered: bool, index: int) -> str | None:
    """Glyph a sibling decoration draws for this item, or ``None``.

    The source's own marker wins so a numbered source stays numbered; a plan
    bullet without a source glyph gets the template's.
    """

    if lines and lines[0].marker:
        return lines[0].marker
    if not (paragraph.bullet or numbered):
        return None
    kind = (paragraph.bullet_kind or "").lower()
    if numbered or "num" in kind or "decimal" in kind:
        return f"{index + 1}."
    if "circle" in kind:
        return _CIRCLED_DIGITS[index] if index < len(_CIRCLED_DIGITS) else f"{index + 1}."
    return "•" if paragraph.level == 0 else "–"


def dominant_size_pt(paragraph: Paragraph, fallback: float | None) -> float | None:
    """Character-weighted source size of the paragraph's runs."""

    weighted = [
        (len(run.text), float(run.size_pt))
        for run in paragraph.runs
        if run.size_pt and run.text.strip()
    ]
    total = sum(weight for weight, _size in weighted)
    if not total:
        return fallback
    return sum(weight * size for weight, size in weighted) / total


def _format_groups(runs: list[TextRun]) -> list[tuple[str, bool, bool]]:
    """Coalesce adjacent runs that share bold/italic; whitespace joins its neighbour."""

    groups: list[tuple[str, bool, bool]] = []
    for run in runs:
        if not run.text:
            continue
        if groups and (groups[-1][1], groups[-1][2]) == (run.bold, run.italic):
            groups[-1] = (groups[-1][0] + run.text, run.bold, run.italic)
        elif groups and not run.text.strip():
            groups[-1] = (groups[-1][0] + run.text, groups[-1][1], groups[-1][2])
        else:
            groups.append((run.text, run.bold, run.italic))
    return groups


def _inline_spans(text: str, groups: list[tuple[str, bool, bool]], *, qid: str, role: str, peeled: str, source_runs: bool=False) -> str | None:
    """Split one rendered line into formatting spans; ``None`` when runs do not map.

    Every span is a strict subset of the line so the bound element stays the
    only exact owner of the run.
    """

    pieces: list[str] = []
    cursor = 0
    emitted = 0
    for index, (raw, bold, italic) in enumerate(groups):
        norm = normalize_source_text(raw)
        if emitted == 0 and peeled and norm.startswith(peeled):
            norm = norm[len(peeled) :].lstrip()
        if not norm:
            continue
        position = text.find(norm, cursor)
        if position < 0:
            return None
        pieces.append(escape_text(text[cursor:position]))
        style = ";".join(
            part for part in ("font-weight:700" if bold else "", "font-style:italic" if italic else "") if part
        )
        style_attr = f' style="{style}"' if style else ""
        pieces.append(
            (f'<span data-qid-ignore="" data-source-run="{index}"{style_attr}>{escape_text(norm)}</span>' if source_runs else f'<span data-qid="{qid}.r{index}" data-semantic-role="{escape_attr(role)}"{style_attr}>'
            f"{escape_text(norm)}</span>")
        )
        cursor = position + len(norm)
        emitted += 1
    tail = text[cursor:]
    if tail.strip() or emitted < 2:
        return None
    pieces.append(escape_text(tail))
    return "".join(pieces)


def render_bound_text(
    paragraph: Paragraph,
    *,
    tag: str,
    role: str,
    css: str,
    font_px: float,
    registry: BindingRegistry,
    draw_marker: bool = False,
    numbered: bool = False,
    index: int = 0,
    extra_style: str = "",
) -> tuple[str, str | None]:
    """Render the element bound to ``paragraph``.

    Returns the element and the marker glyph a sibling decoration must draw
    (``None`` when the text keeps or has no marker).
    """

    source_marker = None
    ctx = registry.source_context
    source_bullet = bool(ctx and ctx.deck.fit_policy_revision == "composer-source-fit/v2"
                        and paragraph.layout.get("bullet"))
    if source_bullet:
        draw_marker = False
        if paragraph.layout.get("sourceBulletOwner"):
            from services.composer.render_source_markers import render_source_marker
            counters = registry.source_marker_counters.setdefault(paragraph.source.block_id, {})
            source_marker = render_source_marker(paragraph, ctx, font_px=font_px, counters=counters)
    lines = projected_lines(paragraph.text)
    glyph = marker_glyph(paragraph, lines, numbered=numbered, index=index) if draw_marker else None
    peeled = lines[0].marker if glyph and lines[0].marker else ""
    qid = registry.bind(paragraph.source.binding_id)
    rendered = [line.body if (peeled and number == 0) else line.full for number, line in enumerate(lines)]
    style = f"font-size:{px(font_px)}"
    if paragraph.level and not draw_marker:
        style += f";padding-left:{px(paragraph.level * _INDENT_PX)}"
    if extra_style:
        style += ";" + extra_style
    groups = _format_groups(paragraph.runs)
    inner: str | None = None
    if len(lines) == 1 and len(groups) >= 2:
        inner = _inline_spans(rendered[0], groups, qid=qid, role=role, peeled=peeled, source_runs=bool(ctx and ctx.deck.fit_policy_revision == "composer-source-fit/v2"))
    if inner is None:
        inner = "<br>".join(escape_text(line) for line in rendered)
        if groups and all(bold for _text, bold, _italic in groups):
            style += ";font-weight:700"
        if groups and all(italic for _text, _bold, italic in groups):
            style += ";font-style:italic"
    attrs = (
        f'data-qid="{qid}" {source_block_data_attribute(paragraph.source.binding_id)} '
        f'data-semantic-role="{escape_attr(role)}" class="{css}" style="{style}"'
    )
    element = f'<{tag} {attrs}>{inner}</{tag}>'
    if source_marker:
        item_qid = registry.qids.claim("source-item")
        element = (f'<div class="cx-item" data-qid="{item_qid}" data-semantic-role="decoration">'
                   + source_marker + element + '</div>')
    return element, glyph


def render_marker(glyph: str, *, qids: QidFactory, font_px: float) -> str:
    """The sibling decoration that draws a list marker; never source text."""

    qid = qids.claim("marker")
    return (
        f'<i class="cx-marker" data-qid="{qid}" data-semantic-role="decoration" aria-hidden="true" '
        f'style="font-size:{px(font_px)}">{escape_text(glyph)}</i>'
    )


def render_flow_paragraph(
    paragraph: Paragraph,
    *,
    role: str,
    css: str,
    font_px: float,
    registry: BindingRegistry,
    index: int,
    numbered: bool = False,
    item_hint: str = "item",
) -> str:
    """A paragraph inside a list-like flow: marker decoration plus bound text."""

    element, glyph = render_bound_text(
        paragraph,
        tag="p",
        role=role,
        css=css,
        font_px=font_px,
        registry=registry,
        draw_marker=True,
        numbered=numbered,
        index=index,
    )
    if glyph is None:
        return element
    indent = f' style="padding-left:{px(paragraph.level * _INDENT_PX)}"' if paragraph.level else ""
    item_qid = registry.qids.claim(f"{item_hint}-{index}")
    return (
        f'<div class="cx-item" data-qid="{item_qid}" data-semantic-role="decoration"{indent}>'
        + render_marker(glyph, qids=registry.qids, font_px=font_px)
        + element
        + "</div>"
    )


__all__ = [
    "BindingRegistry",
    "QidFactory",
    "TextRenderError",
    "dominant_size_pt",
    "escape_attr",
    "escape_text",
    "marker_glyph",
    "open_tag",
    "projected_lines",
    "render_bound_text",
    "render_flow_paragraph",
    "render_marker",
]


def source_paragraph_style(paragraph: Paragraph, ctx, *, box_scale: float = 1.0) -> str:
    """Captured paragraph spacing in canvas units, reduced only with its box."""
    layout = paragraph.layout
    emu_px = ctx.deck.source_px_scale * ctx.params.page_scale / 9525.0
    pt_px = ctx.deck.source_px_scale * ctx.params.page_scale * 96.0 / 72.0
    values = ["margin:0", "line-height:1.0"]
    alignment = {"l": "left", "ctr": "center", "r": "right", "just": "justify"}.get(layout.get("alignment"))
    if alignment:
        values.append(f"text-align:{alignment}")
    for key, css in (("marginLeftEmu", "margin-left"), ("indentEmu", "text-indent")):
        if isinstance(layout.get(key), (int, float)):
            values.append(f"{css}:{px(layout[key] * emu_px)}")
    font_size = ctx.size("body", dominant_size_pt(paragraph, None), box_scale)
    ratio = float(layout.get("lineSpacingPct") or 100.0) / 100.0
    if ratio <= 0:
        raise TextRenderError(f"invalid source paragraph line spacing: {paragraph.source.block_id}")
    line_height = font_size * ratio
    if isinstance(layout.get("lineSpacingPt"), (int, float)):
        line_height = layout["lineSpacingPt"] * pt_px * box_scale
    # At entry, honor source spacing even below 100%. Only a measured shrink
    # reaching the font floor needs a corresponding non-collapsing line box.
    if box_scale < 1.0 and font_size <= (ctx.deck.font_floor_px or 7.0) + 1e-6:
        line_height = max(font_size, line_height)
    values.append(f"line-height:{px(line_height)}")
    for point_key, percent_key, css in (("spaceBeforePt", "spaceBeforePct", "margin-top"),
                                       ("spaceAfterPt", "spaceAfterPct", "margin-bottom")):
        if isinstance(layout.get(point_key), (int, float)):
            values.append(f"{css}:{px(layout[point_key] * pt_px * box_scale)}")
        elif isinstance(layout.get(percent_key), (int, float)):
            values.append(f"{css}:{px(line_height * layout[percent_key] / 100.0)}")
    return ";".join(values)


def source_run_html(paragraph: Paragraph, ctx, *, role: str, fallback_pt: float | None,
                    box_scale: float = 1.0) -> str:
    """One pass through original run order; no substring-based format recovery."""
    from services.composer.fonts import font_family_stack
    pieces: list[str] = []
    runs = paragraph.runs or [TextRun(text=paragraph.text)]
    if "".join(run.text for run in runs) != paragraph.text:
        raise TextRenderError(f"source run text mismatch: {paragraph.source.block_id}")
    for index, run in enumerate(runs):
        size = ctx.size(role, run.size_pt or fallback_pt, box_scale)
        family = font_family_stack(run.font_family or "")
        font_class = ctx.source_font_classes.setdefault(family, f"cx-source-font-{len(ctx.source_font_classes)}")
        style = f"font-size:{px(size)};font-weight:{700 if run.bold else 400}"
        if run.italic:
            style += ";font-style:italic"
        if run.color:
            style += f";color:{run.color}"
        pieces.append(f'<span class="{font_class}" data-qid-ignore="" data-source-run="{index}" style="{escape_attr(style)}">'
                      + escape_text(run.text).replace("\n", "<br>") + "</span>")
    return "".join(pieces)


def render_source_text(component, ctx, *, box_scale: float = 1.0) -> str:
    from services.composer.render_source_markers import render_source_marker
    counters: dict[int, int] = {}
    body = component.source_text_body
    scale = ctx.deck.source_px_scale * ctx.params.page_scale / 9525.0
    padding = [float(body.get(key) or 0) * scale for key in
               ("marginTopEmu", "marginRightEmu", "marginBottomEmu", "marginLeftEmu")]
    alignment = {"ctr": "center", "b": "flex-end"}.get(body.get("anchor"), "flex-start")
    style = "padding:" + " ".join(px(value) for value in padding) + f";justify-content:{alignment}"
    if body.get("wrap") in {"none", False}:
        style += ";white-space:pre"
    out = [open_tag("div", qid=ctx.qids.claim(component.component_id), role="decoration",
                    css="cx-component cx-source-text", style=style)]
    for index, paragraph in enumerate(component.paragraphs):
        if paragraph.layout.get("emptySourceParagraph"):
            size = ctx.size(component.semantic_role, component.source_size_pt, box_scale)
            content = escape_text(paragraph.text).replace("\n", "<br>") or "<br>"
            out.append(f'<p data-qid-ignore="" data-source-paragraph="{index}" style="font-size:{px(size)};line-height:1.0">{content}</p>')
            continue
        qid = ctx.registry.bind(paragraph.source.binding_id)
        size = ctx.size(component.semantic_role, dominant_size_pt(paragraph, component.source_size_pt), box_scale)
        para_style = f"font-size:{px(size)};" + source_paragraph_style(paragraph, ctx, box_scale=box_scale)
        marker = render_source_marker(paragraph, ctx, font_px=size, counters=counters)
        if marker is not None:
            # The marker has its own native identity; source readback owns only p.
            offset = float(paragraph.layout.get("indentEmu") or 0) * scale
            left_margin = paragraph.layout.get("marginLeftEmu")
            if left_margin is None:
                para_style += f";margin-left:{px(size * (1.25 + paragraph.level))}"
                offset = -size * 1.25
            para_style += ";text-indent:0"
            outer = para_style + ";position:relative;flex-shrink:0"
            out.append(open_tag("div", qid=ctx.qids.claim(paragraph.source.binding_id + "-list"),
                                role="decoration", style=escape_attr(outer)))
            out.append(f'<div data-qid-ignore="" style="position:absolute;left:{px(offset)};top:0;line-height:inherit">{marker}</div>')
            para_style = f"font-size:{px(size)};margin:0;text-indent:0;line-height:inherit"
        attrs = f'{source_block_data_attribute(paragraph.source.binding_id)} data-source-paragraph="{index}"'
        out.append(open_tag("p", qid=qid, role=component.semantic_role, css="cx-text",
                            style=escape_attr(para_style), extra=attrs))
        out.append(source_run_html(paragraph, ctx, role=component.semantic_role,
                                   fallback_pt=component.source_size_pt, box_scale=box_scale))
        out.append("</p>")
        if marker is not None:
            out.append("</div>")
    out.append("</div>")
    return "".join(out)
