import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { runInNewContext } from "node:vm";

import sharp from "sharp";

import { backplatePixelSize, BACKPLATE_SCALE } from "./capture-scale.ts";
import { resolveScriptAwareTypeface } from "./font-policy.ts";
import { instrumentAuthoredHtml } from "./html-instrumentation.ts";
import { LOCAL_FIT_BROWSER_SOURCE } from "./local-fit-browser.ts";
import { planLayerSafeNativeElements, prepareNativeElements } from "./native-plan.ts";
import {
  buildBrowserObservationReceiptV1,
  digestObservedDeclaredAssets,
  reconcileDeclaredObservation,
} from "./semantic-reconciliation.ts";
import { semanticRasterOwnerQids } from "./slide-layer.ts";
import { assembleAuthoredHybridPptx } from "./pptx-assembler.ts";
import { inspectPptxNativeElementReadback } from "./pptx-quality-inspection.ts";
import { readPptxArchive, writePptxArchive } from "./pptx-archive.ts";

const { width: BACKPLATE_WIDTH, height: BACKPLATE_HEIGHT } = backplatePixelSize();
const digest = (value) =>
  `sha256:${createHash("sha256").update(value).digest("hex")}`;

test("local-fit export verifies declared fallback fonts without changing measured size", () => {
  const observe = ({ family, installed = [], status = "loaded", checked = true }) => {
    const style = { fontFamily: family, fontStyle: "normal", fontWeight: "400",
      fontSize: "13px", display: "block", visibility: "visible", transform: "none",
      scale: "none", rotate: "none", zoom: "1" };
    const owner = { parentElement: null, getAttribute: () => "body.note",
      closest: (selector) => selector === "[data-qid]" ? owner : null };
    const text = { parentElement: owner, textContent: "Fallback 123" };
    const fonts = [];
    fonts.status = status;
    fonts.check = () => checked;
    const document = { body: owner, fonts,
      createTreeWalker() {
        let visited = false;
        return { currentNode: null, nextNode() {
          if (visited) return false;
          visited = true; this.currentNode = text; return text;
        } };
      },
      createElement: () => ({ getContext: () => ({ font: "",
        measureText() {
          return { width: installed.some((name) => this.font.includes(JSON.stringify(name))) ? 123 : 100 };
        },
      }) }),
    };
    return runInNewContext(LOCAL_FIT_BROWSER_SOURCE + "\nobserveLocalFitFonts()", {
      document, NodeFilter: { SHOW_TEXT: 4 }, getComputedStyle: () => style,
      innerWidth: 1280, innerHeight: 720, devicePixelRatio: 1,
    }).runs[0];
  };
  const fallback = observe({ family: '"IBM Plex Mono","Consolas",monospace', installed: ["Consolas"] });
  assert.equal(fallback.font_loaded, true);
  assert.equal(fallback.resolved_font_family, "Consolas");
  assert.equal(fallback.font_resolution_source, "installed-local-font");
  assert.equal(fallback.font_px, 13);
  assert.equal(fallback.effective_text_scale, 1);
  assert.equal(fallback.font_px * 0.75, 9.75);
  const generic = observe({ family: '"Missing",monospace' });
  assert.equal(generic.font_loaded, true);
  assert.equal(generic.font_resolution_source, "generic-fallback");
  assert.equal(observe({ family: '"Missing"' }).font_loaded, false);
  assert.equal(observe({ family: '"Missing",monospace', status: "loading" }).font_loaded, false);
  assert.equal(observe({ family: '"Missing",monospace', checked: false }).font_loaded, false);
  assert.equal(observe({ family: '"Comma, Font",monospace', installed: ["Comma, Font"] }).resolved_font_family, "Comma, Font");
});
const bounds = (x, y, width, height) => ({
  px: { x, y, width, height },
  inches: { x: x / 96, y: y / 96, width: width / 96, height: height / 96 },
});
const base = (id, zOrder, rect) => ({
  id,
  identityAuthority: "declared-qid",
  parentElementId: null,
  domPath: `body > [data-qid="${id}"]`,
  tagName: "div",
  sourceIndex: zOrder,
  zOrder,
  cssZIndex: null,
  bounds: bounds(...rect),
  rotationDeg: 0,
  opacity: 1,
});
const textStyle = {
  fontFamily: "Noto Serif KR",
  fontFamilies: ["Noto Serif KR", "serif"],
  cjkFallbackFamilies: ["Noto Serif KR", "Batang", "serif"],
  fontSizePt: 24,
  fontWeight: 400,
  bold: false,
  italic: false,
  underline: false,
  strike: false,
  color: { hex: "112233", alpha: 1 },
  letterSpacingPt: 0,
  lineHeight: { points: 30, multiple: 1.25, source: "computed" },
  horizontalAlignment: "left",
  verticalAlignment: "top",
  direction: "ltr",
  wrapMode: "wrap",
};

