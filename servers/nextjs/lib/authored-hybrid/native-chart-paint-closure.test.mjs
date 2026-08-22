import assert from "node:assert/strict";
import test from "node:test";

import sharp from "sharp";

import {
  parseAuthoredNativeChartPayload,
  prepareDeclaredNativeCharts,
} from "./native-chart.ts";
import { planLayerSafeNativeElements } from "./native-plan.ts";
import { assembleAuthoredHybridPptx } from "./pptx-assembler.ts";
import { createAuthoredHybridCoverageManifest } from "./export-metadata.ts";
import {
  inspectPptxEditability,
  inspectPptxNativeElementReadback,
} from "./pptx-quality-inspection.ts";
import { readPptxArchive, writePptxArchive } from "./pptx-archive.ts";

const bounds = (x, y, width, height) => ({
  px: { x, y, width, height },
  inches: { x: x / 96, y: y / 96, width: width / 96, height: height / 96 },
});

const base = (id, sourceIndex, zOrder, rect, opacity = 1) => ({
  id,
  domPath: `body > div:nth-of-type(${sourceIndex + 1})`,
  tagName: "div",
  sourceIndex,
  zOrder,
  cssZIndex: zOrder,
  bounds: rect,
  rotationDeg: 0,
  opacity,
});

const shape = (id, sourceIndex, zOrder, rect, opacity = 1) => ({
  ...base(id, sourceIndex, zOrder, rect, opacity),
  classification: { mode: "native", kind: "shape", confidence: "safe" },
  shape: {
    shape: "rectangle",
    fill: { hex: "2457D6", alpha: 1 },
    stroke: null,
    strokeWidthPt: 0,
    radiusPt: 0,
  },
});

const raster = (id, sourceIndex, zOrder, rect, parentElementId = null) => ({
  ...base(id, sourceIndex, zOrder, rect),
  parentElementId,
  classification: {
    mode: "raster",
    candidateKind: "complex",
    reasons: ["complex-content"],
  },
});

function chartPayload(overrides = {}) {
  return {
    chartType: "column",
    categories: ["Q1", "Q2", "Q3"],
    series: [
      { name: "Revenue", values: [12, 18, 27], color: "#2457D6" },
      { name: "Margin", values: [7, 11, 16], color: "#19A974" },
    ],
    title: "분기별 성과",
    fontFamily: "Aptos",
    fontSizePt: 11,
    textColor: "#243447",
    legend: {
      visible: true,
      position: "right",
      fontFamily: "IBM Plex Sans",
      fontSizePt: 9,
      color: "#40516A",
    },
    categoryAxis: {
      title: "분기",
      fontFamily: "Noto Sans KR",
      fontSizePt: 9,
      color: "#40516A",
    },
    valueAxis: {
      title: "백만 원",
      fontFamily: "Noto Sans KR",
      fontSizePt: 9,
      color: "#40516A",
    },
    palette: ["#2457D6", "#19A974"],
    ...overrides,
  };
}

function observedChart(fallbackReasons = ["complex-chart"]) {
  return {
    id: "chart.performance",
    identityAuthority: "declared-qid",
    parentElementId: null,
    domPath: "body > div:nth-of-type(1)",
    tagName: "canvas",
    sourceIndex: 0,
    cssZIndex: 3,
    boundsPx: { x: 120, y: 110, width: 720, height: 420 },
    rotationDeg: 0,
    opacity: 1,
    candidateKind: "complex",
    fallbackReasons,
  };
}

function declaredChart(payload = chartPayload()) {
  return {
    elementId: "chart.performance",
    kind: "chart",
    parentId: null,
    childIds: [],
    sourceDigest: `sha256:${"a".repeat(64)}`,
    requiredFeatures: ["chart.axes", "chart.legend", "chart.fonts"],
    payload,
  };
}

function skeletonPptx() {
  const contentTypes = '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/></Types>';
  const presentation = '<?xml version="1.0"?><p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldSz cx="12192000" cy="6858000"/></p:presentation>';
  const slide = '<?xml version="1.0"?><p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/></p:spTree></p:cSld></p:sld>';
  const rels = '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/></Relationships>';
  return writePptxArchive(new Map([
    ["[Content_Types].xml", Buffer.from(contentTypes)],
    ["ppt/presentation.xml", Buffer.from(presentation)],
    ["ppt/slides/slide1.xml", Buffer.from(slide)],
    ["ppt/slides/_rels/slide1.xml.rels", Buffer.from(rels)],
  ]));
}

