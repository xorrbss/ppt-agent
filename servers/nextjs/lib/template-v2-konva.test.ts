import assert from "node:assert/strict";
import test from "node:test";

import {
  elementCapabilities,
  layoutTemplateV2List,
  layoutTemplateV2Table,
  templateV2InfographicView,
} from "./template-v2-konva.ts";
import { snapTemplateV2Position } from "./template-v2-snapping.ts";
import { translateTemplateV2Vector } from "./template-v2-vector.ts";

test("elementCapabilities grants move/resize/rotate to content and layout types", () => {
  for (const type of ["text-list", "table", "infographic", "chart", "flex", "grid"]) {
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
  assert.deepEqual(elementCapabilities({ type: "vector" }), {
    move: true,
    resize: false,
    rotate: false,
  });
});

test("vector movement translates every authored point by the canvas delta", () => {
  const vector = {
    type: "vector",
    points: [
      { x: 100.125, y: 50, pressure: 0.5 },
      { x: 300, y: 150 },
    ],
    curve: { type: "smooth" },
  };

  assert.deepEqual(translateTemplateV2Vector(vector, 10.005, -4.126), {
    ...vector,
    points: [
      { x: 110.13, y: 45.87, pressure: 0.5 },
      { x: 310.01, y: 145.87 },
    ],
  });
  assert.equal(translateTemplateV2Vector(vector, 0, 0), vector);
  const malformed = {
    type: "vector",
    points: [{ x: 1, y: 2 }, { x: "bad", y: 4 }],
  };
  assert.equal(translateTemplateV2Vector(malformed, 5, 5), malformed);
});

test("canvas snapping uses an eight-pixel grid inside a bounded threshold", () => {
  assert.deepEqual(snapTemplateV2Position({ x: 14, y: 19 }), { x: 16, y: 16 });
  assert.deepEqual(snapTemplateV2Position({ x: 12, y: 20 }), { x: 12, y: 20 });
  assert.deepEqual(snapTemplateV2Position({ x: -9.5, y: 0 }), { x: -8, y: 0 });
  assert.deepEqual(
    snapTemplateV2Position({ x: 24.2, y: 31.8 }, 10, 1),
    { x: 24.2, y: 31.8 }
  );
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
