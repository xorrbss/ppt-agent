import {
  parseChartType,
  TEMPLATE_V2_AXIS_CHART_TYPES,
  TEMPLATE_V2_DATA_LABEL_POSITIONS,
} from "../components/slide-editor/charts/chart-data.ts";
import type {
  ChartType,
  DataLabelPosition,
} from "../components/slide-editor/types.ts";

export type JsonRecord = Record<string, unknown>;

export type TemplateV2ContractResult<T> =
  | { ok: true; value: T }
  | { ok: false; reason: string };

export type TemplateV2ContractPatch = {
  path: (string | number)[];
  value: unknown;
};

export type NormalizeChartContractOptions = {
  /**
   * Supplying the old type proves this is an intentional type migration.
   * Without it, stale options fail closed instead of being silently deleted.
   */
  previousType?: unknown;
};

export type ChartContractCapabilities = {
  axisOptions: boolean;
  dataLabelPositions: readonly DataLabelPosition[];
  multipleSeries: boolean;
};

const AXIS_CHART_TYPES = new Set<ChartType>(TEMPLATE_V2_AXIS_CHART_TYPES);
const DATA_LABEL_POSITIONS = new Set<string>(
  TEMPLATE_V2_DATA_LABEL_POSITIONS,
);
const CHART_AXIS_FIELDS = [
  "x_axis",
  "y_axis",
  "x_axis_title",
  "y_axis_title",
  "axis_color",
  "x_axis_grid",
  "y_axis_grid",
  "grid_color",
] as const;
const CHART_LIMIT = 24;
const SERIES_LIMIT = 12;

function isRecord(value: unknown): value is JsonRecord {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    (Object.getPrototypeOf(value) === Object.prototype ||
      Object.getPrototypeOf(value) === null)
  );
}

function cloneJson(
  value: unknown,
  path: string,
  active: Set<object>,
): TemplateV2ContractResult<unknown> {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean"
  ) {
    return { ok: true, value };
  }
  if (typeof value === "number") {
    return Number.isFinite(value)
      ? { ok: true, value }
      : { ok: false, reason: `non_finite_number:${path}` };
  }
  if (Array.isArray(value)) {
    if (active.has(value)) {
      return { ok: false, reason: `recursive_reference:${path}` };
    }
    active.add(value);
    const cloned: unknown[] = [];
    for (let index = 0; index < value.length; index += 1) {
      const item = cloneJson(value[index], `${path}.${index}`, active);
      if (!item.ok) return item;
      cloned.push(item.value);
    }
    active.delete(value);
    return { ok: true, value: cloned };
  }
  if (!isRecord(value)) {
    return { ok: false, reason: `non_json_value:${path}` };
  }
  if (active.has(value)) {
    return { ok: false, reason: `recursive_reference:${path}` };
  }
  active.add(value);
  const cloned: JsonRecord = {};
  for (const [key, itemValue] of Object.entries(value)) {
    const item = cloneJson(itemValue, `${path}.${key}`, active);
    if (!item.ok) return item;
    cloned[key] = item.value;
  }
  active.delete(value);
  return { ok: true, value: cloned };
}

function cloneJsonRecord(
  value: unknown,
): TemplateV2ContractResult<JsonRecord> {
  const cloned = cloneJson(value, "$", new Set());
  if (!cloned.ok) return cloned;
  return isRecord(cloned.value)
    ? { ok: true, value: cloned.value }
    : { ok: false, reason: "element_must_be_object" };
}

function hasMeaningfulField(element: JsonRecord, field: string): boolean {
  return element[field] !== undefined && element[field] !== null;
}

export function chartContractCapabilities(
  chartType: unknown,
): TemplateV2ContractResult<ChartContractCapabilities> {
  const parsed = parseChartType(chartType);
  if (!parsed) return { ok: false, reason: "unsupported_chart_type" };
  const axisOptions = AXIS_CHART_TYPES.has(parsed);
  return {
    ok: true,
    value: {
      axisOptions,
      dataLabelPositions: axisOptions
        ? parsed === "line" || parsed === "area"
          ? ["top", "outside"]
          : TEMPLATE_V2_DATA_LABEL_POSITIONS
        : [],
      multipleSeries: parsed !== "pie" && parsed !== "donut",
    },
  };
}

