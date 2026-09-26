"""Table cell pixel overlay: a structured table whose few unverified cells stay pixels.

An unverified cell line never carries its OCR reading as editable text. Before
this module any such line kept the whole table as source pixels. Now a table
stays native when its grid is confirmed by the pixels and at most half of its
filled cells are unverified; each unverified cell is published as ``""`` and
its pixels travel with the table as a crop of that cell (a *pixel cell*).

Plan level, a pixel cell is part of its table region (``PlannedRegion.pixel_cells``):
the table carries the cell's tokens, so no page repair can cut, move or merge
the crop on its own. From the sealed analysis on, the cell is a ``screenshot``
region linked by ``RasterRegion.table_pixel_cells``; the layout draws it only
inside its table and drops it with the table (``raster_redesign_fallback``).

Rules (the single place they are decided):

* the grid is confirmed when the cells' line boxes are ordered like the model's
  rows and columns (no column or row reaches into the next); the cell boxes are
  the bands between them;
* the whole table stays pixels when the grid is not confirmed or more than
  ``MAX_UNVERIFIED_SHARE`` of its filled cells are unverified;
* a crop is the cell's line box, padded, clipped to its cell box; it must lie
  inside the table box, show no word another carrier holds (the exactly-once
  word rule), and the table's cells must equal its remaining tokens; otherwise
  the whole table becomes pixels (``settle_pixel_cells``).
"""

from __future__ import annotations

import math
from dataclasses import replace

from services.source_content.raster_captures import Box, _union
from services.source_content.raster_page_plan_regions import PagePlan, PixelCell, PlannedRegion

MAX_UNVERIFIED_SHARE = 0.5
PAD = 3
GRID_TOLERANCE = 1.0
WORD_TOLERANCE = 3.0
OVERLAY_REVIEW = "table_cell_unverified"
PIXEL_TABLE_REVIEW = "table_cell_ocr_low_confidence"


def _bands(extents: list[tuple[float, float] | None], start: float, stop: float) -> list[float] | None:
    """Band edges between consecutive extents (midpoints), or None when they overlap."""

    if any(extent is None for extent in extents):
        return None
    edges = [start]
    for (_, end), (begin, _) in zip(extents, extents[1:], strict=False):
        if end > begin + GRID_TOLERANCE:
            return None
        edges.append((end + begin) / 2)
    edges.append(stop)
    return edges


def confirmed_cell_boxes(cell_boxes: list[list[list[Box]]], table: Box, width: int) -> list[list[Box]] | None:
    """Cell boxes when the line boxes confirm the row/column structure, else None.

    ``cell_boxes[r][c]`` are the line boxes of cell (r, c) (rows may be short)."""

    rows = []
    columns: list[list[Box]] = [[] for _ in range(width)]
    for row in cell_boxes:
        flat = [box for cell in row for box in cell]
        rows.append((min(b[1] for b in flat), max(b[1] + b[3] for b in flat)) if flat else None)
        for c, cell in enumerate(row):
            columns[c].extend(cell)
    column_extents = [(min(b[0] for b in col), max(b[0] + b[2] for b in col)) if col else None for col in columns]
    xs = _bands(column_extents, table[0], table[0] + table[2])
    ys = _bands(rows, table[1], table[1] + table[3])
    if xs is None or ys is None:
        return None
    return [[(xs[c], ys[r], xs[c + 1] - xs[c], ys[r + 1] - ys[r]) for c in range(width)] for r in range(len(rows))]


def _crop(lines: list[Box], cell: Box) -> Box | None:
    x, y, w, h = _union(lines)
    x0, y0 = max(math.floor(x - PAD), math.ceil(cell[0])), max(math.floor(y - PAD), math.ceil(cell[1]))
    x1 = min(math.ceil(x + w + PAD), math.floor(cell[0] + cell[2]))
    y1 = min(math.ceil(y + h + PAD), math.floor(cell[1] + cell[3]))
    return (x0, y0, x1 - x0, y1 - y0) if x1 > x0 and y1 > y0 else None


def overlay_cells(page, rows: list[list[list[int]]], texts, tokens, table: Box) -> tuple[PixelCell, ...] | None:
    """Pixel cells for the unverified cells of a structured table; None keeps it pixels.

    ``rows`` are cells of line indices, ``texts`` line -> text (None: unverified),
    ``tokens`` line -> carried token indices."""

    filled = [(r, c) for r, row in enumerate(rows) for c, cell in enumerate(row) if cell]
    unverified = [(r, c) for r, c in filled if any(texts.get(i) is None for i in rows[r][c])]
    if not unverified:
        return ()
    if len(unverified) > MAX_UNVERIFIED_SHARE * len(filled):
        return None
    width = max(len(row) for row in rows)
    line_boxes = [[[page.lines[i].bbox for i in cell] for cell in row] for row in rows]
    grid = confirmed_cell_boxes(line_boxes, table, width)
    if grid is None:
        return None
    cells = []
    for r, c in unverified:
        box = _crop(line_boxes[r][c], grid[r][c])
        if box is None:
            return None
        cells.append(PixelCell(r, c, box, tuple(t for i in rows[r][c] for t in tokens.get(i, ()))))
    return tuple(cells)


