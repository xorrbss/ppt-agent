import assert from "node:assert/strict";
import test from "node:test";

import {
  buildFidelityQaSummary,
  compareElementMapSemantics,
  renderFidelityQaMarkdown,
} from "./authored-hybrid-fidelity-report.mjs";

function map(bounds, { lineCount = 1, mode = "native", clipped = false } = {}) {
  return { slides: [{ slideNumber: 1, elements: [{ id: "text-1", kind: "text", mode, bounds, reasons: clipped ? ["overflow-clipped"] : [], text: { layout: { lineCount } }, powerPoint: { bounds, text: { layout: { lineCount } } } }] }] };
}

test("semantic map comparison flags native-text, line and clipping regressions", () => {
  const result = compareElementMapSemantics(map({ x: 1, y: 2, width: 40, height: 10 }), map({ x: 2.2, y: 2, width: 40, height: 10 }, { lineCount: 2, clipped: true }));
  assert.equal(result.passed, false);
  assert.equal(result.summary.bboxViolations, 1);
  assert.equal(result.summary.lineCountChanges, 1);
  assert.equal(result.summary.newClipping, 1);
  assert.equal(compareElementMapSemantics(map({ x: 0, y: 0, width: 1, height: 1 }), { slides: [{ slideNumber: 1, elements: [] }] }).summary.missingNativeText, 1);
});

test("fidelity summary combines 20-slide visual, quality and semantics gates", () => {
  const visual = { summary: { authoredVsEditable: { meanAbsoluteErrorAverage: 1, badPixelRatioAverage: 0.01 } }, slides: [{ slideNumber: 1, authoredVsEditable: { meanAbsoluteError: 1, badPixelRatio: 0.01 } }] };
  const quality = { nativeTextElements: 2, nativeShapeElements: 3, rasterFallbackElements: 0, imageFallbackSlides: 0, editableSlides: 20, slides: [{ slideNumber: 1, nativeTextElements: 2, nativeShapeElements: 3, rasterFallbackElements: 0 }] };
  const summary = buildFidelityQaSummary({ candidateQuality: quality, candidateVisual: visual, candidateElementMap: map({ x: 0, y: 0, width: 1, height: 1 }), baselineQuality: quality, baselineVisual: visual, baselineElementMap: map({ x: 0, y: 0, width: 1, height: 1 }), semantics: { summary: { centeredMisclassifications: 0, partialBoldLosses: 0 } }, releaseGates: { passed: true }, expectedSlides: 1 });
  assert.equal(summary.aggregate.passed, true);
  assert.match(renderFidelityQaMarkdown(summary), /Native text/);
});
