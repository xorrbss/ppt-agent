export type JsonRecord = Record<string, unknown>;

export const TEMPLATE_V2_CHART_COPILOT_LIMITS = Object.freeze({
  maxInputBytes: 64 * 1024,
  maxCategories: 24,
  maxSeries: 12,
  maxCellCharacters: 256,
  maxSeriesNameCharacters: 120,
  maxTitleCharacters: 240,
  maxCandidateCount: 3,
  maxOperationsPerCandidate: 32,
});

export const TEMPLATE_V2_CHART_TYPES = Object.freeze([
  "area",
  "bar",
  "bubble",
  "donut",
  "horizontal_bar",
  "horizontal_stacked_bar",
  "line",
  "pie",
  "polar_area",
  "radar",
  "scatter",
  "stacked_bar",
] as const);

export type TemplateV2ChartType = (typeof TEMPLATE_V2_CHART_TYPES)[number];

const CHART_TYPES = new Set<string>(TEMPLATE_V2_CHART_TYPES);
const AXIS_CHART_TYPES = new Set<TemplateV2ChartType>([
  "area",
  "bar",
  "horizontal_bar",
  "horizontal_stacked_bar",
  "line",
  "stacked_bar",
]);
const DATA_LABEL_POSITIONS = new Set(["base", "mid", "top", "outside"]);
// Kept byte-for-byte compatible with the strict render-plan color grammar.
const SAFE_COLOR_PATTERN =
  /^(?:#[0-9a-fA-F]{3,8}|[a-zA-Z]+|rgba?\([0-9.,%+\-\s]+\)|hsla?\([0-9a-zA-Z.,%+\-\s]+\))$/;
const COLOR_CONTROLS = new Set<TemplateV2ChartControl>([
  "title_color",
  "legend_color",
  "axis_color",
  "grid_color",
]);
const BOOLEAN_CONTROLS = new Set<TemplateV2ChartControl>([
  "legend",
  "x_axis",
  "y_axis",
  "x_axis_grid",
  "y_axis_grid",
]);
const AXIS_CONTROLS = new Set<TemplateV2ChartControl>([
  "x_axis",
  "y_axis",
  "x_axis_title",
  "y_axis_title",
  "axis_color",
  "x_axis_grid",
  "y_axis_grid",
  "grid_color",
]);

export type TemplateV2ChartControl =
  | "chart_type"
  | "title"
  | "title_color"
  | "legend"
  | "legend_color"
  | "x_axis"
  | "y_axis"
  | "x_axis_title"
  | "y_axis_title"
  | "axis_color"
  | "x_axis_grid"
  | "y_axis_grid"
  | "grid_color"
  | "data_labels";

const CHART_CONTROLS = new Set<TemplateV2ChartControl>([
  "chart_type",
  "title",
  "title_color",
  "legend",
  "legend_color",
  "x_axis",
  "y_axis",
  "x_axis_title",
  "y_axis_title",
  "axis_color",
  "x_axis_grid",
  "y_axis_grid",
  "grid_color",
  "data_labels",
]);

export type TemplateV2ChartSeries = JsonRecord & {
  name: string;
  values: number[];
};

export type TemplateV2Chart = JsonRecord & {
  type: "chart";
  chart_type: TemplateV2ChartType;
  categories: string[];
  series: TemplateV2ChartSeries[];
};

export type TemplateV2ChartCopilotOperation =
  | {
      kind: "set-control";
      control: TemplateV2ChartControl;
      value: string | boolean | null;
    }
  | {
      kind: "add-series";
      series: { name: string; values: number[] };
      index?: number;
    }
  | { kind: "remove-series"; seriesIndex: number }
  | { kind: "move-series"; seriesIndex: number; destinationIndex: number }
  | { kind: "rename-series"; seriesIndex: number; name: string }
  | {
      kind: "set-series-value";
      seriesIndex: number;
      categoryIndex: number;
      value: number;
    }
  | {
      kind: "replace-data";
      categories: string[];
      series: Array<{ name: string; values: number[] }>;
    };

export type TemplateV2ChartCopilotReasonCode =
  | "template_v2_chart_copilot_invalid_chart"
  | "template_v2_chart_copilot_invalid_operation"
  | "template_v2_chart_copilot_unsupported_control"
  | "template_v2_chart_copilot_incompatible_control"
  | "template_v2_chart_copilot_limit_exceeded"
  | "template_v2_chart_copilot_invalid_import"
  | "template_v2_chart_copilot_provider_unavailable"
  | "template_v2_chart_copilot_invalid_provider_response";

export class TemplateV2ChartCopilotError extends Error {
  readonly code: TemplateV2ChartCopilotReasonCode;
  readonly path: string;

  constructor(code: TemplateV2ChartCopilotReasonCode, path: string) {
    super(`${code}:${path}`);
    this.name = "TemplateV2ChartCopilotError";
    this.code = code;
    this.path = path;
  }
}

export interface TemplateV2ChartCopilotDiff {
  path: string;
  before: unknown;
  after: unknown;
}

export interface TemplateV2ChartCopilotPreview {
  before: TemplateV2Chart;
  after: TemplateV2Chart;
  diff: TemplateV2ChartCopilotDiff[];
  operations: TemplateV2ChartCopilotOperation[];
}

export interface TemplateV2ChartRecommendation {
  chartType: TemplateV2ChartType;
  reasonCode:
    | "time_series"
    | "long_category_labels"
    | "part_to_whole"
    | "multi_series_comparison"
    | "single_series_comparison";
  confidence: "high" | "medium";
}

export interface TemplateV2ChartCopilotCandidate {
  id: string;
  label: string;
  preview: TemplateV2ChartCopilotPreview;
}

export interface TemplateV2ChartCopilotProvider {
  recommend(request: {
    chart: TemplateV2Chart;
    allowedChartTypes: readonly TemplateV2ChartType[];
    maxCandidates: number;
  }): Promise<unknown>;
}

function fail(
  code: TemplateV2ChartCopilotReasonCode,
  path: string,
): never {
  throw new TemplateV2ChartCopilotError(code, path);
}

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isSafeColor(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length <= 96 &&
    SAFE_COLOR_PATTERN.test(value)
  );
}

