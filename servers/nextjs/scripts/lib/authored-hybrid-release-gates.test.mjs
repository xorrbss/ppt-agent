import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_AUTHORED_HYBRID_RELEASE_THRESHOLDS,
  evaluateAuthoredHybridReleaseGates,
} from "./authored-hybrid-release-gates.mjs";

function visual(mae, bad) {
  return {
    pageCount: 20,
    summary: {
      authoredVsEditable: {
        meanAbsoluteErrorAverage: mae,
        badPixelRatioAverage: bad,
      },
    },
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
    nativeTextElements: 1184,
    nativeShapeElements: 568,
    nativeGroupElements: 0,
    nativeImageElements: 0,
    rasterFallbackElements: 56,
  };
}

function textElement(
  id,
  {
    profile,
    role = "body",
    runCount = 1,
    x = 20,
    y = 20,
    width = 200,
    height = 40,
    lineCount = 1,
    display = "block",
    flexDirection = "row",
    paddingPx = { top: 0, right: 0, bottom: 0, left: 0 },
    horizontalAlignment = "left",
    verticalAlignment = "top",
    singleLine = lineCount === 1,
    reasons = [],
  }
) {
  const text = {
    profile,
    role,
    runCount,
    font: { horizontalAlignment, verticalAlignment },
    layout: {
      paintedTextBounds: { x, y, width, height },
      lineCount,
      display,
      flexDirection,
      paddingPx,
      singleLine,
    },
  };
  return {
    id,
    kind: role === "table-cell" ? "table-cell" : "text",
    mode: "native",
    reasons,
    bounds: { x, y, width, height },
    text,
    powerPoint: {
      bounds: { x, y, width, height },
      text: structuredClone(text),
    },
  };
}

function elementMap() {
  const elements = [
    textElement("display-title", {
      profile: "display-title",
      role: "title",
      display: "flex",
      flexDirection: "row",
      horizontalAlignment: "center",
    }),
    textElement("multiline-title", {
      profile: "multiline-title",
      role: "title",
      runCount: 3,
      lineCount: 2,
      display: "flex",
      flexDirection: "column",
    }),
    textElement("mixed-weight", {
      profile: "mixed-weight",
      runCount: 4,
      lineCount: 2,
      y: 80,
    }),
    textElement("asymmetric-padding", {
      profile: "body",
      paddingPx: { top: 2, right: 12, bottom: 6, left: 4 },
      y: 140,
    }),
    textElement("table-cell", {
      profile: "table-cell",
      role: "table-cell",
      horizontalAlignment: "center",
      verticalAlignment: "middle",
      y: 200,
    }),
    textElement("nowrap-title", {
      profile: "display-title",
      role: "title",
      singleLine: true,
      y: 260,
    }),
    textElement("vertical-anchor", {
      profile: "centered-label",
      horizontalAlignment: "center",
      verticalAlignment: "bottom",
      y: 320,
    }),
    textElement("compact-caption", {
      profile: "compact-caption",
      runCount: 2,
      y: 380,
    }),
  ];
  return {
    slideCount: 20,
    summary: {
      elementCount: elements.length,
      rasterClassifiedElements: 56,
    },
    slides: [{ slideNumber: 1, elements }],
  };
}

function semantics(overrides = {}) {
  return {
    summary: {
      nativeText: 1184,
      centeredTargets: 91,
      centeredMisclassifications: 0,
      partialBoldTargets: 74,
      partialBoldLosses: 0,
      ...overrides,
    },
  };
}

function packageHealth(overrides = {}) {
  return {
    summary: {
      ooxmlErrors: 0,
      missingRelationships: 0,
      duplicateNonVisualIds: 0,
      ...overrides,
    },
  };
}

function powerPointValidation(overrides = {}) {
  return {
    summary: {
      repairWarnings: 0,
      openedWithoutWarnings: true,
      saveAsSucceeded: true,
      reopenedWithoutWarnings: true,
      ...overrides,
    },
  };
}

