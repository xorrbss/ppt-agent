"""Detect ruled tables, choose the best cell grid, and judge whether a grid
reading is dense enough to publish as structured cells.
"""

from __future__ import annotations

from services.source_content.raster_captures import Box, PageContext, _center_in
from services.source_content.raster_page_plan_regions import PlannedRegion
from services.source_content.raster_page_plan_text import _text_confidence
from services.source_content.raster_tables import TableCells, TableGrid, assign_table_cells, detect_ruled_tables


def _tables(page: PageContext, screenshots: list[Box], claimed: set[int]) -> list[PlannedRegion]:
    result = []
    for detected in detect_ruled_tables(page.image, screenshots):
        grid, inside, cells = _best_grid(page, detected, claimed)
        if not inside:
            continue
        structured = cells.cells is not None and _dense_enough(cells.cells)
        if not structured and not grid.ruled_columns:
            continue  # stacked cards or fields, not a table: leave the text editable
        order = (
            tuple(i for row in cells.cell_words for cell in row for i in cell)
            if structured
            else tuple(sorted(inside, key=lambda i: (inside[i].bbox[1], inside[i].bbox[0])))
        )
        claimed.update(order)
        confidence, review, suggestion = 0.7, None, None
        if structured:
            line_map = {i: line for line in page.lines for i in line.word_indices if i in inside}
            unique = [(list(line.word_indices), line) for line in {id(v): v for v in line_map.values()}.values()]
            confidence, _ = _text_confidence(page, unique)
            if any(_text_confidence(page, [item])[1] for item in unique):
                # A doubtful cell reading keeps the whole table as source pixels.
                review, structured = "table_cell_ocr_low_confidence", False
                suggestion = "\n".join("\t".join(row) for row in cells.cells)
        result.append(
            PlannedRegion(
                "table",
                grid.bbox,
                order,
                text=suggestion,
                table_cells=cells.cells if structured else None,
                confidence=confidence,
                review_reason=review,
                reason=cells.reason,
                editable_fallback=review is not None,
            )
        )
    return result


def _best_grid(page: PageContext, grid: TableGrid, claimed: set[int]):
    """Try the detected grid, then without an outer band (a card header or
    footer framed by the same rules), and keep the first clean structure."""

    attempts = []
    edges = grid.row_edges
    for top, bottom in ((0, 0), (1, 0), (0, 1), (1, 1)):
        rows = edges[top : len(edges) - bottom]
        if len(rows) < 3:
            continue
        x0, _, width, _ = grid.bbox
        candidate = TableGrid((x0, rows[0], width, rows[-1] - rows[0]), rows, grid.column_edges, grid.ruled_columns)
        inside = {
            i: page.words[i]
            for i in range(len(page.words))
            if i not in claimed and _center_in(page.words[i].bbox, candidate.bbox)
        }
        cells = assign_table_cells(
            candidate,
            {i: w.bbox for i, w in inside.items()},
            {i: w.text for i, w in inside.items()},
            {i: w.space_before for i, w in inside.items()},
        )
        attempts.append((candidate, inside, cells))
        if cells.cells is not None and _dense_enough(cells.cells):
            return candidate, inside, cells
    return attempts[0] if attempts else (grid, {}, TableCells(None, (), "table_grid_too_small"))


def _dense_enough(cells: tuple[tuple[str, ...], ...]) -> bool:
    rows_with_text = sum(1 for row in cells if sum(1 for cell in row if cell) >= 2)
    filled = sum(1 for row in cells for cell in row if cell)
    return rows_with_text >= max(2, 0.6 * len(cells)) and filled >= 0.5 * len(cells) * len(cells[0])