function hasExactKeys(
  value: JsonRecord,
  required: readonly string[],
  optional: readonly string[] = [],
): boolean {
  const allowed = new Set([...required, ...optional]);
  return (
    required.every((key) => Object.hasOwn(value, key)) &&
    Object.keys(value).every((key) => allowed.has(key))
  );
}

function finiteNumber(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    fail("template_v2_chart_copilot_invalid_chart", path);
  }
  return value;
}

function boundedString(
  value: unknown,
  path: string,
  maximum: number,
  { allowEmpty = true }: { allowEmpty?: boolean } = {},
): string {
  if (
    typeof value !== "string" ||
    value.length > maximum ||
    (!allowEmpty && value.trim().length === 0) ||
    /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(value)
  ) {
    fail("template_v2_chart_copilot_invalid_chart", path);
  }
  return value;
}

function validateSeries(
  value: unknown,
  categoryCount: number,
  path: string,
): asserts value is TemplateV2ChartSeries {
  if (!isRecord(value)) {
    fail("template_v2_chart_copilot_invalid_chart", path);
  }
  boundedString(
    value.name,
    `${path}.name`,
    TEMPLATE_V2_CHART_COPILOT_LIMITS.maxSeriesNameCharacters,
    { allowEmpty: false },
  );
  if (
    !Array.isArray(value.values) ||
    value.values.length !== categoryCount
  ) {
    fail("template_v2_chart_copilot_invalid_chart", `${path}.values`);
  }
  value.values.forEach((item, index) =>
    finiteNumber(item, `${path}.values.${index}`),
  );
}

/**
 * Validates only the chart fields owned by the copilot. Forward-compatible
 * metadata remains opaque and is retained by every patch below.
 */
