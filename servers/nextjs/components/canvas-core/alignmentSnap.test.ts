import assert from "node:assert/strict";
import test from "node:test";

import {
  createCanvasCoreAlignmentTargets,
  snapCanvasCoreBounds,
} from "./alignmentSnap.ts";

const canvas = { x: 0, y: 0, width: 1280, height: 720 };
const safeArea = { x: 40, y: 40, width: 1200, height: 640 };

test("snaps a moving element by its center and end anchors", () => {
  const targets = createCanvasCoreAlignmentTargets(safeArea, [
    { x: 700, y: 280, width: 200, height: 100 },
  ]);
  const result = snapCanvasCoreBounds({
    movingBounds: { x: 504, y: 181, width: 198, height: 100 },
    guideBounds: canvas,
    targets,
    threshold: 3,
  });

  assert.deepEqual(result.position, { x: 502, y: 180 });
  assert.deepEqual(result.guides.map(({ orientation, position }) => ({ orientation, position })), [
    { orientation: "vertical", position: 700 },
    { orientation: "horizontal", position: 280 },
  ]);
});

test("prefers the alignment frame center and keeps guides inside the canvas", () => {
  const targets = createCanvasCoreAlignmentTargets(safeArea, []);
  const result = snapCanvasCoreBounds({
    movingBounds: { x: 541, y: 311, width: 200, height: 100 },
    guideBounds: canvas,
    targets,
    threshold: 2,
  });

  assert.deepEqual(result.position, { x: 540, y: 310 });
  assert.deepEqual(result.guides, [
    { orientation: "vertical", position: 640, start: 0, end: 720 },
    { orientation: "horizontal", position: 360, start: 0, end: 1280 },
  ]);
});