function validateChartValue(
  chart: JsonRecord,
): TemplateV2ContractResult<JsonRecord> {
  if (chart.type !== "chart") return { ok: false, reason: "not_chart" };
  const chartType = parseChartType(chart.chart_type);
  if (!chartType) return { ok: false, reason: "unsupported_chart_type" };
  const capabilities = chartContractCapabilities(chartType);
  if (!capabilities.ok) return capabilities;

  let categories: unknown[] | null = null;
  if (chart.categories !== undefined && chart.categories !== null) {
    if (
      !Array.isArray(chart.categories) ||
      chart.categories.length > CHART_LIMIT ||
      chart.categories.some((item) => typeof item !== "string")
    ) {
      return { ok: false, reason: "invalid_chart_categories" };
    }
    categories = chart.categories;
  }

  if (chart.series !== undefined && chart.series !== null) {
    if (!Array.isArray(chart.series) || chart.series.length > SERIES_LIMIT) {
      return { ok: false, reason: "invalid_chart_series" };
    }
    if (
      !capabilities.value.multipleSeries &&
      chart.series.length > 1
    ) {
      return { ok: false, reason: "chart_series_limit" };
    }
    for (const series of chart.series) {
      if (
        !isRecord(series) ||
        typeof series.name !== "string" ||
        !Array.isArray(series.values) ||
        series.values.length > CHART_LIMIT ||
        series.values.some(
          (value) => typeof value !== "number" || !Number.isFinite(value),
        )
      ) {
        return { ok: false, reason: "invalid_chart_series" };
      }
      if (categories && series.values.length !== categories.length) {
        return { ok: false, reason: "chart_category_mismatch" };
      }
    }
  }

  const hasAxisOptions = CHART_AXIS_FIELDS.some((field) =>
    hasMeaningfulField(chart, field)
  );
  if (hasAxisOptions && !capabilities.value.axisOptions) {
    return { ok: false, reason: "stale_chart_axis_options" };
  }
  if (hasMeaningfulField(chart, "data_labels")) {
    if (
      typeof chart.data_labels !== "string" ||
      !DATA_LABEL_POSITIONS.has(chart.data_labels)
    ) {
      return { ok: false, reason: "invalid_chart_data_labels" };
    }
    if (
      !capabilities.value.dataLabelPositions.includes(
        chart.data_labels as DataLabelPosition,
      )
    ) {
      return { ok: false, reason: "stale_chart_data_labels" };
    }
  }
  if (isRecord(chart.size)) {
    const { width, height } = chart.size;
    if (
      typeof width !== "number" ||
      !Number.isFinite(width) ||
      typeof height !== "number" ||
      !Number.isFinite(height) ||
      width < 80 ||
      height < 60
    ) {
      return { ok: false, reason: "invalid_chart_size" };
    }
  } else if (chart.size !== undefined && chart.size !== null) {
    return { ok: false, reason: "invalid_chart_size" };
  }
  return { ok: true, value: chart };
}

export function validateChartContract(
  chart: unknown,
): TemplateV2ContractResult<JsonRecord> {
  const cloned = cloneJsonRecord(chart);
  return cloned.ok ? validateChartValue(cloned.value) : cloned;
}

export function normalizeChartContract(
  chart: unknown,
  options: NormalizeChartContractOptions = {},
): TemplateV2ContractResult<JsonRecord> {
  const cloned = cloneJsonRecord(chart);
  if (!cloned.ok) return cloned;
  const value = cloned.value;
  if (value.type !== "chart") return { ok: false, reason: "not_chart" };
  const chartType = parseChartType(value.chart_type);
  if (!chartType) return { ok: false, reason: "unsupported_chart_type" };

  // This boolean migration is explicitly defined by the FastAPI Chart model.
  if (value.data_labels === true) value.data_labels = "top";
  if (value.data_labels === false) delete value.data_labels;

  const previousType =
    options.previousType === undefined
      ? null
      : parseChartType(options.previousType);
  if (options.previousType !== undefined && !previousType) {
    return { ok: false, reason: "unsupported_previous_chart_type" };
  }
  if (previousType && previousType !== chartType) {
    const capabilities = chartContractCapabilities(chartType);
    if (!capabilities.ok) return capabilities;
    if (!capabilities.value.axisOptions) {
      for (const field of CHART_AXIS_FIELDS) delete value[field];
    }
    if (
      hasMeaningfulField(value, "data_labels") &&
      !capabilities.value.dataLabelPositions.includes(
        value.data_labels as DataLabelPosition,
      )
    ) {
      delete value.data_labels;
    }
  }
  return validateChartValue(value);
}