export function validateTemplateV2ChartCopilotChart(
  value: unknown,
): asserts value is TemplateV2Chart {
  if (
    !isRecord(value) ||
    value.type !== "chart" ||
    typeof value.chart_type !== "string" ||
    !CHART_TYPES.has(value.chart_type)
  ) {
    fail("template_v2_chart_copilot_invalid_chart", "chart");
  }
  if (
    !Array.isArray(value.categories) ||
    value.categories.length > TEMPLATE_V2_CHART_COPILOT_LIMITS.maxCategories
  ) {
    fail("template_v2_chart_copilot_limit_exceeded", "chart.categories");
  }
  const categories = value.categories;
  categories.forEach((category, index) =>
    boundedString(
      category,
      `chart.categories.${index}`,
      TEMPLATE_V2_CHART_COPILOT_LIMITS.maxCellCharacters,
    ),
  );
  if (
    !Array.isArray(value.series) ||
    value.series.length > TEMPLATE_V2_CHART_COPILOT_LIMITS.maxSeries
  ) {
    fail("template_v2_chart_copilot_limit_exceeded", "chart.series");
  }
  value.series.forEach((series, index) =>
    validateSeries(series, categories.length, `chart.series.${index}`),
  );
  if (
    (value.chart_type === "pie" || value.chart_type === "donut") &&
    value.series.length > 1
  ) {
    fail("template_v2_chart_copilot_incompatible_control", "chart.series");
  }
  validateExistingControls(value as TemplateV2Chart);
}

function validateExistingControls(chart: TemplateV2Chart): void {
  for (const control of BOOLEAN_CONTROLS) {
    const value = chart[control];
    if (value !== undefined && value !== null && typeof value !== "boolean") {
      fail("template_v2_chart_copilot_invalid_chart", `chart.${control}`);
    }
  }
  for (const control of COLOR_CONTROLS) {
    const value = chart[control];
    if (value !== undefined && value !== null && !isSafeColor(value)) {
      fail("template_v2_chart_copilot_invalid_chart", `chart.${control}`);
    }
  }
  for (const control of ["title", "x_axis_title", "y_axis_title"] as const) {
    const value = chart[control];
    if (value !== undefined && value !== null) {
      boundedString(
        value,
        `chart.${control}`,
        control === "title"
          ? TEMPLATE_V2_CHART_COPILOT_LIMITS.maxTitleCharacters
          : TEMPLATE_V2_CHART_COPILOT_LIMITS.maxCellCharacters,
      );
    }
  }
  if (
    chart.data_labels !== undefined &&
    chart.data_labels !== null &&
    (typeof chart.data_labels !== "string" ||
      !DATA_LABEL_POSITIONS.has(chart.data_labels))
  ) {
    fail("template_v2_chart_copilot_invalid_chart", "chart.data_labels");
  }
  for (const control of AXIS_CONTROLS) {
    if (
      chart[control] !== undefined &&
      chart[control] !== null &&
      !AXIS_CHART_TYPES.has(chart.chart_type)
    ) {
      fail(
        "template_v2_chart_copilot_incompatible_control",
        `chart.${control}`,
      );
    }
  }
  if (
    chart.data_labels !== undefined &&
    chart.data_labels !== null &&
    !AXIS_CHART_TYPES.has(chart.chart_type)
  ) {
    fail(
      "template_v2_chart_copilot_incompatible_control",
      "chart.data_labels",
    );
  }
  if (
    typeof chart.data_labels === "string" &&
    (!DATA_LABEL_POSITIONS.has(chart.data_labels) ||
      ((chart.chart_type === "line" || chart.chart_type === "area") &&
        chart.data_labels !== "top" &&
        chart.data_labels !== "outside"))
  ) {
    fail(
      "template_v2_chart_copilot_incompatible_control",
      "chart.data_labels",
    );
  }
}

