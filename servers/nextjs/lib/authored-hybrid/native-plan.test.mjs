import assert from "node:assert/strict";
import test from "node:test";

import sharp from "sharp";

import {
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

const style = (overrides = {}) => ({
  fontFamily: "Pretendard, sans-serif",
  fontFamilies: ["Pretendard", "sans-serif"],
  cjkFallbackFamilies: ["Malgun Gothic", "Noto Sans CJK KR"],
  fontSizePt: 24,
  fontWeight: 700,
  bold: true,
  italic: false,
  underline: false,
  strike: false,
  color: { hex: "123456", alpha: 1 },
  letterSpacingPt: 0,
  lineHeight: { points: 30, multiple: 1.25, source: "computed" },
  horizontalAlignment: "center",
  verticalAlignment: "middle",
  direction: "ltr",
  ...overrides,
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

test("rich CJK text, a simple shape, and a safe data image become native OOXML", async () => {
  const redPng = await sharp({
    create: { width: 8, height: 6, channels: 4, background: "#ff0000" },
  })
    .png()
    .toBuffer();
  const dataUrl = `data:image/png;base64,${redPng.toString("base64")}`;
  const elements = [
    {
      ...base("text-ko", 0, 0, bounds(96, 72, 600, 120)),
      rotationDeg: -7.5,
      opacity: 0.8,
      classification: { mode: "native", kind: "text", confidence: "safe" },
      text: {
        role: "title",
        plainText: "서울 Hybrid",
        paragraphs: ["서울 Hybrid"],
        style: style(),
        runs: [
          {
            text: "서울 ",
            bounds: bounds(96, 72, 120, 40),
            fragments: [bounds(96, 72, 120, 40)],
            style: style({ color: { hex: "123456", alpha: 1 } }),
          },
          {
            text: "Hybrid",
            bounds: bounds(216, 72, 160, 40),
            fragments: [bounds(216, 72, 160, 40)],
            style: style({ italic: true, color: { hex: "AA5500", alpha: 1 } }),
          },
        ],
      },
    },
    {
      ...base("shape-one", 1, 1, bounds(80, 240, 280, 100)),
      classification: { mode: "native", kind: "shape", confidence: "safe" },
      shape: {
        shape: "round-rectangle",
        fill: { hex: "00AA66", alpha: 0.75 },
        stroke: { hex: "003322", alpha: 1 },
        strokeWidthPt: 1.5,
        radiusPt: 12,
      },
    },
    {
      ...base("image-one", 2, 2, bounds(800, 100, 240, 180)),
      opacity: 0.65,
      classification: { mode: "native", kind: "image", confidence: "safe" },
      image: {
        src: dataUrl,
        alt: "안전한 붉은 이미지",
        naturalWidth: 8,
        naturalHeight: 6,
        objectFit: "cover",
        objectPosition: "50% 50%",
        crop: { left: 0, top: 0, right: 0, bottom: 0 },
      },
    },
  ];

  const prepared = await prepareNativeElements(elements);
  assert.deepEqual(prepared.map((item) => item.kind), ["text", "shape", "image"]);
  assert.ok(prepared[2].png.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])));

  const textXml = serializePreparedNativeElement(prepared[0], 3);
  assert.match(textXml, /<a:t xml:space="preserve">서울 <\/a:t>/);
  assert.match(textXml, /<a:t xml:space="preserve">Hybrid<\/a:t>/);
  assert.match(textXml, /<a:latin typeface="Pretendard"\/>/);
  assert.match(textXml, /<a:ea typeface="Malgun Gothic"\/>/);
  assert.match(textXml, /algn="ctr"/);
  assert.match(textXml, /anchor="ctr"/);
  assert.match(textXml, /rot="21150000"/);
  assert.match(textXml, /<a:alpha val="80000"\/>/);

  const shapeXml = serializePreparedNativeElement(prepared[1], 4);
  assert.match(shapeXml, /prst="roundRect"/);
  assert.match(shapeXml, /<a:srgbClr val="00AA66"><a:alpha val="75000"\/>/);
  assert.match(shapeXml, /<a:ln w="19050">/);

  const imageXml = serializePreparedNativeElement(prepared[2], 5, "rId99");
  assert.match(imageXml, /r:embed="rId99"/);
  assert.match(imageXml, /<a:alphaModFix amt="65000"\/>/);
});

