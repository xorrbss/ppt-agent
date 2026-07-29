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
    for (const segment of patch.path.slice(0, -1)) {
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
