import sharp from "sharp";

export const DEFAULT_REGRESSION_THRESHOLDS = Object.freeze({
  averageMaeIncrease: 0.02,
  averageBadPixelRatioIncrease: 0.0002,
  slideMaeIncrease: 0.1,
  slideBadPixelRatioIncrease: 0.001,
  materialAverageMaeDecrease: 0.1,
  materialAverageBadPixelRatioDecrease: 0.001,
});

function intersectArea(a, b) {
  const left = Math.max(a.x, b.x);
  const top = Math.max(a.y, b.y);
  const right = Math.min(a.x + a.width, b.x + b.width);
  const bottom = Math.min(a.y + a.height, b.y + b.height);
  return Math.max(0, right - left) * Math.max(0, bottom - top);
}

function classifyRegion(region) {
  const { width, height, occupancy, authoredEdgeDensity, editableEdgeDensity } =
    region;
  const longest = Math.max(width, height);
  const shortest = Math.min(width, height);
  const edgeDensity = Math.max(authoredEdgeDensity, editableEdgeDensity);
  if (shortest <= 5 && longest >= 20) return "likely-line-or-border";
  if (height <= 96 && width >= 6 && occupancy <= 0.58 && edgeDensity >= 0.05) {
    return "likely-text-or-glyph";
  }
  if (occupancy >= 0.58 || width * height >= 20_000) {
    return "likely-paint-or-geometry";
  }
  return "mixed-small-detail";
}

function edgeDensity(data, channels, width, height, bounds) {
  const left = Math.max(0, Math.floor(bounds.x));
  const top = Math.max(0, Math.floor(bounds.y));
  const right = Math.min(width - 1, Math.ceil(bounds.x + bounds.width));
  const bottom = Math.min(height - 1, Math.ceil(bounds.y + bounds.height));
  const stride = Math.max(1, Math.floor(Math.max(right - left, bottom - top) / 160));
  let samples = 0;
  let edges = 0;
  for (let y = top; y < bottom; y += stride) {
    for (let x = left; x < right; x += stride) {
      const offset = (y * width + x) * channels;
      const rightOffset = offset + channels;
      const downOffset = offset + width * channels;
      const lum =
        data[offset] * 0.299 +
        data[offset + 1] * 0.587 +
        data[offset + 2] * 0.114;
      const rightLum =
        data[rightOffset] * 0.299 +
        data[rightOffset + 1] * 0.587 +
        data[rightOffset + 2] * 0.114;
      const downLum =
        data[downOffset] * 0.299 +
        data[downOffset + 1] * 0.587 +
        data[downOffset + 2] * 0.114;
      if (Math.max(Math.abs(lum - rightLum), Math.abs(lum - downLum)) > 24) {
        edges += 1;
      }
      samples += 1;
    }
  }
  return samples ? edges / samples : 0;
}

function normalizeElement(element) {
  const bounds = element.bounds?.px ?? element.bounds;
  if (
    !bounds ||
    ![bounds.x, bounds.y, bounds.width, bounds.height].every(Number.isFinite)
  ) {
    return null;
  }
  return {
    id: element.id,
    kind:
      element.kind ??
      element.classification?.kind ??
      element.classification?.candidateKind ??
      "unknown",
    mode: element.mode ?? element.classification?.mode ?? "unknown",
    reasons: element.reasons ?? element.classification?.reasons ?? [],
    bounds,
    font:
      element.font ??
      (element.text?.style
        ? {
            family: element.text.style.fontFamily,
            sizePt: element.text.style.fontSizePt,
            lineHeight: element.text.style.lineHeight,
            letterSpacingPt: element.text.style.letterSpacingPt,
          }
        : undefined),
  };
}