function validateControlValue(
  chart: TemplateV2Chart,
  control: TemplateV2ChartControl,
  value: unknown,
): void {
  if (!CHART_CONTROLS.has(control)) {
    fail("template_v2_chart_copilot_unsupported_control", `control.${control}`);
  }
  if (control === "chart_type") {
    if (typeof value !== "string" || !CHART_TYPES.has(value)) {
      fail("template_v2_chart_copilot_invalid_operation", "value");
    }
    return;
  }
  if (value === null) return;
  if (BOOLEAN_CONTROLS.has(control)) {
    if (typeof value !== "boolean") {
      fail("template_v2_chart_copilot_invalid_operation", `control.${control}`);
    }
    return;
  }
  if (COLOR_CONTROLS.has(control)) {
    if (!isSafeColor(value)) {
      fail("template_v2_chart_copilot_invalid_operation", `control.${control}`);
    }
    return;
  }
  if (control === "data_labels") {
    if (typeof value !== "string" || !DATA_LABEL_POSITIONS.has(value)) {
      fail("template_v2_chart_copilot_invalid_operation", "control.data_labels");
    }
    return;
  }
  boundedString(
    value,
    `control.${control}`,
    control === "title"
      ? TEMPLATE_V2_CHART_COPILOT_LIMITS.maxTitleCharacters
      : TEMPLATE_V2_CHART_COPILOT_LIMITS.maxCellCharacters,
  );
  if (AXIS_CONTROLS.has(control) && !AXIS_CHART_TYPES.has(chart.chart_type)) {
    fail(
      "template_v2_chart_copilot_incompatible_control",
      `control.${control}`,
    );
  }
}

function normalizeOperation(
  value: unknown,
  path: string,
): TemplateV2ChartCopilotOperation {
  if (!isRecord(value) || typeof value.kind !== "string") {
    fail("template_v2_chart_copilot_invalid_operation", path);
  }
  switch (value.kind) {
    case "set-control": {
      if (
        !hasExactKeys(value, ["kind", "control", "value"]) ||
        typeof value.control !== "string"
      ) {
        fail("template_v2_chart_copilot_invalid_operation", path);
      }
      if (!CHART_CONTROLS.has(value.control as TemplateV2ChartControl)) {
        fail(
          "template_v2_chart_copilot_unsupported_control",
          `${path}.control`,
        );
      }
      return value as unknown as TemplateV2ChartCopilotOperation;
    }
    case "add-series": {
      if (
        !hasExactKeys(value, ["kind", "series"], ["index"]) ||
        !isRecord(value.series) ||
        !hasExactKeys(value.series, ["name", "values"])
      ) {
        fail("template_v2_chart_copilot_invalid_operation", path);
      }
      return value as unknown as TemplateV2ChartCopilotOperation;
    }
    case "remove-series":
      if (!hasExactKeys(value, ["kind", "seriesIndex"])) {
        fail("template_v2_chart_copilot_invalid_operation", path);
      }
      return value as unknown as TemplateV2ChartCopilotOperation;
    case "move-series":
      if (
        !hasExactKeys(value, [
          "kind",
          "seriesIndex",
          "destinationIndex",
        ])
      ) {
        fail("template_v2_chart_copilot_invalid_operation", path);
      }
      return value as unknown as TemplateV2ChartCopilotOperation;
    case "rename-series":
      if (!hasExactKeys(value, ["kind", "seriesIndex", "name"])) {
        fail("template_v2_chart_copilot_invalid_operation", path);
      }
      return value as unknown as TemplateV2ChartCopilotOperation;
    case "set-series-value":
      if (
        !hasExactKeys(value, [
          "kind",
          "seriesIndex",
          "categoryIndex",
          "value",
        ])
      ) {
        fail("template_v2_chart_copilot_invalid_operation", path);
      }
      return value as unknown as TemplateV2ChartCopilotOperation;
    case "replace-data":
      if (!hasExactKeys(value, ["kind", "categories", "series"])) {
        fail("template_v2_chart_copilot_invalid_operation", path);
      }
      return value as unknown as TemplateV2ChartCopilotOperation;
    default:
      fail("template_v2_chart_copilot_invalid_operation", path);
  }
}

