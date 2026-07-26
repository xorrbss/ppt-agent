export type JsonRecord = Record<string, unknown>;

export type TemplateV2RunTarget =
  | { kind: "text"; runIndex: number }
  | { kind: "list-item"; itemIndex: number; runIndex: number }
  | { kind: "table-column"; columnIndex: number; runIndex: number }
  | {
      kind: "table-cell";
      rowIndex: number;
      columnIndex: number;
      runIndex: number;
    }
  | { kind: "chart-title" }
  | { kind: "chart-category"; categoryIndex: number }
  | { kind: "chart-series-name"; seriesIndex: number }
  | { kind: "chart-series-value"; seriesIndex: number; valueIndex: number }
  | { kind: "asset-data" }
  | { kind: "asset-fit" };

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function updateRun(
  value: unknown,
  runIndex: number,
  text: string,
): unknown[] | null {
  if (!Array.isArray(value) || !Number.isInteger(runIndex) || runIndex < 0) {
    return null;
  }
  const run = value[runIndex];
  if (!isRecord(run) || run.text === text) return null;
  const runs = value.slice();
  runs[runIndex] = { ...run, text };
  return runs;
}

function updateListItem(
  element: JsonRecord,
  target: Extract<TemplateV2RunTarget, { kind: "list-item" }>,
  text: string,
): JsonRecord {
  if (element.type !== "text-list" || !Array.isArray(element.items)) {
    return element;
  }
  const runs = updateRun(
    element.items[target.itemIndex],
    target.runIndex,
    text,
  );
  if (!runs) return element;
  const items = element.items.slice();
  items[target.itemIndex] = runs;
  return { ...element, items };
}

function updateTableCell(
  element: JsonRecord,
  target: Extract<TemplateV2RunTarget, { kind: "table-column" | "table-cell" }>,
  text: string,
): JsonRecord {
  if (element.type !== "table") return element;
  if (target.kind === "table-column") {
    if (!Array.isArray(element.columns)) return element;
    const column = element.columns[target.columnIndex];
    if (!isRecord(column)) return element;
    const runs = updateRun(column.runs, target.runIndex, text);
    if (!runs) return element;
    const columns = element.columns.slice();
    columns[target.columnIndex] = { ...column, runs };
    return { ...element, columns };
  }

  if (!Array.isArray(element.rows)) return element;
  const row = element.rows[target.rowIndex];
  if (!Array.isArray(row)) return element;
  const cell = row[target.columnIndex];
  if (!isRecord(cell)) return element;
  const runs = updateRun(cell.runs, target.runIndex, text);
  if (!runs) return element;
  const rows = element.rows.slice();
  const cells = row.slice();
  cells[target.columnIndex] = { ...cell, runs };
  rows[target.rowIndex] = cells;
  return { ...element, rows };
}

function updateArrayValue(
  value: unknown,
  index: number,
  nextValue: string | number,
): unknown[] | null {
  if (
    !Array.isArray(value) ||
    !Number.isInteger(index) ||
    index < 0 ||
    index >= value.length ||
    value[index] === nextValue
  ) {
    return null;
  }
  const next = value.slice();
  next[index] = nextValue;
  return next;
}

function updateChart(
  element: JsonRecord,
  target: Extract<TemplateV2RunTarget, { kind: `chart-${string}` }>,
  text: string,
): JsonRecord {
  if (element.type !== "chart") return element;
  if (target.kind === "chart-title") {
    return element.title === text ? element : { ...element, title: text };
  }
  if (target.kind === "chart-category") {
    const categories = updateArrayValue(
      element.categories,
      target.categoryIndex,
      text,
    );
    return categories ? { ...element, categories } : element;
  }
  if (!Array.isArray(element.series)) return element;
  const series = element.series[target.seriesIndex];
  if (!isRecord(series)) return element;
  let nextSeries: JsonRecord = series;
  if (target.kind === "chart-series-name") {
    if (series.name === text) return element;
    nextSeries = { ...series, name: text };
  } else {
    const normalized = text.trim();
    if (!normalized) return element;
    const numericValue = Number(normalized);
    if (!Number.isFinite(numericValue)) return element;
    const values = updateArrayValue(
      series.values,
      target.valueIndex,
      numericValue,
    );
    if (!values) return element;
    nextSeries = { ...series, values };
  }
  const nextSeriesList = element.series.slice();
  nextSeriesList[target.seriesIndex] = nextSeries;
  return { ...element, series: nextSeriesList };
}

export function isSafeTemplateV2AssetSource(source: string): boolean {
  if (
    source.startsWith("/") &&
    !source.startsWith("//") &&
    !source.startsWith("/\\") &&
    !/[\u0000-\u001f\u007f\\]/.test(source)
  ) {
    return true;
  }
  return /^data:image\/(?:png|jpeg|webp|gif);base64,/i.test(source) ||
    /^data:image\/svg\+xml(?:;base64)?,/i.test(source);
}

function updateAsset(
  element: JsonRecord,
  target: Extract<TemplateV2RunTarget, { kind: `asset-${string}` }>,
  text: string,
): JsonRecord {
  if (element.type !== "image") return element;
  if (target.kind === "asset-data") {
    if (!isSafeTemplateV2AssetSource(text) || element.data === text) {
      return element;
    }
    return { ...element, data: text };
  }
  if (!["fill", "contain", "cover"].includes(text) || element.fit === text) {
    return element;
  }
  return { ...element, fit: text };
}

export function updateTemplateV2ContentRun(
  element: JsonRecord,
  target: TemplateV2RunTarget,
  text: string,
): JsonRecord {
  if (target.kind === "text") {
    if (element.type !== "text") return element;
    const runs = updateRun(element.runs, target.runIndex, text);
    return runs ? { ...element, runs } : element;
  }
  if (target.kind === "list-item") {
    return updateListItem(element, target, text);
  }
  if (target.kind === "table-column" || target.kind === "table-cell") {
    return updateTableCell(element, target, text);
  }
  if (target.kind.startsWith("chart-")) {
    return updateChart(
      element,
      target as Extract<TemplateV2RunTarget, { kind: `chart-${string}` }>,
      text,
    );
  }
  return updateAsset(
    element,
    target as Extract<TemplateV2RunTarget, { kind: `asset-${string}` }>,
    text,
  );
}
