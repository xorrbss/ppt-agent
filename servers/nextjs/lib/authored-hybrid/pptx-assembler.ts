import { createHash } from "node:crypto";
import path from "node:path";

import {
  serializePreparedNativeElement,
  type PreparedNativeElement,
} from "./native-plan.ts";
import { readPptxArchive, writePptxArchive } from "./pptx-archive.ts";

const SLIDE_WIDTH_EMU = 12_192_000;
const SLIDE_HEIGHT_EMU = 6_858_000;
const PNG_SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);

export interface AuthoredHybridSlideLayer {
  /** One-based OOXML slide number. */
  slideNumber: number;
  backplatePng: Buffer;
  /** Already filtered in ascending browser paint order. */
  elements: readonly PreparedNativeElement[];
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

function appendRelationship(
  relationshipsXml: string,
  relationshipId: string,
  target: string
): string {
  if (!/<Relationships\b[^>]*>[\s\S]*<\/Relationships>\s*$/i.test(relationshipsXml)) {
    throw new Error("PPTX slide relationship document is malformed.");
  }
  const relationship =
    `<Relationship Id="${relationshipId}" ` +
    `Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" ` +
    `Target="${target}"/>`;
  return relationshipsXml.replace(/<\/Relationships>\s*$/i, `${relationship}</Relationships>`);
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

function backplatePictureXml(relationshipId: string): string {
  return `<p:pic><p:nvPicPr><p:cNvPr id="2" name="Presenton hybrid backplate"/><p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="${relationshipId}"/><a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="${SLIDE_WIDTH_EMU}" cy="${SLIDE_HEIGHT_EMU}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr></p:pic>`;
}

function rawRelationshipAttribute(tag: string, attribute: string): string | null {
  const value = tag.match(
    new RegExp(`(?:^|\\s)${attribute}\\s*=\\s*(["'])(.*?)\\1`, "i")
  )?.[2];
  return value ?? null;
}

function validXmlCodePoint(codePoint: number): boolean {
  return (
    codePoint === 0x9 ||
    codePoint === 0xa ||
    codePoint === 0xd ||
    (codePoint >= 0x20 && codePoint <= 0xd7ff) ||
    (codePoint >= 0xe000 && codePoint <= 0xfffd) ||
    (codePoint >= 0x10000 && codePoint <= 0x10ffff)
  );
}

function decodeXmlAttributeValue(value: string): string | null {
  let decoded = "";
  let cursor = 0;
  while (cursor < value.length) {
    const entityStart = value.indexOf("&", cursor);
    const literalEnd = entityStart === -1 ? value.length : entityStart;
    const literal = value.slice(cursor, literalEnd);
    if (/[<\0]/.test(literal)) return null;
    decoded += literal;
    if (entityStart === -1) break;

    const entityEnd = value.indexOf(";", entityStart + 1);
    if (entityEnd === -1) return null;
    const entity = value.slice(entityStart + 1, entityEnd);
    const predefined: Readonly<Record<string, string>> = {
      amp: "&",
      apos: "'",
      gt: ">",
      lt: "<",
      quot: '"',
    };
    let replacement = predefined[entity];
    if (replacement === undefined) {
      const hexadecimal = entity.match(/^#x([0-9A-Fa-f]+)$/)?.[1];
      const decimal = entity.match(/^#([0-9]+)$/)?.[1];
      if (hexadecimal === undefined && decimal === undefined) return null;
      const codePoint = Number.parseInt(hexadecimal ?? decimal!, hexadecimal ? 16 : 10);
      if (!Number.isSafeInteger(codePoint) || !validXmlCodePoint(codePoint)) return null;
      replacement = String.fromCodePoint(codePoint);
    }
    decoded += replacement;
    cursor = entityEnd + 1;
  }
  return decoded;
}

function relationshipAttribute(tag: string, attribute: string): string | null {
  const raw = rawRelationshipAttribute(tag, attribute);
  return raw === null ? null : decodeXmlAttributeValue(raw);
}

function relationshipTargetPath(relsPath: string, target: string): string | null {
  if (/[<>&\0?#%]/.test(target)) return null;
  const marker = "/_rels/";
  const comparisonPath = opcPartComparisonKey(relsPath);
  const markerIndex = comparisonPath.lastIndexOf(marker);
  let sourcePath: string;
  if (comparisonPath === "_rels/.rels") {
    sourcePath = "";
  } else if (markerIndex >= 0 && comparisonPath.endsWith(".rels")) {
    const ownerDirectory = relsPath.slice(0, markerIndex);
    const ownerName = relsPath.slice(markerIndex + marker.length, -".rels".length);
    sourcePath = ownerDirectory ? `${ownerDirectory}/${ownerName}` : ownerName;
  } else {
    return null;
  }

  const normalizedTarget = target.replaceAll("\\", "/");
  const resolved = path.posix.normalize(
    normalizedTarget.startsWith("/")
      ? normalizedTarget.slice(1)
      : path.posix.join(path.posix.dirname(sourcePath || "."), normalizedTarget)
  );
  if (!resolved || resolved === ".." || resolved.startsWith("../")) return null;
  return resolved;
}

function opcPartComparisonKey(partPath: string): string {
  return partPath.replace(/[A-Z]/g, (character) => character.toLowerCase());
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

function referencedPackageTargets(entries: ReadonlyMap<string, Buffer>): {
  targets: ReadonlySet<string>;
  complete: boolean;
} {
  const targets = new Set<string>();
  let complete = true;
  for (const [relsPath, bytes] of entries) {
    if (!opcPartComparisonKey(relsPath).endsWith(".rels")) continue;
    const relationshipsXml = bytes.toString("utf8");
    for (const match of relationshipsXml.matchAll(
      /<(?:[A-Za-z_][\w.-]*:)?Relationship\b[^>]*>/gi
    )) {
      const rawTargetMode = rawRelationshipAttribute(match[0], "TargetMode");
      const targetMode =
        rawTargetMode === null ? null : decodeXmlAttributeValue(rawTargetMode);
      if (rawTargetMode !== null && targetMode === null) {
        complete = false;
        continue;
      }
      if (targetMode?.toLowerCase() === "external") continue;
      const rawTarget = rawRelationshipAttribute(match[0], "Target");
      const target = rawTarget === null ? null : decodeXmlAttributeValue(rawTarget);
      if (target === null) {
        complete = false;
        continue;
      }
      const targetPath = relationshipTargetPath(relsPath, target);
      if (targetPath) {
        targets.add(opcPartComparisonKey(targetPath));
      } else {
        complete = false;
      }
    }
  }
  return { targets, complete };
}

const GROUP_ROOT_XML =
  '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>' +
  '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>';

function assembleSlide(
  entries: Map<string, Buffer>,
  layer: AuthoredHybridSlideLayer
): ReadonlySet<string> {
  if (!Number.isInteger(layer.slideNumber) || layer.slideNumber < 1) {
    throw new Error("Hybrid slide number is invalid.");
  }
  assertBackplatePng(layer.backplatePng);
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
  const backplateName = allocateMediaName(
    entries,
    layer.slideNumber,
    "backplate",
    layer.backplatePng
  );
  const backplateRelationshipId = `rId${relationshipNumber}`;
  relationshipNumber += 1;
  relationshipsXml = appendRelationship(
    relationshipsXml,
    backplateRelationshipId,
    `../media/${backplateName}`
  );

  const nativeXml: string[] = [];
  const ordered = [...layer.elements].sort(
    (left, right) =>
      left.source.zOrder - right.source.zOrder ||
      left.source.sourceIndex - right.source.sourceIndex
  );
  ordered.forEach((item, index) => {
    let relationshipId: string | undefined;
    if (item.kind === "image") {
      const imageRelationshipId = `rId${relationshipNumber}`;
      const mediaName = allocateMediaName(
        entries,
        layer.slideNumber,
        `image-${index + 1}`,
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
    nativeXml.push(
      serializePreparedNativeElement(item, index + 3, relationshipId)
    );
  });

  entries.set(`ppt/media/${backplateName}`, Buffer.from(layer.backplatePng));
  const replacement =
    `<p:spTree>${GROUP_ROOT_XML}${backplatePictureXml(backplateRelationshipId)}` +
    `${nativeXml.join("")}</p:spTree>`;
  const newSlideXml = slideXml.replace(spTree[0], replacement);
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
  layers: readonly AuthoredHybridSlideLayer[]
): Buffer {
  if (!layers.length) return Buffer.from(fidelityPptx);
  const slideNumbers = new Set<number>();
  for (const layer of layers) {
    if (slideNumbers.has(layer.slideNumber)) {
      throw new Error("Hybrid slide layers contain a duplicate slide number.");
    }
    slideNumbers.add(layer.slideNumber);
  }

  const entries = readPptxArchive(fidelityPptx);
  assertPresentationSize(entries);
  addPngContentType(entries);
  const removedMediaPaths = new Set<string>();
  for (const layer of layers) {
    for (const mediaPath of assembleSlide(entries, layer)) {
      removedMediaPaths.add(mediaPath);
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
  const output = writePptxArchive(entries);
  readPptxArchive(output);
  return output;
}
