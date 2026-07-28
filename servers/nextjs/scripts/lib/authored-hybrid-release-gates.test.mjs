import assert from "node:assert/strict";
import test from "node:test";

import { evaluateAuthoredHybridReleaseGates } from "./authored-hybrid-release-gates.mjs";

function visual(mae, bad) {
  return {
    pageCount: 20,
    summary: { authoredVsEditable: { meanAbsoluteErrorAverage: mae, badPixelRatioAverage: bad } },
    slides: Array.from({ length: 20 }, (_, index) => ({
      slideNumber: index + 1,
      authoredVsEditable: { meanAbsoluteError: mae, badPixelRatio: bad },
    })),
  };
}

function quality() {
  return {
    totalSlides: 20,
    editableSlides: 20,
    imageFallbackSlides: 0,
    nativeTextElements: 10,
    nativeShapeElements: 10,
    nativeGroupElements: 0,
    nativeImageElements: 0,
    rasterFallbackElements: 2,
  };
}

test("release gates accept preserved editability and per-slide visual improvements", () => {
  const result = evaluateAuthoredHybridReleaseGates({
    baselineQuality: quality(),
    candidateQuality: quality(),
    baselineVisual: visual(12, 0.12),
    candidateVisual: visual(11, 0.11),
    baselineElementMap: { summary: { rasterClassifiedElements: 2 } },
    candidateElementMap: { slideCount: 20, summary: { elementCount: 40, rasterClassifiedElements: 2 } },
  });
  assert.equal(result.passed, true);
  assert.deepEqual(result.regressedSlides, []);
  assert.deepEqual(result.focusBadPixelImprovedSlides, [5, 8, 12, 18]);
});

test("release gates reject a single slide regression even when averages improve", () => {
  const candidate = visual(11, 0.11);
  candidate.slides[4].authoredVsEditable.badPixelRatio = 0.13;
  const result = evaluateAuthoredHybridReleaseGates({
    baselineQuality: quality(),
    candidateQuality: quality(),
    baselineVisual: visual(12, 0.12),
    candidateVisual: candidate,
    candidateElementMap: { slideCount: 20, summary: { elementCount: 40 } },
  });
  assert.equal(result.passed, false);
  assert.deepEqual(result.regressedSlides, [5]);
});
