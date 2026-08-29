import { createHash } from "node:crypto";

import {
  decodeXmlAttributeValue,
  readPptxArchive,
  relationshipTargetPath,
} from "./pptx-archive.ts";
import {
  createPresentationExportQuality,
  type PresentationExportFallbackElement,
  type PresentationExportNativeElementReadback,
  type PresentationExportQualityReport,
} from "../presentation-export-quality.ts";
import {
  AUTHORED_NATIVE_OWNER_ALTERNATIVE_TEXT_PREFIX,
  parseAuthoredNativeOwnerAlternativeText,
} from "./native-plan.ts";
import {
  attachAuthoredNativeDispositionReceipt,
  type WithAuthoredNativeDisposition,
} from "./native-disposition.ts";

const DEFAULT_SLIDE_WIDTH_EMU = 12_192_000;
const DEFAULT_SLIDE_HEIGHT_EMU = 6_858_000;
const BASE32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

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

export interface AuthoredHybridNativeReadbackElement extends PresentationExportNativeElementReadback {
  readonly slideNumber: number;
  readonly elementId: string;
  readonly kind: "text" | "shape" | "image" | "table" | "chart";
  readonly capabilityId?: string;
  readonly latinTypefaces: readonly string[];
  readonly eastAsianTypefaces: readonly string[];
  readonly lineBreakCount: number;
  readonly renderedBounds?: {
    readonly x: number;
    readonly y: number;
    readonly width: number;
    readonly height: number;
  };
  readonly autoFit?: "none" | "shrink";
  readonly textRunMap?: readonly {
    readonly runIndex: number;
    readonly runId: string;
    readonly utf16Start: number;
    readonly utf16Length: number;
    readonly textDigest: string;
  }[];
  readonly textRunStyles?: readonly {
    readonly runIndex: number;
    readonly runId: string;
    readonly fontFamily: string;
    readonly eastAsianTypeface: string;
    readonly fontSizePt: number;
    readonly bold: boolean;
    readonly italic: boolean;
    readonly underline: boolean;
    readonly color: string;
  }[];
  readonly advancedTextDigest?: string;
  readonly chart?: {
    readonly chartType: "bar" | "column" | "line" | "area" | "pie" | "donut";
    readonly chartStyle: number;
    readonly categories: readonly string[];
    readonly series: readonly {
      readonly name: string;
      readonly values: readonly number[];
      readonly color: string;
      readonly pointColors: readonly string[];
    }[];
    readonly categoryAxis: {
      readonly present: boolean;
      readonly title: string | null;
      readonly latinTypefaces: readonly string[];
      readonly eastAsianTypefaces: readonly string[];
    };
    readonly valueAxis: {
      readonly present: boolean;
      readonly title: string | null;
      readonly latinTypefaces: readonly string[];
      readonly eastAsianTypefaces: readonly string[];
    };
    readonly legend: {
      readonly present: boolean;
      readonly title: null;
      readonly position: "bottom" | "top" | "right" | "left" | null;
      readonly latinTypefaces: readonly string[];
      readonly eastAsianTypefaces: readonly string[];
    };
    readonly title: string | null;
    readonly titleLatinTypefaces: readonly string[];
    readonly titleEastAsianTypefaces: readonly string[];
  };
  readonly table?: {
    readonly rows: number;
    readonly columns: number;
    readonly cellTexts: readonly string[];
    readonly rowHeightsPt: readonly number[];
    readonly columnWidthsPt: readonly number[];
    readonly mergedCells: readonly {
      readonly row: number;
      readonly column: number;
      readonly rowSpan: number;
      readonly columnSpan: number;
    }[];
    readonly cellStyles: readonly (readonly {
      readonly fill: string | null;
      readonly horizontalAlign: "left" | "center" | "right" | "justify";
      readonly verticalAlign: "top" | "middle" | "bottom";
      readonly borders: Readonly<Record<"top" | "right" | "bottom" | "left", {
        readonly color: string;
        readonly widthPt: number;
      } | null>>;
    }[])[];
    readonly tableStyleDigest: string;
  };
  readonly imageCrop?: { readonly left: number; readonly top: number; readonly right: number; readonly bottom: number };
}

function typefaces(xml: string, tag: "latin" | "ea"): string[] {
  return [...xml.matchAll(new RegExp(`<a:${tag}\\b[^>]*\\btypeface="([^"]*)"`, "g"))]
    .map((match) => decodeXmlAttributeValue(match[1] ?? ""))
    .filter((value): value is string => value !== null)
    .map((value) => value.trim())
    .filter((value, index, values) => value.length > 0 && values.indexOf(value) === index);
}

