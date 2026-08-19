import {
  extractAuthoredSlideDom,
  renderAuthoredBackplate,
} from "./index.ts";
import {
  prepareNativeElements,
  selectLayerSafeNativeElements,
  serializePreparedNativeElement,
  type PowerPointTypefaceSerializationOptions,
  type PreparedNativeElement,
} from "./native-plan.ts";
import type { AuthoredHybridSlideLayer } from "./pptx-assembler.ts";
import { preflightAuthoredHtmlForHybrid } from "./security.ts";
import { getGoogleFontsCollector } from "./google-fonts-runtime.ts";
import { mergePowerPointTextLayout } from "./font-layout.ts";
import type { PresentationExportSlideQuality } from "../presentation-export-quality.ts";
import {
  fallbackElementForContractElement,
  imageFallbackSlide,
} from "./export-slide-quality.ts";

// The collector adds a bounded network step ahead of each browser pass, so
// the per-pass ceiling is higher than the pre-collection 20s.
const HYBRID_BROWSER_PASS_TIMEOUT_MS = 45_000;

export interface PreparedSlideOutcome {
  layer: AuthoredHybridSlideLayer | null;
  quality: PresentationExportSlideQuality;
  fontRendering: {
    browserFontFilesCollected: number;
    browserCollectionFailures: number;
  };
}

function preSerializePreparedElements(
  elements: readonly PreparedNativeElement[],
  options: PowerPointTypefaceSerializationOptions = {}
): PreparedNativeElement[] {
  const serializable: PreparedNativeElement[] = [];
  for (const element of elements) {
    try {
      serializePreparedNativeElement(
        element,
        serializable.length + 3,
        element.kind === "image" ? "rId999" : undefined,
        options
      );
      serializable.push(element);
    } catch {
      // Never hide an element whose final OOXML cannot be constructed.
    }
  }
  return serializable;
}

function sameIds(
  elements: readonly PreparedNativeElement[],
  ids: readonly string[]
): boolean {
  return (
    elements.length === ids.length &&
    elements.every((element, index) => element.source.id === ids[index])
  );
}

export async function prepareSlideLayer(
  html: string,
  slideNumber: number,
  chromeExecutable: string,
  embeddedTypefaceFamilies: readonly string[] = []
): Promise<PreparedSlideOutcome> {
  // Network access is permitted only in this validated server-side collector.
  // The resulting document is self-contained before isolated Chrome receives it.
  // The collector returns two documents on purpose. Only the sanitized form is
  // preflighted, so untrusted authored HTML can never self-authorize a data
  // font face; the verified form carrying collector-built @font-face rules is
  // used only after that preflight succeeds.
  const fontCollection = await getGoogleFontsCollector().prepareHtml(html);
  const fontRendering = {
    browserFontFilesCollected: fontCollection.fontFiles,
    browserCollectionFailures: fontCollection.failures.length,
  };
  const preflight = preflightAuthoredHtmlForHybrid(
    fontCollection.htmlForPreflight
  );
  if (!preflight.ok) {
    return {
      layer: null,
      quality: imageFallbackSlide(
        slideNumber,
        preflight.reason ?? "html-preflight-failed"
      ),
      fontRendering,
    };
  }
  const collectedHtml = fontCollection.htmlForChrome;
  const extractionOptions = {
    chromeExecutable,
    timeoutMs: HYBRID_BROWSER_PASS_TIMEOUT_MS,
  };
  const contract = await extractAuthoredSlideDom(collectedHtml, extractionOptions);
  let nativeLayoutElements = contract.elements;
  try {
    const layoutContract = await extractAuthoredSlideDom(collectedHtml, {
      ...extractionOptions,
      fontLayoutMode: "powerpoint",
    });
    const merged = mergePowerPointTextLayout(
      contract.elements,
      layoutContract.elements,
      { embeddedTypefaceFamilies }
    );
    nativeLayoutElements = merged.elements;
  } catch {
    // The mapped-font pass is an accuracy enhancement. Source extraction and
    // fidelity backplate remain a safe compatible fallback if it cannot run.
  }
  const prepared = preSerializePreparedElements(
    await prepareNativeElements(nativeLayoutElements, {
      includeRasterText: true,
      includeRasterShapes: true,
    }),
    { embeddedTypefaceFamilies }
  );
  let selected = selectLayerSafeNativeElements(
    contract.elements,
    prepared,
    undefined,
    // Text remains editable above the residual backplate. Shapes only move to
    // the native layer when doing so preserves their authored stacking order;
    // otherwise translucent ancestor cards would wash out later SVG artwork.
    {
      promoteTextAboveRaster: true,
      promoteShapesAboveRaster: false,
      retainedChildPaint: "slide-root",
    }
  );
  if (!selected.length) {
    return {
      layer: null,
      quality: imageFallbackSlide(slideNumber, "no-safe-native-elements"),
      fontRendering,
    };
  }

  let backplate = await renderAuthoredBackplate(
    collectedHtml,
    contract,
    selected.map((element) => element.source.id),
    extractionOptions
  );
  const applied = new Set(backplate.appliedPromotedElementIds);
  const finalSelected = selectLayerSafeNativeElements(
    contract.elements,
    prepared,
    applied,
    {
      promoteTextAboveRaster: true,
      promoteShapesAboveRaster: false,
      retainedChildPaint: "slide-root",
    }
  );
  if (!finalSelected.length) {
    return {
      layer: null,
      quality: imageFallbackSlide(slideNumber, "backplate-identity-mismatch"),
      fontRendering,
    };
  }

  if (!sameIds(finalSelected, backplate.appliedPromotedElementIds)) {
    selected = finalSelected;
    backplate = await renderAuthoredBackplate(
      collectedHtml,
      contract,
      selected.map((element) => element.source.id),
      extractionOptions
    );
    if (!sameIds(selected, backplate.appliedPromotedElementIds)) {
      return {
        layer: null,
        quality: imageFallbackSlide(slideNumber, "backplate-identity-mismatch"),
        fontRendering,
      };
    }
  } else {
    selected = finalSelected;
  }
  const selectedIds = new Set(
    selected.map((element) => element.source.id)
  );
  const fallbackElements = contract.elements
    .filter((element) => !selectedIds.has(element.id))
    .map(fallbackElementForContractElement);

  return {
    layer: {
      slideNumber,
      backplatePng: backplate.backplatePng,
      elements: selected,
    },
    quality: {
      slideNumber,
      editable: selected.length > 0,
      imageFallback: selected.length === 0,
      nativeTextElements: selected.filter((element) => element.kind === "text")
        .length,
      nativeShapeElements: selected.filter((element) => element.kind === "shape")
        .length,
      nativeGroupElements: 0,
      nativeImageElements: selected.filter((element) => element.kind === "image")
        .length,
      rasterFallbackElements: fallbackElements.length,
      fallbackReasons: fallbackElements.flatMap((element) => element.reasons),
      fallbackElements,
    },
    fontRendering,
  };
}
