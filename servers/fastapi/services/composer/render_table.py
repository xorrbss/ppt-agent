"""Native tables: ``<table data-native="table">`` with the table as the declared element.

Follows ``docs/authored-native-figure-contract-20260903.md``: ``data-qid`` on
the ``<table>`` only, rows and cells carry ``data-qid-ignore``, cells hold
text only, and the grid is rectangular.
"""

from __future__ import annotations

import re

from services.authored_vnext.source_render_geometry import text_advance_px

from services.composer.plan import ComposerComponent, Paragraph, TextRun
from services.composer.render_context import RenderContext, component_role, size_role
from services.composer.render_css import px
from services.composer.render_text import escape_attr, escape_text, TextRenderError, source_run_html, source_paragraph_style
from services.source_content.source_stamp_dom import source_block_data_attribute


def _cell_text(paragraph: Paragraph) -> str:
    return "<br>".join(escape_text(line) for line in paragraph.text.split("\n"))


def render_table(component: ComposerComponent, ctx: RenderContext, *, box_scale: float = 1.0) -> str:
    """Render one table component.

    When every cell comes from one source block (a manifest ``table`` block)
    the ``<table>`` carries that block's binding and QID and the cells are
    text only with ``data-qid-ignore``, as the native-figure contract asks.
    Cells assembled from several blocks are bound individually instead; the
    exporter then rasterizes that table but the readback still holds.
    """

    if ctx.deck.fit_policy_revision == "composer-source-fit/v2" or component.table_merged_cells or (ctx.params.source_geometry and component.table_column_widths_emu):
        return _render_source_table(component, ctx, box_scale=box_scale)
    rows = component.table_rows
    if not rows or not any(rows):
        return ""
    width = max(len(row) for row in rows)
    role = component_role(component)
    block_ids = {cell.source.block_id for row in rows for cell in row}
    table_block = next(iter(block_ids)) if len(block_ids) == 1 else None
    size = ctx.size(size_role(component, ctx.params), component.source_size_pt, box_scale)
    if table_block is not None:
        qid = ctx.registry.bind(table_block)
        attrs = (
            f'data-qid="{qid}" {source_block_data_attribute(table_block)} '
            f'data-semantic-role="{escape_attr(role)}"'
        )
    else:
        attrs = f'data-qid="{ctx.qids.claim(component.component_id)}" data-semantic-role="decoration"'
    out = [f'<table class="cx-component cx-table" data-native="table" {attrs} style="font-size:{px(size)}">']
    for row_index, row in enumerate(rows):
        tag = "th" if row_index == 0 else "td"
        if row_index == 0:
            out.append('<thead data-qid-ignore="">')
        elif row_index == 1:
            out.append('<tbody data-qid-ignore="">')
        out.append('<tr data-qid-ignore="">')
        for cell in row:
            if table_block is not None:
                out.append(f'<{tag} data-qid-ignore="">{_cell_text(cell)}</{tag}>')
            else:
                cell_qid = ctx.registry.bind(cell.source.binding_id)
                binding = source_block_data_attribute(cell.source.binding_id)
                out.append(
                    f'<{tag} data-qid="{cell_qid}" {binding} data-semantic-role="{escape_attr(role)}">'
                    f"{_cell_text(cell)}</{tag}>"
                )
        out.extend(f'<{tag} data-qid-ignore=""></{tag}>' for _ in range(width - len(row)))
        out.append("</tr>")
        if row_index == 0:
            out.append("</thead>")
    if len(rows) > 1:
        out.append("</tbody>")
    out.append("</table>")
    return "".join(out)


__all__ = ["render_table"]


def _numeric_table_widths(component: ComposerComponent, ctx: RenderContext, box_scale: float) -> list[float]:
    widths = component.table_column_widths_emu
    if ctx.params.source_geometry or not widths or sum(widths) <= 0:
        return widths
    available = float(ctx.geometry.content_width)
    base = [width / sum(widths) * available for width in widths]
    required = [0.0] * len(widths)
    for row in component.table_rows:
        for column, cell in enumerate(row[:len(widths)]):
            if not re.fullmatch(r"[0-9\s.,~:/()%+\-–—$€₩]+", cell.text.strip()):
                continue
            size = max((ctx.size("body", source_pt=run.size_pt, box_scale=box_scale)
                        for run in cell.runs), default=ctx.size("body", source_pt=None, box_scale=box_scale))
            required[column] = max(required[column], text_advance_px(cell.text.strip(), font_px=size) * 1.12 + 8.0)
    result = [max(width, minimum) for width, minimum in zip(base, required)]
    extra = sum(result) - available
    capacities = [max(0.0, width - max(minimum, width * 0.65, 32.0))
                  for width, minimum in zip(base, required)]
    capacity = sum(capacities)
    if extra > 0 and capacity > 0:
        for column, room in enumerate(capacities):
            result[column] -= min(extra, capacity) * room / capacity
    return result


