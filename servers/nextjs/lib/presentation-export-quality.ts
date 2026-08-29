export const PRESENTATION_EXPORT_QUALITY_SCHEMA_VERSION =
  "presenton.export-quality/v1" as const;

export type PresentationExportQualityStatus =
  | "fully-editable"
  | "partially-editable"
  | "image-only";

export type PresentationExportFallbackCandidateKind =
  | "text"
  | "image"
  | "shape"
  | "table"
  | "chart"
  | "complex"
  | "slide"
  | "unknown";

export interface PresentationExportFallbackElement {
  /** Stable DOM id when available; structural inspectors use an `ooxml:` id. */
  elementId: string;
  domPath?: string;
  candidateKind: PresentationExportFallbackCandidateKind;
  reasons: string[];
}

export interface PresentationExportRasterFallbackAudit {
  elementId: string;
  reason: string;
  /** True when this semantic owner is in the supported native denominator. */
  eligible: boolean;
  /** False means the exporter had to synthesize an unclassified reason. */
  explicit: boolean;
}

export interface PresentationExportNativeCoverage {
  status: "passed" | "blocked" | "not_assessed";
  eligibleElementCount: number;
  nativeElementCount: number;
  ratio?: number;
  rasterFallbacks: PresentationExportRasterFallbackAudit[];
}

export const PRESENTATION_EXPORT_MINIMUM_NATIVE_COVERAGE_RATIO = 0.7;

import type {
  PresentationExportFontEmbeddingStatus,
  PresentationExportFontRenderingQuality,
} from "./presentation-export-font-quality.ts";

export type {
  PresentationExportEmbeddedFontFaceStatus,
  PresentationExportFontEmbeddingFailure,
  PresentationExportFontEmbeddingStatus,
  PresentationExportFontRenderingQuality,
} from "./presentation-export-font-quality.ts";

export interface PresentationExportSlideQuality {
  slideNumber: number;
  editable: boolean;
  imageFallback: boolean;
  nativeTextElements: number;
  nativeShapeElements: number;
  nativeChartElements?: number;
  nativeTableElements?: number;
  nativeGroupElements?: number;
  nativeImageElements: number;
  rasterFallbackElements: number;
  /** Logical DOM candidates that remain raster; backplates are counted separately. */
  logicalCoverage: PresentationExportCoverageCounts;
  /** Native objects observed by reopening the assembled OOXML package. */
  ooxmlReadback: PresentationExportOoxmlReadback;
  backplateCount: number;
  typographyIssues: PresentationExportTypographyIssue[];
  fallbackReasons: string[];
  /**
   * Element-level detail is additive. Older exporters may only know aggregate
   * counts and slide-level reasons, so this list can be shorter than
   * `rasterFallbackElements`.
   */
  fallbackElements?: PresentationExportFallbackElement[];
  /** Digest-only semantic receipts; bounded and safe for attempt binding. */
  semanticReceipts?: {
    observationReceiptDigest?: string;
    nativeAdmissionReceiptDigest?: string;
    hybridPlanDigest?: string;
    rasterDegradationReceiptDigest?: string;
  };
  /** Single declared-QID authority across logical admission, OOXML, and optional COM reopen. */
  qidCoverage?: PresentationExportQidCoverage[];
  geometryContract?: {
    readonly schemaVersion: "presenton.hybrid-geometry/v1";
    readonly chromiumDigest: string;
    readonly elements: readonly {
      readonly elementId: string;
      readonly kind: "text" | "image" | "shape" | "table" | "chart";
      readonly x: number;
      readonly y: number;
      readonly width: number;
      readonly height: number;
    }[];
    readonly ooxmlDigest?: string;
    readonly ooxmlMatch?: true;
  };
  /** Immutable detailed OOXML projection, keyed by the declared QID. */
  nativeElementReadback?: PresentationExportNativeElementReadback[];
  /** Fixed-point paint closure that produced this native/backplate layering. */
  paintPlan?: PresentationExportPaintPlanReadback;
}

export interface PresentationExportNativeElementReadback {
  readonly slideNumber: number;
  readonly elementId: string;
  readonly kind: "text" | "shape" | "image" | "table" | "chart";
  readonly latinTypefaces: readonly string[];
  readonly eastAsianTypefaces: readonly string[];
  readonly lineBreakCount: number;
  readonly chart?: {
    readonly chartType: "bar" | "column" | "line" | "area" | "pie" | "donut";
    readonly title: string | null;
    readonly titleLatinTypefaces: readonly string[];
    readonly titleEastAsianTypefaces: readonly string[];
    readonly categories: readonly string[];
    readonly series: readonly { readonly name: string; readonly values: readonly number[] }[];
    readonly categoryAxis: PresentationExportChartTextReadback;
    readonly valueAxis: PresentationExportChartTextReadback;
    readonly legend: PresentationExportChartLegendReadback;
  };
}

export interface PresentationExportChartTextReadback {
  readonly present: boolean;
  readonly title: string | null;
  readonly latinTypefaces: readonly string[];
  readonly eastAsianTypefaces: readonly string[];
}

