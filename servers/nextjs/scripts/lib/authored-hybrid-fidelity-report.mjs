export const DEFAULT_SEMANTIC_THRESHOLDS = Object.freeze({
  bboxMovementPx: 1,
  allowedLineCountChanges: 0,
  allowedNewClipping: 0,
});

function finiteNumber(value, fallback = 0) {
  return Number.isFinite(value) ? value : fallback;
}

function asSlides(document) {
  return Array.isArray(document?.slides) ? document.slides : [];
}

function valueFrom(element, path) {
  return path.split(".").reduce((value, key) => value?.[key], element);
}

function textLayout(element) {
  return (
    valueFrom(element, "powerPoint.text.layout") ??
    valueFrom(element, "text.layout") ??
    {}
  );
}

function renderedBounds(element) {
  return valueFrom(element, "powerPoint.bounds") ?? element.bounds ?? null;
}

function isText(element) {
  return element?.kind === "text" || element?.classification?.kind === "text";
}

function isNativeText(element) {
  return isText(element) && (element.mode ?? element.classification?.mode) === "native";
}

function hasClipping(element) {
  const reasons = element?.reasons ?? element?.classification?.reasons ?? [];
  const layout = textLayout(element);
  return (
    reasons.includes("overflow-clipped") ||
    layout.overflow === "hidden" ||
    layout.clipped === true ||
    layout.hasOverflow === true
  );
}

function mapElements(elementMap) {
  const values = new Map();
  const slides = asSlides(elementMap);
  // element-map uses source slide indexes (0..19), while visual/quality reports
  // use human slide numbers (1..20). Normalize only when a zero index is present.
  const offset = slides.some((slide) => slide.slideNumber === 0) ? 1 : 0;
  for (const slide of slides) {
    for (const element of slide.elements ?? []) {
      const slideNumber = slide.slideNumber + offset;
      if (element?.id) values.set(`${slideNumber}:${element.id}`, { ...element, slideNumber });
    }
  }
  return values;
}

function slideNumberSet(...documents) {
  const numbers = new Set();
  for (const document of documents) {
    for (const slide of asSlides(document)) numbers.add(slide.slideNumber);
  }
  return [...numbers].filter(Number.isInteger).sort((left, right) => left - right);
}

export function compareElementMapSemantics(
  baselineElementMap,
  candidateElementMap,
  thresholds = DEFAULT_SEMANTIC_THRESHOLDS
) {
  if (!baselineElementMap || !candidateElementMap) return { available: false };
  const baseline = mapElements(baselineElementMap);
  const candidate = mapElements(candidateElementMap);
  const bySlide = new Map();
  const getSlide = (slideNumber) => {
    if (!bySlide.has(slideNumber)) {
      bySlide.set(slideNumber, {
        slideNumber,
        matchedText: 0,
        missingNativeText: 0,
        rasterizedText: 0,
        maxBBoxMovementPx: 0,
        bboxViolations: 0,
        lineCountChanges: 0,
        newClipping: 0,
        newClippingIds: [],
      });
    }
    return bySlide.get(slideNumber);
  };
  const details = [];
  for (const [key, baselineElement] of baseline) {
    // element-map's `mode` is a DOM-paint classification, not the exported
    // OOXML fallback decision. Quality JSON is authoritative for native/fallback
    // counts; geometry must cover every meaningful text element here.
    if (!isText(baselineElement)) continue;
    const slide = getSlide(baselineElement.slideNumber);
    const candidateElement = candidate.get(key);
    if (!candidateElement) {
      slide.missingNativeText += 1;
      details.push({ key, slideNumber: baselineElement.slideNumber, classification: "missing-text" });
      continue;
    }
    slide.matchedText += 1;
    const before = renderedBounds(baselineElement);
    const after = renderedBounds(candidateElement);
    let movement = 0;
    if (before && after) {
      movement = Math.max(
        Math.abs(finiteNumber(after.x) - finiteNumber(before.x)),
        Math.abs(finiteNumber(after.y) - finiteNumber(before.y)),
        Math.abs(finiteNumber(after.width) - finiteNumber(before.width)),
        Math.abs(finiteNumber(after.height) - finiteNumber(before.height))
      );
      slide.maxBBoxMovementPx = Math.max(slide.maxBBoxMovementPx, movement);
      if (movement > thresholds.bboxMovementPx) slide.bboxViolations += 1;
    }
    const beforeLines = textLayout(baselineElement).lineCount;
    const afterLines = textLayout(candidateElement).lineCount;
    const lineCountChanged = Number.isInteger(beforeLines) && Number.isInteger(afterLines) && beforeLines !== afterLines;
    if (lineCountChanged) slide.lineCountChanges += 1;
    const newlyClipped = !hasClipping(baselineElement) && hasClipping(candidateElement);
    if (newlyClipped) {
      slide.newClipping += 1;
      slide.newClippingIds.push(candidateElement.id);
    }
    if (movement > thresholds.bboxMovementPx || lineCountChanged || newlyClipped) {
      details.push({ key, slideNumber: baselineElement.slideNumber, movement, beforeLines, afterLines, newlyClipped });
    }
  }
  for (const candidateElement of candidate.values()) {
    if (hasClipping(candidateElement) && !baseline.has(`${candidateElement.slideNumber}:${candidateElement.id}`)) {
      const slide = getSlide(candidateElement.slideNumber);
      slide.newClipping += 1;
      slide.newClippingIds.push(candidateElement.id);
    }
  }
  const slides = [...bySlide.values()].sort((left, right) => left.slideNumber - right.slideNumber);
  const summary = slides.reduce(
    (total, slide) => ({
      matchedText: total.matchedText + slide.matchedText,
      missingNativeText: total.missingNativeText + slide.missingNativeText,
      rasterizedText: total.rasterizedText + slide.rasterizedText,
      maxBBoxMovementPx: Math.max(total.maxBBoxMovementPx, slide.maxBBoxMovementPx),
      bboxViolations: total.bboxViolations + slide.bboxViolations,
      lineCountChanges: total.lineCountChanges + slide.lineCountChanges,
      newClipping: total.newClipping + slide.newClipping,
    }),
    { matchedText: 0, missingNativeText: 0, rasterizedText: 0, maxBBoxMovementPx: 0, bboxViolations: 0, lineCountChanges: 0, newClipping: 0 }
  );
  return {
    available: true,
    thresholds,
    passed:
      summary.missingNativeText === 0 &&
      summary.rasterizedText === 0 &&
      summary.bboxViolations === 0 &&
      summary.lineCountChanges <= thresholds.allowedLineCountChanges &&
      summary.newClipping <= thresholds.allowedNewClipping,
    summary,
    slides,
    details,
  };
}

