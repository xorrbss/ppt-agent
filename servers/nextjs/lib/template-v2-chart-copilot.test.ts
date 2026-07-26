import assert from "node:assert/strict";
import test from "node:test";

import {
  TEMPLATE_V2_CHART_COPILOT_LIMITS,
  TemplateV2ChartCopilotError,
  importTemplateV2ChartData,
  previewTemplateV2ChartCopilot,
  recommendTemplateV2Chart,
  requestTemplateV2ChartCopilotCandidates,
  type TemplateV2Chart,
} from "./template-v2-chart-copilot.ts";

function chart(overrides: Record<string, unknown> = {}): TemplateV2Chart {
  return {
    type: "chart",
    chart_type: "bar",
    title: "Revenue",
    categories: ["Q1", "Q2"],
    series: [
      {
        name: "Actual",
        values: [10, 12],
        stable_id: "series-actual",
        future_series_metadata: { retained: true },
      },
      {
        name: "Plan",
        values: [11, 13],
        stable_id: "series-plan",
      },
    ],
    stable_id: "chart-1",
    future_chart_metadata: { retained: true },
    ...overrides,
  } as TemplateV2Chart;
}

test("previews allowed chart controls and preserves opaque metadata", () => {
  const source = chart();
  const preview = previewTemplateV2ChartCopilot(source, [
    { kind: "set-control", control: "title", value: "FY26 Revenue" },
    { kind: "set-control", control: "legend", value: false },
    { kind: "set-control", control: "x_axis", value: true },
    { kind: "set-control", control: "x_axis_grid", value: true },
    { kind: "set-control", control: "data_labels", value: "top" },
  ]);

  assert.equal(preview.before, source);
  assert.equal(preview.after.title, "FY26 Revenue");
  assert.equal(preview.after.legend, false);
  assert.equal(preview.after.x_axis_grid, true);
  assert.equal(preview.after.data_labels, "top");
  assert.equal(preview.after.stable_id, "chart-1");
  assert.equal(
    preview.after.future_chart_metadata,
    source.future_chart_metadata,
  );
  assert.deepEqual(
    preview.diff.map(({ path }) => path),
    ["title", "legend", "x_axis", "x_axis_grid", "data_labels"],
  );
});

test("adds, removes, edits, and reorders series without replacing retained series", () => {
  const source = chart();
  const preview = previewTemplateV2ChartCopilot(source, [
    {
      kind: "add-series",
      series: { name: "Forecast", values: [14, 16] },
      index: 1,
    },
    { kind: "set-series-value", seriesIndex: 0, categoryIndex: 1, value: 15 },
    { kind: "rename-series", seriesIndex: 2, name: "Operating Plan" },
    { kind: "move-series", seriesIndex: 2, destinationIndex: 0 },
    { kind: "remove-series", seriesIndex: 1 },
  ]);

  assert.deepEqual(
    preview.after.series.map(({ name }) => name),
    ["Operating Plan", "Forecast"],
  );
  assert.equal(preview.after.series[0].stable_id, "series-plan");
  assert.equal(preview.before.series[1].name, "Plan");
  assert.equal(preview.after.series[1].name, "Forecast");
  assert.equal(source.series[0].values[1], 12);
});

test("rejects unknown operation fields and out-of-range edits", () => {
  assert.throws(
    () =>
      previewTemplateV2ChartCopilot(chart(), [
        { kind: "set-control", control: "three_dimensional", value: true },
      ]),
    /template_v2_chart_copilot_unsupported_control:operations.0.control/,
  );
  assert.throws(
    () =>
      previewTemplateV2ChartCopilot(chart(), [
        {
          kind: "rename-series",
          seriesIndex: 0,
          name: "Actual",
          replace_slide: true,
        },
      ]),
    (error) =>
      error instanceof TemplateV2ChartCopilotError &&
      error.code === "template_v2_chart_copilot_invalid_operation",
  );
  assert.throws(
    () =>
      previewTemplateV2ChartCopilot(chart(), [
        { kind: "set-series-value", seriesIndex: 0, categoryIndex: 2, value: 1 },
      ]),
    /template_v2_chart_copilot_invalid_operation:operation.categoryIndex/,
  );
});