function serializedNativeKind(block: string):
  | "text"
  | "shape"
  | "image"
  | "table"
  | "chart"
  | null {
  if (block.startsWith("<p:pic")) return "image";
  if (block.startsWith("<p:grpSp")) return "shape";
  if (block.startsWith("<p:graphicFrame")) {
    if (/<c:chart\b/.test(block)) return "chart";
    if (/<a:tbl\b/.test(block)) return "table";
    return null;
  }
  if (block.startsWith("<p:sp")) {
    return /<p:txBody\b/.test(block) ? "text" : "shape";
  }
  return null;
}

function groupHeaderBlocks(xml: string): string[] {
  // Match each group header independently instead of consuming through the
  // closing tag. This keeps nested groups visible as separate QID owners while
  // still exposing the exact group transform needed for geometry readback.
  return [...xml.matchAll(
    /<p:grpSp\b[^>]*>\s*<p:nvGrpSpPr>[\s\S]*?<\/p:nvGrpSpPr>\s*<p:grpSpPr>[\s\S]*?<\/p:grpSpPr>/g,
  )].map((match) => match[0]);
}

function hybridIdentity(name: string, serializedKind: ReturnType<typeof serializedNativeKind>): {
  kind: "text" | "shape" | "image" | "table" | "chart";
  elementId: string;
} | null {
  const decodedValue = decodeXmlAttributeValue(name);
  if (decodedValue === null) return null;
  const decoded = decodedValue.trim();
  // Text containers, shadows, outlines, and split borders are physical paint
  // fragments of the primary native object, not additional semantic QID
  // owners. The corresponding text/base object carries the logical owner.
  if (
    /-container$/.test(decoded) ||
    / (?:outline|shadow \d+|(?:top|right|bottom|left) border)$/.test(decoded)
  ) {
    return null;
  }
  const typed = /^Presenton hybrid (?:native |raster )?(text|shape|image|table|chart) ([A-Za-z][A-Za-z0-9._:-]{0,127})$/.exec(decoded);
  if (typed) {
    return {
      kind: typed[1] as "text" | "shape" | "image" | "table" | "chart",
      elementId: typed[2],
    };
  }
  // Raster-classified text/shapes can still be admitted as native after the
  // bounded editable pre-serializer proves them safe. Their historical name
  // carries the source disposition but omits the emitted OOXML kind; derive
  // that kind from the exact serialized block instead of dropping ownership.
  const raster = /^Presenton hybrid raster ([A-Za-z][A-Za-z0-9._:-]{0,127})$/.exec(decoded);
  return raster && serializedKind
    ? { kind: serializedKind, elementId: raster[1] }
    : null;
}

function chartPointValues(xml: string): string[] {
  return [...xml.matchAll(/<c:pt\b[^>]*\bidx="(\d+)"[^>]*>[\s\S]*?<c:v>([\s\S]*?)<\/c:v>[\s\S]*?<\/c:pt>/g)]
    .sort((left, right) => Number(left[1]) - Number(right[1]))
    .map((match) => decodeXmlAttributeValue(match[2]) ?? "");
}

function canonicalValue(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalValue).join(",")}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map((key) =>
    `${JSON.stringify(key)}:${canonicalValue(record[key])}`
  ).join(",")}}`;
}

function canonicalDigest(value: unknown): string {
  return `sha256:${createHash("sha256").update(canonicalValue(value), "utf8").digest("hex")}`;
}

function decodeBase32Utf8(value: string): string | null {
  if (!value || !/^[A-Z2-7]+$/.test(value)) return null;
  let bits = 0;
  let bitCount = 0;
  const bytes: number[] = [];
  for (const character of value) {
    const index = BASE32_ALPHABET.indexOf(character);
    if (index < 0) return null;
    bits = (bits << 5) | index;
    bitCount += 5;
    if (bitCount >= 8) {
      bytes.push((bits >>> (bitCount - 8)) & 255);
      bitCount -= 8;
      bits &= bitCount === 0 ? 0 : (1 << bitCount) - 1;
    }
  }
  if (bitCount > 0 && bits !== 0) return null;
  const decoded = Buffer.from(bytes).toString("utf8");
  return Buffer.from(decoded, "utf8").equals(Buffer.from(bytes)) ? decoded : null;
}

function xmlOpeningAttribute(xml: string, attribute: string): string | null {
  const match = xml.match(new RegExp(`\\b${attribute}="([^"]*)"`));
  return match ? decodeXmlAttributeValue(match[1]) : null;
}

