/**
 * Bounded, lossless table editing contracts for Template V2.
 *
 * This module deliberately does not persist anything. A successful `apply`
 * returns one replacement element which the Studio reducer can commit through
 * its existing history, journal, autosave and revision-CAS path.
 */

export type TemplateV2TableRecord = Record<string, unknown>;

export const TEMPLATE_V2_TABLE_HARD_LIMITS = Object.freeze({
  maxRows: 200,
  maxColumns: 32,
  maxCells: 4096,
  maxCellCharacters: 4000,
  maxInputCharacters: 1_000_000,
});

export type TemplateV2TableReasonCode =
  | "table_invalid_contract"
  | "table_operation_invalid_index"
  | "table_operation_no_change"
  | "table_min_rows_exceeded"
  | "table_max_rows_exceeded"
  | "table_min_columns_exceeded"
  | "table_max_columns_exceeded"
  | "table_max_cells_exceeded"
  | "table_import_empty"
  | "table_import_too_large"
  | "table_import_malformed_quotes"
  | "table_import_ragged_rows"
  | "table_import_cell_too_large"
  | "table_paste_out_of_bounds"
  | "table_preview_stale"
  | "table_preview_tampered"
  | "table_chart_requires_data"
  | "table_chart_non_numeric_value"
  | "table_chart_incompatible_type";

export type TemplateV2TableOperation =
  | { type: "insert-row"; index: number; values?: string[] }
  | { type: "delete-row"; index: number }
  | { type: "move-row"; from: number; to: number }
  | {
      type: "insert-column";
      index: number;
      header?: string;
      values?: string[];
    }
  | { type: "delete-column"; index: number }
  | { type: "move-column"; from: number; to: number }
  | { type: "promote-first-row-to-header" }
  | { type: "demote-header-to-first-row" }
  | { type: "transpose" };

export interface TemplateV2TableDiff {
  operation: string;
  beforeRows: number;
  afterRows: number;
  beforeColumns: number;
  afterColumns: number;
  changedCells: number;
}

export interface TemplateV2TablePreview {
  kind: "template-v2-table-preview";
  beforeDigest: string;
  afterDigest: string;
  before: TemplateV2TableRecord;
  after: TemplateV2TableRecord;
  diff: TemplateV2TableDiff;
}

export interface TemplateV2TableChartPreview {
  kind: "template-v2-table-chart-preview";
  beforeDigest: string;
  afterDigest: string;
  before: TemplateV2TableRecord;
  chart: TemplateV2TableRecord;
  diff: {
    operation: "table-to-chart";
    chartType: "bar" | "line" | "area" | "pie" | "donut";
    categories: number;
    series: number;
  };
}

export type TemplateV2TableResult<T> =
  | { ok: true; value: T }
  | {
      ok: false;
      reasonCode: TemplateV2TableReasonCode;
      path?: string;
      limit?: number;
    };

export interface TemplateV2DelimitedOptions {
  delimiter?: "," | "\t" | "auto";
  firstRowIsHeader?: boolean;
}

export interface TemplateV2TablePasteOptions {
  delimiter?: "," | "\t" | "auto";
  startRow: number;
  startColumn: number;
  includeHeader?: boolean;
}

export interface TemplateV2LongTableSplitSuggestion {
  kind: "template-v2-long-table-split-suggestion";
  reasonCode: "table_rows_exceed_readable_page";
  totalRows: number;
  maxRowsPerSlide: number;
  repeatHeader: true;
  segments: Array<{
    index: number;
    startRow: number;
    endRowExclusive: number;
  }>;
}

type TableShape = {
  table: TemplateV2TableRecord;
  columns: TemplateV2TableRecord[];
  rows: TemplateV2TableRecord[][];
  minRows: number;
  maxRows: number;
  minColumns: number;
  maxColumns: number;
};

function isRecord(value: unknown): value is TemplateV2TableRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNonNegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && (value as number) >= 0;
}

function clone<T>(value: T): T {
  return structuredClone(value);
}

