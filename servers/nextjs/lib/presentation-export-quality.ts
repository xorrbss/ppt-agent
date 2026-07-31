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

export interface PresentationExportFontEmbeddingStatus {
  /** Embedding remains an explicit opt-in even when a future exporter supports it. */
  policy: "opt-in";
  requested: boolean;
  applied: boolean;
  embeddedFontFiles: number;
  embeddedTypefaces?: number;
  strategy?: "full" | "subset";
  /**
   * Subset fonts cover all characters in this deck, but newly typed characters
   * outside that set may be substituted by PowerPoint.
   */
  editLimitation?:
    | "none"
    | "characters-outside-subset-may-substitute";
  faces?: PresentationExportEmbeddedFontFaceStatus[];
  failures?: PresentationExportFontEmbeddingFailure[];
  reason:
    | "not-requested"
    | "embedded"
    | "unsupported"
    | "failed";
}

export interface PresentationExportEmbeddedFontFaceStatus {
  typeface: string;
  face: "regular" | "bold" | "italic" | "boldItalic";
  weight: number;
  style: "normal" | "italic";
  source: string;
  /** Server-safe allowlist-relative source identifier, never a client path. */
  sourcePath?: string;
  sourceSha256: string;
  sourceBytes: number;
  embeddedBytes: number;
  fsType: number;
  licenseDecision:
    | "allowed-installable"
    | "allowed-editable"
    | "denied-restricted"
    | "denied-preview-print"
    | "denied-bitmap-only"
    | "denied-invalid";
  subset: boolean;
  strategy: "full" | "subset";
  partName: string;
  format: string;
  derivedFromVariable?: boolean;
}

export interface PresentationExportFontEmbeddingFailure {
  family?: string;
  face?: "regular" | "bold" | "italic" | "boldItalic";
  reason: string;
  detail?: string;
}

export interface PresentationExportFontRenderingQuality {
  browserFontFilesCollected: number;
  browserCollectionFailures: number;
  /** Identifies the stable authored-font to PowerPoint-typeface mapping policy. */
  powerpointTypefacePolicy: "central-compatible-fallbacks";
}

export interface PresentationExportSlideQuality {
  slideNumber: number;
  editable: boolean;
  imageFallback: boolean;
  nativeTextElements: number;
  nativeShapeElements: number;
  nativeGroupElements?: number;
  nativeImageElements: number;
  rasterFallbackElements: number;
  fallbackReasons: string[];
  /**
   * Element-level detail is additive. Older exporters may only know aggregate
   * counts and slide-level reasons, so this list can be shorter than
   * `rasterFallbackElements`.
   */
  fallbackElements?: PresentationExportFallbackElement[];
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
  fallbackReasonCounts?: Record<string, number>;
  slides: PresentationExportSlideQuality[];
  /** Kept for v1 consumers that only need a boolean. */
  fontEmbedding: boolean;
  fontEmbeddingStatus?: PresentationExportFontEmbeddingStatus;
  fontRendering?: PresentationExportFontRenderingQuality;
}

export type PresentationExportSlideQualityInput = Omit<
  PresentationExportSlideQuality,
  "nativeGroupElements" | "fallbackElements"
> & {
  nativeGroupElements?: number;
  fallbackElements?: readonly PresentationExportFallbackElement[];
};

export interface PresentationExportQualityOptions {
  fontEmbeddingStatus?: Partial<PresentationExportFontEmbeddingStatus>;
  fontRendering?: Partial<PresentationExportFontRenderingQuality>;
}

function nonNegativeInteger(value: number): number {
  return Number.isFinite(value) ? Math.max(0, Math.trunc(value)) : 0;
}

function cleanStrings(values: readonly string[]): string[] {
  return [...new Set(values.filter(Boolean))].sort();
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
      return {
        ...slide,
        slideNumber: Math.max(1, Math.trunc(slide.slideNumber)),
        nativeTextElements: nonNegativeInteger(slide.nativeTextElements),
        nativeShapeElements: nonNegativeInteger(slide.nativeShapeElements),
        nativeGroupElements: nonNegativeInteger(
          slide.nativeGroupElements ?? 0
        ),
        nativeImageElements: nonNegativeInteger(slide.nativeImageElements),
        rasterFallbackElements: nonNegativeInteger(
          slide.rasterFallbackElements
        ),
        fallbackReasons: cleanStrings([
          ...slide.fallbackReasons,
          ...fallbackElements.flatMap((element) => element.reasons),
        ]),
        fallbackElements,
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