export interface PresentationExportChartLegendReadback extends PresentationExportChartTextReadback {
  readonly position: "bottom" | "top" | "right" | "left" | null;
}

export interface PresentationExportPaintPlanReadback {
  readonly schemaVersion: "hybrid-paint-plan/browser-v1";
  readonly strategy: "single-backplate-fixed-point" | "audited-multi-z-band";
  readonly fixedPointReached: true;
  readonly closureIterations: number;
  readonly closureDigest: string;
  readonly hybridPlanDigest: string;
  readonly slideDemotedToRaster: boolean;
  readonly downgradeReceipt: {
    readonly schemaVersion: "hybrid-layer-downgrade-receipt/browser-v1";
    readonly state: "closed" | "slideRaster";
    readonly reason: "no-closed-native-z-band" | "backplate-identity-mismatch" | "opaque-backplate-collision" | null;
    readonly demotedElementIds: readonly string[];
    readonly receiptDigest: string;
  };
  readonly demotions: readonly {
    readonly elementId: string;
    readonly fallbackReason: "z-band-unsafe";
    readonly blockedByElementIds: readonly string[];
  }[];
  readonly bands?: readonly {
    readonly zBand: number;
    readonly paintOwner: "native" | "rasterIsland";
    readonly elementIds: readonly string[];
    readonly artifactDigest?: string;
  }[];
}

export interface PresentationExportQidCoverage {
  readonly elementId: string;
  readonly kind: "text" | "image" | "shape" | "table" | "chart";
  readonly logical: "native" | "raster";
  readonly ooxml: "not-run" | "present" | "missing";
  readonly com: "not-run" | "present" | "missing";
}

export interface PresentationExportCoverageCounts {
  nativeText: number;
  nativeImage: number;
  nativeShape: number;
  nativeTable?: number;
  nativeChart?: number;
  nativeGroup: number;
  rasterRemaining: number;
}

export interface PresentationExportOoxmlReadback
  extends PresentationExportCoverageCounts {
  status: "not-run" | "passed" | "failed";
}

export interface PresentationExportTypographyIssue {
  code: string;
  elementId?: string;
}

export interface PresentationExportRendererIsolation {
  osIsolation:
    | "nonroot-readonly-tmpfs-network-none"
    | "development-unattested"
    | "local-native-unattested";
  executionBoundary: "external-worker" | "development-local" | "local-native";
  /** False whenever the render carries no OS-isolation guarantee. */
  attested: boolean;
}

export interface PresentationExportQualityReport {
  schemaVersion: typeof PRESENTATION_EXPORT_QUALITY_SCHEMA_VERSION;
  mode: "hybrid" | "fidelity";
  status: PresentationExportQualityStatus;
  totalSlides: number;
  editableSlides: number;
  imageFallbackSlides: number;
  nativeTextElements: number;
  nativeShapeElements: number;
  nativeTableElements?: number;
  nativeChartElements?: number;
  nativeGroupElements?: number;
  nativeImageElements: number;
  rasterFallbackElements: number;
  logicalCoverage: PresentationExportCoverageCounts;
  ooxmlReadback: PresentationExportOoxmlReadback;
  backplateCount: number;
  typographyIssues: PresentationExportTypographyIssue[];
  fallbackReasonCounts?: Record<string, number>;
  /** Supported semantic denominator and element-level raster audit. */
  nativeCoverage?: PresentationExportNativeCoverage;
  slides: PresentationExportSlideQuality[];
  /** Kept for v1 consumers that only need a boolean. */
  fontEmbedding: boolean;
  fontEmbeddingStatus?: PresentationExportFontEmbeddingStatus;
  fontRendering?: PresentationExportFontRenderingQuality;
  /**
   * Which renderer isolation the artifact was actually produced under. Recorded
   * so an unattested local-native render is never mistaken for an isolated
   * external-worker one after the file leaves this process. Additive: exporters
   * that never consult the isolation policy may omit it.
   */
  rendererIsolation?: PresentationExportRendererIsolation;
  exportAttemptId?: string;
  atomicBinding?: {
    presentationId: string;
    presentationRevision: number;
    canonicalSourceDigest: string;
    rendererSourceDigest: string;
    pptxSha256: string;
  };
  powerPointQa?: {
    status: "preflight" | "not-verified" | "passed" | "failed";
    platform: "windows" | "unsupported" | "unknown";
  };
}

export type PresentationExportSlideQualityInput = Omit<
  PresentationExportSlideQuality,
  | "nativeGroupElements"
  | "fallbackElements"
  | "logicalCoverage"
  | "ooxmlReadback"
  | "backplateCount"
  | "typographyIssues"
> & {
  nativeGroupElements?: number;
  fallbackElements?: readonly PresentationExportFallbackElement[];
  logicalCoverage?: Partial<PresentationExportCoverageCounts>;
  ooxmlReadback?: Partial<PresentationExportOoxmlReadback>;
  backplateCount?: number;
  typographyIssues?: readonly PresentationExportTypographyIssue[];
};

