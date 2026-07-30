import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  renderTemplateV2GeneralSlideCanvasHtml,
} from "./template-v2-general-renderer.mjs";
import {
  buildChartContractEditPatches,
  buildTableContractEditPatches,
  validateChartContract,
  validateTableContract,
  type TemplateV2ContractPatch,
} from "./template-v2-chart-table-contract.ts";
import {
  adaptUpstreamTemplateV2LayoutsToStudio,
  serializeStudioLayoutsForUpstream,
} from "./template-v2-upstream-compat.ts";

const pinnedUpstreamElements = JSON.parse(
  readFileSync(
    new URL(
      "../../fastapi/tests/fixtures/template_v2/upstream-elements-57b194b.json",
      import.meta.url,
    ),
    "utf8",
  ),
) as {
  upstream_sha: string;
  elements: Array<Record<string, unknown>>;
};

const tableStructureRoundTripFixture = JSON.parse(
  readFileSync(
    new URL(
      "./fixtures/template-v2-upstream-table-structure-roundtrip-57b194b.json",
      import.meta.url,
    ),
    "utf8",
  ),
) as {
  upstream_sha: string;
  model_path: string;
  document: TableRoundTripEnvelope;
};

type TableRoundTripEnvelope = {
  layouts: Array<{
    id: string;
    components: Array<{
      elements: Array<Record<string, unknown>>;
      [key: string]: unknown;
    }>;
    [key: string]: unknown;
  }>;
  future_envelope_field: { retained: boolean };
  [key: string]: unknown;
};

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

test("pinned upstream exact-bound table structural edits stay fail closed", () => {
  const fixtureTable = pinnedUpstreamElements.elements.find(
    (element) => element.type === "table",
  );
  assert.ok(fixtureTable);
  assert.deepEqual(
    buildTableContractEditPatches(fixtureTable, {
      kind: "column-count",
      value: 2,
    }),
    {
      ok: false,
      reason: "table_column_count_edit_out_of_bounds",
    },
  );
  assert.deepEqual(
    buildTableContractEditPatches(fixtureTable, {
      kind: "row-count",
      value: 0,
    }),
    {
      ok: false,
      reason: "table_row_count_edit_out_of_bounds",
    },
  );
});

test("unmodeled table column-width edits stay explicitly fail closed", () => {
  const fixtureTable = pinnedUpstreamElements.elements.find(
    (element) => element.type === "table",
  );
  assert.ok(fixtureTable);
  const source = structuredClone(fixtureTable);
  const snapshot = structuredClone(source);

  assert.deepEqual(
    buildTableContractEditPatches(
      source,
      {
        kind: "column-width",
        columnIndex: 0,
        value: 1,
      } as never,
    ),
    {
      ok: false,
      reason: "unsupported_table_edit",
    },
  );
  assert.equal(source.upstream_element_extension, "table-vNext");
  assert.deepEqual(source, snapshot);
});

