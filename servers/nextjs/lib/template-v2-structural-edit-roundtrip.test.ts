import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  validateChartContract,
  validateTableContract,
  type JsonRecord,
} from "./template-v2-chart-table-contract.ts";
import {
  renderTemplateV2GeneralSlideCanvasHtml,
} from "./template-v2-general-renderer.mjs";
import {
  applyTemplateV2StructuralEdit,
  type TemplateV2StructuralEdit,
} from "./template-v2-structural-edit.ts";
import {
  adaptUpstreamTemplateV2LayoutsToStudio,
  serializeStudioLayoutsForUpstream,
} from "./template-v2-upstream-compat.ts";

type StructuralFixture = {
  upstream_sha: string;
  model_path: string;
  document: StructuralEnvelope;
};

type StructuralEnvelope = {
  layouts: Array<{
    id: string;
    components: Array<{
      elements: JsonRecord[];
      [key: string]: unknown;
    }>;
    [key: string]: unknown;
  }>;
  future_envelope_field: { retained: boolean };
  [key: string]: unknown;
};

const fixture = JSON.parse(
  readFileSync(
    new URL(
      "./fixtures/template-v2-upstream-structural-edit-roundtrip-57b194b.json",
      import.meta.url,
    ),
    "utf8",
  ),
) as StructuralFixture;

function roundTrip(
  elementIndex: number,
  edit: TemplateV2StructuralEdit,
) {
  const source = structuredClone(fixture.document);
  const document = adaptUpstreamTemplateV2LayoutsToStudio(source);
  const edited = structuredClone(document.studioLayouts) as StructuralEnvelope;
  const current =
    edited.layouts[0]!.components[0]!.elements[elementIndex]!;
  const result = applyTemplateV2StructuralEdit(current, edit);
  if (result.ok === false) assert.fail(result.reason);
  edited.layouts[0]!.components[0]!.elements[elementIndex] = result.value;

  const saved = serializeStudioLayoutsForUpstream(
    document,
    edited,
  ) as StructuralEnvelope;
  const savedAcrossJson = JSON.parse(
    JSON.stringify(saved),
  ) as StructuralEnvelope;
  const reloaded = adaptUpstreamTemplateV2LayoutsToStudio(savedAcrossJson);
  const persisted = JSON.parse(
    JSON.stringify(serializeStudioLayoutsForUpstream(reloaded)),
  ) as StructuralEnvelope;
  const renderLayout = structuredClone(persisted.layouts[0]!);
  renderLayout.components[0]!.elements = [
    renderLayout.components[0]!.elements[elementIndex]!,
  ];
  const html = renderTemplateV2GeneralSlideCanvasHtml({
    id: renderLayout.id,
    ui: renderLayout,
  });

  assert.deepEqual(persisted.future_envelope_field, { retained: true });
  assert.deepEqual(
    persisted.layouts[0]!.future_layout_field,
    { retained: true },
  );
  assert.deepEqual(
    persisted.layouts[0]!.components[0]!.future_component_field,
    { retained: true },
  );
  assert.deepEqual(source, fixture.document);
  return {
    element:
      persisted.layouts[0]!.components[0]!.elements[elementIndex]!,
    html,
  };
}

function cellText(cell: JsonRecord) {
  return (cell.runs as JsonRecord[])
    .map((run) => run.text)
    .join("");
}

test("fixed upstream chart series reorder survives reload and render", () => {
  assert.equal(
    fixture.upstream_sha,
    "57b194b234b42c8b28f8a507a30322de200e3e83",
  );
  assert.equal(
    fixture.model_path,
    "servers/fastapi/templates/v2/models/elements.py",
  );
  const { element, html } = roundTrip(2, {
    kind: "chart-series-reorder",
    fromIndex: 1,
    toIndex: 0,
  });

  assert.equal(validateChartContract(element).ok, true);
  assert.deepEqual(
    (element.series as JsonRecord[]).map((series) => series.name),
    ["Cost", "Revenue"],
  );
  assert.deepEqual(
    (element.series as JsonRecord[]).map(
      (series) => series.future_series_field,
    ),
    [{ id: "cost" }, { id: "revenue" }],
  );
  assert.equal(element.upstream_element_extension, "chart-vNext");
  assert.match(html, /data-template-v2-element="chart"/);
  assert.ok(html.indexOf("Cost") < html.indexOf("Revenue"));
});

