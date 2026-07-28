import assert from "node:assert/strict";
import test from "node:test";

import sharp from "sharp";

import {
  analyzeDiffRegions,
  compareAgainstBaseline,
  summarizeFallbackQuality,
} from "./qa-visual-diagnostics.mjs";

test("analyzeDiffRegions returns bounded paint-aware regions and element matches", async () => {
  const source = await sharp({
    create: {
      width: 16,
      height: 12,
      channels: 4,
      background: "#ffffff",
    },
  })
    .png()
    .toBuffer();
  const exported = await sharp(source)
    .composite([
      {
        input: {
          create: {
            width: 6,
            height: 3,
            channels: 4,
            background: "#000000",
          },
        },
        left: 4,
        top: 5,
      },
    ])
    .png()
    .toBuffer();
  const regions = await analyzeDiffRegions(source, exported, {
    width: 16,
    height: 12,
    pixelDelta: 32,
    elements: [
      {
        id: "shape-1",
        kind: "shape",
        bounds: { x: 4, y: 5, width: 6, height: 3 },
      },
    ],
  });
  assert.equal(regions[0].badPixels, 18);
  assert.deepEqual(
    {
      x: regions[0].x,
      y: regions[0].y,
      width: regions[0].width,
      height: regions[0].height,
    },
    { x: 4, y: 5, width: 6, height: 3 }
  );
  assert.equal(regions[0].matchedElements[0].id, "shape-1");
});

test("compareAgainstBaseline identifies average and slide regressions", () => {
  const baseline = {
    __path: "baseline.json",
    summary: {
      authoredVsEditable: {
        meanAbsoluteErrorAverage: 10,
        badPixelRatioAverage: 0.1,
      },
    },
    slides: [
      {
        slideNumber: 1,
        authoredVsEditable: {
          meanAbsoluteError: 10,
          badPixelRatio: 0.1,
        },
      },
    ],
  };
  const result = compareAgainstBaseline(
    { meanAbsoluteErrorAverage: 10.2, badPixelRatioAverage: 0.101 },
    [
      {
        slideNumber: 1,
        authoredVsEditable: {
          meanAbsoluteError: 10.2,
          badPixelRatio: 0.101,
        },
      },
    ],
    baseline
  );
  assert.equal(result.classification, "regressed");
  assert.deepEqual(result.regressedSlides, [1]);
});

test("summarizeFallbackQuality aggregates slide counts and reason frequency", () => {
  const result = summarizeFallbackQuality(
    {
      slides: [
        {
          slideNumber: 1,
          rasterFallbackElements: 2,
          fallbackReasons: ["shadow", "shadow", "clip"],
        },
      ],
    },
    2
  );
  assert.equal(result.totalRasterFallbackElements, 2);
  assert.deepEqual(result.reasonFrequency, { shadow: 2, clip: 1 });
  assert.equal(result.slides[1].rasterFallbackElements, 0);
});