function integerInRange(
  value: unknown,
  minimum: number,
  maximum: number,
  path: string,
): number {
  if (
    typeof value !== "number" ||
    !Number.isInteger(value) ||
    value < minimum ||
    value > maximum
  ) {
    fail("template_v2_chart_copilot_invalid_operation", path);
  }
  return value;
}

function validatedNewSeries(
  value: unknown,
  categoryCount: number,
  path: string,
): { name: string; values: number[] } {
  if (!isRecord(value) || !hasExactKeys(value, ["name", "values"])) {
    fail("template_v2_chart_copilot_invalid_operation", path);
  }
  const name = boundedString(
    value.name,
    `${path}.name`,
    TEMPLATE_V2_CHART_COPILOT_LIMITS.maxSeriesNameCharacters,
    { allowEmpty: false },
  );
  if (
    !Array.isArray(value.values) ||
    value.values.length !== categoryCount
  ) {
    fail("template_v2_chart_copilot_invalid_operation", `${path}.values`);
  }
  const values = value.values.map((item, index) =>
    finiteNumber(item, `${path}.values.${index}`),
  );
  return { name, values };
}

function applyOperation(
  chart: TemplateV2Chart,
  operation: TemplateV2ChartCopilotOperation,
): TemplateV2Chart {
  switch (operation.kind) {
    case "set-control": {
      validateControlValue(chart, operation.control, operation.value);
      if (chart[operation.control] === operation.value) return chart;
      return { ...chart, [operation.control]: operation.value };
    }
    case "add-series": {
      if (chart.series.length >= TEMPLATE_V2_CHART_COPILOT_LIMITS.maxSeries) {
        fail("template_v2_chart_copilot_limit_exceeded", "chart.series");
      }
      const series = validatedNewSeries(
        operation.series,
        chart.categories.length,
        "operation.series",
      );
      const index =
        operation.index === undefined
          ? chart.series.length
          : integerInRange(
              operation.index,
              0,
              chart.series.length,
              "operation.index",
            );
      const next = chart.series.slice();
      next.splice(index, 0, series);
      return { ...chart, series: next };
    }
    case "remove-series": {
      const index = integerInRange(
        operation.seriesIndex,
        0,
        chart.series.length - 1,
        "operation.seriesIndex",
      );
      const series = chart.series.slice();
      series.splice(index, 1);
      return { ...chart, series };
    }
    case "move-series": {
      const source = integerInRange(
        operation.seriesIndex,
        0,
        chart.series.length - 1,
        "operation.seriesIndex",
      );
      const destination = integerInRange(
        operation.destinationIndex,
        0,
        chart.series.length - 1,
        "operation.destinationIndex",
      );
      if (source === destination) return chart;
      const series = chart.series.slice();
      const [moved] = series.splice(source, 1);
      series.splice(destination, 0, moved);
      return { ...chart, series };
    }
    case "rename-series": {
      const index = integerInRange(
        operation.seriesIndex,
        0,
        chart.series.length - 1,
        "operation.seriesIndex",
      );
      const name = boundedString(
        operation.name,
        "operation.name",
        TEMPLATE_V2_CHART_COPILOT_LIMITS.maxSeriesNameCharacters,
        { allowEmpty: false },
      );
      if (chart.series[index].name === name) return chart;
      const series = chart.series.slice();
      series[index] = { ...series[index], name };
      return { ...chart, series };
    }
    case "set-series-value": {
      const seriesIndex = integerInRange(
        operation.seriesIndex,
        0,
        chart.series.length - 1,
        "operation.seriesIndex",
      );
      const categoryIndex = integerInRange(
        operation.categoryIndex,
        0,
        chart.categories.length - 1,
        "operation.categoryIndex",
      );
      const value = finiteNumber(operation.value, "operation.value");
      if (chart.series[seriesIndex].values[categoryIndex] === value) return chart;
      const values = chart.series[seriesIndex].values.slice();
      values[categoryIndex] = value;
      const series = chart.series.slice();
      series[seriesIndex] = { ...series[seriesIndex], values };
      return { ...chart, series };
    }
    case "replace-data":
      return replaceChartDataPreservingMetadata(
        chart,
        operation.categories,
        operation.series,
      );
  }
}

