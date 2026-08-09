"""Native Template V2 table element and its cell model."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field, model_validator

from .base import (
    Fill,
    Font,
    HorizontalAlignment,
    Position,
    Size,
    TemplateV2Model,
    TextRun,
)


class TableCellReference(TemplateV2Model):
    """Zero-based absolute table coordinate, including the header row."""

    row: int = Field(ge=0, strict=True)
    column: int = Field(ge=0, strict=True)


class TableCell(TemplateV2Model):
    color: Optional[Fill] = None
    font: Optional[Font] = None
    alignment: Optional[HorizontalAlignment] = None
    runs: list[TextRun]
    rowspan: int = Field(default=1, ge=1, strict=True)
    colspan: int = Field(default=1, ge=1, strict=True)
    covered_by: Optional[TableCellReference] = None

    @model_validator(mode="after")
    def _covered_cells_cannot_start_spans(self) -> "TableCell":
        if self.covered_by is not None and (
            self.rowspan != 1 or self.colspan != 1
        ):
            raise ValueError("covered table cells cannot start spans")
        return self


class Table(TemplateV2Model):
    type: Literal["table"]
    position: Optional[Position] = None
    size: Optional[Size] = None
    rotation: Optional[float] = None
    columns: list[TableCell]
    rows: list[list[TableCell]]
    decorative: bool
    name: str
    max_columns: int
    min_columns: int
    max_rows: int
    min_rows: int

    @model_validator(mode="after")
    def _ranges_are_valid(self) -> "Table":
        if (
            self.min_columns < 0
            or self.max_columns < self.min_columns
            or self.min_rows < 0
            or self.max_rows < self.min_rows
        ):
            raise ValueError("table bounds are invalid")
        column_count = len(self.columns)
        if not self.min_columns <= column_count <= self.max_columns:
            raise ValueError("table column count is outside its declared bounds")
        if not self.min_rows <= len(self.rows) <= self.max_rows:
            raise ValueError("table row count is outside its declared bounds")
        if any(len(row) != column_count for row in self.rows):
            raise ValueError("table rows must match the declared column count")
        grid = [self.columns, *self.rows]
        occupied: dict[tuple[int, int], tuple[int, int]] = {}
        for row_index, row in enumerate(grid):
            for column_index, cell in enumerate(row):
                if cell.covered_by is not None:
                    continue
                end_row = row_index + cell.rowspan
                end_column = column_index + cell.colspan
                if end_row > len(grid) or end_column > column_count:
                    raise ValueError("table cell span is outside the table grid")
                anchor = (row_index, column_index)
                for covered_row in range(row_index, end_row):
                    for covered_column in range(column_index, end_column):
                        coordinate = (covered_row, covered_column)
                        prior = occupied.get(coordinate)
                        if prior is not None and prior != anchor:
                            raise ValueError("table cell spans overlap")
                        occupied[coordinate] = anchor
                        if coordinate == anchor:
                            continue
                        marker = grid[covered_row][covered_column].covered_by
                        if marker is None or (
                            marker.row,
                            marker.column,
                        ) != anchor:
                            raise ValueError(
                                "table covered cell must reference its span anchor"
                            )
        for row_index, row in enumerate(grid):
            for column_index, cell in enumerate(row):
                marker = cell.covered_by
                if marker is None:
                    continue
                coordinate = (row_index, column_index)
                if occupied.get(coordinate) != (marker.row, marker.column):
                    raise ValueError(
                        "table covered cell references a missing span anchor"
                    )
        return self