export interface PresentationExportQualityOptions {
  fontEmbeddingStatus?: Partial<PresentationExportFontEmbeddingStatus>;
  fontRendering?: Partial<PresentationExportFontRenderingQuality>;
  rendererIsolation?: PresentationExportRendererIsolation;
}

function nonNegativeInteger(value: number): number {
  return Number.isFinite(value) ? Math.max(0, Math.trunc(value)) : 0;
}

function cleanStrings(values: readonly string[]): string[] {
  return [...new Set(values.filter(Boolean))].sort();
}

function coverageCounts(
  value: Partial<PresentationExportCoverageCounts> | undefined,
  fallback: PresentationExportCoverageCounts
): PresentationExportCoverageCounts {
  const nativeChart = value?.nativeChart ?? fallback.nativeChart;
  const nativeTable = value?.nativeTable ?? fallback.nativeTable;
  return {
    nativeText: nonNegativeInteger(value?.nativeText ?? fallback.nativeText),
    nativeImage: nonNegativeInteger(value?.nativeImage ?? fallback.nativeImage),
    nativeShape: nonNegativeInteger(value?.nativeShape ?? fallback.nativeShape),
    ...(nativeTable === undefined
      ? {}
      : { nativeTable: nonNegativeInteger(nativeTable) }),
    nativeGroup: nonNegativeInteger(value?.nativeGroup ?? fallback.nativeGroup),
    ...(nativeChart === undefined
      ? {}
      : { nativeChart: nonNegativeInteger(nativeChart) }),
    rasterRemaining: nonNegativeInteger(
      value?.rasterRemaining ?? fallback.rasterRemaining
    ),
  };
}

function nativeCoverageForSlides(
  slides: readonly PresentationExportSlideQuality[],
): PresentationExportNativeCoverage {
  const qids = slides.flatMap((slide) =>
    (slide.qidCoverage ?? []).map((entry) => ({ slide, entry })),
  );
  if (!qids.length) {
    return {
      status: "not_assessed",
      eligibleElementCount: 0,
      nativeElementCount: 0,
      rasterFallbacks: [],
    };
  }
  const seen = new Set<string>();
  let invalid = false;
  for (const { slide, entry } of qids) {
    const ownerKey = `${slide.slideNumber}:${entry.elementId}`;
    if (seen.has(ownerKey)) invalid = true;
    seen.add(ownerKey);
  }
  const fallbackAudit: PresentationExportRasterFallbackAudit[] = [];
  const auditedIds = new Set<string>();
  const supportedKinds = new Set<PresentationExportFallbackCandidateKind>([
    "text",
    "image",
    "shape",
    "table",
    "chart",
  ]);
  for (const { slide, entry } of qids) {
    if (entry.logical !== "raster") continue;
    const declared = (slide.fallbackElements ?? []).find(
      (element) => element.elementId === entry.elementId,
    );
    const reason = declared?.reasons.find((value) => value.trim())?.trim();
    if (
      declared &&
      supportedKinds.has(declared.candidateKind) &&
      declared.candidateKind !== entry.kind
    ) {
      invalid = true;
    }
    const ownerKey = `${slide.slideNumber}:${entry.elementId}`;
    fallbackAudit.push({
      elementId: ownerKey,
      reason: reason ?? "unclassified-raster-fallback",
      // Every declared semantic owner stays in the release denominator. Native
      // capability support decides disposition, never denominator membership.
      eligible: true,
      explicit: Boolean(reason),
    });
    auditedIds.add(ownerKey);
  }
  for (const slide of slides) {
    for (const element of slide.fallbackElements ?? []) {
      const ownerKey = `${slide.slideNumber}:${element.elementId}`;
      if (auditedIds.has(ownerKey)) continue;
      const reason = element.reasons.find((value) => value.trim())?.trim();
      const qidOwner = (slide.qidCoverage ?? []).find(
        (entry) => entry.elementId === element.elementId,
      );
      fallbackAudit.push({
        elementId: ownerKey,
        reason: reason ?? "unclassified-raster-fallback",
        eligible: qidOwner?.logical !== "native",
        explicit: Boolean(reason),
      });
      auditedIds.add(ownerKey);
    }
  }
  const nativeElementCount = qids.filter(
    ({ entry }) => entry.logical === "native",
  ).length;
  const eligibleRasterCount = fallbackAudit.filter(
    (fallback) => fallback.eligible,
  ).length;
  const eligibleElementCount = nativeElementCount + eligibleRasterCount;
  const declaredNative = slides.reduce(
    (sum, slide) =>
      sum +
      slide.logicalCoverage.nativeText +
      slide.logicalCoverage.nativeImage +
      slide.logicalCoverage.nativeShape +
      (slide.logicalCoverage.nativeTable ?? 0) +
      (slide.logicalCoverage.nativeChart ?? 0) +
      // Groups are structural OOXML containers, not independently supported
      // semantic owners. Their child QIDs are already counted by kind.
      0,
    0,
  );
  const declaredRaster = slides.reduce(
    (sum, slide) => sum + slide.logicalCoverage.rasterRemaining,
    0,
  );
  invalid ||= nativeElementCount !== declaredNative;
  invalid ||= fallbackAudit.length !== declaredRaster;
  invalid ||= fallbackAudit.some(
    (fallback) =>
      !fallback.elementId.trim() ||
      !fallback.reason.trim() ||
      !fallback.explicit,
  );
  return {
    status: invalid ? "blocked" : "passed",
    eligibleElementCount,
    nativeElementCount,
    ...(eligibleElementCount
      ? { ratio: nativeElementCount / eligibleElementCount }
      : {}),
    rasterFallbacks: fallbackAudit.sort((left, right) =>
      left.elementId.localeCompare(right.elementId),
    ),
  };
}

