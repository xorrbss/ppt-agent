import { createHash } from "node:crypto";

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
  for (const match of relationshipsXml.matchAll(/\bId=["']rId(\d+)["']/gi)) {
    maximum = Math.max(maximum, Number(match[1]));
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

const GROUP_ROOT_XML =
  '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>' +
  '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>';

function assembleSlide(
  entries: Map<string, Buffer>,
  layer: AuthoredHybridSlideLayer
): void {
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
  entries.set(relsPath, Buffer.from(relationshipsXml, "utf8"));
  const replacement =
    `<p:spTree>${GROUP_ROOT_XML}${backplatePictureXml(backplateRelationshipId)}` +
    `${nativeXml.join("")}</p:spTree>`;
  entries.set(
    slidePath,
    Buffer.from(slideXml.replace(spTree[0], replacement), "utf8")
  );
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
  for (const layer of layers) assembleSlide(entries, layer);
  const output = writePptxArchive(entries);
  readPptxArchive(output);
  return output;
}
