const EPSILON = 1e-9;

export const DEFAULT_AUTHORED_HYBRID_RELEASE_THRESHOLDS = Object.freeze({
  slideMaeIncrease: 0.1,
  slideBadPixelRatioIncrease: 0.001,
  textBboxMovementPx: 1,
  lineCountDelta: 0,
  newOverflowClipping: 0,
});

function number(value, fallback = 0) {
  return Number.isFinite(value) ? value : fallback;
}

function visualSummary(metrics) {
  return metrics?.summary?.authoredVsEditable ?? {};
}

function slidesByNumber(metrics) {
  return new Map(
    (metrics?.slides ?? []).map((slide) => [slide.slideNumber, slide])
  );
}

function add(checks, id, passed, actual, expected, details) {
  checks.push({
    id,
    passed,
    actual,
    expected,
    ...(details === undefined ? {} : { details }),
  });
}

function thresholdsWithDefaults(overrides) {
  return Object.fromEntries(
    Object.entries(DEFAULT_AUTHORED_HYBRID_RELEASE_THRESHOLDS).map(
      ([key, fallback]) => {
        const value = overrides?.[key];
        return [
          key,
          Number.isFinite(value) && value >= 0 ? value : fallback,
        ];
      }
    )
  );
}

function nestedValuesForKeys(root, keys) {
  const matches = [];
  const seen = new Set();
  const wanted = new Set(keys);
  function visit(value) {
    if (!value || typeof value !== "object" || seen.has(value)) return;
    seen.add(value);
    for (const [key, child] of Object.entries(value)) {
      if (wanted.has(key)) matches.push(child);
      visit(child);
    }
  }
  visit(root);
  return matches;
}

function violationMetric(report, keys) {
  const values = nestedValuesForKeys(report, keys);
  return {
    available: values.length > 0,
    count: values.reduce((maximum, value) => {
      const count = Array.isArray(value)
        ? value.length
        : typeof value === "boolean"
          ? Number(value)
          : number(value);
      return Math.max(maximum, count);
    }, 0),
  };
}

function booleanMetric(report, keys) {
  const values = nestedValuesForKeys(report, keys).filter(
    (value) => typeof value === "boolean"
  );
  return {
    available: values.length > 0,
    passed: values.length > 0 && values.every(Boolean),
  };
}

function summaryMetric(report, key) {
  const value = report?.summary?.[key];
  return { available: Number.isFinite(value), value: number(value) };
}

function addRequiredZeroMetric(checks, id, report, keys) {
  const metric = violationMetric(report, keys);
  add(
    checks,
    id,
    metric.available && metric.count === 0,
    metric.available ? metric.count : "missing evidence",
    0
  );
}

function renderedText(element) {
  return element?.powerPoint?.text ?? element?.text;
}

function renderedBounds(element) {
  const text = renderedText(element);
  return (
    text?.layout?.paintedTextBounds ??
    text?.layout?.contentBounds ??
    text?.layout?.boxBounds ??
    element?.powerPoint?.bounds ??
    element?.bounds
  );
}

function validBounds(bounds) {
  return (
    bounds &&
    [bounds.x, bounds.y, bounds.width, bounds.height].every(Number.isFinite)
  );
}

function textContext(element) {
  const text = renderedText(element);
  return {
    kind: element?.kind,
    profile: text?.profile,
    role: text?.role,
    runCount: text?.runCount,
    horizontalAlignment: text?.font?.horizontalAlignment,
    verticalAlignment: text?.font?.verticalAlignment,
    display: text?.layout?.display,
    flexDirection: text?.layout?.flexDirection,
    paddingPx: text?.layout?.paddingPx,
    singleLine: text?.layout?.singleLine,
  };
}