test("invalid native images and z-order inversions stay on the raster backplate", async () => {
  const lowText = {
    ...base("low-text", 0, 0, bounds(100, 100, 300, 80)),
    classification: { mode: "native", kind: "text", confidence: "safe" },
    text: {
      role: "body",
      plainText: "editable",
      paragraphs: ["editable"],
      style: style(),
      runs: [],
    },
  };
  const isolatedShape = {
    ...base("isolated-shape", 1, 1, bounds(700, 500, 120, 60)),
    classification: { mode: "native", kind: "shape", confidence: "safe" },
    shape: {
      shape: "rectangle",
      fill: { hex: "445566", alpha: 1 },
      stroke: null,
      strokeWidthPt: 0,
      radiusPt: 0,
    },
  };
  const badImage = {
    ...base("bad-image", 2, 2, bounds(900, 500, 100, 80)),
    classification: { mode: "native", kind: "image", confidence: "safe" },
    image: {
      src: "data:image/png;base64,bm90LWEtcG5n",
      alt: "invalid",
      naturalWidth: 1,
      naturalHeight: 1,
      objectFit: "fill",
      objectPosition: "50% 50%",
      crop: { left: 0, top: 0, right: 0, bottom: 0 },
    },
  };
  const rasterAbove = {
    ...base("raster-above", 3, 3, bounds(120, 110, 250, 60)),
    classification: {
      mode: "raster",
      candidateKind: "complex",
      reasons: ["complex-content"],
    },
  };
  const elements = [lowText, isolatedShape, badImage, rasterAbove];
  const prepared = await prepareNativeElements(elements);
  assert.deepEqual(prepared.map((item) => item.source.id), ["low-text", "isolated-shape"]);

  const selected = selectLayerSafeNativeElements(elements, prepared);
  assert.deepEqual(selected.map((item) => item.source.id), ["isolated-shape"]);
  const explicitlyRejected = selectLayerSafeNativeElements(
    elements,
    prepared,
    new Set(["low-text"])
  );
  assert.deepEqual(explicitlyRejected, []);
});

test("raster fallback propagates through overlapping native layers", async () => {
  const nativeText = (id, sourceIndex, zOrder, rect) => ({
    ...base(id, sourceIndex, zOrder, rect),
    classification: { mode: "native", kind: "text", confidence: "safe" },
    text: {
      role: "body",
      plainText: id,
      paragraphs: [id],
      style: style(),
      runs: [],
    },
  });
  const low = nativeText("low", 0, 0, bounds(100, 100, 100, 100));
  const middle = nativeText("middle", 1, 1, bounds(150, 100, 100, 100));
  const highRaster = {
    ...base("high-raster", 2, 2, bounds(220, 100, 100, 100)),
    classification: {
      mode: "raster",
      candidateKind: "complex",
      reasons: ["complex-content"],
    },
  };
  const elements = [low, middle, highRaster];
  const prepared = await prepareNativeElements(elements);

  // The high raster overlaps the middle native object; once middle falls back,
  // its overlap with low must also keep low on the backplate.
  assert.deepEqual(selectLayerSafeNativeElements(elements, prepared), []);
});

test("a simple line remains an editable OOXML line instead of a rectangle", async () => {
  const element = {
    ...base("line-one", 0, 0, bounds(120, 360, 420, 4)),
    classification: { mode: "native", kind: "shape", confidence: "safe" },
    shape: {
      shape: "line",
      fill: null,
      stroke: { hex: "3366CC", alpha: 1 },
      strokeWidthPt: 2,
      radiusPt: 0,
    },
  };
  const prepared = await prepareNativeElements([element]);
  assert.equal(prepared.length, 1);
  const xml = serializePreparedNativeElement(prepared[0], 3);
  assert.match(xml, /<a:prstGeom prst="line">/);
  assert.match(xml, /<a:ln w="25400">/);
  assert.match(xml, /<a:srgbClr val="3366CC">/);
});