def _source_cell_html(cell: Paragraph, ctx: RenderContext, *, box_scale: float) -> str:
    from services.composer.classify_common import formatted_runs
    meta = cell.layout.get("paragraphs") or []
    paragraphs = []
    for item in meta:
        runs = [TextRun(text=r.text, bold=r.bold, italic=r.italic, size_pt=r.size_pt,
                        color=r.color, font_family=r.font_family) for r in formatted_runs(item)]
        paragraphs.append(cell.model_copy(update={"text": str(item.get("text") or ""),
                                                  "runs": runs, "layout": item}))
    if not paragraphs:
        paragraphs = [cell]
    out = []
    for index, paragraph in enumerate(paragraphs):
        style = source_paragraph_style(paragraph, ctx, box_scale=box_scale)
        if (ctx.deck.fit_policy_revision == "composer-source-fit/v2"
                and re.fullmatch(r"[0-9\s.,~:/()%+\-–—$€₩]+", paragraph.text.strip())):
            style += ";white-space:nowrap"
        out.append(f'<p data-qid-ignore="" data-source-paragraph="{index}" style="{escape_attr(style)}">')
        out.append(source_run_html(paragraph, ctx, role="body", fallback_pt=None, box_scale=box_scale))
        out.append("</p>")
    return "".join(out)


def _render_source_table(component: ComposerComponent, ctx: RenderContext, *, box_scale: float) -> str:
    rows = component.table_rows
    if not rows:
        return ""
    width = max(map(len, rows))
    anchors: dict[tuple[int, int], tuple[int, int]] = {}
    covered: set[tuple[int, int]] = set()
    occupied: set[tuple[int, int]] = set()
    for merge in component.table_merged_cells:
        r, c = int(merge["row"]), int(merge["column"])
        rs, cs = int(merge["rowSpan"]), int(merge["columnSpan"])
        region = {(y, x) for y in range(r, r + rs) for x in range(c, c + cs)}
        if r < 0 or c < 0 or rs < 1 or cs < 1 or r + rs > len(rows) or c + cs > width or occupied & region:
            raise TextRenderError(f"source table merge invalid: {component.component_id}")
        occupied |= region
        anchors[(r, c)] = (rs, cs)
        covered |= region - {(r, c)}
    for r, c in covered:
        if c < len(rows[r]) and rows[r][c].text.strip():
            raise TextRenderError(f"source table covered cell has text: {component.component_id}:{r}:{c}")
    qid = ctx.registry.bind(component.component_id)
    attrs = source_block_data_attribute(component.component_id)
    size = ctx.size("body", component.source_size_pt, box_scale)
    out = [f'<table class="cx-component cx-table cx-source-table" data-native="table" data-qid="{qid}" '
           f'{attrs} data-semantic-role="body" style="font-size:{px(size)}">']
    widths = _numeric_table_widths(component, ctx, box_scale)
    if widths and len(widths) == width and sum(widths) > 0:
        out.append('<colgroup data-qid-ignore="">')
        out.extend(f'<col data-qid-ignore="" style="width:{value / sum(widths) * 100:.6f}%">' for value in widths)
        out.append('</colgroup>')
    out.append('<tbody data-qid-ignore="">')
    for r, row in enumerate(rows):
        height = component.table_row_heights_emu[r] * ctx.deck.source_px_scale / 9525.0 if r < len(component.table_row_heights_emu) else None
        row_style = f' style="height:{px(height)}"' if height is not None and ctx.params.source_geometry else ""
        out.append(f'<tr data-qid-ignore=""{row_style}>')
        for c in range(width):
            if (r, c) in covered:
                continue
            rs, cs = anchors.get((r, c), (1, 1))
            cell = row[c] if c < len(row) else None
            attrs = f'data-qid-ignore="" data-source-row="{r}" data-source-column="{c}" rowspan="{rs}" colspan="{cs}"'
            out.append(f'<td {attrs}>')
            if cell is not None:
                out.append(_source_cell_html(cell, ctx, box_scale=box_scale))
            out.append('</td>')
        out.append('</tr>')
    out.append('</tbody></table>')
    return "".join(out)
