import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import sharp from "sharp";

import {
  TEMPLATE_IDS,
  loadGoldenInventory,
  selectGoldenLayouts,
} from "./template-v2-pdf-golden.mjs";
import {
  DEFAULT_THRESHOLDS,
  applyLayoutTextPolicy,
  classifyFailure,
  comparePngBuffers,
  imageReadinessState,
} from "./template-v2-pdf-golden-metrics.mjs";
import {
  clipsOverflow,
  textRangesOverflowContainer,
  validateGatePaths,
} from "./template-v2-pdf-golden-runtime.mjs";

const REPOSITORY_ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../../..",
);

async function png(width, height, background, patches = []) {
  let image = sharp({ create: { width, height, channels: 3, background } });
  if (patches.length) image = image.composite(patches);
  return image.png().toBuffer();
}

test("inventory is exactly the existing eight-template 158-PNG browser baseline", async () => {
  const inventory = await loadGoldenInventory(REPOSITORY_ROOT);
  assert.equal(inventory.templates.length, 8);
  assert.equal(inventory.totalLayouts, 158);
  assert.deepEqual(
    Object.fromEntries(inventory.templates.map((template) => [template.templateId, template.layouts.length])),
    { dynamic: 32, editorial: 24, executive: 32, general: 12, modern: 10, momentum: 28, standard: 11, swift: 9 },
  );
});

test("PR selection is eight representatives plus every layout in changed templates", async () => {
  const inventory = await loadGoldenInventory(REPOSITORY_ROOT);
  const representative = selectGoldenLayouts(inventory);
  assert.equal(representative.scope, "representative-plus-changed");
  assert.equal(representative.selectedLayoutCount, 8);

  const changed = selectGoldenLayouts(inventory, {
    changedFiles: ["templates/modern/template.json", "templates/swift/static/Albert Sans Regular.ttf"],
  });
  assert.equal(changed.selectedLayoutCount, 8 - 2 + 10 + 9);
  assert.equal(changed.templates.find((template) => template.templateId === "modern").selectedLayouts.length, 10);
  assert.equal(changed.templates.find((template) => template.templateId === "swift").selectedLayouts.length, 9);
});

test("shared renderer/export changes and explicit full mode select all 158 layouts", async () => {
  const inventory = await loadGoldenInventory(REPOSITORY_ROOT);
  for (const options of [
    { full: true },
    { changedFiles: ["servers/nextjs/lib/template-v2-json-to-html.ts"] },
    { changedFiles: ["presentation-export/index.js"] },
  ]) {
    const selection = selectGoldenLayouts(inventory, options);
    assert.equal(selection.scope, "full");
    assert.equal(selection.selectedLayoutCount, 158);
  }
});

test("pixel gate reports MAE, perceptual pixels, SSIM, connected component, and text bbox", async () => {
  const expected = await png(32, 24, { r: 255, g: 255, b: 255 });
  const actual = await png(32, 24, { r: 255, g: 255, b: 255 }, [
    { input: await png(4, 3, { r: 0, g: 0, b: 0 }), left: 5, top: 6 },
  ]);
  const comparison = await comparePngBuffers(actual, expected, {
    textBoxes: [{ x: 5, y: 6, width: 4, height: 3 }],
    thresholds: {
      ...DEFAULT_THRESHOLDS,
      meanAbsoluteError: 1,
      perceptualBadPixelRatio: 1,
      ssim: 0,
      largestBadComponent: 100,
      textBboxMeanAbsoluteError: 1,
    },
  });
  assert.equal(comparison.metrics.largestBadComponent, 12);
  assert.equal(comparison.metrics.perceptualBadPixelRatio, 12 / (32 * 24));
  assert.ok(comparison.metrics.meanAbsoluteError > 0);
  assert.ok(comparison.metrics.ssim < 1);
  assert.equal(comparison.textBbox.count, 1);
  assert.equal(comparison.textBbox.outOfBounds, 0);
  assert.equal(comparison.passed, true);
});

test("text bboxes outside the slide fail closed", async () => {
  const image = await png(16, 16, { r: 255, g: 255, b: 255 });
  const comparison = await comparePngBuffers(image, image, {
    textBoxes: Array.from({ length: 5 }, (_, index) => ({ x: 15, y: index, width: 4, height: 1 })),
  });
  assert.equal(comparison.textBbox.outOfBounds, 5);
  assert.equal(comparison.passed, false);
  assert.deepEqual(comparison.failures, ["text-bbox"]);
});

