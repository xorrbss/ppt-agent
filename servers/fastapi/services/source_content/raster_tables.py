"""Ruled-table detection and cell assignment for raster slide redesign.

A table is promoted to editable cells only when its grid is corroborated by
pixel rules and every OCR word falls cleanly inside one cell. Anything less
leaves ``cells`` as ``None`` so the caller keeps the table as an image crop.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

EDGE_LEVEL = 10
MERGE_PX = 3


@dataclass(frozen=True)
class TableGrid:
    bbox: tuple[int, int, int, int]
    row_edges: tuple[int, ...]
    column_edges: tuple[int, ...]
    ruled_columns: bool


@dataclass(frozen=True)
class TableCells:
    cells: tuple[tuple[str, ...], ...] | None
    # Word indices per cell (row-major); empty when cells is None.
    cell_words: tuple[tuple[tuple[int, ...], ...], ...]
    reason: str


def _gray(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L"), dtype=np.int16)


def _merge_positions(values: list[int]) -> list[int]:
    merged: list[list[int]] = []
    for value in sorted(values):
        if merged and value - merged[-1][-1] <= MERGE_PX:
            merged[-1].append(value)
        else:
            merged.append([value])
    return [round(sum(group) / len(group)) for group in merged]


def _runs(line: np.ndarray, bridge: int) -> list[tuple[int, int]]:
    padded = np.concatenate(([0], line.astype(np.int8), [0]))
    changes = np.flatnonzero(np.diff(padded))
    runs: list[tuple[int, int]] = []
    for start, end in zip(changes[::2], changes[1::2], strict=False):
        # Crossing column rules interrupt a row rule; bridge such short gaps.
        if runs and start - runs[-1][1] <= bridge:
            runs[-1] = (runs[-1][0], int(end))
        else:
            runs.append((int(start), int(end)))
    return runs


def _span_overlap(a: tuple[int, int], b: tuple[int, int]) -> float:
    inter = min(a[1], b[1]) - max(a[0], b[0])
    return max(0, inter) / max(1, min(a[1] - a[0], b[1] - b[0]))


def _horizontal_rules(gray: np.ndarray, min_length: int) -> list[tuple[int, int, int]]:
    """Return (y, x0, x1) for long horizontal intensity steps or thin rules."""

    edges = np.abs(gray[2:, :] - gray[:-2, :]) >= EDGE_LEVEL
    rules: list[tuple[int, int, int]] = []
    for row in np.flatnonzero(edges.sum(axis=1) >= min_length):
        for start, end in _runs(edges[row], 12):
            if end - start >= min_length:
                rules.append((int(row) + 1, start, end))
    # Collapse the adjacent edge rows produced by one thin rule or band edge.
    collapsed: list[tuple[int, int, int]] = []
    for rule in sorted(rules):
        match = next(
            (
                index
                for index in range(len(collapsed) - 1, -1, -1)
                if rule[0] - collapsed[index][0] <= MERGE_PX
                and _span_overlap((rule[1], rule[2]), (collapsed[index][1], collapsed[index][2])) >= 0.7
            ),
            None,
        )
        if match is None:
            collapsed.append(rule)
        else:
            previous = collapsed[match]
            collapsed[match] = (previous[0], min(previous[1], rule[1]), max(previous[2], rule[2]))
    return sorted(collapsed)


def _split_irregular(group: list[tuple[int, int, int]]) -> list[list[tuple[int, int, int]]]:
    """Split a rule stack where a thin margin or an outsized band interrupts it."""

    pieces: list[list[tuple[int, int, int]]] = [[group[0]]]
    for rule in group[1:]:
        if rule[0] - pieces[-1][-1][0] < 18:
            pieces.append([rule])
        else:
            pieces[-1].append(rule)
    result: list[list[tuple[int, int, int]]] = []
    for piece in pieces:
        while len(piece) >= 4:
            spacings = [b[0] - a[0] for a, b in zip(piece, piece[1:], strict=False)]
            median = sorted(spacings)[len(spacings) // 2]
            worst = max(range(len(spacings)), key=lambda index: spacings[index])
            if spacings[worst] <= 2.5 * median:
                break
            left, right = piece[: worst + 1], piece[worst + 1 :]
            piece = left if len(left) >= len(right) else right
        # Outer rules that only close a thin panel margin are not table rows.
        while len(piece) >= 3:
            spacings = sorted(b[0] - a[0] for a, b in zip(piece, piece[1:], strict=False))
            median = spacings[len(spacings) // 2]
            if piece[1][0] - piece[0][0] < 0.45 * median:
                piece = piece[1:]
            elif piece[-1][0] - piece[-2][0] < 0.45 * median:
                piece = piece[:-1]
            else:
                break
        result.append(piece)
    return result


def detect_ruled_tables(image: Image.Image, excluded: list[tuple[int, int, int, int]]) -> tuple[TableGrid, ...]:
    """Find grids of at least three aligned horizontal rules outside ``excluded``."""

    gray = _gray(image)
    height, width = gray.shape
    rules = _horizontal_rules(gray, max(160, width // 9))
    groups: list[list[tuple[int, int, int]]] = []
    for rule in rules:
        target = next(
            (
                group
                for group in groups
                if _span_overlap((group[-1][1], group[-1][2]), (rule[1], rule[2])) >= 0.8
                and max(group[-1][2] - group[-1][1], rule[2] - rule[1])
                <= 1.35 * min(group[-1][2] - group[-1][1], rule[2] - rule[1])
                and 12 <= rule[0] - group[-1][0] <= 130
            ),
            None,
        )
        if target is None:
            groups.append([rule])
        else:
            target.append(rule)
    grids: list[TableGrid] = []
    for group in (piece for stack in groups for piece in _split_irregular(stack)):
        spacings = [b[0] - a[0] for a, b in zip(group, group[1:], strict=False)]
        # A table needs at least three bands of comparable height; stacked
        # cards or form fields rarely satisfy that.
        if len(spacings) < 3 or max(spacings) > 2.5 * min(spacings):
            continue
        x0 = min(rule[1] for rule in group)
        x1 = max(rule[2] for rule in group)
        y0, y1 = group[0][0], group[-1][0]
        box = (x0, y0, x1 - x0, y1 - y0)
        if any(_contains_ratio(box, other) > 0.3 for other in excluded):
            continue
        column_edges = _vertical_rules(gray, box)
        grids.append(TableGrid(box, tuple(rule[0] for rule in group), tuple(column_edges), bool(column_edges)))
    grids.sort(key=lambda grid: grid.bbox[2] * grid.bbox[3], reverse=True)
    distinct: list[TableGrid] = []
    for grid in grids:
        if not any(_contains_ratio(grid.bbox, kept.bbox) > 0.6 for kept in distinct):
            distinct.append(grid)
    return tuple(sorted(distinct, key=lambda grid: (grid.bbox[1], grid.bbox[0])))


def _vertical_rules(gray: np.ndarray, box: tuple[int, int, int, int]) -> list[int]:
    x0, y0, width, height = box
    region = gray[y0 : y0 + height, max(0, x0 - 1) : x0 + width + 1]
    if region.shape[1] < 5 or height < 10:
        return []
    edges = np.abs(region[:, 2:] - region[:, :-2]) >= EDGE_LEVEL
    coverage = edges.mean(axis=0)
    columns = [int(index) + max(0, x0 - 1) + 1 for index in np.flatnonzero(coverage >= 0.6)]
    inner = [x for x in _merge_positions(columns) if x0 + 10 < x < x0 + width - 10]
    return inner


def _contains_ratio(inner: tuple[int, int, int, int], outer: tuple[int, int, int, int]) -> float:
    ix, iy, iw, ih = inner
    ox, oy, ow, oh = outer
    inter = max(0, min(ix + iw, ox + ow) - max(ix, ox)) * max(0, min(iy + ih, oy + oh) - max(iy, oy))
    return inter / max(1, iw * ih)


def _text_gap_columns(grid: TableGrid, boxes: list[tuple[int, int, int, int]]) -> list[int]:
    """Column separators from x-gaps no word crosses (borderless columns)."""

    x0, _, width, _ = grid.bbox
    occupied = np.zeros(width, dtype=bool)
    for bx, _, bw, _ in boxes:
        occupied[max(0, bx - x0) : max(0, min(width, bx + bw - x0))] = True
    separators: list[int] = []
    start = None
    for offset, filled in enumerate(occupied):
        if not filled and start is None:
            start = offset
        elif filled and start is not None:
            if offset - start >= 20 and start > 0:
                separators.append(x0 + (start + offset) // 2)
            start = None
    return separators


def assign_table_cells(
    grid: TableGrid,
    word_boxes: dict[int, tuple[int, int, int, int]],
    word_texts: dict[int, str],
    word_spaces: dict[int, bool],
) -> TableCells:
    """Place each word in exactly one cell or give up on cell structure."""

    columns = list(grid.column_edges) or _text_gap_columns(grid, list(word_boxes.values()))
    x0, y0, width, height = grid.bbox
    col_bounds = [x0, *columns, x0 + width]
    rows = [edge for edge in grid.row_edges]
    row_bounds = [(top, bottom) for top, bottom in zip(rows, rows[1:], strict=False) if bottom - top >= 14]
    if len(row_bounds) < 2 or len(col_bounds) < 3:
        return TableCells(None, (), "table_grid_too_small")
    placement: dict[tuple[int, int], list[int]] = {}
    for index, (bx, by, bw, bh) in word_boxes.items():
        cx, cy = bx + bw / 2, by + bh / 2
        row = next((r for r, (top, bottom) in enumerate(row_bounds) if top <= cy < bottom), None)
        col = next((c for c in range(len(col_bounds) - 1) if col_bounds[c] <= cx < col_bounds[c + 1]), None)
        if row is None or col is None:
            return TableCells(None, (), "table_word_outside_grid")
        if bx < col_bounds[col] - 3 or bx + bw > col_bounds[col + 1] + 3:
            return TableCells(None, (), "table_word_crosses_column")
        placement.setdefault((row, col), []).append(index)
    filled_rows = {row for row, _ in placement}
    filled_cols = {col for _, col in placement}
    if len(filled_rows) < 2 or len(filled_cols) < 2:
        return TableCells(None, (), "table_sparse_cells")
    cells: list[tuple[str, ...]] = []
    cell_words: list[tuple[tuple[int, ...], ...]] = []
    for row in range(len(row_bounds)):
        texts: list[str] = []
        words_row: list[tuple[int, ...]] = []
        for col in range(len(col_bounds) - 1):
            indices = _reading_sorted(placement.get((row, col), []), word_boxes)
            texts.append(_cell_text(indices, word_boxes, word_texts, word_spaces))
            words_row.append(tuple(indices))
        cells.append(tuple(texts))
        cell_words.append(tuple(words_row))
    return TableCells(
        tuple(cells), tuple(cell_words), "ruled_grid" if grid.ruled_columns else "ruled_rows_text_columns"
    )


def _reading_sorted(indices: list[int], boxes: dict[int, tuple[int, int, int, int]]) -> list[int]:
    ordered = sorted(indices, key=lambda index: (boxes[index][1], boxes[index][0]))
    lines: list[list[int]] = []
    for index in ordered:
        box = boxes[index]
        if lines:
            anchor = boxes[lines[-1][0]]
            if abs((box[1] + box[3] / 2) - (anchor[1] + anchor[3] / 2)) < max(box[3], anchor[3]) * 0.6:
                lines[-1].append(index)
                continue
        lines.append([index])
    return [index for line in lines for index in sorted(line, key=lambda item: boxes[item][0])]


def _cell_text(
    indices: list[int], boxes: dict[int, tuple[int, int, int, int]], texts: dict[int, str], spaces: dict[int, bool]
) -> str:
    parts: list[str] = []
    previous = None
    for index in indices:
        if previous is not None:
            same_line = abs(boxes[index][1] - boxes[previous][1]) < max(boxes[index][3], boxes[previous][3]) * 0.6
            if not same_line:
                parts.append("\n")
            elif spaces.get(index, True):
                parts.append(" ")
        parts.append(texts[index])
        previous = index
    return "".join(parts)
