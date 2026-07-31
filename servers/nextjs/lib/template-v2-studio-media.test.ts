import assert from "node:assert/strict";
import test from "node:test";

import {
  clampStudioSelectionBounds,
  closestStudioPath,
  normalizeStudioImageFit,
  recolorStudioSvgMarkup,
  sameStudioSelectionBounds,
  studioImagePlacement,
  unionStudioCanvasRects,
} from "./template-v2-studio-media.ts";

test("image fit accepts legacy stretch and preserves fill semantics", () => {
  assert.equal(normalizeStudioImageFit("cover"), "cover");
  assert.equal(normalizeStudioImageFit("contain"), "contain");
  assert.equal(normalizeStudioImageFit("fill"), "fill");
  assert.equal(normalizeStudioImageFit("stretch"), "fill");
});

test("cover placement supports focus and zoom-out crop scales", () => {
  assert.deepEqual(
    studioImagePlacement({
      naturalWidth: 200,
      naturalHeight: 100,
      width: 100,
      height: 100,
      fit: "cover",
      focusX: 75,
      focusY: 25,
      cropScale: 0.5,
    }),
    { x: 0, y: 12.5, width: 100, height: 50 }
  );

  assert.deepEqual(
    studioImagePlacement({
      naturalWidth: 100,
      naturalHeight: 100,
      width: 100,
      height: 100,
      fit: "cover",
      focusX: 100,
      focusY: 0,
      cropScale: 2,
    }),
    { x: -100, y: 0, width: 200, height: 200 }
  );
});

test("contain uses focus for the free axis and fill stretches", () => {
  assert.deepEqual(
    studioImagePlacement({
      naturalWidth: 200,
      naturalHeight: 100,
      width: 100,
      height: 100,
      fit: "contain",
      focusY: 75,
    }),
    { x: 0, y: 37.5, width: 100, height: 50 }
  );
  assert.deepEqual(
    studioImagePlacement({
      naturalWidth: 200,
      naturalHeight: 100,
      width: 100,
      height: 100,
      fit: "stretch",
    }),
    { x: 0, y: 0, width: 100, height: 100 }
  );
});

test("selection bounds union and clamp stay in canvas DOM coordinates", () => {
  const union = unionStudioCanvasRects([
    { x: -20, y: 10, width: 50, height: 40 },
    { x: 25, y: 35, width: 100, height: 60 },
  ]);
  assert.deepEqual(union, { x: -20, y: 10, width: 145, height: 85 });
  assert.deepEqual(clampStudioSelectionBounds(union, { width: 90, height: 80 }), {
    left: 0,
    top: 10,
    width: 90,
    height: 70,
  });
  assert.equal(
    sameStudioSelectionBounds(
      { left: 1, top: 2, width: 3, height: 4 },
      { left: 1.1, top: 2.1, width: 3.1, height: 4.1 }
    ),
    true
  );
});

test("nested selection safely falls back to its closest registered ancestor", () => {
  const registered = new Set(["0", "0/children/2"]);
  assert.deepEqual(
    closestStudioPath([0, "children", 2, "children", 1], (candidate) =>
      registered.has(candidate.join("/"))
    ),
    [0, "children", 2]
  );
  assert.equal(
    closestStudioPath([1, "children", 0], (candidate) =>
      registered.has(candidate.join("/"))
    ),
    null
  );
});

test("icon SVG color replacement preserves none and paint servers", () => {
  assert.equal(
    recolorStudioSvgMarkup(
      '<svg fill="#111"><path stroke="#222"/><path fill="none"/><path fill="url(#a)"/></svg>',
      "#abcdef"
    ),
    '<svg fill="#abcdef"><path stroke="#abcdef"/><path fill="none"/><path fill="url(#a)"/></svg>'
  );
});
