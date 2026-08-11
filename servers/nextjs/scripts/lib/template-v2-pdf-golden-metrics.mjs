import assert from "node:assert/strict";
import { createHash } from "node:crypto";

import sharp from "sharp";

export const VIEWPORT = Object.freeze({ width: 1280, height: 720, dpi: 96 });

export const DEFAULT_THRESHOLDS = Object.freeze({
  meanAbsoluteError: 0.03,
  perceptualBadPixelRatio: 0.07,
  perceptualPixelDelta: 0.08,
  ssim: 0.9,
  largestBadComponent: 25_000,
  textBboxMeanAbsoluteError: 0.25,
  textBboxOutOfBounds: 0,
});

export const KNOWN_TEXT_LAYOUT_OBSERVATIONS = Object.freeze({
  "momentum/title_with_side_callouts_2594": Object.freeze({
    code: "known-momentum-side-callout-glyph-bounds-v1",
    reason: "Four transformed side-callout glyph ranges extend beyond the slide by browser Range geometry while the committed browser PNG remains clipped to the 1280x720 canvas.",
    evidence: "templates/manifests/momentum.browser-golden/04-title_with_side_callouts_2594.png",
    maxOutOfBounds: 4,
    maxClippedText: 4,
    maxOverflowElements: 4,
  }),
});

export function sha256(bytes) {
  return createHash("sha256").update(bytes).digest("hex");
}

function average(values) {
  return values.length
    ? values.reduce((total, value) => total + value, 0) / values.length
    : 0;
}

function maximum(values) {
  return values.length ? Math.max(...values) : 0;
}

function round(value, digits = 8) {
  return Number(value.toFixed(digits));
}

function largestConnectedComponent(mask, width, height) {
  const queue = new Int32Array(width * height);
  let largest = 0;
  for (let start = 0; start < mask.length; start += 1) {
    if (mask[start] !== 1) continue;
    let head = 0;
    let tail = 0;
    queue[tail++] = start;
    mask[start] = 2;
    while (head < tail) {
      const current = queue[head++];
      const x = current % width;
      const up = current - width;
      const down = current + width;
      if (up >= 0 && mask[up] === 1) {
        mask[up] = 2;
        queue[tail++] = up;
      }
      if (down < mask.length && mask[down] === 1) {
        mask[down] = 2;
        queue[tail++] = down;
      }
      if (x > 0 && mask[current - 1] === 1) {
        mask[current - 1] = 2;
        queue[tail++] = current - 1;
      }
      if (x + 1 < width && mask[current + 1] === 1) {
        mask[current + 1] = 2;
        queue[tail++] = current + 1;
      }
    }
    largest = Math.max(largest, tail);
  }
  return largest;
}

function blockSsim(actualLuma, expectedLuma, width, height, blockSize = 8) {
  const c1 = (0.01 * 255) ** 2;
  const c2 = (0.03 * 255) ** 2;
  let total = 0;
  let blocks = 0;
  for (let top = 0; top < height; top += blockSize) {
    for (let left = 0; left < width; left += blockSize) {
      const right = Math.min(width, left + blockSize);
      const bottom = Math.min(height, top + blockSize);
      const count = (right - left) * (bottom - top);
      let actualMean = 0;
      let expectedMean = 0;
      for (let y = top; y < bottom; y += 1) {
        for (let x = left; x < right; x += 1) {
          const index = y * width + x;
          actualMean += actualLuma[index];
          expectedMean += expectedLuma[index];
        }
      }
      actualMean /= count;
      expectedMean /= count;
      let actualVariance = 0;
      let expectedVariance = 0;
      let covariance = 0;
      for (let y = top; y < bottom; y += 1) {
        for (let x = left; x < right; x += 1) {
          const index = y * width + x;
          const actualDelta = actualLuma[index] - actualMean;
          const expectedDelta = expectedLuma[index] - expectedMean;
          actualVariance += actualDelta ** 2;
          expectedVariance += expectedDelta ** 2;
          covariance += actualDelta * expectedDelta;
        }
      }
      const divisor = Math.max(1, count - 1);
      actualVariance /= divisor;
      expectedVariance /= divisor;
      covariance /= divisor;
      total +=
        ((2 * actualMean * expectedMean + c1) * (2 * covariance + c2)) /
        ((actualMean ** 2 + expectedMean ** 2 + c1) *
          (actualVariance + expectedVariance + c2));
      blocks += 1;
    }
  }
  return total / blocks;
}

