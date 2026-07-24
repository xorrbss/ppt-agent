import assert from "node:assert/strict";
import test from "node:test";

import {
  planStudioElement,
  rebaseStudioChild,
  resolveStudioPlanFrame,
} from "./template-v2-studio-plan.ts";

test("planStudioElement plans a vector from its point bounds", () => {
  const node = planStudioElement({
    type: "vector",
    points: [
      { x: 100, y: 50 },
      { x: 300, y: 50 },
      { x: 200, y: 150 },
      { x: 100, y: 50 },
    ],
    shape: "polygon",
  });
  assert.ok(node?.vector);
  assert.equal(node.vector.closed, true);
  // The element frame is the geometry bounds; points are rebased to that origin.
  assert.deepEqual(node.frame, { x: 100, y: 50, width: 200, height: 100 });
  assert.deepEqual(node.vector.points[0], { x: 0, y: 0 });
  assert.deepEqual(node.vector.points[2], { x: 100, y: 100 });
});

test("planStudioElement lays out flex children as relative placements", () => {
  const node = planStudioElement({
    type: "flex",
    direction: "row",
    gap: 10,
    position: { x: 40, y: 20 },
    size: { width: 500, height: 120 },
    children: [
      { type: "text", size: { width: 200, height: 100 }, runs: [{ text: "a" }] },
      { type: "text", size: { width: 150, height: 80 }, runs: [{ text: "b" }] },
    ],
  });
  assert.ok(node);
  assert.equal(node.children.length, 2);
  assert.deepEqual(
    { x: node.children[0].frame.x, y: node.children[0].frame.y },
    { x: 0, y: 0 }
  );
  // Second child starts after the first child's width plus the gap.
  assert.equal(node.children[1].frame.x, 210);
});

test("planStudioElement plans grid cells and chart series", () => {
  const grid = planStudioElement({
    type: "grid",
    columns: 2,
    gap: 20,
    position: { x: 0, y: 0 },
    size: { width: 420, height: 200 },
    children: [
      { type: "container", size: { width: 200, height: 90 } },
      { type: "container", size: { width: 200, height: 90 } },
      { type: "container", size: { width: 200, height: 90 } },
    ],
  });
  assert.ok(grid);
  assert.equal(grid.children[1].frame.x, 220);
  assert.equal(grid.children[2].frame.y, 110);

  const chart = planStudioElement({
    type: "chart",
    chart_type: "bar",
    position: { x: 0, y: 0 },
    size: { width: 320, height: 200 },
    categories: ["Q1", "Q2"],
    series: [{ name: "Revenue", values: [10, 30] }],
  });
  assert.equal(chart?.chart?.type, "bar");
  assert.deepEqual(chart.chart.series[0].values, [10, 30]);
});

test("planStudioElement fails soft to null instead of throwing", () => {
  // Below the export renderer's 80x60 chart minimum.
  assert.equal(
    planStudioElement({
      type: "chart",
      chart_type: "bar",
      size: { width: 40, height: 30 },
      series: [{ name: "s", values: [1] }],
    }),
    null
  );
  assert.equal(planStudioElement({ type: "not-a-type" }), null);
  assert.equal(
    planStudioElement({ type: "vector", points: "invalid" }),
    null
  );
});

test("resolveStudioPlanFrame substitutes fallbacks for auto dimensions", () => {
  assert.deepEqual(
    resolveStudioPlanFrame({ x: 5, y: 6, width: null, height: 90 }),
    { x: 5, y: 6, width: 240, height: 90 }
  );
});

test("rebaseStudioChild collapses position and pins the planned size", () => {
  const rebased = rebaseStudioChild(
    { type: "text", position: { x: 99, y: 99 }, size: { width: 10, height: 10 } },
    { x: 30, y: 40, width: 120, height: 60 }
  );
  assert.deepEqual(rebased.position, { x: 0, y: 0 });
  assert.deepEqual(rebased.size, { width: 120, height: 60 });
  // Auto (null) planned dimensions keep the child's own size untouched.
  const auto = rebaseStudioChild(
    { type: "text", size: { width: 10, height: 10 } },
    { x: 0, y: 0, width: null, height: null }
  );
  assert.deepEqual(auto.size, { width: 10, height: 10 });
});
