import assert from "node:assert/strict";
import test from "node:test";

import {
  TEMPLATE_V2_TABLE_HARD_LIMITS,
  applyTemplateV2TablePreview,
  applyTemplateV2TableToChartPreview,
  previewTemplateV2DelimitedImport,
  previewTemplateV2DelimitedPaste,
  previewTemplateV2TableOperation,
  previewTemplateV2TableToChart,
  suggestTemplateV2LongTableSplit,
} from "./template-v2-table-operations.ts";

function cell(text: string, metadata: Record<string, unknown> = {}) {
  return {
    runs: [
      {
        text,
        font: { bold: true },
        future_run_field: { retained: text },
      },
    ],
    alignment: "right",
    color: { color: "#ffffff", opacity: 0.75 },
    future_cell_field: metadata,
  };
}

function table() {
  return {
    type: "table",
    name: "Revenue",
    decorative: false,
    min_rows: 1,
    max_rows: 20,
    min_columns: 2,
    max_columns: 8,
    position: { x: 20, y: 30 },
    size: { width: 500, height: 280 },
    columns: [cell("Quarter", { id: "h1" }), cell("Revenue", { id: "h2" })],
    rows: [
      [cell("Q1", { id: "a1" }), cell("42", { id: "a2" })],
      [cell("Q2", { id: "b1" }), cell("55", { id: "b2" })],
    ],
    future_table_field: { retained: true },
  };
}

function ready<T>(result: { ok: true; value: T } | { ok: false }): T {
  assert.equal(result.ok, true);
  return (result as { ok: true; value: T }).value;
}

test("inserts, deletes and reorders rows and columns without losing metadata", () => {
  const source = table();
  const insertedRow = ready(
    previewTemplateV2TableOperation(source, {
      type: "insert-row",
      index: 1,
      values: ["Q1.5", "48"],
    }),
  );
  const rowApplied = ready(applyTemplateV2TablePreview(source, insertedRow));
  assert.deepEqual(rowApplied.future_table_field, { retained: true });
  assert.deepEqual(
    (rowApplied.rows as Record<string, unknown>[][])[1][1].future_cell_field,
    { id: "b2" },
  );
  assert.deepEqual(
    ((rowApplied.rows as any[][])[1][1].runs as any[])[0].future_run_field,
    { retained: "55" },
  );
  assert.equal(((rowApplied.rows as any[][])[1][1].runs as any[])[0].text, "48");

  const insertedColumn = ready(
    previewTemplateV2TableOperation(rowApplied, {
      type: "insert-column",
      index: 1,
      header: "Owner",
      values: ["A", "B", "C"],
    }),
  );
  const columnApplied = ready(
    applyTemplateV2TablePreview(rowApplied, insertedColumn),
  );
  assert.equal((columnApplied.columns as any[]).length, 3);
  assert.equal(((columnApplied.columns as any[])[1].runs as any[])[0].text, "Owner");
  assert.deepEqual(
    (columnApplied.columns as any[])[1].color,
    (source.columns as any[])[1].color,
  );

  const moved = ready(
    previewTemplateV2TableOperation(columnApplied, {
      type: "move-column",
      from: 2,
      to: 0,
    }),
  );
  const movedApplied = ready(applyTemplateV2TablePreview(columnApplied, moved));
  assert.equal(((movedApplied.columns as any[])[0].runs as any[])[0].text, "Revenue");
  assert.equal(((movedApplied.rows as any[][])[0][0].runs as any[])[0].text, "42");

  const deleted = ready(
    previewTemplateV2TableOperation(movedApplied, {
      type: "delete-row",
      index: 1,
    }),
  );
  assert.equal(ready(applyTemplateV2TablePreview(movedApplied, deleted)).rows instanceof Array, true);
});