function validTableCell(value: unknown): boolean {
  if (!isRecord(value)) return false;
  if (hasMeaningfulField(value, "stroke") || hasMeaningfulField(value, "border")) {
    // The canonical TableCell model has no border field. Konva and HTML/PPT
    // rendering currently use a fixed fallback border, so accepting either
    // undeclared shape would make persistence invalid and imply false fidelity.
    return false;
  }
  return (
    validOptionalFill(value.color) &&
    validOptionalFont(value.font) &&
    validOptionalTableAlignment(value.alignment) &&
    Array.isArray(value.runs) &&
    value.runs.every(
      (run) =>
        isRecord(run) &&
        typeof run.text === "string" &&
        validOptionalFont(run.font),
    )
  );
}

function validOptionalFill(value: unknown): boolean {
  if (value === undefined || value === null) return true;
  if (!isRecord(value) || typeof value.color !== "string") return false;
  return (
    !hasMeaningfulField(value, "opacity") ||
    (typeof value.opacity === "number" && Number.isFinite(value.opacity))
  );
}

function validOptionalTableAlignment(value: unknown): boolean {
  return (
    value === undefined ||
    value === null ||
    value === "left" ||
    value === "center" ||
    value === "right"
  );
}

const FONT_STRING_FIELDS = ["family", "color"] as const;
const FONT_BOOLEAN_FIELDS = [
  "bold",
  "italic",
  "underline",
  "ellipsis",
] as const;
const FONT_NUMBER_FIELDS = [
  "size",
  "line_height",
  "letter_spacing",
  "opacity",
] as const;

function validOptionalFont(value: unknown): boolean {
  if (value === undefined || value === null) return true;
  if (!isRecord(value)) return false;
  return (
    FONT_STRING_FIELDS.every(
      (field) =>
        !hasMeaningfulField(value, field) || typeof value[field] === "string",
    ) &&
    FONT_BOOLEAN_FIELDS.every(
      (field) =>
        !hasMeaningfulField(value, field) || typeof value[field] === "boolean",
    ) &&
    FONT_NUMBER_FIELDS.every(
      (field) =>
        !hasMeaningfulField(value, field) ||
        (typeof value[field] === "number" && Number.isFinite(value[field])),
    )
  );
}

function readTableBound(
  table: JsonRecord,
  field: "min_columns" | "max_columns" | "min_rows" | "max_rows",
): number | null {
  const value = table[field];
  return Number.isInteger(value) && (value as number) >= 0
    ? (value as number)
    : null;
}

function validateTableValue(
  table: JsonRecord,
): TemplateV2ContractResult<JsonRecord> {
  if (table.type !== "table") return { ok: false, reason: "not_table" };
  if (
    !Array.isArray(table.columns) ||
    !table.columns.every(validTableCell)
  ) {
    return { ok: false, reason: "invalid_table_columns" };
  }
  const columns = table.columns;
  if (
    !Array.isArray(table.rows) ||
    !table.rows.every(
      (row) => Array.isArray(row) && row.every(validTableCell),
    )
  ) {
    return { ok: false, reason: "invalid_table_rows" };
  }
  if (
    table.rows.some(
      (row) => (row as unknown[]).length !== columns.length,
    )
  ) {
    return { ok: false, reason: "table_not_rectangular" };
  }
  const minColumns = readTableBound(table, "min_columns");
  const maxColumns = readTableBound(table, "max_columns");
  const minRows = readTableBound(table, "min_rows");
  const maxRows = readTableBound(table, "max_rows");
  if (
    minColumns === null ||
    maxColumns === null ||
    minRows === null ||
    maxRows === null
  ) {
    return { ok: false, reason: "table_bounds_required" };
  }
  if (minColumns > maxColumns || minRows > maxRows) {
    return { ok: false, reason: "invalid_table_bounds" };
  }
  if (
    columns.length < minColumns ||
    columns.length > maxColumns
  ) {
    return { ok: false, reason: "table_column_count_out_of_bounds" };
  }
  // `rows` contains body rows only. The header in `columns` is deliberately
  // excluded, matching FastAPI models, JSON schema, and PPTX ingestion.
  if (table.rows.length < minRows || table.rows.length > maxRows) {
    return { ok: false, reason: "table_body_row_count_out_of_bounds" };
  }
  return { ok: true, value: table };
}