function bboxSummary(prefix, width, height, textBoxes, thresholds) {
  const stride = width + 1;
  const values = [];
  let outOfBounds = 0;
  for (const box of textBoxes ?? []) {
    if (
      box.x < -1 ||
      box.y < -1 ||
      box.x + box.width > width + 1 ||
      box.y + box.height > height + 1
    ) {
      outOfBounds += 1;
    }
    const left = Math.max(0, Math.min(width, Math.floor(box.x)));
    const top = Math.max(0, Math.min(height, Math.floor(box.y)));
    const right = Math.max(left + 1, Math.min(width, Math.ceil(box.x + box.width)));
    const bottom = Math.max(top + 1, Math.min(height, Math.ceil(box.y + box.height)));
    const sum =
      prefix[bottom * stride + right] -
      prefix[top * stride + right] -
      prefix[bottom * stride + left] +
      prefix[top * stride + left];
    values.push(sum / ((right - left) * (bottom - top)));
  }
  const maximumMeanAbsoluteError = maximum(values);
  return {
    count: values.length,
    outOfBounds,
    maximumMeanAbsoluteError: round(maximumMeanAbsoluteError),
    passed:
      outOfBounds <= thresholds.textBboxOutOfBounds &&
      maximumMeanAbsoluteError <= thresholds.textBboxMeanAbsoluteError,
  };
}

export async function comparePngBuffers(
  actualBytes,
  expectedBytes,
  { textBoxes = [], thresholds = DEFAULT_THRESHOLDS } = {},
) {
  const [actual, expected] = await Promise.all([
    sharp(actualBytes).removeAlpha().raw().toBuffer({ resolveWithObject: true }),
    sharp(expectedBytes).removeAlpha().raw().toBuffer({ resolveWithObject: true }),
  ]);
  assert.equal(actual.info.width, expected.info.width);
  assert.equal(actual.info.height, expected.info.height);
  assert.equal(actual.info.channels, 3);
  assert.equal(expected.info.channels, 3);
  const { width, height } = actual.info;
  const pixels = width * height;
  const mask = new Uint8Array(pixels);
  const actualLuma = new Float32Array(pixels);
  const expectedLuma = new Float32Array(pixels);
  const prefix = new Float64Array((width + 1) * (height + 1));
  let totalDelta = 0;
  let badPixels = 0;
  for (let index = 0; index < pixels; index += 1) {
    const offset = index * 3;
    const dr = Math.abs(actual.data[offset] - expected.data[offset]);
    const dg = Math.abs(actual.data[offset + 1] - expected.data[offset + 1]);
    const db = Math.abs(actual.data[offset + 2] - expected.data[offset + 2]);
    const normalizedMae = (dr + dg + db) / (3 * 255);
    totalDelta += normalizedMae;
    const perceptual = Math.sqrt(0.299 * dr ** 2 + 0.587 * dg ** 2 + 0.114 * db ** 2) / 255;
    if (perceptual > thresholds.perceptualPixelDelta) {
      mask[index] = 1;
      badPixels += 1;
    }
    actualLuma[index] = 0.2126 * actual.data[offset] + 0.7152 * actual.data[offset + 1] + 0.0722 * actual.data[offset + 2];
    expectedLuma[index] = 0.2126 * expected.data[offset] + 0.7152 * expected.data[offset + 1] + 0.0722 * expected.data[offset + 2];
    const y = Math.floor(index / width) + 1;
    const x = (index % width) + 1;
    const stride = width + 1;
    prefix[y * stride + x] = normalizedMae + prefix[(y - 1) * stride + x] + prefix[y * stride + x - 1] - prefix[(y - 1) * stride + x - 1];
  }
  const metrics = {
    meanAbsoluteError: round(totalDelta / pixels),
    perceptualBadPixelRatio: round(badPixels / pixels),
    ssim: round(blockSsim(actualLuma, expectedLuma, width, height)),
    largestBadComponent: largestConnectedComponent(mask, width, height),
  };
  const textBbox = bboxSummary(prefix, width, height, textBoxes, thresholds);
  const failures = [];
  if (metrics.meanAbsoluteError > thresholds.meanAbsoluteError) failures.push("mean-absolute-error");
  if (metrics.perceptualBadPixelRatio > thresholds.perceptualBadPixelRatio) failures.push("perceptual-pixels");
  if (metrics.ssim < thresholds.ssim) failures.push("ssim");
  if (metrics.largestBadComponent > thresholds.largestBadComponent) failures.push("largest-component");
  if (!textBbox.passed) failures.push("text-bbox");
  return { width, height, metrics, textBbox, failures, passed: failures.length === 0, badMask: mask };
}