function matchElements(region, elements) {
  const regionArea = region.width * region.height;
  return elements
    .map(normalizeElement)
    .filter(Boolean)
    .map((element) => {
      const overlap = intersectArea(region, element.bounds);
      const elementArea = element.bounds.width * element.bounds.height;
      const overlapRatio = overlap / Math.max(1, regionArea);
      const elementCoverage = overlap / Math.max(1, elementArea);
      return {
        ...element,
        overlapRatio,
        elementCoverage,
        matchScore: overlapRatio * 0.7 + elementCoverage * 0.3,
      };
    })
    .filter((element) => element.overlapRatio > 0)
    .sort(
      (left, right) =>
        right.matchScore - left.matchScore ||
        left.bounds.width * left.bounds.height -
          right.bounds.width * right.bounds.height
    )
    .slice(0, 5);
}

export async function analyzeDiffRegions(
  sourcePng,
  exportedPng,
  {
    width = 1280,
    height = 720,
    pixelDelta = 32,
    maxRegions = 12,
    elements = [],
  } = {}
) {
  const [source, exported] = await Promise.all([
    sharp(sourcePng)
      .flatten({ background: "#ffffff" })
      .ensureAlpha()
      .raw()
      .toBuffer({ resolveWithObject: true }),
    sharp(exportedPng)
      .flatten({ background: "#ffffff" })
      .ensureAlpha()
      .raw()
      .toBuffer({ resolveWithObject: true }),
  ]);
  if (
    source.info.width !== width ||
    source.info.height !== height ||
    exported.info.width !== width ||
    exported.info.height !== height
  ) {
    throw new Error(`Expected ${width}x${height} images for region analysis.`);
  }
  const pixels = width * height;
  const badMask = new Uint8Array(pixels);
  const deltas = new Uint8Array(pixels);
  for (let pixel = 0; pixel < pixels; pixel += 1) {
    const offset = pixel * 4;
    const delta = Math.max(
      Math.abs(source.data[offset] - exported.data[offset]),
      Math.abs(source.data[offset + 1] - exported.data[offset + 1]),
      Math.abs(source.data[offset + 2] - exported.data[offset + 2])
    );
    deltas[pixel] = delta;
    if (delta > pixelDelta) badMask[pixel] = 1;
  }

  const seen = new Uint8Array(pixels);
  const queue = new Uint32Array(pixels);
  const regions = [];
  for (let start = 0; start < pixels; start += 1) {
    if (!badMask[start] || seen[start]) continue;
    let head = 0;
    let tail = 0;
    let count = 0;
    let totalDelta = 0;
    let minX = width;
    let minY = height;
    let maxX = 0;
    let maxY = 0;
    seen[start] = 1;
    queue[tail++] = start;
    while (head < tail) {
      const pixel = queue[head++];
      const x = pixel % width;
      const y = Math.floor(pixel / width);
      count += 1;
      totalDelta += deltas[pixel];
      minX = Math.min(minX, x);
      minY = Math.min(minY, y);
      maxX = Math.max(maxX, x);
      maxY = Math.max(maxY, y);
      const neighbors = [
        x > 0 ? pixel - 1 : -1,
        x + 1 < width ? pixel + 1 : -1,
        y > 0 ? pixel - width : -1,
        y + 1 < height ? pixel + width : -1,
      ];
      for (const neighbor of neighbors) {
        if (neighbor >= 0 && badMask[neighbor] && !seen[neighbor]) {
          seen[neighbor] = 1;
          queue[tail++] = neighbor;
        }
      }
    }
    const bounds = {
      x: minX,
      y: minY,
      width: maxX - minX + 1,
      height: maxY - minY + 1,
    };
    regions.push({
      ...bounds,
      badPixels: count,
      occupancy: count / Math.max(1, bounds.width * bounds.height),
      meanMaxChannelDelta: totalDelta / count,
    });
  }

  return regions
    .sort((left, right) => right.badPixels - left.badPixels)
    .slice(0, maxRegions)
    .map((region) => {
      const enriched = {
        ...region,
        authoredEdgeDensity: edgeDensity(
          source.data,
          4,
          width,
          height,
          region
        ),
        editableEdgeDensity: edgeDensity(
          exported.data,
          4,
          width,
          height,
          region
        ),
      };
      return {
        ...enriched,
        heuristic: classifyRegion(enriched),
        matchedElements: matchElements(enriched, elements),
      };
    });
}