function normalizeFallbackElement(
  element: PresentationExportFallbackElement,
  index: number
): PresentationExportFallbackElement {
  const candidateKinds = new Set<PresentationExportFallbackCandidateKind>([
    "text",
    "image",
    "shape",
    "table",
    "chart",
    "complex",
    "slide",
    "unknown",
  ]);
  return {
    elementId: element.elementId.trim() || `unknown:${index + 1}`,
    ...(element.domPath?.trim() ? { domPath: element.domPath.trim() } : {}),
    candidateKind: candidateKinds.has(element.candidateKind)
      ? element.candidateKind
      : "unknown",
    reasons: cleanStrings(element.reasons),
  };
}

export function createPresentationExportQuality(
  mode: PresentationExportQualityReport["mode"],
  inputSlides: readonly PresentationExportSlideQualityInput[],
  options: PresentationExportQualityOptions = {}
): PresentationExportQualityReport {
  const slides = inputSlides
    .map((slide) => {
      const fallbackElements = (slide.fallbackElements ?? []).map(
        normalizeFallbackElement
      );
      const nativeGroupElements = nonNegativeInteger(
        slide.nativeGroupElements ?? 0
      );
      const logicalCoverage = coverageCounts(slide.logicalCoverage, {
        nativeText: nonNegativeInteger(slide.nativeTextElements),
        nativeImage: nonNegativeInteger(slide.nativeImageElements),
        nativeShape: nonNegativeInteger(slide.nativeShapeElements),
        ...(slide.nativeTableElements === undefined
          ? {}
          : { nativeTable: nonNegativeInteger(slide.nativeTableElements) }),
        ...(slide.nativeChartElements === undefined
          ? {}
          : { nativeChart: nonNegativeInteger(slide.nativeChartElements) }),
        nativeGroup: nativeGroupElements,
        rasterRemaining: nonNegativeInteger(slide.rasterFallbackElements),
      });
      const readback = coverageCounts(slide.ooxmlReadback, {
        nativeText: 0,
        nativeImage: 0,
        nativeShape: 0,
        ...(slide.ooxmlReadback?.status === "passed" &&
        slide.nativeTableElements !== undefined
          ? { nativeTable: nonNegativeInteger(slide.nativeTableElements) }
          : {}),
        nativeGroup: 0,
        rasterRemaining: 0,
      });
      return {
        ...slide,
        slideNumber: Math.max(1, Math.trunc(slide.slideNumber)),
        nativeTextElements: nonNegativeInteger(slide.nativeTextElements),
        nativeShapeElements: nonNegativeInteger(slide.nativeShapeElements),
        ...(slide.nativeChartElements === undefined
          ? {}
          : {
              nativeChartElements: nonNegativeInteger(
                slide.nativeChartElements,
              ),
            }),
        ...(slide.nativeTableElements === undefined ? {} : { nativeTableElements: nonNegativeInteger(slide.nativeTableElements) }),
        nativeGroupElements,
        nativeImageElements: nonNegativeInteger(slide.nativeImageElements),
        rasterFallbackElements: nonNegativeInteger(
          slide.rasterFallbackElements
        ),
        fallbackReasons: cleanStrings([
          ...slide.fallbackReasons,
          ...fallbackElements.flatMap((element) => element.reasons),
        ]),
        fallbackElements,
        logicalCoverage,
        ooxmlReadback: {
          ...readback,
          status: slide.ooxmlReadback?.status ?? "not-run",
        },
        backplateCount: nonNegativeInteger(slide.backplateCount ?? 0),
        typographyIssues: (slide.typographyIssues ?? []).map((issue) => ({
          code: issue.code.trim() || "unknown",
          ...(issue.elementId?.trim()
            ? { elementId: issue.elementId.trim() }
            : {}),
        })),
      };
    })
    .sort((a, b) => a.slideNumber - b.slideNumber);
  const editableSlides = slides.filter((slide) => slide.editable).length;
  const imageFallbackSlides = slides.filter(
    (slide) => slide.imageFallback
  ).length;
  const rasterFallbackElements = slides.reduce(
    (sum, slide) => sum + slide.rasterFallbackElements,
    0
  );
  const fallbackReasonCounts = new Map<string, number>();
  for (const slide of slides) {
    if (slide.fallbackElements.length > 0) {
      for (const element of slide.fallbackElements) {
        for (const reason of element.reasons) {
          fallbackReasonCounts.set(
            reason,
            (fallbackReasonCounts.get(reason) ?? 0) + 1
          );
        }
      }
    } else {
      for (const reason of slide.fallbackReasons) {
        fallbackReasonCounts.set(
          reason,
          (fallbackReasonCounts.get(reason) ?? 0) + 1
        );
      }
    }
  }
  const status: PresentationExportQualityStatus =
    editableSlides === 0
      ? "image-only"
      : editableSlides === slides.length &&
          imageFallbackSlides === 0 &&
          rasterFallbackElements === 0
        ? "fully-editable"
        : "partially-editable";
  const requested = options.fontEmbeddingStatus?.requested === true;
  const applied =
    requested && options.fontEmbeddingStatus?.applied === true;
  const embeddedFontFiles = applied
    ? nonNegativeInteger(
        options.fontEmbeddingStatus?.embeddedFontFiles ?? 0
      )
    : 0;
  const embeddingReason: PresentationExportFontEmbeddingStatus["reason"] =
    applied
      ? "embedded"
      : !requested
        ? "not-requested"
        : options.fontEmbeddingStatus?.reason === "failed"
          ? "failed"
          : "unsupported";
  const fontEmbeddingStatus: PresentationExportFontEmbeddingStatus = {
    policy: "opt-in",
    requested,
    applied,
    embeddedFontFiles,
    reason: embeddingReason,
    ...(options.fontEmbeddingStatus?.nativeTextUsage
      ? { nativeTextUsage: options.fontEmbeddingStatus.nativeTextUsage }
      : {}),
    ...(options.fontEmbeddingStatus?.nativeTextElements !== undefined
      ? {
          nativeTextElements: nonNegativeInteger(
            options.fontEmbeddingStatus.nativeTextElements,
          ),
        }
      : {}),
    ...(options.fontEmbeddingStatus
      ?.nativeTextElementsUsingEmbeddedTypefaces !== undefined
      ? {
          nativeTextElementsUsingEmbeddedTypefaces: nonNegativeInteger(
            options.fontEmbeddingStatus
              .nativeTextElementsUsingEmbeddedTypefaces,
          ),
        }
      : {}),
    ...(options.fontEmbeddingStatus?.nativeTypefaceFamilies
      ? {
          nativeTypefaceFamilies: cleanStrings(
            options.fontEmbeddingStatus.nativeTypefaceFamilies.map((family) =>
              family.trim().slice(0, 127),
            ),
          ),
        }
      : {}),
    ...(options.fontEmbeddingStatus?.strategy
      ? { strategy: options.fontEmbeddingStatus.strategy }
      : {}),
    ...(options.fontEmbeddingStatus?.embeddedTypefaces !== undefined
      ? {
          embeddedTypefaces: applied
            ? nonNegativeInteger(
                options.fontEmbeddingStatus.embeddedTypefaces
              )
            : 0,
        }
      : {}),
    ...(options.fontEmbeddingStatus?.editLimitation
      ? {
          editLimitation: applied
            ? options.fontEmbeddingStatus.editLimitation
            : "none",
        }
      : {}),
    ...(options.fontEmbeddingStatus?.faces
      ? {
          faces: applied
            ? options.fontEmbeddingStatus.faces.map((face) => ({
                ...face,
                typeface: face.typeface.trim().slice(0, 127),
                source: face.source.trim(),
                ...(face.sourcePath?.trim()
                  ? { sourcePath: face.sourcePath.trim() }
                  : {}),
                sourceBytes: nonNegativeInteger(face.sourceBytes),
                embeddedBytes: nonNegativeInteger(face.embeddedBytes),
                weight: nonNegativeInteger(face.weight),
                fsType: nonNegativeInteger(face.fsType) & 0xffff,
              }))
            : [],
        }
      : {}),
    ...(options.fontEmbeddingStatus?.failures
      ? {
          failures: options.fontEmbeddingStatus.failures.map((failure) => ({
            ...(failure.family?.trim()
              ? { family: failure.family.trim().slice(0, 127) }
              : {}),
            ...(failure.face ? { face: failure.face } : {}),
            reason: failure.reason.trim() || "unknown",
            ...(failure.detail?.trim()
              ? { detail: failure.detail.trim() }
              : {}),
          })),
        }
      : {}),
  };

  const hasNativeChartCounts = slides.some(
    (slide) => slide.nativeChartElements !== undefined,
  );
  const hasNativeTableCounts = slides.some(
    (slide) => slide.nativeTableElements !== undefined,
  );
  return {
    schemaVersion: PRESENTATION_EXPORT_QUALITY_SCHEMA_VERSION,
    mode,
    status,
    totalSlides: slides.length,
    editableSlides,
    imageFallbackSlides,
    nativeTextElements: slides.reduce(
      (sum, slide) => sum + slide.nativeTextElements,
      0
    ),
    nativeShapeElements: slides.reduce(
      (sum, slide) => sum + slide.nativeShapeElements,
      0
    ),
    ...(hasNativeTableCounts
      ? {
          nativeTableElements: slides.reduce(
            (sum, slide) => sum + (slide.nativeTableElements ?? 0),
            0,
          ),
        }
      : {}),
    ...(hasNativeChartCounts
      ? {
          nativeChartElements: slides.reduce(
            (sum, slide) => sum + (slide.nativeChartElements ?? 0),
            0,
          ),
        }
      : {}),
    nativeGroupElements: slides.reduce(
      (sum, slide) => sum + slide.nativeGroupElements,
      0
    ),
    nativeImageElements: slides.reduce(
      (sum, slide) => sum + slide.nativeImageElements,
      0
    ),
    rasterFallbackElements,
    logicalCoverage: {
      nativeText: slides.reduce((sum, slide) => sum + slide.logicalCoverage.nativeText, 0),
      nativeImage: slides.reduce((sum, slide) => sum + slide.logicalCoverage.nativeImage, 0),
      nativeShape: slides.reduce((sum, slide) => sum + slide.logicalCoverage.nativeShape, 0),
      ...(slides.some((slide) => slide.logicalCoverage.nativeTable !== undefined)
        ? {
            nativeTable: slides.reduce(
              (sum, slide) => sum + (slide.logicalCoverage.nativeTable ?? 0),
              0,
            ),
          }
        : {}),
      ...(slides.some((slide) => slide.logicalCoverage.nativeChart !== undefined)
        ? {
            nativeChart: slides.reduce(
              (sum, slide) => sum + (slide.logicalCoverage.nativeChart ?? 0),
              0,
            ),
          }
        : {}),
      nativeGroup: slides.reduce((sum, slide) => sum + slide.logicalCoverage.nativeGroup, 0),
      rasterRemaining: slides.reduce((sum, slide) => sum + slide.logicalCoverage.rasterRemaining, 0),
    },
    ooxmlReadback: {
      status: slides.length > 0 && slides.every((slide) => slide.ooxmlReadback.status === "passed")
        ? "passed"
        : slides.some((slide) => slide.ooxmlReadback.status === "failed")
          ? "failed"
          : "not-run",
      nativeText: slides.reduce((sum, slide) => sum + slide.ooxmlReadback.nativeText, 0),
      nativeImage: slides.reduce((sum, slide) => sum + slide.ooxmlReadback.nativeImage, 0),
      nativeShape: slides.reduce((sum, slide) => sum + slide.ooxmlReadback.nativeShape, 0),
      ...(slides.some((slide) => slide.ooxmlReadback.nativeTable !== undefined)
        ? {
            nativeTable: slides.reduce(
              (sum, slide) => sum + (slide.ooxmlReadback.nativeTable ?? 0),
              0,
            ),
          }
        : {}),
      ...(slides.some((slide) => slide.ooxmlReadback.nativeChart !== undefined)
        ? {
            nativeChart: slides.reduce(
              (sum, slide) => sum + (slide.ooxmlReadback.nativeChart ?? 0),
              0,
            ),
          }
        : {}),
      nativeGroup: slides.reduce((sum, slide) => sum + slide.ooxmlReadback.nativeGroup, 0),
      rasterRemaining: slides.reduce((sum, slide) => sum + slide.ooxmlReadback.rasterRemaining, 0),
    },
    backplateCount: slides.reduce((sum, slide) => sum + slide.backplateCount, 0),
    typographyIssues: slides.flatMap((slide) => slide.typographyIssues),
    fallbackReasonCounts: Object.fromEntries(
      [...fallbackReasonCounts.entries()].sort(([a], [b]) =>
        a.localeCompare(b)
      )
    ),
    nativeCoverage: nativeCoverageForSlides(slides),
    slides,
    fontEmbedding: fontEmbeddingStatus.applied,
    fontEmbeddingStatus,
    fontRendering: {
      browserFontFilesCollected: nonNegativeInteger(
        options.fontRendering?.browserFontFilesCollected ?? 0
      ),
      browserCollectionFailures: nonNegativeInteger(
        options.fontRendering?.browserCollectionFailures ?? 0
      ),
      powerpointTypefacePolicy: "central-compatible-fallbacks",
    },
    ...(options.rendererIsolation
      ? { rendererIsolation: options.rendererIsolation }
      : {}),
  };
}