export function validateTableContract(
  table: unknown,
): TemplateV2ContractResult<JsonRecord> {
  const cloned = cloneJsonRecord(table);
  return cloned.ok ? validateTableValue(cloned.value) : cloned;
}

export function normalizeTableContract(
  table: unknown,
): TemplateV2ContractResult<JsonRecord> {
  const cloned = cloneJsonRecord(table);
  if (!cloned.ok) return cloned;
  const value = cloned.value;
  if (
    value.type !== "table" ||
    !Array.isArray(value.columns) ||
    !Array.isArray(value.rows)
  ) {
    return validateTableValue(value);
  }
  const boundFields = [
    "min_columns",
    "max_columns",
    "min_rows",
    "max_rows",
  ] as const;
  const missing = boundFields.filter(
    (field) => value[field] === undefined || value[field] === null,
  );
  if (missing.length === boundFields.length) {
    value.min_columns = value.columns.length;
    value.max_columns = value.columns.length;
    value.min_rows = value.rows.length;
    value.max_rows = value.rows.length;
  } else if (missing.length > 0) {
    return { ok: false, reason: "partial_table_bounds" };
  }
  return validateTableValue(value);
}

export type ChartContractEdit =
  | { kind: "chart-type"; value: unknown }
  | { kind: "data-labels"; value: unknown }
  | { kind: "category-count"; value: unknown }
  | { kind: "series-count"; value: unknown }
  | { kind: "category-label"; categoryIndex: number; value: unknown }
  | { kind: "series-name"; seriesIndex: number; value: unknown }
  | {
      kind: "series-value";
      seriesIndex: number;
      categoryIndex: number;
      value: unknown;
    };

