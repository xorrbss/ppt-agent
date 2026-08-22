import {
  decodeXmlAttributeValue,
  readPptxArchive,
  relationshipTargetPath,
} from "./pptx-archive.ts";
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
  readonly kind: "text" | "shape" | "image" | "table" | "chart";
  readonly latinTypefaces: readonly string[];
  readonly eastAsianTypefaces: readonly string[];
  readonly lineBreakCount: number;
  readonly chart?: {
    readonly chartType: "bar" | "column" | "line" | "area" | "pie" | "donut";
    readonly categories: readonly string[];
    readonly series: readonly {
      readonly name: string;
      readonly values: readonly number[];
    }[];
    readonly hasCategoryAxis: boolean;
    readonly hasValueAxis: boolean;
    readonly legendPosition: "bottom" | "top" | "right" | "left" | null;
  };
  readonly table?: { readonly rows: number; readonly columns: number; readonly cellTexts: readonly string[] };
  readonly imageCrop?: { readonly left: number; readonly top: number; readonly right: number; readonly bottom: number };
}

function typefaces(xml: string, tag: "latin" | "ea"): string[] {
  return [...xml.matchAll(new RegExp(`<a:${tag}\\b[^>]*\\btypeface="([^"]*)"`, "g"))]
    .map((match) => decodeXmlAttributeValue(match[1]).trim())
    .filter((value, index, values) => value.length > 0 && values.indexOf(value) === index);
}

function hybridIdentity(name: string): {
  kind: "text" | "shape" | "image" | "table" | "chart";
  elementId: string;
} | null {
  const decoded = decodeXmlAttributeValue(name).trim();
  const match = /^Presenton hybrid (?:native |raster )?(text|shape|image|table|chart) (.+?)(?:-container)?(?: (?:outline|shadow \d+|(?:top|right|bottom|left) border))?$/.exec(decoded);
  if (!match) return null;
  return {
    kind: match[1] as "text" | "shape" | "image" | "table" | "chart",
    elementId: match[2],
  };
}

function chartPointValues(xml: string): string[] {
  return [...xml.matchAll(/<c:pt\b[^>]*\bidx="(\d+)"[^>]*>[\s\S]*?<c:v>([\s\S]*?)<\/c:v>[\s\S]*?<\/c:pt>/g)]
    .sort((left, right) => Number(left[1]) - Number(right[1]))
    .map((match) => decodeXmlAttributeValue(match[2]) ?? "");
}

function chartReadback(xml: string): NonNullable<AuthoredHybridNativeReadbackElement["chart"]> | null {
  const chartType = /<c:doughnutChart\b/.test(xml)
    ? "donut" as const
    : /<c:pieChart\b/.test(xml)
      ? "pie" as const
      : /<c:lineChart\b/.test(xml)
        ? "line" as const
        : /<c:areaChart\b/.test(xml)
          ? "area" as const
          : /<c:barChart\b[\s\S]*?<c:barDir\b[^>]*\bval="bar"/.test(xml)
            ? "bar" as const
            : /<c:barChart\b/.test(xml)
              ? "column" as const
              : null;
  if (!chartType) return null;
  const series = [...xml.matchAll(/<c:ser\b[\s\S]*?<\/c:ser>/g)].map((match) => {
    const block = match[0];
    const name = block.match(/<c:tx>[\s\S]*?<c:v>([\s\S]*?)<\/c:v>[\s\S]*?<\/c:tx>/)?.[1] ?? "";
    const valuesBlock = block.match(/<c:val>([\s\S]*?)<\/c:val>/)?.[1] ?? "";
    return {
      name: decodeXmlAttributeValue(name) ?? "",
      values: chartPointValues(valuesBlock)
        .map(Number)
        .filter((value) => Number.isFinite(value)),
    };
  });
  const categoryBlock = xml.match(/<c:cat>([\s\S]*?)<\/c:cat>/)?.[1] ?? "";
  const legend = xml.match(/<c:legendPos\b[^>]*\bval="([btrl])"/)?.[1];
  return {
    chartType,
    categories: chartPointValues(categoryBlock),
    series,
    hasCategoryAxis: /<c:catAx\b/.test(xml),
    hasValueAxis: /<c:valAx\b/.test(xml),
    legendPosition: legend
      ? ({ b: "bottom", t: "top", r: "right", l: "left" } as const)[legend as "b" | "t" | "r" | "l"]
      : null,
  };
}

