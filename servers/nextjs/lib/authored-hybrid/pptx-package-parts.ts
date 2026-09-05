/**
 * OPC package plumbing shared by the authored-hybrid PPTX assembler.
 *
 * Split out of `pptx-assembler.ts`, which owns the slide itself: how a
 * backplate, its raster bands and the native OOXML shapes are stacked into one
 * `<p:spTree>`. This module owns the surrounding package bookkeeping that the
 * assembler has to get right for PowerPoint to open the file at all - slide
 * relationship ids, `[Content_Types].xml` declarations, chart part numbering
 * and media file naming.
 *
 * It deliberately knows nothing about slide geometry. Every function here
 * reads or rewrites an existing package part and fails loudly when that part
 * does not look like the OOXML it expects, because silently appending to a
 * malformed relationship or content-type document produces a deck that only
 * breaks later, inside PowerPoint.
 *
 * Media names are content-addressed (`hybrid-s<n>-<role>-<sha256 prefix>.png`)
 * so re-running the assembler over the same pixels reuses one part instead of
 * growing the archive, while a digest collision on different bytes still falls
 * back to a numbered suffix rather than overwriting.
 */
import { createHash } from "node:crypto";

import {
  decodeXmlAttributeValue,
  rawXmlAttribute as rawRelationshipAttribute,
} from "./pptx-archive.ts";

const IMAGE_RELATIONSHIP_TYPE =
  "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image";
export const CHART_RELATIONSHIP_TYPE =
  "http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart";
const CHART_CONTENT_TYPE =
  "application/vnd.openxmlformats-officedocument.drawingml.chart+xml";

export function appendRelationship(
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

export function nextChartNumber(entries: ReadonlyMap<string, Buffer>): number {
  let maximum = 0;
  for (const name of entries.keys()) {
    const number = /^ppt\/charts\/chart(\d+)\.xml$/i.exec(name)?.[1];
    if (number) maximum = Math.max(maximum, Number(number));
  }
  return maximum + 1;
}

export function addChartContentType(
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

export function nextRelationshipNumber(relationshipsXml: string): number {
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

export function addPngContentType(entries: Map<string, Buffer>): void {
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

export function allocateMediaName(
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