export function compareAgainstBaseline(
  currentSummary,
  currentSlides,
  baselineMetrics,
  thresholds = DEFAULT_REGRESSION_THRESHOLDS
) {
  if (!baselineMetrics) return { available: false };
  const baselineSummary = baselineMetrics.summary?.authoredVsEditable;
  const baselineBySlide = new Map(
    (baselineMetrics.slides ?? []).map((slide) => [
      slide.slideNumber,
      slide.authoredVsEditable,
    ])
  );
  const maeDelta =
    currentSummary.meanAbsoluteErrorAverage -
    baselineSummary.meanAbsoluteErrorAverage;
  const badPixelRatioDelta =
    currentSummary.badPixelRatioAverage - baselineSummary.badPixelRatioAverage;
  const slides = currentSlides.map((slide) => {
    const baseline = baselineBySlide.get(slide.slideNumber);
    if (!baseline) return { slideNumber: slide.slideNumber, available: false };
    const slideMaeDelta =
      slide.authoredVsEditable.meanAbsoluteError - baseline.meanAbsoluteError;
    const slideBadDelta =
      slide.authoredVsEditable.badPixelRatio - baseline.badPixelRatio;
    return {
      slideNumber: slide.slideNumber,
      available: true,
      meanAbsoluteErrorDelta: slideMaeDelta,
      badPixelRatioDelta: slideBadDelta,
      classification:
        slideMaeDelta > thresholds.slideMaeIncrease ||
        slideBadDelta > thresholds.slideBadPixelRatioIncrease
          ? "regressed"
          : slideMaeDelta < -thresholds.slideMaeIncrease ||
              slideBadDelta < -thresholds.slideBadPixelRatioIncrease
            ? "improved"
            : "neutral",
    };
  });
  const regressed =
    maeDelta > thresholds.averageMaeIncrease ||
    badPixelRatioDelta > thresholds.averageBadPixelRatioIncrease;
  const materiallyImproved =
    maeDelta < -thresholds.materialAverageMaeDecrease ||
    badPixelRatioDelta < -thresholds.materialAverageBadPixelRatioDecrease;
  return {
    available: true,
    baselineMetrics: baselineMetrics.__path,
    thresholds,
    meanAbsoluteErrorDelta: maeDelta,
    badPixelRatioDelta,
    classification: regressed
      ? "regressed"
      : materiallyImproved
        ? "improved"
        : "neutral",
    regressedSlides: slides
      .filter((slide) => slide.classification === "regressed")
      .map((slide) => slide.slideNumber),
    improvedSlides: slides
      .filter((slide) => slide.classification === "improved")
      .map((slide) => slide.slideNumber),
    slides,
  };
}

export function summarizeFallbackQuality(quality, pageCount) {
  if (!quality) return { available: false };
  const perSlide = new Map(
    (quality.slides ?? []).map((slide) => [slide.slideNumber, slide])
  );
  const reasonFrequency = {};
  const slides = Array.from({ length: pageCount }, (_, index) => {
    const slideNumber = index + 1;
    const slide = perSlide.get(slideNumber) ?? {};
    const reasons = slide.fallbackReasons ?? [];
    for (const reason of reasons) {
      reasonFrequency[reason] = (reasonFrequency[reason] ?? 0) + 1;
    }
    return {
      slideNumber,
      rasterFallbackElements: slide.rasterFallbackElements ?? 0,
      fallbackReasons: reasons,
    };
  });
  return {
    available: true,
    totalRasterFallbackElements: slides.reduce(
      (sum, slide) => sum + slide.rasterFallbackElements,
      0
    ),
    reasonFrequency: Object.fromEntries(
      Object.entries(reasonFrequency).sort((left, right) => right[1] - left[1])
    ),
    slides,
  };
}
