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
    };

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
  return updateTableCell(element, target, text);
}
