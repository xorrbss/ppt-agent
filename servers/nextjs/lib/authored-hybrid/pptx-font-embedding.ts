import { createHash } from "node:crypto";

import { readPptxArchive, writePptxArchive } from "./pptx-archive.ts";

const RELATIONSHIPS_NS =
  "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
const FONT_RELATIONSHIP = `${RELATIONSHIPS_NS}/font`;
const FONT_CONTENT_TYPE = "application/x-fontdata";
const EOT_XOR_ENCRYPT_DATA = 0x10000000;
const EOT_SUBSET = 0x00000001;

export type PowerPointEmbeddedFontFace =
  | "regular"
  | "bold"
  | "italic"
  | "boldItalic";

export interface PowerPointFontFaceInput {
  readonly data: Buffer;
  /** True only when the supplied SFNT has already been subsetted upstream. */
  readonly subset?: boolean;
  readonly source?: string;
}

export interface PowerPointTypefaceInput {
  readonly typeface: string;
  readonly pitchFamily?: number;
  /**
   * Windows LOGFONT charset byte. Values 128-255 are accepted for callers
   * that use the unsigned Win32 constants and are serialized as the
   * equivalent signed OOXML byte (for example, HANGEUL_CHARSET 129 -> -127).
   */
  readonly charset?: number;
  readonly faces: Readonly<
    Partial<Record<PowerPointEmbeddedFontFace, PowerPointFontFaceInput>>
  >;
}

export interface EmbeddedPowerPointFontFace {
  readonly face: PowerPointEmbeddedFontFace;
  readonly relationshipId: string;
  readonly partName: string;
  readonly source?: string;
  readonly sourceSha256: string;
  readonly sourceBytes: number;
  readonly embeddedBytes: number;
  readonly subset: boolean;
  readonly fsType: number;
  readonly weight: number;
  readonly italic: boolean;
  readonly format: "eot-uncompressed-xor";
}

export interface EmbeddedPowerPointTypeface {
  readonly typeface: string;
  readonly pitchFamily: number;
  readonly charset: number;
  readonly faces: readonly EmbeddedPowerPointFontFace[];
}

export interface PowerPointFontEmbeddingResult {
  readonly requested: true;
  readonly applied: boolean;
  readonly embeddedFontFiles: number;
  readonly embeddedTypefaces: number;
  readonly fonts: readonly EmbeddedPowerPointTypeface[];
  readonly reason?: string;
}

export interface EmbeddedPowerPointPptx {
  readonly pptx: Buffer;
  readonly result: PowerPointFontEmbeddingResult;
}

interface SfntMetadata {
  readonly family: string;
  readonly style: string;
  readonly version: string;
  readonly fullName: string;
  readonly panose: Buffer;
  readonly italic: boolean;
  readonly weight: number;
  readonly fsType: number;
  readonly unicodeRanges: readonly [number, number, number, number];
  readonly codePageRanges: readonly [number, number];
  readonly checkSumAdjustment: number;
}

const FACE_ORDER = Object.freeze([
  "regular",
  "bold",
  "italic",
  "boldItalic",
] as const);

function xmlEscapeAttribute(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll('"', "&quot;")
    .replaceAll("\r", "&#xD;")
    .replaceAll("\n", "&#xA;");
}

function normalizePowerPointCharset(value: number): {
  readonly eot: number;
  readonly presentation: number;
} {
  if (!Number.isInteger(value) || value < -128 || value > 255) {
    throw new Error("Embedded PowerPoint font charset is invalid.");
  }
  const eot = value < 0 ? value + 256 : value;
  // CT_TextFont.charset is an XML Schema byte, not an unsigned Win32 BYTE.
  // PowerPoint consequently writes HANGEUL_CHARSET 129 as -127.
  return {
    eot,
    presentation: eot > 127 ? eot - 256 : eot,
  };
}

function readUInt16BE(bytes: Buffer, offset: number, label: string): number {
  if (offset < 0 || offset + 2 > bytes.length) {
    throw new Error(`Embedded font has a truncated ${label}.`);
  }
  return bytes.readUInt16BE(offset);
}

function readUInt32BE(bytes: Buffer, offset: number, label: string): number {
  if (offset < 0 || offset + 4 > bytes.length) {
    throw new Error(`Embedded font has a truncated ${label}.`);
  }
  return bytes.readUInt32BE(offset);
}