function hasOverflowOrClipping(element) {
  const text = renderedText(element);
  const layout = text?.layout ?? {};
  const explicit = [
    layout.overflow,
    layout.overflowed,
    layout.hasOverflow,
    layout.clipped,
    layout.hasClipping,
  ].some((value) => value === true);
  const reasons = [
    ...(element?.reasons ?? []),
    ...(text?.reasons ?? []),
    ...(layout.reasons ?? []),
  ];
  return (
    explicit ||
    reasons.some((reason) => /(?:overflow|clipp?ed|clipping)/i.test(reason))
  );
}

function textElementsByKey(elementMap) {
  const output = new Map();
  for (const slide of elementMap?.slides ?? []) {
    for (const element of slide.elements ?? []) {
      if (!renderedText(element)) continue;
      output.set(`${slide.slideNumber}:${element.id}`, {
        slideNumber: slide.slideNumber,
        element,
      });
    }
  }
  return output;
}

function compareTextLayout(baselineMap, candidateMap, thresholds) {
  const baseline = textElementsByKey(baselineMap);
  const candidate = textElementsByKey(candidateMap);
  const bboxViolations = [];
  const lineCountChanges = [];
  const newOverflowClipping = [];
  let matchedTextElements = 0;
  let bboxCompared = 0;
  let lineCountCompared = 0;
  let maxBboxMovementPx = 0;

  for (const [key, candidateEntry] of candidate) {
    const baselineEntry = baseline.get(key);
    const candidateElement = candidateEntry.element;
    const baselineElement = baselineEntry?.element;
    if (!baselineEntry) {
      if (hasOverflowOrClipping(candidateElement)) {
        newOverflowClipping.push({
          slideNumber: candidateEntry.slideNumber,
          elementId: candidateElement.id,
          context: textContext(candidateElement),
        });
      }
      continue;
    }
    matchedTextElements += 1;

    const baselineBounds = renderedBounds(baselineElement);
    const candidateBounds = renderedBounds(candidateElement);
    if (validBounds(baselineBounds) && validBounds(candidateBounds)) {
      bboxCompared += 1;
      const delta = Object.fromEntries(
        ["x", "y", "width", "height"].map((field) => [
          field,
          candidateBounds[field] - baselineBounds[field],
        ])
      );
      const movementPx = Math.max(
        ...Object.values(delta).map((value) => Math.abs(value))
      );
      maxBboxMovementPx = Math.max(maxBboxMovementPx, movementPx);
      if (movementPx > thresholds.textBboxMovementPx + EPSILON) {
        bboxViolations.push({
          slideNumber: candidateEntry.slideNumber,
          elementId: candidateElement.id,
          movementPx,
          delta,
          context: textContext(candidateElement),
        });
      }
    }

    const baselineLines = renderedText(baselineElement)?.layout?.lineCount;
    const candidateLines = renderedText(candidateElement)?.layout?.lineCount;
    if (Number.isFinite(baselineLines) && Number.isFinite(candidateLines)) {
      lineCountCompared += 1;
      const delta = candidateLines - baselineLines;
      if (Math.abs(delta) > thresholds.lineCountDelta + EPSILON) {
        lineCountChanges.push({
          slideNumber: candidateEntry.slideNumber,
          elementId: candidateElement.id,
          baseline: baselineLines,
          candidate: candidateLines,
          delta,
          context: textContext(candidateElement),
        });
      }
    }

    if (
      hasOverflowOrClipping(candidateElement) &&
      !hasOverflowOrClipping(baselineElement)
    ) {
      newOverflowClipping.push({
        slideNumber: candidateEntry.slideNumber,
        elementId: candidateElement.id,
        context: textContext(candidateElement),
      });
    }
  }

  return {
    matchedTextElements,
    bboxCompared,
    lineCountCompared,
    maxBboxMovementPx,
    bboxViolations,
    lineCountChanges,
    newOverflowClipping,
  };
}