function digest(value: unknown): string {
  const input = JSON.stringify(value);
  let hash = 0x811c9dc5;
  for (let index = 0; index < input.length; index += 1) {
    hash ^= input.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return `fnv1a32:${(hash >>> 0).toString(16).padStart(8, "0")}`;
}

function rejected<T>(
  reasonCode: TemplateV2TableReasonCode,
  details: { path?: string; limit?: number } = {},
): TemplateV2TableResult<T> {
  return { ok: false, reasonCode, ...details };
}

function inspectTable(
  table: TemplateV2TableRecord,
): TemplateV2TableResult<TableShape> {
  const columns = table.columns;
  const rows = table.rows;
  if (
    table.type !== "table" ||
    !Array.isArray(columns) ||
    !Array.isArray(rows) ||
    !columns.every(isRecord) ||
    !rows.every(
      (row) =>
        Array.isArray(row) &&
        row.length === columns.length &&
        row.every(isRecord),
    )
  ) {
    return rejected("table_invalid_contract");
  }
  const declaredMinRows = isNonNegativeInteger(table.min_rows)
    ? table.min_rows
    : 0;
  const declaredMaxRows = isNonNegativeInteger(table.max_rows)
    ? table.max_rows
    : TEMPLATE_V2_TABLE_HARD_LIMITS.maxRows;
  const declaredMinColumns = isNonNegativeInteger(table.min_columns)
    ? table.min_columns
    : 0;
  const declaredMaxColumns = isNonNegativeInteger(table.max_columns)
    ? table.max_columns
    : TEMPLATE_V2_TABLE_HARD_LIMITS.maxColumns;
  const maxRows = Math.min(
    declaredMaxRows,
    TEMPLATE_V2_TABLE_HARD_LIMITS.maxRows,
  );
  const maxColumns = Math.min(
    declaredMaxColumns,
    TEMPLATE_V2_TABLE_HARD_LIMITS.maxColumns,
  );
  if (
    declaredMinRows > maxRows ||
    declaredMinColumns > maxColumns ||
    rows.length < declaredMinRows ||
    rows.length > maxRows ||
    columns.length < declaredMinColumns ||
    columns.length > maxColumns ||
    rows.length * columns.length >
      TEMPLATE_V2_TABLE_HARD_LIMITS.maxCells
  ) {
    return rejected("table_invalid_contract");
  }
  return {
    ok: true,
    value: {
      table,
      columns: columns as TemplateV2TableRecord[],
      rows: rows as TemplateV2TableRecord[][],
      minRows: declaredMinRows,
      maxRows,
      minColumns: declaredMinColumns,
      maxColumns,
    },
  };
}

function textOfCell(cell: TemplateV2TableRecord): string {
  if (!Array.isArray(cell.runs)) return "";
  return cell.runs
    .map((run) => (isRecord(run) && typeof run.text === "string" ? run.text : ""))
    .join("");
}

function cellWithText(
  cell: TemplateV2TableRecord | undefined,
  text: string,
): TemplateV2TableRecord {
  const base = cell ? clone(cell) : {};
  if (!Array.isArray(base.runs) || base.runs.length === 0) {
    return { ...base, runs: [{ text }] };
  }
  const runs = base.runs.map((run, index) =>
    isRecord(run)
      ? { ...run, text: index === 0 ? text : "" }
      : { text: index === 0 ? text : "" },
  );
  return { ...base, runs };
}

function validateDimensions(
  shape: TableShape,
  rowCount: number,
  columnCount: number,
): TemplateV2TableResult<true> {
  if (rowCount < shape.minRows) return rejected("table_min_rows_exceeded");
  if (rowCount > shape.maxRows) {
    return rejected("table_max_rows_exceeded", { limit: shape.maxRows });
  }
  if (columnCount < shape.minColumns) {
    return rejected("table_min_columns_exceeded");
  }
  if (columnCount > shape.maxColumns) {
    return rejected("table_max_columns_exceeded", {
      limit: shape.maxColumns,
    });
  }
  if (
    rowCount * columnCount >
    TEMPLATE_V2_TABLE_HARD_LIMITS.maxCells
  ) {
    return rejected("table_max_cells_exceeded", {
      limit: TEMPLATE_V2_TABLE_HARD_LIMITS.maxCells,
    });
  }
  return { ok: true, value: true };
}

function move<T>(items: T[], from: number, to: number): T[] | null {
  if (
    !Number.isSafeInteger(from) ||
    !Number.isSafeInteger(to) ||
    from < 0 ||
    to < 0 ||
    from >= items.length ||
    to >= items.length ||
    from === to
  ) {
    return null;
  }
  const next = items.slice();
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

function changedCellCount(
  before: TableShape,
  after: TemplateV2TableRecord,
): number {
  const nextColumns = after.columns as TemplateV2TableRecord[];
  const nextRows = after.rows as TemplateV2TableRecord[][];
  const rowCount = Math.max(before.rows.length + 1, nextRows.length + 1);
  const columnCount = Math.max(before.columns.length, nextColumns.length);
  let changed = 0;
  for (let rowIndex = -1; rowIndex < rowCount - 1; rowIndex += 1) {
    for (let columnIndex = 0; columnIndex < columnCount; columnIndex += 1) {
      const left =
        rowIndex === -1
          ? before.columns[columnIndex]
          : before.rows[rowIndex]?.[columnIndex];
      const right =
        rowIndex === -1
          ? nextColumns[columnIndex]
          : nextRows[rowIndex]?.[columnIndex];
      if (JSON.stringify(left) !== JSON.stringify(right)) changed += 1;
    }
  }
  return changed;
}

function makePreview(
  shape: TableShape,
  operation: string,
  after: TemplateV2TableRecord,
): TemplateV2TableResult<TemplateV2TablePreview> {
  if (JSON.stringify(shape.table) === JSON.stringify(after)) {
    return rejected("table_operation_no_change");
  }
  return {
    ok: true,
    value: {
      kind: "template-v2-table-preview",
      beforeDigest: digest(shape.table),
      afterDigest: digest(after),
      before: clone(shape.table),
      after: clone(after),
      diff: {
        operation,
        beforeRows: shape.rows.length,
        afterRows: (after.rows as unknown[]).length,
        beforeColumns: shape.columns.length,
        afterColumns: (after.columns as unknown[]).length,
        changedCells: changedCellCount(shape, after),
      },
    },
  };
}

export function previewTemplateV2TableOperation(
  table: TemplateV2TableRecord,
  operation: TemplateV2TableOperation,
): TemplateV2TableResult<TemplateV2TablePreview> {
  const inspected = inspectTable(table);
  if (!inspected.ok) return inspected;
  const shape = inspected.value;
  let columns = shape.columns.slice();
  let rows = shape.rows.map((row) => row.slice());

  if (operation.type === "insert-row") {
    if (
      !Number.isSafeInteger(operation.index) ||
      operation.index < 0 ||
      operation.index > rows.length ||
      (operation.values && operation.values.length > columns.length)
    ) {
      return rejected("table_operation_invalid_index");
    }
    const dimensions = validateDimensions(
      shape,
      rows.length + 1,
      columns.length,
    );
    if (!dimensions.ok) return dimensions;
    const prototypeRow =
      rows[Math.min(operation.index, Math.max(0, rows.length - 1))] ?? [];
    const inserted = columns.map((header, columnIndex) =>
      cellWithText(
        prototypeRow[columnIndex] ?? header,
        operation.values?.[columnIndex] ?? "",
      ),
    );
    rows.splice(operation.index, 0, inserted);
  } else if (operation.type === "delete-row") {
    if (
      !Number.isSafeInteger(operation.index) ||
      operation.index < 0 ||
      operation.index >= rows.length
    ) {
      return rejected("table_operation_invalid_index");
    }
    const dimensions = validateDimensions(
      shape,
      rows.length - 1,
      columns.length,
    );
    if (!dimensions.ok) return dimensions;
    rows.splice(operation.index, 1);
  } else if (operation.type === "move-row") {
    const moved = move(rows, operation.from, operation.to);
    if (!moved) return rejected("table_operation_invalid_index");
    rows = moved;
  } else if (operation.type === "insert-column") {
    if (
      !Number.isSafeInteger(operation.index) ||
      operation.index < 0 ||
      operation.index > columns.length ||
      (operation.values && operation.values.length > rows.length)
    ) {
      return rejected("table_operation_invalid_index");
    }
    const dimensions = validateDimensions(
      shape,
      rows.length,
      columns.length + 1,
    );
    if (!dimensions.ok) return dimensions;
    const prototypeColumn = Math.min(
      operation.index,
      Math.max(0, columns.length - 1),
    );
    columns.splice(
      operation.index,
      0,
      cellWithText(columns[prototypeColumn], operation.header ?? ""),
    );
    rows = rows.map((row, rowIndex) => {
      const next = row.slice();
      next.splice(
        operation.index,
        0,
        cellWithText(
          row[prototypeColumn],
          operation.values?.[rowIndex] ?? "",
        ),
      );
      return next;
    });
  } else if (operation.type === "delete-column") {
    if (
      !Number.isSafeInteger(operation.index) ||
      operation.index < 0 ||
      operation.index >= columns.length
    ) {
      return rejected("table_operation_invalid_index");
    }
    const dimensions = validateDimensions(
      shape,
      rows.length,
      columns.length - 1,
    );
    if (!dimensions.ok) return dimensions;
    columns.splice(operation.index, 1);
    rows = rows.map((row) => {
      const next = row.slice();
      next.splice(operation.index, 1);
      return next;
    });
  } else if (operation.type === "move-column") {
    const movedColumns = move(columns, operation.from, operation.to);
    if (!movedColumns) return rejected("table_operation_invalid_index");
    const movedRows = rows.map((row) => move(row, operation.from, operation.to));
    if (movedRows.some((row) => row === null)) {
      return rejected("table_invalid_contract");
    }
    columns = movedColumns;
    rows = movedRows as TemplateV2TableRecord[][];
  } else if (operation.type === "promote-first-row-to-header") {
    if (rows.length === 0) return rejected("table_operation_invalid_index");
    const first = rows[0];
    rows = [columns, ...rows.slice(1)];
    columns = first;
  } else if (operation.type === "demote-header-to-first-row") {
    const dimensions = validateDimensions(
      shape,
      rows.length + 1,
      columns.length,
    );
    if (!dimensions.ok) return dimensions;
    const blankHeader = columns.map((cell) => cellWithText(cell, ""));
    rows = [columns, ...rows];
    columns = blankHeader;
  } else {
    const matrix = [columns, ...rows];
    const nextRowCount = columns.length - 1;
    const nextColumnCount = matrix.length;
    const dimensions = validateDimensions(
      shape,
      nextRowCount,
      nextColumnCount,
    );
    if (!dimensions.ok) return dimensions;
    const transposed = Array.from(
      { length: shape.columns.length },
      (_, columnIndex) => matrix.map((row) => row[columnIndex]),
    );
    columns = transposed[0];
    rows = transposed.slice(1);
  }

  return makePreview(shape, operation.type, { ...table, columns, rows });
}

export function applyTemplateV2TablePreview(
  current: TemplateV2TableRecord,
  preview: TemplateV2TablePreview,
): TemplateV2TableResult<TemplateV2TableRecord> {
  if (
    preview.kind !== "template-v2-table-preview" ||
    digest(preview.before) !== preview.beforeDigest ||
    digest(preview.after) !== preview.afterDigest
  ) {
    return rejected("table_preview_tampered");
  }
  if (
    digest(current) !== preview.beforeDigest ||
    JSON.stringify(current) !== JSON.stringify(preview.before)
  ) {
    return rejected("table_preview_stale");
  }
  const inspected = inspectTable(preview.after);
  if (!inspected.ok) return inspected;
  return { ok: true, value: clone(preview.after) };
}

function parseDelimited(
  input: string,
  delimiterOption: "," | "\t" | "auto" = "auto",
): TemplateV2TableResult<{ delimiter: "," | "\t"; rows: string[][] }> {
  if (input.length === 0) return rejected("table_import_empty");
  if (input.length > TEMPLATE_V2_TABLE_HARD_LIMITS.maxInputCharacters) {
    return rejected("table_import_too_large", {
      limit: TEMPLATE_V2_TABLE_HARD_LIMITS.maxInputCharacters,
    });
  }
  const firstLine = input.split(/\r?\n/, 1)[0];
  const delimiter =
    delimiterOption === "auto"
      ? firstLine.includes("\t")
        ? "\t"
        : ","
      : delimiterOption;
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  let quoteClosed = false;

  const pushCell = () => {
    row.push(cell);
    cell = "";
    quoteClosed = false;
  };
  const pushRow = () => {
    pushCell();
    rows.push(row);
    row = [];
  };

  for (let index = 0; index < input.length; index += 1) {
    const character = input[index];
    if (quoted) {
      if (character === '"') {
        if (input[index + 1] === '"') {
          cell += '"';
          index += 1;
        } else {
          quoted = false;
          quoteClosed = true;
        }
      } else {
        cell += character;
      }
      continue;
    }
    if (character === '"' && cell.length === 0 && !quoteClosed) {
      quoted = true;
    } else if (character === delimiter) {
      pushCell();
    } else if (character === "\n" || character === "\r") {
      if (character === "\r" && input[index + 1] === "\n") index += 1;
      pushRow();
    } else if (quoteClosed) {
      return rejected("table_import_malformed_quotes");
    } else {
      cell += character;
    }
  }
  if (quoted) return rejected("table_import_malformed_quotes");
  if (cell.length > 0 || row.length > 0 || !/[\r\n]$/.test(input)) pushRow();
  if (rows.length === 0 || rows.every((item) => item.every((value) => !value))) {
    return rejected("table_import_empty");
  }
  const width = rows[0].length;
  if (rows.some((item) => item.length !== width)) {
    return rejected("table_import_ragged_rows");
  }
  for (let rowIndex = 0; rowIndex < rows.length; rowIndex += 1) {
    for (
      let columnIndex = 0;
      columnIndex < rows[rowIndex].length;
      columnIndex += 1
    ) {
      if (
        rows[rowIndex][columnIndex].length >
        TEMPLATE_V2_TABLE_HARD_LIMITS.maxCellCharacters
      ) {
        return rejected("table_import_cell_too_large", {
          path: `rows.${rowIndex}.${columnIndex}`,
          limit: TEMPLATE_V2_TABLE_HARD_LIMITS.maxCellCharacters,
        });
      }
    }
  }
  return { ok: true, value: { delimiter, rows } };
}

export function previewTemplateV2DelimitedImport(
  table: TemplateV2TableRecord,
  input: string,
  options: TemplateV2DelimitedOptions = {},
): TemplateV2TableResult<TemplateV2TablePreview> {
  const inspected = inspectTable(table);
  if (!inspected.ok) return inspected;
  const parsed = parseDelimited(input, options.delimiter);
  if (!parsed.ok) return parsed;
  const shape = inspected.value;
  const firstRowIsHeader = options.firstRowIsHeader ?? true;
  const sourceRows = parsed.value.rows;
  const columnCount = sourceRows[0].length;
  const rowValues = firstRowIsHeader ? sourceRows.slice(1) : sourceRows;
  const dimensions = validateDimensions(shape, rowValues.length, columnCount);
  if (!dimensions.ok) return dimensions;

  const headerValues = firstRowIsHeader
    ? sourceRows[0]
    : Array.from({ length: columnCount }, (_, index) =>
        textOfCell(shape.columns[index] ?? {}) || `Column ${index + 1}`,
      );
  const columns = headerValues.map((text, columnIndex) =>
    cellWithText(
      shape.columns[columnIndex] ??
        shape.columns[Math.max(0, shape.columns.length - 1)],
      text,
    ),
  );
  const rows = rowValues.map((values, rowIndex) =>
    values.map((text, columnIndex) =>
      cellWithText(
        shape.rows[rowIndex]?.[columnIndex] ??
          shape.rows[Math.max(0, shape.rows.length - 1)]?.[columnIndex] ??
          shape.columns[columnIndex],
        text,
      ),
    ),
  );
  return makePreview(shape, `import-${parsed.value.delimiter === "\t" ? "tsv" : "csv"}`, {
    ...table,
    columns,
    rows,
  });
}

export function previewTemplateV2DelimitedPaste(
  table: TemplateV2TableRecord,
  input: string,
  options: TemplateV2TablePasteOptions,
): TemplateV2TableResult<TemplateV2TablePreview> {
  const inspected = inspectTable(table);
  if (!inspected.ok) return inspected;
  const parsed = parseDelimited(input, options.delimiter);
  if (!parsed.ok) return parsed;
  if (
    !Number.isSafeInteger(options.startRow) ||
    !Number.isSafeInteger(options.startColumn) ||
    options.startColumn < 0 ||
    options.startRow < (options.includeHeader ? -1 : 0)
  ) {
    return rejected("table_operation_invalid_index");
  }
  const shape = inspected.value;
  const matrix = [shape.columns.slice(), ...shape.rows.map((row) => row.slice())];
  const matrixStartRow = options.startRow + 1;
  const pasteRows = parsed.value.rows;
  if (
    matrixStartRow < 0 ||
    matrixStartRow + pasteRows.length > matrix.length ||
    options.startColumn + pasteRows[0].length > shape.columns.length
  ) {
    return rejected("table_paste_out_of_bounds");
  }
  pasteRows.forEach((values, rowOffset) => {
    values.forEach((text, columnOffset) => {
      const rowIndex = matrixStartRow + rowOffset;
      const columnIndex = options.startColumn + columnOffset;
      matrix[rowIndex][columnIndex] = cellWithText(
        matrix[rowIndex][columnIndex],
        text,
      );
    });
  });
  return makePreview(shape, "paste-delimited", {
    ...table,
    columns: matrix[0],
    rows: matrix.slice(1),
  });
}

export function previewTemplateV2TableToChart(
  table: TemplateV2TableRecord,
  options: {
    chartType?: "bar" | "line" | "area" | "pie" | "donut";
    title?: string;
  } = {},
): TemplateV2TableResult<TemplateV2TableChartPreview> {
  const inspected = inspectTable(table);
  if (!inspected.ok) return inspected;
  const shape = inspected.value;
  if (shape.columns.length < 2 || shape.rows.length < 1) {
    return rejected("table_chart_requires_data");
  }
  const chartType = options.chartType ?? "bar";
  const seriesColumns =
    chartType === "pie" || chartType === "donut"
      ? shape.columns.slice(1, 2)
      : shape.columns.slice(1);
  if (
    (chartType === "pie" || chartType === "donut") &&
    shape.columns.length !== 2
  ) {
    return rejected("table_chart_incompatible_type");
  }
  const series = seriesColumns.map((column, seriesIndex) => {
    const values: number[] = [];
    for (let rowIndex = 0; rowIndex < shape.rows.length; rowIndex += 1) {
      const raw = textOfCell(shape.rows[rowIndex][seriesIndex + 1]).trim();
      const value = Number(raw);
      if (!raw || !Number.isFinite(value)) {
        return {
          error: rejected<never>("table_chart_non_numeric_value", {
            path: `rows.${rowIndex}.${seriesIndex + 1}`,
          }),
        };
      }
      values.push(value);
    }
    return {
      value: {
        name: textOfCell(column) || `Series ${seriesIndex + 1}`,
        values,
      },
    };
  });
  const error = series.find(
    (item): item is { error: TemplateV2TableResult<never> } => "error" in item,
  );
  if (error) return error.error;

  const chart: TemplateV2TableRecord = {
    type: "chart",
    chart_type: chartType,
    categories: shape.rows.map((row) => textOfCell(row[0])),
    series: series.map((item) => ("value" in item ? item.value : null)),
    decorative: table.decorative === true,
    name:
      typeof table.name === "string" && table.name
        ? `${table.name} chart`
        : "Table chart",
    ...(isRecord(table.position) ? { position: clone(table.position) } : {}),
    ...(isRecord(table.size) ? { size: clone(table.size) } : {}),
    ...(typeof table.rotation === "number"
      ? { rotation: table.rotation }
      : {}),
    ...(options.title?.trim() ? { title: options.title.trim() } : {}),
  };
  return {
    ok: true,
    value: {
      kind: "template-v2-table-chart-preview",
      beforeDigest: digest(table),
      afterDigest: digest(chart),
      before: clone(table),
      chart: clone(chart),
      diff: {
        operation: "table-to-chart",
        chartType,
        categories: shape.rows.length,
        series: series.length,
      },
    },
  };
}

export function applyTemplateV2TableToChartPreview(
  current: TemplateV2TableRecord,
  preview: TemplateV2TableChartPreview,
): TemplateV2TableResult<TemplateV2TableRecord> {
  if (
    preview.kind !== "template-v2-table-chart-preview" ||
    digest(preview.before) !== preview.beforeDigest ||
    digest(preview.chart) !== preview.afterDigest
  ) {
    return rejected("table_preview_tampered");
  }
  if (
    digest(current) !== preview.beforeDigest ||
    JSON.stringify(current) !== JSON.stringify(preview.before)
  ) {
    return rejected("table_preview_stale");
  }
  return { ok: true, value: clone(preview.chart) };
}

export function suggestTemplateV2LongTableSplit(
  table: TemplateV2TableRecord,
  maxRowsPerSlide = 12,
): TemplateV2TableResult<TemplateV2LongTableSplitSuggestion | null> {
  const inspected = inspectTable(table);
  if (!inspected.ok) return inspected;
  if (
    !Number.isSafeInteger(maxRowsPerSlide) ||
    maxRowsPerSlide < 1 ||
    maxRowsPerSlide > TEMPLATE_V2_TABLE_HARD_LIMITS.maxRows
  ) {
    return rejected("table_operation_invalid_index");
  }
  const totalRows = inspected.value.rows.length;
  if (totalRows <= maxRowsPerSlide) return { ok: true, value: null };
  const segments = [];
  for (let startRow = 0; startRow < totalRows; startRow += maxRowsPerSlide) {
    segments.push({
      index: segments.length,
      startRow,
      endRowExclusive: Math.min(totalRows, startRow + maxRowsPerSlide),
    });
  }
  return {
    ok: true,
    value: {
      kind: "template-v2-long-table-split-suggestion",
      reasonCode: "table_rows_exceed_readable_page",
      totalRows,
      maxRowsPerSlide,
      repeatHeader: true,
      segments,
    },
  };
}