test("header conversion and transpose preserve every cell object and table metadata", () => {
  const source = table();
  const promoted = ready(
    previewTemplateV2TableOperation(source, {
      type: "promote-first-row-to-header",
    }),
  );
  const promotedTable = ready(applyTemplateV2TablePreview(source, promoted));
  assert.equal(((promotedTable.columns as any[])[0].runs as any[])[0].text, "Q1");
  assert.equal(((promotedTable.rows as any[][])[0][0].runs as any[])[0].text, "Quarter");
  assert.deepEqual((promotedTable.columns as any[])[0].future_cell_field, {
    id: "a1",
  });

  const transposed = ready(
    previewTemplateV2TableOperation(source, { type: "transpose" }),
  );
  const transposedTable = ready(
    applyTemplateV2TablePreview(source, transposed),
  );
  assert.deepEqual(
    (transposedTable.columns as any[]).map((item) => item.future_cell_field.id),
    ["h1", "a1", "b1"],
  );
  assert.deepEqual(
    (transposedTable.rows as any[][])[0].map(
      (item) => item.future_cell_field.id,
    ),
    ["h2", "a2", "b2"],
  );
  assert.deepEqual(transposedTable.future_table_field, { retained: true });

  const demoted = ready(
    previewTemplateV2TableOperation(source, {
      type: "demote-header-to-first-row",
    }),
  );
  const demotedTable = ready(applyTemplateV2TablePreview(source, demoted));
  assert.deepEqual(
    (demotedTable.rows as any[][])[0].map((item) => item.future_cell_field.id),
    ["h1", "h2"],
  );
  assert.deepEqual(
    (demotedTable.columns as any[]).map((item) => item.future_cell_field.id),
    ["h1", "h2"],
  );
  assert.deepEqual(
    (demotedTable.columns as any[])[0].color,
    (source.columns as any[])[0].color,
  );
});

test("CSV and TSV import are quote-aware, bounded and preserve style prototypes", () => {
  const source = table();
  const csv = ready(
    previewTemplateV2DelimitedImport(
      source,
      'Region,Revenue\n"North, East",12\nSouth,18',
    ),
  );
  const imported = ready(applyTemplateV2TablePreview(source, csv));
  assert.equal(((imported.rows as any[][])[0][0].runs as any[])[0].text, "North, East");
  assert.deepEqual(
    (imported.rows as any[][])[0][0].future_cell_field,
    (source.rows as any[][])[0][0].future_cell_field,
  );
  assert.deepEqual(
    (imported.rows as any[][])[0][0].color,
    (source.rows as any[][])[0][0].color,
  );

  const tsv = ready(
    previewTemplateV2DelimitedPaste(source, "Q3\t61", {
      startRow: 1,
      startColumn: 0,
    }),
  );
  const pasted = ready(applyTemplateV2TablePreview(source, tsv));
  assert.equal(((pasted.rows as any[][])[1][0].runs as any[])[0].text, "Q3");
  assert.deepEqual(
    ((pasted.rows as any[][])[1][0].runs as any[])[0].future_run_field,
    { retained: "Q2" },
  );

  const headerPaste = ready(
    previewTemplateV2DelimitedPaste(source, "Period\tAmount", {
      startRow: -1,
      startColumn: 0,
      includeHeader: true,
    }),
  );
  const headerPasted = ready(
    applyTemplateV2TablePreview(source, headerPaste),
  );
  assert.deepEqual(
    (headerPasted.columns as any[]).map((item) => item.runs[0].text),
    ["Period", "Amount"],
  );
  assert.deepEqual(
    (headerPasted.columns as any[])[0].future_cell_field,
    { id: "h1" },
  );

  assert.deepEqual(
    previewTemplateV2DelimitedImport(source, "a,b\n1", {}),
    { ok: false, reasonCode: "table_import_ragged_rows" },
  );
  assert.deepEqual(
    previewTemplateV2DelimitedPaste(source, "1\t2\t3", {
      startRow: 0,
      startColumn: 0,
    }),
    { ok: false, reasonCode: "table_paste_out_of_bounds" },
  );
  assert.deepEqual(
    previewTemplateV2DelimitedImport(
      source,
      `A,B\n${"x".repeat(TEMPLATE_V2_TABLE_HARD_LIMITS.maxCellCharacters + 1)},1`,
    ),
    {
      ok: false,
      reasonCode: "table_import_cell_too_large",
      path: "rows.1.0",
      limit: TEMPLATE_V2_TABLE_HARD_LIMITS.maxCellCharacters,
    },
  );
});

