import assert from "node:assert/strict";
import test from "node:test";

import {
  chartContractCapabilities,
  normalizeChartContract,
  normalizeTableContract,
  validateChartContract,
  validateTableContract,
} from "./template-v2-chart-table-contract.ts";

const cell = (text: string, extension?: unknown) => ({
  runs: [
    extension === undefined
      ? { text }
      : { text, run_extension: extension },
  ],
  ...(extension === undefined ? {} : { cell_extension: extension }),
});

test("chart validation is non-mutating and preserves unknown JSON recursively", () => {
  const source = {
    type: "chart",
    chart_type: "bar",
    categories: ["Q1"],
    series: [
      {
        name: "Revenue",
        values: [10],
        series_extension: { unit: "USD" },
      },
    ],
    data_labels: "mid",
    future_chart: { enabled: true },
  };
  const result = validateChartContract(source);

  assert.equal(result.ok, true);
  if (!result.ok) return;
  assert.deepEqual(result.value, source);
  assert.notEqual(result.value, source);
  assert.notEqual(result.value.future_chart, source.future_chart);
});

test("intentional chart type migration removes only stale known options", () => {
  const source = {
    type: "chart",
    chart_type: "radar",
    categories: ["Q1"],
    series: [{ name: "Revenue", values: [10] }],
    x_axis: true,
    y_axis_grid: false,
    data_labels: "mid",
    future_axis_policy: { retained: true },
  };
  const result = normalizeChartContract(source, { previousType: "bar" });

  assert.equal(result.ok, true);
  if (!result.ok) return;
  assert.equal("x_axis" in result.value, false);
  assert.equal("y_axis_grid" in result.value, false);
  assert.equal("data_labels" in result.value, false);
  assert.deepEqual(result.value.future_axis_policy, { retained: true });
  assert.equal(source.x_axis, true);
});

test("stale chart options fail closed without explicit transition evidence", () => {
  assert.deepEqual(
    validateChartContract({
      type: "chart",
      chart_type: "radar",
      x_axis: true,
    }),
    { ok: false, reason: "stale_chart_axis_options" },
  );
  assert.deepEqual(
    normalizeChartContract({
      type: "chart",
      chart_type: "line",
      data_labels: "mid",
    }),
    { ok: false, reason: "stale_chart_data_labels" },
  );
});

test("legacy boolean labels migrate but lossy series conversion is refused", () => {
  const legacy = normalizeChartContract({
    type: "chart",
    chart_type: "bar",
    data_labels: true,
  });
  assert.equal(legacy.ok, true);
  if (legacy.ok) assert.equal(legacy.value.data_labels, "top");

  assert.deepEqual(
    normalizeChartContract(
      {
        type: "chart",
        chart_type: "pie",
        categories: ["A"],
        series: [
          { name: "One", values: [1] },
          { name: "Two", values: [2] },
        ],
      },
      { previousType: "bar" },
    ),
    { ok: false, reason: "chart_series_limit" },
  );
});

test("capabilities mirror the fail-closed export option matrix", () => {
  const line = chartContractCapabilities("line");
  const bar = chartContractCapabilities("bar");
  const pie = chartContractCapabilities("pie");
  assert.deepEqual(
    line.ok ? line.value.dataLabelPositions : null,
    ["top", "outside"],
  );
  assert.deepEqual(
    bar.ok ? bar.value.dataLabelPositions : null,
    ["base", "mid", "top", "outside"],
  );
  assert.deepEqual(
    pie.ok ? pie.value : null,
    { axisOptions: false, dataLabelPositions: [], multipleSeries: false },
  );
});

test("table rows are body rows and bounds do not include the header", () => {
  const source = {
    type: "table",
    columns: [cell("Header 1"), cell("Header 2")],
    rows: [[cell("A"), cell("B")]],
    min_columns: 2,
    max_columns: 2,
    min_rows: 1,
    max_rows: 1,
  };
  assert.equal(validateTableContract(source).ok, true);
  assert.deepEqual(
    validateTableContract({ ...source, min_rows: 2, max_rows: 2 }),
    { ok: false, reason: "table_body_row_count_out_of_bounds" },
  );
});

test("missing table bounds migrate to exact fail-closed body dimensions", () => {
  const source = {
    type: "table",
    columns: [cell("Header", { role: "dimension" })],
    rows: [[cell("Body", { source: "crm" })]],
    future_table: { retained: true },
  };
  const result = normalizeTableContract(source);

  assert.equal(result.ok, true);
  if (!result.ok) return;
  assert.deepEqual(
    {
      min_columns: result.value.min_columns,
      max_columns: result.value.max_columns,
      min_rows: result.value.min_rows,
      max_rows: result.value.max_rows,
    },
    { min_columns: 1, max_columns: 1, min_rows: 1, max_rows: 1 },
  );
  assert.deepEqual(result.value.future_table, { retained: true });
  assert.equal("min_rows" in source, false);
});

test("partial bounds and non-rectangular tables fail closed", () => {
  assert.deepEqual(
    normalizeTableContract({
      type: "table",
      columns: [cell("Header")],
      rows: [[cell("Body")]],
      min_rows: 0,
    }),
    { ok: false, reason: "partial_table_bounds" },
  );
  assert.deepEqual(
    normalizeTableContract({
      type: "table",
      columns: [cell("One"), cell("Two")],
      rows: [[cell("Only one")]],
    }),
    { ok: false, reason: "table_not_rectangular" },
  );
});

test("table fonts validate known fields while unsupported cell borders fail closed", () => {
  const source = {
    type: "table",
    columns: [
      {
        runs: [{ text: "Header", font: { bold: true } }],
        font: {
          family: "Arial",
          size: 18,
          color: "#111827",
          extension: { retained: true },
        },
      },
    ],
    rows: [[cell("Body")]],
    min_columns: 1,
    max_columns: 1,
    min_rows: 1,
    max_rows: 1,
  };
  assert.equal(validateTableContract(source).ok, true);
  assert.deepEqual(
    validateTableContract({
      ...source,
      columns: [{ ...source.columns[0], font: { bold: "yes" } }],
    }),
    { ok: false, reason: "invalid_table_columns" },
  );
  assert.deepEqual(
    validateTableContract({
      ...source,
      columns: [{ ...source.columns[0], stroke: { color: "#000", width: 1 } }],
    }),
    { ok: false, reason: "invalid_table_columns" },
  );
  assert.deepEqual(
    validateTableContract({
      ...source,
      columns: [{ ...source.columns[0], border: { color: "#000", width: 1 } }],
    }),
    { ok: false, reason: "invalid_table_columns" },
  );
  assert.deepEqual(
    validateTableContract({
      ...source,
      rows: [[{ ...source.rows[0][0], stroke: "#000" }]],
    }),
    { ok: false, reason: "invalid_table_rows" },
  );
});
