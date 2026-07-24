import assert from "node:assert/strict";
import test from "node:test";

import { updateTemplateV2ContentRun } from "./template-v2-studio-content.ts";

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
