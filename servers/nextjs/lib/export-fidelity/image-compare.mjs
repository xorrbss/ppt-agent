import fs from "node:fs/promises";
import path from "node:path";

import sharp from "sharp";

export const FIDELITY_CANVAS = Object.freeze({ width: 1280, height: 720 });

export const DEFAULT_FIDELITY_TOLERANCES = Object.freeze({
  maxMeanAbsoluteError: 6,
  maxBadPixelRatio: 0.015,
  maxBadComponentPixels: 4500,
  pixelDelta: 32,
});

const THRESHOLD_DEFINITIONS = Object.freeze([
  Object.freeze({
    metric: "meanAbsoluteError",
    tolerance: "maxMeanAbsoluteError",
  }),
  Object.freeze({
    metric: "badPixelRatio",
    tolerance: "maxBadPixelRatio",
  }),
  Object.freeze({
    metric: "largestBadComponentPixels",
    tolerance: "maxBadComponentPixels",
  }),
]);

async function rgbaAtCanvas(png, canvas) {
  const image = sharp(png).flatten({ background: "#ffffff" }).ensureAlpha();
  const metadata = await image.metadata();
  if (metadata.width !== canvas.width || metadata.height !== canvas.height) {
    throw new Error(
      `Expected ${canvas.width}x${canvas.height} PNG, received ${metadata.width}x${metadata.height}.`
    );
  }
  return image.raw().toBuffer({ resolveWithObject: true });
}

function largestBadComponent(mask, width, height) {
  const seen = new Uint8Array(mask.length);
  const queue = new Uint32Array(mask.length);
  let largest = 0;
  for (let start = 0; start < mask.length; start += 1) {
    if (!mask[start] || seen[start]) continue;
    let head = 0;
    let tail = 0;
    let size = 0;
    seen[start] = 1;
    queue[tail++] = start;
    while (head < tail) {
      const index = queue[head++];
      size += 1;
      const x = index % width;
      const y = Math.floor(index / width);
      const neighbors = [
        x > 0 ? index - 1 : -1,
        x + 1 < width ? index + 1 : -1,
        y > 0 ? index - width : -1,
        y + 1 < height ? index + width : -1,
      ];
      for (const neighbor of neighbors) {
        if (neighbor >= 0 && mask[neighbor] && !seen[neighbor]) {
          seen[neighbor] = 1;
          queue[tail++] = neighbor;
        }
      }
    }
    largest = Math.max(largest, size);
  }
  return largest;
}

/**
 * Compare source and exported slide renderings without resampling either image.
 * Flattening on white makes transparent Chrome screenshots comparable with the
 * opaque PDF renderer while preserving the fixed 1280 x 720 presentation grid.
 */
export async function compareSlidePngs(sourcePng, pptxPng, options = {}) {
  const canvas = options.canvas ?? FIDELITY_CANVAS;
  const tolerances = { ...DEFAULT_FIDELITY_TOLERANCES, ...options.tolerances };
  const [source, exported] = await Promise.all([
    rgbaAtCanvas(sourcePng, canvas),
    rgbaAtCanvas(pptxPng, canvas),
  ]);
  const pixels = canvas.width * canvas.height;
  const badMask = new Uint8Array(pixels);
  const diff = Buffer.alloc(pixels * 4);
  let totalAbsoluteError = 0;
  let badPixels = 0;
  for (let pixel = 0; pixel < pixels; pixel += 1) {
    const offset = pixel * 4;
    const red = Math.abs(source.data[offset] - exported.data[offset]);
    const green = Math.abs(source.data[offset + 1] - exported.data[offset + 1]);
    const blue = Math.abs(source.data[offset + 2] - exported.data[offset + 2]);
    const maxDelta = Math.max(red, green, blue);
    totalAbsoluteError += red + green + blue;
    const bad = maxDelta > tolerances.pixelDelta;
    if (bad) {
      badMask[pixel] = 1;
      badPixels += 1;
    }
    // A high-contrast red overlay leaves the source context recognizable.
    diff[offset] = Math.max(source.data[offset], Math.min(255, maxDelta * 4));
    diff[offset + 1] = bad ? Math.floor(source.data[offset + 1] * 0.35) : source.data[offset + 1];
    diff[offset + 2] = bad ? Math.floor(source.data[offset + 2] * 0.35) : source.data[offset + 2];
    diff[offset + 3] = 255;
  }
  const meanAbsoluteError = totalAbsoluteError / (pixels * 3);
  const badPixelRatio = badPixels / pixels;
  const largestBadComponentPixels = largestBadComponent(badMask, canvas.width, canvas.height);
  const metrics = {
    canvas,
    meanAbsoluteError,
    badPixels,
    badPixelRatio,
    largestBadComponentPixels,
    tolerances,
  };
  const thresholdResults = THRESHOLD_DEFINITIONS.map(({ metric, tolerance }) => {
    const actual = metrics[metric];
    const maximum = tolerances[tolerance];
    return {
      metric,
      tolerance,
      actual,
      maximum,
      passed: actual <= maximum,
      exceededBy: Math.max(0, actual - maximum),
    };
  });
  const failedThresholds = thresholdResults
    .filter((threshold) => !threshold.passed)
    .map((threshold) => threshold.metric);
  const passed = failedThresholds.length === 0;
  return {
    passed,
    metrics: {
      ...metrics,
      thresholdResults,
      failedThresholds,
    },
    diffPng: await sharp(diff, {
      raw: { width: canvas.width, height: canvas.height, channels: 4 },
    }).png().toBuffer(),
  };
}

