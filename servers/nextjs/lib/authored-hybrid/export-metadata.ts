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
  "presenton.authored-hybrid-coverage/v2" as const;

export interface AuthoredHybridCoverageSource {
  exportAttemptId: string;
  presentationRevision: number;
  canonicalSourceDigest: string;
  /** Digest of the exact persisted GET payload rendered by Next.js. */
  sourceDigest: string;
}

export interface AuthoredHybridCoverageCounts {
  nativeText: number;
  nativeImage: number;
  nativeShape: number;
  nativeTable: number;
  nativeChart: number;
  rasterRemaining: number;
}

export interface AuthoredHybridSlideCoverage {
  slideNumber: number;
  nativeText: number;
  nativeImage: number;
  nativeShape: number;
  /** Tables remain zero until their OOXML serializer is real. */
  nativeTable: number;
  nativeChart: number;
  logicalCoverage: AuthoredHybridCoverageCounts;
  ooxmlReadback: AuthoredHybridCoverageCounts & {
    status: "not-run" | "passed" | "failed";
  };
  backplateCount: number;
  typographyIssues: Array<{ code: string; elementId?: string }>;
  rasterRemaining: number;
  fallbackReasons: string[];
  /**
   * This records construction evidence, not a pixel-parity claim. Native paint
   * is removed from the fidelity backplate before the native layer is inserted.
   */
  visualParity: "preflight" | "passed" | "failed" | "not-verified";
  nativeBackplateDeDuplication: "applied" | "not-applicable";
  semanticReceipts?: {
    observationReceiptDigest?: string;
    nativeAdmissionReceiptDigest?: string;
    hybridPlanDigest?: string;
    rasterDegradationReceiptDigest?: string;
  };
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
  logicalCoverage: AuthoredHybridCoverageCounts;
  ooxmlReadback: AuthoredHybridCoverageCounts & {
    status: "not-run" | "passed" | "failed";
  };
  backplateCount: number;
  typographyIssues: Array<{
    slideNumber: number;
    code: string;
    elementId?: string;
  }>;
  rasterRemaining: number;
  /** False whenever even one raster element remains. */
  fullyEditable: boolean;
  visualParity: "preflight" | "passed" | "failed" | "not-verified";
  slides: AuthoredHybridSlideCoverage[];
}