def blank_cells(cells: tuple[tuple[str, ...], ...], pixel_cells) -> tuple[tuple[str, ...], ...]:
    """Row-major cell text with every pixel cell published as ``""``."""

    blank = {(cell.row, cell.col) for cell in pixel_cells}
    return tuple(tuple("" if (r, c) in blank else value for c, value in enumerate(row)) for r, row in enumerate(cells))


def _native(region: PlannedRegion) -> bool:
    return region.role == "table" and region.table_cells is not None and not region.editable_fallback


def _inside(inner: Box, outer: Box, tolerance: float = 0.5) -> bool:
    return (
        inner[0] >= outer[0] - tolerance
        and inner[1] >= outer[1] - tolerance
        and inner[0] + inner[2] <= outer[0] + outer[2] + tolerance
        and inner[1] + inner[3] <= outer[1] + outer[3] + tolerance
    )


def _hits(a: Box, b: Box) -> bool:
    width = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    height = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return width >= WORD_TOLERANCE and height >= WORD_TOLERANCE


def _compact(value: str) -> str:
    return "".join(value.split())


def _holds(region: PlannedRegion, regions, words, unpromoted: set[int]) -> bool:
    cells = region.table_cells or ()
    positions = [(cell.row, cell.col) for cell in region.pixel_cells]
    if len(set(positions)) != len(positions):
        return False
    owned = [t for cell in region.pixel_cells for t in cell.word_indices]
    if len(set(owned)) != len(owned) or not set(owned) <= set(region.word_indices):
        return False
    for cell in region.pixel_cells:
        if cell.row >= len(cells) or cell.col >= len(cells[cell.row]) or cells[cell.row][cell.col]:
            return False
        if not _inside(cell.bbox, region.bbox):
            return False
    rest = [t for t in region.word_indices if t not in set(owned)]
    if _compact("".join(value for row in cells for value in row)) != "".join(_compact(words[t].text) for t in rest):
        return False
    for other in regions:
        for t in other.word_indices:
            if t in unpromoted or t >= len(words):
                continue
            for cell in region.pixel_cells:
                if t not in cell.word_indices and _hits(cell.bbox, words[t].bbox):
                    return False  # the crop would show a word another carrier holds
    return True


def as_pixel_table(region: PlannedRegion) -> PlannedRegion:
    """The whole table kept as source pixels (its cell text only as a suggestion)."""

    suggestion = "\n".join("\t".join(row) for row in region.table_cells) if region.table_cells else region.text
    return replace(region, text=suggestion, table_cells=None, editable_fallback=True, pixel_cells=(),
                   review_reason=PIXEL_TABLE_REVIEW)


def settle_pixel_cells(plan: PagePlan, words) -> PagePlan:
    """Enforce the pixel-cell rules on a finished plan (idempotent).

    A region that is no longer a native table drops its pixel cells (it already
    carries their tokens as pixels); a native table whose cells break a rule
    becomes pixels as a whole."""

    unpromoted = set(plan.unpromoted)
    regions = list(plan.regions)
    changed = False
    for n, region in enumerate(regions):
        if not region.pixel_cells:
            continue
        if not _native(region):
            regions[n] = replace(region, pixel_cells=())
        elif not _holds(region, regions, words, unpromoted):
            regions[n] = as_pixel_table(region)
        else:
            continue
        changed = True
    return replace(plan, regions=tuple(regions)) if changed else plan


def expand_overlays(regions, page_number: int, words) -> list[tuple[str, PlannedRegion, tuple, bool]]:
    """Sealed-analysis entries ``(region_id, planned, table_pixel_cells, is_cell)``.

    A native table with pixel cells is followed by one ``screenshot`` region per
    cell (``p<n>-r<i>-cell-r<row>c<col>``); the table keeps the other tokens."""

    entries = []
    for index, region in enumerate(regions, 1):
        region_id = f"p{page_number}-r{index}"
        if not region.pixel_cells or not _native(region):
            entries.append((region_id, region, (), False))
            continue
        owned = {t for cell in region.pixel_cells for t in cell.word_indices}
        table = replace(region, word_indices=tuple(t for t in region.word_indices if t not in owned))
        crops = []
        for cell in region.pixel_cells:
            crop_id = f"{region_id}-cell-r{cell.row}c{cell.col}"
            reading = " ".join(words[t].text for t in cell.word_indices if t < len(words)) or None
            crops.append((crop_id, PlannedRegion(
                "screenshot", cell.bbox, cell.word_indices, reading, confidence=region.confidence,
                review_reason=OVERLAY_REVIEW, reason="table_pixel_cell", editable_fallback=True,
                group_id=region.group_id,
            ), (), True))
        links = tuple((cell.row, cell.col, crop_id) for cell, (crop_id, *_) in zip(region.pixel_cells, crops,
                                                                                    strict=True))
        entries.append((region_id, table, links, False))
        entries.extend(crops)
    return entries


def overlay_links(regions) -> dict[str, str]:
    """Sealed regions: cell crop id -> id of the table that draws it."""

    return {crop: region.region_id for region in regions
            for _, _, crop in (getattr(region, "table_pixel_cells", None) or ())}


def overlay_pairs(regions) -> set[tuple[int, int]]:
    """(crop index, table index) pairs of linked sealed regions (exempt from the box rule)."""

    index = {region.region_id: n for n, region in enumerate(regions)}
    return {(index[crop], index[table]) for crop, table in overlay_links(regions).items() if crop in index}