test("rejects ill-typed chart controls before producing a preview", () => {
  assert.throws(
    () => previewTemplateV2ChartCopilot(chart({ legend: "yes" }), []),
    /template_v2_chart_copilot_invalid_chart:chart.legend/,
  );
  assert.throws(
    () =>
      previewTemplateV2ChartCopilot(
        chart({ axis_color: "url(javascript:alert(1))" }),
        [],
      ),
    /template_v2_chart_copilot_invalid_chart:chart.axis_color/,
  );
  assert.throws(
    () => previewTemplateV2ChartCopilot(chart({ data_labels: true }), []),
    /template_v2_chart_copilot_invalid_chart:chart.data_labels/,
  );
});

test("fails closed for radar axes, grids, and data labels", () => {
  for (const operation of [
    { kind: "set-control", control: "x_axis", value: true },
    { kind: "set-control", control: "y_axis_grid", value: true },
    { kind: "set-control", control: "data_labels", value: "outside" },
  ]) {
    assert.throws(
      () =>
        previewTemplateV2ChartCopilot(
          chart({ chart_type: "radar" }),
          [operation],
        ),
      /template_v2_chart_copilot_incompatible_control/,
    );
  }
});

test("fails closed when a type change makes existing controls incompatible", () => {
  assert.throws(
    () =>
      previewTemplateV2ChartCopilot(chart({ x_axis: true }), [
        { kind: "set-control", control: "chart_type", value: "radar" },
      ]),
    /template_v2_chart_copilot_incompatible_control:chart.x_axis/,
  );
  assert.throws(
    () =>
      previewTemplateV2ChartCopilot(chart(), [
        { kind: "set-control", control: "chart_type", value: "pie" },
      ]),
    /template_v2_chart_copilot_incompatible_control:chart.series/,
  );
});

test("enforces line and area data-label positions from the render contract", () => {
  assert.throws(
    () =>
      previewTemplateV2ChartCopilot(chart({ chart_type: "line" }), [
        { kind: "set-control", control: "data_labels", value: "mid" },
      ]),
    /template_v2_chart_copilot_incompatible_control:chart.data_labels/,
  );
  const preview = previewTemplateV2ChartCopilot(
    chart({ chart_type: "line" }),
    [{ kind: "set-control", control: "data_labels", value: "outside" }],
  );
  assert.equal(preview.after.data_labels, "outside");
});

test("imports quoted CSV and TSV into the bounded chart shape", () => {
  assert.deepEqual(
    importTemplateV2ChartData({
      format: "csv",
      text: 'Quarter,"North, Inc.",South\r\nQ1,10,12\r\nQ2,11.5,13',
    }),
    {
      categories: ["Q1", "Q2"],
      series: [
        { name: "North, Inc.", values: [10, 11.5] },
        { name: "South", values: [12, 13] },
      ],
    },
  );
  assert.deepEqual(
    importTemplateV2ChartData({
      format: "tsv",
      text: "Quarter\tActual\nQ1\t10\nQ2\t12",
    }),
    {
      categories: ["Q1", "Q2"],
      series: [{ name: "Actual", values: [10, 12] }],
    },
  );
});

test("imports bounded table arrays and rejects ragged or nonnumeric data", () => {
  assert.deepEqual(
    importTemplateV2ChartData({
      format: "table",
      rows: [
        ["Category", "Revenue"],
        ["Q1", 10],
        ["Q2", 12],
      ],
    }),
    {
      categories: ["Q1", "Q2"],
      series: [{ name: "Revenue", values: [10, 12] }],
    },
  );
  assert.throws(
    () =>
      importTemplateV2ChartData({
        format: "table",
        rows: [
          ["Category", "Revenue"],
          ["Q1"],
        ],
      }),
    /template_v2_chart_copilot_invalid_import:import.rectangular/,
  );
  assert.throws(
    () =>
      importTemplateV2ChartData({
        format: "csv",
        text: "Category,Revenue\nQ1,not-a-number",
      }),
    /template_v2_chart_copilot_invalid_import:import.rows.1.1/,
  );
});

