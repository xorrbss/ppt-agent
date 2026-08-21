import { extractAuthoredSlideDom } from "./index.ts";
import type { AuthoredHybridSlideLayer } from "./pptx-assembler.ts";
import type {
  PresentationExportFallbackElement,
  PresentationExportSlideQuality,
} from "../presentation-export-quality.ts";

export interface PreparedSlideFontRendering {
  browserFontFilesCollected: number;
  browserCollectionFailures: number;
}

export function imageFallbackSlide(slideNumber: number, reason: string): PresentationExportSlideQuality {
  return { slideNumber, editable: false, imageFallback: true, nativeTextElements: 0, nativeShapeElements: 0, nativeGroupElements: 0, nativeImageElements: 0, rasterFallbackElements: 1, fallbackReasons: [reason], fallbackElements: [{ elementId: `slide:${slideNumber}`, candidateKind: "slide", reasons: [reason] }] };
}

export function noFontRenderingTelemetry(): PreparedSlideFontRendering {
  return { browserFontFilesCollected: 0, browserCollectionFailures: 0 };
}

export function fallbackElementForContractElement(element: Awaited<ReturnType<typeof extractAuthoredSlideDom>>["elements"][number]): PresentationExportFallbackElement {
  const candidateKind = element.classification.mode === "native" ? element.classification.kind : element.classification.candidateKind;
  const nativeBackplateReasons =
    element.classification.mode === "native" && "shape" in element
      ? [
          ...(element.shape.gradient ? ["gradient-backplate-only"] : []),
          ...(element.shape.shadowLayers?.length ? ["shadow-backplate-only"] : []),
        ]
      : [];
  return {
    elementId: element.id,
    domPath: element.domPath,
    candidateKind,
    reasons:
      element.classification.mode === "raster"
        ? element.classification.reasons
        : nativeBackplateReasons.length
          ? nativeBackplateReasons
          : ["native-preparation-or-layer-safety-rejected"],
  };
}

export type PreparedSlideLayer = AuthoredHybridSlideLayer | null;
