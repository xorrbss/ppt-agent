import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import sharp from "sharp";

import {
  FIDELITY_CANVAS,
  compareSlidePngs,
  writeFidelityComparisonErrorArtifacts,
  writeFidelityFailureArtifacts,
} from "./image-compare.mjs";

async function solid(color) {
  return sharp({
    create: { width: FIDELITY_CANVAS.width, height: FIDELITY_CANVAS.height, channels: 4, background: color },
  }).png().toBuffer();
}

test("fixed-canvas comparison accepts equal images and rejects a large visual regression", async () => {
  const source = await solid("#ffffff");
  const same = await compareSlidePngs(source, source);
  assert.equal(same.passed, true);
  assert.equal(same.metrics.badPixels, 0);

  const changed = await sharp(source)
    .composite([{ input: await sharp({ create: { width: 200, height: 100, channels: 4, background: "#123b72" } }).png().toBuffer(), left: 100, top: 100 }])
    .png()
    .toBuffer();
  const regression = await compareSlidePngs(source, changed);
  assert.equal(regression.passed, false);
  assert.ok(regression.metrics.largestBadComponentPixels >= 20_000);
  assert.deepEqual(regression.metrics.failedThresholds, [
    "badPixelRatio",
    "largestBadComponentPixels",
  ]);
  for (const threshold of regression.metrics.thresholdResults) {
    assert.equal(threshold.passed, threshold.actual <= threshold.maximum);
    assert.equal(threshold.exceededBy, Math.max(0, threshold.actual - threshold.maximum));
  }
});

test("comparison rejects dimensions and writes inspectable failure artifacts", async () => {
  const source = await solid("#ffffff");
  const tiny = await sharp({ create: { width: 20, height: 20, channels: 4, background: "#fff" } }).png().toBuffer();
  await assert.rejects(() => compareSlidePngs(source, tiny), /Expected 1280x720/);

  const changed = await solid("#123b72");
  const comparison = await compareSlidePngs(source, changed);
  const temp = await fs.mkdtemp(path.join(os.tmpdir(), "export-fidelity-artifacts-"));
  try {
    const directory = await writeFidelityFailureArtifacts({ outputDirectory: temp, label: "case", sourcePng: source, pptxPng: changed, comparison });
    for (const filename of ["source.png", "pptx.png", "diff.png", "metrics.json", "diagnostics.md"]) {
      await fs.access(path.join(directory, filename));
    }
    const metrics = JSON.parse(await fs.readFile(path.join(directory, "metrics.json"), "utf8"));
    assert.deepEqual(metrics.failedThresholds, comparison.metrics.failedThresholds);
    const diagnostics = await fs.readFile(path.join(directory, "diagnostics.md"), "utf8");
    assert.match(diagnostics, /largestBadComponentPixels.*FAIL/);

    const errorDirectory = await writeFidelityComparisonErrorArtifacts({
      outputDirectory: temp,
      label: "dimension-error",
      sourcePng: source,
      pptxPng: tiny,
      error: new Error("Expected 1280x720 PNG, received 20x20."),
    });
    for (const filename of ["source.png", "pptx.png", "comparison-error.json", "diagnostics.md"]) {
      await fs.access(path.join(errorDirectory, filename));
    }
    const comparisonError = JSON.parse(
      await fs.readFile(path.join(errorDirectory, "comparison-error.json"), "utf8")
    );
    assert.match(comparisonError.message, /received 20x20/);
  } finally {
    await fs.rm(temp, { recursive: true, force: true });
  }
});
