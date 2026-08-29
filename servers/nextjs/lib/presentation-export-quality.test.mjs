import assert from "node:assert/strict";
import test from "node:test";

import {
  assertPresentationExportQualityCoverage,
  attachPresentationExportOoxmlReadback,
  createImageOnlyExportQuality,
  createPresentationExportQuality,
} from "./presentation-export-quality.ts";

test("quality sidecar preserves audited multi-z-band paint evidence", () => {
  const paintPlan = {
    schemaVersion: "hybrid-paint-plan/browser-v1",
    strategy: "audited-multi-z-band",
    fixedPointReached: true,
    closureIterations: 2,
    closureDigest: "a".repeat(64),
    hybridPlanDigest: "b".repeat(64),
    slideDemotedToRaster: false,
    downgradeReceipt: {
      schemaVersion: "hybrid-layer-downgrade-receipt/browser-v1",
      state: "closed",
      reason: null,
      demotedElementIds: [],
      receiptDigest: "c".repeat(64),
    },
    demotions: [],
    bands: [
      { zBand: 0, paintOwner: "rasterIsland", elementIds: ["s01-bg"], artifactDigest: "d".repeat(64) },
      { zBand: 1, paintOwner: "native", elementIds: ["s01-title"] },
    ],
  };
  const report = createPresentationExportQuality("hybrid", [{
    slideNumber: 1,
    editable: true,
    imageFallback: false,
    nativeTextElements: 1,
    nativeShapeElements: 0,
    nativeImageElements: 0,
    rasterFallbackElements: 1,
    fallbackReasons: ["external-paint"],
    paintPlan,
  }]);

  assert.deepEqual(report.slides[0].paintPlan, paintPlan);
});

test("native coverage scopes repeated QIDs by slide without shrinking unsupported fallbacks", () => {
  const slide = (slideNumber, candidateKind) => ({
    slideNumber,
    editable: true,
    imageFallback: false,
    nativeTextElements: 1,
    nativeShapeElements: 0,
    nativeGroupElements: slideNumber === 1 ? 3 : 0,
    nativeImageElements: 0,
    rasterFallbackElements: 1,
    logicalCoverage: {
      nativeText: 1,
      nativeImage: 0,
      nativeShape: 0,
      nativeGroup: 0,
      rasterRemaining: 1,
    },
    ooxmlReadback: {
      status: "not-run",
      nativeText: 0,
      nativeImage: 0,
      nativeShape: 0,
      nativeGroup: 0,
      rasterRemaining: 0,
    },
    fallbackReasons: [candidateKind === "complex" ? "filter" : "unsupported-shape"],
    fallbackElements: [{
      elementId: "body.fx",
      candidateKind,
      reasons: [candidateKind === "complex" ? "filter" : "unsupported-shape"],
    }],
    qidCoverage: [
      { elementId: "header.band", kind: "text", logical: "native", ooxml: "not-run", com: "not-run" },
      { elementId: "body.fx", kind: "shape", logical: "raster", ooxml: "not-run", com: "not-run" },
    ],
  });
  const report = createPresentationExportQuality("hybrid", [
    slide(1, "complex"),
    slide(2, "shape"),
  ]);
  assert.equal(report.nativeCoverage.status, "passed");
  assert.equal(report.nativeCoverage.nativeElementCount, 2);
  assert.equal(report.nativeCoverage.eligibleElementCount, 4);
  assert.equal(report.nativeCoverage.ratio, 2 / 4);
  assert.deepEqual(
    report.nativeCoverage.rasterFallbacks.map((item) => [item.elementId, item.eligible]),
    [["1:body.fx", true], ["2:body.fx", true]],
  );

  const coverageSlides = report.slides.map((item) => ({
    slideNumber: item.slideNumber,
    nativeText: 1,
    nativeImage: 0,
    nativeShape: 0,
    nativeTable: 0,
    nativeChart: 0,
    rasterRemaining: 1,
    logicalCoverage: {
      nativeText: 1,
      nativeImage: 0,
      nativeShape: 0,
      nativeTable: 0,
      nativeChart: 0,
      rasterRemaining: 1,
    },
    ooxmlReadback: {
      status: "not-run",
      nativeText: 0,
      nativeImage: 0,
      nativeShape: 0,
      nativeTable: 0,
      nativeChart: 0,
      rasterRemaining: 0,
    },
  }));
  const coverage = {
    totalSlides: 2,
    nativeText: 2,
    nativeImage: 0,
    nativeShape: 0,
    nativeTable: 0,
    nativeChart: 0,
    rasterRemaining: 2,
    logicalCoverage: {
      nativeText: 2,
      nativeImage: 0,
      nativeShape: 0,
      nativeTable: 0,
      nativeChart: 0,
      rasterRemaining: 2,
    },
    ooxmlReadback: {
      status: "not-run",
      nativeText: 0,
      nativeImage: 0,
      nativeShape: 0,
      nativeTable: 0,
      nativeChart: 0,
      rasterRemaining: 0,
    },
    slides: coverageSlides,
  };
  assert.doesNotThrow(() =>
    assertPresentationExportQualityCoverage(report, coverage, "development"),
  );
  assert.throws(
    () => assertPresentationExportQualityCoverage(report, coverage, "release"),
    /presentation_export_release_native_coverage_blocked/,
  );
});