function renderedBounds(block: string): AuthoredHybridNativeReadbackElement["renderedBounds"] {
  const transform = block.match(/<(?:a|p):xfrm\b[^>]*>[\s\S]*?<\/(?:a|p):xfrm>/)?.[0];
  const offset = transform?.match(/<a:off\b[^>]*\bx="(-?\d+)"[^>]*\by="(-?\d+)"/);
  const extent = transform?.match(/<a:ext\b[^>]*\bcx="(\d+)"[^>]*\bcy="(\d+)"/);
  if (!offset || !extent) return undefined;
  const px = (emu: string) => Math.round((Number(emu) / 9_525) * 1_000_000) / 1_000_000;
  return { x: px(offset[1]), y: px(offset[2]), width: px(extent[1]), height: px(extent[2]) };
}

function nativeOwnerReceipt(
  block: string,
  slideNumber: number,
  identity: { kind: AuthoredHybridNativeReadbackElement["kind"]; elementId: string },
): ReturnType<typeof parseAuthoredNativeOwnerAlternativeText> {
  const cNvPr = block.match(/<p:cNvPr\b[^>]*>/)?.[0] ?? "";
  const description = xmlOpeningAttribute(cNvPr, "descr");
  if (!description?.startsWith(AUTHORED_NATIVE_OWNER_ALTERNATIVE_TEXT_PREFIX)) return null;
  const owner = parseAuthoredNativeOwnerAlternativeText(description);
  if (!owner) throw new Error(`Hybrid ${identity.kind} ${identity.elementId} has an invalid native owner receipt.`);
  if (owner.slideNumber !== slideNumber || owner.elementId !== identity.elementId || owner.kind !== identity.kind) {
    throw new Error(`Hybrid ${identity.kind} ${identity.elementId} owner receipt identity mismatch.`);
  }
  return owner;
}

function advancedTextReadback(
  block: string,
  identity: { kind: AuthoredHybridNativeReadbackElement["kind"]; elementId: string },
  owner: NonNullable<ReturnType<typeof parseAuthoredNativeOwnerAlternativeText>> | null,
): Pick<AuthoredHybridNativeReadbackElement, "autoFit" | "textRunMap" | "textRunStyles" | "advancedTextDigest"> | null {
  if (identity.kind !== "text") return null;
  const extension = block.match(/<pt:advancedText\b[^>]*\/?\s*>/)?.[0] ?? null;
  if (!owner) {
    if (extension) throw new Error(`Hybrid text ${identity.elementId} has an orphan advanced-text extension.`);
    return null;
  }
  if (owner.capabilityId !== "editable.text.advanced.v1") {
    if (extension || owner.autoFit !== undefined || owner.textRunMap !== undefined || owner.textRunStyles !== undefined || owner.advancedTextDigest !== undefined) {
      throw new Error(`Hybrid text ${identity.elementId} has an invalid advanced-text owner boundary.`);
    }
    return null;
  }
  if (!extension || !owner.textRunMap || !owner.textRunStyles || !owner.autoFit || !owner.advancedTextDigest) {
    throw new Error(`Hybrid text ${identity.elementId} advanced-text receipt is incomplete.`);
  }
  const extensionDigest = xmlOpeningAttribute(extension, "digest");
  const encodedPayload = xmlOpeningAttribute(extension, "payload");
  if (!encodedPayload || !/^[A-Za-z0-9_-]+$/.test(encodedPayload) ||
    Buffer.from(encodedPayload, "base64url").toString("base64url") !== encodedPayload) {
    throw new Error(`Hybrid text ${identity.elementId} advanced-text payload encoding is invalid.`);
  }
  let parsedPayload: unknown;
  const decodedPayload = Buffer.from(encodedPayload, "base64url").toString("utf8");
  try { parsedPayload = JSON.parse(decodedPayload); } catch {
    throw new Error(`Hybrid text ${identity.elementId} advanced-text payload is invalid.`);
  }
  const expectedPayload = {
    textRunMap: owner.textRunMap,
    textRunStyles: owner.textRunStyles,
    autoFit: owner.autoFit,
  };
  if (decodedPayload !== canonicalValue(parsedPayload) ||
    canonicalValue(parsedPayload) !== canonicalValue(expectedPayload) ||
    extensionDigest !== owner.advancedTextDigest ||
    canonicalDigest(expectedPayload) !== owner.advancedTextDigest) {
    throw new Error(`Hybrid text ${identity.elementId} advanced-text carriers disagree.`);
  }
  const bodyProperties = block.match(/<a:bodyPr\b[^>]*>[\s\S]*?<\/a:bodyPr>/)?.[0] ?? "";
  const normAutofitCount = occurrences(bodyProperties, /<a:normAutofit(?:\s[^>]*)?\/>/g);
  const noAutofitCount = occurrences(bodyProperties, /<a:noAutofit(?:\s[^>]*)?\/>/g);
  if (normAutofitCount + noAutofitCount !== 1 ||
    (owner.autoFit === "shrink" && normAutofitCount !== 1) ||
    (owner.autoFit === "none" && noAutofitCount !== 1)) {
    throw new Error(`Hybrid text ${identity.elementId} autofit OOXML disagrees with its owner receipt.`);
  }
  return {
    autoFit: owner.autoFit,
    textRunMap: owner.textRunMap,
    textRunStyles: owner.textRunStyles,
    advancedTextDigest: owner.advancedTextDigest,
  };
}

