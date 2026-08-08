import assert from "node:assert/strict";
import test from "node:test";

import {
  preparedNativeElementNonVisualIdCount,
  prepareNativeElements,
  selectLayerSafeNativeElements,
  serializePreparedNativeElement,
} from "./native-plan.ts";

const bounds = (x, y, width, height) => ({
  px: { x, y, width, height },
  inches: {
    x: x / 96,
    y: y / 96,
    width: width / 96,
    height: height / 96,
  },
});

const base = (id, sourceIndex, zOrder, rect) => ({
  id,
  domPath: `body > div:nth-of-type(${sourceIndex + 1})`,
  tagName: "div",
  sourceIndex,
  zOrder,
  cssZIndex: zOrder,
  bounds: rect,
  rotationDeg: 0,
  opacity: 1,
});

const rasterShape = (overrides = {}) => ({
  ...base("paint", 0, 0, bounds(100, 100, 400, 240)),
  classification: {
    mode: "raster",
    candidateKind: "shape",
    reasons: ["external-paint"],
  },
  shape: {
    shape: "rectangle",
    fill: { hex: "F4F7FB", alpha: 1 },
    stroke: null,
    strokeWidthPt: 0,
    radiusPt: 0,
  },
  ...overrides,
});

test("a promoted panel stays below its retained descendants without ignoring sibling occlusion", async () => {
  const panel = rasterShape({
    id: "panel",
    shape: {
      shape: "round-rectangle",
      fill: { hex: "F4F7FB", alpha: 1 },
      stroke: { hex: "C6D3E5", alpha: 1 },
      strokeWidthPt: 1,
      radiusPt: 12,
      preserveContents: true,
    },
  });
  const childArtwork = {
    ...base("panel-child", 1, 1, bounds(140, 140, 120, 80)),
    domPath: `${panel.domPath} > div:nth-of-type(1)`,
    classification: {
      mode: "raster",
      candidateKind: "complex",
      reasons: ["complex-content"],
    },
  };
  const prepared = await prepareNativeElements([panel, childArtwork], {
    includeRasterShapes: true,
  });

  assert.deepEqual(prepared.map((item) => item.source.id), ["panel"]);
  assert.deepEqual(
    selectLayerSafeNativeElements([panel, childArtwork], prepared).map(
      (item) => item.source.id
    ),
    ["panel"]
  );

  const overlappingSibling = {
    ...base("sibling-overlay", 2, 2, bounds(420, 160, 140, 80)),
    classification: {
      mode: "raster",
      candidateKind: "complex",
      reasons: ["complex-content"],
    },
  };
  assert.deepEqual(
    selectLayerSafeNativeElements(
      [panel, childArtwork, overlappingSibling],
      prepared
    ),
    []
  );

  assert.deepEqual(
    selectLayerSafeNativeElements(
      [panel, childArtwork],
      prepared,
      undefined,
      { retainedChildPaint: "slide-root" }
    ),
    []
  );

  const slideRoot = rasterShape({
    id: "slide-root",
    domPath: "body",
    shape: {
      shape: "rectangle",
      fill: { hex: "F4F7FB", alpha: 1 },
      stroke: null,
      strokeWidthPt: 0,
      radiusPt: 0,
      preserveContents: true,
    },
  });
  const rootPrepared = await prepareNativeElements(
    [slideRoot, childArtwork],
    { includeRasterShapes: true }
  );
  assert.deepEqual(
    selectLayerSafeNativeElements(
      [slideRoot, childArtwork],
      rootPrepared,
      undefined,
      { retainedChildPaint: "slide-root" }
    ).map((item) => item.source.id),
    ["slide-root"]
  );
});

test("affine pseudo paint is promoted only after it is flattened to safe freeform points", async () => {
  const flattenedPseudo = rasterShape({
    id: "skewed-pseudo",
    domPath: "body > div:nth-of-type(1)::before",
    classification: {
      mode: "raster",
      candidateKind: "shape",
      reasons: ["pseudo-element", "complex-transform"],
    },
    shape: {
      shape: "freeform",
      fill: { hex: "1F55D5", alpha: 0.9 },
      stroke: null,
      strokeWidthPt: 0,
      radiusPt: 0,
      points: [
        { x: 0.12, y: 0 },
        { x: 1, y: 0 },
        { x: 0.88, y: 1 },
        { x: 0, y: 1 },
      ],
      closed: true,
    },
  });
  const unflattenedPseudo = rasterShape({
    id: "unflattened-pseudo",
    sourceIndex: 1,
    zOrder: 1,
    domPath: "body > div:nth-of-type(2)::after",
    classification: {
      mode: "raster",
      candidateKind: "shape",
      reasons: ["pseudo-element", "complex-transform"],
    },
  });
  const invalidFreeform = rasterShape({
    id: "invalid-freeform",
    sourceIndex: 2,
    zOrder: 2,
    shape: {
      shape: "freeform",
      fill: { hex: "1F55D5", alpha: 1 },
      stroke: null,
      strokeWidthPt: 0,
      radiusPt: 0,
      points: [
        { x: 0, y: 0 },
        { x: 1.1, y: 0 },
        { x: 0, y: 1 },
      ],
      closed: true,
    },
  });

  const prepared = await prepareNativeElements(
    [flattenedPseudo, unflattenedPseudo, invalidFreeform],
    { includeRasterShapes: true }
  );
  assert.deepEqual(prepared.map((item) => item.source.id), ["skewed-pseudo"]);
  assert.match(serializePreparedNativeElement(prepared[0], 3), /<a:custGeom>/);
});