export function summarizeQuality(quality, expectedSlides = 20) {
  if (!quality) return { available: false };
  const values = new Map(asSlides(quality).map((slide) => [slide.slideNumber, slide]));
  const slides = Array.from({ length: expectedSlides }, (_, index) => {
    const slide = values.get(index + 1) ?? {};
    return {
      slideNumber: index + 1,
      nativeTextElements: finiteNumber(slide.nativeTextElements),
      nativeShapeElements: finiteNumber(slide.nativeShapeElements),
      rasterFallbackElements: finiteNumber(slide.rasterFallbackElements),
    };
  });
  return {
    available: true,
    nativeTextElements: finiteNumber(quality.nativeTextElements),
    nativeShapeElements: finiteNumber(quality.nativeShapeElements),
    rasterFallbackElements: finiteNumber(quality.rasterFallbackElements),
    fullSlideRaster: finiteNumber(quality.imageFallbackSlides),
    editableSlides: finiteNumber(quality.editableSlides),
    slides,
  };
}

export function buildFidelityQaSummary({
  candidateQuality,
  candidateVisual,
  candidateElementMap,
  baselineQuality = null,
  baselineVisual = null,
  baselineElementMap = null,
  semantics = null,
  releaseGates = null,
  libreOffice = null,
  powerPoint = null,
  expectedSlides = 20,
}) {
  const quality = summarizeQuality(candidateQuality, expectedSlides);
  const baseline = summarizeQuality(baselineQuality, expectedSlides);
  const geometry = compareElementMapSemantics(baselineElementMap, candidateElementMap);
  const visualBySlide = new Map(asSlides(candidateVisual).map((slide) => [slide.slideNumber, slide]));
  const baselineVisualBySlide = new Map(asSlides(baselineVisual).map((slide) => [slide.slideNumber, slide]));
  const qualityBySlide = new Map(quality.slides?.map((slide) => [slide.slideNumber, slide]));
  const geometryBySlide = new Map(geometry.slides?.map((slide) => [slide.slideNumber, slide]));
  const semanticBySlide = new Map(asSlides(semantics).map((slide) => [slide.slideNumber, slide]));
  const slides = Array.from({ length: expectedSlides }, (_, index) => {
    const slideNumber = index + 1;
    const current = visualBySlide.get(slideNumber)?.authoredVsEditable;
    const previous = baselineVisualBySlide.get(slideNumber)?.authoredVsEditable;
    const maeDelta = current && previous ? current.meanAbsoluteError - previous.meanAbsoluteError : null;
    const badPixelRatioDelta = current && previous ? current.badPixelRatio - previous.badPixelRatio : null;
    return {
      slideNumber,
      visual: current ?? null,
      meanAbsoluteErrorDelta: maeDelta,
      badPixelRatioDelta,
      quality: qualityBySlide.get(slideNumber) ?? null,
      geometry: geometryBySlide.get(slideNumber) ?? null,
      semantics: semanticBySlide.get(slideNumber) ?? null,
    };
  });
  const semanticSummary = semantics?.summary ?? { available: false };
  const hardFailures = [
    ...(releaseGates?.failedHardGates ?? []),
    ...(quality.fullSlideRaster > 0 ? ["full-slide-raster"] : []),
    ...(baseline.available && quality.nativeTextElements < baseline.nativeTextElements ? ["native-text-count-decreased"] : []),
    ...(baseline.available && quality.rasterFallbackElements > baseline.rasterFallbackElements ? ["fallback-count-increased"] : []),
    ...(semanticSummary.centeredMisclassifications > 0 ? ["center-alignment-loss"] : []),
    ...(semanticSummary.partialBoldLosses > 0 ? ["partial-bold-loss"] : []),
    ...(geometry.available && !geometry.passed ? ["text-geometry-regression"] : []),
    ...(releaseGates?.passed === false ? ["release-gates-failed"] : []),
  ];
  return {
    schema: "presenton.authored-hybrid-fidelity-qa/v1",
    expectedSlides,
    aggregate: {
      visual: candidateVisual?.summary?.authoredVsEditable ?? null,
      baselineVisual: baselineVisual?.summary?.authoredVsEditable ?? null,
      quality,
      baselineQuality: baseline,
      semantics: semanticSummary,
      geometry,
      releaseGates: releaseGates ?? { available: false },
      compatibility: {
        powerPoint: powerPoint ?? { available: false, status: "not-run" },
        libreOffice: libreOffice ?? { available: false, status: "not-run" },
      },
      hardFailures: [...new Set(hardFailures)],
      passed: hardFailures.length === 0 && releaseGates?.passed !== false,
    },
    slides,
  };
}