function replaceChartDataPreservingMetadata(
  chart: TemplateV2Chart,
  rawCategories: unknown,
  rawSeries: unknown,
): TemplateV2Chart {
  if (
    !Array.isArray(rawCategories) ||
    rawCategories.length > TEMPLATE_V2_CHART_COPILOT_LIMITS.maxCategories ||
    !Array.isArray(rawSeries) ||
    rawSeries.length > TEMPLATE_V2_CHART_COPILOT_LIMITS.maxSeries
  ) {
    fail("template_v2_chart_copilot_limit_exceeded", "operation.replace-data");
  }
  const categories = rawCategories.map((category, index) =>
    boundedString(
      category,
      `operation.categories.${index}`,
      TEMPLATE_V2_CHART_COPILOT_LIMITS.maxCellCharacters,
    ),
  );
  const claimed = new Set<number>();
  const series = rawSeries.map((item, index) => {
    const replacement = validatedNewSeries(
      item,
      categories.length,
      `operation.series.${index}`,
    );
    let existingIndex = chart.series.findIndex(
      (candidate, candidateIndex) =>
        !claimed.has(candidateIndex) && candidate.name === replacement.name,
    );
    if (existingIndex < 0 && index < chart.series.length && !claimed.has(index)) {
      existingIndex = index;
    }
    if (existingIndex < 0) return replacement;
    claimed.add(existingIndex);
    return { ...chart.series[existingIndex], ...replacement };
  });
  return { ...chart, categories, series };
}

function comparable(value: unknown): string {
  return JSON.stringify(value);
}

function collectDiff(
  before: TemplateV2Chart,
  after: TemplateV2Chart,
): TemplateV2ChartCopilotDiff[] {
  const paths: TemplateV2ChartControl[] = [...CHART_CONTROLS];
  const diff: TemplateV2ChartCopilotDiff[] = [];
  for (const path of paths) {
    if (comparable(before[path]) !== comparable(after[path])) {
      diff.push({ path, before: before[path], after: after[path] });
    }
  }
  if (comparable(before.categories) !== comparable(after.categories)) {
    diff.push({
      path: "categories",
      before: before.categories,
      after: after.categories,
    });
  }
  if (comparable(before.series) !== comparable(after.series)) {
    diff.push({ path: "series", before: before.series, after: after.series });
  }
  return diff;
}

export function previewTemplateV2ChartCopilot(
  chartValue: unknown,
  operationValues: readonly unknown[],
): TemplateV2ChartCopilotPreview {
  validateTemplateV2ChartCopilotChart(chartValue);
  if (
    !Array.isArray(operationValues) ||
    operationValues.length > TEMPLATE_V2_CHART_COPILOT_LIMITS.maxOperationsPerCandidate
  ) {
    fail("template_v2_chart_copilot_limit_exceeded", "operations");
  }
  const operations = operationValues.map((operation, index) =>
    normalizeOperation(operation, `operations.${index}`),
  );
  let after = chartValue;
  for (const operation of operations) {
    after = applyOperation(after, operation);
    validateTemplateV2ChartCopilotChart(after);
  }
  return {
    before: chartValue,
    after,
    diff: collectDiff(chartValue, after),
    operations,
  };
}

function parseDelimited(text: string, delimiter: "," | "\t"): string[][] {
  if (
    new TextEncoder().encode(text).byteLength >
    TEMPLATE_V2_CHART_COPILOT_LIMITS.maxInputBytes
  ) {
    fail("template_v2_chart_copilot_limit_exceeded", "import.bytes");
  }
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (character === '"') {
      if (quoted && text[index + 1] === '"') {
        cell += '"';
        index += 1;
      } else if (quoted || cell.length === 0) {
        quoted = !quoted;
      } else {
        fail("template_v2_chart_copilot_invalid_import", `import.${index}`);
      }
    } else if (character === delimiter && !quoted) {
      row.push(cell);
      cell = "";
    } else if ((character === "\n" || character === "\r") && !quoted) {
      if (character === "\r" && text[index + 1] === "\n") index += 1;
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
    } else {
      cell += character;
      if (cell.length > TEMPLATE_V2_CHART_COPILOT_LIMITS.maxCellCharacters) {
        fail("template_v2_chart_copilot_limit_exceeded", "import.cell");
      }
    }
  }
  if (quoted) {
    fail("template_v2_chart_copilot_invalid_import", "import.quote");
  }
  row.push(cell);
  rows.push(row);
  if (rows.length > 1 && rows.at(-1)?.every((value) => value === "")) rows.pop();
  return rows;
}

