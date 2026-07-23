import assert from "node:assert/strict";
import test from "node:test";

import sharp from "sharp";

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
  wrapMode: "wrap",
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

test("text intersecting a slide edge is retained while off-slide text is rejected", async () => {
  const textElement = (id, rect, rotationDeg = 0) => ({
    ...base(id, 0, 0, rect),
    rotationDeg,
    classification: {
      mode: "raster",
      candidateKind: "text",
      reasons: ["outside-slide"],
    },
    text: {
      role: "body",
      plainText: id,
      paragraphs: [id],
      style: style(),
      runs: [],
    },
  });

  const prepared = await prepareNativeElements(
    [
      textElement("bottom-footer", bounds(76, 702, 688, 20)),
      textElement("rotated-edge-label", bounds(1245, -49, 9, 154), -90),
      textElement("fully-off-slide", bounds(100, 740, 300, 40)),
    ],
    { includeRasterText: true }
  );

  assert.deepEqual(
    prepared.map((item) => item.source.id),
    ["bottom-footer", "rotated-edge-label"]
  );
  assert.equal(prepared[0].source.bounds.px.y, 702);
  assert.equal(prepared[0].source.bounds.px.height, 18);
  assert.deepEqual(prepared[1].source.bounds, bounds(1245, -49, 9, 154));
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
  assert.match(textXml, /wrap="square"/);
  assert.match(textXml, /horzOverflow="clip"/);
  assert.match(textXml, /vertOverflow="clip"/);
  assert.match(textXml, /<a:noAutofit\/>/);
  assert.match(textXml, /<a:spcPct val="125000"\/>/);
  assert.doesNotMatch(textXml, /<a:normAutofit/);
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

test("nowrap text stays on one line without PowerPoint shrinking it", async () => {
  const element = {
    ...base("nowrap-title", 0, 0, bounds(1120, 40, 150, 50)),
    classification: { mode: "native", kind: "text", confidence: "safe" },
    text: {
      role: "title",
      plainText: "One line title",
      paragraphs: ["One line title"],
      style: style({ wrapMode: "no-wrap" }),
      runs: [],
    },
  };
  const prepared = await prepareNativeElements([element]);
  const xml = serializePreparedNativeElement(prepared[0], 3);
  assert.match(xml, /wrap="none"/);
  assert.match(xml, /horzOverflow="clip"/);
  assert.match(xml, /vertOverflow="clip"/);
  assert.match(xml, /<a:noAutofit\/>/);
  assert.doesNotMatch(xml, /<a:normAutofit/);
  assert.match(xml, /<a:off x="10668000" y="381000"\/>/);
  assert.match(xml, /<a:ext cx="1428750" cy="476250"\/>/);
});

test("editable text and runs are clamped to 9pt", async () => {
  const element = {
    ...base("minimum-font", 0, 0, bounds(100, 100, 400, 60)),
    classification: { mode: "native", kind: "text", confidence: "safe" },
    text: {
      role: "body",
      plainText: "small text",
      paragraphs: ["small text"],
      style: style({
        fontSizePt: 6,
        lineHeight: { points: 7.2, multiple: 1.2, source: "computed" },
      }),
      runs: [
        {
          text: "small text",
          bounds: bounds(100, 100, 100, 12),
          fragments: [],
          style: style({
            fontSizePt: 7.5,
            lineHeight: { points: 9, multiple: 1.2, source: "computed" },
          }),
        },
      ],
    },
  };

  const prepared = await prepareNativeElements([element]);
  assert.equal(prepared[0].source.text.style.fontSizePt, 9);
  assert.equal(prepared[0].source.text.runs[0].style.fontSizePt, 9);
  assert.ok(
    Math.abs(prepared[0].source.text.style.lineHeight.points - 10.8) < 1e-9
  );
  const xml = serializePreparedNativeElement(prepared[0], 3);
  assert.match(xml, /sz="900"/);
  assert.doesNotMatch(xml, /sz="[1-8]\d\d"/);
  assert.match(xml, /<a:noAutofit\/>/);
});

test("a browser-confirmed single line cannot rewrap into the content below", async () => {
  const element = {
    ...base("tight-title", 0, 0, bounds(400, 200, 230, 26)),
    classification: { mode: "native", kind: "text", confidence: "safe" },
    text: {
      role: "title",
      plainText: "Minimum Sufficient Change",
      paragraphs: ["Minimum Sufficient Change"],
      style: style({
        fontSizePt: 13.5,
        lineHeight: { points: 17, multiple: 1.259259, source: "computed" },
      }),
      runs: [],
    },
  };
  const prepared = await prepareNativeElements([element]);
  const xml = serializePreparedNativeElement(prepared[0], 3);
  assert.match(xml, /wrap="none"/);
});

test("captured visual lines retain PowerPoint wrapping as an overflow safety net", async () => {
  const runStyle = style({
    fontSizePt: 13.5,
    lineHeight: { points: 17, multiple: 1.259259, source: "computed" },
  });
  const element = {
    ...base("wrapped-body", 0, 0, bounds(400, 200, 230, 52)),
    classification: { mode: "native", kind: "text", confidence: "safe" },
    text: {
      role: "body",
      plainText: "First line\nSecond line",
      paragraphs: ["First line", "Second line"],
      style: runStyle,
      runs: [
        { text: "First line", bounds: bounds(400, 200, 100, 20), fragments: [], style: runStyle },
        { text: "\n", bounds: bounds(400, 220, 0, 0), fragments: [], style: runStyle },
        { text: "Second line", bounds: bounds(400, 226, 110, 20), fragments: [], style: runStyle },
      ],
    },
  };
  const prepared = await prepareNativeElements([element]);
  const xml = serializePreparedNativeElement(prepared[0], 3);
  assert.match(xml, /wrap="square"/);
  assert.match(xml, /<a:br\/>/);
  assert.equal((xml.match(/<a:p>/g) || []).length, 1);
  assert.match(xml, /<a:spcBef><a:spcPts val="0"\/><\/a:spcBef>/);
  assert.match(xml, /<a:spcAft><a:spcPts val="0"\/><\/a:spcAft>/);
});

test("editable mode promotes raster-classified text above retained artwork", async () => {
  const rasterText = {
    ...base("decorated-text", 0, 0, bounds(100, 100, 300, 80)),
    classification: {
      mode: "raster",
      candidateKind: "text",
      reasons: ["decorated-text", "occluded"],
    },
    text: {
      role: "body",
      plainText: "always editable",
      paragraphs: ["always editable"],
      style: style(),
      runs: [],
    },
  };
  const artworkAbove = {
    ...base("artwork", 1, 1, bounds(120, 110, 250, 60)),
    classification: {
      mode: "raster",
      candidateKind: "complex",
      reasons: ["complex-content"],
    },
  };
  const elements = [rasterText, artworkAbove];

  assert.deepEqual(await prepareNativeElements(elements), []);
  const prepared = await prepareNativeElements(elements, { includeRasterText: true });
  assert.deepEqual(prepared.map((item) => item.source.id), ["decorated-text"]);
  assert.deepEqual(selectLayerSafeNativeElements(elements, prepared), []);
  assert.deepEqual(
    selectLayerSafeNativeElements(elements, prepared, undefined, {
      promoteTextAboveRaster: true,
    }).map((item) => item.source.id),
    ["decorated-text"]
  );
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
    ...base("line-one", 0, 0, bounds(120, 360, 420, 2)),
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
  assert.match(xml, /<a:off x="1143000" y="3438525"\/><a:ext cx="4000500" cy="95"\/>/);
});

test("thin vertical lines are locked to a PowerPoint axis", async () => {
  const element = {
    ...base("vertical-line", 0, 0, bounds(200, 100, 2, 280)),
    classification: { mode: "native", kind: "shape", confidence: "safe" },
    shape: {
      shape: "line",
      fill: null,
      stroke: { hex: "65B889", alpha: 1 },
      strokeWidthPt: 1,
      radiusPt: 0,
    },
  };
  const prepared = await prepareNativeElements([element]);
  const xml = serializePreparedNativeElement(prepared[0], 3);

  assert.match(xml, /<a:off x="1914525" y="952500"\/><a:ext cx="95" cy="2667000"\/>/);
});

test("gradient rails export as one editable axis-aligned PowerPoint line", async () => {
  const element = {
    ...base("gradient-rail", 0, 0, bounds(40, 200, 800, 2)),
    classification: { mode: "native", kind: "shape", confidence: "safe" },
    shape: {
      shape: "line",
      fill: null,
      gradient: {
        angleDeg: 90,
        stops: [
          { color: { hex: "4F86D9", alpha: 1 }, position: 0 },
          { color: { hex: "ECA064", alpha: 1 }, position: 1 },
        ],
      },
      stroke: null,
      strokeWidthPt: 0,
      radiusPt: 0,
    },
  };
  const prepared = await prepareNativeElements([element]);
  const xml = serializePreparedNativeElement(prepared[0], 3);

  assert.equal(preparedNativeElementNonVisualIdCount(prepared[0]), 1);
  assert.equal((xml.match(/<p:sp>/g) ?? []).length, 1);
  assert.match(xml, /<a:off x="381000" y="1914525"\/><a:ext cx="7620000" cy="95"\/>/);
  assert.match(xml, /<a:noFill\/><a:ln w="19050"><a:gradFill rotWithShape="1">/);
  assert.match(xml, /<a:gs pos="0"><a:srgbClr val="4F86D9">/);
  assert.match(xml, /<a:gs pos="100000"><a:srgbClr val="ECA064">/);
  assert.doesNotMatch(xml, /gradient \d+/);
});

test("vertical gradient rails stay on one exact PowerPoint axis", async () => {
  const element = {
    ...base("vertical-gradient-rail", 0, 0, bounds(80, 40, 2, 620)),
    classification: { mode: "native", kind: "shape", confidence: "safe" },
    shape: {
      shape: "line",
      fill: null,
      gradient: {
        angleDeg: 180,
        stops: [
          { color: { hex: "4F86D9", alpha: 1 }, position: 0 },
          { color: { hex: "A58BE8", alpha: 1 }, position: 0.5 },
          { color: { hex: "ECA064", alpha: 1 }, position: 1 },
        ],
      },
      stroke: null,
      strokeWidthPt: 0,
      radiusPt: 0,
    },
  };
  const prepared = await prepareNativeElements([element]);
  const xml = serializePreparedNativeElement(prepared[0], 3);

  assert.equal((xml.match(/<p:sp>/g) ?? []).length, 1);
  assert.match(xml, /<a:off x="771525" y="381000"\/><a:ext cx="95" cy="5905500"\/>/);
  assert.match(xml, /<a:ln w="19050"><a:gradFill rotWithShape="1">/);
});

test("raster-classified CSS panels retain editable fill and per-side borders", async () => {
  const element = {
    ...base("panel-one", 0, 0, bounds(120, 180, 420, 220)),
    classification: {
      mode: "raster",
      candidateKind: "shape",
      reasons: ["unsupported-shape", "occluded"],
    },
    shape: {
      shape: "rectangle",
      fill: { hex: "F3F7FF", alpha: 1 },
      stroke: null,
      strokeWidthPt: 0,
      radiusPt: 0,
      borderLines: [
        { side: "top", color: { hex: "1F55D5", alpha: 1 }, widthPt: 2, dash: "dash" },
        { side: "left", color: { hex: "D6E2FF", alpha: 1 }, widthPt: 1 },
      ],
    },
  };

  assert.deepEqual(await prepareNativeElements([element]), []);
  const prepared = await prepareNativeElements([element], { includeRasterShapes: true });
  assert.equal(prepared.length, 1);
  assert.equal(preparedNativeElementNonVisualIdCount(prepared[0]), 3);
  assert.deepEqual(
    selectLayerSafeNativeElements([element], prepared, undefined, {
      promoteShapesAboveRaster: true,
    }).map((item) => item.source.id),
    ["panel-one"]
  );

  const xml = serializePreparedNativeElement(prepared[0], 3);
  assert.equal((xml.match(/<p:sp>/g) ?? []).length, 3);
  assert.match(xml, /Presenton hybrid raster panel-one/);
  assert.match(xml, /Presenton hybrid raster panel-one top border/);
  assert.match(xml, /Presenton hybrid raster panel-one left border/);
  assert.match(xml, /<a:srgbClr val="F3F7FF">/);
  assert.match(xml, /<a:srgbClr val="1F55D5">/);
  assert.match(xml, /<a:srgbClr val="D6E2FF">/);
  assert.match(xml, /<a:prstDash val="dash"\/>/);
});

test("CSS hard-stop gradients become editable PowerPoint gradient fills", async () => {
  const element = {
    ...base("gradient-panel", 0, 0, bounds(0, 0, 1280, 720)),
    classification: { mode: "native", kind: "shape", confidence: "safe" },
    shape: {
      shape: "rectangle",
      fill: null,
      gradient: {
        angleDeg: 90,
        stops: [
          { color: { hex: "071B4F", alpha: 1 }, position: 0 },
          { color: { hex: "071B4F", alpha: 1 }, position: 310 / 1280 },
          { color: { hex: "1848C8", alpha: 1 }, position: 310 / 1280 },
          { color: { hex: "1848C8", alpha: 1 }, position: 338 / 1280 },
          { color: { hex: "FFFFFF", alpha: 1 }, position: 338 / 1280 },
          { color: { hex: "FFFFFF", alpha: 1 }, position: 1 },
        ],
      },
      stroke: null,
      strokeWidthPt: 0,
      radiusPt: 0,
    },
  };

  const prepared = await prepareNativeElements([element]);
  assert.equal(prepared.length, 1);
  const xml = serializePreparedNativeElement(prepared[0], 3);
  assert.match(xml, /<a:gradFill rotWithShape="1">/);
  assert.match(xml, /<a:gs pos="24219"><a:srgbClr val="071B4F">/);
  assert.match(xml, /<a:gs pos="26406"><a:srgbClr val="FFFFFF">/);
  assert.match(xml, /<a:lin ang="0" scaled="1"\/>/);
});

test("SVG connector samples become editable PowerPoint freeform geometry", async () => {
  const element = {
    ...base("connector", 0, 0, bounds(100, 120, 360, 140)),
    classification: { mode: "native", kind: "shape", confidence: "safe" },
    shape: {
      shape: "freeform",
      fill: null,
      stroke: { hex: "2878D8", alpha: 1 },
      strokeWidthPt: 2.25,
      radiusPt: 0,
      points: [
        { x: 0, y: 0 },
        { x: 0.45, y: 0 },
        { x: 0.65, y: 1 },
        { x: 1, y: 1 },
      ],
      closed: false,
      endArrow: "triangle",
      dash: "dash",
      lineCap: "round",
      lineJoin: "round",
    },
  };

  const prepared = await prepareNativeElements([element]);
  const xml = serializePreparedNativeElement(prepared[0], 3);
  assert.match(xml, /<a:custGeom>/);
  assert.match(xml, /<a:moveTo><a:pt x="0" y="0"\/><\/a:moveTo>/);
  assert.match(xml, /<a:lnTo><a:pt x="100000" y="100000"\/><\/a:lnTo>/);
  assert.match(xml, /<a:ln w="[0-9]+" cap="rnd">/);
  assert.match(xml, /<a:prstDash val="dash"\/>/);
  assert.match(xml, /<a:round\/>/);
  assert.match(xml, /<a:tailEnd type="triangle" w="lg" len="lg"\/>/);
  assert.doesNotMatch(xml, /<a:close\/>/);
});

test("false browser line breaks in a single-line box collapse to spaces", async () => {
  const element = {
    ...base("compact-step", 0, 0, bounds(100, 100, 134, 19)),
    classification: { mode: "native", kind: "text", confidence: "safe" },
    text: {
      role: "caption",
      plainText: "01\nNatural language",
      paragraphs: ["01", "Natural language"],
      style: style({
        fontSizePt: 9,
        lineHeight: { points: 11, multiple: 1.22, source: "computed" },
      }),
      runs: [],
    },
  };

  const prepared = await prepareNativeElements([element]);
  const xml = serializePreparedNativeElement(prepared[0], 3);
  assert.match(xml, /<a:t xml:space="preserve">01 Natural language<\/a:t>/);
  assert.doesNotMatch(xml, /<a:br\/>/);
  assert.match(xml, /wrap="none"/);
});

test("decorated text becomes one editable PowerPoint shape with safe text insets", async () => {
  const element = {
    ...base("badge", 0, 0, bounds(124, 120, 336, 60)),
    classification: { mode: "native", kind: "text", confidence: "safe" },
    text: {
      role: "caption",
      plainText: "Editable badge",
      paragraphs: ["Editable badge"],
      style: style(),
      runs: [],
      containerShape: {
        bounds: bounds(100, 100, 400, 100),
        shape: {
          shape: "round-rectangle",
          fill: { hex: "102A56", alpha: 1 },
          stroke: { hex: "68A4FF", alpha: 1 },
          strokeWidthPt: 1,
          radiusPt: 12,
        },
      },
    },
  };
  const prepared = await prepareNativeElements([element]);
  const xml = serializePreparedNativeElement(prepared[0], 3);

  assert.match(xml, /<a:off x="952500" y="952500"\/>/);
  assert.match(xml, /<a:ext cx="3810000" cy="952500"\/>/);
  assert.match(xml, /prst="roundRect"/);
  assert.match(xml, /<a:srgbClr val="102A56">/);
  assert.match(xml, /<a:srgbClr val="68A4FF">/);
  assert.match(xml, /lIns="228600"/);
  assert.match(xml, /rIns="381000"/);
  assert.match(xml, /tIns="190500"/);
  assert.match(xml, /bIns="190500"/);
  assert.doesNotMatch(xml, /txBox="1"/);
});

test("backplate decoration keeps a transparent outer box for multiline fit", async () => {
  const element = {
    ...base("two-line-badge", 0, 0, bounds(124, 120, 106, 21)),
    classification: { mode: "native", kind: "text", confidence: "safe" },
    text: {
      role: "caption",
      plainText: "01\nNatural language",
      paragraphs: ["01", "Natural language"],
      style: style({
        fontSizePt: 8.25,
        lineHeight: { points: 9.9, multiple: 1.2, source: "computed" },
      }),
      runs: [],
      containerShape: {
        bounds: bounds(120, 110, 134, 40),
        shape: {
          shape: "round-rectangle",
          fill: null,
          stroke: null,
          strokeWidthPt: 0,
          radiusPt: 8,
        },
      },
    },
  };
  const prepared = await prepareNativeElements([element]);
  const xml = serializePreparedNativeElement(prepared[0], 3);

  assert.match(xml, /<a:off x="1143000" y="1047750"\/>/);
  assert.match(xml, /<a:ext cx="1276350" cy="381000"\/>/);
  assert.match(xml, /<a:noFill\/><a:ln><a:noFill\/><\/a:ln>/);
  assert.doesNotMatch(xml, /val="FFFFFF"/);
  assert.doesNotMatch(xml, /val="2878D8"/);
  assert.match(xml, /<a:br\/>/);
  const topInset = Number(xml.match(/tIns="(\d+)"/)?.[1]);
  const bottomInset = Number(xml.match(/bIns="(\d+)"/)?.[1]);
  assert.ok(topInset < 95250, `expected reclaimed top padding, got ${topInset}`);
  assert.ok(bottomInset < 180975, `expected reclaimed bottom padding, got ${bottomInset}`);
});

test("standalone multiline text boxes gain CJK-safe PowerPoint height", async () => {
  const element = {
    ...base("two-line-summary", 0, 0, bounds(280, 430, 310, 34.1)),
    classification: { mode: "native", kind: "text", confidence: "safe" },
    text: {
      role: "body",
      plainText: "Private GPU-based sLLM\nMulti-agent · RAG",
      paragraphs: ["Private GPU-based sLLM", "Multi-agent · RAG"],
      style: style({
        fontSizePt: 8.25,
        lineHeight: { points: 12.7875, multiple: 1.55, source: "computed" },
        horizontalAlignment: "left",
        verticalAlignment: "top",
      }),
      runs: [],
    },
  };
  const prepared = await prepareNativeElements([element]);
  const xml = serializePreparedNativeElement(prepared[0], 3);
  const heightEmu = Number(xml.match(/<a:ext cx="\d+" cy="(\d+)"\/>/)?.[1]);

  assert.ok(heightEmu > Math.round(34.1 * 9525));
  assert.equal(heightEmu, Math.round(49.2 * 9525));
  assert.match(xml, /<a:br\/>/);
});

test("standalone multiline boxes account for the largest run on each line", async () => {
  const normalStyle = style({
    fontSizePt: 28.5,
    lineHeight: { points: 32.205, multiple: 1.13, source: "computed" },
  });
  const emphasisStyle = style({
    fontSizePt: 41.25,
    lineHeight: { points: 46.6125, multiple: 1.13, source: "computed" },
  });
  const element = {
    ...base("mixed-title", 0, 0, bounds(54, 63, 684, 105.1)),
    classification: { mode: "native", kind: "text", confidence: "safe" },
    text: {
      role: "title",
      plainText: "3년의 운영을\n축적 → 학습 → 판단 성능으로",
      paragraphs: ["3년의 운영을", "축적 → 학습 → 판단 성능으로"],
      style: normalStyle,
      runs: [
        { text: "3년", bounds: bounds(54, 63, 86, 55), fragments: [], style: emphasisStyle },
        { text: "의 운영을", bounds: bounds(140, 78, 170, 38), fragments: [], style: normalStyle },
        { text: "\n", bounds: bounds(0, 0, 0, 0), fragments: [], style: normalStyle },
        { text: "축적 → 학습 → 판단 성능으로", bounds: bounds(54, 124, 420, 38), fragments: [], style: normalStyle },
      ],
    },
  };
  const prepared = await prepareNativeElements([element]);
  const xml = serializePreparedNativeElement(prepared[0], 3);
  const heightEmu = Number(xml.match(/<a:ext cx="\d+" cy="(\d+)"\/>/)?.[1]);

  assert.ok(heightEmu >= Math.round(117 * 9525));
  assert.ok(heightEmu <= Math.round(118 * 9525));
  assert.match(xml, /<a:br\/>/);
});