test("quality report totals, orders, and de-duplicates slide metadata", () => {
  const report = createPresentationExportQuality("hybrid", [
    {
      slideNumber: 2,
      editable: false,
      imageFallback: true,
      nativeTextElements: 0,
      nativeShapeElements: 0,
      nativeGroupElements: 0,
      nativeImageElements: 0,
      rasterFallbackElements: 3,
      fallbackReasons: ["clip-path", "clip-path"],
      fallbackElements: [
        {
          elementId: "hero::before",
          domPath: "body > main > section:nth-child(1)",
          candidateKind: "complex",
          reasons: ["clip-path", "clip-path"],
        },
      ],
    },
    {
      slideNumber: 1,
      editable: true,
      imageFallback: false,
      nativeTextElements: 4,
      nativeShapeElements: 2,
      nativeGroupElements: 1,
      nativeImageElements: 1,
      rasterFallbackElements: 0,
      fallbackReasons: [],
    },
  ]);
  assert.equal(report.status, "partially-editable");
  assert.equal(report.totalSlides, 2);
  assert.equal(report.editableSlides, 1);
  assert.equal(report.imageFallbackSlides, 1);
  assert.equal(report.nativeTextElements, 4);
  assert.equal(report.nativeGroupElements, 1);
  assert.equal(report.rasterFallbackElements, 3);
  assert.deepEqual(report.slides.map((slide) => slide.slideNumber), [1, 2]);
  assert.deepEqual(report.slides[1].fallbackReasons, ["clip-path"]);
  assert.deepEqual(report.fallbackReasonCounts, { "clip-path": 1 });
  assert.equal(report.slides[1].fallbackElements[0].elementId, "hero::before");
  assert.equal(report.fontEmbedding, false);
  assert.deepEqual(report.fontEmbeddingStatus, {
    policy: "opt-in",
    requested: false,
    applied: false,
    embeddedFontFiles: 0,
    reason: "not-requested",
  });
  assert.equal(
    report.fontRendering.powerpointTypefacePolicy,
    "central-compatible-fallbacks"
  );
});

test("font embedding metadata is honest and opt-in", () => {
  const baseSlide = {
    slideNumber: 1,
    editable: true,
    imageFallback: false,
    nativeTextElements: 1,
    nativeShapeElements: 0,
    nativeImageElements: 0,
    rasterFallbackElements: 0,
    fallbackReasons: [],
  };
  const rejected = createPresentationExportQuality("hybrid", [baseSlide], {
    fontEmbeddingStatus: {
      requested: false,
      applied: true,
      embeddedFontFiles: 3,
      reason: "embedded",
    },
  });
  assert.equal(rejected.fontEmbedding, false);
  assert.equal(rejected.fontEmbeddingStatus.reason, "not-requested");
  assert.equal(rejected.fontEmbeddingStatus.embeddedFontFiles, 0);

  const embedded = createPresentationExportQuality("hybrid", [baseSlide], {
    fontEmbeddingStatus: {
      requested: true,
      applied: true,
      embeddedFontFiles: 2,
    },
    fontRendering: {
      browserFontFilesCollected: 4,
      browserCollectionFailures: 1,
    },
  });
  assert.equal(embedded.fontEmbedding, true);
  assert.equal(embedded.fontEmbeddingStatus.reason, "embedded");
  assert.equal(embedded.fontEmbeddingStatus.embeddedFontFiles, 2);
  assert.equal(embedded.fontRendering.browserFontFilesCollected, 4);
});

test("font embedding metadata records faces, provenance, license, and subset limits", () => {
  const report = createPresentationExportQuality(
    "hybrid",
    [
      {
        slideNumber: 1,
        editable: true,
        imageFallback: false,
        nativeTextElements: 1,
        nativeShapeElements: 0,
        nativeImageElements: 0,
        rasterFallbackElements: 0,
        fallbackReasons: [],
      },
    ],
    {
      fontEmbeddingStatus: {
        requested: true,
        applied: true,
        embeddedFontFiles: 1,
        embeddedTypefaces: 1,
        strategy: "subset",
        editLimitation: "characters-outside-subset-may-substitute",
        faces: [
          {
            typeface: "Noto Sans KR",
            face: "regular",
            weight: 400,
            style: "normal",
            source: "local-derived-static",
            sourcePath: "NotoSansKR-Regular-derived-static.ttf",
            sourceSha256: "a".repeat(64),
            sourceBytes: 1024,
            embeddedBytes: 1100,
            fsType: 0,
            licenseDecision: "allowed-installable",
            subset: true,
            strategy: "subset",
            partName: "ppt/fonts/font1.fntdata",
            format: "eot-uncompressed-xor",
            derivedFromVariable: true,
          },
        ],
        failures: [],
      },
    }
  );
  assert.equal(report.fontEmbeddingStatus.applied, true);
  assert.equal(report.fontEmbeddingStatus.embeddedTypefaces, 1);
  assert.equal(
    report.fontEmbeddingStatus.editLimitation,
    "characters-outside-subset-may-substitute"
  );
  assert.equal(report.fontEmbeddingStatus.faces[0].fsType, 0);
  assert.equal(report.fontEmbeddingStatus.faces[0].derivedFromVariable, true);
});