function number(value, digits = 4) {
  return Number.isFinite(value) ? value.toFixed(digits) : "—";
}

export function renderFidelityQaMarkdown(summary, paths = {}) {
  const aggregate = summary.aggregate;
  const visual = aggregate.visual ?? {};
  const baseline = aggregate.baselineVisual ?? {};
  const lines = [
    "# Authored Hybrid Fidelity QA",
    "",
    `Result: **${aggregate.passed ? "PASS" : "FAIL"}**`,
    "",
    "## Aggregate",
    "",
    `- Visual MAE: ${number(visual.meanAbsoluteErrorAverage, 6)} (baseline ${number(baseline.meanAbsoluteErrorAverage, 6)})`,
    `- Bad-pixel ratio: ${number(visual.badPixelRatioAverage, 6)} (baseline ${number(baseline.badPixelRatioAverage, 6)})`,
    `- Native text / shapes / fallback: ${aggregate.quality.nativeTextElements} / ${aggregate.quality.nativeShapeElements} / ${aggregate.quality.rasterFallbackElements}`,
    `- Text bbox max movement / line-count changes / new clipping: ${number(aggregate.geometry.summary?.maxBBoxMovementPx, 2)}px / ${aggregate.geometry.summary?.lineCountChanges ?? "—"} / ${aggregate.geometry.summary?.newClipping ?? "—"}`,
    `- Centered losses / partial-bold losses: ${aggregate.semantics.centeredMisclassifications ?? "—"} / ${aggregate.semantics.partialBoldLosses ?? "—"}`,
    `- PowerPoint Desktop: ${aggregate.compatibility.powerPoint.status ?? "not-run"}; LibreOffice: ${aggregate.compatibility.libreOffice.status ?? "not-run"}`,
    `- Hard failures: ${aggregate.hardFailures.length ? aggregate.hardFailures.join(", ") : "none"}`,
    "",
    "## Per-slide",
    "",
    "| Slide | MAE Δ | Bad-pixel Δ | Native text | Shapes | Fallback | BBox max px | Lines Δ | New clip |",
    "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
  ];
  for (const slide of summary.slides) {
    lines.push(`| ${slide.slideNumber} | ${number(slide.meanAbsoluteErrorDelta, 4)} | ${number(slide.badPixelRatioDelta, 6)} | ${slide.quality?.nativeTextElements ?? "—"} | ${slide.quality?.nativeShapeElements ?? "—"} | ${slide.quality?.rasterFallbackElements ?? "—"} | ${number(slide.geometry?.maxBBoxMovementPx, 2)} | ${slide.geometry?.lineCountChanges ?? "—"} | ${slide.geometry?.newClipping ?? "—"} |`);
  }
  const presentPaths = Object.entries(paths).filter(([, value]) => value);
  if (presentPaths.length) {
    lines.push("", "## Artifacts", "");
    for (const [label, value] of presentPaths) lines.push(`- ${label}: \`${value}\``);
  }
  return `${lines.join("\n")}\n`;
}