test("asymmetric-radius freeforms retain shadow and dashed per-side borders as editable shapes", async () => {
  const element = rasterShape({
    id: "paint-stack",
    rotationDeg: -6,
    classification: {
      mode: "raster",
      candidateKind: "shape",
      reasons: ["asymmetric-border-radius", "box-shadow"],
    },
    shape: {
      shape: "freeform",
      fill: { hex: "FFFFFF", alpha: 1 },
      stroke: null,
      strokeWidthPt: 0,
      radiusPt: 0,
      points: [
        { x: 0.08, y: 0 },
        { x: 1, y: 0 },
        { x: 1, y: 0.88 },
        { x: 0.92, y: 1 },
        { x: 0, y: 1 },
        { x: 0, y: 0.12 },
      ],
      closed: true,
      shadowLayers: [
        {
          offsetXPx: 5,
          offsetYPx: 7,
          spreadPx: 2,
          color: { hex: "102A56", alpha: 0.28 },
        },
      ],
      borderLines: [
        {
          side: "top",
          color: { hex: "1F55D5", alpha: 1 },
          widthPt: 1.5,
          dash: "dash",
        },
        {
          side: "bottom",
          color: { hex: "68A4FF", alpha: 1 },
          widthPt: 1,
          dash: "dot",
        },
      ],
    },
  });
  const prepared = await prepareNativeElements([element], {
    includeRasterShapes: true,
  });

  assert.equal(prepared.length, 1);
  assert.equal(preparedNativeElementNonVisualIdCount(prepared[0]), 4);
  const xml = serializePreparedNativeElement(prepared[0], 3);
  assert.equal((xml.match(/<p:sp>/g) ?? []).length, 4);
  assert.match(xml, /Presenton hybrid raster paint-stack shadow 1/);
  assert.match(xml, /<a:srgbClr val="102A56">/);
  assert.match(xml, /<a:prstDash val="dash"\/>/);
  assert.match(xml, /<a:prstDash val="dot"\/>/);
  assert.match(xml, /rot="21240000"/);
});

test("outline-only paint emits one editable outline without a transparent duplicate shape", async () => {
  const element = rasterShape({
    id: "outline-only",
    shape: {
      shape: "ellipse",
      fill: null,
      stroke: null,
      strokeWidthPt: 0,
      radiusPt: 0,
      outline: {
        color: { hex: "2457D6", alpha: 1 },
        widthPt: 1.5,
        offsetPx: 2,
        dash: "dot",
      },
    },
  });
  const prepared = await prepareNativeElements([element], {
    includeRasterShapes: true,
  });

  assert.equal(prepared.length, 1);
  assert.equal(preparedNativeElementNonVisualIdCount(prepared[0]), 1);
  const xml = serializePreparedNativeElement(prepared[0], 3);
  assert.equal((xml.match(/<p:sp>/g) ?? []).length, 1);
  assert.match(xml, /Presenton hybrid raster outline-only outline/);
  assert.match(xml, /<a:prstDash val="dot"\/>/);
});

test("thin filled CSS rails remain rectangles rather than becoming connector lines", async () => {
  const rail = rasterShape({
    id: "thin-rail",
    bounds: bounds(160, 340, 620, 2),
    shape: {
      shape: "rectangle",
      fill: { hex: "68A4FF", alpha: 1 },
      stroke: null,
      strokeWidthPt: 0,
      radiusPt: 0,
    },
  });
  const prepared = await prepareNativeElements([rail], {
    includeRasterShapes: true,
  });

  assert.equal(prepared.length, 1);
  const xml = serializePreparedNativeElement(prepared[0], 3);
  assert.match(xml, /<a:prstGeom prst="rect">/);
  assert.doesNotMatch(xml, /<a:prstGeom prst="line">/);
  assert.match(xml, /<a:srgbClr val="68A4FF">/);
});