test("Fidelity instrumentation preserves authored paint/layout CSS", () => {
  const source = "<html><head><style>.x{font:7px serif;animation:none}</style></head><body><p class='x'>한글 A1</p></body></html>";
  const instrumented = instrumentAuthoredHtml(source, {
    captureMode: "fidelity",
    completionToken: "receipt-token",
  });
  assert.match(instrumented, /\.x\{font:7px serif;animation:none\}/);
  assert.match(instrumented, /"captureMode":"fidelity"/);
  assert.doesNotMatch(instrumented, /__presenton_authored_hybrid_stability_v1__/);
  assert.doesNotMatch(instrumented, /data-presenton-minimum-font-size/);
  assert.doesNotMatch(instrumented, /applyFontFallbackPolicy/);
});

test("declared QID reconciliation ignores diagnostic DOM path and fails closed on ownership", () => {
  const sourceDigest = digest("semantic-title");
  const declared = [{
    elementId: "title.main",
    kind: "text",
    parentId: null,
    childIds: [],
    sourceDigest,
    requiredFeatures: [],
  }];
  const observation = {
    viewport: { widthPx: 1280, heightPx: 720, devicePixelRatio: BACKPLATE_SCALE },
    elements: [{
      id: "title.main",
      identityAuthority: "declared-qid",
      parentElementId: null,
      domPath: "body > section:nth-of-type(999) > h1",
      tagName: "h1",
      sourceIndex: 998,
      cssZIndex: null,
      boundsPx: { x: 40, y: 50, width: 500, height: 80 },
      rotationDeg: 0,
      opacity: 1,
      candidateKind: "text",
      fallbackReasons: [],
      text: {
        role: "title",
        plainText: "한글 A1",
        paragraphs: ["한글 A1"],
        style: textStyle,
        runs: [],
      },
    }],
    warnings: [],
    appliedPromotedElementIds: [],
    rejectedPromotedElementIds: [],
    renderReadiness: {
      schemaVersion: "browser-render-readiness/v1",
      status: "ready",
      fontStatus: "loaded",
      imageCount: 0,
      decodedImageCount: 0,
      unresolvedResources: [],
      mutationFreeWindowMs: 100,
      continuousPaintEpochs: 2,
      captureEpoch: 7,
      captureMode: "observation",
    },
  };
  assert.equal(
    reconcileDeclaredObservation(declared, observation).elements[0].state,
    "matched"
  );
  const receipt = buildBrowserObservationReceiptV1(declared, observation, {
    inputBundleDigest: digest("bundle"),
    rendererSourceDigest: digest("renderer"),
    rendererVersion: "chromium/test",
    policyVersion: "reconciliation-policy/v1",
    fontSetDigest: digest("fonts"),
    capturedPngDigest: digest("png"),
  });
  assert.equal(receipt.elements[0].elementId, "title.main");
  assert.equal(receipt.elements[0].ordinal, 0);
  assert.match(receipt.receiptDigest, /^sha256:[a-f0-9]{64}$/);

  const childDeclared = [
    { ...declared[0], childIds: ["shape.child"] },
    {
      elementId: "shape.child",
      kind: "shape",
      parentId: "title.main",
      childIds: [],
      sourceDigest: digest("shape"),
    },
  ];
  assert.throws(
    () => reconcileDeclaredObservation(childDeclared, observation),
    /authored_semantic_qid_mismatch/
  );
});

test("raster ownership excludes admitted native QIDs with residual paint telemetry", () => {
  const fallbackElements = [
    { elementId: "shape.native-with-shadow" },
    { elementId: "shape.demoted" },
  ];

  assert.deepEqual(
    semanticRasterOwnerQids(
      fallbackElements,
      new Set(["shape.native-with-shadow"]),
    ),
    ["shape.demoted"],
  );
});