test("preview apply rejects stale and tampered structural changes", () => {
  const source = table();
  const preview = ready(
    previewTemplateV2TableOperation(source, {
      type: "move-row",
      from: 0,
      to: 1,
    }),
  );
  const stale = {
    ...source,
    rows: [
      [cell("Q1", { id: "a1" }), cell("43", { id: "a2" })],
      source.rows[1],
    ],
  };
  assert.deepEqual(applyTemplateV2TablePreview(stale, preview), {
    ok: false,
    reasonCode: "table_preview_stale",
  });
  preview.after.rows = [];
  assert.deepEqual(applyTemplateV2TablePreview(source, preview), {
    ok: false,
    reasonCode: "table_preview_tampered",
  });
});

test("table-to-chart has a separate preview/apply gate and rejects invalid data", () => {
  const source = table();
  const preview = ready(
    previewTemplateV2TableToChart(source, {
      chartType: "bar",
      title: "Quarterly revenue",
    }),
  );
  assert.equal(preview.diff.categories, 2);
  assert.equal(preview.diff.series, 1);
  assert.equal(source.type, "table");

  const chart = ready(applyTemplateV2TableToChartPreview(source, preview));
  assert.deepEqual(chart, {
    type: "chart",
    chart_type: "bar",
    categories: ["Q1", "Q2"],
    series: [{ name: "Revenue", values: [42, 55] }],
    decorative: false,
    name: "Revenue chart",
    position: { x: 20, y: 30 },
    size: { width: 500, height: 280 },
    title: "Quarterly revenue",
  });

  const pieSource = table();
  const addedSeries = ready(
    previewTemplateV2TableOperation(pieSource, {
      type: "insert-column",
      index: 2,
      header: "Forecast",
      values: ["45", "60"],
    }),
  );
  const threeColumnTable = ready(
    applyTemplateV2TablePreview(pieSource, addedSeries),
  );
  assert.deepEqual(
    previewTemplateV2TableToChart(threeColumnTable, { chartType: "pie" }),
    { ok: false, reasonCode: "table_chart_incompatible_type" },
  );
  const invalid = table();
  (invalid.rows as any[][])[0][1].runs[0].text = "not-a-number";
  assert.deepEqual(previewTemplateV2TableToChart(invalid), {
    ok: false,
    reasonCode: "table_chart_non_numeric_value",
    path: "rows.0.1",
  });
});

test("long-table split suggestion is deterministic and does not clone a presentation", () => {
  const source = table();
  source.max_rows = 40;
  source.rows = Array.from({ length: 25 }, (_, index) => [
    cell(`Q${index + 1}`),
    cell(String(index)),
  ]);
  assert.deepEqual(ready(suggestTemplateV2LongTableSplit(source, 10)), {
    kind: "template-v2-long-table-split-suggestion",
    reasonCode: "table_rows_exceed_readable_page",
    totalRows: 25,
    maxRowsPerSlide: 10,
    repeatHeader: true,
    segments: [
      { index: 0, startRow: 0, endRowExclusive: 10 },
      { index: 1, startRow: 10, endRowExclusive: 20 },
      { index: 2, startRow: 20, endRowExclusive: 25 },
    ],
  });
  assert.equal(ready(suggestTemplateV2LongTableSplit(table(), 12)), null);
});

test("declared row, column and hard cell limits fail closed", () => {
  const source = table();
  source.max_rows = 2;
  assert.deepEqual(
    previewTemplateV2TableOperation(source, { type: "insert-row", index: 2 }),
    { ok: false, reasonCode: "table_max_rows_exceeded", limit: 2 },
  );
  source.min_columns = 2;
  assert.deepEqual(
    previewTemplateV2TableOperation(source, {
      type: "delete-column",
      index: 1,
    }),
    { ok: false, reasonCode: "table_min_columns_exceeded" },
  );
});