test("bounded semantic chart becomes native OOXML with data, axes, legend, and font readback", async () => {
  const prepared = prepareDeclaredNativeCharts(
    [declaredChart()],
    [observedChart()],
  );
  assert.deepEqual([...prepared.rejections], []);
  assert.equal(prepared.charts.length, 1);

  const backplatePng = await sharp({
    create: {
      width: 1280,
      height: 720,
      channels: 4,
      background: { r: 0, g: 0, b: 0, alpha: 0 },
    },
  }).png().toBuffer();
  const pptx = assembleAuthoredHybridPptx(skeletonPptx(), [{
    slideNumber: 1,
    backplatePng,
    elements: [{ kind: "chart", source: prepared.charts[0] }],
  }]);
  const archive = readPptxArchive(pptx);
  const slideXml = archive.get("ppt/slides/slide1.xml").toString("utf8");
  const chartXml = archive.get("ppt/charts/chart1.xml").toString("utf8");
  const contentTypes = archive.get("[Content_Types].xml").toString("utf8");
  assert.match(slideXml, /<p:graphicFrame>/);
  assert.match(slideXml, /Presenton hybrid chart chart\.performance/);
  assert.match(contentTypes, /drawingml\.chart\+xml/);
  assert.match(chartXml, /<c:barChart>/);
  assert.match(chartXml, /<c:catAx>/);
  assert.match(chartXml, /<c:valAx>/);
  assert.match(chartXml, /<c:legendPos val="r"\/>/);
  assert.match(chartXml, /<a:latin typeface="Noto Sans KR"\/>/);

  const readback = inspectPptxNativeElementReadback(pptx);
  assert.equal(readback.length, 1);
  assert.equal(readback[0].kind, "chart");
  assert.deepEqual(readback[0].chart, {
    chartType: "column",
    categories: ["Q1", "Q2", "Q3"],
    series: [
      { name: "Revenue", values: [12, 18, 27] },
      { name: "Margin", values: [7, 11, 16] },
    ],
    hasCategoryAxis: true,
    hasValueAxis: true,
    legendPosition: "right",
  });
  assert.ok(readback[0].latinTypefaces.includes("Noto Sans KR"));
  const quality = inspectPptxEditability(pptx);
  assert.equal(quality.nativeChartElements, 1);
  assert.equal(quality.ooxmlReadback.nativeChart, 1);
  assert.equal(quality.status, "fully-editable");
  const coverage = createAuthoredHybridCoverageManifest(quality, {
    exportAttemptId: "attempt-chart-readback",
    presentationRevision: 7,
    canonicalSourceDigest: `sha256:${"b".repeat(64)}`,
    sourceDigest: `sha256:${"c".repeat(64)}`,
  });
  assert.equal(coverage.nativeChart, 1);
  // A pure package inspector has no pre-serialization semantic plan; logical
  // coverage remains zero while OOXML readback still proves the native chart.
  assert.equal(coverage.logicalCoverage.nativeChart, 0);
  assert.equal(coverage.ooxmlReadback.nativeChart, 1);
  assert.equal(coverage.slides[0].nativeChart, 1);
});

test("unsupported chart contracts and complex chart CSS remain raster islands with stable reasons", () => {
  assert.equal(
    parseAuthoredNativeChartPayload(chartPayload({ chartType: "scatter" })),
    null,
  );
  const unsupported = prepareDeclaredNativeCharts(
    [declaredChart(chartPayload({ chartType: "scatter" }))],
    [observedChart()],
  );
  assert.equal(
    unsupported.rejections.get("chart.performance"),
    "unsupported-native-chart",
  );
  const css = prepareDeclaredNativeCharts(
    [declaredChart()],
    [observedChart(["complex-chart", "filter"])],
  );
  assert.equal(
    css.rejections.get("chart.performance"),
    "unsupported-chart-css",
  );
});

test("paint graph closure converges across overlap, transparency, nesting, and backplate collisions", () => {
  const lower = shape("native.lower", 0, 0, bounds(0, 0, 100, 100));
  const middle = shape("native.middle", 1, 1, bounds(60, 0, 100, 100));
  const foregroundRaster = raster(
    "raster.foreground",
    2,
    2,
    bounds(130, 0, 60, 100),
  );
  const chained = planLayerSafeNativeElements(
    [lower, middle, foregroundRaster],
    [
      { kind: "shape", source: lower },
      { kind: "shape", source: middle },
    ],
    undefined,
    { retainedChildPaint: "none" },
  );
  assert.equal(chained.closureIterations, 3);
  assert.equal(chained.slideDemotedToRaster, true);
  assert.equal(chained.downgradeReceipt.state, "slideRaster");
  assert.equal(chained.downgradeReceipt.reason, "no-closed-native-z-band");
  assert.match(chained.closureDigest, /^sha256:[a-f0-9]{64}$/);
  assert.ok(chained.paintGraph.every((node) => node.paintOwner === "rasterIsland"));

  const translucentTop = shape(
    "native.translucent",
    1,
    1,
    bounds(20, 20, 100, 100),
    0.45,
  );
  const lowerRaster = raster("raster.lower", 0, 0, bounds(0, 0, 150, 150));
  const transparency = planLayerSafeNativeElements(
    [lowerRaster, translucentTop],
    [{ kind: "shape", source: translucentTop }],
  );
  assert.deepEqual(transparency.selected.map((item) => item.source.id), [
    "native.translucent",
  ]);
  assert.equal(
    transparency.paintGraph.find((node) => node.elementId === "native.translucent").zBand,
    1,
  );

  const nestedPanel = {
    ...shape("panel", 0, 0, bounds(100, 100, 300, 200)),
    domPath: "body > section:nth-of-type(1)",
  };
  nestedPanel.shape = { ...nestedPanel.shape, preserveContents: true };
  const nestedChild = {
    ...raster("panel.child", 1, 1, bounds(120, 120, 80, 60), "panel"),
    domPath: `${nestedPanel.domPath} > div:nth-of-type(1)`,
  };
  const nesting = planLayerSafeNativeElements(
    [nestedPanel, nestedChild],
    [{ kind: "shape", source: nestedPanel }],
    undefined,
    { retainedChildPaint: "slide-root" },
  );
  assert.equal(nesting.selected.length, 0);

  const canvas = shape("canvas", 0, 0, bounds(0, 0, 1280, 720));
  const backplatePaint = raster(
    "backplate.paint",
    1,
    1,
    bounds(0, 0, 1280, 720),
  );
  const collision = planLayerSafeNativeElements(
    [canvas, backplatePaint],
    [{ kind: "shape", source: canvas }],
  );
  assert.equal(collision.slideDemotedToRaster, true);
  assert.deepEqual(collision.demotions[0].blockedByElementIds, [
    "backplate.paint",
  ]);
});