test("failure taxonomy is deterministic and capability-specific", () => {
  assert.equal(classifyFailure({ telemetry: { fontSetStatus: "loading" } }), "font-fallback");
  assert.equal(classifyFailure({ telemetry: { clippedText: 1 } }), "clip");
  assert.equal(classifyFailure({ telemetry: { overflowElements: 1 } }), "overflow");
  assert.equal(classifyFailure({ telemetry: { chartCanvasCount: 1 } }), "chart");
  assert.equal(classifyFailure({ telemetry: { svgCount: 1 } }), "svg");
  assert.equal(classifyFailure({ telemetry: { transparencyCount: 1 } }), "transparency");
  assert.equal(classifyFailure({ comparison: { textBbox: { passed: false } } }), "text-bbox");
  assert.equal(classifyFailure({}), "unclassified-visual-drift");
  assert.deepEqual(TEMPLATE_IDS.length, 8);
});

test("image readiness fails immediately for completed zero-pixel images and bounds pending work", () => {
  assert.equal(imageReadinessState({ complete: true, naturalWidth: 640 }), "loaded");
  assert.equal(imageReadinessState({ complete: true, naturalWidth: 0 }), "failed");
  assert.equal(imageReadinessState({ complete: false, naturalWidth: 0 }), "pending");
});

test("only the exact known side-callout observation can accept its bounded text geometry", () => {
  const known = applyLayoutTextPolicy({
    templateId: "momentum",
    layoutId: "title_with_side_callouts_2594",
    textBbox: { outOfBounds: 4, maximumMeanAbsoluteError: 0.2 },
    telemetry: { clippedText: 4, overflowElements: 4 },
    failures: ["text-bbox"],
  });
  assert.equal(known.passed, true);
  assert.equal(known.acceptedObservation.code, "known-momentum-side-callout-glyph-bounds-v1");
  assert.deepEqual(known.acceptedObservation.observed, {
    outOfBounds: 4,
    clippedText: 4,
    overflowElements: 4,
  });

  const beyondKnown = applyLayoutTextPolicy({
    templateId: "momentum",
    layoutId: "title_with_side_callouts_2594",
    textBbox: { outOfBounds: 4, maximumMeanAbsoluteError: 0.2 },
    telemetry: { clippedText: 5, overflowElements: 4 },
    failures: ["text-bbox"],
  });
  assert.equal(beyondKnown.passed, false);
  assert.deepEqual(beyondKnown.failures, ["text-clip"]);

  const differentLayout = applyLayoutTextPolicy({
    templateId: "momentum",
    layoutId: "different-layout",
    textBbox: { outOfBounds: 1, maximumMeanAbsoluteError: 0.1 },
    telemetry: { clippedText: 0, overflowElements: 0 },
    failures: ["text-bbox"],
  });
  assert.equal(differentLayout.passed, false);
  assert.deepEqual(differentLayout.failures, ["text-bbox"]);
});

test("Range geometry uses clipping ancestor or page boundaries, not an inline own box", () => {
  const page = { left: 0, top: 0, right: 1280, bottom: 720 };
  const clippingAncestor = { left: 10, top: 20, right: 110, bottom: 70 };
  assert.equal(
    textRangesOverflowContainer(page, [{ left: 10, top: 20, right: 112, bottom: 70 }]),
    false,
  );
  assert.equal(
    textRangesOverflowContainer(clippingAncestor, [{ left: 10, top: 20, right: 112, bottom: 70 }]),
    true,
  );
  assert.equal(
    textRangesOverflowContainer(page, [{ left: 10, top: 20, right: 1282, bottom: 70 }]),
    true,
  );
  assert.equal(clipsOverflow(["visible", "auto", "visible"]), true);
  assert.equal(clipsOverflow(["visible", "visible", "visible"]), false);
});

test("non-allowlisted text clipping and overflow fail closed", () => {
  const result = applyLayoutTextPolicy({
    templateId: "general",
    layoutId: "title_intro",
    textBbox: { outOfBounds: 0, maximumMeanAbsoluteError: 0.1 },
    telemetry: { clippedText: 1, overflowElements: 1 },
    failures: [],
  });
  assert.equal(result.passed, false);
  assert.deepEqual(result.failures, ["text-clip", "text-overflow"]);
  assert.equal(result.acceptedObservation, null);
});

test("artifact and receipt paths reject writes or recursive cleanup outside fixed repository boundaries", async () => {
  await assert.rejects(
    validateGatePaths({
      repositoryRoot: REPOSITORY_ROOT,
      artifactRoot: path.join(REPOSITORY_ROOT, "..", "external-artifacts"),
      receiptPath: path.join(REPOSITORY_ROOT, "templates", "manifests", "template-v2.pdf-golden.v1.json"),
    }),
    /artifactRoot must be/,
  );
  await assert.rejects(
    validateGatePaths({
      repositoryRoot: REPOSITORY_ROOT,
      artifactRoot: path.join(REPOSITORY_ROOT, "test-artifacts", "template-v2-pdf-golden-test"),
      receiptPath: path.join(REPOSITORY_ROOT, "..", "template-v2.pdf-golden.v1.json"),
    }),
    /receiptPath must be/,
  );
});
