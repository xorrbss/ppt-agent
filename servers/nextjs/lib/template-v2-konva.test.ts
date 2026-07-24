import assert from "node:assert/strict";
import test from "node:test";

import {
  elementCapabilities,
  layoutTemplateV2List,
  layoutTemplateV2Table,
  templateV2InfographicView,
} from "./template-v2-konva.ts";

test("elementCapabilities grants move/resize/rotate to list, table, infographic", () => {
  for (const type of ["text-list", "table", "infographic"]) {
    assert.deepEqual(elementCapabilities({ type }), {
      move: true,
      resize: true,
      rotate: true,
    });
  }
  assert.deepEqual(elementCapabilities({ type: "group" }), {
    move: true,
    resize: false,
    rotate: false,
  });
  assert.deepEqual(elementCapabilities({ type: "chart" }), {
    move: false,
    resize: false,
    rotate: false,
  });
});

test("layoutTemplateV2List concatenates runs and applies the marker per item", () => {
  const disc = layoutTemplateV2List({
    type: "text-list",
    font: { size: 20, line_height: 1.5 },
    items: [
      [{ text: "Alpha " }, { text: "one" }],
      [{ text: "Beta" }],
    ],
  });
  assert.equal(disc.marker, "disc");
  assert.equal(disc.lineHeightPx, 30);
  assert.deepEqual(
    disc.items.map((item) => `${item.markerLabel} ${item.text}`),
    ["• Alpha one", "• Beta"]
  );

  const numbered = layoutTemplateV2List({
    type: "text-list",
    marker: "number",
    items: [[{ text: "first" }], [{ text: "second" }]],
  });
  assert.deepEqual(
    numbered.items.map((item) => item.markerLabel),
    ["1.", "2."]
  );

  const none = layoutTemplateV2List({
    type: "text-list",
    marker: "none",
    items: [[{ text: "plain" }]],
  });
  assert.equal(none.items[0].markerLabel, "");
});

test("layoutTemplateV2Table lays a fixed grid with header and body rows", () => {
  const layout = layoutTemplateV2Table(
    {
      type: "table",
      columns: [{ runs: [{ text: "H1" }] }, { runs: [{ text: "H2" }] }],
      rows: [
        [
          { runs: [{ text: "a" }], alignment: "center" },
          { runs: [{ text: "b" }], color: "#ffeeee" },
        ],
      ],
    },
    200,
    90
  );
  // 2 columns x (1 header + 1 body) => 4 cells, each 100 wide, 45 tall.
  assert.equal(layout.cells.length, 4);
  const header0 = layout.cells.find((cell) => cell.key === "head-0");
  assert.ok(header0);
  assert.deepEqual(
    { x: header0.x, y: header0.y, width: header0.width, height: header0.height },
    { x: 0, y: 0, width: 100, height: 45 }
  );
  assert.equal(header0.header, true);
  assert.equal(header0.text, "H1");

  const body01 = layout.cells.find((cell) => cell.key === "0-1");
  assert.ok(body01);
  assert.equal(body01.y, 45);
  assert.equal(body01.x, 100);
  assert.equal(body01.background, "#ffeeee");
  const body00 = layout.cells.find((cell) => cell.key === "0-0");
  assert.equal(body00?.align, "center");
});

test("templateV2InfographicView clamps ratio and rejects unknown types", () => {
  const progress = templateV2InfographicView({
    type: "infographic",
    data: { type: "progress_bar", value: 75, min_value: 0, max_value: 100 },
  });
  assert.equal(progress?.type, "progress_bar");
  assert.equal(progress?.ratio, 0.75);
  assert.equal(progress?.label, "75%");

  const overflow = templateV2InfographicView({
    type: "infographic",
    data: { type: "gauge", value: 500, min_value: 0, max_value: 100 },
  });
  assert.equal(overflow?.ratio, 1);
  assert.equal(overflow?.label, "100%");

  assert.equal(
    templateV2InfographicView({ type: "infographic", data: { type: "sankey" } }),
    null
  );
});
