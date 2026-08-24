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
  licenseDescriptions: readonly string[];
  licenseUrls: readonly string[];
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
  | "licenseDescriptions" | "licenseUrls"
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
    [13, new Set()],
    [14, new Set()],
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
    licenseDescriptions: [...(names.get(13) ?? [])].sort(),
    licenseUrls: [...(names.get(14) ?? [])].sort(),
  };
}

function inspectSfntFace(buffer: Buffer, faceOffset: number): SfntFontInspection {
  if (faceOffset < 0 || faceOffset + 12 > buffer.length) throw new Error("font source is too small");
  const signature = buffer.toString("ascii", faceOffset, faceOffset + 4);
  const scalarType = u32(buffer, faceOffset);
  const format =
    scalarType === 0x00010000 || signature === "true"
      ? "ttf"
      : signature === "OTTO"
        ? "otf"
        : undefined;
  if (!format) throw new Error("unsupported SFNT signature");

  const numTables = u16(buffer, faceOffset + 4);
  if (numTables < 1 || faceOffset + 12 + numTables * 16 > buffer.length) {
    throw new Error("invalid SFNT table directory");
  }
  const tables = new Map<string, { offset: number; length: number }>();
  for (let index = 0; index < numTables; index += 1) {
    const recordOffset = faceOffset + 12 + index * 16;
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

export function inspectSfntFont(buffer: Buffer): SfntFontInspection {
  if (buffer.toString("ascii", 0, 4) === "ttcf") throw new Error("sfnt_collection_requires_face_selection");
  return inspectSfntFace(buffer, 0);
}

export interface SfntCollectionInspection {
  readonly format: "ttc";
  readonly version: number;
  readonly faces: readonly (SfntFontInspection & { readonly faceIndex: number })[];
}

interface SfntTableRecord {
  readonly tag: string;
  readonly checksum: number;
  readonly offset: number;
  readonly length: number;
}

function collectionFaceOffsets(buffer: Buffer): number[] {
  if (buffer.length < 12 || buffer.toString("ascii", 0, 4) !== "ttcf") throw new Error("unsupported TTC signature");
  const count = u32(buffer, 8);
  if (count < 1 || count > 256 || 12 + count * 4 > buffer.length) throw new Error("invalid TTC face directory");
  const offsets = Array.from({ length: count }, (_, index) => u32(buffer, 12 + index * 4));
  if (new Set(offsets).size !== offsets.length) throw new Error("invalid TTC duplicate face offset");
  return offsets;
}

function tableDirectory(buffer: Buffer, faceOffset: number): SfntTableRecord[] {
  if (faceOffset < 0 || faceOffset + 12 > buffer.length) throw new Error("invalid TTC face offset");
  const count = u16(buffer, faceOffset + 4);
  if (count < 1 || count > 4096 || faceOffset + 12 + count * 16 > buffer.length) throw new Error("invalid SFNT table directory");
  const seen = new Set<string>();
  return Array.from({ length: count }, (_, index) => {
    const at = faceOffset + 12 + index * 16;
    const tag = buffer.toString("ascii", at, at + 4);
    if (!/^[\x20-\x7e]{4}$/.test(tag) || seen.has(tag)) throw new Error("invalid SFNT duplicate or non-ASCII table tag");
    seen.add(tag);
    const offset = u32(buffer, at + 8); const length = u32(buffer, at + 12);
    if (offset > buffer.length || length > buffer.length - offset) throw new Error(`invalid SFNT table bounds: ${tag}`);
    return { tag, checksum: u32(buffer, at + 4), offset, length };
  });
}

function checksum(buffer: Buffer, offset = 0, length = buffer.length, zeroAdjustmentAt?: number): number {
  let sum = 0;
  const paddedLength = Math.ceil(length / 4) * 4;
  for (let relative = 0; relative < paddedLength; relative += 4) {
    let word = 0;
    for (let byte = 0; byte < 4; byte += 1) {
      const absolute = offset + relative + byte;
      const value = relative + byte < length && absolute < buffer.length &&
          !(zeroAdjustmentAt !== undefined && absolute >= zeroAdjustmentAt && absolute < zeroAdjustmentAt + 4)
        ? buffer[absolute]
        : 0;
      word = (word * 256 + value) >>> 0;
    }
    sum = (sum + word) >>> 0;
  }
  return sum >>> 0;
}

export interface ExtractedSfntCollectionFace {
  readonly data: Buffer;
  readonly faceIndex: number;
  readonly inspection: SfntFontInspection;
}

/** Copy one TTC face into a self-contained SFNT without mutating collection bytes. */
export function extractSfntCollectionFace(buffer: Buffer, faceIndex: number): ExtractedSfntCollectionFace {
  const offsets = collectionFaceOffsets(buffer);
  if (!Number.isSafeInteger(faceIndex) || faceIndex < 0 || faceIndex >= offsets.length) throw new Error("sfnt_collection_face_not_found");
  const directories = offsets.map((offset) => tableDirectory(buffer, offset));
  const ranges = directories.flatMap((tables) => tables.map((table) => table));
  for (let left = 0; left < ranges.length; left += 1) {
    for (let right = left + 1; right < ranges.length; right += 1) {
      const a = ranges[left]; const b = ranges[right];
      const overlap = a.offset < b.offset + b.length && b.offset < a.offset + a.length;
      const exactSafeShare = a.offset === b.offset && a.length === b.length && a.tag === b.tag && a.checksum === b.checksum;
      if (overlap && !exactSafeShare) throw new Error("sfnt_collection_unsafe_shared_table");
    }
  }
  const sourceTables = directories[faceIndex];
  if (sourceTables.some((table) => table.tag === "fvar" || table.tag === "gvar")) throw new Error("sfnt_collection_variable_face_unsupported");
  const head = sourceTables.find((table) => table.tag === "head");
  if (!head || head.length < 12) throw new Error("sfnt_collection_invalid_head_table");
  for (const table of sourceTables) {
    const actual = checksum(buffer, table.offset, table.length, table.tag === "head" ? table.offset + 8 : undefined);
    if (actual !== table.checksum) throw new Error(`sfnt_collection_table_checksum_mismatch:${table.tag}`);
  }
  const tables = [...sourceTables].sort((a, b) => a.tag.localeCompare(b.tag, "en-US"));
  const directoryBytes = 12 + tables.length * 16;
  let outputLength = directoryBytes;
  const destinations = tables.map((table) => { const offset = outputLength; outputLength += Math.ceil(table.length / 4) * 4; return offset; });
  const output = Buffer.alloc(outputLength);
  buffer.copy(output, 0, offsets[faceIndex], offsets[faceIndex] + 4);
  output.writeUInt16BE(tables.length, 4);
  const maxPower = 2 ** Math.floor(Math.log2(tables.length));
  output.writeUInt16BE(maxPower * 16, 6);
  output.writeUInt16BE(Math.log2(maxPower), 8);
  output.writeUInt16BE(tables.length * 16 - maxPower * 16, 10);
  tables.forEach((table, index) => {
    const at = 12 + index * 16; output.write(table.tag, at, 4, "ascii"); output.writeUInt32BE(table.checksum, at + 4); output.writeUInt32BE(destinations[index], at + 8); output.writeUInt32BE(table.length, at + 12);
    buffer.copy(output, destinations[index], table.offset, table.offset + table.length);
  });
  const outputHead = destinations[tables.findIndex((table) => table.tag === "head")];
  output.writeUInt32BE(0, outputHead + 8);
  output.writeUInt32BE((0xb1b0afba - checksum(output)) >>> 0, outputHead + 8);
  if (checksum(output) !== 0xb1b0afba) throw new Error("sfnt_collection_output_checksum_invalid");
  return { data: output, faceIndex, inspection: inspectSfntFont(output) };
}

export function inspectSfntCollection(buffer: Buffer): SfntCollectionInspection {
  const offsets = collectionFaceOffsets(buffer);
  return { format: "ttc", version: u32(buffer, 4), faces: offsets.map((offset, faceIndex) => ({ faceIndex, ...inspectSfntFace(buffer, offset) })) };
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

