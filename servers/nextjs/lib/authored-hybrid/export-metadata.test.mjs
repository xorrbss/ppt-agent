import assert from "node:assert/strict";
import test from "node:test";

import {
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
    "Editable 1/2 slides; image fallback 1. Reasons: security preflight (unsafe-css-url): 1."
  );
});

test("reports a fully editable export without fallback reasons", () => {
  assert.equal(
    formatAuthoredHybridExportSummary(
      summarizeAuthoredHybridExport([{ editable: true }, { editable: true }])
    ),
    "Editable 2/2 slides; image fallback 0."
  );
});