export function classifyFailure({ telemetry = {}, comparison = {} }) {
  if (telemetry.fontSetStatus && telemetry.fontSetStatus !== "loaded") return "font-fallback";
  if ((telemetry.failedFonts ?? 0) > 0) return "font-fallback";
  if ((telemetry.clippedText ?? 0) > 0) return "clip";
  if ((telemetry.overflowElements ?? 0) > 0) return "overflow";
  if ((telemetry.chartCanvasCount ?? 0) > 0) return "chart";
  if ((telemetry.svgCount ?? 0) > 0) return "svg";
  if ((telemetry.transparencyCount ?? 0) > 0) return "transparency";
  if (comparison.textBbox?.passed === false) return "text-bbox";
  return "unclassified-visual-drift";
}

export function imageReadinessState({ complete, naturalWidth }) {
  if (!complete) return "pending";
  return naturalWidth > 0 ? "loaded" : "failed";
}

export function applyLayoutTextPolicy({
  templateId,
  layoutId,
  textBbox,
  telemetry,
  failures,
  thresholds = DEFAULT_THRESHOLDS,
}) {
  const observation = KNOWN_TEXT_LAYOUT_OBSERVATIONS[`${templateId}/${layoutId}`];
  const outOfBoundsAllowed = observation
    ? textBbox.outOfBounds <= observation.maxOutOfBounds
    : textBbox.outOfBounds <= thresholds.textBboxOutOfBounds;
  const clippedAllowed = observation
    ? (telemetry.clippedText ?? 0) <= observation.maxClippedText
    : (telemetry.clippedText ?? 0) === 0;
  const overflowAllowed = observation
    ? (telemetry.overflowElements ?? 0) <= observation.maxOverflowElements
    : (telemetry.overflowElements ?? 0) === 0;
  const meanAllowed =
    textBbox.maximumMeanAbsoluteError <= thresholds.textBboxMeanAbsoluteError;
  const adjusted = failures.filter((failure) => failure !== "text-bbox");
  if (!meanAllowed || !outOfBoundsAllowed) adjusted.push("text-bbox");
  if (!clippedAllowed) adjusted.push("text-clip");
  if (!overflowAllowed) adjusted.push("text-overflow");
  const observedException =
    observation &&
    ((textBbox.outOfBounds ?? 0) > 0 ||
      (telemetry.clippedText ?? 0) > 0 ||
      (telemetry.overflowElements ?? 0) > 0);
  return {
    failures: [...new Set(adjusted)],
    passed: adjusted.length === 0,
    acceptedObservation: observedException
      ? { ...observation, observed: {
          outOfBounds: textBbox.outOfBounds,
          clippedText: telemetry.clippedText ?? 0,
          overflowElements: telemetry.overflowElements ?? 0,
        } }
      : null,
  };
}

export function aggregateLayoutMetrics(layoutResults) {
  return {
    meanAbsoluteErrorAverage: round(average(layoutResults.map((layout) => layout.metrics.meanAbsoluteError))),
    meanAbsoluteErrorMaximum: round(maximum(layoutResults.map((layout) => layout.metrics.meanAbsoluteError))),
    perceptualBadPixelRatioAverage: round(average(layoutResults.map((layout) => layout.metrics.perceptualBadPixelRatio))),
    perceptualBadPixelRatioMaximum: round(maximum(layoutResults.map((layout) => layout.metrics.perceptualBadPixelRatio))),
    ssimMinimum: round(Math.min(...layoutResults.map((layout) => layout.metrics.ssim))),
    largestBadComponentMaximum: maximum(layoutResults.map((layout) => layout.metrics.largestBadComponent)),
    textBboxMeanAbsoluteErrorMaximum: round(maximum(layoutResults.map((layout) => layout.textBbox.maximumMeanAbsoluteError))),
    textBboxCount: layoutResults.reduce((total, layout) => total + layout.textBbox.count, 0),
    textBboxOutOfBounds: layoutResults.reduce((total, layout) => total + layout.textBbox.outOfBounds, 0),
  };
}
