export const AUTHORED_HYBRID_EXPORT_METADATA_VERSION = 1 as const;

export interface AuthoredHybridFallbackReasonSummary {
  reason: string;
  slideCount: number;
}

export interface AuthoredHybridExportMetadata {
  version: typeof AUTHORED_HYBRID_EXPORT_METADATA_VERSION;
  strategy: "authored-hybrid";
  totalSlides: number;
  editableSlides: number;
  imageFallbackSlides: number;
  fallbackReasons: AuthoredHybridFallbackReasonSummary[];
  /**
   * Additive, truthful element coverage. Older clients can keep consuming the
   * slide summary above; new clients must use this manifest before describing
   * a Hybrid file as editable.
   */
  coverageManifest?: AuthoredHybridCoverageManifest;
}

export const AUTHORED_HYBRID_COVERAGE_SCHEMA_VERSION =
  "presenton.authored-hybrid-coverage/v1" as const;

export interface AuthoredHybridCoverageSource {
  presentationRevision: number;
  sourceDigest: string;
}

export interface AuthoredHybridSlideCoverage {
  slideNumber: number;
  nativeText: number;
  nativeImage: number;
  nativeShape: number;
  /** Tables and charts remain zero until their OOXML serializers are real. */
  nativeTable: number;
  nativeChart: number;
  rasterRemaining: number;
  fallbackReasons: string[];
  /**
   * This records construction evidence, not a pixel-parity claim. Native paint
   * is removed from the fidelity backplate before the native layer is inserted.
   */
  visualParity: "fidelity-backplate-retained" | "not-verified";
  nativeBackplateDeDuplication: "applied" | "not-applicable";
}

export interface AuthoredHybridCoverageManifest {
  schemaVersion: typeof AUTHORED_HYBRID_COVERAGE_SCHEMA_VERSION;
  mode: "hybrid";
  source: AuthoredHybridCoverageSource;
  totalSlides: number;
  nativeText: number;
  nativeImage: number;
  nativeShape: number;
  nativeTable: number;
  nativeChart: number;
  rasterRemaining: number;
  /** False whenever even one raster element remains. */
  fullyEditable: boolean;
  slides: AuthoredHybridSlideCoverage[];
}

interface CoverageQualitySlide {
  slideNumber: number;
  editable: boolean;
  imageFallback: boolean;
  nativeTextElements: number;
  nativeImageElements: number;
  nativeShapeElements: number;
  rasterFallbackElements: number;
  fallbackReasons: readonly string[];
}

export interface AuthoredHybridCoverageQuality {
  slides: readonly CoverageQualitySlide[];
}

function nonNegativeInteger(value: number): number {
  return Number.isFinite(value) ? Math.max(0, Math.trunc(value)) : 0;
}

function normalizedReasons(values: readonly string[]): string[] {
  return [
    ...new Set(values.map((value) => value.trim()).filter(Boolean)),
  ].sort();
}

