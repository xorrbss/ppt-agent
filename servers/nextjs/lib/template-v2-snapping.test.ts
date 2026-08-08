import assert from "node:assert/strict";
import test from "node:test";

import {
  sameTemplateV2Guides,
  snapTemplateV2Bounds,
  templateV2GuideTargets,
  type TemplateV2Bounds,
  type TemplateV2BoundsNode,
} from "./template-v2-snapping.ts";

function box(
  x: number,
  y: number,
  width = 50,
  height = 20
): TemplateV2Bounds {
  return { x, y, width, height };
}

test("an axis without a target in reach falls back to the eight-pixel grid", () => {
  assert.deepEqual(snapTemplateV2Bounds(box(14, 19)).position, { x: 16, y: 16 });
  assert.deepEqual(snapTemplateV2Bounds(box(12, 20)).position, { x: 12, y: 20 });
  assert.deepEqual(snapTemplateV2Bounds(box(-9.5, 0)).position, { x: -8, y: 0 });
  assert.deepEqual(
    snapTemplateV2Bounds(box(24.2, 31.8), [], 1, 10).position,
    { x: 24.2, y: 31.8 }
  );
  assert.deepEqual(snapTemplateV2Bounds(box(14, 19)).guides, []);
});

test("edge alignment wins over the grid and reports the guide to draw", () => {
  const result = snapTemplateV2Bounds(box(100, 100), [box(98, 300)]);

  assert.deepEqual(result.position, { x: 98, y: 100 });
  assert.deepEqual(result.guides, [
    { orientation: "vertical", position: 98, start: 100, end: 320 },
  ]);
});

test("center and trailing edges align, and the closest candidate wins", () => {
  const centered = snapTemplateV2Bounds(box(100, 0, 100, 10), [
    box(149, 200, 2, 10),
  ]);
  assert.deepEqual(centered.position, { x: 100, y: 0 });
  assert.deepEqual(centered.guides, [
    { orientation: "vertical", position: 150, start: 0, end: 210 },
  ]);

  const trailing = snapTemplateV2Bounds(box(100, 100, 50, 20), [
    box(20, 118, 128, 4),
  ]);
  assert.deepEqual(trailing.position, { x: 98, y: 100 });
  assert.deepEqual(trailing.guides, [
    { orientation: "vertical", position: 148, start: 100, end: 122 },
    { orientation: "horizontal", position: 120, start: 20, end: 148 },
  ]);
});

test("targets beyond the threshold and unusable numbers stay inert", () => {
  const far = snapTemplateV2Bounds(box(100, 100), [box(104, 300)]);
  assert.deepEqual(far.position, { x: 100, y: 100 });
  assert.deepEqual(far.guides, []);

  const broken = snapTemplateV2Bounds(box(Number.NaN, 100), [box(98, 300)]);
  assert.ok(Number.isNaN(broken.position.x));
  assert.deepEqual(broken.guides, []);
});

test("guide targets skip the dragged nodes and always include the slide", () => {
  const node = (bounds: TemplateV2Bounds): TemplateV2BoundsNode => ({
    x: () => bounds.x,
    y: () => bounds.y,
    width: () => bounds.width,
    height: () => bounds.height,
  });
  const slide = box(0, 0, 1280, 720);
  const nodes = new Map<string, TemplateV2BoundsNode>([
    ["a", node(box(10, 10))],
    ["b", node(box(20, 20))],
  ]);

  assert.deepEqual(
    templateV2GuideTargets(nodes, ["a", "b", "missing"], new Set(["b"]), slide),
    [box(10, 10), slide]
  );
  assert.deepEqual(
    templateV2GuideTargets(nodes, ["a", "b"], new Set(["a", "b"]), slide),
    [slide]
  );
});

test("sameTemplateV2Guides compares guides field by field", () => {
  const guides = snapTemplateV2Bounds(box(100, 100), [box(98, 300)]).guides;

  assert.equal(sameTemplateV2Guides(guides, [...guides]), true);
  assert.equal(sameTemplateV2Guides(guides, []), false);
  assert.equal(
    sameTemplateV2Guides(guides, [{ ...guides[0], position: 99 }]),
    false
  );
});
