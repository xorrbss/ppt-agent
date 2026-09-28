import assert from "node:assert/strict";
import test from "node:test";

import { normalizeAuthoredStyleSamples } from "./authored-style-samples.ts";
import { normalizeAuthoredStyles } from "./authored.ts";

const samplePath = (index: number, size: "thumb" | "full") =>
  `/api/v1/ppt/authored/styles/neon-venture/samples/${index}?size=${size}`;

const validSamples = {
  canvas: { width: 1280, height: 720 },
  slides: [
    { index: 1, title: " Cover ", thumb: samplePath(1, "thumb"), full: samplePath(1, "full") },
    { index: 2, title: "Market", thumb: samplePath(2, "thumb"), full: samplePath(2, "full") },
  ],
};

test("style samples keep the canvas and map slide URLs", () => {
  assert.deepEqual(normalizeAuthoredStyleSamples(validSamples), {
    canvas: { width: 1280, height: 720 },
    slides: [
      { index: 1, title: "Cover", thumbUrl: samplePath(1, "thumb"), fullUrl: samplePath(1, "full") },
      { index: 2, title: "Market", thumbUrl: samplePath(2, "thumb"), fullUrl: samplePath(2, "full") },
    ],
  });
});

test("style sample URLs resolve through getApiUrl", () => {
  const previous = process.env.NEXT_PUBLIC_FAST_API;
  process.env.NEXT_PUBLIC_FAST_API = "http://backend.test:8000";
  try {
    const samples = normalizeAuthoredStyleSamples(validSamples);
    assert.equal(samples?.slides[0].thumbUrl, `http://backend.test:8000${samplePath(1, "thumb")}`);
    assert.equal(samples?.slides[0].fullUrl, `http://backend.test:8000${samplePath(1, "full")}`);
  } finally {
    if (previous === undefined) delete process.env.NEXT_PUBLIC_FAST_API;
    else process.env.NEXT_PUBLIC_FAST_API = previous;
  }
});

test("style samples accept a non-16:9 canvas", () => {
  const samples = normalizeAuthoredStyleSamples({ ...validSamples, canvas: { width: 1280, height: 905 } });
  assert.deepEqual(samples?.canvas, { width: 1280, height: 905 });
});

test("malformed sample slides are dropped individually and sorted by index", () => {
  const samples = normalizeAuthoredStyleSamples({
    canvas: { width: 1280, height: 720 },
    slides: [
      { index: 3, title: "Product", thumb: samplePath(3, "thumb"), full: samplePath(3, "full") },
      { index: 0, title: "Zero", thumb: samplePath(1, "thumb"), full: samplePath(1, "full") },
      { index: 1.5, title: "Fraction", thumb: samplePath(1, "thumb"), full: samplePath(1, "full") },
      { index: 4, title: "   ", thumb: samplePath(4, "thumb"), full: samplePath(4, "full") },
      { index: 5, title: "No full", thumb: samplePath(5, "thumb") },
      { index: 6, title: "Foreign", thumb: "https://evil.test/a.webp", full: samplePath(6, "full") },
      { index: 7, title: "Protocol relative", thumb: "//evil.test/a.webp", full: samplePath(7, "full") },
      { index: 1, title: "Cover", thumb: samplePath(1, "thumb"), full: samplePath(1, "full") },
      { index: 1, title: "Duplicate", thumb: samplePath(1, "thumb"), full: samplePath(1, "full") },
      null,
      "slide",
    ],
  });
  assert.deepEqual(
    samples?.slides.map(({ index, title }) => [index, title]),
    [[1, "Cover"], [3, "Product"]],
  );
});

test("invalid canvas or empty decks yield undefined", () => {
  assert.equal(normalizeAuthoredStyleSamples(undefined), undefined);
  assert.equal(normalizeAuthoredStyleSamples([]), undefined);
  assert.equal(normalizeAuthoredStyleSamples({ slides: validSamples.slides }), undefined);
  assert.equal(normalizeAuthoredStyleSamples({ ...validSamples, canvas: { width: 1280 } }), undefined);
  assert.equal(normalizeAuthoredStyleSamples({ ...validSamples, canvas: { width: 0, height: 720 } }), undefined);
  assert.equal(normalizeAuthoredStyleSamples({ ...validSamples, canvas: { width: "1280", height: 720 } }), undefined);
  assert.equal(normalizeAuthoredStyleSamples({ ...validSamples, slides: [] }), undefined);
  assert.equal(normalizeAuthoredStyleSamples({ ...validSamples, slides: [{ index: 1 }] }), undefined);
});

test("catalogue entries attach samples only when valid", () => {
  const base = {
    id: "neon-venture",
    name: "Neon Venture",
    description: "desc",
    category: "creative",
    preview: { bg: "#000000", accent: "#FF00FF" },
  };
  const [, withSamples, withoutSamples, withInvalidSamples] = normalizeAuthoredStyles([
    { ...base, samples: validSamples },
    { ...base, id: "plain" },
    { ...base, id: "broken", samples: { canvas: { width: 1280, height: 720 }, slides: [] } },
  ]);
  assert.equal(withSamples.samples?.slides.length, 2);
  assert.equal(Object.hasOwn(withoutSamples, "samples"), false);
  assert.equal(Object.hasOwn(withInvalidSamples, "samples"), false);
});
