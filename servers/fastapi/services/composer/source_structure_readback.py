"""Read back source images and native table structure from final Composer HTML.

The ordinary verbatim gate proves text blocks.  This companion gate covers the
source structures that text-only readback cannot prove: every image occurrence
must retain its source block identity, and every source table must retain its
logical grid, cell values, and merged ranges after covered cells are omitted
from the DOM.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from services.composer.receipt import ComposerAssertionError
from services.source_content.source_runs import normalize_source_text


READBACK_SCHEMA_VERSION = "composer-source-structure-readback/v1"
IMAGE_MISMATCH = "composer_source_image_occurrence_mismatch"
TABLE_MISMATCH = "composer_source_table_structure_mismatch"
CARDINALITY_MISMATCH = "composer_source_structure_cardinality_mismatch"


def _mapping(value: Any) -> Mapping[str, Any]:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        value = dump(mode="json", by_alias=True)
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    return list(value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else []


def _value(value: Mapping[str, Any], camel: str, snake: str, default: Any = None) -> Any:
    return value[camel] if camel in value else value.get(snake, default)


def _normalized(value: Any) -> str:
    return normalize_source_text(str(value or ""))


def _positive_int(value: Any, *, default: int | None = None) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return result if result >= 1 else default


def _nonnegative_int(value: Any, *, default: int | None = None) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return result if result >= 0 else default


@dataclass(frozen=True, slots=True)
class _Cell:
    row: int
    column: int
    row_span: int
    column_span: int
    text: str


@dataclass(slots=True)
class _OpenCell:
    row: int
    column: int
    row_span: int
    column_span: int
    text: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _Table:
    block_id: str
    cells: list[_Cell] = field(default_factory=list)
    malformed: bool = False


class _StructureParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.content_image_ids: list[str] = []
        self.glyph_image_count = 0
        self.tables: list[_Table] = []
        self._table: _Table | None = None
        self._cell: _OpenCell | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        source_id = str(attributes.get("data-source-block-id") or "").strip()
        if tag == "img":
            if source_id:
                self.content_image_ids.append(source_id)
            else:
                self.glyph_image_count += 1
            return
        if tag == "table" and source_id:
            if self._table is not None:
                self._table.malformed = True
            self._table = _Table(source_id)
            return
        if self._cell is not None and tag == "br":
            self._cell.text.append("\n")
            return
        if self._cell is not None and tag == "p" and self._cell.text:
            self._cell.text.append("\n")
        if tag not in {"td", "th"} or self._table is None:
            return
        if self._cell is not None:
            self._table.malformed = True
            return
        row = _nonnegative_int(attributes.get("data-source-row"))
        column = _nonnegative_int(attributes.get("data-source-column"))
        row_span = _positive_int(attributes.get("rowspan"), default=1)
        column_span = _positive_int(attributes.get("colspan"), default=1)
        if row is None or column is None or row_span is None or column_span is None:
            self._table.malformed = True
            return
        self._cell = _OpenCell(row, column, row_span, column_span)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._table is not None and self._cell is not None:
            cell = self._cell
            self._table.cells.append(
                _Cell(cell.row, cell.column, cell.row_span, cell.column_span, "".join(cell.text))
            )
            self._cell = None
        elif tag == "table" and self._table is not None:
            if self._cell is not None:
                self._table.malformed = True
                self._cell = None
            self.tables.append(self._table)
            self._table = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.text.append(data)

    def close(self) -> None:
        super().close()
        if self._table is not None:
            self._table.malformed = True
            self.tables.append(self._table)
            self._table = None


def _expected_rows(payload: Mapping[str, Any]) -> list[list[str]] | None:
    raw_rows = _sequence(payload.get("rows"))
    if raw_rows:
        rows: list[list[str]] = []
        for raw_row in raw_rows:
            row = _sequence(raw_row)
            if not row:
                return None
            rows.append([str(_mapping(cell).get("text") or "") for cell in row])
        return rows
    raw_cells = _sequence(payload.get("cells"))
    if not raw_cells:
        return None
    rows = [_sequence(row) for row in raw_cells]
    if any(not row for row in rows):
        return None
    return [[str(cell or "") for cell in row] for row in rows]


def _merge_tuple(value: Any) -> tuple[int, int, int, int] | None:
    item = _mapping(value)
    row = _nonnegative_int(_value(item, "row", "row"))
    column = _nonnegative_int(_value(item, "column", "column"))
    row_span = _positive_int(_value(item, "rowSpan", "row_span"))
    column_span = _positive_int(_value(item, "columnSpan", "column_span"))
    if row is None or column is None or row_span is None or column_span is None:
        return None
    if row_span == 1 and column_span == 1:
        return None
    return (row, column, row_span, column_span)


def _expected_table(payload: Mapping[str, Any]) -> tuple[list[list[str]], tuple[tuple[int, int, int, int], ...]] | None:
    rows = _expected_rows(payload)
    if not rows:
        return None
    width = len(rows[0])
    if width < 1 or any(len(row) != width for row in rows):
        return None
    merges: list[tuple[int, int, int, int]] = []
    occupied: set[tuple[int, int]] = set()
    for raw in _sequence(payload.get("mergedCells")):
        merge = _merge_tuple(raw)
        if merge is None:
            return None
        row, column, row_span, column_span = merge
        region = {
            (r, c)
            for r in range(row, row + row_span)
            for c in range(column, column + column_span)
        }
        if row + row_span > len(rows) or column + column_span > width or occupied & region:
            return None
        if any(_normalized(rows[r][c]) for r, c in region if (r, c) != (row, column)):
            return None
        occupied |= region
        merges.append(merge)
    return rows, tuple(sorted(merges))


def _actual_table(
    table: _Table, *, rows: int, columns: int
) -> tuple[list[list[str]], tuple[tuple[int, int, int, int], ...]] | None:
    if table.malformed:
        return None
    grid: list[list[str | None]] = [[None for _ in range(columns)] for _ in range(rows)]
    merges: list[tuple[int, int, int, int]] = []
    for cell in table.cells:
        if cell.row >= rows or cell.column >= columns:
            return None
        if cell.row + cell.row_span > rows or cell.column + cell.column_span > columns:
            return None
        merge = (cell.row, cell.column, cell.row_span, cell.column_span)
        if cell.row_span > 1 or cell.column_span > 1:
            merges.append(merge)
        for row in range(cell.row, cell.row + cell.row_span):
            for column in range(cell.column, cell.column + cell.column_span):
                if grid[row][column] is not None:
                    return None
                grid[row][column] = cell.text if (row, column) == (cell.row, cell.column) else ""
    if any(value is None for row in grid for value in row):
        return None
    return [[str(value) for value in row] for row in grid], tuple(sorted(merges))


def _fail(slide_index: int, code: str, category: str, block_id: str | None = None) -> None:
    detail = category if not block_id else f"{category}: {block_id}"
    raise ComposerAssertionError(slide_index, code, detail)


def assert_source_structure_readback(
    manifest: Any, htmls: Sequence[str]
) -> Mapping[str, Any]:
    """Verify source image occurrences and table structure in final v2 HTML.

    ``htmls`` is in source-slide order.  The returned mapping is safe to place
    in the Composer receipt; failures contain only a category and source block
    identity, never source cell text.
    """

    manifest_value = _mapping(manifest)
    units = sorted(
        (_mapping(unit) for unit in _sequence(manifest_value.get("units"))),
        key=lambda unit: int(_value(unit, "sourceIndex", "source_index", 0) or 0),
    )
    if not units or len(units) != len(htmls):
        _fail(0, CARDINALITY_MISMATCH, "source slide cardinality mismatch")

    slide_receipts: list[dict[str, int]] = []
    total_images = total_glyphs = total_tables = total_cells = total_merges = 0
    for output_index, (unit, html) in enumerate(zip(units, htmls, strict=True)):
        source_index = _nonnegative_int(_value(unit, "sourceIndex", "source_index"))
        if source_index is None:
            _fail(output_index, CARDINALITY_MISMATCH, "source slide index missing")
        blocks = [_mapping(block) for block in _sequence(unit.get("blocks"))]
        image_ids = [
            str(_value(block, "sourceBlockId", "source_block_id") or "")
            for block in blocks
            if str(block.get("kind") or "") == "image"
        ]
        expected_tables = {
            str(_value(block, "sourceBlockId", "source_block_id") or ""): _mapping(block.get("payload"))
            for block in blocks
            if str(block.get("kind") or "") == "table"
        }
        if any(not block_id for block_id in image_ids) or any(not block_id for block_id in expected_tables):
            _fail(output_index, CARDINALITY_MISMATCH, "source block identity missing")

        parser = _StructureParser()
        try:
            parser.feed(str(html))
            parser.close()
        except (ValueError, TypeError) as error:
            raise ComposerAssertionError(
                output_index, CARDINALITY_MISMATCH, "final HTML structure invalid"
            ) from error

        if parser.content_image_ids != image_ids:
            expected = set(image_ids)
            actual = set(parser.content_image_ids)
            block_id = next((value for value in image_ids if value not in actual), None)
            block_id = block_id or next((value for value in parser.content_image_ids if value not in expected), None)
            _fail(output_index, IMAGE_MISMATCH, "source image occurrence mismatch", block_id)

        actual_tables: dict[str, _Table] = {}
        for table in parser.tables:
            if table.block_id in actual_tables:
                _fail(output_index, TABLE_MISMATCH, "duplicate source table", table.block_id)
            actual_tables[table.block_id] = table
        if set(actual_tables) != set(expected_tables):
            block_id = next((value for value in expected_tables if value not in actual_tables), None)
            block_id = block_id or next((value for value in actual_tables if value not in expected_tables), None)
            _fail(output_index, TABLE_MISMATCH, "source table occurrence mismatch", block_id)

        slide_cells = slide_merges = 0
        for block_id, payload in expected_tables.items():
            expected = _expected_table(payload)
            if expected is None:
                _fail(output_index, TABLE_MISMATCH, "source table manifest invalid", block_id)
            expected_rows, expected_merges = expected
            actual = _actual_table(
                actual_tables[block_id], rows=len(expected_rows), columns=len(expected_rows[0])
            )
            if actual is None:
                _fail(output_index, TABLE_MISMATCH, "rendered table grid invalid", block_id)
            actual_rows, actual_merges = actual
            if actual_merges != expected_merges:
                _fail(output_index, TABLE_MISMATCH, "source table merge mismatch", block_id)
            if [list(map(_normalized, row)) for row in actual_rows] != [
                list(map(_normalized, row)) for row in expected_rows
            ]:
                _fail(output_index, TABLE_MISMATCH, "source table cell mismatch", block_id)
            slide_cells += sum(len(row) for row in expected_rows)
            slide_merges += len(expected_merges)

        slide_receipts.append(
            {
                "slideIndex": output_index,
                "sourceSlideIndex": source_index,
                "contentImageOccurrences": len(image_ids),
                "glyphImages": parser.glyph_image_count,
                "tables": len(expected_tables),
                "logicalTableCells": slide_cells,
                "mergedCells": slide_merges,
            }
        )
        total_images += len(image_ids)
        total_glyphs += parser.glyph_image_count
        total_tables += len(expected_tables)
        total_cells += slide_cells
        total_merges += slide_merges

    counts = _mapping(manifest_value.get("counts"))
    expected_images = _nonnegative_int(_value(counts, "imageOccurrences", "image_occurrences"))
    expected_cells = _nonnegative_int(_value(counts, "tableCells", "table_cells"))
    if expected_images is None or expected_images != total_images:
        _fail(0, IMAGE_MISMATCH, "manifest image occurrence total mismatch")
    if expected_cells is None or expected_cells != total_cells:
        _fail(0, TABLE_MISMATCH, "manifest logical table cell total mismatch")

    return {
        "schemaVersion": READBACK_SCHEMA_VERSION,
        "passed": True,
        "slides": slide_receipts,
        "totals": {
            "contentImageOccurrences": total_images,
            "glyphImages": total_glyphs,
            "tables": total_tables,
            "logicalTableCells": total_cells,
            "mergedCells": total_merges,
        },
    }


__all__ = ["assert_source_structure_readback"]
