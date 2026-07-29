import { readPptxArchive } from "./pptx-archive.ts";
import {
  createPresentationExportQuality,
  type PresentationExportFallbackElement,
  type PresentationExportQualityReport,
} from "../presentation-export-quality.ts";

const DEFAULT_SLIDE_WIDTH_EMU = 12_192_000;
const DEFAULT_SLIDE_HEIGHT_EMU = 6_858_000;

function occurrences(xml: string, pattern: RegExp): number {
  return [...xml.matchAll(pattern)].length;
}

function presentationSlideSize(
  presentationXml: string | undefined
): { width: number; height: number } {
  const match =
    presentationXml?.match(
      /<p:sldSz\b[^>]*\bcx="(\d+)"[^>]*\bcy="(\d+)"/
    ) ??
    presentationXml?.match(
      /<p:sldSz\b[^>]*\bcy="(\d+)"[^>]*\bcx="(\d+)"/
    );
  if (!match) {
    return {
      width: DEFAULT_SLIDE_WIDTH_EMU,
      height: DEFAULT_SLIDE_HEIGHT_EMU,
    };
  }
  const cxBeforeCy = match[0].indexOf("cx=") < match[0].indexOf("cy=");
  return cxBeforeCy
    ? { width: Number(match[1]), height: Number(match[2]) }
    : { width: Number(match[2]), height: Number(match[1]) };
}

function pictureBlocks(xml: string): string[] {
  return [...xml.matchAll(/<p:pic\b[\s\S]*?<\/p:pic>/g)].map(
    (match) => match[0]
  );
}

function pictureIdentity(block: string, index: number): string {
  const id = block.match(/<p:cNvPr\b[^>]*\bid="(\d+)"/)?.[1];
  return `ooxml:picture:${id ?? index + 1}`;
}

function isHybridBackplate(block: string): boolean {
  return /<p:cNvPr\b[^>]*\bname="Presenton hybrid backplate"/.test(block);
}

function coversWholeSlide(
  block: string,
  slideSize: { width: number; height: number }
): boolean {
  const transform = block.match(/<a:xfrm\b[^>]*>([\s\S]*?)<\/a:xfrm>/)?.[1];
  if (!transform) return false;
  const offset = transform.match(/<a:off\b[^>]*\bx="(-?\d+)"[^>]*\by="(-?\d+)"/);
  const extent = transform.match(/<a:ext\b[^>]*\bcx="(\d+)"[^>]*\bcy="(\d+)"/);
  return (
    offset?.[1] === "0" &&
    offset[2] === "0" &&
    Number(extent?.[1]) === slideSize.width &&
    Number(extent?.[2]) === slideSize.height
  );
}

/**
 * Structural QA for generated files. This verifies native OOXML objects actually
 * exist; it does not infer the converter's full raster fallback reason set.
 */
export function inspectPptxEditability(
  pptxBytes: Buffer
): PresentationExportQualityReport {
  const archive = readPptxArchive(pptxBytes);
  const slideSize = presentationSlideSize(
    archive.get("ppt/presentation.xml")?.toString("utf8")
  );
  const slideEntries = [...archive.entries()]
    .map(([name, bytes]) => {
      const match = /^ppt\/slides\/slide(\d+)\.xml$/.exec(name);
      return match ? { slideNumber: Number(match[1]), bytes } : null;
    })
    .filter(
      (entry): entry is { slideNumber: number; bytes: Buffer } => entry !== null
    )
    .sort((a, b) => a.slideNumber - b.slideNumber);

  return createPresentationExportQuality(
    "hybrid",
    slideEntries.map(({ slideNumber, bytes }) => {
      const xml = bytes.toString("utf8");
      const nativeTextElements = occurrences(xml, /<p:txBody\b/g);
      const allShapes = occurrences(xml, /<p:sp\b/g);
      const connectorElements = occurrences(xml, /<p:cxnSp\b/g);
      const nativeShapeElements =
        Math.max(0, allShapes - nativeTextElements) + connectorElements;
      const nativeGroupElements = occurrences(xml, /<p:grpSp\b/g);
      const pictures = pictureBlocks(xml);
      const backplates = pictures
        .map((block, index) => ({ block, index }))
        .filter(({ block }) => isHybridBackplate(block));
      const fullSlideRasterPictures = pictures
        .map((block, index) => ({ block, index }))
        .filter(
          ({ block }) =>
            !isHybridBackplate(block) && coversWholeSlide(block, slideSize)
        );
      const rasterBackgrounds = /<p:bg\b[\s\S]*?<a:blip\b/.test(xml) ? 1 : 0;
      const nativeImageElements = Math.max(
        0,
        pictures.length - backplates.length - fullSlideRasterPictures.length
      );
      const editable =
        nativeTextElements + nativeShapeElements + nativeImageElements > 0;
      const fallbackElements: PresentationExportFallbackElement[] = [
        ...backplates.map(({ block, index }) => ({
          elementId: pictureIdentity(block, index),
          candidateKind: "slide" as const,
          reasons: ["residual-backplate-present"],
        })),
        ...fullSlideRasterPictures.map(({ block, index }) => ({
          elementId: pictureIdentity(block, index),
          candidateKind: "slide" as const,
          reasons: ["full-slide-image-fallback"],
        })),
        ...(rasterBackgrounds
          ? [
              {
                elementId: "ooxml:slide-background",
                candidateKind: "slide" as const,
                reasons: ["raster-background-present"],
              },
            ]
          : []),
      ];
      if (!editable && fallbackElements.length === 0) {
        fallbackElements.push({
          elementId: "ooxml:no-native-content",
          candidateKind: "slide",
          reasons: ["no-native-ooxml-elements"],
        });
      }
      return {
        slideNumber,
        editable,
        imageFallback: !editable,
        nativeTextElements,
        nativeShapeElements,
        nativeGroupElements,
        nativeImageElements,
        rasterFallbackElements: fallbackElements.length,
        fallbackElements,
        fallbackReasons: fallbackElements.flatMap((element) => element.reasons),
      };
    })
  );
}
