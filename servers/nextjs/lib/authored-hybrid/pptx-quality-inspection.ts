import { decodeXmlAttributeValue, readPptxArchive } from "./pptx-archive.ts";
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

export interface AuthoredHybridNativeReadbackElement {
  readonly slideNumber: number;
  readonly elementId: string;
  readonly kind: "text" | "shape" | "image";
  readonly latinTypefaces: readonly string[];
  readonly eastAsianTypefaces: readonly string[];
  readonly lineBreakCount: number;
}

function typefaces(xml: string, tag: "latin" | "ea"): string[] {
  return [...xml.matchAll(new RegExp(`<a:${tag}\\b[^>]*\\btypeface="([^"]*)"`, "g"))]
    .map((match) => decodeXmlAttributeValue(match[1]).trim())
    .filter((value, index, values) => value.length > 0 && values.indexOf(value) === index);
}

function hybridIdentity(name: string): {
  kind: "text" | "shape" | "image";
  elementId: string;
} | null {
  const decoded = decodeXmlAttributeValue(name).trim();
  const match = /^Presenton hybrid (?:native |raster )?(text|shape|image) (.+?)(?:-container)?(?: (?:outline|shadow \d+|(?:top|right|bottom|left) border))?$/.exec(decoded);
  if (!match) return null;
  return {
    kind: match[1] as "text" | "shape" | "image",
    elementId: match[2],
  };
}

/**
 * Structural native readback keyed by the declared QID stored in cNvPr.name.
 * This reports the actual OOXML run faces and explicit breaks; it does not
 * substitute for PowerPoint COM save/reopen/render evidence.
 */
export function inspectPptxNativeElementReadback(
  pptxBytes: Buffer
): AuthoredHybridNativeReadbackElement[] {
  const archive = readPptxArchive(pptxBytes);
  const results: AuthoredHybridNativeReadbackElement[] = [];
  for (const [name, bytes] of archive) {
    const slideMatch = /^ppt\/slides\/slide(\d+)\.xml$/.exec(name);
    if (!slideMatch) continue;
    const slideNumber = Number(slideMatch[1]);
    const xml = bytes.toString("utf8");
    for (const block of [
      ...xml.matchAll(/<p:sp\b[\s\S]*?<\/p:sp>/g),
      ...xml.matchAll(/<p:pic\b[\s\S]*?<\/p:pic>/g),
    ].map((match) => match[0])) {
      const rawName = block.match(/<p:cNvPr\b[^>]*\bname="([^"]*)"/)?.[1];
      if (!rawName) continue;
      const identity = hybridIdentity(rawName);
      if (!identity || results.some((entry) =>
        entry.slideNumber === slideNumber &&
        entry.elementId === identity.elementId &&
        entry.kind === identity.kind
      )) continue;
      results.push({
        slideNumber,
        elementId: identity.elementId,
        kind: identity.kind,
        latinTypefaces: typefaces(block, "latin"),
        eastAsianTypefaces: typefaces(block, "ea"),
        lineBreakCount: occurrences(block, /<a:br\/>/g),
      });
    }
  }
  return results.sort((left, right) =>
    left.slideNumber - right.slideNumber ||
    left.elementId.localeCompare(right.elementId) ||
    left.kind.localeCompare(right.kind)
  );
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
      return {
        slideNumber,
        editable,
        imageFallback: !editable,
        nativeTextElements,
        nativeShapeElements,
        nativeGroupElements,
        nativeImageElements,
        rasterFallbackElements: fallbackElements.length,
        logicalCoverage: {
          nativeText: 0,
          nativeImage: 0,
          nativeShape: 0,
          nativeGroup: 0,
          rasterRemaining: 0,
        },
        ooxmlReadback: {
          status: "passed" as const,
          nativeText: nativeTextElements,
          nativeImage: nativeImageElements,
          nativeShape: nativeShapeElements,
          nativeGroup: nativeGroupElements,
          rasterRemaining: fullSlideRasterPictures.length + rasterBackgrounds,
        },
        backplateCount: backplates.length,
        typographyIssues: [],
        fallbackElements,
        fallbackReasons: fallbackElements.flatMap((element) => element.reasons),
      };
    })
  );
}
