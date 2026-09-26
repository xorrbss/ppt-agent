import { semanticOwnerQids, backplatePictureXml, semanticOwnershipShapeXml, backplateBackgroundXml, escapeXmlAttribute, isCanvasBackgroundShape, isOpaqueRgbPng } from "./pptx-assembler-backplate.ts";
export { SEMANTIC_OWNERSHIP_ALT_TEXT_PREFIX, isCanvasBackgroundShape, isOpaqueRgbPng } from "./pptx-assembler-backplate.ts";
import { assertFragmentBand, isFragmentBand, validateFragmentBand, RASTER_FRAGMENT_LIMIT, RASTER_FRAGMENT_BYTES, type RasterBand } from "./raster-fragments.ts";
import { createHash } from "node:crypto";
import {
  authoredCanvasPhysicalSize,
  canvasEmuPerUnit,
  LEGACY_AUTHORED_CANVAS,
  type AuthoredCanvasContract,
} from "../authored-canvas.ts";
import type { LocalFitCaptureEvidence } from "./local-fit-export-evidence.ts";

import {
  AUTHORED_NATIVE_OWNER_ALTERNATIVE_TEXT_PREFIX,
  flattenPreparedNativeElements,
  preparedNativeElementNonVisualIdCount,
  preparedNativeElementUnderlayNonVisualIdCount,
  powerPointTextBoundsForPreflight,
  serializePreparedNativeElementOverlay,
  serializePreparedNativeElementUnderlay,
  type PowerPointTypefaceSerializationOptions,
  type PreparedNativeElement,
} from "./native-plan.ts";
import { serializeNativeChartPart } from "./native-chart.ts";
import { backplatePixelSize } from "./capture-scale.ts";
import {
  assertSerializedTextBreakIntegrity,
  authoredHardBreakCount,
} from "./pptx-text-integrity.ts";
import {
  addChartContentType,
  addPngContentType,
  allocateMediaName,
  appendRelationship,
  CHART_RELATIONSHIP_TYPE,
  nextChartNumber,
  nextRelationshipNumber,
} from "./pptx-package-parts.ts";
import {
  decodeXmlAttributeValue,
  opcPartComparisonKey,
  rawXmlAttribute as rawRelationshipAttribute,
  readPptxArchive,
  referencedPackageTargets,
  relationshipTargetPath,
  type PptxCustomPropertyInput,
  upsertPptxCustomProperties,
  writePptxArchive,
  writePptxArchiveAsync,
  xmlAttributeValue as relationshipAttribute,
} from "./pptx-archive.ts";

const SLIDE_WIDTH_EMU = 12_192_000;
const SLIDE_HEIGHT_EMU = 6_858_000;
const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
export interface AuthoredHybridSlideLayer {
  localFitCapture?: LocalFitCaptureEvidence;
  /** One-based OOXML slide number. */
  slideNumber: number;
  backplatePng: Buffer;
  /** Already filtered in ascending browser paint order. */
  elements: readonly PreparedNativeElement[];
  /** Ordered semantic QIDs represented by native objects or this slide's raster. */
  semanticOwnerQids?: readonly string[];
  /** Transparent Chromium raster bands in audited paint order. */
  rasterBands?: readonly RasterBand[];
  selectionObstructions?: readonly { zBand: number; elementId: string; reason: string }[];
  /** Exact band assignment for each top-level native owner. */
  nativeZBandByElementId?: Readonly<Record<string, number>>;
  /** Scene-authoritative ownership supplied only for native objects on this slide. */
  nativeOwnership?: {
    readonly slideId: string;
    readonly owners: readonly {
      readonly elementId: string;
      readonly kind: string;
      readonly capabilityId: string;
      readonly advancedText?: {
        readonly schemaVersion: string;
        readonly text: string;
        readonly autoFit: "none" | "shrink";
        readonly style: {
          readonly fontFamily: string; readonly fontSizePt: number;
          readonly fontWeight: number; readonly color: string;
          readonly italic: boolean; readonly underline: boolean;
        };
        readonly runs: readonly {
          readonly runId: string; readonly text: string;
          readonly fontFamily: string | null; readonly fontSizePt: number | null;
          readonly fontWeight: number | null; readonly color: string | null;
          readonly italic: boolean | null; readonly underline: boolean | null;
        }[];
      };
    }[];
  };
}
export interface AuthoredHybridPptxProvenance {
  readonly exportAttemptId?: string; readonly presentationRevision?: number;
  readonly profileId?: "authored-editable-hybrid-ko-en-basic-v1";
  readonly canonicalSourceDigest?: string; readonly sourceDigest?: string;
}
export interface AuthoredHybridAssemblerOptions extends PowerPointTypefaceSerializationOptions {
  readonly provenance?: AuthoredHybridPptxProvenance;
  readonly canvas?: AuthoredCanvasContract;
}

function xmlAttribute(xml: string, element: string, attribute: string): number | null {
  const opening = xml.match(new RegExp(`<${element}\\b[^>]*>`, "i"))?.[0];
  const value = opening?.match(new RegExp(`\\b${attribute}=["'](\\d+)["']`, "i"))?.[1];
  return value === undefined ? null : Number(value);
}