function completeEvidence(overrides = {}) {
  const baselineMap = overrides.baselineElementMap ?? elementMap();
  const candidateMap =
    overrides.candidateElementMap ?? structuredClone(baselineMap);
  return {
    baselineQuality: quality(),
    candidateQuality: quality(),
    baselineVisual: visual(12, 0.12),
    candidateVisual: visual(11, 0.11),
    baselineElementMap: baselineMap,
    candidateElementMap: candidateMap,
    baselineTextSemantics: semantics(),
    candidateTextSemantics: semantics(),
    candidatePackageHealth: packageHealth(),
    candidatePowerPointValidation: powerPointValidation(),
    requirePackageHealth: true,
    requirePowerPointValidation: true,
    requireTextSemantics: true,
    requireLayoutEvidence: true,
    ...overrides,
  };
}

function check(result, id) {
  const found = result.checks.find((entry) => entry.id === id);
  assert.ok(found, `missing check ${id}`);
  return found;
}

test("release gates accept preserved editability and per-slide visual improvements", () => {
  const result = evaluateAuthoredHybridReleaseGates({
    baselineQuality: quality(),
    candidateQuality: quality(),
    baselineVisual: visual(12, 0.12),
    candidateVisual: visual(11, 0.11),
    baselineElementMap: { summary: { rasterClassifiedElements: 56 } },
    candidateElementMap: {
      slideCount: 20,
      summary: { elementCount: 40, rasterClassifiedElements: 56 },
    },
  });
  assert.equal(result.passed, true);
  assert.deepEqual(result.regressedSlides, []);
  assert.deepEqual(result.focusBadPixelImprovedSlides, [5, 8, 12, 18]);
});

test("release gates classify bounded per-slide movement as renderer noise", () => {
  const candidate = visual(11, 0.11);
  candidate.slides[2].authoredVsEditable = {
    meanAbsoluteError: 12.1,
    badPixelRatio: 0.121,
  };
  const result = evaluateAuthoredHybridReleaseGates({
    baselineQuality: quality(),
    candidateQuality: quality(),
    baselineVisual: visual(12, 0.12),
    candidateVisual: candidate,
    candidateElementMap: {
      slideCount: 20,
      summary: { elementCount: 40 },
    },
  });
  assert.equal(result.passed, true);
  assert.deepEqual(result.regressedSlides, []);
  assert.equal(result.slideClassifications[2].classification, "noise");
  assert.deepEqual(result.thresholds, {
    ...DEFAULT_AUTHORED_HYBRID_RELEASE_THRESHOLDS,
  });
});

test("release gates keep a large slide 19 regression as a failure", () => {
  const candidate = visual(11, 0.11);
  candidate.slides[18].authoredVsEditable.meanAbsoluteError = 12.1001;
  const result = evaluateAuthoredHybridReleaseGates({
    baselineQuality: quality(),
    candidateQuality: quality(),
    baselineVisual: visual(12, 0.12),
    candidateVisual: candidate,
    candidateElementMap: {
      slideCount: 20,
      summary: { elementCount: 40 },
    },
  });
  assert.equal(result.passed, false);
  assert.deepEqual(result.regressedSlides, [19]);
  assert.equal(result.slideClassifications[18].classification, "regressed");
});

test("release gates retain semantic layout evidence across representative text contexts", () => {
  const result = evaluateAuthoredHybridReleaseGates(completeEvidence());
  assert.equal(result.passed, true);
  assert.equal(result.layout.matchedTextElements, 8);
  assert.equal(result.layout.bboxCompared, 8);
  assert.equal(result.layout.lineCountCompared, 8);
  assert.deepEqual(result.layout.bboxViolations, []);
  assert.deepEqual(result.layout.lineCountChanges, []);
  assert.deepEqual(result.layout.newOverflowClipping, []);
});

