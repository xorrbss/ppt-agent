import assert from "node:assert/strict";
import test from "node:test";

import {
  isSafeTemplateV2AssetSource,
  isSafeTemplateV2Color,
  updateTemplateV2ContentRun,
} from "./template-v2-studio-content.ts";

test("updates a text-list run without changing sibling runs or item metadata", () => {
  const element = {
    type: "text-list",
    marker: "number",
    future_field: { retained: true },
    items: [
      [
        { text: "Old", font: { bold: true } },
        { text: " suffix", language: "ko-KR" },
      ],
      [{ text: "Second" }],
    ],
  };
  const updated = updateTemplateV2ContentRun(
    element,
    { kind: "list-item", itemIndex: 0, runIndex: 0 },
    "New",
  );

  assert.deepEqual(updated.items, [
    [
      { text: "New", font: { bold: true } },
      { text: " suffix", language: "ko-KR" },
    ],
    [{ text: "Second" }],
  ]);
  assert.deepEqual(updated.future_field, { retained: true });
});

test("updates table headers and body cells while retaining cell metadata", () => {
  const element = {
    type: "table",
    columns: [{ runs: [{ text: "Header" }], alignment: "center" }],
    rows: [
      [
        {
          runs: [{ text: "Body", font: { italic: true } }],
          color: "#ffeeee",
        },
      ],
    ],
  };
  const header = updateTemplateV2ContentRun(
    element,
    { kind: "table-column", columnIndex: 0, runIndex: 0 },
    "Title",
  );
  const body = updateTemplateV2ContentRun(
    header,
    { kind: "table-cell", rowIndex: 0, columnIndex: 0, runIndex: 0 },
    "Value",
  );

  assert.deepEqual(body.columns, [
    { runs: [{ text: "Title" }], alignment: "center" },
  ]);
  assert.deepEqual(body.rows, [
    [
      {
        runs: [{ text: "Value", font: { italic: true } }],
        color: "#ffeeee",
      },
    ],
  ]);
});

test("updates table header and body styles without replacing runs or color metadata", () => {
  const element = {
    type: "table",
    columns: [
      {
        runs: [{ text: "Header", future_run_field: "retained" }],
        alignment: "left",
        color: { color: "#ffffff", opacity: 0.75 },
      },
    ],
    rows: [
      [
        {
          runs: [{ text: "Body", font: { italic: true } }],
          alignment: "right",
          future_cell_field: { retained: true },
        },
      ],
    ],
  };
  const header = updateTemplateV2ContentRun(
    element,
    {
      kind: "table-column-style",
      columnIndex: 0,
      property: "alignment",
    },
    "center",
  );
  const headerColor = updateTemplateV2ContentRun(
    header,
    { kind: "table-column-style", columnIndex: 0, property: "color" },
    "#1d4ed8",
  );
  const body = updateTemplateV2ContentRun(
    headerColor,
    {
      kind: "table-cell-style",
      rowIndex: 0,
      columnIndex: 0,
      property: "alignment",
    },
    "left",
  );
  const bodyColor = updateTemplateV2ContentRun(
    body,
    {
      kind: "table-cell-style",
      rowIndex: 0,
      columnIndex: 0,
      property: "color",
    },
    "rgba(255, 238, 238, 0.8)",
  );

  assert.deepEqual(headerColor.columns, [
    {
      runs: [{ text: "Header", future_run_field: "retained" }],
      alignment: "center",
      color: { color: "#1d4ed8", opacity: 0.75 },
    },
  ]);
  assert.deepEqual(bodyColor.rows, [
    [
      {
        runs: [{ text: "Body", font: { italic: true } }],
        alignment: "left",
        color: { color: "rgba(255, 238, 238, 0.8)" },
        future_cell_field: { retained: true },
      },
    ],
  ]);
});