function chartColor(xml: string): string {
  const value = xml.match(/<a:srgbClr\b[^>]*\bval="([0-9A-Fa-f]{6})"/)?.[1];
  return value ? `#${value.toUpperCase()}` : "";
}

/** OOXML dPt overrides the series fill; without it the series fill owns every point. */
function chartPointColors(seriesXml: string, count: number, seriesColor: string): string[] {
  const colors = Array.from({ length: count }, () => seriesColor);
  for (const match of seriesXml.matchAll(/<c:dPt\b[\s\S]*?<c:idx\b[^>]*\bval="(\d+)"[^>]*\/>[\s\S]*?<\/c:dPt>/g)) {
    const index = Number(match[1]);
    const color = chartColor(match[0]);
    if (Number.isSafeInteger(index) && index >= 0 && index < colors.length && color) colors[index] = color;
  }
  return colors;
}

function chartText(block: string): string | null {
  const value = block.match(/<a:t>([\s\S]*?)<\/a:t>/)?.[1];
  return value === undefined ? null : decodeXmlAttributeValue(value);
}

function chartTextReadback(xml: string, tag: "catAx" | "valAx") {
  const block = xml.match(new RegExp(`<c:${tag}\\b[\\s\\S]*?<\\/c:${tag}>`))?.[0];
  return {
    present: Boolean(block),
    title: block ? chartText(block.match(/<c:title\b[\s\S]*?<\/c:title>/)?.[0] ?? "") : null,
    latinTypefaces: block ? typefaces(block, "latin") : [],
    eastAsianTypefaces: block ? typefaces(block, "ea") : [],
  };
}