function formatDiagnosticNumber(value) {
  if (Number.isInteger(value)) return String(value);
  return Number(value).toPrecision(6).replace(/(?:\.0+|(\.\d+?)0+)$/, "$1");
}

function thresholdDiagnosticsMarkdown(label, comparison) {
  const rows = comparison.metrics.thresholdResults.map((threshold) => {
    const delta = threshold.passed
      ? "-"
      : `+${formatDiagnosticNumber(threshold.exceededBy)}`;
    return `| ${threshold.metric} | ${formatDiagnosticNumber(threshold.actual)} | ${formatDiagnosticNumber(threshold.maximum)} | ${threshold.passed ? "PASS" : "FAIL"} | ${delta} |`;
  });
  return [
    `# Template V2 fidelity diagnostic: ${label}`,
    "",
    `Result: **${comparison.passed ? "PASS" : "FAIL"}**`,
    "",
    "| Product metric | Actual | Maximum | Result | Exceeded by |",
    "| --- | ---: | ---: | --- | ---: |",
    ...rows,
    "",
    `Pixel classification delta: ${formatDiagnosticNumber(comparison.metrics.tolerances.pixelDelta)}`,
    `Canvas: ${comparison.metrics.canvas.width} x ${comparison.metrics.canvas.height}`,
    "",
    "Inspect `source.png`, `pptx.png`, and the red-overlay `diff.png` together.",
    "",
  ].join("\n");
}

function serializeError(error) {
  if (error instanceof Error) {
    return {
      name: error.name,
      message: error.message,
      stack: error.stack,
    };
  }
  return {
    name: "Error",
    message: String(error),
  };
}

export async function writeFidelityFailureArtifacts({
  outputDirectory,
  label,
  sourcePng,
  pptxPng,
  comparison,
}) {
  const artifactDirectory = path.join(outputDirectory, label);
  await fs.mkdir(artifactDirectory, { recursive: true });
  await Promise.all([
    fs.writeFile(path.join(artifactDirectory, "source.png"), sourcePng),
    fs.writeFile(path.join(artifactDirectory, "pptx.png"), pptxPng),
    fs.writeFile(path.join(artifactDirectory, "diff.png"), comparison.diffPng),
    fs.writeFile(
      path.join(artifactDirectory, "metrics.json"),
      `${JSON.stringify({ passed: comparison.passed, ...comparison.metrics }, null, 2)}\n`
    ),
    fs.writeFile(
      path.join(artifactDirectory, "diagnostics.md"),
      thresholdDiagnosticsMarkdown(label, comparison)
    ),
  ]);
  return artifactDirectory;
}

export async function writeFidelityComparisonErrorArtifacts({
  outputDirectory,
  label,
  sourcePng,
  pptxPng,
  error,
}) {
  const artifactDirectory = path.join(outputDirectory, label);
  const serializedError = serializeError(error);
  await fs.mkdir(artifactDirectory, { recursive: true });
  await Promise.all([
    fs.writeFile(path.join(artifactDirectory, "source.png"), sourcePng),
    fs.writeFile(path.join(artifactDirectory, "pptx.png"), pptxPng),
    fs.writeFile(
      path.join(artifactDirectory, "comparison-error.json"),
      `${JSON.stringify(serializedError, null, 2)}\n`
    ),
    fs.writeFile(
      path.join(artifactDirectory, "diagnostics.md"),
      [
        `# Template V2 fidelity comparison error: ${label}`,
        "",
        `The visual comparison could not produce product metrics: ${serializedError.message}`,
        "",
        "Inspect `source.png` and `pptx.png` for canvas or renderer differences.",
        "",
      ].join("\n")
    ),
  ]);
  return artifactDirectory;
}
