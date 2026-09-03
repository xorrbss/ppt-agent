"""Native tables: ``<table data-native="table">`` with the table as the declared element.

Follows ``docs/authored-native-figure-contract-20260903.md``: ``data-qid`` on
the ``<table>`` only, rows and cells carry ``data-qid-ignore``, cells hold
text only, and the grid is rectangular.
"""

from __future__ import annotations

from services.composer.plan import ComposerComponent, Paragraph
from services.composer.render_context import RenderContext, component_role, size_role
from services.composer.render_css import px
from services.composer.render_text import escape_attr, escape_text
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