function relatedChartXml(
  archive: ReadonlyMap<string, Buffer>,
  slideNumber: number,
  block: string,
): string | null {
  const rawId = block.match(/<c:chart\b[^>]*\br:id="([^"]+)"/)?.[1];
  if (!rawId) return null;
  const relationshipId = decodeXmlAttributeValue(rawId);
  if (!relationshipId) return null;
  const relsPath = `ppt/slides/_rels/slide${slideNumber}.xml.rels`;
  const rels = archive.get(relsPath)?.toString("utf8");
  if (!rels) return null;
  const relationship = [...rels.matchAll(/<(?:[A-Za-z_][\w.-]*:)?Relationship\b[^>]*\/?\s*>/gi)]
    .map((match) => match[0])
    .find((entry) => decodeXmlAttributeValue(
      entry.match(/\bId="([^"]+)"/)?.[1] ?? "",
    ) === relationshipId);
  const rawTarget = relationship?.match(/\bTarget="([^"]+)"/)?.[1];
  if (!rawTarget) return null;
  const decodedTarget = decodeXmlAttributeValue(rawTarget);
  if (!decodedTarget) return null;
  const target = relationshipTargetPath(relsPath, decodedTarget);
  return target ? archive.get(target)?.toString("utf8") ?? null : null;
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
      ...xml.matchAll(/<p:graphicFrame\b[\s\S]*?<\/p:graphicFrame>/g),
    ].map((match) => match[0])) {
      const rawName = block.match(/<p:cNvPr\b[^>]*\bname="([^"]*)"/)?.[1];
      if (!rawName) continue;
      const identity = hybridIdentity(rawName);
      if (!identity || results.some((entry) =>
        entry.slideNumber === slideNumber &&
        entry.elementId === identity.elementId &&
        entry.kind === identity.kind
      )) continue;
      const chartXml = identity.kind === "chart"
        ? relatedChartXml(archive, slideNumber, block)
        : null;
      const chart = chartXml ? chartReadback(chartXml) : null;
      const tableRows = identity.kind === "table" ? occurrences(block, /<a:tr\b/g) : 0;
      const firstRow = identity.kind === "table" ? block.match(/<a:tr\b[\s\S]*?<\/a:tr>/)?.[0] ?? "" : "";
      const sourceRect = identity.kind === "image" ? block.match(/<a:srcRect\b[^>]*\/>/)?.[0] : undefined;
      results.push({
        slideNumber,
        elementId: identity.elementId,
        kind: identity.kind,
        latinTypefaces: typefaces(chartXml ?? block, "latin"),
        eastAsianTypefaces: typefaces(chartXml ?? block, "ea"),
        lineBreakCount: occurrences(block, /<a:br\/>/g),
        ...(chart ? { chart } : {}),
        ...(identity.kind === "table" ? { table: { rows: tableRows, columns: occurrences(firstRow, /<a:tc\b/g), cellTexts: [...block.matchAll(/<a:t>([\s\S]*?)<\/a:t>/g)].map((match) => decodeXmlAttributeValue(match[1])) } } : {}),
        ...(sourceRect ? { imageCrop: { left: Number(sourceRect.match(/\bl="(\d+)"/)?.[1] ?? 0), top: Number(sourceRect.match(/\bt="(\d+)"/)?.[1] ?? 0), right: Number(sourceRect.match(/\br="(\d+)"/)?.[1] ?? 0), bottom: Number(sourceRect.match(/\bb="(\d+)"/)?.[1] ?? 0) } } : {}),
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

  const nativeReadback = inspectPptxNativeElementReadback(pptxBytes);
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
      const nativeChartElements = occurrences(
        xml,
        /<p:graphicFrame\b[\s\S]*?<c:chart\b/g,
      );
      const nativeTableElements = occurrences(xml, /<p:graphicFrame\b[\s\S]*?<a:tbl\b/g);
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
        nativeTextElements + nativeShapeElements + nativeImageElements +
          nativeChartElements + nativeTableElements > 0;
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
        nativeChartElements,
        nativeTableElements,
        nativeImageCropElements: pictures.filter((block) => /<a:srcRect\b[^>]*\/>/.test(block)).length,
        qidCoverage: nativeReadback.filter((entry) => entry.slideNumber === slideNumber).map((entry) => ({ elementId: entry.elementId, kind: entry.kind, logical: "native" as const, ooxml: "present" as const, com: "not-run" as const })),
        nativeGroupElements,
        nativeImageElements,
        rasterFallbackElements: fallbackElements.length,
        logicalCoverage: {
          nativeText: 0,
          nativeImage: 0,
          nativeShape: 0,
          nativeChart: 0,
          nativeGroup: 0,
          rasterRemaining: 0,
        },
        ooxmlReadback: {
          status: "passed" as const,
          nativeText: nativeTextElements,
          nativeImage: nativeImageElements,
          nativeShape: nativeShapeElements,
          nativeChart: nativeChartElements,
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

/** Preserve stage2 structural counters after the generic logical/readback join. */
export function attachAuthoredHybridStage2ReadbackMetadata<
  T extends PresentationExportQualityReport,
>(quality: T, pptxBytes: Buffer): T {
  const structural = inspectPptxEditability(pptxBytes);
  const bySlide = new Map(structural.slides.map((slide) => [slide.slideNumber, slide as typeof slide & { nativeTableElements?: number; nativeImageCropElements?: number }]));
  return {
    ...quality,
    slides: quality.slides.map((slide) => {
      const observed = bySlide.get(slide.slideNumber);
      return {
        ...slide,
        nativeTableElements: observed?.nativeTableElements ?? 0,
        nativeImageCropElements: observed?.nativeImageCropElements ?? 0,
      };
    }),
  } as T;
}