function normalizeTableRows(value: unknown): string[][] {
  if (!Array.isArray(value)) {
    fail("template_v2_chart_copilot_invalid_import", "import.rows");
  }
  return value.map((row, rowIndex) => {
    if (!Array.isArray(row)) {
      fail("template_v2_chart_copilot_invalid_import", `import.rows.${rowIndex}`);
    }
    return row.map((cell, columnIndex) => {
      if (
        typeof cell !== "string" &&
        typeof cell !== "number"
      ) {
        fail(
          "template_v2_chart_copilot_invalid_import",
          `import.rows.${rowIndex}.${columnIndex}`,
        );
      }
      return boundedString(
        String(cell),
        `import.rows.${rowIndex}.${columnIndex}`,
        TEMPLATE_V2_CHART_COPILOT_LIMITS.maxCellCharacters,
      );
    });
  });
}

export function importTemplateV2ChartData(
  request:
    | { format: "csv" | "tsv"; text: string }
    | { format: "table"; rows: unknown },
): { categories: string[]; series: Array<{ name: string; values: number[] }> } {
  if (!isRecord(request)) {
    fail("template_v2_chart_copilot_invalid_import", "import");
  }
  let rows: string[][];
  if (request.format === "csv" || request.format === "tsv") {
    if (
      !hasExactKeys(request, ["format", "text"]) ||
      typeof request.text !== "string"
    ) {
      fail("template_v2_chart_copilot_invalid_import", "import");
    }
    rows = parseDelimited(request.text, request.format === "csv" ? "," : "\t");
  } else if (request.format === "table") {
    if (!hasExactKeys(request, ["format", "rows"])) {
      fail("template_v2_chart_copilot_invalid_import", "import");
    }
    rows = normalizeTableRows(request.rows);
  } else {
    fail("template_v2_chart_copilot_invalid_import", "import.format");
  }
  if (rows.length < 2 || rows[0].length < 2) {
    fail("template_v2_chart_copilot_invalid_import", "import.shape");
  }
  const width = rows[0].length;
  if (
    width - 1 > TEMPLATE_V2_CHART_COPILOT_LIMITS.maxSeries ||
    rows.length - 1 > TEMPLATE_V2_CHART_COPILOT_LIMITS.maxCategories
  ) {
    fail("template_v2_chart_copilot_limit_exceeded", "import.shape");
  }
  if (rows.some((row) => row.length !== width)) {
    fail("template_v2_chart_copilot_invalid_import", "import.rectangular");
  }
  const names = rows[0].slice(1).map((name, index) =>
    boundedString(
      name.trim(),
      `import.series.${index}.name`,
      TEMPLATE_V2_CHART_COPILOT_LIMITS.maxSeriesNameCharacters,
      { allowEmpty: false },
    ),
  );
  const categories = rows.slice(1).map((row) => row[0].trim());
  const series = names.map((name, seriesIndex) => ({
    name,
    values: rows.slice(1).map((row, categoryIndex) => {
      const cell = row[seriesIndex + 1].trim();
      if (cell.length === 0) {
        fail(
          "template_v2_chart_copilot_invalid_import",
          `import.rows.${categoryIndex + 1}.${seriesIndex + 1}`,
        );
      }
      const value = Number(cell);
      if (!Number.isFinite(value)) {
        fail(
          "template_v2_chart_copilot_invalid_import",
          `import.rows.${categoryIndex + 1}.${seriesIndex + 1}`,
        );
      }
      return value;
    }),
  }));
  return { categories, series };
}

