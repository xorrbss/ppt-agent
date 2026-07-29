import { deflateRawSync, inflateRawSync } from "node:zlib";

const EOCD_SIGNATURE = 0x06054b50;
const CENTRAL_SIGNATURE = 0x02014b50;
const LOCAL_SIGNATURE = 0x04034b50;
const MAX_EOCD_SEARCH = 65_557;
const MAX_ENTRY_COUNT = 2_000;
const MAX_ENTRY_BYTES = 128 * 1024 * 1024;
const MAX_TOTAL_BYTES = 512 * 1024 * 1024;
const MAX_COMPRESSION_RATIO = 1_000;

let crcTable: Uint32Array | undefined;

function getCrcTable(): Uint32Array {
  if (crcTable) return crcTable;
  crcTable = new Uint32Array(256);
  for (let index = 0; index < 256; index += 1) {
    let value = index;
    for (let bit = 0; bit < 8; bit += 1) {
      value = (value >>> 1) ^ (value & 1 ? 0xedb88320 : 0);
    }
    crcTable[index] = value >>> 0;
  }
  return crcTable;
}

export function crc32(bytes: Buffer): number {
  const table = getCrcTable();
  let value = 0xffffffff;
  for (const byte of bytes) value = table[(value ^ byte) & 0xff] ^ (value >>> 8);
  return (value ^ 0xffffffff) >>> 0;
}

function assertArchivePath(name: string): void {
  if (
    !name ||
    name.includes("\0") ||
    name.includes("\\") ||
    name.startsWith("/") ||
    /^[A-Za-z]:/.test(name) ||
    name.split("/").some((part) => part === ".." || part === ".")
  ) {
    throw new Error("PPTX archive contains an unsafe entry path.");
  }
}

function findEndOfCentralDirectory(bytes: Buffer): number {
  const start = Math.max(0, bytes.length - MAX_EOCD_SEARCH);
  for (let offset = bytes.length - 22; offset >= start; offset -= 1) {
    if (bytes.readUInt32LE(offset) === EOCD_SIGNATURE) return offset;
  }
  throw new Error("PPTX archive end record was not found.");
}

export function readPptxArchive(bytes: Buffer): Map<string, Buffer> {
  if (bytes.length < 22) throw new Error("PPTX archive is truncated.");
  const eocd = findEndOfCentralDirectory(bytes);
  const disk = bytes.readUInt16LE(eocd + 4);
  const centralDisk = bytes.readUInt16LE(eocd + 6);
  const diskEntries = bytes.readUInt16LE(eocd + 8);
  const entryCount = bytes.readUInt16LE(eocd + 10);
  const centralSize = bytes.readUInt32LE(eocd + 12);
  const centralOffset = bytes.readUInt32LE(eocd + 16);
  const commentLength = bytes.readUInt16LE(eocd + 20);
  if (eocd + 22 + commentLength !== bytes.length) {
    throw new Error("PPTX archive has trailing or malformed data.");
  }
  if (disk !== 0 || centralDisk !== 0 || diskEntries !== entryCount) {
    throw new Error("Multi-disk PPTX archives are unsupported.");
  }
  if (entryCount === 0xffff || centralSize === 0xffffffff || centralOffset === 0xffffffff) {
    throw new Error("ZIP64 PPTX archives are unsupported.");
  }
  if (entryCount > MAX_ENTRY_COUNT || centralOffset + centralSize > eocd) {
    throw new Error("PPTX archive central directory exceeds safety limits.");
  }

  const result = new Map<string, Buffer>();
  let totalBytes = 0;
  let offset = centralOffset;
  for (let index = 0; index < entryCount; index += 1) {
    if (offset + 46 > eocd || bytes.readUInt32LE(offset) !== CENTRAL_SIGNATURE) {
      throw new Error("PPTX archive central directory is malformed.");
    }
    const flags = bytes.readUInt16LE(offset + 8);
    const method = bytes.readUInt16LE(offset + 10);
    const expectedCrc = bytes.readUInt32LE(offset + 16);
    const compressedSize = bytes.readUInt32LE(offset + 20);
    const uncompressedSize = bytes.readUInt32LE(offset + 24);
    const nameLength = bytes.readUInt16LE(offset + 28);
    const extraLength = bytes.readUInt16LE(offset + 30);
    const commentSize = bytes.readUInt16LE(offset + 32);
    const entryDisk = bytes.readUInt16LE(offset + 34);
    const localOffset = bytes.readUInt32LE(offset + 42);
    const centralEnd = offset + 46 + nameLength + extraLength + commentSize;
    if (centralEnd > eocd) throw new Error("PPTX archive entry is truncated.");
    if (entryDisk !== 0 || flags & 0x0001 || !new Set([0, 8]).has(method)) {
      throw new Error("PPTX archive uses an unsupported or encrypted entry.");
    }
    if (
      uncompressedSize > MAX_ENTRY_BYTES ||
      totalBytes + uncompressedSize > MAX_TOTAL_BYTES ||
      (compressedSize === 0 ? uncompressedSize > 0 : uncompressedSize / compressedSize > MAX_COMPRESSION_RATIO)
    ) {
      throw new Error("PPTX archive entry exceeds decompression safety limits.");
    }
    const name = bytes.toString("utf8", offset + 46, offset + 46 + nameLength);
    assertArchivePath(name);
    if (result.has(name)) throw new Error("PPTX archive contains duplicate entries.");

    if (localOffset + 30 > centralOffset || bytes.readUInt32LE(localOffset) !== LOCAL_SIGNATURE) {
      throw new Error("PPTX archive local entry is malformed.");
    }
    const localFlags = bytes.readUInt16LE(localOffset + 6);
    const localMethod = bytes.readUInt16LE(localOffset + 8);
    const localNameLength = bytes.readUInt16LE(localOffset + 26);
    const localExtraLength = bytes.readUInt16LE(localOffset + 28);
    const dataStart = localOffset + 30 + localNameLength + localExtraLength;
    const dataEnd = dataStart + compressedSize;
    if (localFlags & 0x0001 || localMethod !== method || dataEnd > centralOffset) {
      throw new Error("PPTX archive local entry metadata is inconsistent.");
    }
    const localName = bytes.toString(
      "utf8",
      localOffset + 30,
      localOffset + 30 + localNameLength
    );
    if (localName !== name) throw new Error("PPTX archive entry names disagree.");
    const compressed = bytes.subarray(dataStart, dataEnd);
    const content =
      method === 0
        ? Buffer.from(compressed)
        : inflateRawSync(compressed, {
            maxOutputLength: Math.max(1, uncompressedSize),
          });
    if (content.length !== uncompressedSize || crc32(content) !== expectedCrc) {
      throw new Error("PPTX archive entry failed size or CRC validation.");
    }
    result.set(name, content);
    totalBytes += content.length;
    offset = centralEnd;
  }
  if (offset !== centralOffset + centralSize) {
    throw new Error("PPTX archive central directory size is inconsistent.");
  }
  return result;
}