function sfntTables(bytes: Buffer): Map<string, { offset: number; length: number }> {
  if (bytes.length < 12) throw new Error("Embedded font is not a complete SFNT.");
  const signature = bytes.subarray(0, 4).toString("latin1");
  if (
    bytes.readUInt32BE(0) !== 0x00010000 &&
    signature !== "true"
  ) {
    if (signature === "ttcf") {
      throw new Error("TrueType collections cannot be embedded as a PowerPoint font face.");
    }
    throw new Error("PowerPoint embedding requires a TrueType-outline SFNT.");
  }
  const tableCount = readUInt16BE(bytes, 4, "table directory");
  if (tableCount < 1 || tableCount > 256 || 12 + tableCount * 16 > bytes.length) {
    throw new Error("Embedded font has an invalid SFNT table directory.");
  }
  const tables = new Map<string, { offset: number; length: number }>();
  for (let index = 0; index < tableCount; index += 1) {
    const record = 12 + index * 16;
    const tag = bytes.subarray(record, record + 4).toString("latin1");
    const offset = readUInt32BE(bytes, record + 8, `${tag} table offset`);
    const length = readUInt32BE(bytes, record + 12, `${tag} table length`);
    if (offset > bytes.length || length > bytes.length - offset) {
      throw new Error(`Embedded font ${tag} table exceeds the SFNT bounds.`);
    }
    if (tables.has(tag)) throw new Error(`Embedded font repeats the ${tag} table.`);
    tables.set(tag, { offset, length });
  }
  if (!tables.has("glyf") || !tables.has("loca")) {
    throw new Error("PowerPoint embedding requires TrueType glyf/loca outlines.");
  }
  if (tables.has("fvar") || tables.has("gvar")) {
    throw new Error(
      "Variable fonts are not packaged directly; provide static PowerPoint-compatible faces."
    );
  }
  return tables;
}

function decodeSfntName(bytes: Buffer, platform: number): string {
  if (platform === 0 || platform === 3) {
    if (bytes.length % 2 !== 0) return "";
    const swapped = Buffer.alloc(bytes.length);
    for (let index = 0; index < bytes.length; index += 2) {
      swapped[index] = bytes[index + 1];
      swapped[index + 1] = bytes[index];
    }
    return swapped.toString("utf16le").replaceAll("\0", "").trim();
  }
  return bytes.toString("latin1").replaceAll("\0", "").trim();
}

function sfntNames(
  bytes: Buffer,
  table: { offset: number; length: number }
): Readonly<Record<number, string>> {
  const end = table.offset + table.length;
  if (table.length < 6) throw new Error("Embedded font has a truncated name table.");
  const count = readUInt16BE(bytes, table.offset + 2, "name record count");
  const stringOffset = readUInt16BE(bytes, table.offset + 4, "name string offset");
  if (table.offset + 6 + count * 12 > end || table.offset + stringOffset > end) {
    throw new Error("Embedded font has invalid name records.");
  }
  const candidates = new Map<number, Array<{ score: number; value: string }>>();
  for (let index = 0; index < count; index += 1) {
    const record = table.offset + 6 + index * 12;
    const platform = readUInt16BE(bytes, record, "name platform");
    const encoding = readUInt16BE(bytes, record + 2, "name encoding");
    const language = readUInt16BE(bytes, record + 4, "name language");
    const nameId = readUInt16BE(bytes, record + 6, "name identifier");
    const length = readUInt16BE(bytes, record + 8, "name length");
    const offset = readUInt16BE(bytes, record + 10, "name offset");
    if (![1, 2, 4, 5].includes(nameId)) continue;
    const start = table.offset + stringOffset + offset;
    if (start > end || length > end - start) continue;
    const value = decodeSfntName(bytes.subarray(start, start + length), platform);
    if (!value) continue;
    const score =
      platform === 3 && language === 0x0409
        ? 4
        : platform === 3 && [1, 10].includes(encoding)
          ? 3
          : platform === 0
            ? 2
            : 1;
    const list = candidates.get(nameId) ?? [];
    list.push({ score, value });
    candidates.set(nameId, list);
  }
  const result: Record<number, string> = {};
  for (const [nameId, values] of candidates) {
    values.sort((left, right) => right.score - left.score);
    result[nameId] = values[0].value;
  }
  return result;
}

