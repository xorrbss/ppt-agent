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
  const passed =
    meanAbsoluteError <= tolerances.maxMeanAbsoluteError &&
    badPixelRatio <= tolerances.maxBadPixelRatio &&
    largestBadComponentPixels <= tolerances.maxBadComponentPixels;
  return {
    passed,
    metrics,
    diffPng: await sharp(diff, {
      raw: { width: canvas.width, height: canvas.height, channels: 4 },
    }).png().toBuffer(),
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
  ]);
  return artifactDirectory;
}