export function createImageOnlyExportQuality(
  totalSlides: number,
  reason: string,
  options: PresentationExportQualityOptions = {}
): PresentationExportQualityReport {
  return createPresentationExportQuality(
    "fidelity",
    Array.from({ length: nonNegativeInteger(totalSlides) }, (_, index) => ({
      slideNumber: index + 1,
      editable: false,
      imageFallback: true,
      nativeTextElements: 0,
      nativeShapeElements: 0,
      nativeImageElements: 0,
      rasterFallbackElements: 1,
      fallbackReasons: [reason],
    })),
    options
  );
}

type ExportCoverageWire = Record<string, unknown>;

function wireRecord(
  value: unknown,
  code = "presentation_export_quality_coverage_mismatch",
): ExportCoverageWire {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(code);
  }
  return value as ExportCoverageWire;
}

function wireCount(value: unknown): number {
  if (!Number.isSafeInteger(value) || Number(value) < 0) {
    throw new Error("presentation_export_quality_coverage_mismatch");
  }
  return Number(value);
}

function assertSameCount(left: unknown, right: unknown): void {
  if (wireCount(left) !== wireCount(right)) {
    throw new Error("presentation_export_quality_coverage_mismatch");
  }
}

function assertCoverageProjection(
  quality: ExportCoverageWire,
  coverage: ExportCoverageWire,
): void {
  for (const [qualityKey, coverageKey] of [
    ["nativeText", "nativeText"],
    ["nativeImage", "nativeImage"],
    ["nativeShape", "nativeShape"],
    ["nativeTable", "nativeTable"],
    ["nativeChart", "nativeChart"],
    ["rasterRemaining", "rasterRemaining"],
  ] as const) {
    assertSameCount(quality[qualityKey] ?? 0, coverage[coverageKey] ?? 0);
  }
}