function parseSfntMetadata(bytes: Buffer): SfntMetadata {
  const tables = sfntTables(bytes);
  const os2 = tables.get("OS/2");
  const head = tables.get("head");
  const name = tables.get("name");
  if (!os2 || os2.length < 68 || !head || head.length < 12 || !name) {
    throw new Error("Embedded font is missing required OS/2, head, or name metadata.");
  }
  const os2Offset = os2.offset;
  const os2Version = readUInt16BE(bytes, os2Offset, "OS/2 version");
  const names = sfntNames(bytes, name);
  const family = names[1] ?? "";
  if (!family) throw new Error("Embedded font has no usable family name.");
  return {
    family,
    style: names[2] ?? "Regular",
    version: names[5] ?? "",
    fullName: names[4] ?? family,
    panose: Buffer.from(bytes.subarray(os2Offset + 32, os2Offset + 42)),
    italic: (readUInt16BE(bytes, os2Offset + 62, "OS/2 fsSelection") & 0x0001) !== 0,
    weight: readUInt16BE(bytes, os2Offset + 4, "OS/2 weight"),
    fsType: readUInt16BE(bytes, os2Offset + 8, "OS/2 fsType"),
    unicodeRanges: [
      readUInt32BE(bytes, os2Offset + 42, "OS/2 Unicode range 1"),
      readUInt32BE(bytes, os2Offset + 46, "OS/2 Unicode range 2"),
      readUInt32BE(bytes, os2Offset + 50, "OS/2 Unicode range 3"),
      readUInt32BE(bytes, os2Offset + 54, "OS/2 Unicode range 4"),
    ],
    codePageRanges:
      os2Version >= 1 && os2.length >= 86
        ? [
            readUInt32BE(bytes, os2Offset + 78, "OS/2 code page range 1"),
            readUInt32BE(bytes, os2Offset + 82, "OS/2 code page range 2"),
          ]
        : [0, 0],
    checkSumAdjustment: readUInt32BE(bytes, head.offset + 8, "head checksum adjustment"),
  };
}

function utf16LeNullTerminated(value: string): Buffer {
  const bytes = Buffer.from(`${value}\0`, "utf16le");
  if (bytes.length > 0xffff) throw new Error("Embedded font metadata string is too long.");
  return bytes;
}

/**
 * Wrap a static TrueType SFNT in the EOT structure PowerPoint writes to
 * `ppt/fonts/*.fntdata`. The SFNT payload is uncompressed and XOR-protected
 * with 0x50; the matching TTEMBED_XORENCRYPTDATA flag is set in the EOT header.
 */
export function createPowerPointFontData(
  sfnt: Buffer,
  options: { readonly subset?: boolean; readonly charset?: number } = {}
): { readonly data: Buffer; readonly metadata: SfntMetadata } {
  const source = Buffer.from(sfnt);
  const metadata = parseSfntMetadata(source);
  const charset = normalizePowerPointCharset(options.charset ?? 1);
  // Restricted-license and bitmap-only fonts cannot be embedded. Preview/print
  // embedding is intentionally rejected because this exporter promises an
  // editable presentation. Installable (0) and editable (0x8) are accepted.
  if ((metadata.fsType & 0x0002) !== 0 || (metadata.fsType & 0x0200) !== 0) {
    throw new Error(
      `Font embedding is prohibited by OS/2 fsType 0x${metadata.fsType.toString(16).padStart(4, "0")}.`
    );
  }
  if ((metadata.fsType & 0x0004) !== 0 && (metadata.fsType & 0x0008) === 0) {
    throw new Error(
      "Preview-and-print-only fonts are not accepted for an editable PowerPoint export."
    );
  }
  if (options.subset && (metadata.fsType & 0x0100) !== 0) {
    throw new Error("This font license forbids subsetting.");
  }

  const family = utf16LeNullTerminated(metadata.family);
  const style = utf16LeNullTerminated(metadata.style);
  const version = utf16LeNullTerminated(metadata.version);
  const fullName = utf16LeNullTerminated(metadata.fullName);
  const fixed = Buffer.alloc(82);
  const flags = EOT_XOR_ENCRYPT_DATA | (options.subset ? EOT_SUBSET : 0);
  fixed.writeUInt32LE(source.length, 4);
  fixed.writeUInt32LE(0x00010000, 8);
  fixed.writeUInt32LE(flags, 12);
  metadata.panose.copy(fixed, 16);
  fixed.writeUInt8(charset.eot, 26);
  fixed.writeUInt8(metadata.italic ? 1 : 0, 27);
  fixed.writeUInt32LE(metadata.weight, 28);
  fixed.writeUInt16LE(metadata.fsType, 32);
  fixed.writeUInt16LE(0x504c, 34);
  metadata.unicodeRanges.forEach((value, index) =>
    fixed.writeUInt32LE(value, 36 + index * 4)
  );
  fixed.writeUInt32LE(metadata.codePageRanges[0], 52);
  fixed.writeUInt32LE(metadata.codePageRanges[1], 56);
  fixed.writeUInt32LE(metadata.checkSumAdjustment, 60);
  // Reserved1-4 and Padding1 remain zero.

  const variable: Buffer[] = [];
  for (const [index, value] of [family, style, version, fullName].entries()) {
    const length = Buffer.alloc(2);
    length.writeUInt16LE(value.length);
    variable.push(length, value);
    // EOT 1.0 defines padding after FamilyName, StyleName, and VersionName.
    // FullName is followed immediately by FontData.
    if (index < 3) variable.push(Buffer.alloc(2));
  }
  const protectedFont = Buffer.alloc(source.length);
  for (let index = 0; index < source.length; index += 1) {
    protectedFont[index] = source[index] ^ 0x50;
  }
  const result = Buffer.concat([fixed, ...variable, protectedFont]);
  result.writeUInt32LE(result.length, 0);
  return { data: result, metadata };
}

