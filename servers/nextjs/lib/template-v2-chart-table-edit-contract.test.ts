import assert from "node:assert/strict";
import test from "node:test";

import {
  buildChartContractEditPatches,
  buildTableContractEditPatches,
  validateChartContract,
  validateTableContract,
  type TemplateV2ContractPatch,
} from "./template-v2-chart-table-contract.ts";

function applyPatches(
  source: Record<string, unknown>,
  patches: TemplateV2ContractPatch[],
) {
  const candidate = structuredClone(source);
  for (const patch of patches) {
    let cursor: Record<string | number, unknown> = candidate;
    for (const [index, segment] of patch.path.slice(0, -1).entries()) {
      const existing = cursor[segment];
      if (existing === null || typeof existing !== "object") {
        cursor[segment] =
          typeof patch.path[index + 1] === "number"
            ? []
            : {};
      }
      cursor = cursor[segment] as Record<string | number, unknown>;
    }
    cursor[patch.path.at(-1) as string | number] = patch.value;
  }
  return candidate;
}

function chart() {
  return {
    type: "chart",
    chart_type: "bar",
    categories: ["Q1", "Q2"],
    series: [
      {
        name: "Revenue",
        values: [10, 20],
        extension: { retained: true },
      },
    ],
    x_axis: true,
    axis_color: "#334155",
    data_labels: "top",
    extension: { retained: true },
  };
}

function table() {
  return {
    type: "table",
    columns: [
      {
        runs: [{ text: "A", font: { bold: true } }],
        extension: { retained: true },
      },
      { runs: [{ text: "B" }] },
    ],
    rows: [
      [
        { runs: [{ text: "1" }] },
        { runs: [{ text: "2" }] },
      ],
    ],
    min_columns: 1,
    max_columns: 3,
    min_rows: 0,
    max_rows: 2,
    extension: { retained: true },
  };
}

test("chart data edits validate exact existing targets without mutating input", () => {
  const source = chart();
  const snapshot = structuredClone(source);
  const category = buildChartContractEditPatches(source, {
    kind: "category-label",
    categoryIndex: 1,
    value: "Second quarter",
  });
  assert.equal(category.ok, true);
  if (!category.ok) return;
  assert.deepEqual(source, snapshot);

  const named = buildChartContractEditPatches(
    applyPatches(source, category.value),
    { kind: "series-name", seriesIndex: 0, value: "Net revenue" },
  );
  assert.equal(named.ok, true);
  if (!named.ok) return;
  const valued = buildChartContractEditPatches(
    applyPatches(applyPatches(source, category.value), named.value),
    {
      kind: "series-value",
      seriesIndex: 0,
      categoryIndex: 0,
      value: 42.5,
    },
  );
  assert.equal(valued.ok, true);
  if (!valued.ok) return;
  const result = applyPatches(
    applyPatches(applyPatches(source, category.value), named.value),
    valued.value,
  );
  assert.equal(validateChartContract(result).ok, true);
  assert.deepEqual(result.extension, { retained: true });
  assert.deepEqual(
    (result.series as Array<Record<string, unknown>>)[0].extension,
    { retained: true },
  );
});

test("chart type edits clear only proven stale state and reject unsafe pie migration", () => {
  const source = chart();
  const changed = buildChartContractEditPatches(source, {
    kind: "chart-type",
    value: "donut",
  });
  assert.equal(changed.ok, true);
  if (!changed.ok) return;
  assert.deepEqual(
    changed.value.map((patch) => patch.path[0]),
    ["chart_type", "x_axis", "axis_color", "data_labels"],
  );
  assert.equal(validateChartContract(applyPatches(source, changed.value)).ok, true);

  const staleLabel = chart();
  staleLabel.data_labels = "mid";
  const line = buildChartContractEditPatches(staleLabel, {
    kind: "chart-type",
    value: "line",
  });
  assert.equal(line.ok, true);
  if (!line.ok) return;
  assert.deepEqual(
    line.value.map((patch) => patch.path[0]),
    ["chart_type", "data_labels"],
  );
  assert.equal(validateChartContract(applyPatches(staleLabel, line.value)).ok, true);

  const unsafe = chart();
  unsafe.series.push({ name: "Cost", values: [5, 8], extension: { retained: true } });
  assert.equal(
    buildChartContractEditPatches(unsafe, {
      kind: "chart-type",
      value: "pie",
    }).ok,
    false,
  );
});