test("rejects invalid table styles without creating history-worthy changes", () => {
  const element = {
    type: "table",
    columns: [
      {
        runs: [{ text: "Header" }],
        alignment: "left",
        color: { color: "#ffffff" },
      },
    ],
    rows: [],
  };

  assert.equal(
    updateTemplateV2ContentRun(
      element,
      {
        kind: "table-column-style",
        columnIndex: 0,
        property: "alignment",
      },
      "justify",
    ),
    element,
  );
  assert.equal(
    updateTemplateV2ContentRun(
      element,
      { kind: "table-column-style", columnIndex: 0, property: "color" },
      '#fff";background:url(https://example.com)',
    ),
    element,
  );
  assert.equal(isSafeTemplateV2Color("#1d4ed8"), true);
  assert.equal(isSafeTemplateV2Color("rgba(255, 255, 255, 0.5)"), true);
  assert.equal(isSafeTemplateV2Color("var(--unsafe-color)"), false);
});

test("returns the original element for invalid targets and unchanged text", () => {
  const element = { type: "text", runs: [{ text: "Same" }] };

  assert.equal(
    updateTemplateV2ContentRun(element, { kind: "text", runIndex: 0 }, "Same"),
    element,
  );
  assert.equal(
    updateTemplateV2ContentRun(
      element,
      { kind: "text", runIndex: 99 },
      "Changed",
    ),
    element,
  );
  assert.equal(
    updateTemplateV2ContentRun(
      element,
      { kind: "table-cell", rowIndex: 0, columnIndex: 0, runIndex: 0 },
      "Changed",
    ),
    element,
  );
});

test("edits chart labels and numeric values without replacing series metadata", () => {
  const element = {
    type: "chart",
    title: "Revenue",
    categories: ["Q1", "Q2"],
    series: [
      {
        name: "Actual",
        values: [10, 20],
        future_series_field: { retained: true },
      },
    ],
    future_chart_field: "retained",
  };
  const title = updateTemplateV2ContentRun(
    element,
    { kind: "chart-title" },
    "Bookings",
  );
  const category = updateTemplateV2ContentRun(
    title,
    { kind: "chart-category", categoryIndex: 1 },
    "FY26 Q2",
  );
  const seriesName = updateTemplateV2ContentRun(
    category,
    { kind: "chart-series-name", seriesIndex: 0 },
    "Forecast",
  );
  const value = updateTemplateV2ContentRun(
    seriesName,
    { kind: "chart-series-value", seriesIndex: 0, valueIndex: 1 },
    "42.5",
  );

  assert.equal(value.title, "Bookings");
  assert.deepEqual(value.categories, ["Q1", "FY26 Q2"]);
  assert.deepEqual(value.series, [
    {
      name: "Forecast",
      values: [10, 42.5],
      future_series_field: { retained: true },
    },
  ]);
  assert.equal(value.future_chart_field, "retained");
  assert.equal(
    updateTemplateV2ContentRun(
      value,
      { kind: "chart-series-value", seriesIndex: 0, valueIndex: 1 },
      "not-a-number",
    ),
    value,
  );
});

test("edits image source and fit only for renderer-safe asset values", () => {
  const element = {
    type: "image",
    data: "/app_data/images/old.png",
    fit: "cover",
    future_image_field: { retained: true },
  };
  const source = updateTemplateV2ContentRun(
    element,
    { kind: "asset-data" },
    "/app_data/images/new.png",
  );
  const fit = updateTemplateV2ContentRun(
    source,
    { kind: "asset-fit" },
    "contain",
  );

  assert.deepEqual(fit, {
    type: "image",
    data: "/app_data/images/new.png",
    fit: "contain",
    future_image_field: { retained: true },
  });
  assert.equal(
    updateTemplateV2ContentRun(
      fit,
      { kind: "asset-data" },
      "https://example.com/tracking.png",
    ),
    fit,
  );
  assert.equal(
    updateTemplateV2ContentRun(fit, { kind: "asset-fit" }, "stretch"),
    fit,
  );
  assert.equal(isSafeTemplateV2AssetSource("/app_data/images/new.png"), true);
  assert.equal(
    isSafeTemplateV2AssetSource("data:image/svg+xml,%3Csvg%3E%3C/svg%3E"),
    true,
  );
  assert.equal(isSafeTemplateV2AssetSource("javascript:alert(1)"), false);
  assert.equal(isSafeTemplateV2AssetSource("//example.com/image.png"), false);
  assert.equal(isSafeTemplateV2AssetSource("/\\example.com/image.png"), false);
  assert.equal(isSafeTemplateV2AssetSource("/images/unsafe\nname.png"), false);
});