function releaseQualityProfile(value: unknown): boolean {
  const normalized = String(value ?? "development")
    .trim()
    .toLowerCase()
    .replaceAll("-", "_");
  return ["release", "strict", "production", "release_strict"].includes(
    normalized,
  );
}

/**
 * Fail closed before a durable Hybrid attempt is completed. The quality and
 * coverage sidecars must describe the same semantic owners and exact OOXML
 * package; release profiles additionally require the audited 70% native gate.
 */
export function assertPresentationExportQualityCoverage(
  quality: PresentationExportQualityReport,
  coverageValue: unknown,
  profile: unknown = "development",
): void {
  const coverage = wireRecord(coverageValue);
  assertSameCount(quality.totalSlides, coverage.totalSlides);
  assertSameCount(quality.nativeTextElements, coverage.nativeText);
  assertSameCount(quality.nativeImageElements, coverage.nativeImage);
  assertSameCount(quality.nativeShapeElements, coverage.nativeShape);
  assertSameCount(quality.nativeTableElements ?? 0, coverage.nativeTable ?? 0);
  assertSameCount(quality.nativeChartElements ?? 0, coverage.nativeChart ?? 0);
  assertSameCount(quality.logicalCoverage.rasterRemaining, coverage.rasterRemaining);
  assertCoverageProjection(
    wireRecord(quality.logicalCoverage),
    wireRecord(coverage.logicalCoverage),
  );
  assertCoverageProjection(
    wireRecord(quality.ooxmlReadback),
    wireRecord(coverage.ooxmlReadback),
  );
  if (quality.ooxmlReadback.status !== wireRecord(coverage.ooxmlReadback).status) {
    throw new Error("presentation_export_quality_coverage_mismatch");
  }
  const coverageSlides = coverage.slides;
  if (!Array.isArray(coverageSlides) || coverageSlides.length !== quality.slides.length) {
    throw new Error("presentation_export_quality_coverage_mismatch");
  }
  const coverageBySlide = new Map(
    coverageSlides.map((value) => {
      const slide = wireRecord(value);
      return [wireCount(slide.slideNumber), slide] as const;
    }),
  );
  if (coverageBySlide.size !== coverageSlides.length) {
    throw new Error("presentation_export_quality_coverage_mismatch");
  }
  for (const qualitySlide of quality.slides) {
    const coverageSlide = coverageBySlide.get(qualitySlide.slideNumber);
    if (!coverageSlide) {
      throw new Error("presentation_export_quality_coverage_mismatch");
    }
    assertSameCount(qualitySlide.nativeTextElements, coverageSlide.nativeText);
    assertSameCount(qualitySlide.nativeImageElements, coverageSlide.nativeImage);
    assertSameCount(qualitySlide.nativeShapeElements, coverageSlide.nativeShape);
    assertSameCount(qualitySlide.nativeTableElements ?? 0, coverageSlide.nativeTable ?? 0);
    assertSameCount(qualitySlide.nativeChartElements ?? 0, coverageSlide.nativeChart ?? 0);
    assertSameCount(
      qualitySlide.logicalCoverage.rasterRemaining,
      coverageSlide.rasterRemaining,
    );
    assertCoverageProjection(
      wireRecord(qualitySlide.logicalCoverage),
      wireRecord(coverageSlide.logicalCoverage),
    );
    assertCoverageProjection(
      wireRecord(qualitySlide.ooxmlReadback),
      wireRecord(coverageSlide.ooxmlReadback),
    );
    if (
      qualitySlide.ooxmlReadback.status !==
      wireRecord(coverageSlide.ooxmlReadback).status
    ) {
      throw new Error("presentation_export_quality_coverage_mismatch");
    }
  }
  const nativeCoverage = quality.nativeCoverage;
  if (nativeCoverage) {
    const coverageNative =
      wireCount(coverage.nativeText) +
      wireCount(coverage.nativeImage) +
      wireCount(coverage.nativeShape) +
      wireCount(coverage.nativeTable ?? 0) +
      wireCount(coverage.nativeChart ?? 0);
    const auditedEligible = nativeCoverage.rasterFallbacks.length;
    if (
      new Set(
        nativeCoverage.rasterFallbacks.map((fallback) => fallback.elementId),
      ).size !== nativeCoverage.rasterFallbacks.length ||
      nativeCoverage.rasterFallbacks.some(
        (fallback) =>
          typeof fallback.elementId !== "string" ||
          typeof fallback.reason !== "string" ||
          typeof fallback.eligible !== "boolean" ||
          typeof fallback.explicit !== "boolean" ||
          !fallback.elementId.trim() ||
          !fallback.reason.trim() ||
          fallback.eligible !== true,
      )
    ) {
      throw new Error("presentation_export_native_coverage_mismatch");
    }
    assertSameCount(nativeCoverage.nativeElementCount, coverageNative);
    assertSameCount(
      nativeCoverage.eligibleElementCount,
      nativeCoverage.nativeElementCount + auditedEligible,
    );
    assertSameCount(
      nativeCoverage.rasterFallbacks.length,
      coverage.rasterRemaining,
    );
    const expectedRatio = nativeCoverage.eligibleElementCount
      ? nativeCoverage.nativeElementCount / nativeCoverage.eligibleElementCount
      : undefined;
    if (
      (expectedRatio === undefined && nativeCoverage.ratio !== undefined) ||
      (expectedRatio !== undefined &&
        (nativeCoverage.ratio === undefined ||
          Math.abs(nativeCoverage.ratio - expectedRatio) > Number.EPSILON))
    ) {
      throw new Error("presentation_export_native_coverage_mismatch");
    }
  }
  if (releaseQualityProfile(profile)) {
    if (
      !nativeCoverage ||
      nativeCoverage.status !== "passed" ||
      nativeCoverage.ratio === undefined ||
      nativeCoverage.ratio < PRESENTATION_EXPORT_MINIMUM_NATIVE_COVERAGE_RATIO ||
      nativeCoverage.rasterFallbacks.some(
        (fallback) =>
          !fallback.explicit ||
          fallback.eligible !== true ||
          !fallback.elementId.trim() ||
          !fallback.reason.trim(),
      )
    ) {
      throw new Error("presentation_export_release_native_coverage_blocked");
    }
  }
}

