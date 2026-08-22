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
}

export interface PresentationExportCoverageCounts {
  nativeText: number;
  nativeImage: number;
  nativeShape: number;
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
  nativeGroupElements?: number;
  nativeImageElements: number;
  rasterFallbackElements: number;
  logicalCoverage: PresentationExportCoverageCounts;
  ooxmlReadback: PresentationExportOoxmlReadback;
  backplateCount: number;
  typographyIssues: PresentationExportTypographyIssue[];
  fallbackReasonCounts?: Record<string, number>;
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
  return {
    nativeText: nonNegativeInteger(value?.nativeText ?? fallback.nativeText),
    nativeImage: nonNegativeInteger(value?.nativeImage ?? fallback.nativeImage),
    nativeShape: nonNegativeInteger(value?.nativeShape ?? fallback.nativeShape),
    nativeGroup: nonNegativeInteger(value?.nativeGroup ?? fallback.nativeGroup),
    rasterRemaining: nonNegativeInteger(
      value?.rasterRemaining ?? fallback.rasterRemaining
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
        nativeGroup: nativeGroupElements,
        rasterRemaining: nonNegativeInteger(slide.rasterFallbackElements),
      });
      const readback = coverageCounts(slide.ooxmlReadback, {
        nativeText: 0,
        nativeImage: 0,
        nativeShape: 0,
        nativeGroup: 0,
        rasterRemaining: 0,
      });
      return {
        ...slide,
        slideNumber: Math.max(1, Math.trunc(slide.slideNumber)),
        nativeTextElements: nonNegativeInteger(slide.nativeTextElements),
        nativeShapeElements: nonNegativeInteger(slide.nativeShapeElements),
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
      return {
        ...slide,
        logicalCoverage: slide.logicalCoverage,
        ooxmlReadback: observed.ooxmlReadback,
        backplateCount: observed.backplateCount,
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