function addPackageHealthChecks(checks, report, required) {
  if (!report && !required) return;
  add(
    checks,
    "package-health-evidence",
    Boolean(report),
    report ? "present" : "missing",
    "present"
  );
  if (!report) return;
  addRequiredZeroMetric(checks, "no-ooxml-errors", report, [
    "ooxmlErrors",
    "ooxmlErrorCount",
    "xmlErrors",
    "packageErrors",
  ]);
  addRequiredZeroMetric(checks, "no-missing-relationships", report, [
    "missingRelationships",
    "missingRelationshipCount",
    "danglingRelationships",
  ]);
  addRequiredZeroMetric(checks, "no-duplicate-nonvisual-ids", report, [
    "duplicateNonVisualIds",
    "duplicateNonVisualIdCount",
    "duplicateNvPrIds",
    "duplicateIds",
  ]);
}

function addPowerPointChecks(checks, report, required) {
  if (!report && !required) return;
  add(
    checks,
    "powerpoint-validation-evidence",
    Boolean(report),
    report ? "present" : "missing",
    "present"
  );
  if (!report) return;
  addRequiredZeroMetric(checks, "no-powerpoint-repair-warnings", report, [
    "repairWarnings",
    "repairWarningCount",
    "recoveryWarnings",
    "powerPointRepairWarnings",
  ]);
  for (const [id, aliases] of [
    [
      "powerpoint-open-without-warning",
      ["openedWithoutWarnings", "openWithoutWarnings"],
    ],
    ["powerpoint-save-as-succeeded", ["saveAsSucceeded", "savedAs"]],
    [
      "powerpoint-reopen-without-warning",
      ["reopenedWithoutWarnings", "reopenWithoutWarnings"],
    ],
  ]) {
    const metric = booleanMetric(report, aliases);
    add(
      checks,
      id,
      metric.available && metric.passed,
      metric.available ? metric.passed : "missing evidence",
      true
    );
  }
}

function addTextSemanticsChecks(checks, baseline, candidate, required) {
  if (!candidate && !required) return;
  add(
    checks,
    "text-semantics-evidence",
    Boolean(candidate),
    candidate ? "present" : "missing",
    "present"
  );
  if (!candidate) return;
  for (const [field, id] of [
    ["centeredMisclassifications", "no-center-alignment-loss"],
    ["partialBoldLosses", "no-partial-bold-loss"],
  ]) {
    const metric = summaryMetric(candidate, field);
    add(
      checks,
      id,
      metric.available && metric.value === 0,
      metric.available ? metric.value : "missing evidence",
      0
    );
  }
  if (!baseline) return;
  for (const field of ["nativeText", "centeredTargets", "partialBoldTargets"]) {
    const baselineMetric = summaryMetric(baseline, field);
    const candidateMetric = summaryMetric(candidate, field);
    add(
      checks,
      `no-${field}-semantic-target-decrease`,
      baselineMetric.available &&
        candidateMetric.available &&
        candidateMetric.value >= baselineMetric.value,
      candidateMetric.available ? candidateMetric.value : "missing evidence",
      baselineMetric.available
        ? `>= ${baselineMetric.value}`
        : "baseline evidence present"
    );
  }
}

/**
 * Evaluates renderer-independent release evidence produced by the authored
 * hybrid QA scripts. PowerPoint/OOXML reports remain external inputs so this
 * evaluator is deterministic and unit-testable.
 */
