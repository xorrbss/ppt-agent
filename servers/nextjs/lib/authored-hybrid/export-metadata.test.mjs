import assert from "node:assert/strict";
import test from "node:test";

import {
  createAuthoredHybridCoverageManifest,
  formatAuthoredHybridExportSummary,
  summarizeAuthoredHybridExport,
} from "./export-metadata.ts";

test("summarizes editable and image-fallback slides deterministically", () => {
  const metadata = summarizeAuthoredHybridExport([
    { editable: true },
    { editable: false, fallbackReason: "slide-processing-failed" },
    { editable: false, fallbackReason: "preflight-active-or-external-html" },
    { editable: false, fallbackReason: "slide-processing-failed" },
  ]);

  assert.deepEqual(metadata, {
    version: 1,
    strategy: "authored-hybrid",
    totalSlides: 4,
    editableSlides: 1,
    imageFallbackSlides: 3,
    fallbackReasons: [
      { reason: "preflight-active-or-external-html", slideCount: 1 },
      { reason: "slide-processing-failed", slideCount: 2 },
    ],
  });
});

test("formats counts and safe fallback reason codes for the UI", () => {
  const summary = formatAuthoredHybridExportSummary(
    summarizeAuthoredHybridExport([
      { editable: true },
      { editable: false, fallbackReason: "preflight-unsafe-css-url" },
    ])
  );

  assert.equal(
    summary,
    "Native elements on 1/2 slides; image-only 1. Reasons: security preflight (unsafe-css-url): 1."
  );
});

test("reports a fully editable export without fallback reasons", () => {
  assert.equal(
    formatAuthoredHybridExportSummary(
      summarizeAuthoredHybridExport([{ editable: true }, { editable: true }])
    ),
    "Native elements on 2/2 slides; image-only 0."
  );
});

test("reports truthful per-slide native and raster coverage", () => {
  const coverage = createAuthoredHybridCoverageManifest(
    {
      slides: [
        {
          slideNumber: 1,
          editable: true,
          imageFallback: false,
          nativeTextElements: 2,
          nativeImageElements: 1,
          nativeShapeElements: 1,
          rasterFallbackElements: 3,
          fallbackReasons: ["complex-chart", "text-shadow"],
        },
        {
          slideNumber: 2,
          editable: false,
          imageFallback: true,
          nativeTextElements: 0,
          nativeImageElements: 0,
          nativeShapeElements: 0,
          rasterFallbackElements: 0,
          fallbackReasons: ["slide-processing-failed"],
        },
      ],
    },
    { presentationRevision: 7, sourceDigest: "a".repeat(64) }
  );

  assert.equal(coverage.nativeText, 2);
  assert.equal(coverage.nativeImage, 1);
  assert.equal(coverage.nativeShape, 1);
  assert.equal(coverage.nativeTable, 0);
  assert.equal(coverage.nativeChart, 0);
  assert.equal(coverage.rasterRemaining, 4);
  assert.equal(coverage.fullyEditable, false);
  assert.deepEqual(coverage.slides[0].fallbackReasons, [
    "complex-chart",
    "text-shadow",
  ]);
  assert.equal(coverage.slides[0].nativeBackplateDeDuplication, "applied");
  assert.equal(coverage.slides[0].visualParity, "not-verified");
  assert.equal(coverage.slides[1].rasterRemaining, 1);
});