function nextRelationshipNumber(xml: string): number {
  let result = 1;
  for (const match of xml.matchAll(/\bId\s*=\s*["']rId(\d+)["']/gi)) {
    result = Math.max(result, Number(match[1]) + 1);
  }
  return result;
}

function appendRelationship(
  xml: string,
  id: string,
  target: string
): string {
  const relationship =
    `<Relationship Id="${id}" Type="${FONT_RELATIONSHIP}" ` +
    `Target="${xmlEscapeAttribute(target)}"/>`;
  if (/<\/(?:\w+:)?Relationships\s*>/i.test(xml)) {
    return xml.replace(
      /<\/(?:\w+:)?Relationships\s*>/i,
      (closing) => `${relationship}${closing}`
    );
  }
  if (/<(?:\w+:)?Relationships\b[^>]*\/\s*>/i.test(xml)) {
    return xml.replace(
      /<((?:\w+:)?Relationships)\b([^>]*)\/\s*>/i,
      `<$1$2>${relationship}</$1>`
    );
  }
  throw new Error("PPTX presentation relationships are malformed.");
}

function ensureFontContentType(xml: string): string {
  if (
    /<Default\b[^>]*\bExtension\s*=\s*["']fntdata["'][^>]*>/i.test(xml)
  ) {
    if (
      !/<Default\b[^>]*\bExtension\s*=\s*["']fntdata["'][^>]*\bContentType\s*=\s*["']application\/x-fontdata["'][^>]*>/i.test(
        xml
      ) &&
      !/<Default\b[^>]*\bContentType\s*=\s*["']application\/x-fontdata["'][^>]*\bExtension\s*=\s*["']fntdata["'][^>]*>/i.test(
        xml
      )
    ) {
      throw new Error("PPTX declares an incompatible fntdata content type.");
    }
    return xml;
  }
  const declaration = `<Default Extension="fntdata" ContentType="${FONT_CONTENT_TYPE}"/>`;
  if (/<\/(?:\w+:)?Types\s*>/i.test(xml)) {
    return xml.replace(/<\/(?:\w+:)?Types\s*>/i, (closing) => `${declaration}${closing}`);
  }
  if (/<(?:\w+:)?Types\b[^>]*\/\s*>/i.test(xml)) {
    return xml.replace(
      /<((?:\w+:)?Types)\b([^>]*)\/\s*>/i,
      `<$1$2>${declaration}</$1>`
    );
  }
  throw new Error("PPTX content types are malformed.");
}

function ensureRelationshipNamespace(xml: string): string {
  const opening = xml.match(/<p:presentation\b[^>]*>/i)?.[0];
  if (!opening) throw new Error("PPTX presentation.xml is malformed.");
  const rBinding = opening.match(/\bxmlns:r\s*=\s*(["'])(.*?)\1/i)?.[2];
  if (rBinding && rBinding !== RELATIONSHIPS_NS) {
    throw new Error("PPTX presentation.xml binds the r prefix incompatibly.");
  }
  if (rBinding) return xml;
  return xml.replace(
    opening,
    opening.replace(/>$/, ` xmlns:r="${RELATIONSHIPS_NS}">`)
  );
}

function ensureEmbedTrueTypeFonts(xml: string): string {
  const opening = xml.match(/<p:presentation\b[^>]*>/i)?.[0];
  if (!opening) throw new Error("PPTX presentation.xml is malformed.");
  if (/\bembedTrueTypeFonts\s*=/i.test(opening)) {
    return xml.replace(
      opening,
      opening.replace(
        /\bembedTrueTypeFonts\s*=\s*(["'])(?:true|false|0|1)\1/i,
        'embedTrueTypeFonts="1"'
      )
    );
  }
  return xml.replace(
    opening,
    opening.replace(/>$/, ' embedTrueTypeFonts="1">')
  );
}

function insertEmbeddedFontList(xml: string, list: string): string {
  if (/<p:embeddedFontLst\b/i.test(xml)) {
    throw new Error("Replacing an existing PowerPoint embedded-font list is unsupported.");
  }
  const orderedSuccessors = [
    "custShowLst",
    "photoAlbum",
    "custDataLst",
    "kinsoku",
    "defaultTextStyle",
    "modifyVerifier",
    "extLst",
  ];
  for (const successor of orderedSuccessors) {
    const expression = new RegExp(`<p:${successor}\\b`, "i");
    const match = expression.exec(xml);
    if (match) return `${xml.slice(0, match.index)}${list}${xml.slice(match.index)}`;
  }
  const closing = /<\/p:presentation\s*>/i.exec(xml);
  if (!closing) throw new Error("PPTX presentation.xml has no closing element.");
  return `${xml.slice(0, closing.index)}${list}${xml.slice(closing.index)}`;
}

function allocateFontPartName(
  entries: ReadonlyMap<string, Buffer>,
  start: number
): { readonly name: string; readonly next: number } {
  let number = start;
  while (entries.has(`ppt/fonts/font${number}.fntdata`)) number += 1;
  return { name: `ppt/fonts/font${number}.fntdata`, next: number + 1 };
}

/**
 * Add real PresentationML embedded-font declarations and EOT font parts.
 *
 * Font discovery, allowlisting, and provenance checks belong at the server-only
 * caller boundary. This function independently validates the binary SFNT and
 * embedding-license bits before mutating the package.
 */
export function embedPowerPointFonts(
  pptxBytes: Buffer,
  requestedFonts: readonly PowerPointTypefaceInput[]
): EmbeddedPowerPointPptx {
  if (requestedFonts.length === 0) {
    return {
      pptx: Buffer.from(pptxBytes),
      result: {
        requested: true,
        applied: false,
        embeddedFontFiles: 0,
        embeddedTypefaces: 0,
        fonts: [],
        reason: "No embeddable font faces were supplied.",
      },
    };
  }
  const entries = readPptxArchive(pptxBytes);
  const presentationPath = "ppt/presentation.xml";
  const relationshipsPath = "ppt/_rels/presentation.xml.rels";
  const contentTypesPath = "[Content_Types].xml";
  let presentation = entries.get(presentationPath)?.toString("utf8");
  let relationships = entries.get(relationshipsPath)?.toString("utf8");
  let contentTypes = entries.get(contentTypesPath)?.toString("utf8");
  if (!presentation || !relationships || !contentTypes) {
    throw new Error("PPTX is missing presentation, relationship, or content-type parts.");
  }
  if (
    new RegExp(`\\bType\\s*=\\s*["']${FONT_RELATIONSHIP.replaceAll("/", "\\/")}["']`, "i").test(
      relationships
    )
  ) {
    throw new Error("PPTX already contains embedded font relationships.");
  }
  presentation = ensureRelationshipNamespace(presentation);
  contentTypes = ensureFontContentType(contentTypes);

  const seenTypefaces = new Set<string>();
  let nextRelationship = nextRelationshipNumber(relationships);
  let nextPart = 1;
  const embeddedFonts: EmbeddedPowerPointTypeface[] = [];
  const declarations: string[] = [];
  for (const requested of requestedFonts) {
    const typeface = requested.typeface.trim();
    if (!typeface || typeface.length > 127 || /[\0\r\n]/.test(typeface)) {
      throw new Error("Embedded PowerPoint typeface is invalid.");
    }
    const typefaceKey = typeface.toLocaleLowerCase("en-US");
    if (seenTypefaces.has(typefaceKey)) {
      throw new Error(`Embedded PowerPoint typeface is duplicated: ${typeface}.`);
    }
    seenTypefaces.add(typefaceKey);
    const pitchFamily = requested.pitchFamily ?? 0;
    const charset = normalizePowerPointCharset(requested.charset ?? 1);
    if (
      !Number.isInteger(pitchFamily) ||
      pitchFamily < 0 ||
      pitchFamily > 255
    ) {
      throw new Error("Embedded PowerPoint font pitchFamily is invalid.");
    }
    const faceResults: EmbeddedPowerPointFontFace[] = [];
    const faceDeclarations: string[] = [];
    for (const face of FACE_ORDER) {
      const input = requested.faces[face];
      if (!input) continue;
      const wrapped = createPowerPointFontData(input.data, {
        subset: input.subset,
        charset: charset.eot,
      });
      const expectedBold = face === "bold" || face === "boldItalic";
      const expectedItalic = face === "italic" || face === "boldItalic";
      if (expectedBold !== (wrapped.metadata.weight >= 700)) {
        throw new Error(`Embedded ${typeface} ${face} face has incompatible weight metadata.`);
      }
      if (expectedItalic !== wrapped.metadata.italic) {
        throw new Error(`Embedded ${typeface} ${face} face has incompatible italic metadata.`);
      }
      const allocated = allocateFontPartName(entries, nextPart);
      nextPart = allocated.next;
      const relationshipId = `rId${nextRelationship}`;
      nextRelationship += 1;
      entries.set(allocated.name, wrapped.data);
      relationships = appendRelationship(
        relationships,
        relationshipId,
        allocated.name.slice("ppt/".length)
      );
      faceDeclarations.push(`<p:${face} r:id="${relationshipId}"/>`);
      faceResults.push({
        face,
        relationshipId,
        partName: allocated.name,
        source: input.source,
        sourceSha256: createHash("sha256").update(input.data).digest("hex"),
        sourceBytes: input.data.length,
        embeddedBytes: wrapped.data.length,
        subset: Boolean(input.subset),
        fsType: wrapped.metadata.fsType,
        weight: wrapped.metadata.weight,
        italic: wrapped.metadata.italic,
        format: "eot-uncompressed-xor",
      });
    }
    if (!faceResults.length) {
      throw new Error(`Embedded PowerPoint typeface has no faces: ${typeface}.`);
    }
    declarations.push(
      `<p:embeddedFont><p:font typeface="${xmlEscapeAttribute(typeface)}" ` +
        `pitchFamily="${pitchFamily}" charset="${charset.presentation}"/>${faceDeclarations.join("")}` +
        `</p:embeddedFont>`
    );
    embeddedFonts.push({
      typeface,
      pitchFamily,
      charset: charset.presentation,
      faces: faceResults,
    });
  }

  presentation = ensureEmbedTrueTypeFonts(presentation);
  presentation = insertEmbeddedFontList(
    presentation,
    `<p:embeddedFontLst>${declarations.join("")}</p:embeddedFontLst>`
  );
  entries.set(presentationPath, Buffer.from(presentation, "utf8"));
  entries.set(relationshipsPath, Buffer.from(relationships, "utf8"));
  entries.set(contentTypesPath, Buffer.from(contentTypes, "utf8"));
  const output = writePptxArchive(entries);
  // Re-read our output so truncated or internally inconsistent packages fail
  // before the caller can label embedding as applied.
  readPptxArchive(output);
  const embeddedFontFiles = embeddedFonts.reduce(
    (sum, item) => sum + item.faces.length,
    0
  );
  return {
    pptx: output,
    result: {
      requested: true,
      applied: embeddedFontFiles > 0,
      embeddedFontFiles,
      embeddedTypefaces: embeddedFonts.length,
      fonts: embeddedFonts,
    },
  };
}