const DATE_LIKE =
  /^(?:(?:19|20)\d{2}(?:[-/.](?:0?[1-9]|1[0-2]))?|Q[1-4](?:\s*(?:19|20)?\d{2})?|(?:19|20)?\d{2}\s*Q[1-4])$/i;

export function recommendTemplateV2Chart(
  chartValue: unknown,
): TemplateV2ChartRecommendation {
  validateTemplateV2ChartCopilotChart(chartValue);
  const chart = chartValue;
  if (
    chart.categories.length >= 3 &&
    chart.categories.every((category) => DATE_LIKE.test(category.trim()))
  ) {
    return { chartType: "line", reasonCode: "time_series", confidence: "high" };
  }
  if (
    chart.categories.length > 8 ||
    chart.categories.some((category) => category.length > 18)
  ) {
    return {
      chartType: "horizontal_bar",
      reasonCode: "long_category_labels",
      confidence: "high",
    };
  }
  if (
    chart.series.length === 1 &&
    chart.categories.length >= 2 &&
    chart.categories.length <= 6 &&
    chart.series[0].values.every((value) => value >= 0) &&
    chart.series[0].values.some((value) => value > 0)
  ) {
    return { chartType: "pie", reasonCode: "part_to_whole", confidence: "medium" };
  }
  if (chart.series.length > 1) {
    return {
      chartType: "bar",
      reasonCode: "multi_series_comparison",
      confidence: "medium",
    };
  }
  return {
    chartType: "bar",
    reasonCode: "single_series_comparison",
    confidence: "medium",
  };
}

/**
 * The provider is injectable by design: this module never discovers credentials,
 * performs fetches, or selects an external endpoint. Missing providers and
 * malformed proposals fail closed.
 */
export async function requestTemplateV2ChartCopilotCandidates(
  chartValue: unknown,
  provider?: TemplateV2ChartCopilotProvider,
): Promise<TemplateV2ChartCopilotCandidate[]> {
  validateTemplateV2ChartCopilotChart(chartValue);
  if (!provider || typeof provider.recommend !== "function") {
    fail("template_v2_chart_copilot_provider_unavailable", "provider");
  }
  let response: unknown;
  try {
    response = await provider.recommend({
      chart: chartValue,
      allowedChartTypes: TEMPLATE_V2_CHART_TYPES,
      maxCandidates: TEMPLATE_V2_CHART_COPILOT_LIMITS.maxCandidateCount,
    });
  } catch {
    fail("template_v2_chart_copilot_provider_unavailable", "provider");
  }
  if (
    !Array.isArray(response) ||
    response.length < 1 ||
    response.length > TEMPLATE_V2_CHART_COPILOT_LIMITS.maxCandidateCount
  ) {
    fail(
      "template_v2_chart_copilot_invalid_provider_response",
      "provider.candidates",
    );
  }
  const ids = new Set<string>();
  return response.map((candidate, index) => {
    if (
      !isRecord(candidate) ||
      !hasExactKeys(candidate, ["id", "label", "operations"]) ||
      typeof candidate.id !== "string" ||
      !/^[a-z0-9][a-z0-9_-]{0,63}$/.test(candidate.id) ||
      ids.has(candidate.id) ||
      typeof candidate.label !== "string" ||
      candidate.label.length < 1 ||
      candidate.label.length > 120 ||
      !Array.isArray(candidate.operations)
    ) {
      fail(
        "template_v2_chart_copilot_invalid_provider_response",
        `provider.candidates.${index}`,
      );
    }
    ids.add(candidate.id);
    let preview: TemplateV2ChartCopilotPreview;
    try {
      preview = previewTemplateV2ChartCopilot(chartValue, candidate.operations);
    } catch {
      fail(
        "template_v2_chart_copilot_invalid_provider_response",
        `provider.candidates.${index}.operations`,
      );
    }
    return { id: candidate.id, label: candidate.label, preview };
  });
}