test("fixed upstream table row and column reorders survive reload and render", () => {
  const rowResult = roundTrip(0, {
    kind: "table-row-reorder",
    fromIndex: 1,
    toIndex: 0,
  });
  assert.equal(validateTableContract(rowResult.element).ok, true);
  const reorderedRows = rowResult.element.rows as JsonRecord[][];
  assert.deepEqual(
    reorderedRows.map((row) => cellText(row[0]!)),
    ["South", "North"],
  );
  assert.deepEqual(
    reorderedRows.map((row) => row[0]!.future_cell_field),
    [{ id: "south" }, { id: "north" }],
  );
  assert.ok(rowResult.html.indexOf("South") < rowResult.html.indexOf("North"));

  const columnResult = roundTrip(0, {
    kind: "table-column-reorder",
    fromIndex: 2,
    toIndex: 1,
  });
  assert.equal(validateTableContract(columnResult.element).ok, true);
  assert.deepEqual(
    (columnResult.element.columns as JsonRecord[]).map(cellText),
    ["Region", "Cost", "Revenue"],
  );
  assert.deepEqual(
    (columnResult.element.rows as JsonRecord[][])[0]!.map(cellText),
    ["North", "4", "10"],
  );
  assert.ok(
    columnResult.html.indexOf("Cost") <
      columnResult.html.indexOf("Revenue"),
  );
});

test("fixed upstream table transpose survives reload and render", () => {
  const { element, html } = roundTrip(0, { kind: "table-transpose" });
  assert.equal(validateTableContract(element).ok, true);
  assert.deepEqual(
    (element.columns as JsonRecord[]).map(cellText),
    ["Region", "North", "South"],
  );
  assert.deepEqual(
    (element.rows as JsonRecord[][]).map((row) => row.map(cellText)),
    [
      ["Revenue", "10", "20"],
      ["Cost", "4", "7"],
    ],
  );
  assert.deepEqual(
    (element.columns as JsonRecord[]).map(
      (cell) => (cell.future_cell_field as JsonRecord).id,
    ),
    ["h-region", "north", "south"],
  );
  assert.equal(element.upstream_element_extension, "table-vNext");
  assert.equal(html.match(/<th\b/g)?.length, 3);
  assert.equal(html.match(/<td\b/g)?.length, 6);
});

test("fixed-shape quoted CSV import survives reload and render", () => {
  const { element, html } = roundTrip(0, {
    kind: "table-csv-import",
    csv: 'Region,Revenue,Cost\r\nWest,30,12\r\n"North, East",40,15',
  });
  assert.equal(validateTableContract(element).ok, true);
  assert.deepEqual(
    (element.rows as JsonRecord[][]).map((row) => row.map(cellText)),
    [
      ["West", "30", "12"],
      ["North, East", "40", "15"],
    ],
  );
  assert.deepEqual(
    (element.rows as JsonRecord[][])[1]![0]!.future_cell_field,
    { id: "south" },
  );
  assert.match(html, />North, East</);
  assert.match(html, />40</);
});

test("fixed upstream table-to-chart conversion survives reload and render", () => {
  const { element, html } = roundTrip(1, {
    kind: "table-to-chart",
    chartType: "bar",
    title: "Regional performance",
  });
  assert.equal(validateChartContract(element).ok, true);
  assert.equal(element.type, "chart");
  assert.deepEqual(element.categories, ["North", "South"]);
  assert.deepEqual(element.series, [
    { name: "Revenue", values: [10, 20] },
    { name: "Cost", values: [4, 7] },
  ]);
  assert.equal(element.name, "chart source");
  assert.match(html, /data-template-v2-element="chart"/);
  assert.match(html, />Regional performance</);
  assert.match(html, /data-chart-series="0"/);
  assert.match(html, /data-chart-series="1"/);
  assert.ok(html.indexOf(">North</") < html.indexOf(">South</"));
});