test("OOXML readback recovers one native owner from raster-classified shape fragments", async () => {
  const source = {
    ...base("shape.raster-admitted", 0, [80, 70, 280, 120]),
    classification: {
      mode: "raster",
      candidateKind: "shape",
      reasons: ["external-paint"],
    },
    shape: {
      shape: "rectangle",
      fill: { hex: "2457D6", alpha: 1 },
      stroke: null,
      strokeWidthPt: 0,
      radiusPt: 0,
      borderLines: [{
        side: "bottom",
        color: { hex: "0B1F3A", alpha: 1 },
        widthPt: 1,
        dash: "solid",
      }],
    },
  };
  const prepared = await prepareNativeElements([source], {
    includeRasterShapes: true,
  });
  const backplatePng = await sharp({
    create: {
      width: BACKPLATE_WIDTH,
      height: BACKPLATE_HEIGHT,
      channels: 4,
      background: { r: 0, g: 0, b: 0, alpha: 0 },
    },
  }).png().toBuffer();
  const skeleton = writePptxArchive(new Map([
    ["[Content_Types].xml", Buffer.from('<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/></Types>')],
    ["ppt/presentation.xml", Buffer.from('<?xml version="1.0"?><p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldSz cx="12192000" cy="6858000"/></p:presentation>')],
    ["ppt/slides/slide1.xml", Buffer.from('<?xml version="1.0"?><p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/></p:spTree></p:cSld></p:sld>')],
    ["ppt/slides/_rels/slide1.xml.rels", Buffer.from('<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/></Relationships>')],
  ]));
  const pptx = assembleAuthoredHybridPptx(skeleton, [{
    slideNumber: 1,
    backplatePng,
    elements: prepared,
  }]);

  assert.deepEqual(
    inspectPptxNativeElementReadback(pptx).map(({ elementId, kind }) => ({
      elementId,
      kind,
    })),
    [{ elementId: "shape.raster-admitted", kind: "shape" }],
  );
});

test("declared image asset authority uses decoded bytes and fails closed on mismatch", () => {
  const pngBytes = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAEElEQVR42mP8z8AARQAFAAH+Av6pAAAAAElFTkSuQmCC",
    "base64",
  );
  const dataUrl = `data:image/png;base64,${pngBytes.toString("base64")}`;
  const assetDigest = digest(pngBytes);
  const declared = [{
    elementId: "image.hero",
    kind: "image",
    parentId: null,
    childIds: [],
    sourceDigest: digest("image-source"),
    assetContentDigest: assetDigest,
  }];
  const observation = {
    viewport: { widthPx: 1280, heightPx: 720, devicePixelRatio: BACKPLATE_SCALE },
    elements: [{
      id: "image.hero",
      identityAuthority: "declared-qid",
      parentElementId: null,
      domPath: "body > img",
      tagName: "img",
      sourceIndex: 0,
      cssZIndex: null,
      boundsPx: { x: 0, y: 0, width: 1, height: 1 },
      rotationDeg: 0,
      opacity: 1,
      candidateKind: "image",
      fallbackReasons: [],
      image: {
        src: dataUrl,
        alt: "pixel",
        naturalWidth: 1,
        naturalHeight: 1,
        objectFit: "fill",
        objectPosition: "50% 50%",
        crop: { left: 0, top: 0, right: 0, bottom: 0 },
      },
    }],
    warnings: [],
    appliedPromotedElementIds: [],
    rejectedPromotedElementIds: [],
  };
  const observedDigests = digestObservedDeclaredAssets(declared, observation);
  assert.equal(observedDigests["image.hero"], assetDigest);
  assert.equal(
    reconcileDeclaredObservation(declared, observation, observedDigests).elements[0].state,
    "matched",
  );
  assert.throws(
    () => reconcileDeclaredObservation(declared, observation, {
      "image.hero": digest("different-image"),
    }),
    /authored_semantic_asset_mismatch/,
  );
});