function semanticRasterOwnerQids(xml: string): string[] {
  return [...xml.matchAll(/<p:cNvPr\b[^>]*\bname="([^"]*)"[^>]*>/g)]
    .map((match) => decodeXmlAttributeValue(match[1] ?? ""))
    .filter((value): value is string => value !== null)
    .map((value) => value.trim())
    .map((name) => /^Presenton raster visual fragment ([A-Za-z][A-Za-z0-9._:-]{0,127}) semantic-owner$/.exec(name)?.[1] ?? null)
    .filter((qid): qid is string => qid !== null);
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
  const chartStyle = Number(xml.match(/<c:style\b[^>]*\bval="(\d+)"[^>]*\/>/)?.[1] ?? 2);
  const series = [...xml.matchAll(/<c:ser\b[\s\S]*?<\/c:ser>/g)].map((match) => {
    const block = match[0];
    const name = block.match(/<c:tx>[\s\S]*?<c:v>([\s\S]*?)<\/c:v>[\s\S]*?<\/c:tx>/)?.[1] ?? "";
    const valuesBlock = block.match(/<c:val>([\s\S]*?)<\/c:val>/)?.[1] ?? "";
    const values = chartPointValues(valuesBlock)
      .map(Number)
      .filter((value) => Number.isFinite(value));
    const color = chartColor(block.match(/<c:spPr\b[\s\S]*?<\/c:spPr>/)?.[0] ?? "");
    return {
      name: decodeXmlAttributeValue(name) ?? "",
      values,
      color,
      pointColors: chartPointColors(block, values.length, color),
    };
  });
  const categoryBlock = xml.match(/<c:cat>([\s\S]*?)<\/c:cat>/)?.[1] ?? "";
  const legendBlock = xml.match(/<c:legend\b[\s\S]*?<\/c:legend>/)?.[0] ?? "";
  const legend = legendBlock.match(/<c:legendPos\b[^>]*\bval="([btrl])"/)?.[1];
  const chartPrefix = xml.slice(0, Math.max(0, xml.indexOf("<c:plotArea")));
  const titleBlock = chartPrefix.match(/<c:title\b[\s\S]*?<\/c:title>/)?.[0] ?? "";
  return {
    chartType,
    chartStyle,
    title: chartText(titleBlock),
    titleLatinTypefaces: typefaces(titleBlock, "latin"),
    titleEastAsianTypefaces: typefaces(titleBlock, "ea"),
    categories: chartPointValues(categoryBlock),
    series,
    categoryAxis: chartTextReadback(xml, "catAx"),
    valueAxis: chartTextReadback(xml, "valAx"),
    legend: {
      present: Boolean(legendBlock),
      title: null,
      position: legend
        ? ({ b: "bottom", t: "top", r: "right", l: "left" } as const)[legend as "b" | "t" | "r" | "l"]
        : null,
      latinTypefaces: typefaces(legendBlock, "latin"),
      eastAsianTypefaces: typefaces(legendBlock, "ea"),
    },
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

function relatedChartTagOwner(
  archive: ReadonlyMap<string, Buffer>,
  slideNumber: number,
  block: string,
): {
  readonly elementId: string;
  readonly kind: "chart";
  readonly capabilityId: "editable.chart.line-pie.v1";
} | null {
  const rawId = block.match(/<p:tags\b[^>]*\br:id="([^"]+)"/)?.[1];
  if (!rawId) return null;
  const relationshipId = decodeXmlAttributeValue(rawId);
  const relsPath = `ppt/slides/_rels/slide${slideNumber}.xml.rels`;
  const rels = archive.get(relsPath)?.toString("utf8");
  if (!relationshipId || !rels) throw new Error("Hybrid chart tag relationship is missing.");
  const relationship = [...rels.matchAll(/<(?:[A-Za-z_][\w.-]*:)?Relationship\b[^>]*\/?\s*>/gi)]
    .map((match) => match[0])
    .find((entry) => decodeXmlAttributeValue(
      entry.match(/\bId="([^"]+)"/)?.[1] ?? "",
    ) === relationshipId);
  if (!relationship || !/\/relationships\/tags"/i.test(relationship)) {
    throw new Error("Hybrid chart tag relationship type is invalid.");
  }
  const rawTarget = relationship.match(/\bTarget="([^"]+)"/)?.[1];
  const decodedTarget = rawTarget ? decodeXmlAttributeValue(rawTarget) : null;
  const target = decodedTarget
    ? relationshipTargetPath(relsPath, decodedTarget)
    : null;
  const tagXml = target ? archive.get(target)?.toString("utf8") : null;
  if (!tagXml) throw new Error("Hybrid chart tag part is missing.");
  const tagEntries = [...tagXml.matchAll(/<p:tag\b[^>]*\bname="([^"]+)"[^>]*\bval="([^"]*)"[^>]*\/>/g)]
    .map((match) => [
      decodeXmlAttributeValue(match[1]),
      decodeXmlAttributeValue(match[2]),
    ] as const);
  const tags = new Map(tagEntries);
  const required = [
    "PRESENTON_QID_V2", "PRESENTON_KIND_V2", "PRESENTON_OWNER_ROLE_V2",
  ];
  if (tagEntries.length !== required.length || tags.size !== required.length ||
    required.some((name) => !tags.has(name))) {
    throw new Error("Hybrid chart tag set is not exact.");
  }
  const decodeTag = (name: string, prefix: string): string | null => {
    const value = tags.get(name);
    return value?.startsWith(prefix)
      ? decodeBase32Utf8(value.slice(prefix.length))
      : null;
  };
  const elementId = decodeTag("PRESENTON_QID_V2", "Q2_");
  const kind = decodeTag("PRESENTON_KIND_V2", "K2_");
  const role = decodeTag("PRESENTON_OWNER_ROLE_V2", "R2_");
  if (!elementId || kind !== "chart" || role !== "native-chart-object" ||
    !new RegExp(`^s${String(slideNumber).padStart(2, "0")}-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`).test(elementId)) {
    throw new Error("Hybrid chart tag value is invalid.");
  }
  return {
    elementId,
    kind: "chart",
    capabilityId: "editable.chart.line-pie.v1",
  };
}