test("unproven structural edits fail closed without mutating input", () => {
  const table =
    fixture.document.layouts[0]!.components[0]!.elements[0]!;
  const cleanTable =
    fixture.document.layouts[0]!.components[0]!.elements[1]!;
  const chart =
    fixture.document.layouts[0]!.components[0]!.elements[2]!;
  const tableSnapshot = structuredClone(table);
  const chartSnapshot = structuredClone(chart);

  assert.deepEqual(
    applyTemplateV2StructuralEdit(chart, {
      kind: "chart-series-reorder",
      fromIndex: 0,
      toIndex: 0,
    }),
    { ok: false, reason: "chart_series_reorder_invalid_index" },
  );
  assert.deepEqual(
    applyTemplateV2StructuralEdit(table, {
      kind: "table-column-reorder",
      fromIndex: 0,
      toIndex: 3,
    }),
    { ok: false, reason: "table_column_reorder_invalid_index" },
  );
  assert.deepEqual(
    applyTemplateV2StructuralEdit(table, {
      kind: "table-csv-import",
      csv: "A,B\n1,2",
    }),
    { ok: false, reason: "table_csv_shape_change_unproven" },
  );
  assert.deepEqual(
    applyTemplateV2StructuralEdit(table, {
      kind: "table-csv-import",
      csv: 'A,B,C\n"unterminated,1,2',
    }),
    { ok: false, reason: "table_csv_malformed_quotes" },
  );
  assert.deepEqual(
    applyTemplateV2StructuralEdit(
      {
        ...cleanTable,
        rows: [
          [
            { runs: [{ text: "North" }] },
            { runs: [{ text: "not numeric" }] },
            { runs: [{ text: "4" }] },
          ],
          (cleanTable.rows as JsonRecord[][])[1],
        ],
      },
      { kind: "table-to-chart" },
    ),
    { ok: false, reason: "table_to_chart_non_numeric_value" },
  );
  assert.deepEqual(
    applyTemplateV2StructuralEdit(table, { kind: "table-to-chart" }),
    { ok: false, reason: "table_to_chart_unproven_metadata" },
  );
  assert.deepEqual(
    applyTemplateV2StructuralEdit(cleanTable, {
      kind: "table-to-chart",
      chartType: "line",
    }),
    { ok: false, reason: "table_to_chart_type_unproven" },
  );
  const exactBoundsNonSquareTable = {
    ...table,
    columns: (table.columns as JsonRecord[]).slice(0, 2),
    rows: (table.rows as JsonRecord[][]).map((row) => row.slice(0, 2)),
    min_columns: 2,
    max_columns: 2,
    min_rows: 2,
    max_rows: 2,
  };
  assert.deepEqual(
    applyTemplateV2StructuralEdit(exactBoundsNonSquareTable, {
      kind: "table-transpose",
    }),
    { ok: false, reason: "table_column_count_out_of_bounds" },
  );
  assert.deepEqual(
    applyTemplateV2StructuralEdit(
      {
        type: "unsupported",
        unsupported_reason: "unsupported_ooxml:smartArt",
        smartart_evidence: {
          status: "structured",
          nodes: [{ model_id: "step-1", text: "Discover" }],
        },
      },
      {
        kind: "smartart-node-reorder",
        fromIndex: 0,
        toIndex: 1,
      },
    ),
    { ok: false, reason: "smartart_edit_unproven" },
  );
  assert.deepEqual(
    applyTemplateV2StructuralEdit(table, {
      kind: "future-structural-edit",
    } as never),
    { ok: false, reason: "unsupported_structural_edit" },
  );
  assert.deepEqual(table, tableSnapshot);
  assert.deepEqual(chart, chartSnapshot);
});