/** Build the persisted/API coverage contract from the same slide facts as QA. */
export function createAuthoredHybridCoverageManifest(
  quality: AuthoredHybridCoverageQuality,
  source: AuthoredHybridCoverageSource,
): AuthoredHybridCoverageManifest {
  const slides = quality.slides
    .map((slide) => {
      const nativeText = nonNegativeInteger(slide.nativeTextElements);
      const nativeImage = nonNegativeInteger(slide.nativeImageElements);
      const nativeShape = nonNegativeInteger(slide.nativeShapeElements);
      const nativeCount = nativeText + nativeImage + nativeShape;
      // An image-only fallback is one retained full-slide raster even when an
      // older quality producer omitted the aggregate element count.
      const rasterRemaining = Math.max(
        slide.imageFallback ? 1 : 0,
        nonNegativeInteger(slide.rasterFallbackElements),
      );
      return {
        slideNumber: Math.max(1, Math.trunc(slide.slideNumber)),
        nativeText,
        nativeImage,
        nativeShape,
        nativeTable: 0,
        nativeChart: 0,
        rasterRemaining,
        fallbackReasons: normalizedReasons(slide.fallbackReasons),
        // Construction removes native paint from the backplate, but only the
        // downstream PowerPoint render/readback gate may claim visual parity.
        visualParity: "not-verified" as const,
        nativeBackplateDeDuplication:
          nativeCount > 0 ? ("applied" as const) : ("not-applicable" as const),
      };
    })
    .sort((left, right) => left.slideNumber - right.slideNumber);
  const nativeText = slides.reduce((sum, slide) => sum + slide.nativeText, 0);
  const nativeImage = slides.reduce((sum, slide) => sum + slide.nativeImage, 0);
  const nativeShape = slides.reduce((sum, slide) => sum + slide.nativeShape, 0);
  const rasterRemaining = slides.reduce(
    (sum, slide) => sum + slide.rasterRemaining,
    0,
  );
  return {
    schemaVersion: AUTHORED_HYBRID_COVERAGE_SCHEMA_VERSION,
    mode: "hybrid",
    source: {
      presentationRevision: Math.max(
        0,
        Math.trunc(source.presentationRevision),
      ),
      sourceDigest: source.sourceDigest.toLowerCase(),
    },
    totalSlides: slides.length,
    nativeText,
    nativeImage,
    nativeShape,
    nativeTable: 0,
    nativeChart: 0,
    rasterRemaining,
    fullyEditable: slides.length > 0 && rasterRemaining === 0,
    slides,
  };
}

export interface AuthoredHybridSlideOutcome {
  editable: boolean;
  fallbackReason?: string;
}

/**
 * Keep export diagnostics small, deterministic, and safe to return to clients.
 * Only stable reason codes are accepted here; exception messages and authored
 * slide content must never cross this metadata boundary.
 */
export function summarizeAuthoredHybridExport(
  outcomes: readonly AuthoredHybridSlideOutcome[],
): AuthoredHybridExportMetadata {
  const reasonCounts = new Map<string, number>();
  let editableSlides = 0;

  for (const outcome of outcomes) {
    if (outcome.editable) {
      editableSlides += 1;
      continue;
    }
    const reason = outcome.fallbackReason?.trim() || "unknown";
    reasonCounts.set(reason, (reasonCounts.get(reason) ?? 0) + 1);
  }

  return {
    version: AUTHORED_HYBRID_EXPORT_METADATA_VERSION,
    strategy: "authored-hybrid",
    totalSlides: outcomes.length,
    editableSlides,
    imageFallbackSlides: outcomes.length - editableSlides,
    fallbackReasons: [...reasonCounts.entries()]
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([reason, slideCount]) => ({ reason, slideCount })),
  };
}

const FALLBACK_REASON_LABELS: Readonly<Record<string, string>> = {
  "chrome-unavailable": "Chrome unavailable",
  "deck-time-budget-exhausted": "conversion time limit",
  "hybrid-processing-failed": "hybrid processing failure",
  "invalid-slide": "invalid slide data",
  "missing-html": "missing slide HTML",
  "no-editable-elements": "no editable elements",
  "no-layer-safe-elements": "no safely editable elements",
  "promotion-mismatch": "layer safety mismatch",
  "slide-limit-exceeded": "hybrid slide limit",
  "slide-processing-failed": "slide processing failure",
};

function fallbackReasonLabel(reason: string): string {
  if (reason.startsWith("preflight-")) {
    return `security preflight (${reason.slice("preflight-".length)})`;
  }
  return FALLBACK_REASON_LABELS[reason] ?? reason;
}

/** Human-readable UI summary without changing the existing download contract. */
export function formatAuthoredHybridExportSummary(
  metadata: AuthoredHybridExportMetadata,
): string {
  const counts =
    `Native elements on ${metadata.editableSlides}/${metadata.totalSlides} slides; ` +
    `image-only ${metadata.imageFallbackSlides}.`;
  if (!metadata.fallbackReasons.length) return counts;
  const reasons = metadata.fallbackReasons
    .map(
      ({ reason, slideCount }) =>
        `${fallbackReasonLabel(reason)}: ${slideCount}`,
    )
    .join(", ");
  return `${counts} Reasons: ${reasons}.`;
}