/** Join logical DOM promotion facts with readback from the exact assembled PPTX. */
export function attachPresentationExportOoxmlReadback(
  logical: PresentationExportQualityReport,
  readback: PresentationExportQualityReport
): PresentationExportQualityReport {
  const readbackBySlide = new Map(
    readback.slides.map((slide) => [slide.slideNumber, slide])
  );
  if (
    logical.totalSlides !== readback.totalSlides ||
    logical.slides.some((slide) => !readbackBySlide.has(slide.slideNumber))
  ) {
    throw new Error("presentation_export_ooxml_readback_slide_mismatch");
  }
  return createPresentationExportQuality(
    logical.mode,
    logical.slides.map((slide) => {
      const observed = readbackBySlide.get(slide.slideNumber)!;
      const observedEntries = (observed.qidCoverage ?? []).filter(
        (entry) => entry.ooxml === "present",
      );
      const observedById = new Map<string, typeof observedEntries>();
      for (const entry of observedEntries) {
        const owners = observedById.get(entry.elementId) ?? [];
        owners.push(entry);
        observedById.set(entry.elementId, owners);
      }
      if (slide.qidCoverage) {
        const declaredIds = new Set(slide.qidCoverage.map((entry) => entry.elementId));
        const duplicateObserved = [...observedById.values()].some(
          (owners) => owners.length !== 1,
        );
        const unexpectedObserved = [...observedById.keys()].some(
          (elementId) => !declaredIds.has(elementId),
        );
        const missingOrMisowned = slide.qidCoverage.some((entry) => {
          const owner = observedById.get(entry.elementId)?.[0];
          if (!owner || owner.logical !== entry.logical) return true;
          // Raster ownership is carried by a non-visual OOXML shape, so its
          // carrier kind is intentionally independent from the declared kind.
          return entry.logical === "native" && owner.kind !== entry.kind;
        });
        if (duplicateObserved || unexpectedObserved || missingOrMisowned) {
          throw new Error("presentation_export_qid_ownership_mismatch");
        }
      }
      return {
        ...slide,
        logicalCoverage: slide.logicalCoverage,
        ooxmlReadback: observed.ooxmlReadback,
        backplateCount: observed.backplateCount,
        ...(slide.qidCoverage
          ? {
              qidCoverage: slide.qidCoverage.map((entry) => ({
                ...entry,
                ooxml: "present" as const,
              })),
            }
          : {}),
        typographyIssues: [
          ...slide.typographyIssues,
          ...observed.typographyIssues,
        ],
      };
    }),
    {
      fontEmbeddingStatus: logical.fontEmbeddingStatus,
      fontRendering: logical.fontRendering,
      rendererIsolation: logical.rendererIsolation,
    }
  );
}