test("chart label and count edits keep series rectangular and preserve metadata", () => {
  const source = chart();
  const labelled = buildChartContractEditPatches(source, {
    kind: "data-labels",
    value: "outside",
  });
  assert.equal(labelled.ok, true);
  if (!labelled.ok) return;
  assert.deepEqual(labelled.value, [
    { path: ["data_labels"], value: "outside" },
  ]);

  const expanded = buildChartContractEditPatches(source, {
    kind: "category-count",
    value: 3,
  });
  assert.equal(expanded.ok, true);
  if (!expanded.ok) return;
  const expandedChart = applyPatches(source, expanded.value);
  assert.deepEqual(expandedChart.categories, ["Q1", "Q2", "Category 3"]);
  assert.deepEqual(
    (expandedChart.series as Array<Record<string, unknown>>)[0].values,
    [10, 20, 0],
  );
  assert.deepEqual(
    (expandedChart.series as Array<Record<string, unknown>>)[0].extension,
    { retained: true },
  );
  assert.equal(validateChartContract(expandedChart).ok, true);

  const moreSeries = buildChartContractEditPatches(expandedChart, {
    kind: "series-count",
    value: 2,
  });
  assert.equal(moreSeries.ok, true);
  if (!moreSeries.ok) return;
  const result = applyPatches(expandedChart, moreSeries.value);
  assert.deepEqual(
    (result.series as Array<Record<string, unknown>>)[1],
    { name: "Series 2", values: [0, 0, 0] },
  );
  assert.equal(validateChartContract(result).ok, true);
});

test("chart count and data-label edits reject capability and shape violations", () => {
  assert.deepEqual(
    buildChartContractEditPatches(chart(), {
      kind: "data-labels",
      value: "unsupported",
    }),
    { ok: false, reason: "invalid_chart_data_labels_edit" },
  );
  assert.deepEqual(
    buildChartContractEditPatches(
      {
        ...chart(),
        chart_type: "pie",
        x_axis: null,
        axis_color: null,
        data_labels: null,
      },
      { kind: "series-count", value: 2 },
    ),
    { ok: false, reason: "invalid_chart_series_count_edit" },
  );
  assert.deepEqual(
    buildChartContractEditPatches(chart(), {
      kind: "category-count",
      value: 25,
    }),
    { ok: false, reason: "invalid_chart_category_count_edit" },
  );
});

test("table text and resize edits preserve existing metadata and rectangularity", () => {
  const source = table();
  const snapshot = structuredClone(source);
  const text = buildTableContractEditPatches(source, {
    kind: "run-text",
    section: "header",
    columnIndex: 0,
    runIndex: 0,
    value: "Account",
  });
  assert.equal(text.ok, true);
  if (!text.ok) return;
  assert.deepEqual(source, snapshot);

  const wider = buildTableContractEditPatches(source, {
    kind: "column-count",
    value: 3,
  });
  assert.equal(wider.ok, true);
  if (!wider.ok) return;
  const wideTable = applyPatches(source, wider.value);
  assert.equal(validateTableContract(wideTable).ok, true);
  assert.deepEqual(
    (wideTable.columns as Array<Record<string, unknown>>)[0].extension,
    { retained: true },
  );
  assert.deepEqual(
    (wideTable.columns as Array<Record<string, unknown>>)[2],
    { runs: [{ text: "" }] },
  );

  const taller = buildTableContractEditPatches(wideTable, {
    kind: "row-count",
    value: 2,
  });
  assert.equal(taller.ok, true);
  if (!taller.ok) return;
  assert.equal(
    validateTableContract(applyPatches(wideTable, taller.value)).ok,
    true,
  );
});

test("table resize fails closed at declared or imported exact bounds", () => {
  const exact = table();
  exact.min_columns = exact.max_columns = exact.columns.length;
  exact.min_rows = exact.max_rows = exact.rows.length;
  assert.equal(
    buildTableContractEditPatches(exact, {
      kind: "column-count",
      value: exact.columns.length + 1,
    }).ok,
    false,
  );
  assert.equal(
    buildTableContractEditPatches(exact, {
      kind: "row-count",
      value: exact.rows.length - 1,
    }).ok,
    false,
  );
});

test("table cell font edits create canonical font objects without losing cell metadata", () => {
  const source = table();
  const changed = buildTableContractEditPatches(source, {
    kind: "cell-font",
    section: "body",
    rowIndex: 0,
    columnIndex: 1,
    field: "color",
    value: "#dc2626",
  });
  assert.equal(changed.ok, true);
  if (!changed.ok) return;
  assert.deepEqual(changed.value, [
    {
      path: ["rows", 0, 1, "font", "color"],
      value: "#dc2626",
    },
  ]);
  const result = applyPatches(source, changed.value);
  assert.equal(validateTableContract(result).ok, true);
  assert.deepEqual(
    ((result.rows as Array<Array<Record<string, unknown>>>)[0][1].font),
    { color: "#dc2626" },
  );
  assert.deepEqual(result.extension, { retained: true });

  assert.deepEqual(
    buildTableContractEditPatches(source, {
      kind: "cell-font",
      section: "body",
      rowIndex: 0,
      columnIndex: 1,
      field: "bold",
      value: "true",
    }),
    { ok: false, reason: "invalid_table_font_edit" },
  );
});