test("enforces category, series, cell, and byte import limits", () => {
  const tooManyRows = [
    "Category,Revenue",
    ...Array.from(
      { length: TEMPLATE_V2_CHART_COPILOT_LIMITS.maxCategories + 1 },
      (_, index) => `C${index},${index}`,
    ),
  ].join("\n");
  assert.throws(
    () => importTemplateV2ChartData({ format: "csv", text: tooManyRows }),
    /template_v2_chart_copilot_limit_exceeded:import.shape/,
  );
  assert.throws(
    () =>
      importTemplateV2ChartData({
        format: "csv",
        text: `Category,Revenue\n${"x".repeat(
          TEMPLATE_V2_CHART_COPILOT_LIMITS.maxCellCharacters + 1,
        )},1`,
      }),
    /template_v2_chart_copilot_limit_exceeded:import.cell/,
  );
  assert.throws(
    () =>
      importTemplateV2ChartData({
        format: "csv",
        text: "x".repeat(
          TEMPLATE_V2_CHART_COPILOT_LIMITS.maxInputBytes + 1,
        ),
      }),
    /template_v2_chart_copilot_limit_exceeded:import.bytes/,
  );
});

test("replace-data keeps matching stable IDs and opaque series metadata", () => {
  const source = chart();
  const imported = importTemplateV2ChartData({
    format: "csv",
    text: "Quarter,Plan,Actual\nH1,20,19\nH2,22,23",
  });
  const preview = previewTemplateV2ChartCopilot(source, [
    { kind: "replace-data", ...imported },
  ]);

  assert.deepEqual(preview.after.categories, ["H1", "H2"]);
  assert.equal(preview.after.series[0].stable_id, "series-plan");
  assert.equal(preview.after.series[1].stable_id, "series-actual");
  assert.deepEqual(preview.after.series[1].future_series_metadata, {
    retained: true,
  });
  assert.equal(preview.after.future_chart_metadata, source.future_chart_metadata);
});

test("deterministic recommendation uses data characteristics and never a provider", () => {
  assert.deepEqual(
    recommendTemplateV2Chart(
      chart({
        categories: ["2025-01", "2025-02", "2025-03"],
        series: [{ name: "Revenue", values: [10, 12, 15] }],
      }),
    ),
    { chartType: "line", reasonCode: "time_series", confidence: "high" },
  );
  assert.deepEqual(
    recommendTemplateV2Chart(
      chart({
        categories: ["Product", "Services", "Other"],
        series: [{ name: "Mix", values: [60, 30, 10] }],
      }),
    ),
    { chartType: "pie", reasonCode: "part_to_whole", confidence: "medium" },
  );
});

test("provider boundary is injectable, bounded, and fail-closed", async () => {
  await assert.rejects(
    () =>
      requestTemplateV2ChartCopilotCandidates(chart(), {
        async recommend() {
          throw new Error("credential detail that must not cross the boundary");
        },
      }),
    /template_v2_chart_copilot_provider_unavailable:provider/,
  );
  await assert.rejects(
    () => requestTemplateV2ChartCopilotCandidates(chart()),
    /template_v2_chart_copilot_provider_unavailable:provider/,
  );
  const candidates = await requestTemplateV2ChartCopilotCandidates(chart(), {
    async recommend(request) {
      assert.deepEqual(request.chart, chart());
      assert.equal(request.maxCandidates, 3);
      return [
        {
          id: "line-option",
          label: "Trend view",
          operations: [
            { kind: "set-control", control: "chart_type", value: "line" },
          ],
        },
      ];
    },
  });
  assert.equal(candidates.length, 1);
  assert.equal(candidates[0].preview.after.chart_type, "line");
  await assert.rejects(
    () =>
      requestTemplateV2ChartCopilotCandidates(chart(), {
        async recommend() {
          return [
            {
              id: "unsafe",
              label: "Unsafe",
              operations: [
                {
                  kind: "set-control",
                  control: "chart_type",
                  value: "treemap",
                },
              ],
            },
          ];
        },
      }),
    /template_v2_chart_copilot_invalid_provider_response/,
  );
});