export function buildChartContractEditPatches(
  chart: unknown,
  edit: ChartContractEdit,
): TemplateV2ContractResult<TemplateV2ContractPatch[]> {
  const validated = validateChartContract(chart);
  if (!validated.ok) return validated;
  const candidate = validated.value;
  const patches: TemplateV2ContractPatch[] = [];

  if (edit.kind === "chart-type") {
    const previousType = parseChartType(candidate.chart_type);
    const nextType = parseChartType(edit.value);
    if (!previousType || !nextType) {
      return { ok: false, reason: "unsupported_chart_type_edit" };
    }
    if (previousType === nextType) return { ok: true, value: [] };
    const capabilities = chartContractCapabilities(nextType);
    if (!capabilities.ok) return capabilities;
    candidate.chart_type = nextType;
    patches.push({ path: ["chart_type"], value: nextType });
    if (!capabilities.value.axisOptions) {
      for (const field of CHART_AXIS_FIELDS) {
        if (hasMeaningfulField(candidate, field)) {
          candidate[field] = null;
          patches.push({ path: [field], value: null });
        }
      }
    }
    if (
      hasMeaningfulField(candidate, "data_labels") &&
      !capabilities.value.dataLabelPositions.includes(
        candidate.data_labels as DataLabelPosition,
      )
    ) {
      candidate.data_labels = null;
      patches.push({ path: ["data_labels"], value: null });
    }
  } else if (edit.kind === "data-labels") {
    const capabilities = chartContractCapabilities(candidate.chart_type);
    if (!capabilities.ok) return capabilities;
    if (
      edit.value !== null &&
      (typeof edit.value !== "string" ||
        !capabilities.value.dataLabelPositions.includes(
          edit.value as DataLabelPosition,
        ))
    ) {
      return { ok: false, reason: "invalid_chart_data_labels_edit" };
    }
    if ((candidate.data_labels ?? null) === edit.value) {
      return { ok: true, value: [] };
    }
    candidate.data_labels = edit.value;
    patches.push({ path: ["data_labels"], value: edit.value });
  } else if (edit.kind === "category-count") {
    if (
      !Number.isInteger(edit.value) ||
      (edit.value as number) < 0 ||
      (edit.value as number) > CHART_LIMIT ||
      !Array.isArray(candidate.categories) ||
      !Array.isArray(candidate.series)
    ) {
      return { ok: false, reason: "invalid_chart_category_count_edit" };
    }
    const nextCount = edit.value as number;
    if (nextCount === candidate.categories.length) {
      return { ok: true, value: [] };
    }
    const categories = candidate.categories as string[];
    if (nextCount < categories.length) categories.splice(nextCount);
    while (categories.length < nextCount) {
      categories.push(`Category ${categories.length + 1}`);
    }
    patches.push({ path: ["categories"], value: categories });
    (candidate.series as JsonRecord[]).forEach((item, seriesIndex) => {
      const values = item.values as number[];
      if (nextCount < values.length) values.splice(nextCount);
      while (values.length < nextCount) values.push(0);
      patches.push({
        path: ["series", seriesIndex, "values"],
        value: values,
      });
    });
  } else if (edit.kind === "series-count") {
    const capabilities = chartContractCapabilities(candidate.chart_type);
    if (!capabilities.ok) return capabilities;
    const limit = capabilities.value.multipleSeries ? SERIES_LIMIT : 1;
    if (
      !Number.isInteger(edit.value) ||
      (edit.value as number) < 0 ||
      (edit.value as number) > limit ||
      !Array.isArray(candidate.categories) ||
      !Array.isArray(candidate.series)
    ) {
      return { ok: false, reason: "invalid_chart_series_count_edit" };
    }
    const nextCount = edit.value as number;
    const series = candidate.series as JsonRecord[];
    if (nextCount === series.length) return { ok: true, value: [] };
    if (nextCount < series.length) series.splice(nextCount);
    while (series.length < nextCount) {
      series.push({
        name: `Series ${series.length + 1}`,
        values: (candidate.categories as string[]).map(() => 0),
      });
    }
    patches.push({ path: ["series"], value: series });
  } else if (edit.kind === "category-label") {
    if (
      !Number.isInteger(edit.categoryIndex) ||
      typeof edit.value !== "string" ||
      !Array.isArray(candidate.categories) ||
      edit.categoryIndex < 0 ||
      edit.categoryIndex >= candidate.categories.length
    ) {
      return { ok: false, reason: "invalid_chart_category_edit" };
    }
    candidate.categories[edit.categoryIndex] = edit.value;
    patches.push({
      path: ["categories", edit.categoryIndex],
      value: edit.value,
    });
  } else {
    if (
      !Number.isInteger(edit.seriesIndex) ||
      !Array.isArray(candidate.series) ||
      edit.seriesIndex < 0 ||
      edit.seriesIndex >= candidate.series.length
    ) {
      return { ok: false, reason: "invalid_chart_series_edit" };
    }
    const selectedSeries = candidate.series[edit.seriesIndex];
    if (!isRecord(selectedSeries)) {
      return { ok: false, reason: "invalid_chart_series_edit" };
    }
    if (edit.kind === "series-name") {
      if (typeof edit.value !== "string") {
        return { ok: false, reason: "invalid_chart_series_name_edit" };
      }
      selectedSeries.name = edit.value;
      patches.push({
        path: ["series", edit.seriesIndex, "name"],
        value: edit.value,
      });
    } else {
      if (
        !Number.isInteger(edit.categoryIndex) ||
        typeof edit.value !== "number" ||
        !Number.isFinite(edit.value) ||
        !Array.isArray(selectedSeries.values) ||
        edit.categoryIndex < 0 ||
        edit.categoryIndex >= selectedSeries.values.length
      ) {
        return { ok: false, reason: "invalid_chart_series_value_edit" };
      }
      selectedSeries.values[edit.categoryIndex] = edit.value;
      patches.push({
        path: ["series", edit.seriesIndex, "values", edit.categoryIndex],
        value: edit.value,
      });
    }
  }

  const result = validateChartValue(candidate);
  return result.ok ? { ok: true, value: patches } : result;
}

