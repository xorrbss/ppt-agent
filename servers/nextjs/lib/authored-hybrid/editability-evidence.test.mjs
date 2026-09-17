import assert from "node:assert/strict";
import test from "node:test";

import {
  createPublicHybridEditabilityEvidence,
  evaluatePublicHybridEditability,
} from "./editability-evidence.ts";
import { writePptxArchive } from "./pptx-archive.ts";

const target = (targetId, role, outcome, reasons = []) => ({
  targetId,
  role,
  required: ["title", "body", "table-cell", "supported-shape"].includes(role),
  outcome,
  reasons,
});

test("keeps correspondence failures distinct and never passes from native counts", () => {
  const evidence = evaluatePublicHybridEditability([
    {
      slideNumber: 1,
      targets: [
        target("title", "title", "native-correspondence"),
        target("body-a", "body", "rasterized", ["rasterized-required-target"]),
        target("body-b", "body", "missing", ["required-target-missing"]),
        target("table-0", "table-cell", "occluded", ["selection-obstructed"]),
      ],
      rasterFragments: 2,
    },
    {
      slideNumber: 2,
      targets: [{
        targetId: "unclassified",
        role: "unknown",
        required: null,
        outcome: "unknown",
        reasons: ["source-role-unknown"],
      }],
    },
  ]);

  assert.equal(evidence.slides[0].verdict, "partially-editable");
  assert.deepEqual({
    requiredTargetCount: evidence.slides[0].requiredTargetCount,
    nativeCorrespondenceCount: evidence.slides[0].nativeCorrespondenceCount,
    rasterizedTargetCount: evidence.slides[0].rasterizedTargetCount,
    missingTargetCount: evidence.slides[0].missingTargetCount,
    occludedTargetCount: evidence.slides[0].occludedTargetCount,
    unknownTargetCount: evidence.slides[0].unknownTargetCount,
  }, {
    requiredTargetCount: 4, nativeCorrespondenceCount: 1,
    rasterizedTargetCount: 1, missingTargetCount: 1,
    occludedTargetCount: 1, unknownTargetCount: 0,
  });
  assert.equal(evidence.slides[1].verdict, "unknown");
  assert.equal(evidence.verdict, "unknown");
  assert.equal(evidence.rasterFragmentCount, 2);
});

test("exempts a proven photo-only image page without treating page furniture as body", () => {
  const evidence = evaluatePublicHybridEditability([{
    slideNumber: 7,
    fullSlideRaster: true,
    targets: [
      target("photo", "photo", "rasterized"),
      target("number", "page-number", "native-correspondence"),
      target("decoration", "decoration", "rasterized"),
    ],
  }]);

  assert.equal(evidence.verdict, "not-applicable");
  assert.equal(evidence.slides[0].photoOnlyExempt, true);
  assert.deepEqual(evidence.imageOnlyPages, [7]);
  assert.equal(evidence.requiredTargetCount, 0);
});

test("reports full required-target failure separately from unknown evidence", () => {
  const failed = evaluatePublicHybridEditability([{
    slideNumber: 1,
    targets: [
      target("title", "title", "rasterized", ["rasterized-required-target"]),
      target("body", "body", "missing", ["required-target-missing"]),
    ],
  }]);
  assert.equal(failed.verdict, "not-editable");

  const unknown = evaluatePublicHybridEditability([{
    slideNumber: 1,
    targets: [target("title", "title", "unknown", ["native-readback-unverified"])],
  }]);
  assert.equal(unknown.verdict, "unknown");
});

const slideXml = (text = "") => Buffer.from(
  `<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree>${text ? `<p:sp><p:nvSpPr><p:cNvPr id="2" name="DevAX hybrid text shared"/></p:nvSpPr><p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="952500" cy="952500"/></a:xfrm></p:spPr><p:txBody><a:p><a:r><a:t>${text}</a:t></a:r></a:p></p:txBody></p:sp>` : ""}</p:spTree></p:cSld></p:sld>`,
  "utf8",
);

const quality = (slideNumber, imageFallback = false) => ({
  slideNumber,
  editable: !imageFallback,
  imageFallback,
  nativeTextElements: imageFallback ? 0 : 1,
  nativeShapeElements: 0,
  nativeImageElements: 0,
  rasterFallbackElements: imageFallback ? 1 : 0,
  logicalCoverage: { nativeText: imageFallback ? 0 : 1, nativeImage: 0, nativeShape: 0, nativeGroup: 0, rasterRemaining: imageFallback ? 1 : 0 },
  ooxmlReadback: { status: "not-run", nativeText: 0, nativeImage: 0, nativeShape: 0, nativeGroup: 0, rasterRemaining: 0 },
  backplateCount: imageFallback ? 1 : 0,
  typographyIssues: [],
  fallbackReasons: imageFallback ? ["full-slide-image-fallback"] : [],
  fallbackElements: [],
});

const textLayer = (slideNumber, text, obstructed = false) => ({
  slideNumber,
  backplatePng: Buffer.alloc(0),
  elements: [{ kind: "text", source: { id: "shared", text: { role: "title", plainText: text } } }],
  ...(obstructed ? { selectionObstructions: [{ zBand: 1, elementId: "shared", reason: "raster-over-native-selection" }] } : {}),
});

test("actual adapter scopes repeated QIDs by slide and fails closed on missing source or obstruction", () => {
  const pptxBytes = writePptxArchive(new Map([
    ["ppt/presentation.xml", Buffer.from('<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldSz cx="12192000" cy="6858000"/></p:presentation>')],
    ["ppt/slides/slide1.xml", slideXml("First")],
    ["ppt/slides/slide2.xml", slideXml("Wrong")],
    ["ppt/slides/slide3.xml", slideXml("Third")],
    ["ppt/slides/slide4.xml", slideXml()],
  ]));
  const evidence = createPublicHybridEditabilityEvidence({
    outcomes: [
      { layer: textLayer(1, "First"), quality: quality(1), fontRendering: { browserFontFilesCollected: 0, browserCollectionFailures: 0 } },
      { layer: textLayer(2, "Second"), quality: quality(2), fontRendering: { browserFontFilesCollected: 0, browserCollectionFailures: 0 } },
      { layer: textLayer(3, "Third", true), quality: quality(3, true), fontRendering: { browserFontFilesCollected: 0, browserCollectionFailures: 0 } },
      { layer: null, quality: quality(4, true), fontRendering: { browserFontFilesCollected: 0, browserCollectionFailures: 0 } },
    ],
    semantics: [null, null, null, null],
    htmls: [
      '<h1 data-qid="shared" data-semantic-role="title">First</h1>',
      '<h1 data-qid="shared" data-semantic-role="title">Second</h1>',
      '<h1 data-qid="shared" data-semantic-role="title">Third</h1>',
      "",
    ],
    pptxBytes,
  });
  assert.equal(evidence.slides[0].verdict, "fully-editable");
  assert.deepEqual(evidence.slides[1].reasons, ["native-text-mismatch"]);
  assert.equal(evidence.slides[2].occludedTargetCount, 1);
  assert.equal(evidence.slides[2].fullSlideRaster, true);
  assert.equal(evidence.slides[3].verdict, "unknown");
});
