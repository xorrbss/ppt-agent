/**
 * SFNT parsing and the EOT wrapper PowerPoint expects in ppt/fonts/*.fntdata.
 *
 * Pure byte-level work: no OOXML package structure and no filesystem access,
 * so the font format rules can be exercised without building a .pptx.
 */

const EOT_XOR_ENCRYPT_DATA = 0x10000000;
const EOT_SUBSET = 0x00000001;

export interface SfntMetadata {
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


export function normalizePowerPointCharset(value: number): {
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