function setPresentationSize(
  entries: Map<string, Buffer>,
  canvas: AuthoredCanvasContract,
): void {
  const presentation = entries.get("ppt/presentation.xml")?.toString("utf8");
  if (!presentation) throw new Error("PPTX presentation.xml is missing.");
  const width = xmlAttribute(presentation, "p:sldSz", "cx");
  const height = xmlAttribute(presentation, "p:sldSz", "cy");
  const target = authoredCanvasPhysicalSize(canvas);
  if (!(
    (width === SLIDE_WIDTH_EMU && height === SLIDE_HEIGHT_EMU) ||
    (width === target.widthEmu && height === target.heightEmu)
  )) {
    throw new Error("Hybrid export source has an unsupported authored slide size.");
  }
  const next = presentation.replace(
    /<p:sldSz\b[^>]*>/i,
    (opening) => opening
      .replace(/\bcx=["']\d+["']/i, `cx="${target.widthEmu}"`)
      .replace(/\bcy=["']\d+["']/i, `cy="${target.heightEmu}"`),
  );
  if (next === presentation &&
      (width !== target.widthEmu || height !== target.heightEmu)) {
    throw new Error("PPTX presentation slide size could not be updated.");
  }
  entries.set("ppt/presentation.xml", Buffer.from(next, "utf8"));
}

function assertBackplatePng(
  png: Buffer,
  canvas: AuthoredCanvasContract,
): void {
  const expected = backplatePixelSize(canvas);
  if (
    png.length < 24 ||
    !png.subarray(0, PNG_SIGNATURE.length).equals(PNG_SIGNATURE) ||
    png.readUInt32BE(16) !== expected.width ||
    png.readUInt32BE(20) !== expected.height
  ) {
    throw new Error(`Hybrid backplate must be a ${expected.width}x${expected.height} PNG.`);
  }
}

function referencedRelationshipIds(slideXml: string): {
  ids: ReadonlySet<string>;
  complete: boolean;
} {
  const ids = new Set<string>();
  let complete = true;
  for (const match of slideXml.matchAll(
    /[^\s<>"'=/:]+:(?:id|embed|link)\s*=\s*(["'])(.*?)\1/gi
  )) {
    const id = decodeXmlAttributeValue(match[2]);
    if (id === null) {
      complete = false;
    } else {
      ids.add(id);
    }
  }
  return { ids, complete };
}

function pruneUnusedImageRelationships(
  relationshipsXml: string,
  slideXml: string,
  relsPath: string
): { xml: string; removedMediaPaths: ReadonlySet<string> } {
  const referenced = referencedRelationshipIds(slideXml);
  const removedMediaPaths = new Set<string>();
  if (!referenced.complete) {
    return { xml: relationshipsXml, removedMediaPaths };
  }
  const xml = relationshipsXml.replace(
    /<((?:[A-Za-z_][\w.-]*:)?Relationship)\b[^>]*(?:\/\s*>|>\s*<\/\1\s*>)/gi,
    (relationship) => {
      const type = relationshipAttribute(relationship, "Type");
      const id = relationshipAttribute(relationship, "Id");
      if (!type?.toLowerCase().endsWith("/image") || !id || referenced.ids.has(id)) {
        return relationship;
      }
      const rawTargetMode = rawRelationshipAttribute(relationship, "TargetMode");
      const targetMode =
        rawTargetMode === null ? null : decodeXmlAttributeValue(rawTargetMode);
      if (rawTargetMode !== null && targetMode === null) return relationship;
      const rawTarget = rawRelationshipAttribute(relationship, "Target");
      const target = rawTarget === null ? null : decodeXmlAttributeValue(rawTarget);
      if (target === null) return relationship;
      if (targetMode?.toLowerCase() === "external") return "";
      const mediaPath = relationshipTargetPath(relsPath, target);
      if (mediaPath === null) return relationship;
      if (mediaPath.startsWith("ppt/media/")) removedMediaPaths.add(mediaPath);
      return "";
    }
  );
  return { xml, removedMediaPaths };
}

const GROUP_ROOT_XML =
  '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>' +
  '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>';

const CJK_TEXT = /[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]/u;
const CJK_TYPEFACE = /(?:malgun|noto.*(?:kr|cjk)|pretendard|맑은|고딕|명조)/i;
function canonicalOwnerJson(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalOwnerJson).join(",")}]`;
  const record = value as Record<string, unknown>;
  return `{${Object.keys(record).sort().map((key) =>
    `${JSON.stringify(key)}:${canonicalOwnerJson(record[key])}`
  ).join(",")}}`;
}

function ownerDigest(value: unknown): string {
  return `sha256:${createHash("sha256").update(canonicalOwnerJson(value), "utf8").digest("hex")}`;
}

function serializeAdvancedTextOverlay(
  xml: string,
  item: Extract<PreparedNativeElement, { kind: "text" }>,
  slideNumber: number,
  ownership: NonNullable<AuthoredHybridSlideLayer["nativeOwnership"]>,
  owner: NonNullable<AuthoredHybridSlideLayer["nativeOwnership"]>["owners"][number],
): string {
  const advanced = owner.advancedText;
  if (!advanced || owner.kind !== "text" || owner.capabilityId !== "editable.text.advanced.v1" ||
    advanced.schemaVersion !== "authored-advanced-text-owner/v1" ||
    !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(ownership.slideId) ||
    !/^s\d{2,3}-[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(owner.elementId) ||
    !owner.elementId.startsWith(`s${String(slideNumber).padStart(2, "0")}-`) ||
    owner.elementId !== item.source.id ||
    !["none", "shrink"].includes(advanced.autoFit) ||
    typeof advanced.text !== "string" || advanced.text !== item.source.text.plainText ||
    !Array.isArray(advanced.runs) || advanced.runs.length < 1 || advanced.runs.length > 512 ||
    advanced.runs.map((run) => run.text).join("") !== advanced.text ||
    /[\r\n]/.test(advanced.text)) {
    throw new Error(`Hybrid advanced text ${item.source.id} has an invalid Scene owner.`);
  }
  const base = advanced.style;
  const validFamily = (value: unknown): value is string =>
    typeof value === "string" && value.trim().length > 0 && value.length <= 128 && !/[\x00-\x1f]/.test(value);
  const validSize = (value: unknown): value is number =>
    typeof value === "number" && Number.isFinite(value) && value > 0 && value <= 400;
  const validWeight = (value: unknown): value is number =>
    typeof value === "number" && Number.isFinite(value) && value >= 1 && value <= 1000;
  const validColor = (value: unknown): value is string =>
    typeof value === "string" && /^#[0-9A-F]{6}$/.test(value);
  if (!base || !validFamily(base.fontFamily) || !validSize(base.fontSizePt) ||
    !validWeight(base.fontWeight) || !validColor(base.color) ||
    typeof base.italic !== "boolean" || typeof base.underline !== "boolean") {
    throw new Error(`Hybrid advanced text ${item.source.id} has an invalid base style.`);
  }
  let utf16Start = 0;
  const runIds = new Set<string>();
  const textRunMap: { runIndex: number; runId: string; utf16Start: number; utf16Length: number; textDigest: string }[] = [];
  const textRunStyles: { runIndex: number; runId: string; fontFamily: string; eastAsianTypeface: string; fontSizePt: number; bold: boolean; italic: boolean; underline: boolean; color: string }[] = [];
  const runXml = advanced.runs.map((run, runIndex) => {
    if (!run || !/^[A-Za-z][A-Za-z0-9._:-]{0,127}$/.test(run.runId) ||
      runIds.has(run.runId) || typeof run.text !== "string" ||
      (run.fontFamily !== null && !validFamily(run.fontFamily)) ||
      (run.fontSizePt !== null && !validSize(run.fontSizePt)) ||
      (run.fontWeight !== null && !validWeight(run.fontWeight)) ||
      (run.color !== null && !validColor(run.color)) ||
      (run.italic !== null && typeof run.italic !== "boolean") ||
      (run.underline !== null && typeof run.underline !== "boolean")) {
      throw new Error(`Hybrid advanced text ${item.source.id} has an invalid run.`);
    }
    runIds.add(run.runId);
    const style = {
      runIndex, runId: run.runId,
      fontFamily: run.fontFamily ?? base.fontFamily,
      eastAsianTypeface: run.fontFamily ?? base.fontFamily,
      fontSizePt: run.fontSizePt ?? base.fontSizePt,
      bold: (run.fontWeight ?? base.fontWeight) >= 600,
      italic: run.italic ?? base.italic,
      underline: run.underline ?? base.underline,
      color: run.color ?? base.color,
    };
    textRunStyles.push(style);
    textRunMap.push({
      runIndex, utf16Start,
      utf16Length: run.text.length,
      textDigest: ownerDigest({ text: run.text }),
      runId: run.runId,
    });
    utf16Start += run.text.length;
    const family = escapeXmlAttribute(style.fontFamily);
    const color = style.color.slice(1);
    return `<a:r><a:rPr lang="ko-KR" sz="${Math.round(style.fontSizePt * 100)}" b="${style.bold ? 1 : 0}" i="${style.italic ? 1 : 0}" u="${style.underline ? "sng" : "none"}"><a:solidFill><a:srgbClr val="${color}"/></a:solidFill><a:latin typeface="${family}"/><a:ea typeface="${family}"/><a:extLst><a:ext uri="{3F05D0C5-A5DB-4B2C-9A89-38F8A106124A}"><pt:runIdentity xmlns:pt="urn:presenton:authored:advanced-text:v1" runId="${escapeXmlAttribute(run.runId)}"/></a:ext></a:extLst></a:rPr><a:t xml:space="preserve">${escapeXmlAttribute(run.text)}</a:t></a:r>`;
  }).join("");
  const advancedCore = { autoFit: advanced.autoFit, textRunMap, textRunStyles };
  const advancedTextDigest = ownerDigest(advancedCore);
  const ownerCore = {
    schemaVersion: "authored-vnext-owner/v1", slideNumber,
    slideId: ownership.slideId, elementId: owner.elementId,
    kind: "text", capabilityId: owner.capabilityId,
    textRunMap, textRunStyles, autoFit: advanced.autoFit, advancedTextDigest,
  };
  const receipt = JSON.stringify({ ...ownerCore, ownerDigest: ownerDigest(ownerCore) });
  const payload = Buffer.from(canonicalOwnerJson({ textRunMap, textRunStyles, autoFit: advanced.autoFit }), "utf8").toString("base64url");
  const paragraph = xml.match(/<a:p>(<a:pPr\b[\s\S]*?<\/a:pPr>)[\s\S]*?<\/a:p>/)?.[0];
  if (!paragraph || (xml.match(/<a:p>/g) ?? []).length !== 1) {
    throw new Error(`Hybrid advanced text ${item.source.id} requires one editable paragraph.`);
  }
  return xml
    .replace(paragraph, `<a:p>${paragraph.match(/<a:pPr\b[\s\S]*?<\/a:pPr>/)?.[0]}${runXml}<a:endParaRPr lang="ko-KR" b="0"/></a:p>`)
    .replace(/<p:cNvPr\b[^>]*\/>/, (tag) => tag.replace(/\/>$/, ` descr="${escapeXmlAttribute(`${AUTHORED_NATIVE_OWNER_ALTERNATIVE_TEXT_PREFIX}${receipt}`)}"/>`))
    .replace(/<a:(?:normAutofit|noAutofit)\/>/, advanced.autoFit === "shrink" ? "<a:normAutofit/>" : "<a:noAutofit/>")
    .replace("</p:spPr>", `<a:extLst><a:ext uri="{3F05D0C5-A5DB-4B2C-9A89-38F8A106124A}"><pt:advancedText xmlns:pt="urn:presenton:authored:advanced-text:v1" digest="${advancedTextDigest}" payload="${payload}"/></a:ext></a:extLst></p:spPr>`);
}

/** Exact geometry the assembler will expose to OOXML readback. */
export function preparedNativeElementExpectedReadbackBounds(
  item: PreparedNativeElement,
  canvas: AuthoredCanvasContract = LEGACY_AUTHORED_CANVAS,
): { x: number; y: number; width: number; height: number } {
  const sourceBounds = item.kind === "text"
    ? item.source.text.containerShape?.bounds.px ??
      powerPointTextBoundsForPreflight(item.source, canvas) ??
      item.source.bounds.px
    : item.source.bounds.px;
  const toEmu = (value: number) => Math.round(value * canvasEmuPerUnit(canvas));
  const toPx = (value: number) =>
    Math.round((value / canvasEmuPerUnit(canvas)) * 1_000_000) / 1_000_000;
  let x = toEmu(sourceBounds.x);
  let y = toEmu(sourceBounds.y);
  let width = Math.max(1, toEmu(sourceBounds.width));
  let height = Math.max(1, toEmu(sourceBounds.height));
  if (
    item.kind === "text" &&
    !item.source.text.containerShape &&
    Math.abs(item.source.rotationDeg) < 0.001
  ) {
    const horizontalSlack = Math.round(
      Math.max(
        8,
        Math.min(16, item.source.text.style.fontSizePt * 0.4 + 4),
      ) * canvasEmuPerUnit(canvas),
    );
    const verticalSlack = Math.round(
      Math.max(
        4,
        Math.min(12, authoredHardBreakCount(item.source.text) > 0 ? 10 : 4),
      ) * canvasEmuPerUnit(canvas),
    );
    const horizontal = item.source.text.style.horizontalAlignment;
    const vertical = item.source.text.style.verticalAlignment;
    const left = horizontal === "right"
      ? horizontalSlack
      : horizontal === "center"
        ? horizontalSlack / 2
        : 0;
    const right = horizontal === "left"
      ? horizontalSlack
      : horizontal === "center"
        ? horizontalSlack / 2
        : 0;
    const top = vertical === "bottom"
      ? verticalSlack
      : vertical === "middle"
        ? verticalSlack / 2
        : 0;
    const bottom = vertical === "top"
      ? verticalSlack
      : vertical === "middle"
        ? verticalSlack / 2
        : 0;
    const nextX = Math.max(0, Math.round(x - left));
    const nextY = Math.max(0, Math.round(y - top));
    const nextRight = Math.min(authoredCanvasPhysicalSize(canvas).widthEmu, Math.round(x + width + right));
    const nextBottom = Math.min(authoredCanvasPhysicalSize(canvas).heightEmu, Math.round(y + height + bottom));
    x = nextX;
    y = nextY;
    width = Math.max(1, nextRight - nextX);
    height = Math.max(1, nextBottom - nextY);
  }
  return { x: toPx(x), y: toPx(y), width: toPx(width), height: toPx(height) };
}

function expandStandaloneTextBounds(
  xml: string,
  item: Extract<PreparedNativeElement, { kind: "text" }>,
  canvas: AuthoredCanvasContract = LEGACY_AUTHORED_CANVAS
): string {
  if (item.source.text.containerShape || Math.abs(item.source.rotationDeg) >= 0.001) return xml;
  const match = xml.match(
    /<a:xfrm><a:off x="(-?\d+)" y="(-?\d+)"\/><a:ext cx="(\d+)" cy="(\d+)"\/><\/a:xfrm>/
  );
  if (!match) throw new Error(`Hybrid text ${item.source.id} has no bounded transform.`);
  const [x, y, width, height] = match.slice(1).map(Number);
  const horizontalSlack = Math.round(
    Math.max(8, Math.min(16, item.source.text.style.fontSizePt * 0.4 + 4)) * canvasEmuPerUnit(canvas)
  );
  const verticalSlack = Math.round(
    Math.max(4, Math.min(12, authoredHardBreakCount(item.source.text) > 0 ? 10 : 4)) * canvasEmuPerUnit(canvas)
  );
  const horizontal = item.source.text.style.horizontalAlignment;
  const vertical = item.source.text.style.verticalAlignment;
  const left = horizontal === "right" ? horizontalSlack : horizontal === "center" ? horizontalSlack / 2 : 0;
  const right = horizontal === "left" ? horizontalSlack : horizontal === "center" ? horizontalSlack / 2 : 0;
  const top = vertical === "bottom" ? verticalSlack : vertical === "middle" ? verticalSlack / 2 : 0;
  const bottom = vertical === "top" ? verticalSlack : vertical === "middle" ? verticalSlack / 2 : 0;
  const nextX = Math.max(0, Math.round(x - left));
  const nextY = Math.max(0, Math.round(y - top));
  const nextRight = Math.min(authoredCanvasPhysicalSize(canvas).widthEmu, Math.round(x + width + right));
  const nextBottom = Math.min(authoredCanvasPhysicalSize(canvas).heightEmu, Math.round(y + height + bottom));
  return xml.replace(
    match[0],
    `<a:xfrm><a:off x="${nextX}" y="${nextY}"/><a:ext cx="${Math.max(1, nextRight - nextX)}" cy="${Math.max(1, nextBottom - nextY)}"/></a:xfrm>`
  );
}
function hardenTextOverlay(
  xml: string,
  item: Extract<PreparedNativeElement, { kind: "text" }>,
  options: AuthoredHybridAssemblerOptions,
  slideNumber: number,
): string {
  const fallback = options.embeddedTypefaceFamilies?.find((family) => CJK_TYPEFACE.test(family)) ??
    "Malgun Gothic";
  let hardened = xml.replace(
    /<a:r>[\s\S]*?<a:t\b[^>]*>([\s\S]*?)<\/a:t><\/a:r>/g,
    (runXml, text: string) => CJK_TEXT.test(text)
      ? runXml.replace(
        /<a:(latin|ea|cs) typeface="Consolas"\/>/gi,
        (_font: string, script: string) => `<a:${script} typeface="${escapeXmlAttribute(fallback)}"/>`
      )
      : runXml
  );
  assertSerializedTextBreakIntegrity(
    hardened,
    item.source.text,
    slideNumber,
    item.source.id,
  );
  hardened = hardened
    .replace(/\bhorzOverflow="(?:overflow|clip)"/, 'horzOverflow="clip"')
    .replace(/\bvertOverflow="(?:overflow|clip)"/, 'vertOverflow="clip"')
    .replace(/<a:(?:normAutofit|noAutofit)\/>/, "<a:noAutofit/>");
  return expandStandaloneTextBounds(hardened, item, options.canvas);
}
function provenanceProperties(
  provenance?: AuthoredHybridPptxProvenance,
): PptxCustomPropertyInput[] {
  if (!provenance) return [];
  const digest = (value: string | undefined, label: string): string | undefined => {
    if (value === undefined) return undefined;
    if (!/^(?:sha256:)?[0-9a-f]{64}$/i.test(value)) {
      throw new Error(`Hybrid ${label} is not a SHA-256 digest.`);
    }
    return value.toLowerCase();
  };
  if (provenance.exportAttemptId !== undefined &&
      (!provenance.exportAttemptId.trim() || /[\0\r\n]/.test(provenance.exportAttemptId))) {
    throw new Error("Hybrid exportAttemptId is invalid.");
  }
  if (provenance.presentationRevision !== undefined &&
      (!Number.isSafeInteger(provenance.presentationRevision) || provenance.presentationRevision < 0)) {
    throw new Error("Hybrid presentationRevision is invalid.");
  }
  const properties: PptxCustomPropertyInput[] = [];
  if (provenance.exportAttemptId !== undefined) {
    properties.push({ name: "DevAX.ExportAttemptId", value: provenance.exportAttemptId });
  }
  if (provenance.presentationRevision !== undefined) {
    properties.push({ name: "DevAX.PresentationRevision", value: provenance.presentationRevision });
  }
  if (provenance.profileId !== undefined) {
    properties.push({ name: "DevAX.EditableHybridProfileId", value: provenance.profileId });
  }
  const canonicalSourceDigest = digest(
    provenance.canonicalSourceDigest,
    "canonicalSourceDigest",
  );
  if (canonicalSourceDigest !== undefined) {
    properties.push({ name: "DevAX.CanonicalSourceDigest", value: canonicalSourceDigest });
  }
  const sourceDigest = digest(provenance.sourceDigest, "sourceDigest");
  if (sourceDigest !== undefined) {
    properties.push({ name: "DevAX.SourceDigest", value: sourceDigest });
  }
  return properties;
}

function assembleSlide(
  entries: Map<string, Buffer>,
  layer: AuthoredHybridSlideLayer,
  options: AuthoredHybridAssemblerOptions
): ReadonlySet<string> {
  const canvas = options.canvas ?? LEGACY_AUTHORED_CANVAS;
  if (!Number.isInteger(layer.slideNumber) || layer.slideNumber < 1) {
    throw new Error("Hybrid slide number is invalid.");
  }
  assertBackplatePng(layer.backplatePng, canvas);
  if (layer.nativeOwnership) {
    const nativeIds = new Set(flattenPreparedNativeElements(layer.elements).map((item) => item.source.id));
    const declaredIds = new Set<string>();
    for (const owner of layer.nativeOwnership.owners) {
      if (declaredIds.has(owner.elementId) || !nativeIds.has(owner.elementId) ||
        owner.kind !== "text" || owner.capabilityId !== "editable.text.advanced.v1") {
        throw new Error(`Hybrid slide ${layer.slideNumber} has an unsupported native ownership declaration.`);
      }
      declaredIds.add(owner.elementId);
    }
  }
  const rasterBands = layer.rasterBands?.length
    ? [...layer.rasterBands].sort((left, right) => left.zBand - right.zBand)
    : [{ zBand: 0, rasterPng: layer.backplatePng, semanticOwnerQids: layer.semanticOwnerQids ?? [] }];
  if (
    rasterBands.some((band, index) =>
      !Number.isInteger(band.zBand) || band.zBand < 0 ||
      (index > 0 && rasterBands[index - 1].zBand >= band.zBand)
    )
  ) {
    throw new Error("Hybrid raster z-bands must be unique ascending integers.");
  }
  if (rasterBands.length > RASTER_FRAGMENT_LIMIT) throw new Error("Hybrid raster band count exceeds page limit.");
  rasterBands.forEach((band) => { if ("schemaVersion" in band && !isFragmentBand(band)) throw new Error("Hybrid raster band schema version unsupported."); if (isFragmentBand(band)) assertFragmentBand(band); else assertBackplatePng(band.rasterPng, canvas); });
  const fragments: { zBand: number; rasterPng: Buffer; semanticOwnerQids: readonly string[]; fragmentId: string;
    placement?: { x: number; y: number; width: number; height: number; captureWidth: number; captureHeight: number } }[] = [];
  for (const band of rasterBands) {
    if (isFragmentBand(band)) for (const fragment of band.fragments) fragments.push({
      zBand: band.zBand, semanticOwnerQids: band.semanticOwnerQids, rasterPng: fragment.png,
      placement: { ...fragment.rect, captureWidth: band.captureWidthPx, captureHeight: band.captureHeightPx }, fragmentId: fragment.id,
    });
    else fragments.push({ ...band, fragmentId: "legacy" });
  }
  if (fragments.length > RASTER_FRAGMENT_LIMIT) throw new Error("Hybrid raster page fragment limit exceeded.");
  const croppedBytes = rasterBands.flatMap(band => isFragmentBand(band) ? band.fragments.filter(f => !f.png.equals(band.rasterPng)) : []).reduce((sum,f)=>sum+f.png.length,0);
  if (croppedBytes > RASTER_FRAGMENT_BYTES) throw new Error("Hybrid raster page crop byte limit exceeded.");
  const slidePath = `ppt/slides/slide${layer.slideNumber}.xml`;
  const relsPath = `ppt/slides/_rels/slide${layer.slideNumber}.xml.rels`;
  const slideXml = entries.get(slidePath)?.toString("utf8");
  const sourceRelationshipsXml = entries.get(relsPath)?.toString("utf8");
  if (!slideXml || !sourceRelationshipsXml) {
    throw new Error(`Hybrid target slide ${layer.slideNumber} is missing.`);
  }
  let relationshipsXml = sourceRelationshipsXml;
  const spTree = slideXml.match(/<p:spTree\b[^>]*>[\s\S]*?<\/p:spTree>/i);
  if (!spTree) throw new Error("PPTX slide shape tree is malformed.");

  let relationshipNumber = nextRelationshipNumber(relationshipsXml);
  const rasterBandParts = fragments.map((band, index) => {
    const mediaName = allocateMediaName(
      entries,
      layer.slideNumber,
      band.fragmentId === "legacy" ? `raster-band-${band.zBand}` : `raster-band-${band.zBand}-${band.fragmentId}`,
      band.rasterPng,
    );
    const relationshipId = `rId${relationshipNumber}`;
    relationshipNumber += 1;
    relationshipsXml = appendRelationship(
      relationshipsXml,
      relationshipId,
      `../media/${mediaName}`,
    );
    entries.set(`ppt/media/${mediaName}`, Buffer.from(band.rasterPng));
    return { ...band, relationshipId, mediaName, nonVisualId: index + 2 };
  });
  const bottom = rasterBands[0];
  const useSlideBackground = bottom && isFragmentBand(bottom) && bottom.backgroundEligible && bottom.opaque && bottom.zBand === 0 &&
    !layer.elements.some(item => isCanvasBackgroundShape(item, canvas)) &&
    bottom.fragments.length === 1 && bottom.fragments[0].rect.x === 0 && bottom.fragments[0].rect.y === 0 &&
    bottom.fragments[0].rect.width === bottom.captureWidthPx && bottom.fragments[0].rect.height === bottom.captureHeightPx;
  const ownershipQids = semanticOwnerQids(layer);
  const flatNativeElements = flattenPreparedNativeElements(layer.elements);
  const nativeOwnerQids = new Set(flatNativeElements.map((item) => item.source.id));
  if (ownershipQids.some((qid) => nativeOwnerQids.has(qid))) {
    throw new Error("Hybrid native/raster duplicate semantic owner detected.");
  }

  // A full-canvas page-colour rectangle belongs below the residual raster.
  // All other promoted geometry, text-owned container paint, transparent text
  // overlays, and native images sit above it. This preserves raster-only
  // illustrations without baking editable boxes and lines into the background.
  const canvasBackgroundXml: string[] = [];
  const orderedNativeXml: Array<{ zBand: number; xml: string }> = [];
  const ordered = [...layer.elements].sort(
    (left, right) =>
      left.source.zOrder - right.source.zOrder ||
      left.source.sourceIndex - right.source.sourceIndex
  );
  const semanticOwnershipXml = ownershipQids.map((qid, index) =>
    semanticOwnershipShapeXml(index + 2 + rasterBandParts.length, layer.slideNumber, qid)
  );
  let nextNonVisualId = 2 + rasterBandParts.length + semanticOwnershipXml.length;
  let mediaIndex = 0;
  const serializeTreeItem = (
    item: PreparedNativeElement,
    insideGroup = false,
  ): string => {
    if (item.kind === "group") {
      const groupId = nextNonVisualId;
      nextNonVisualId += 1;
      const bounds = item.source.bounds.px;
      const x = Math.round(bounds.x * canvasEmuPerUnit(canvas));
      const y = Math.round(bounds.y * canvasEmuPerUnit(canvas));
      const width = Math.max(1, Math.round(bounds.width * canvasEmuPerUnit(canvas)));
      const height = Math.max(1, Math.round(bounds.height * canvasEmuPerUnit(canvas)));
      const children = [...item.children]
        .sort((left, right) =>
          left.source.zOrder - right.source.zOrder ||
          left.source.sourceIndex - right.source.sourceIndex
        )
        .map((child) => serializeTreeItem(child, true))
        .join("");
      if (!children) {
        throw new Error(`Hybrid group ${item.source.id} has no native children.`);
      }
      const name = escapeXmlAttribute(`DevAX hybrid shape ${item.source.id}`);
      return `<p:grpSp><p:nvGrpSpPr><p:cNvPr id="${groupId}" name="${name}"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="${x}" y="${y}"/><a:ext cx="${width}" cy="${height}"/><a:chOff x="${x}" y="${y}"/><a:chExt cx="${width}" cy="${height}"/></a:xfrm></p:grpSpPr>${children}</p:grpSp>`;
    }
    let relationshipId: string | undefined;
    if (item.kind === "image") {
      mediaIndex += 1;
      const imageRelationshipId = `rId${relationshipNumber}`;
      const mediaName = allocateMediaName(
        entries,
        layer.slideNumber,
        `image-${mediaIndex}`,
        item.png
      );
      relationshipId = imageRelationshipId;
      relationshipNumber += 1;
      relationshipsXml = appendRelationship(
        relationshipsXml,
        imageRelationshipId,
        `../media/${mediaName}`
      );
      entries.set(`ppt/media/${mediaName}`, Buffer.from(item.png));
    }
    if (item.kind === "chart") {
      const chartNumber = nextChartNumber(entries);
      const chartPath = `ppt/charts/chart${chartNumber}.xml`;
      relationshipId = `rId${relationshipNumber}`;
      relationshipNumber += 1;
      relationshipsXml = appendRelationship(
        relationshipsXml,
        relationshipId,
        `../charts/chart${chartNumber}.xml`,
        CHART_RELATIONSHIP_TYPE,
      );
      entries.set(
        chartPath,
        Buffer.from(serializeNativeChartPart(item.source, canvas), "utf8"),
      );
      addChartContentType(entries, chartPath);
    }
    const underlayCount = preparedNativeElementUnderlayNonVisualIdCount(item);
    const underlay = serializePreparedNativeElementUnderlay(
      item,
      nextNonVisualId,
      options
    );
    let overlay = serializePreparedNativeElementOverlay(
      item,
      nextNonVisualId + underlayCount,
      relationshipId,
      options
    );
    if (overlay && item.kind === "text") {
      overlay = hardenTextOverlay(overlay, item, options, layer.slideNumber);
      const owner = layer.nativeOwnership?.owners.find((candidate) => candidate.elementId === item.source.id);
      if (owner) {
        overlay = serializeAdvancedTextOverlay(
          overlay, item, layer.slideNumber, layer.nativeOwnership!, owner,
        );
      }
    }
    if (underlay) {
      if (isCanvasBackgroundShape(item, canvas)) {
        if (insideGroup) {
          throw new Error("Hybrid canvas background cannot be nested in a native group.");
        }
        canvasBackgroundXml.push(underlay);
      }
    }
    nextNonVisualId += preparedNativeElementNonVisualIdCount(item);
    return `${isCanvasBackgroundShape(item, canvas) ? "" : underlay}${overlay}`;
  };
  ordered.forEach((item) => {
    const xml = serializeTreeItem(item);
    const zBand = layer.nativeZBandByElementId?.[item.source.id] ?? 1;
    if (
      layer.rasterBands &&
      (!Number.isInteger(zBand) || zBand < 0)
    ) {
      throw new Error(`Hybrid native z-band missing for ${item.source.id}.`);
    }
    if (xml) orderedNativeXml.push({ zBand, xml });
  });

  const paintXml = [
    ...rasterBandParts.map((band, index) => ({
      zBand: band.zBand,
      ownerOrder: 0,
      xml: useSlideBackground && index === 0
        ? ""
        : backplatePictureXml(
            band.relationshipId,
            layer.slideNumber,
            band.semanticOwnerQids,
            band.nonVisualId,
            index,
            canvas,
            band.placement,
          ),
    })),
    ...orderedNativeXml.map((item) => ({ ...item, ownerOrder: 1 })),
  ].sort((left, right) => left.zBand - right.zBand || left.ownerOrder - right.ownerOrder)
    .map((item) => item.xml)
    .join("");
  const replacement =
    `<p:spTree>${GROUP_ROOT_XML}${useSlideBackground ? "" : canvasBackgroundXml.join("")}` +
    `${semanticOwnershipXml.join("")}` +
    `${paintXml}</p:spTree>`;
  // Pass a replacer function so `$&`, `$'`, `$\`` and `$$` sequences inside the
  // assembled shape XML (e.g. text like "US$'000") are inserted literally instead
  // of being expanded as String.replace special patterns.
  let newSlideXml = slideXml.replace(spTree[0], () => replacement);
  if (useSlideBackground) {
    const background = backplateBackgroundXml(rasterBandParts[0].relationshipId);
    if (/<p:bg\b[^>]*>[\s\S]*?<\/p:bg>/i.test(newSlideXml)) {
      newSlideXml = newSlideXml.replace(
        /<p:bg\b[^>]*>[\s\S]*?<\/p:bg>/i,
        () => background
      );
    } else {
      newSlideXml = newSlideXml.replace(
        /<p:cSld\b[^>]*>/i,
        (openingTag) => `${openingTag}${background}`
      );
    }
  }
  const pruned = pruneUnusedImageRelationships(relationshipsXml, newSlideXml, relsPath);
  entries.set(relsPath, Buffer.from(pruned.xml, "utf8"));
  entries.set(
    slidePath,
    Buffer.from(newSlideXml, "utf8")
  );
  return pruned.removedMediaPaths;
}

/** Rebuild selected slides inside the already-generated fidelity PPTX skeleton. */
function assembleAuthoredHybridEntries(
  fidelityPptx: Buffer,
  layers: readonly AuthoredHybridSlideLayer[],
  options: AuthoredHybridAssemblerOptions = {}
): Map<string, Buffer> | null {
  if (!layers.length && !options.provenance) return null;
  const slideNumbers = new Set<number>();
  for (const layer of layers) {
    if (slideNumbers.has(layer.slideNumber)) {
      throw new Error("Hybrid slide layers contain a duplicate slide number.");
    }
    slideNumbers.add(layer.slideNumber);
  }

  const entries = readPptxArchive(fidelityPptx);
  const removedMediaPaths = new Set<string>();
  if (layers.length) {
    setPresentationSize(entries, options.canvas ?? LEGACY_AUTHORED_CANVAS);
    addPngContentType(entries);
    for (const layer of layers) {
      for (const mediaPath of assembleSlide(entries, layer, options)) {
        removedMediaPaths.add(mediaPath);
      }
    }
  }
  const remainingTargets = referencedPackageTargets(entries);
  if (remainingTargets.complete) {
    for (const mediaPath of removedMediaPaths) {
      if (!remainingTargets.targets.has(opcPartComparisonKey(mediaPath))) {
        entries.delete(mediaPath);
      }
    }
  }
  upsertPptxCustomProperties(entries, provenanceProperties(options.provenance));
  return entries;
}

export function assembleAuthoredHybridPptx(
  fidelityPptx: Buffer,
  layers: readonly AuthoredHybridSlideLayer[],
  options: AuthoredHybridAssemblerOptions = {}
): Buffer {
  const entries = assembleAuthoredHybridEntries(fidelityPptx, layers, options);
  return entries === null ? Buffer.from(fidelityPptx) : writePptxArchive(entries);
}

/** Async ZIP assembly used by the server export path for large presentations. */
export async function assembleAuthoredHybridPptxAsync(
  fidelityPptx: Buffer,
  layers: readonly AuthoredHybridSlideLayer[],
  options: AuthoredHybridAssemblerOptions = {}
): Promise<Buffer> {
  for (const layer of layers) for (const band of layer.rasterBands ?? []) if (isFragmentBand(band)) await validateFragmentBand(band);
  const entries = assembleAuthoredHybridEntries(fidelityPptx, layers, options);
  return entries === null
    ? Buffer.from(fidelityPptx)
    : await writePptxArchiveAsync(entries);
}