interface CoverageQualitySlide {
  slideNumber: number;
  editable: boolean;
  imageFallback: boolean;
  nativeTextElements: number;
  nativeImageElements: number;
  nativeShapeElements: number;
  nativeChartElements?: number;
  rasterFallbackElements: number;
  logicalCoverage: {
    nativeText: number;
    nativeImage: number;
    nativeShape: number;
    nativeChart?: number;
    nativeGroup: number;
    rasterRemaining: number;
  };
  ooxmlReadback: {
    status: "not-run" | "passed" | "failed";
    nativeText: number;
    nativeImage: number;
    nativeShape: number;
    nativeChart?: number;
    nativeGroup: number;
    rasterRemaining: number;
  };
  backplateCount: number;
  typographyIssues: ReadonlyArray<{ code: string; elementId?: string }>;
  fallbackReasons: readonly string[];
  semanticReceipts?: AuthoredHybridSlideCoverage["semanticReceipts"];
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
      const nativeChart = nonNegativeInteger(slide.nativeChartElements ?? 0);
      const nativeCount = nativeText + nativeImage + nativeShape + nativeChart;
      // An image-only fallback is one retained full-slide raster even when an
      // older quality producer omitted the aggregate element count.
      const rasterRemaining = Math.max(
        slide.imageFallback ? 1 : 0,
        nonNegativeInteger(slide.logicalCoverage.rasterRemaining),
      );
      const logicalCoverage = {
        nativeText: nonNegativeInteger(slide.logicalCoverage.nativeText),
        nativeImage: nonNegativeInteger(slide.logicalCoverage.nativeImage),
        nativeShape: nonNegativeInteger(slide.logicalCoverage.nativeShape),
        nativeTable: 0,
        nativeChart: nonNegativeInteger(slide.logicalCoverage.nativeChart ?? nativeChart),
        rasterRemaining,
      };
      const ooxmlReadback = {
        status: slide.ooxmlReadback.status,
        nativeText: nonNegativeInteger(slide.ooxmlReadback.nativeText),
        nativeImage: nonNegativeInteger(slide.ooxmlReadback.nativeImage),
        nativeShape: nonNegativeInteger(slide.ooxmlReadback.nativeShape),
        nativeTable: 0,
        nativeChart: nonNegativeInteger(slide.ooxmlReadback.nativeChart ?? 0),
        rasterRemaining: nonNegativeInteger(
          slide.ooxmlReadback.rasterRemaining,
        ),
      };
      return {
        slideNumber: Math.max(1, Math.trunc(slide.slideNumber)),
        nativeText,
        nativeImage,
        nativeShape,
        nativeTable: 0,
        nativeChart,
        logicalCoverage,
        ooxmlReadback,
        backplateCount: nonNegativeInteger(slide.backplateCount),
        typographyIssues: slide.typographyIssues.map((issue) => ({ ...issue })),
        rasterRemaining,
        fallbackReasons: normalizedReasons(slide.fallbackReasons),
        // Construction removes native paint from the backplate, but only the
        // downstream PowerPoint render/readback gate may claim visual parity.
        visualParity: "not-verified" as const,
        nativeBackplateDeDuplication:
          nativeCount > 0 ? ("applied" as const) : ("not-applicable" as const),
        ...(slide.semanticReceipts
          ? { semanticReceipts: { ...slide.semanticReceipts } }
          : {}),
      };
    })
    .sort((left, right) => left.slideNumber - right.slideNumber);
  const nativeText = slides.reduce((sum, slide) => sum + slide.nativeText, 0);
  const nativeImage = slides.reduce((sum, slide) => sum + slide.nativeImage, 0);
  const nativeShape = slides.reduce((sum, slide) => sum + slide.nativeShape, 0);
  const nativeChart = slides.reduce((sum, slide) => sum + slide.nativeChart, 0);
  const rasterRemaining = slides.reduce(
    (sum, slide) => sum + slide.rasterRemaining,
    0,
  );
  const sumLogical = (field: keyof AuthoredHybridCoverageCounts) =>
    slides.reduce((sum, slide) => sum + slide.logicalCoverage[field], 0);
  const sumReadback = (field: keyof AuthoredHybridCoverageCounts) =>
    slides.reduce((sum, slide) => sum + slide.ooxmlReadback[field], 0);
  return {
    schemaVersion: AUTHORED_HYBRID_COVERAGE_SCHEMA_VERSION,
    mode: "hybrid",
    source: {
      exportAttemptId: source.exportAttemptId,
      presentationRevision: Math.max(
        0,
        Math.trunc(source.presentationRevision),
      ),
      canonicalSourceDigest: source.canonicalSourceDigest.toLowerCase(),
      sourceDigest: source.sourceDigest.toLowerCase(),
    },
    totalSlides: slides.length,
    nativeText,
    nativeImage,
    nativeShape,
    nativeTable: 0,
    nativeChart,
    logicalCoverage: {
      nativeText: sumLogical("nativeText"),
      nativeImage: sumLogical("nativeImage"),
      nativeShape: sumLogical("nativeShape"),
      nativeTable: 0,
      nativeChart: sumLogical("nativeChart"),
      rasterRemaining: sumLogical("rasterRemaining"),
    },
    ooxmlReadback: {
      status:
        slides.length > 0 &&
        slides.every((slide) => slide.ooxmlReadback.status === "passed")
          ? "passed"
          : slides.some((slide) => slide.ooxmlReadback.status === "failed")
            ? "failed"
            : "not-run",
      nativeText: sumReadback("nativeText"),
      nativeImage: sumReadback("nativeImage"),
      nativeShape: sumReadback("nativeShape"),
      nativeTable: 0,
      nativeChart: sumReadback("nativeChart"),
      rasterRemaining: sumReadback("rasterRemaining"),
    },
    backplateCount: slides.reduce(
      (sum, slide) => sum + slide.backplateCount,
      0,
    ),
    typographyIssues: slides.flatMap((slide) =>
      slide.typographyIssues.map((issue) => ({
        slideNumber: slide.slideNumber,
        ...issue,
      })),
    ),
    rasterRemaining,
    fullyEditable: slides.length > 0 && rasterRemaining === 0,
    visualParity: "not-verified",
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