test("text bbox movement at 1px passes and movement over 1px fails with context", () => {
  const baselineMap = elementMap();
  const within = structuredClone(baselineMap);
  within.slides[0].elements[0].powerPoint.text.layout.paintedTextBounds.x += 1;
  const accepted = evaluateAuthoredHybridReleaseGates(
    completeEvidence({
      baselineElementMap: baselineMap,
      candidateElementMap: within,
    })
  );
  assert.equal(accepted.passed, true);

  const outside = structuredClone(within);
  outside.slides[0].elements[0].powerPoint.text.layout.paintedTextBounds.x +=
    0.01;
  const rejected = evaluateAuthoredHybridReleaseGates(
    completeEvidence({
      baselineElementMap: baselineMap,
      candidateElementMap: outside,
    })
  );
  assert.equal(rejected.passed, false);
  const bboxCheck = check(rejected, "text-bbox-movement-within-tolerance");
  assert.equal(bboxCheck.passed, false);
  assert.equal(bboxCheck.details[0].elementId, "display-title");
  assert.equal(bboxCheck.details[0].context.display, "flex");
  assert.equal(bboxCheck.details[0].context.flexDirection, "row");
});

test("line-count changes and new table-cell clipping are hard failures", () => {
  const baselineMap = elementMap();
  const candidateMap = structuredClone(baselineMap);
  candidateMap.slides[0].elements[1].powerPoint.text.layout.lineCount = 3;
  candidateMap.slides[0].elements[4].powerPoint.text.layout.clipped = true;
  const result = evaluateAuthoredHybridReleaseGates(
    completeEvidence({
      baselineElementMap: baselineMap,
      candidateElementMap: candidateMap,
    })
  );
  assert.equal(result.passed, false);
  assert.equal(check(result, "text-line-count-preserved").passed, false);
  assert.equal(check(result, "no-new-text-overflow-clipping").passed, false);
  assert.equal(result.layout.lineCountChanges[0].elementId, "multiline-title");
  assert.equal(result.layout.lineCountChanges[0].context.flexDirection, "column");
  assert.equal(result.layout.newOverflowClipping[0].elementId, "table-cell");
  assert.equal(
    result.layout.newOverflowClipping[0].context.verticalAlignment,
    "middle"
  );
});

test("OOXML, PowerPoint round-trip, center, bold, editability, and fallback violations are hard failures", () => {
  const candidateQuality = quality();
  candidateQuality.editableSlides = 19;
  candidateQuality.imageFallbackSlides = 1;
  candidateQuality.rasterFallbackElements = 57;
  const result = evaluateAuthoredHybridReleaseGates(
    completeEvidence({
      candidateQuality,
      candidatePackageHealth: packageHealth({
        ooxmlErrors: 1,
        missingRelationships: 1,
        duplicateNonVisualIds: 1,
      }),
      candidatePowerPointValidation: powerPointValidation({
        repairWarnings: 1,
        saveAsSucceeded: false,
        reopenedWithoutWarnings: false,
      }),
      candidateTextSemantics: semantics({
        centeredMisclassifications: 1,
        partialBoldLosses: 1,
      }),
    })
  );
  assert.equal(result.passed, false);
  for (const id of [
    "all-slides-editable",
    "no-full-slide-raster",
    "no-residual-fallback-increase",
    "no-ooxml-errors",
    "no-missing-relationships",
    "no-duplicate-nonvisual-ids",
    "no-powerpoint-repair-warnings",
    "powerpoint-save-as-succeeded",
    "powerpoint-reopen-without-warning",
    "no-center-alignment-loss",
    "no-partial-bold-loss",
  ]) {
    assert.equal(check(result, id).passed, false, id);
  }
});

test("required hard-gate reports fail closed when evidence is missing", () => {
  const result = evaluateAuthoredHybridReleaseGates({
    ...completeEvidence(),
    candidatePackageHealth: null,
    candidatePowerPointValidation: null,
    candidateTextSemantics: null,
  });
  assert.equal(result.passed, false);
  assert.equal(check(result, "package-health-evidence").passed, false);
  assert.equal(check(result, "powerpoint-validation-evidence").passed, false);
  assert.equal(check(result, "text-semantics-evidence").passed, false);
});