export type TableContractEdit =
  | {
      kind: "run-text";
      section: "header" | "body";
      rowIndex?: number;
      columnIndex: number;
      runIndex: number;
      value: unknown;
    }
  | {
      kind: "cell-font";
      section: "header" | "body";
      rowIndex?: number;
      columnIndex: number;
      field: "size" | "family" | "color" | "bold" | "italic" | "underline";
      value: unknown;
    }
  | {
      kind: "cell-alignment";
      section: "header" | "body";
      rowIndex?: number;
      columnIndex: number;
      value: unknown;
    }
  | {
      kind: "section-fill";
      section: "header" | "body";
      value: unknown;
    }
  | { kind: "row-count"; value: unknown }
  | { kind: "column-count"; value: unknown };

function blankTableCell(): JsonRecord {
  return { runs: [{ text: "" }] };
}

export function buildTableContractEditPatches(
  table: unknown,
  edit: TableContractEdit,
): TemplateV2ContractResult<TemplateV2ContractPatch[]> {
  const validated = validateTableContract(table);
  if (!validated.ok) return validated;
  const candidate = validated.value;
  const columns = candidate.columns as JsonRecord[];
  const rows = candidate.rows as JsonRecord[][];
  const patches: TemplateV2ContractPatch[] = [];

  if (
    edit.kind === "run-text" ||
    edit.kind === "cell-font" ||
    edit.kind === "cell-alignment"
  ) {
    if (
      !Number.isInteger(edit.columnIndex) ||
      edit.columnIndex < 0 ||
      (edit.kind === "run-text" &&
        (!Number.isInteger(edit.runIndex) ||
          typeof edit.value !== "string" ||
          edit.runIndex < 0))
    ) {
      return {
        ok: false,
        reason:
          edit.kind === "run-text"
            ? "invalid_table_text_edit"
            : edit.kind === "cell-font"
              ? "invalid_table_font_edit"
              : "invalid_table_alignment_edit",
      };
    }
    let cell: JsonRecord | undefined;
    let cellPath: (string | number)[];
    if (edit.section === "header") {
      cell = columns[edit.columnIndex];
      cellPath = ["columns", edit.columnIndex];
    } else {
      if (!Number.isInteger(edit.rowIndex) || (edit.rowIndex as number) < 0) {
        return {
          ok: false,
          reason:
            edit.kind === "run-text"
              ? "invalid_table_text_edit"
              : edit.kind === "cell-font"
                ? "invalid_table_font_edit"
                : "invalid_table_alignment_edit",
        };
      }
      cell = rows[edit.rowIndex as number]?.[edit.columnIndex];
      cellPath = [
        "rows",
        edit.rowIndex as number,
        edit.columnIndex,
      ];
    }
    if (!cell) {
      return {
        ok: false,
        reason:
          edit.kind === "run-text"
            ? "invalid_table_text_edit"
            : edit.kind === "cell-font"
              ? "invalid_table_font_edit"
              : "invalid_table_alignment_edit",
      };
    }
    if (edit.kind === "run-text") {
      const runs = cell.runs;
      if (!Array.isArray(runs) || edit.runIndex >= runs.length) {
        return { ok: false, reason: "invalid_table_text_edit" };
      }
      const run = runs[edit.runIndex];
      if (!isRecord(run)) {
        return { ok: false, reason: "invalid_table_text_edit" };
      }
      run.text = edit.value;
      patches.push({
        path: [...cellPath, "runs", edit.runIndex, "text"],
        value: edit.value,
      });
    } else if (edit.kind === "cell-font") {
      const isBooleanField = ["bold", "italic", "underline"].includes(edit.field);
      const isNumberField = edit.field === "size";
      if (
        (isBooleanField && typeof edit.value !== "boolean") ||
        (isNumberField &&
          (typeof edit.value !== "number" || !Number.isFinite(edit.value))) ||
        (!isBooleanField && !isNumberField && typeof edit.value !== "string")
      ) {
        return { ok: false, reason: "invalid_table_font_edit" };
      }
      const font = isRecord(cell.font) ? cell.font : {};
      font[edit.field] = edit.value;
      cell.font = font;
      patches.push({
        path: [...cellPath, "font", edit.field],
        value: edit.value,
      });
    } else {
      if (
        edit.value !== "left" &&
        edit.value !== "center" &&
        edit.value !== "right"
      ) {
        return { ok: false, reason: "invalid_table_alignment_edit" };
      }
      cell.alignment = edit.value;
      patches.push({
        path: [...cellPath, "alignment"],
        value: edit.value,
      });
    }
  } else if (edit.kind === "section-fill") {
    if (typeof edit.value !== "string") {
      return { ok: false, reason: "invalid_table_fill_edit" };
    }
    const targets =
      edit.section === "header"
        ? columns.map((cell, columnIndex) => ({
            cell,
            path: ["columns", columnIndex] as (string | number)[],
          }))
        : rows.flatMap((row, rowIndex) =>
            row.map((cell, columnIndex) => ({
              cell,
              path: [
                "rows",
                rowIndex,
                columnIndex,
              ] as (string | number)[],
            })),
          );
    for (const { cell, path } of targets) {
      const fill = isRecord(cell.color) ? cell.color : {};
      if (fill.color === edit.value) continue;
      fill.color = edit.value;
      cell.color = fill;
      patches.push({
        path: [...path, "color", "color"],
        value: edit.value,
      });
    }
  } else if (edit.kind === "row-count") {
    if (!Number.isInteger(edit.value) || (edit.value as number) < 0) {
      return { ok: false, reason: "invalid_table_row_count_edit" };
    }
    const nextCount = edit.value as number;
    const min = readTableBound(candidate, "min_rows");
    const max = readTableBound(candidate, "max_rows");
    if (min === null || max === null || nextCount < min || nextCount > max) {
      return { ok: false, reason: "table_row_count_edit_out_of_bounds" };
    }
    if (nextCount === rows.length) return { ok: true, value: [] };
    // The toolbar emits one +/- action at a time; only that fixed-fixture
    // round-trip is proven lossless.
    if (Math.abs(nextCount - rows.length) !== 1) {
      return {
        ok: false,
        reason: "table_row_count_edit_requires_single_step",
      };
    }
    if (nextCount < rows.length) rows.splice(nextCount);
    else {
      while (rows.length < nextCount) {
        rows.push(columns.map(() => blankTableCell()));
      }
    }
    patches.push({ path: ["rows"], value: rows });
  } else if (edit.kind === "column-count") {
    if (!Number.isInteger(edit.value) || (edit.value as number) < 1) {
      return { ok: false, reason: "invalid_table_column_count_edit" };
    }
    const nextCount = edit.value as number;
    const min = readTableBound(candidate, "min_columns");
    const max = readTableBound(candidate, "max_columns");
    if (min === null || max === null || nextCount < min || nextCount > max) {
      return { ok: false, reason: "table_column_count_edit_out_of_bounds" };
    }
    if (nextCount === columns.length) return { ok: true, value: [] };
    // Keep programmatic callers on the same proven unit as the toolbar.
    if (Math.abs(nextCount - columns.length) !== 1) {
      return {
        ok: false,
        reason: "table_column_count_edit_requires_single_step",
      };
    }
    if (nextCount < columns.length) {
      columns.splice(nextCount);
      for (const row of rows) row.splice(nextCount);
    } else {
      while (columns.length < nextCount) {
        columns.push(blankTableCell());
        for (const row of rows) row.push(blankTableCell());
      }
    }
    patches.push(
      { path: ["columns"], value: columns },
      { path: ["rows"], value: rows },
    );
  } else {
    return { ok: false, reason: "unsupported_table_edit" };
  }

  const result = validateTableValue(candidate);
  return result.ok ? { ok: true, value: patches } : result;
}