test("successful structural edits require upstream metadata and renderer-valid results", () => {
  const cleanTable =
    fixture.document.layouts[0]!.components[0]!.elements[1]!;
  const chart =
    fixture.document.layouts[0]!.components[0]!.elements[2]!;
  const cleanTableSnapshot = structuredClone(cleanTable);
  const chartSnapshot = structuredClone(chart);

  assert.deepEqual(
    applyTemplateV2StructuralEdit(
      { ...cleanTable, name: 7 },
      { kind: "table-to-chart" },
    ),
    {
      ok: false,
      reason: "structural_edit_upstream_metadata_invalid",
    },
  );
  const missingDecorative = structuredClone(cleanTable);
  delete missingDecorative.decorative;
  assert.deepEqual(
    applyTemplateV2StructuralEdit(missingDecorative, {
      kind: "table-row-reorder",
      fromIndex: 0,
      toIndex: 1,
    }),
    {
      ok: false,
      reason: "structural_edit_upstream_metadata_invalid",
    },
  );
  assert.deepEqual(
    applyTemplateV2StructuralEdit(
      { ...cleanTable, position: { x: "500", y: 20 } },
      {
        kind: "table-row-reorder",
        fromIndex: 0,
        toIndex: 1,
      },
    ),
    {
      ok: false,
      reason: "structural_edit_result_not_renderable",
    },
  );
  assert.deepEqual(
    applyTemplateV2StructuralEdit(
      { ...chart, title: 7 },
      {
        kind: "chart-series-reorder",
        fromIndex: 0,
        toIndex: 1,
      },
    ),
    {
      ok: false,
      reason: "structural_edit_result_not_renderable",
    },
  );
  assert.deepEqual(cleanTable, cleanTableSnapshot);
  assert.deepEqual(chart, chartSnapshot);
});

test("exported structural edit boundary rejects malformed payloads without mutation", () => {
  const cleanTable =
    fixture.document.layouts[0]!.components[0]!.elements[1]!;
  const cleanTableSnapshot = structuredClone(cleanTable);
  const malformedPayloads: unknown[] = [
    null,
    [],
    {},
    { kind: null },
    { kind: "table-transpose", futureField: true },
    {
      kind: "chart-series-reorder",
      fromIndex: "0",
      toIndex: 1,
    },
    {
      kind: "table-row-reorder",
      fromIndex: 0,
      toIndex: null,
    },
    { kind: "table-csv-import", csv: null },
    { kind: "table-to-chart", title: null },
    { kind: "table-to-chart", title: undefined },
    { kind: "table-to-chart", chartType: null },
    {
      kind: "table-to-chart",
      chartType: "bar",
      unknownSemanticField: "future",
    },
    {
      kind: "smartart-node-reorder",
      fromIndex: 0,
      toIndex: 1,
      nodes: [],
    },
  ];

  for (const payload of malformedPayloads) {
    const element = structuredClone(cleanTable);
    const elementSnapshot = structuredClone(element);
    const payloadSnapshot = structuredClone(payload);
    assert.deepEqual(
      applyTemplateV2StructuralEdit(element, payload),
      { ok: false, reason: "structural_edit_payload_invalid" },
    );
    assert.deepEqual(element, elementSnapshot);
    assert.deepEqual(payload, payloadSnapshot);
  }

  const throwingPayload = Object.defineProperty({}, "kind", {
    enumerable: true,
    get(): never {
      throw new Error("malformed getter");
    },
  });
  assert.deepEqual(
    applyTemplateV2StructuralEdit(cleanTable, throwingPayload),
    { ok: false, reason: "structural_edit_payload_invalid" },
  );

  const unsupportedPayload = {
    kind: "future-structural-edit",
    futureField: true,
  };
  const unsupportedSnapshot = structuredClone(unsupportedPayload);
  assert.deepEqual(
    applyTemplateV2StructuralEdit(cleanTable, unsupportedPayload),
    { ok: false, reason: "unsupported_structural_edit" },
  );
  assert.deepEqual(unsupportedPayload, unsupportedSnapshot);

  const unprovenChartType = {
    kind: "table-to-chart",
    chartType: "scatter",
  };
  const unprovenSnapshot = structuredClone(unprovenChartType);
  assert.deepEqual(
    applyTemplateV2StructuralEdit(cleanTable, unprovenChartType),
    { ok: false, reason: "table_to_chart_type_unproven" },
  );
  assert.deepEqual(unprovenChartType, unprovenSnapshot);
  assert.deepEqual(cleanTable, cleanTableSnapshot);
});
