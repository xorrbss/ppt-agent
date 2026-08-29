import { createHash } from "node:crypto";

import {
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
import {
  decodeXmlAttributeValue,
  opcPartComparisonKey,
  rawXmlAttribute as rawRelationshipAttribute,
  readPptxArchive,
  referencedPackageTargets,
  relationshipTargetPath,
  upsertPptxCustomProperties,
  writePptxArchive,
  xmlAttributeValue as relationshipAttribute,
} from "./pptx-archive.ts";

const SLIDE_WIDTH_EMU = 12_192_000;
const SLIDE_HEIGHT_EMU = 6_858_000;
const SLIDE_WIDTH_PX = 1_280;
const SLIDE_HEIGHT_PX = 720;
const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
const IMAGE_RELATIONSHIP_TYPE =
  "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image";
const CHART_RELATIONSHIP_TYPE =
  "http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart";
const CHART_CONTENT_TYPE =
  "application/vnd.openxmlformats-officedocument.drawingml.chart+xml";
export interface AuthoredHybridSlideLayer {
  /** One-based OOXML slide number. */
  slideNumber: number;
  backplatePng: Buffer;
  /** Already filtered in ascending browser paint order. */
  elements: readonly PreparedNativeElement[];
  /** Ordered semantic QIDs represented by native objects or this slide's raster. */
  semanticOwnerQids?: readonly string[];
  /** Transparent Chromium raster bands in audited paint order. */
  rasterBands?: readonly {
    readonly zBand: number;
    readonly rasterPng: Buffer;
    readonly semanticOwnerQids: readonly string[];
  }[];
  /** Exact band assignment for each top-level native owner. */
  nativeZBandByElementId?: Readonly<Record<string, number>>;
}
export interface AuthoredHybridPptxProvenance {
  readonly exportAttemptId?: string; readonly presentationRevision?: number;
  readonly profileId?: "authored-editable-hybrid-ko-en-basic-v1";
  readonly canonicalSourceDigest?: string; readonly sourceDigest?: string;
}
export interface AuthoredHybridAssemblerOptions extends PowerPointTypefaceSerializationOptions {
  readonly provenance?: AuthoredHybridPptxProvenance;
}

function xmlAttribute(xml: string, element: string, attribute: string): number | null {
  const opening = xml.match(new RegExp(`<${element}\\b[^>]*>`, "i"))?.[0];
  const value = opening?.match(new RegExp(`\\b${attribute}=["'](\\d+)["']`, "i"))?.[1];
  return value === undefined ? null : Number(value);
}

function assertPresentationSize(entries: ReadonlyMap<string, Buffer>): void {
  const presentation = entries.get("ppt/presentation.xml")?.toString("utf8");
  if (!presentation) throw new Error("PPTX presentation.xml is missing.");
  if (
    xmlAttribute(presentation, "p:sldSz", "cx") !== SLIDE_WIDTH_EMU ||
    xmlAttribute(presentation, "p:sldSz", "cy") !== SLIDE_HEIGHT_EMU
  ) {
    throw new Error("Hybrid export requires the fixed 16:9 authored slide size.");
  }
}

function assertBackplatePng(png: Buffer): void {
  if (
    png.length < 24 ||
    !png.subarray(0, PNG_SIGNATURE.length).equals(PNG_SIGNATURE) ||
    png.readUInt32BE(16) !== 1280 ||
    png.readUInt32BE(20) !== 720
  ) {
    throw new Error("Hybrid backplate must be a 1280x720 PNG.");
  }
}

/**
 * Authored slides commonly start with an opaque, full-canvas rectangle that
 * supplies only the page colour. It is editable geometry, but it must remain
 * below the residual raster; otherwise it hides raster-only illustrations
 * while later text and small shapes still appear correctly.
 */
export function isCanvasBackgroundShape(item: PreparedNativeElement): boolean {
  if (item.kind !== "shape") return false;
  const { x, y, width, height } = item.source.bounds.px;
  const shape = item.source.shape;
  const fillIsOpaque = shape.fill
    ? shape.fill.alpha * item.source.opacity >= 0.995
    : Boolean(
        shape.gradient?.stops.length &&
          shape.gradient.stops.every(
            (stop) => stop.color.alpha * item.source.opacity >= 0.995
          )
      );
  return (
    shape.shape === "rectangle" &&
    fillIsOpaque &&
    Math.abs(item.source.rotationDeg) < 0.001 &&
    x <= 0.5 &&
    y <= 0.5 &&
    x + width >= SLIDE_WIDTH_PX - 0.5 &&
    y + height >= SLIDE_HEIGHT_PX - 0.5
  );
}

function appendRelationship(
  relationshipsXml: string,
  relationshipId: string,
  target: string,
  relationshipType = IMAGE_RELATIONSHIP_TYPE,
): string {
  if (!/<Relationships\b[^>]*>[\s\S]*<\/Relationships>\s*$/i.test(relationshipsXml)) {
    throw new Error("PPTX slide relationship document is malformed.");
  }
  const relationship =
    `<Relationship Id="${relationshipId}" ` +
    `Type="${relationshipType}" ` +
    `Target="${target}"/>`;
  return relationshipsXml.replace(/<\/Relationships>\s*$/i, `${relationship}</Relationships>`);
}

function nextChartNumber(entries: ReadonlyMap<string, Buffer>): number {
  let maximum = 0;
  for (const name of entries.keys()) {
    const number = /^ppt\/charts\/chart(\d+)\.xml$/i.exec(name)?.[1];
    if (number) maximum = Math.max(maximum, Number(number));
  }
  return maximum + 1;
}

function addChartContentType(
  entries: Map<string, Buffer>,
  chartPath: string,
): void {
  const path = "[Content_Types].xml";
  const original = entries.get(path)?.toString("utf8");
  if (!original) throw new Error("PPTX content types document is missing.");
  if (original.includes(`PartName="/${chartPath}"`)) return;
  if (!/<\/Types>\s*$/i.test(original)) {
    throw new Error("PPTX content types document is malformed.");
  }
  const override =
    `<Override PartName="/${chartPath}" ContentType="${CHART_CONTENT_TYPE}"/>`;
  entries.set(
    path,
    Buffer.from(original.replace(/<\/Types>\s*$/i, `${override}</Types>`), "utf8"),
  );
}

function nextRelationshipNumber(relationshipsXml: string): number {
  let maximum = 0;
  for (const match of relationshipsXml.matchAll(
    /<(?:[A-Za-z_][\w.-]*:)?Relationship\b[^>]*>/gi
  )) {
    const rawId = rawRelationshipAttribute(match[0], "Id");
    if (rawId === null) continue;
    const id = decodeXmlAttributeValue(rawId);
    if (id === null) {
      throw new Error("PPTX slide relationship Id is malformed.");
    }
    const numericId = id.match(/^rId(\d+)$/i)?.[1];
    if (numericId === undefined) continue;
    const value = Number(numericId);
    if (!Number.isSafeInteger(value)) {
      throw new Error("PPTX slide relationship Id is out of range.");
    }
    maximum = Math.max(maximum, value);
  }
  return maximum + 1;
}

function addPngContentType(entries: Map<string, Buffer>): void {
  const path = "[Content_Types].xml";
  const original = entries.get(path)?.toString("utf8");
  if (!original) throw new Error("PPTX content types document is missing.");
  if (/<Default\b[^>]*\bExtension=["']png["']/i.test(original)) return;
  if (!/<\/Types>\s*$/i.test(original)) {
    throw new Error("PPTX content types document is malformed.");
  }
  entries.set(
    path,
    Buffer.from(
      original.replace(
        /<\/Types>\s*$/i,
        '<Default Extension="png" ContentType="image/png"/></Types>'
      ),
      "utf8"
    )
  );
}

function allocateMediaName(
  entries: ReadonlyMap<string, Buffer>,
  slideNumber: number,
  role: string,
  bytes: Buffer
): string {
  const digest = createHash("sha256").update(bytes).digest("hex").slice(0, 16);
  const stem = `hybrid-s${slideNumber}-${role}-${digest}`;
  let candidate = `${stem}.png`;
  let suffix = 2;
  while (entries.has(`ppt/media/${candidate}`)) {
    const existing = entries.get(`ppt/media/${candidate}`);
    if (existing?.equals(bytes)) return candidate;
    candidate = `${stem}-${suffix}.png`;
    suffix += 1;
  }
  return candidate;
}

export const SEMANTIC_OWNERSHIP_ALT_TEXT_PREFIX =
  "presenton-semantic-raster-ownership-v1:";

function semanticOwnerQids(layer: AuthoredHybridSlideLayer): string[] {
  const qids = layer.rasterBands
    ? layer.rasterBands.flatMap((band) => [...band.semanticOwnerQids])
    : [...(layer.semanticOwnerQids ?? [])];
  if (qids.length > 2048) {
    throw new Error("Hybrid semantic ownership QID count exceeds the bounded limit.");
  }
  const unique = new Set<string>();
  for (const qid of qids) {
    if (
      typeof qid !== "string" ||
      !/^[A-Za-z][A-Za-z0-9._:-]{0,95}$/.test(qid) ||
      unique.has(qid)
    ) {
      throw new Error("Hybrid semantic ownership QIDs must be unique bounded strings.");
    }
    unique.add(qid);
  }
  return qids;
}

function semanticOwnershipAltText(slideNumber: number, qids: readonly string[]): string {
  const qidSetDigest = `sha256:${createHash("sha256")
    .update(qids.join("\n"), "utf8")
    .digest("hex")}`;
  const payload = Buffer.from(JSON.stringify({
    schemaVersion: "presenton-semantic-raster-ownership/v1",
    slideNumber,
    qids,
    qidSetDigest,
  }), "utf8").toString("base64url");
  return `${SEMANTIC_OWNERSHIP_ALT_TEXT_PREFIX}${payload}`;
}

function backplatePictureXml(
  relationshipId: string,
  _slideNumber: number,
  _qids: readonly string[],
  nonVisualId = 2,
  bandIndex = 0,
): string {
  // Semantic QIDs live only on the hidden exact-owner carriers. Repeating the
  // set on a raster picture would create a second logical owner in readback.
  const description = "";
  const name = bandIndex === 0
    ? "Presenton hybrid backplate"
    : `Presenton hybrid raster z-band ${bandIndex}`;
  return `<p:pic><p:nvPicPr><p:cNvPr id="${nonVisualId}" name="${name}"${description}/><p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="${relationshipId}"/><a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="${SLIDE_WIDTH_EMU}" cy="${SLIDE_HEIGHT_EMU}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr></p:pic>`;
}

function semanticOwnershipShapeXml(
  nonVisualId: number,
  slideNumber: number,
  qid: string,
): string {
  const name = escapeXmlAttribute(
    `Presenton raster visual fragment ${qid} semantic-owner`,
  );
  const description = escapeXmlAttribute(
    semanticOwnershipAltText(slideNumber, [qid]),
  );
  return `<p:sp><p:nvSpPr><p:cNvPr id="${nonVisualId}" name="${name}" descr="${description}" hidden="1"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="1" cy="1"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr></p:sp>`;
}

function backplateBackgroundXml(relationshipId: string): string {
  return `<p:bg><p:bgPr><a:blipFill dpi="0" rotWithShape="1"><a:blip r:embed="${relationshipId}"/><a:stretch><a:fillRect/></a:stretch></a:blipFill><a:effectLst/></p:bgPr></p:bg>`;
}

export function isOpaqueRgbPng(png: Buffer): boolean {
  // The PNG IHDR colour-type byte is 2 for true-colour RGB without an alpha
  // channel. Such a residual covers the entire canvas (typically a page-level
  // gradient), so keeping it as a selectable picture only creates a misleading
  // full-slide object. A real slide background preserves the appearance while
  // leaving all extracted text, boxes, lines, and icons independently editable.
  return png.length > 25 && png[25] === 2;
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
function escapeXmlAttribute(value: string): string {
  return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;");
}

function authoredBreakCount(item: Extract<PreparedNativeElement, { kind: "text" }>): number {
  const explicit = item.source.text.runs.reduce(
    (count, run) => count + (
      run.breakKind === "line" || run.breakKind === "paragraph"
        ? (run.text.match(/\n/g) ?? []).length
        : 0
    ),
    0
  );
  return Math.max(explicit, Math.max(0, item.source.text.paragraphs.length - 1));
}

/** Exact geometry the assembler will expose to OOXML readback. */
export function preparedNativeElementExpectedReadbackBounds(
  item: PreparedNativeElement,
): { x: number; y: number; width: number; height: number } {
  const sourceBounds = item.kind === "text"
    ? item.source.text.containerShape?.bounds.px ??
      powerPointTextBoundsForPreflight(item.source) ??
      item.source.bounds.px
    : item.source.bounds.px;
  const toEmu = (value: number) => Math.round(value * 9_525);
  const toPx = (value: number) =>
    Math.round((value / 9_525) * 1_000_000) / 1_000_000;
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
      ) * 9_525,
    );
    const verticalSlack = Math.round(
      Math.max(
        4,
        Math.min(12, authoredBreakCount(item) > 0 ? 10 : 4),
      ) * 9_525,
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
    const nextRight = Math.min(SLIDE_WIDTH_EMU, Math.round(x + width + right));
    const nextBottom = Math.min(SLIDE_HEIGHT_EMU, Math.round(y + height + bottom));
    x = nextX;
    y = nextY;
    width = Math.max(1, nextRight - nextX);
    height = Math.max(1, nextBottom - nextY);
  }
  return { x: toPx(x), y: toPx(y), width: toPx(width), height: toPx(height) };
}

function expandStandaloneTextBounds(
  xml: string,
  item: Extract<PreparedNativeElement, { kind: "text" }>
): string {
  if (item.source.text.containerShape || Math.abs(item.source.rotationDeg) >= 0.001) return xml;
  const match = xml.match(
    /<a:xfrm><a:off x="(-?\d+)" y="(-?\d+)"\/><a:ext cx="(\d+)" cy="(\d+)"\/><\/a:xfrm>/
  );
  if (!match) throw new Error(`Hybrid text ${item.source.id} has no bounded transform.`);
  const [x, y, width, height] = match.slice(1).map(Number);
  const horizontalSlack = Math.round(
    Math.max(8, Math.min(16, item.source.text.style.fontSizePt * 0.4 + 4)) * 9_525
  );
  const verticalSlack = Math.round(
    Math.max(4, Math.min(12, authoredBreakCount(item) > 0 ? 10 : 4)) * 9_525
  );
  const horizontal = item.source.text.style.horizontalAlignment;
  const vertical = item.source.text.style.verticalAlignment;
  const left = horizontal === "right" ? horizontalSlack : horizontal === "center" ? horizontalSlack / 2 : 0;
  const right = horizontal === "left" ? horizontalSlack : horizontal === "center" ? horizontalSlack / 2 : 0;
  const top = vertical === "bottom" ? verticalSlack : vertical === "middle" ? verticalSlack / 2 : 0;
  const bottom = vertical === "top" ? verticalSlack : vertical === "middle" ? verticalSlack / 2 : 0;
  const nextX = Math.max(0, Math.round(x - left));
  const nextY = Math.max(0, Math.round(y - top));
  const nextRight = Math.min(SLIDE_WIDTH_EMU, Math.round(x + width + right));
  const nextBottom = Math.min(SLIDE_HEIGHT_EMU, Math.round(y + height + bottom));
  return xml.replace(
    match[0],
    `<a:xfrm><a:off x="${nextX}" y="${nextY}"/><a:ext cx="${Math.max(1, nextRight - nextX)}" cy="${Math.max(1, nextBottom - nextY)}"/></a:xfrm>`
  );
}
function hardenTextOverlay(
  xml: string,
  item: Extract<PreparedNativeElement, { kind: "text" }>,
  options: AuthoredHybridAssemblerOptions
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
  const expectedBreaks = authoredBreakCount(item);
  const serializedBreaks = (hardened.match(/<a:br\/>/g) ?? []).length +
    Math.max(0, (hardened.match(/<a:p>/g) ?? []).length - 1);
  if (serializedBreaks < expectedBreaks) {
    throw new Error(`Hybrid text ${item.source.id} lost an authored line break.`);
  }
  hardened = hardened
    .replace(/\bhorzOverflow="(?:overflow|clip)"/, 'horzOverflow="clip"')
    .replace(/\bvertOverflow="(?:overflow|clip)"/, 'vertOverflow="clip"')
    .replace(/<a:(?:normAutofit|noAutofit)\/>/, "<a:noAutofit/>");
  return expandStandaloneTextBounds(hardened, item);
}
function provenanceProperties(provenance?: AuthoredHybridPptxProvenance) {
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
  return [
    provenance.exportAttemptId === undefined ? null : { name: "Presenton.ExportAttemptId", value: provenance.exportAttemptId },
    provenance.presentationRevision === undefined ? null : { name: "Presenton.PresentationRevision", value: provenance.presentationRevision },
    provenance.profileId === undefined ? null : { name: "Presenton.EditableHybridProfileId", value: provenance.profileId },
    digest(provenance.canonicalSourceDigest, "canonicalSourceDigest") === undefined ? null : { name: "Presenton.CanonicalSourceDigest", value: digest(provenance.canonicalSourceDigest, "canonicalSourceDigest")! },
    digest(provenance.sourceDigest, "sourceDigest") === undefined ? null : { name: "Presenton.SourceDigest", value: digest(provenance.sourceDigest, "sourceDigest")! },
  ].filter((property): property is { name: string; value: string | number } => property !== null);
}

function assembleSlide(
  entries: Map<string, Buffer>,
  layer: AuthoredHybridSlideLayer,
  options: AuthoredHybridAssemblerOptions
): ReadonlySet<string> {
  if (!Number.isInteger(layer.slideNumber) || layer.slideNumber < 1) {
    throw new Error("Hybrid slide number is invalid.");
  }
  assertBackplatePng(layer.backplatePng);
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
  rasterBands.forEach((band) => assertBackplatePng(band.rasterPng));
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
  const rasterBandParts = rasterBands.map((band, index) => {
    const mediaName = allocateMediaName(
      entries,
      layer.slideNumber,
      `raster-band-${band.zBand}`,
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
  const useSlideBackground =
    rasterBandParts.length === 1 &&
    rasterBandParts[0].zBand === 0 &&
    isOpaqueRgbPng(rasterBandParts[0].rasterPng);
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
      const x = Math.round(bounds.x * 9_525);
      const y = Math.round(bounds.y * 9_525);
      const width = Math.max(1, Math.round(bounds.width * 9_525));
      const height = Math.max(1, Math.round(bounds.height * 9_525));
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
      const name = escapeXmlAttribute(`Presenton hybrid shape ${item.source.id}`);
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
        Buffer.from(serializeNativeChartPart(item.source), "utf8"),
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
    if (overlay && item.kind === "text") overlay = hardenTextOverlay(overlay, item, options);
    if (underlay) {
      if (isCanvasBackgroundShape(item)) {
        if (insideGroup) {
          throw new Error("Hybrid canvas background cannot be nested in a native group.");
        }
        canvasBackgroundXml.push(underlay);
      }
    }
    nextNonVisualId += preparedNativeElementNonVisualIdCount(item);
    return `${isCanvasBackgroundShape(item) ? "" : underlay}${overlay}`;
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
      xml: useSlideBackground
        ? ""
        : backplatePictureXml(
            band.relationshipId,
            layer.slideNumber,
            band.semanticOwnerQids,
            band.nonVisualId,
            index,
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
export function assembleAuthoredHybridPptx(
  fidelityPptx: Buffer,
  layers: readonly AuthoredHybridSlideLayer[],
  options: AuthoredHybridAssemblerOptions = {}
): Buffer {
  if (!layers.length && !options.provenance) return Buffer.from(fidelityPptx);
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
    assertPresentationSize(entries);
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
  const output = writePptxArchive(entries);
  readPptxArchive(output);
  return output;
}
