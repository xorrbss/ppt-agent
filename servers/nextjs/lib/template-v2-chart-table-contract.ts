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
  return (
    isRecord(value) &&
    Array.isArray(value.runs) &&
    value.runs.every(
      (run) => isRecord(run) && typeof run.text === "string",
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