test("failed embedding cannot claim files or faces but retains failure reasons", () => {
  const report = createPresentationExportQuality("hybrid", [], {
    fontEmbeddingStatus: {
      requested: true,
      applied: false,
      embeddedFontFiles: 4,
      embeddedTypefaces: 2,
      strategy: "full",
      faces: [
        {
          typeface: "Noto Sans KR",
          face: "regular",
          weight: 400,
          style: "normal",
          source: "server-font-allowlist",
          sourceSha256: "b".repeat(64),
          sourceBytes: 1,
          embeddedBytes: 1,
          fsType: 0,
          licenseDecision: "allowed-installable",
          subset: false,
          strategy: "full",
          partName: "ppt/fonts/font1.fntdata",
          format: "eot-uncompressed-xor",
        },
      ],
      failures: [
        {
          family: "Noto Sans KR",
          reason: "variable-font-unsupported",
        },
      ],
      reason: "failed",
    },
  });
  assert.equal(report.fontEmbeddingStatus.embeddedFontFiles, 0);
  assert.equal(report.fontEmbeddingStatus.embeddedTypefaces, 0);
  assert.deepEqual(report.fontEmbeddingStatus.faces, []);
  assert.equal(
    report.fontEmbeddingStatus.failures[0].reason,
    "variable-font-unsupported"
  );
});

test("image-only fallback describes every slide", () => {
  const report = createImageOnlyExportQuality(20, "chrome-unavailable");
  assert.equal(report.status, "image-only");
  assert.equal(report.totalSlides, 20);
  assert.equal(report.imageFallbackSlides, 20);
  assert.ok(
    report.slides.every(
      (slide) => slide.fallbackReasons[0] === "chrome-unavailable"
    )
  );
});

test("OOXML QID ownership preserves exact native/raster disposition and fails closed", () => {
  const baseSlide = {
    slideNumber: 1,
    editable: true,
    imageFallback: false,
    nativeTextElements: 1,
    nativeShapeElements: 0,
    nativeImageElements: 0,
    rasterFallbackElements: 1,
    fallbackReasons: [],
  };
  const logical = createPresentationExportQuality("hybrid", [{
    ...baseSlide,
    qidCoverage: [
      { elementId: "s01-title", kind: "text", logical: "native", ooxml: "not-run", com: "not-run" },
      { elementId: "s01-complex-copy", kind: "text", logical: "raster", ooxml: "not-run", com: "not-run" },
    ],
  }]);
  const readback = (qidCoverage) => createPresentationExportQuality("hybrid", [{
    ...baseSlide,
    qidCoverage,
    ooxmlReadback: { status: "passed" },
  }]);

  const joined = attachPresentationExportOoxmlReadback(logical, readback([
    { elementId: "s01-title", kind: "text", logical: "native", ooxml: "present", com: "not-run" },
    // Raster ownership uses a non-visual OOXML shape carrier by design.
    { elementId: "s01-complex-copy", kind: "shape", logical: "raster", ooxml: "present", com: "not-run" },
  ]));
  assert.deepEqual(
    joined.slides[0].qidCoverage.map((entry) => [entry.elementId, entry.ooxml]),
    [["s01-title", "present"], ["s01-complex-copy", "present"]],
  );

  assert.throws(
    () => attachPresentationExportOoxmlReadback(logical, readback([
      { elementId: "s01-title", kind: "text", logical: "native", ooxml: "present", com: "not-run" },
    ])),
    /presentation_export_qid_ownership_mismatch/,
  );
  assert.throws(
    () => attachPresentationExportOoxmlReadback(logical, readback([
      { elementId: "s01-title", kind: "text", logical: "native", ooxml: "present", com: "not-run" },
      { elementId: "s01-title", kind: "shape", logical: "raster", ooxml: "present", com: "not-run" },
      { elementId: "s01-complex-copy", kind: "shape", logical: "raster", ooxml: "present", com: "not-run" },
    ])),
    /presentation_export_qid_ownership_mismatch/,
  );
});