export function evaluateAuthoredHybridReleaseGates({
  baselineQuality,
  candidateQuality,
  baselineVisual,
  candidateVisual,
  baselineElementMap,
  candidateElementMap,
  baselineTextSemantics,
  candidateTextSemantics,
  candidatePackageHealth,
  candidatePowerPointValidation,
  expectedSlides = 20,
  focusSlides = [5, 8, 12, 18],
  thresholds: thresholdOverrides,
  requirePackageHealth = false,
  requirePowerPointValidation = false,
  requireTextSemantics = false,
  requireLayoutEvidence = false,
}) {
  const thresholds = thresholdsWithDefaults(thresholdOverrides);
  const checks = [];
  add(
    checks,
    "candidate-slide-count",
    candidateQuality?.totalSlides === expectedSlides &&
      candidateVisual?.pageCount === expectedSlides &&
      candidateElementMap?.slideCount === expectedSlides,
    {
      quality: candidateQuality?.totalSlides,
      visual: candidateVisual?.pageCount,
      elementMap: candidateElementMap?.slideCount,
    },
    expectedSlides
  );
  add(
    checks,
    "all-slides-editable",
    candidateQuality?.editableSlides === expectedSlides,
    candidateQuality?.editableSlides,
    expectedSlides
  );
  add(
    checks,
    "no-full-slide-raster",
    candidateQuality?.imageFallbackSlides === 0,
    candidateQuality?.imageFallbackSlides,
    0
  );

  for (const field of [
    "nativeTextElements",
    "nativeShapeElements",
    "nativeGroupElements",
    "nativeImageElements",
  ]) {
    add(
      checks,
      `no-${field}-decrease`,
      number(candidateQuality?.[field]) >= number(baselineQuality?.[field]),
      number(candidateQuality?.[field]),
      `>= ${number(baselineQuality?.[field])}`
    );
  }
  add(
    checks,
    "no-residual-fallback-increase",
    number(candidateQuality?.rasterFallbackElements) <=
      number(baselineQuality?.rasterFallbackElements),
    number(candidateQuality?.rasterFallbackElements),
    `<= ${number(baselineQuality?.rasterFallbackElements)}`
  );

  addPackageHealthChecks(checks, candidatePackageHealth, requirePackageHealth);
  addPowerPointChecks(
    checks,
    candidatePowerPointValidation,
    requirePowerPointValidation
  );
  addTextSemanticsChecks(
    checks,
    baselineTextSemantics,
    candidateTextSemantics,
    requireTextSemantics
  );

  const baselineSummary = visualSummary(baselineVisual);
  const candidateSummary = visualSummary(candidateVisual);
  add(
    checks,
    "mean-mae-no-regression",
    number(candidateSummary.meanAbsoluteErrorAverage) <=
      number(baselineSummary.meanAbsoluteErrorAverage) + EPSILON,
    number(candidateSummary.meanAbsoluteErrorAverage),
    `<= ${number(baselineSummary.meanAbsoluteErrorAverage)}`
  );
  add(
    checks,
    "mean-bad-pixel-no-regression",
    number(candidateSummary.badPixelRatioAverage) <=
      number(baselineSummary.badPixelRatioAverage) + EPSILON,
    number(candidateSummary.badPixelRatioAverage),
    `<= ${number(baselineSummary.badPixelRatioAverage)}`
  );

  const baselineSlides = slidesByNumber(baselineVisual);
  const candidateSlides = slidesByNumber(candidateVisual);
  const regressedSlides = [];
  const focusImproved = [];
  const slideClassifications = [];
  for (let slideNumber = 1; slideNumber <= expectedSlides; slideNumber += 1) {
    const baseline = baselineSlides.get(slideNumber)?.authoredVsEditable;
    const candidate = candidateSlides.get(slideNumber)?.authoredVsEditable;
    const present = Boolean(baseline && candidate);
    const maeDelta = present
      ? number(candidate.meanAbsoluteError) - number(baseline.meanAbsoluteError)
      : Number.POSITIVE_INFINITY;
    const badDelta = present
      ? number(candidate.badPixelRatio) - number(baseline.badPixelRatio)
      : Number.POSITIVE_INFINITY;
    const passed =
      present &&
      maeDelta <= thresholds.slideMaeIncrease + EPSILON &&
      badDelta <= thresholds.slideBadPixelRatioIncrease + EPSILON;
    if (!passed) regressedSlides.push(slideNumber);
    if (focusSlides.includes(slideNumber) && present && badDelta < -EPSILON) {
      focusImproved.push(slideNumber);
    }
    const classification = !present
      ? "missing"
      : !passed
        ? "regressed"
        : maeDelta > EPSILON || badDelta > EPSILON
          ? "noise"
          : maeDelta < -EPSILON || badDelta < -EPSILON
            ? "improved"
            : "unchanged";
    slideClassifications.push({
      slideNumber,
      classification,
      maeDelta,
      badPixelRatioDelta: badDelta,
    });
    add(
      checks,
      `slide-${slideNumber}-visual-tolerance`,
      passed,
      present ? { maeDelta, badPixelRatioDelta: badDelta } : "missing metrics",
      {
        maeDelta: `<= ${thresholds.slideMaeIncrease}`,
        badPixelRatioDelta: `<= ${thresholds.slideBadPixelRatioIncrease}`,
      }
    );
  }

  add(
    checks,
    "element-map-has-elements",
    number(candidateElementMap?.summary?.elementCount) > 0,
    candidateElementMap?.summary?.elementCount,
    "> 0"
  );
  if (baselineElementMap) {
    add(
      checks,
      "no-element-map-raster-increase",
      number(candidateElementMap?.summary?.rasterClassifiedElements) <=
        number(baselineElementMap?.summary?.rasterClassifiedElements),
      number(candidateElementMap?.summary?.rasterClassifiedElements),
      `<= ${number(baselineElementMap?.summary?.rasterClassifiedElements)}`
    );
  }

  const hasLayoutInputs =
    (baselineElementMap?.slides?.length ?? 0) > 0 &&
    (candidateElementMap?.slides?.length ?? 0) > 0;
  const layout =
    hasLayoutInputs
      ? compareTextLayout(baselineElementMap, candidateElementMap, thresholds)
      : null;
  if (layout || requireLayoutEvidence) {
    add(
      checks,
      "text-layout-evidence",
      Boolean(layout) && layout.matchedTextElements > 0,
      layout?.matchedTextElements ?? "missing",
      "> 0 matched text elements"
    );
    if (layout) {
      add(
        checks,
        "text-bbox-movement-within-tolerance",
        layout.bboxCompared > 0 && layout.bboxViolations.length === 0,
        {
          compared: layout.bboxCompared,
          maxMovementPx: layout.maxBboxMovementPx,
          violations: layout.bboxViolations.length,
        },
        `<= ${thresholds.textBboxMovementPx}px`,
        layout.bboxViolations
      );
      add(
        checks,
        "text-line-count-preserved",
        layout.lineCountCompared > 0 && layout.lineCountChanges.length === 0,
        {
          compared: layout.lineCountCompared,
          violations: layout.lineCountChanges.length,
        },
        `absolute delta <= ${thresholds.lineCountDelta}`,
        layout.lineCountChanges
      );
      add(
        checks,
        "no-new-text-overflow-clipping",
        layout.newOverflowClipping.length <= thresholds.newOverflowClipping,
        layout.newOverflowClipping.length,
        `<= ${thresholds.newOverflowClipping}`,
        layout.newOverflowClipping
      );
    }
  }

  return {
    schema: "presenton.authored-hybrid-release-gates/v2",
    passed: checks.every((check) => check.passed),
    expectedSlides,
    focusSlides,
    thresholds,
    focusBadPixelImprovedSlides: focusImproved,
    regressedSlides,
    slideClassifications,
    checks,
    layout,
    summary: {
      baseline: baselineSummary,
      candidate: candidateSummary,
      candidateNative: {
        text: number(candidateQuality?.nativeTextElements),
        shapes: number(candidateQuality?.nativeShapeElements),
        groups: number(candidateQuality?.nativeGroupElements),
        images: number(candidateQuality?.nativeImageElements),
        residualFallbacks: number(candidateQuality?.rasterFallbackElements),
      },
    },
  };
}