function tableReadback(
  block: string,
  elementId: string,
): NonNullable<AuthoredHybridNativeReadbackElement["table"]> {
  const grid = block.match(/<a:tblGrid\b[^>]*>([\s\S]*?)<\/a:tblGrid>/)?.[1];
  const rowBlocks = [...block.matchAll(/<a:tr\b[^>]*>[\s\S]*?<\/a:tr>/g)].map((match) => match[0]);
  if (!grid || !rowBlocks.length) throw new Error(`Hybrid table ${elementId} structure is incomplete.`);
  const columnWidthsPt = [...grid.matchAll(/<a:gridCol\b[^>]*\bw="(\d+)"[^>]*\/>/g)]
    .map((match) => Math.round((Number(match[1]) / 12_700) * 1_000_000) / 1_000_000);
  if (!columnWidthsPt.length || columnWidthsPt.some((value) => !Number.isFinite(value) || value <= 0)) {
    throw new Error(`Hybrid table ${elementId} column widths are invalid.`);
  }
  const rowHeightsPt: number[] = [];
  const cellTexts: string[] = [];
  const mergedCells: Array<{ row: number; column: number; rowSpan: number; columnSpan: number }> = [];
  const cellStyles: Array<Array<NonNullable<AuthoredHybridNativeReadbackElement["table"]>["cellStyles"][number][number]>> = [];
  const alignment = { l: "left", ctr: "center", r: "right", just: "justify" } as const;
  const vertical = { t: "top", ctr: "middle", b: "bottom" } as const;
  const borderTags = { top: "T", right: "R", bottom: "B", left: "L" } as const;
  rowBlocks.forEach((rowBlock, rowIndex) => {
    const rowOpening = rowBlock.match(/<a:tr\b[^>]*>/)?.[0] ?? "";
    const height = Number(xmlOpeningAttribute(rowOpening, "h"));
    if (!Number.isSafeInteger(height) || height <= 0) throw new Error(`Hybrid table ${elementId} row height is invalid.`);
    rowHeightsPt.push(Math.round((height / 12_700) * 1_000_000) / 1_000_000);
    const cells = [...rowBlock.matchAll(/<a:tc(?:\s[^>]*)?>[\s\S]*?<\/a:tc>/g)].map((match) => match[0]);
    if (cells.length !== columnWidthsPt.length) throw new Error(`Hybrid table ${elementId} row width is invalid.`);
    const projectedRow: Array<NonNullable<AuthoredHybridNativeReadbackElement["table"]>["cellStyles"][number][number]> = [];
    cells.forEach((cell, columnIndex) => {
      const opening = cell.match(/<a:tc(?:\s[^>]*)?>/)?.[0] ?? "";
      const rowSpan = Number(xmlOpeningAttribute(opening, "rowSpan") ?? 1);
      const columnSpan = Number(xmlOpeningAttribute(opening, "gridSpan") ?? 1);
      const horizontalMerge = xmlOpeningAttribute(opening, "hMerge") === "1";
      const verticalMerge = xmlOpeningAttribute(opening, "vMerge") === "1";
      if (!Number.isSafeInteger(rowSpan) || !Number.isSafeInteger(columnSpan) || rowSpan < 1 || columnSpan < 1 ||
        rowIndex + rowSpan > rowBlocks.length || columnIndex + columnSpan > columnWidthsPt.length ||
        ((rowSpan > 1 || columnSpan > 1) && (horizontalMerge || verticalMerge))) {
        throw new Error(`Hybrid table ${elementId} merge geometry is invalid.`);
      }
      if (rowSpan > 1 || columnSpan > 1) mergedCells.push({ row: rowIndex, column: columnIndex, rowSpan, columnSpan });
      const texts = [...cell.matchAll(/<a:t(?:\s[^>]*)?>([\s\S]*?)<\/a:t>/g)]
        .map((match) => decodeXmlAttributeValue(match[1]));
      cellTexts.push(texts.join("\n"));
      const pPr = cell.match(/<a:pPr\b[^>]*>/)?.[0] ?? "";
      const horizontal = alignment[(xmlOpeningAttribute(pPr, "algn") ?? "") as keyof typeof alignment];
      const tcPr = cell.match(/<a:tcPr\b[^>]*>[\s\S]*?<\/a:tcPr>/)?.[0] ?? "";
      const verticalAlign = vertical[(xmlOpeningAttribute(tcPr.match(/<a:tcPr\b[^>]*>/)?.[0] ?? "", "anchor") ?? "") as keyof typeof vertical];
      if (!horizontal || !verticalAlign) throw new Error(`Hybrid table ${elementId} alignment is invalid.`);
      const borders = {} as Record<"top" | "right" | "bottom" | "left", { color: string; widthPt: number } | null>;
      (Object.keys(borderTags) as Array<keyof typeof borderTags>).forEach((side) => {
        const line = tcPr.match(new RegExp(`<a:ln${borderTags[side]}\\b[^>]*>[\\s\\S]*?<\\/a:ln${borderTags[side]}>`))?.[0];
        if (!line) { borders[side] = null; return; }
        if (/<a:noFill\s*\/>/.test(line)) { borders[side] = null; return; }
        const width = Number(xmlOpeningAttribute(line.match(new RegExp(`<a:ln${borderTags[side]}\\b[^>]*>`))?.[0] ?? "", "w"));
        const color = line.match(/<a:solidFill\b[^>]*>[\s\S]*?<a:srgbClr\b[^>]*\bval="([0-9A-Fa-f]{6})"[^>]*\/?\s*>/)?.[1];
        if (!Number.isSafeInteger(width) || width <= 0 || !color) throw new Error(`Hybrid table ${elementId} border is invalid.`);
        borders[side] = { color: `#${color.toUpperCase()}`, widthPt: Math.round((width / 12_700) * 1_000_000) / 1_000_000 };
      });
      let fillCarrier = tcPr;
      for (const suffix of Object.values(borderTags)) {
        fillCarrier = fillCarrier.replace(new RegExp(`<a:ln${suffix}\\b[^>]*>[\\s\\S]*?<\\/a:ln${suffix}>`, "g"), "");
      }
      const fillValue = fillCarrier.match(/<a:solidFill\b[^>]*>[\s\S]*?<a:srgbClr\b[^>]*\bval="([0-9A-Fa-f]{6})"[^>]*\/?\s*>/)?.[1];
      const noFill = /<a:noFill\s*\/>/.test(fillCarrier);
      if (!fillValue && !noFill) throw new Error(`Hybrid table ${elementId} fill is invalid.`);
      projectedRow.push({
        fill: fillValue ? `#${fillValue.toUpperCase()}` : null,
        horizontalAlign: horizontal,
        verticalAlign,
        borders,
      });
    });
    cellStyles.push(projectedRow);
  });
  const styleProjection = { columnWidthsPt, rowHeightsPt, mergedCells, cellStyles };
  return {
    rows: rowBlocks.length,
    columns: columnWidthsPt.length,
    cellTexts,
    ...styleProjection,
    tableStyleDigest: canonicalDigest(styleProjection),
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
      ...groupHeaderBlocks(xml),
      ...[...xml.matchAll(/<p:sp\b[\s\S]*?<\/p:sp>/g)].map((match) => match[0]),
      ...[...xml.matchAll(/<p:pic\b[\s\S]*?<\/p:pic>/g)].map((match) => match[0]),
      ...[...xml.matchAll(/<p:graphicFrame\b[\s\S]*?<\/p:graphicFrame>/g)].map((match) => match[0]),
    ]) {
      const rawName = block.match(/<p:cNvPr\b[^>]*\bname="([^"]*)"/)?.[1];
      if (!rawName) continue;
      const tagOwner = /<c:chart\b/.test(block)
        ? relatedChartTagOwner(archive, slideNumber, block)
        : null;
      const identity = tagOwner
        ? { kind: "chart" as const, elementId: tagOwner.elementId }
        : hybridIdentity(rawName, serializedNativeKind(block));
      if (!identity) continue;
      if (results.some((entry) =>
        entry.slideNumber === slideNumber &&
        entry.elementId === identity.elementId &&
        entry.kind === identity.kind
      )) {
        throw new Error(`Hybrid ${identity.kind} ${identity.elementId} has duplicate owners.`);
      }
      const chartXml = identity.kind === "chart"
        ? relatedChartXml(archive, slideNumber, block)
        : null;
      const chart = chartXml ? chartReadback(chartXml) : null;
      if (tagOwner) {
        const decodedName = decodeXmlAttributeValue(rawName);
        const carrier = /^Presenton hybrid chart slide-3:chart:(line|pie)_01$/.exec(
          decodedName ?? "",
        );
        if (!carrier || !chart || carrier[1] !== chart.chartType) {
          throw new Error(`Hybrid chart ${identity.elementId} normalized carrier mismatch.`);
        }
      }
      const bounds = renderedBounds(block);
      const nativeOwner = tagOwner
        ? null
        : nativeOwnerReceipt(block, slideNumber, identity);
      const owner = tagOwner ?? nativeOwner;
      const advancedText = advancedTextReadback(block, identity, nativeOwner);
      const table = identity.kind === "table" ? tableReadback(block, identity.elementId) : null;
      const sourceRect = identity.kind === "image" ? block.match(/<a:srcRect\b[^>]*\/>/)?.[0] : undefined;
      results.push({
        slideNumber,
        elementId: identity.elementId,
        kind: identity.kind,
        ...(owner ? { capabilityId: owner.capabilityId } : {}),
        latinTypefaces: typefaces(chartXml ?? block, "latin"),
        eastAsianTypefaces: typefaces(chartXml ?? block, "ea"),
        lineBreakCount: occurrences(block, /<a:br\/>/g),
        ...(bounds ? { renderedBounds: bounds } : {}),
        ...(advancedText ?? {}),
        ...(chart ? { chart } : {}),
        ...(table ? { table } : {}),
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
): WithAuthoredNativeDisposition<PresentationExportQualityReport> {
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
  return attachAuthoredNativeDispositionReceipt(createPresentationExportQuality(
    "hybrid",
    slideEntries.map(({ slideNumber, bytes }) => {
      const xml = bytes.toString("utf8");
      const nativeTextElements = occurrences(xml, /<p:txBody\b/g);
      const allShapes = occurrences(xml, /<p:sp\b/g);
      const semanticOwnershipCarrierElements = occurrences(
        xml,
        /<p:cNvPr\b[^>]*\bname="Presenton raster visual fragment [^"]+ semantic-owner"/g,
      );
      const connectorElements = occurrences(xml, /<p:cxnSp\b/g);
      const nativeGroupElements = occurrences(xml, /<p:grpSp\b/g);
      const nativeShapeElements =
        Math.max(
          0,
          allShapes - nativeTextElements - semanticOwnershipCarrierElements,
        ) + connectorElements + nativeGroupElements;
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
      const slideNativeReadback = nativeReadback.filter(
        (entry) => entry.slideNumber === slideNumber,
      );
      const rasterOwnerQids = semanticRasterOwnerQids(xml);
      const nativeOwnerIds = new Set(slideNativeReadback.map((entry) => entry.elementId));
      const ownerDispositionConflict = rasterOwnerQids.some((qid) => nativeOwnerIds.has(qid));
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
        qidCoverage: [
          ...slideNativeReadback.map((entry) => ({ elementId: entry.elementId, kind: entry.kind, logical: "native" as const, ooxml: "present" as const, com: "not-run" as const })),
          ...rasterOwnerQids.map((elementId) => ({ elementId, kind: "shape" as const, logical: "raster" as const, ooxml: "present" as const, com: "not-run" as const })),
        ].sort((left, right) => left.elementId.localeCompare(right.elementId)),
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
          status: ownerDispositionConflict ? "failed" as const : "passed" as const,
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
  ));
}

/** Preserve stage2 structural counters after the generic logical/readback join. */
export function attachAuthoredHybridStage2ReadbackMetadata<
  T extends PresentationExportQualityReport,
>(quality: T, pptxBytes: Buffer): WithAuthoredNativeDisposition<T> {
  const structural = inspectPptxEditability(pptxBytes);
  const nativeReadback = inspectPptxNativeElementReadback(pptxBytes);
  const bySlide = new Map(structural.slides.map((slide) => [slide.slideNumber, slide as typeof slide & { nativeTableElements?: number; nativeImageCropElements?: number }]));
  return attachAuthoredNativeDispositionReceipt({
    ...quality,
    slides: quality.slides.map((slide) => {
      const observed = bySlide.get(slide.slideNumber);
      const nativeForSlide = nativeReadback.filter(
        (entry) => entry.slideNumber === slide.slideNumber,
      );
      const geometryContract = slide.geometryContract;
      const ooxmlGeometry = geometryContract?.elements.map((expected) => {
        const actual = nativeForSlide.find(
          (entry) => entry.elementId === expected.elementId && entry.kind === expected.kind,
        );
        if (!actual?.renderedBounds) {
          throw new Error("presentation_export_geometry_readback_missing");
        }
        for (const field of ["x", "y", "width", "height"] as const) {
          if (Math.abs(actual.renderedBounds[field] - expected[field]) > 0.01) {
            console.warn(
              "[authored-hybrid] OOXML geometry mismatch " +
                JSON.stringify({
                  slideNumber: slide.slideNumber,
                  elementId: expected.elementId,
                  kind: expected.kind,
                  field,
                  expected: expected[field],
                  actual: actual.renderedBounds[field],
                }),
            );
            throw new Error("presentation_export_geometry_readback_mismatch");
          }
        }
        return { elementId: expected.elementId, kind: expected.kind, ...actual.renderedBounds };
      });
      return {
        ...slide,
        nativeTableElements: observed?.nativeTableElements ?? 0,
        nativeImageCropElements: observed?.nativeImageCropElements ?? 0,
        nativeElementReadback: nativeForSlide,
        ...(geometryContract && ooxmlGeometry
          ? {
              geometryContract: {
                ...geometryContract,
                ooxmlDigest: canonicalDigest({
                  schemaVersion: geometryContract.schemaVersion,
                  slideNumber: slide.slideNumber,
                  elements: ooxmlGeometry,
                }),
                ooxmlMatch: true as const,
              },
            }
          : {}),
      };
    }),
  } as T);
}
