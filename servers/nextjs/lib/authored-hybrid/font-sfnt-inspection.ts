/**
 * SFNT name-table inspection and OS/2 embedding-permission assessment.
 *
 * Answers "what is this font and may it be embedded" from the font bytes
 * alone. The allowlist and plan resolution that consume these answers live
 * in font-embedding-policy.
 */

export type EmbeddedFontFaceKind =
  | "regular"
  | "bold"
  | "italic"
  | "boldItalic";
export type FontEmbeddingStrategy = "full" | "subset";

export interface SfntFontInspection {
  format: "ttf" | "otf";
  familyNames: readonly string[];
  fullNames: readonly string[];
  postscriptNames: readonly string[];
  tableTags: readonly string[];
  weightClass: number;
  fsType: number;
  variable: boolean;
}

export interface FontEmbeddingLicenseAssessment {
  allowed: boolean;
  decision:
    | "allowed-installable"
    | "allowed-editable"
    | "denied-restricted"
    | "denied-preview-print"
    | "denied-bitmap-only"
    | "denied-invalid";
  fsType: number;
  noSubsetting: boolean;
  strategyAllowed: boolean;
}


function u16(buffer: Buffer, offset: number): number {
  if (offset < 0 || offset + 2 > buffer.length) {
    throw new Error("truncated SFNT uint16");
  }
  return buffer.readUInt16BE(offset);
}

function u32(buffer: Buffer, offset: number): number {
  if (offset < 0 || offset + 4 > buffer.length) {
    throw new Error("truncated SFNT uint32");
  }
  return buffer.readUInt32BE(offset);
}

function decodeUtf16Be(value: Buffer): string {
  if (value.length % 2 !== 0) return "";
  const swapped = Buffer.allocUnsafe(value.length);
  for (let index = 0; index < value.length; index += 2) {
    swapped[index] = value[index + 1];
    swapped[index + 1] = value[index];
  }
  return swapped.toString("utf16le").replace(/\u0000/g, "").trim();
}

function readNameTable(
  buffer: Buffer,
  tableOffset: number,
  tableLength: number
): Pick<
  SfntFontInspection,
  "familyNames" | "fullNames" | "postscriptNames"
> {
  if (tableLength < 6 || tableOffset + tableLength > buffer.length) {
    throw new Error("invalid name table bounds");
  }
  const count = u16(buffer, tableOffset + 2);
  const stringsOffset = u16(buffer, tableOffset + 4);
  const recordsEnd = tableOffset + 6 + count * 12;
  const stringsStart = tableOffset + stringsOffset;
  if (recordsEnd > tableOffset + tableLength || stringsStart > tableOffset + tableLength) {
    throw new Error("invalid name table records");
  }

  const names = new Map<number, Set<string>>([
    [1, new Set()],
    [4, new Set()],
    [6, new Set()],
    [16, new Set()],
  ]);
  for (let index = 0; index < count; index += 1) {
    const recordOffset = tableOffset + 6 + index * 12;
    const platformId = u16(buffer, recordOffset);
    const nameId = u16(buffer, recordOffset + 6);
    const length = u16(buffer, recordOffset + 8);
    const offset = u16(buffer, recordOffset + 10);
    if (!names.has(nameId)) continue;
    const start = stringsStart + offset;
    const end = start + length;
    if (start < stringsStart || end > tableOffset + tableLength) continue;
    const raw = buffer.subarray(start, end);
    const decoded =
      platformId === 0 || platformId === 3
        ? decodeUtf16Be(raw)
        : raw.toString("latin1").replace(/\u0000/g, "").trim();
    if (decoded) names.get(nameId)?.add(decoded);
  }
  const familyNames = new Set([
    ...(names.get(1) ?? []),
    ...(names.get(16) ?? []),
  ]);
  return {
    familyNames: [...familyNames].sort(),
    fullNames: [...(names.get(4) ?? [])].sort(),
    postscriptNames: [...(names.get(6) ?? [])].sort(),
  };
}

export function inspectSfntFont(buffer: Buffer): SfntFontInspection {
  if (buffer.length < 12) throw new Error("font source is too small");
  const signature = buffer.toString("ascii", 0, 4);
  const scalarType = u32(buffer, 0);
  const format =
    scalarType === 0x00010000 || signature === "true"
      ? "ttf"
      : signature === "OTTO"
        ? "otf"
        : undefined;
  if (!format) throw new Error("unsupported SFNT signature");

  const numTables = u16(buffer, 4);
  if (numTables < 1 || 12 + numTables * 16 > buffer.length) {
    throw new Error("invalid SFNT table directory");
  }
  const tables = new Map<string, { offset: number; length: number }>();
  for (let index = 0; index < numTables; index += 1) {
    const recordOffset = 12 + index * 16;
    const tag = buffer.toString("ascii", recordOffset, recordOffset + 4);
    const offset = u32(buffer, recordOffset + 8);
    const length = u32(buffer, recordOffset + 12);
    if (offset > buffer.length || length > buffer.length - offset) {
      throw new Error(`invalid SFNT table bounds: ${tag}`);
    }
    tables.set(tag, { offset, length });
  }
  const os2 = tables.get("OS/2");
  const name = tables.get("name");
  if (!os2 || os2.length < 10) throw new Error("font is missing a valid OS/2 table");
  if (!name) throw new Error("font is missing a name table");
  const parsedNames = readNameTable(buffer, name.offset, name.length);
  return {
    format,
    ...parsedNames,
    tableTags: [...tables.keys()].sort(),
    weightClass: u16(buffer, os2.offset + 4),
    fsType: u16(buffer, os2.offset + 8),
    variable: tables.has("fvar") || tables.has("gvar"),
  };
}

export function assessEmbeddingLicense(
  fsType: number,
  strategy: FontEmbeddingStrategy
): FontEmbeddingLicenseAssessment {
  const normalized = fsType & 0xffff;
  const restricted = (normalized & 0x0002) !== 0;
  const previewPrint = (normalized & 0x0004) !== 0;
  const editable = (normalized & 0x0008) !== 0;
  const bitmapOnly = (normalized & 0x0200) !== 0;
  const noSubsetting = (normalized & 0x0100) !== 0;
  const mutuallyExclusiveBits = Number(restricted) + Number(previewPrint) + Number(editable);
  let decision: FontEmbeddingLicenseAssessment["decision"];
  if (mutuallyExclusiveBits > 1) decision = "denied-invalid";
  else if (restricted) decision = "denied-restricted";
  else if (previewPrint) decision = "denied-preview-print";
  else if (bitmapOnly) decision = "denied-bitmap-only";
  else if (editable) decision = "allowed-editable";
  else decision = "allowed-installable";
  const allowed = decision.startsWith("allowed-");
  return {
    allowed,
    decision,
    fsType: normalized,
    noSubsetting,
    strategyAllowed: allowed && !(strategy === "subset" && noSubsetting),
  };
}

