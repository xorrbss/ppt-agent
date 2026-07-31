import {
  validateChartContract,
  validateTableContract,
  type JsonRecord,
  type TemplateV2ContractResult,
} from "./template-v2-chart-table-contract.ts";
import { createTemplateV2SlideRenderPlan } from "./template-v2-render-plan.mjs";

const CSV_INPUT_LIMIT = 100_000;
const CSV_CELL_LIMIT = 4_000;

export type TemplateV2StructuralEdit =
  | {
      kind: "chart-series-reorder";
      fromIndex: number;
      toIndex: number;
    }
  | {
      kind: "table-row-reorder";
      fromIndex: number;
      toIndex: number;
    }
  | {
      kind: "table-column-reorder";
      fromIndex: number;
      toIndex: number;
    }
  | { kind: "table-transpose" }
  | { kind: "table-csv-import"; csv: string }
  | {
      kind: "table-to-chart";
      chartType?: "bar";
      title?: string;
    }
  | {
      /**
       * SmartArt is currently imported as inert manual-review evidence. This
       * operation exists only so callers receive a stable fail-closed result.
       */
      kind: "smartart-node-reorder";
      fromIndex: number;
      toIndex: number;
    };

function rejected<T>(reason: string): TemplateV2ContractResult<T> {
  return { ok: false, reason };
}

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isPlainRecord(value: unknown): value is JsonRecord {
  if (!isRecord(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function hasExactKeys(
  value: JsonRecord,
  expected: readonly string[],
): boolean {
  const keys = Reflect.ownKeys(value);
  return (
    keys.length === expected.length &&
    keys.every(
      (key) => typeof key === "string" && expected.includes(key),
    )
  );
}

function validateStructuralEditPayload(
  value: unknown,
): TemplateV2ContractResult<TemplateV2StructuralEdit> {
  try {
    if (!isPlainRecord(value)) {
      return rejected("structural_edit_payload_invalid");
    }
    if (typeof value.kind !== "string") {
      return rejected("structural_edit_payload_invalid");
    }

    switch (value.kind) {
      case "chart-series-reorder":
      case "table-row-reorder":
      case "table-column-reorder":
      case "smartart-node-reorder":
        if (
          !hasExactKeys(value, ["kind", "fromIndex", "toIndex"]) ||
          typeof value.fromIndex !== "number" ||
          typeof value.toIndex !== "number"
        ) {
          return rejected("structural_edit_payload_invalid");
        }
        return {
          ok: true,
          value: {
            kind: value.kind,
            fromIndex: value.fromIndex,
            toIndex: value.toIndex,
          },
        };
      case "table-transpose":
        return hasExactKeys(value, ["kind"])
          ? { ok: true, value: { kind: value.kind } }
          : rejected("structural_edit_payload_invalid");
      case "table-csv-import":
        return hasExactKeys(value, ["kind", "csv"]) &&
          typeof value.csv === "string"
          ? { ok: true, value: { kind: value.kind, csv: value.csv } }
          : rejected("structural_edit_payload_invalid");
      case "table-to-chart": {
        const hasChartType = Object.prototype.hasOwnProperty.call(
          value,
          "chartType",
        );
        const hasTitle = Object.prototype.hasOwnProperty.call(value, "title");
        const expected = [
          "kind",
          ...(hasChartType ? ["chartType"] : []),
          ...(hasTitle ? ["title"] : []),
        ];
        if (
          !hasExactKeys(value, expected) ||
          (hasChartType && typeof value.chartType !== "string") ||
          (hasTitle && typeof value.title !== "string")
        ) {
          return rejected("structural_edit_payload_invalid");
        }
        if (hasChartType && value.chartType !== "bar") {
          return rejected("table_to_chart_type_unproven");
        }
        return {
          ok: true,
          value: {
            kind: value.kind,
            ...(hasChartType ? { chartType: "bar" as const } : {}),
            ...(hasTitle ? { title: value.title as string } : {}),
          },
        };
      }
      default:
        return rejected("unsupported_structural_edit");
    }
  } catch {
    return rejected("structural_edit_payload_invalid");
  }
}

function moved<T>(values: readonly T[], from: number, to: number): T[] | null {
  if (
    !Number.isSafeInteger(from) ||
    !Number.isSafeInteger(to) ||
    from < 0 ||
    to < 0 ||
    from >= values.length ||
    to >= values.length ||
    from === to
  ) {
    return null;
  }
  const result = values.slice();
  const value = result[from]!;
  result.splice(from, 1);
  result.splice(to, 0, value);
  return result;
}

function textOfCell(cell: JsonRecord): string {
  if (!Array.isArray(cell.runs)) return "";
  return cell.runs
    .map((run) =>
      isRecord(run) && typeof run.text === "string" ? run.text : "",
    )
    .join("");
}

function cellWithText(cell: JsonRecord, text: string): JsonRecord {
  const next = structuredClone(cell);
  if (!Array.isArray(next.runs) || next.runs.length === 0) {
    next.runs = [{ text }];
    return next;
  }
  next.runs = next.runs.map((run, index) =>
    isRecord(run)
      ? { ...run, text: index === 0 ? text : "" }
      : { text: index === 0 ? text : "" },
  );
  return next;
}

function parseCsv(
  input: string,
): TemplateV2ContractResult<string[][]> {
  if (input.length === 0) return rejected("table_csv_empty");
  if (input.length > CSV_INPUT_LIMIT) {
    return rejected("table_csv_input_too_large");
  }

  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  let closedQuote = false;
  let endedWithLineBreak = false;

  const pushCell = () => {
    if (cell.length > CSV_CELL_LIMIT) return false;
    row.push(cell);
    cell = "";
    closedQuote = false;
    return true;
  };
  const pushRow = () => {
    rows.push(row);
    row = [];
  };

  for (let index = 0; index < input.length; index += 1) {
    const character = input[index];
    endedWithLineBreak = false;

    if (quoted) {
      if (character === '"') {
        if (input[index + 1] === '"') {
          cell += '"';
          index += 1;
        } else {
          quoted = false;
          closedQuote = true;
        }
      } else {
        cell += character;
        if (cell.length > CSV_CELL_LIMIT) {
          return rejected("table_csv_cell_too_large");
        }
      }
      continue;
    }

    if (closedQuote && character !== "," && character !== "\r" && character !== "\n") {
      return rejected("table_csv_malformed_quotes");
    }
    if (character === '"') {
      if (cell.length !== 0 || closedQuote) {
        return rejected("table_csv_malformed_quotes");
      }
      quoted = true;
    } else if (character === ",") {
      if (!pushCell()) return rejected("table_csv_cell_too_large");
    } else if (character === "\r" || character === "\n") {
      if (character === "\r" && input[index + 1] === "\n") index += 1;
      if (!pushCell()) return rejected("table_csv_cell_too_large");
      pushRow();
      endedWithLineBreak = true;
    } else {
      cell += character;
      if (cell.length > CSV_CELL_LIMIT) {
        return rejected("table_csv_cell_too_large");
      }
    }
  }

  if (quoted) return rejected("table_csv_malformed_quotes");
  if (!endedWithLineBreak || cell.length > 0 || row.length > 0) {
    if (!pushCell()) return rejected("table_csv_cell_too_large");
    pushRow();
  }
  if (rows.length === 0) return rejected("table_csv_empty");
  const width = rows[0]?.length ?? 0;
  if (width === 0 || rows.some((candidate) => candidate.length !== width)) {
    return rejected("table_csv_ragged_rows");
  }
  return { ok: true, value: rows };
}

function reorderChartSeries(
  element: unknown,
  edit: Extract<
    TemplateV2StructuralEdit,
    { kind: "chart-series-reorder" }
  >,
): TemplateV2ContractResult<JsonRecord> {
  const validated = validateChartContract(element);
  if (!validated.ok) return validated;
  if (!Array.isArray(validated.value.series)) {
    return rejected("chart_series_reorder_requires_series");
  }
  const series = moved(
    validated.value.series,
    edit.fromIndex,
    edit.toIndex,
  );
  if (!series) return rejected("chart_series_reorder_invalid_index");
  const candidate = { ...validated.value, series };
  return validateChartContract(candidate);
}

function reorderTableRows(
  element: unknown,
  edit: Extract<
    TemplateV2StructuralEdit,
    { kind: "table-row-reorder" }
  >,
): TemplateV2ContractResult<JsonRecord> {
  const validated = validateTableContract(element);
  if (!validated.ok) return validated;
  const rows = moved(
    validated.value.rows as JsonRecord[][],
    edit.fromIndex,
    edit.toIndex,
  );
  if (!rows) return rejected("table_row_reorder_invalid_index");
  return validateTableContract({ ...validated.value, rows });
}

function reorderTableColumns(
  element: unknown,
  edit: Extract<
    TemplateV2StructuralEdit,
    { kind: "table-column-reorder" }
  >,
): TemplateV2ContractResult<JsonRecord> {
  const validated = validateTableContract(element);
  if (!validated.ok) return validated;
  const columns = moved(
    validated.value.columns as JsonRecord[],
    edit.fromIndex,
    edit.toIndex,
  );
  if (!columns) return rejected("table_column_reorder_invalid_index");
  const rows = (validated.value.rows as JsonRecord[][]).map((row) =>
    moved(row, edit.fromIndex, edit.toIndex),
  );
  if (rows.some((row) => row === null)) {
    return rejected("table_not_rectangular");
  }
  return validateTableContract({
    ...validated.value,
    columns,
    rows: rows as JsonRecord[][],
  });
}

function transposeTable(
  element: unknown,
): TemplateV2ContractResult<JsonRecord> {
  const validated = validateTableContract(element);
  if (!validated.ok) return validated;
  const columns = validated.value.columns as JsonRecord[];
  const rows = validated.value.rows as JsonRecord[][];
  if (columns.length === 0) return rejected("table_transpose_requires_columns");
  const matrix = [columns, ...rows];
  const transposed = Array.from({ length: columns.length }, (_, columnIndex) =>
    matrix.map((row) => row[columnIndex]),
  );
  return validateTableContract({
    ...validated.value,
    columns: transposed[0],
    rows: transposed.slice(1),
  });
}

function importTableCsv(
  element: unknown,
  csv: string,
): TemplateV2ContractResult<JsonRecord> {
  const validated = validateTableContract(element);
  if (!validated.ok) return validated;
  const parsed = parseCsv(csv);
  if (!parsed.ok) return parsed;
  const columns = validated.value.columns as JsonRecord[];
  const rows = validated.value.rows as JsonRecord[][];
  if (
    parsed.value.length !== rows.length + 1 ||
    parsed.value[0]?.length !== columns.length
  ) {
    return rejected("table_csv_shape_change_unproven");
  }
  const nextColumns = columns.map((cell, columnIndex) =>
    cellWithText(cell, parsed.value[0]![columnIndex]!),
  );
  const nextRows = rows.map((row, rowIndex) =>
    row.map((cell, columnIndex) =>
      cellWithText(cell, parsed.value[rowIndex + 1]![columnIndex]!),
    ),
  );
  return validateTableContract({
    ...validated.value,
    columns: nextColumns,
    rows: nextRows,
  });
}

function tableToChart(
  element: unknown,
  edit: Extract<TemplateV2StructuralEdit, { kind: "table-to-chart" }>,
): TemplateV2ContractResult<JsonRecord> {
  const validated = validateTableContract(element);
  if (!validated.ok) return validated;
  const provenTableFields = new Set([
    "type",
    "position",
    "size",
    "rotation",
    "columns",
    "rows",
    "decorative",
    "name",
    "min_columns",
    "max_columns",
    "min_rows",
    "max_rows",
  ]);
  const cells = [
    ...(validated.value.columns as JsonRecord[]),
    ...(validated.value.rows as JsonRecord[][]).flat(),
  ];
  const hasUnprovenMetadata =
    Object.keys(validated.value).some((field) => !provenTableFields.has(field)) ||
    cells.some(
      (cell) =>
        Object.keys(cell).some((field) => field !== "runs") ||
        !Array.isArray(cell.runs) ||
        cell.runs.some(
          (run) =>
            !isRecord(run) ||
            Object.keys(run).some((field) => field !== "text"),
        ),
    );
  if (hasUnprovenMetadata) {
    return rejected("table_to_chart_unproven_metadata");
  }
  const columns = validated.value.columns as JsonRecord[];
  const rows = validated.value.rows as JsonRecord[][];
  if (columns.length < 2 || rows.length === 0) {
    return rejected("table_to_chart_requires_data");
  }
  const chartType = edit.chartType ?? "bar";
  if (chartType !== "bar") {
    return rejected("table_to_chart_type_unproven");
  }

  const categories = rows.map((row) => textOfCell(row[0]!));
  const series: JsonRecord[] = [];
  for (let columnIndex = 1; columnIndex < columns.length; columnIndex += 1) {
    const values: number[] = [];
    for (let rowIndex = 0; rowIndex < rows.length; rowIndex += 1) {
      const source = textOfCell(rows[rowIndex]![columnIndex]!).trim();
      const numeric = Number(source);
      if (source.length === 0 || !Number.isFinite(numeric)) {
        return rejected("table_to_chart_non_numeric_value");
      }
      values.push(numeric);
    }
    series.push({
      name: textOfCell(columns[columnIndex]!),
      values,
    });
  }

  const table = validated.value;
  const chart: JsonRecord = {
    type: "chart",
    chart_type: chartType,
    categories,
    series,
  };
  for (const field of [
    "position",
    "size",
    "rotation",
    "decorative",
    "name",
  ] as const) {
    if (table[field] !== undefined) chart[field] = structuredClone(table[field]);
  }
  if (edit.title !== undefined) chart.title = edit.title;
  return validateChartContract(chart);
}

function validateStructuralEditResult(
  element: JsonRecord,
): TemplateV2ContractResult<JsonRecord> {
  // These fields are required by the pinned upstream Table and Chart models.
  // The narrower chart/table edit contracts intentionally retain unknown
  // extension fields, so enforce the shared required model fields here.
  if (
    typeof element.decorative !== "boolean" ||
    typeof element.name !== "string"
  ) {
    return rejected("structural_edit_upstream_metadata_invalid");
  }

  // Structural edits can preserve known fields that their focused contracts do
  // not interpret (for example chart title/axis options and frame rotation).
  // Preflight the result through the same runtime plan used by HTML rendering so
  // an edit cannot report success and then fail at the render boundary.
  try {
    createTemplateV2SlideRenderPlan({
      ui: {
        components: [
          {
            id: "structural-edit-validation",
            position: { x: 0, y: 0 },
            elements: [element],
          },
        ],
      },
    });
  } catch {
    return rejected("structural_edit_result_not_renderable");
  }
  return { ok: true, value: element };
}

export function applyTemplateV2StructuralEdit(
  element: unknown,
  edit: unknown,
): TemplateV2ContractResult<JsonRecord> {
  const validatedEdit = validateStructuralEditPayload(edit);
  if (!validatedEdit.ok) return validatedEdit;

  const result: TemplateV2ContractResult<JsonRecord> = (() => {
    switch (validatedEdit.value.kind) {
      case "chart-series-reorder":
        return reorderChartSeries(element, validatedEdit.value);
      case "table-row-reorder":
        return reorderTableRows(element, validatedEdit.value);
      case "table-column-reorder":
        return reorderTableColumns(element, validatedEdit.value);
      case "table-transpose":
        return transposeTable(element);
      case "table-csv-import":
        return importTableCsv(element, validatedEdit.value.csv);
      case "table-to-chart":
        return tableToChart(element, validatedEdit.value);
      case "smartart-node-reorder":
        return rejected("smartart_edit_unproven");
    }
  })();
  return result.ok ? validateStructuralEditResult(result.value) : result;
}