test("table resize fails closed for unproven multi-step count changes", () => {
  const source = table();
  source.max_columns = 4;
  source.max_rows = 3;
  assert.deepEqual(
    buildTableContractEditPatches(source, {
      kind: "column-count",
      value: 4,
    }),
    {
      ok: false,
      reason: "table_column_count_edit_requires_single_step",
    },
  );
  assert.deepEqual(
    buildTableContractEditPatches(source, {
      kind: "row-count",
      value: 3,
    }),
    {
      ok: false,
      reason: "table_row_count_edit_requires_single_step",
    },
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

test("table cell alignment edits one canonical cell and rejects unmodeled values", () => {
  const source = table();
  const changed = buildTableContractEditPatches(source, {
    kind: "cell-alignment",
    section: "body",
    rowIndex: 0,
    columnIndex: 1,
    value: "right",
  });
  assert.equal(changed.ok, true);
  if (!changed.ok) return;
  assert.deepEqual(changed.value, [{
    path: ["rows", 0, 1, "alignment"],
    value: "right",
  }]);
  const result = applyPatches(source, changed.value);
  assert.equal(validateTableContract(result).ok, true);
  assert.equal(
    (result.rows as Array<Array<Record<string, unknown>>>)[0]![1]!
      .alignment,
    "right",
  );
  assert.equal(
    (result.rows as Array<Array<Record<string, unknown>>>)[0]![0]!
      .alignment,
    undefined,
  );
  assert.deepEqual(
    buildTableContractEditPatches(source, {
      kind: "cell-alignment",
      section: "body",
      rowIndex: 0,
      columnIndex: 1,
      value: "justify",
    }),
    { ok: false, reason: "invalid_table_alignment_edit" },
  );
});

test("table section fill edits create canonical Fill objects and retain metadata", () => {
  const source = table();
  (source.columns[0] as Record<string, unknown>).color = {
    color: "#f1f5f9",
    opacity: 0.8,
    extension: { retained: true },
  };
  const changed = buildTableContractEditPatches(source, {
    kind: "section-fill",
    section: "header",
    value: "#ddeeff",
  });
  assert.equal(changed.ok, true);
  if (!changed.ok) return;
  assert.deepEqual(changed.value, [
    {
      path: ["columns", 0, "color", "color"],
      value: "#ddeeff",
    },
    {
      path: ["columns", 1, "color", "color"],
      value: "#ddeeff",
    },
  ]);

  const result = applyPatches(source, changed.value);
  assert.equal(validateTableContract(result).ok, true);
  assert.deepEqual(
    (result.columns as Array<Record<string, unknown>>)[0].color,
    {
      color: "#ddeeff",
      opacity: 0.8,
      extension: { retained: true },
    },
  );
  assert.deepEqual(
    (result.columns as Array<Record<string, unknown>>)[1].color,
    { color: "#ddeeff" },
  );
  assert.deepEqual(
    buildTableContractEditPatches(source, {
      kind: "section-fill",
      section: "body",
      value: 123,
    }),
    { ok: false, reason: "invalid_table_fill_edit" },
  );
});

test("pinned upstream table fill edit survives save, render, and reload", () => {
  assert.equal(
    pinnedUpstreamElements.upstream_sha,
    "57b194b234b42c8b28f8a507a30322de200e3e83",
  );
  const fixtureTable = pinnedUpstreamElements.elements.find(
    (element) => element.type === "table",
  );
  assert.ok(fixtureTable);
  const source = {
    layouts: [{
      id: "upstream-table-roundtrip",
      components: [{
        id: "table-component",
        position: { x: 0, y: 0 },
        elements: [structuredClone(fixtureTable)],
      }],
    }],
    future_envelope_field: { retained: true },
  };
  const document = adaptUpstreamTemplateV2LayoutsToStudio(source);
  const edited = structuredClone(document.studioLayouts) as {
    layouts: Array<{
      components: Array<{
        elements: Array<Record<string, unknown>>;
      }>;
    }>;
  };
  const element = edited.layouts[0]!.components[0]!.elements[0]!;
  const patches = buildTableContractEditPatches(element, {
    kind: "section-fill",
    section: "header",
    value: "#ddeeff",
  });
  assert.equal(patches.ok, true);
  if (!patches.ok) return;
  edited.layouts[0]!.components[0]!.elements[0] = applyPatches(
    element,
    patches.value,
  );

  const saved = serializeStudioLayoutsForUpstream(document, edited) as {
    layouts: Array<{
      components: Array<{
        elements: Array<Record<string, unknown>>;
      }>;
    }>;
    future_envelope_field: { retained: boolean };
  };
  const reloaded = adaptUpstreamTemplateV2LayoutsToStudio(saved);
  const persisted = serializeStudioLayoutsForUpstream(reloaded) as typeof saved;
  const persistedTable =
    persisted.layouts[0]!.components[0]!.elements[0]!;

  assert.deepEqual(persisted.future_envelope_field, { retained: true });
  assert.equal(validateTableContract(persistedTable).ok, true);
  assert.deepEqual(persistedTable, {
    ...fixtureTable,
    columns: [{
      runs: [{ text: "Metric" }],
      color: { color: "#ddeeff" },
    }],
  });
  const html = renderTemplateV2GeneralSlideCanvasHtml({
    id: "upstream-table-roundtrip",
    ui: persisted.layouts[0],
  });
  assert.match(html, /data-template-v2-element="table"/);
  assert.match(html, /background:#ddeeff/);
  assert.match(html, />Metric</);
  assert.match(html, />Revenue</);
  assert.deepEqual(source.layouts[0]!.components[0]!.elements[0], fixtureTable);
});

test("fixed upstream table cell alignment survives save, reload, and render", () => {
  const source = structuredClone(tableStructureRoundTripFixture.document);
  const document = adaptUpstreamTemplateV2LayoutsToStudio(source);
  const edited = structuredClone(
    document.studioLayouts,
  ) as TableRoundTripEnvelope;
  const element = edited.layouts[0]!.components[0]!.elements[0]!;
  const patches = buildTableContractEditPatches(element, {
    kind: "cell-alignment",
    section: "header",
    columnIndex: 0,
    value: "center",
  });
  assert.equal(patches.ok, true);
  if (!patches.ok) return;
  edited.layouts[0]!.components[0]!.elements[0] = applyPatches(
    element,
    patches.value,
  );

  const saved = serializeStudioLayoutsForUpstream(
    document,
    edited,
  ) as TableRoundTripEnvelope;
  const reloaded = adaptUpstreamTemplateV2LayoutsToStudio(
    JSON.parse(JSON.stringify(saved)),
  );
  const persisted = serializeStudioLayoutsForUpstream(
    reloaded,
  ) as TableRoundTripEnvelope;
  const persistedTable =
    persisted.layouts[0]!.components[0]!.elements[0]!;
  assert.equal(validateTableContract(persistedTable).ok, true);
  assert.equal(
    (persistedTable.columns as Array<Record<string, unknown>>)[0]!
      .alignment,
    "center",
  );
  assert.equal(
    (persistedTable.columns as Array<Record<string, unknown>>)[1]!
      .alignment,
    undefined,
  );
  assert.equal(persistedTable.upstream_element_extension, "table-vNext");
  const html = renderTemplateV2GeneralSlideCanvasHtml({
    id: persisted.layouts[0]!.id,
    ui: persisted.layouts[0]!,
  });
  assert.match(html, /<th style="[^"]*text-align:center/);
  assert.match(html, />Metric</);
  assert.deepEqual(source, tableStructureRoundTripFixture.document);
});

test("fixed upstream table single-step additions survive save, reload, and render", () => {
  assert.equal(
    tableStructureRoundTripFixture.upstream_sha,
    "57b194b234b42c8b28f8a507a30322de200e3e83",
  );
  assert.equal(
    tableStructureRoundTripFixture.model_path,
    "servers/fastapi/templates/v2/models/elements.py",
  );
  const source = structuredClone(tableStructureRoundTripFixture.document);
  const document = adaptUpstreamTemplateV2LayoutsToStudio(source);
  const edited = structuredClone(
    document.studioLayouts,
  ) as TableRoundTripEnvelope;
  let element = edited.layouts[0]!.components[0]!.elements[0]!;

  const wider = buildTableContractEditPatches(element, {
    kind: "column-count",
    value: 3,
  });
  assert.equal(wider.ok, true);
  if (!wider.ok) return;
  element = applyPatches(element, wider.value);

  const taller = buildTableContractEditPatches(element, {
    kind: "row-count",
    value: 2,
  });
  assert.equal(taller.ok, true);
  if (!taller.ok) return;
  edited.layouts[0]!.components[0]!.elements[0] = applyPatches(
    element,
    taller.value,
  );

  const saved = serializeStudioLayoutsForUpstream(
    document,
    edited,
  ) as TableRoundTripEnvelope;
  const reloaded = adaptUpstreamTemplateV2LayoutsToStudio(saved);
  const persisted = serializeStudioLayoutsForUpstream(
    reloaded,
  ) as TableRoundTripEnvelope;
  const persistedTable =
    persisted.layouts[0]!.components[0]!.elements[0]!;

  assert.equal(validateTableContract(persistedTable).ok, true);
  assert.deepEqual(persisted.future_envelope_field, { retained: true });
  assert.deepEqual(
    persisted.layouts[0]!.upstream_layout_extension,
    { retained: true },
  );
  assert.equal(persistedTable.upstream_element_extension, "table-vNext");
  assert.deepEqual(
    (persistedTable.columns as Array<Record<string, unknown>>)[2],
    { runs: [{ text: "" }] },
  );
  assert.deepEqual(
    (persistedTable.rows as Array<Array<Record<string, unknown>>>)[1],
    [
      { runs: [{ text: "" }] },
      { runs: [{ text: "" }] },
      { runs: [{ text: "" }] },
    ],
  );
  assert.deepEqual(
    (persistedTable.rows as Array<Array<Record<string, unknown>>>)[0],
    [
      { runs: [{ text: "Revenue" }] },
      { runs: [{ text: "$12M" }] },
      { runs: [{ text: "" }] },
    ],
  );

  const html = renderTemplateV2GeneralSlideCanvasHtml({
    id: persisted.layouts[0]!.id,
    ui: persisted.layouts[0],
  });
  assert.equal(html.match(/<th\b/g)?.length, 3);
  assert.equal(html.match(/<td\b/g)?.length, 6);
  assert.equal(html.match(/<tr>/g)?.length, 3);
  assert.match(html, />Metric</);
  assert.match(html, />Revenue</);
  assert.deepEqual(source, tableStructureRoundTripFixture.document);
});

test("fixed upstream table single-step removals survive save, reload, and render", () => {
  const source = structuredClone(tableStructureRoundTripFixture.document);
  const document = adaptUpstreamTemplateV2LayoutsToStudio(source);
  const edited = structuredClone(
    document.studioLayouts,
  ) as TableRoundTripEnvelope;
  let element = edited.layouts[0]!.components[0]!.elements[0]!;

  const narrower = buildTableContractEditPatches(element, {
    kind: "column-count",
    value: 1,
  });
  assert.equal(narrower.ok, true);
  if (!narrower.ok) return;
  element = applyPatches(element, narrower.value);

  const shorter = buildTableContractEditPatches(element, {
    kind: "row-count",
    value: 0,
  });
  assert.equal(shorter.ok, true);
  if (!shorter.ok) return;
  edited.layouts[0]!.components[0]!.elements[0] = applyPatches(
    element,
    shorter.value,
  );

  const saved = serializeStudioLayoutsForUpstream(
    document,
    edited,
  ) as TableRoundTripEnvelope;
  const reloaded = adaptUpstreamTemplateV2LayoutsToStudio(saved);
  const persisted = serializeStudioLayoutsForUpstream(
    reloaded,
  ) as TableRoundTripEnvelope;
  const persistedTable =
    persisted.layouts[0]!.components[0]!.elements[0]!;

  assert.equal(validateTableContract(persistedTable).ok, true);
  assert.deepEqual(persisted.future_envelope_field, { retained: true });
  assert.deepEqual(
    persisted.layouts[0]!.upstream_layout_extension,
    { retained: true },
  );
  assert.equal(persistedTable.upstream_element_extension, "table-vNext");
  assert.deepEqual(persistedTable.columns, [
    {
      runs: [{ text: "Metric", font: { bold: true } }],
      color: { color: "#E2E8F0" },
    },
  ]);
  assert.deepEqual(persistedTable.rows, []);

  const html = renderTemplateV2GeneralSlideCanvasHtml({
    id: persisted.layouts[0]!.id,
    ui: persisted.layouts[0],
  });
  assert.equal(html.match(/<th\b/g)?.length, 1);
  assert.equal(html.match(/<td\b/g)?.length ?? 0, 0);
  assert.equal(html.match(/<tbody><tr>/g)?.length ?? 0, 0);
  assert.match(html, />Metric</);
  assert.doesNotMatch(html, />Actual</);
  assert.doesNotMatch(html, />Revenue</);
  assert.deepEqual(source, tableStructureRoundTripFixture.document);
});
