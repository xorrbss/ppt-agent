const EPSILON = 1e-9;

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

function add(checks, id, passed, actual, expected) {
  checks.push({ id, passed, actual, expected });
}

/**
 * Evaluates the authored-hybrid release contract without depending on a
 * renderer. Rendering remains the responsibility of qa-authored-hybrid-visual;
 * this function makes the structural and per-slide fidelity gate explicit.
 */
export function evaluateAuthoredHybridReleaseGates({
  baselineQuality,
  candidateQuality,
  baselineVisual,
  candidateVisual,
  baselineElementMap,
  candidateElementMap,
  expectedSlides = 20,
  focusSlides = [5, 8, 12, 18],
}) {
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
    const passed = present && maeDelta <= EPSILON && badDelta <= EPSILON;
    if (!passed) regressedSlides.push(slideNumber);
    if (focusSlides.includes(slideNumber) && present && badDelta < -EPSILON) {
      focusImproved.push(slideNumber);
    }
    add(
      checks,
      `slide-${slideNumber}-no-visual-regression`,
      passed,
      present ? { maeDelta, badPixelRatioDelta: badDelta } : "missing metrics",
      "MAE <= 0 and bad-pixel ratio <= 0 versus baseline"
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

  return {
    schema: "presenton.authored-hybrid-release-gates/v1",
    passed: checks.every((check) => check.passed),
    expectedSlides,
    focusSlides,
    focusBadPixelImprovedSlides: focusImproved,
    regressedSlides,
    checks,
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