interface EncodedEntry {
  name: Buffer;
  content: Buffer;
  compressed: Buffer;
  method: 0 | 8;
  crc: number;
  offset: number;
}

export function writePptxArchive(entries: ReadonlyMap<string, Buffer>): Buffer {
  if (entries.size === 0 || entries.size > MAX_ENTRY_COUNT) {
    throw new Error("PPTX archive entry count is invalid.");
  }
  let totalBytes = 0;
  const encoded: EncodedEntry[] = [];
  for (const [name, contentValue] of entries) {
    assertArchivePath(name);
    const content = Buffer.from(contentValue);
    totalBytes += content.length;
    if (content.length > MAX_ENTRY_BYTES || totalBytes > MAX_TOTAL_BYTES) {
      throw new Error("PPTX archive output exceeds safety limits.");
    }
    const nameBytes = Buffer.from(name, "utf8");
    if (nameBytes.length > 0xffff) throw new Error("PPTX archive entry name is too long.");
    const deflated = deflateRawSync(content, { level: 6 });
    const useDeflate = deflated.length < content.length;
    encoded.push({
      name: nameBytes,
      content,
      compressed: useDeflate ? deflated : content,
      method: useDeflate ? 8 : 0,
      crc: crc32(content),
      offset: 0,
    });
  }

  const localParts: Buffer[] = [];
  let localOffset = 0;
  for (const entry of encoded) {
    entry.offset = localOffset;
    const header = Buffer.alloc(30);
    header.writeUInt32LE(LOCAL_SIGNATURE, 0);
    header.writeUInt16LE(20, 4);
    header.writeUInt16LE(0x0800, 6);
    header.writeUInt16LE(entry.method, 8);
    header.writeUInt16LE(0, 10);
    header.writeUInt16LE(0x0021, 12);
    header.writeUInt32LE(entry.crc, 14);
    header.writeUInt32LE(entry.compressed.length, 18);
    header.writeUInt32LE(entry.content.length, 22);
    header.writeUInt16LE(entry.name.length, 26);
    header.writeUInt16LE(0, 28);
    localParts.push(header, entry.name, entry.compressed);
    localOffset += header.length + entry.name.length + entry.compressed.length;
    if (localOffset > 0xffffffff) throw new Error("PPTX archive output requires ZIP64.");
  }

  const centralParts: Buffer[] = [];
  let centralSize = 0;
  for (const entry of encoded) {
    const header = Buffer.alloc(46);
    header.writeUInt32LE(CENTRAL_SIGNATURE, 0);
    header.writeUInt16LE(20, 4);
    header.writeUInt16LE(20, 6);
    header.writeUInt16LE(0x0800, 8);
    header.writeUInt16LE(entry.method, 10);
    header.writeUInt16LE(0, 12);
    header.writeUInt16LE(0x0021, 14);
    header.writeUInt32LE(entry.crc, 16);
    header.writeUInt32LE(entry.compressed.length, 20);
    header.writeUInt32LE(entry.content.length, 24);
    header.writeUInt16LE(entry.name.length, 28);
    header.writeUInt16LE(0, 30);
    header.writeUInt16LE(0, 32);
    header.writeUInt16LE(0, 34);
    header.writeUInt16LE(0, 36);
    header.writeUInt32LE(0, 38);
    header.writeUInt32LE(entry.offset, 42);
    centralParts.push(header, entry.name);
    centralSize += header.length + entry.name.length;
  }

  const eocd = Buffer.alloc(22);
  eocd.writeUInt32LE(EOCD_SIGNATURE, 0);
  eocd.writeUInt16LE(0, 4);
  eocd.writeUInt16LE(0, 6);
  eocd.writeUInt16LE(encoded.length, 8);
  eocd.writeUInt16LE(encoded.length, 10);
  eocd.writeUInt32LE(centralSize, 12);
  eocd.writeUInt32LE(localOffset, 16);
  eocd.writeUInt16LE(0, 20);
  return Buffer.concat([...localParts, ...centralParts, eocd]);
}