test("single-backplate planner reaches a fixed point and records z-band demotions", () => {
  // identityAuthority is pinned to "compatibility-ordinal" (not the file's
  // default "declared-qid" from base()) so this exercises the iterative
  // single-backplate-fixed-point demotion path this test targets. Since
  // 2971728b6 (feat(authored): harden release generation and hybrid
  // evidence), an all-declared-qid element set takes a different
  // "audited-multi-z-band" shortcut (see the "declared paint order..." test
  // in native-plan.test.mjs) that does not perform overlap-based demotion.
  const nativeShape = (id, zOrder, rect) => ({
    ...base(id, zOrder, rect),
    identityAuthority: "compatibility-ordinal",
    classification: { mode: "native", kind: "shape", confidence: "safe" },
    shape: {
      shape: "rectangle",
      fill: { hex: "2457D6", alpha: 1 },
      stroke: null,
      strokeWidthPt: 0,
      radiusPt: 0,
    },
  });
  const lower = nativeShape("native.lower", 0, [0, 0, 100, 100]);
  const middle = nativeShape("native.middle", 1, [60, 0, 100, 100]);
  const upperRaster = {
    ...base("chart.raster", 2, [130, 0, 60, 100]),
    identityAuthority: "compatibility-ordinal",
    classification: {
      mode: "raster",
      candidateKind: "complex",
      reasons: ["complex-chart"],
    },
  };
  const prepared = [
    { kind: "shape", source: lower },
    { kind: "shape", source: middle },
  ];
  const plan = planLayerSafeNativeElements(
    [lower, middle, upperRaster],
    prepared,
    undefined,
    { retainedChildPaint: "none" }
  );
  assert.equal(plan.fixedPointReached, true);
  assert.equal(plan.slideDemotedToRaster, true);
  assert.deepEqual(plan.selected, []);
  assert.deepEqual(
    plan.demotions.map((item) => item.elementId),
    ["native.lower", "native.middle"]
  );
  assert.ok(plan.demotions.every((item) => item.reason === "z-band-unsafe"));
});

test("serializer preserves native paint order and CJK serif run readback", async () => {
  const CONTENT_TYPES = `<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/></Types>`;
  const PRESENTATION = `<?xml version="1.0"?><p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldSz cx="12192000" cy="6858000"/></p:presentation>`;
  const SLIDE = `<?xml version="1.0"?><p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/></p:spTree></p:cSld></p:sld>`;
  const RELS = `<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/></Relationships>`;
  const skeleton = writePptxArchive(new Map([
    ["[Content_Types].xml", Buffer.from(CONTENT_TYPES)],
    ["ppt/presentation.xml", Buffer.from(PRESENTATION)],
    ["ppt/slides/slide1.xml", Buffer.from(SLIDE)],
    ["ppt/slides/_rels/slide1.xml.rels", Buffer.from(RELS)],
  ]));
  const text = {
    ...base("title.serif", 0, [80, 60, 600, 100]),
    classification: { mode: "native", kind: "text", confidence: "safe" },
    text: {
      role: "title",
      plainText: "한글 Serif",
      paragraphs: ["한글 Serif"],
      style: textStyle,
      runs: [{
        text: "한글",
        bounds: bounds(80, 60, 80, 40),
        fragments: [],
        style: textStyle,
        script: "cjk",
      }],
      containerShape: {
        bounds: bounds(72, 52, 620, 116),
        shape: {
          shape: "round-rectangle",
          fill: { hex: "F4F7FB", alpha: 1 },
          stroke: null,
          strokeWidthPt: 0,
          radiusPt: 8,
        },
      },
    },
  };
  const foreground = {
    ...base("shape.foreground", 1, [700, 80, 100, 100]),
    classification: { mode: "native", kind: "shape", confidence: "safe" },
    shape: {
      shape: "ellipse",
      fill: { hex: "F2B134", alpha: 1 },
      stroke: null,
      strokeWidthPt: 0,
      radiusPt: 0,
    },
  };
  const prepared = await prepareNativeElements([text, foreground]);
  const backplatePng = await sharp({
    create: {
      width: BACKPLATE_WIDTH,
      height: BACKPLATE_HEIGHT,
      channels: 4,
      background: { r: 0, g: 0, b: 0, alpha: 0 },
    },
  }).png().toBuffer();
  const pptx = assembleAuthoredHybridPptx(skeleton, [{
    slideNumber: 1,
    backplatePng,
    elements: prepared,
  }]);
  const xml = readPptxArchive(pptx).get("ppt/slides/slide1.xml").toString("utf8");
  assert.ok(xml.indexOf("title.serif-container") < xml.indexOf("text title.serif"));
  assert.ok(xml.indexOf("text title.serif") < xml.indexOf("shape shape.foreground"));
  const readback = inspectPptxNativeElementReadback(pptx);
  const title = readback.find((entry) => entry.elementId === "title.serif" && entry.kind === "text");
  assert.ok(title);
  assert.deepEqual(title.eastAsianTypefaces, ["Batang"]);
  assert.equal(resolveScriptAwareTypeface("cjk", ["Noto Serif KR", "serif"]).fallbackTypeface, "Batang");
});
