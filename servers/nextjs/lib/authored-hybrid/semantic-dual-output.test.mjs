import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import sharp from "sharp";

import { resolveScriptAwareTypeface } from "./font-policy.ts";
import { instrumentAuthoredHtml } from "./html-instrumentation.ts";
import { planLayerSafeNativeElements, prepareNativeElements } from "./native-plan.ts";
import {
  buildBrowserObservationReceiptV1,
  digestObservedDeclaredAssets,
  reconcileDeclaredObservation,
} from "./semantic-reconciliation.ts";
import { assembleAuthoredHybridPptx } from "./pptx-assembler.ts";
import { inspectPptxNativeElementReadback } from "./pptx-quality-inspection.ts";
import { readPptxArchive, writePptxArchive } from "./pptx-archive.ts";
import {
  bindActiveAuthoredEmissionForExport,
  parseActiveAuthoredEmissionResponseV1,
} from "./active-emission.ts";

const digest = (value) =>
  `sha256:${createHash("sha256").update(value).digest("hex")}`;
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
    viewport: { widthPx: 1280, heightPx: 720, devicePixelRatio: 1 },
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
    viewport: { widthPx: 1280, heightPx: 720, devicePixelRatio: 1 },
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
  const nativeShape = (id, zOrder, rect) => ({
    ...base(id, zOrder, rect),
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
      width: 1280,
      height: 720,
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

test("active bundle graph binds ordered slide QIDs and fails closed for Hybrid degradation", () => {
  const presentationId = "11111111-1111-4111-8111-111111111111";
  const slideId = "22222222-2222-4222-8222-222222222222";
  const operationId = "33333333-3333-4333-8333-333333333333";
  const canonicalDigest = digest("manifest");
  const bundleDigest = digest("bundle-active");
  const htmlDigest = digest("html");
  const pointer = (label) => ({
    schemaVersion: "authored-artifact-pointer/v1",
    artifactRef: `artifact://sha256/${createHash("sha256").update(label).digest("hex")}`,
    digest: digest(label),
    byteLength: 32,
    mediaType: "application/json",
  });
  const semantic = {
    schemaVersion: "authored-semantic-source/v1",
    componentKind: "authoredSemanticSource",
    presentationId,
    slideId,
    slideRevision: 4,
    operationId,
    origin: "authored-declared-v1",
    authoredHtmlDigest: htmlDigest,
    elements: [{
      elementId: "title.main",
      semanticRole: "title",
      parentId: null,
      childIds: [],
      intendedOrder: 0,
      intendedBounds: null,
      requiredFeatures: [],
      kind: "text",
      payload: {
        paragraphs: [{ runs: [{ fontFamily: "IBM Plex Mono" }] }],
      },
    }],
    identityRegistry: {
      schemaVersion: "authored-element-identity-registry/v1",
      activeQids: ["title.main"],
      retiredQids: [],
      lineageDigest: null,
      digest: digest("registry"),
    },
    digest: digest("semantic"),
  };
  const bundle = {
    schemaVersion: "authored-emission-bundle/v1",
    bundleKind: "authored",
    presentationId,
    slideId,
    slideRevision: 4,
    operationId,
    originGenerationAttemptId: null,
    authoredHtml: { ...pointer("html"), digest: htmlDigest },
    assetManifest: pointer("assets"),
    fontManifest: pointer("fonts"),
    masterContractDigest: digest("master"),
    compositionDigest: digest("composition"),
    elementIdentityRegistryDigest: digest("registry"),
    qualityPolicyVersion: "quality/v1",
    bundleDigest,
    semanticComponent: semantic,
  };
  const response = {
    schemaVersion: "active-authored-emission-response/v1",
    presentationId,
    canonicalMode: "bundleGraph",
    presentationRevision: 4,
    activeGeneration: 7,
    canonicalDigest,
    manifest: {
      schemaVersion: "authored-deck-emission-manifest/v1",
      presentationId,
      presentationRevision: 4,
      operationId,
      generationAttemptId: null,
      orderedSlides: [{ slideId, slideRevision: 4, bundleKind: "authored", bundleDigest }],
      masterContractDigest: digest("master"),
      compositionDigest: digest("composition"),
      assetManifestDigest: digest("assets"),
      fontManifestDigest: digest("fonts"),
      qualityPolicyVersion: "quality/v1",
      sceneDigest: null,
      scene: null,
      canonicalDigest,
    },
    bundles: [bundle],
  };
  const parsed = parseActiveAuthoredEmissionResponseV1(response);
  const bound = bindActiveAuthoredEmissionForExport(parsed, {
    mode: "hybrid",
    explicitMode: true,
    presentationId,
    presentationRevision: 4,
    canonicalSourceDigest: canonicalDigest.slice(7),
    slideIds: [slideId],
  });
  assert.deepEqual(bound.slides[0].declaredElementIds, ["title.main"]);
  assert.equal(bound.activeGeneration, 7);
  assert.deepEqual(bound.slides[0].fontFamilies, ["IBM Plex Mono"]);
  assert.throws(
    () => bindActiveAuthoredEmissionForExport(parsed, {
      mode: "hybrid",
      explicitMode: true,
      presentationId,
      presentationRevision: 4,
      canonicalSourceDigest: canonicalDigest.slice(7),
      slideIds: ["44444444-4444-4444-8444-444444444444"],
    }),
    /authored_active_manifest_slide_binding_mismatch/,
  );
  const degraded = structuredClone(response);
  degraded.bundles[0].semanticComponent = {
    schemaVersion: "semantic-unavailable-receipt/v1",
    componentKind: "semanticUnavailable",
    presentationId,
    slideId,
    slideRevision: 4,
    operationId,
    originGenerationAttemptId: null,
    authoredHtmlDigest: htmlDigest,
    state: "degradedFidelityOnly",
    failureCode: "authored_semantic_retry_exhausted",
    boundedAttemptCount: 2,
    compilerVersion: "compiler/v1",
    policyVersion: "quality/v1",
    digest: digest("unavailable"),
  };
  assert.throws(
    () => bindActiveAuthoredEmissionForExport(
      parseActiveAuthoredEmissionResponseV1(degraded),
      {
        mode: "hybrid",
        explicitMode: true,
        presentationId,
        presentationRevision: 4,
        canonicalSourceDigest: canonicalDigest,
        slideIds: [slideId],
      },
    ),
    /authored_hybrid_degraded_fidelity_only/,
  );
  assert.throws(
    () => parseActiveAuthoredEmissionResponseV1({
      ...response,
      bundles: [{ ...bundle, compatibilityComponent: semantic }],
    }),
    /authored_active_manifest_response_invalid/,
  );
  assert.throws(
    () => parseActiveAuthoredEmissionResponseV1({
      ...response,
      manifest: { ...response.manifest, sceneDigest: digest("scene") },
    }),
    /authored_active_manifest_response_invalid/,
  );
});

test("legacy active mode is allowed only for default or attempt-bound Fidelity", () => {
  const legacyDigest = digest("legacy-cutover");
  const legacy = parseActiveAuthoredEmissionResponseV1({
    schemaVersion: "active-authored-emission-response/v1",
    presentationId: "11111111-1111-4111-8111-111111111111",
    canonicalMode: "legacyHtml",
    presentationRevision: 2,
    activeGeneration: 3,
    canonicalDigest: legacyDigest,
    manifest: null,
    bundles: [],
  });
  const common = {
    presentationId: legacy.presentationId,
    presentationRevision: 2,
    slideIds: [],
  };
  const defaultBinding = bindActiveAuthoredEmissionForExport(legacy, {
    ...common,
    mode: "fidelity",
    explicitMode: false,
  });
  assert.equal(defaultBinding.canonicalMode, "legacyHtml");
  assert.equal(defaultBinding.canonicalDigest, legacyDigest);
  assert.throws(
    () => bindActiveAuthoredEmissionForExport(legacy, {
      ...common,
      mode: "fidelity",
      explicitMode: true,
    }),
    /authored_fidelity_legacy_attempt_binding_required/,
  );
  assert.equal(bindActiveAuthoredEmissionForExport(legacy, {
    ...common,
    mode: "fidelity",
    explicitMode: true,
    canonicalSourceDigest: "a".repeat(64),
  }).canonicalDigest, `sha256:${"a".repeat(64)}`);
  assert.throws(
    () => bindActiveAuthoredEmissionForExport(legacy, {
      ...common,
      mode: "hybrid",
      explicitMode: true,
      canonicalSourceDigest: "a".repeat(64),
    }),
    /authored_hybrid_active_manifest_required/,
  );
});
