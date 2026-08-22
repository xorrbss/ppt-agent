import { deflateRawSync, inflateRawSync } from "node:zlib";
import path from "node:path";

const EOCD_SIGNATURE = 0x06054b50;
const CENTRAL_SIGNATURE = 0x02014b50;
const LOCAL_SIGNATURE = 0x04034b50;
const MAX_EOCD_SEARCH = 65_557;
const MAX_ENTRY_COUNT = 2_000;
const MAX_ENTRY_BYTES = 128 * 1024 * 1024;
const MAX_TOTAL_BYTES = 512 * 1024 * 1024;
const MAX_COMPRESSION_RATIO = 1_000;
const CUSTOM_PROPERTIES_CONTENT_TYPE =
  "application/vnd.openxmlformats-officedocument.custom-properties+xml";
const CUSTOM_PROPERTIES_RELATIONSHIP =
  "http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties";
const CUSTOM_PROPERTIES_NAMESPACE =
  "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties";
const CUSTOM_PROPERTIES_FMTID = "{D5CDD505-2E9C-101B-9397-08002B2CF9AE}";
const VT_NAMESPACE =
  "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes";

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

function validXmlCodePoint(codePoint: number): boolean {
  return codePoint === 0x9 || codePoint === 0xa || codePoint === 0xd ||
    (codePoint >= 0x20 && codePoint <= 0xd7ff) ||
    (codePoint >= 0xe000 && codePoint <= 0xfffd) ||
    (codePoint >= 0x10000 && codePoint <= 0x10ffff);
}

export function decodeXmlAttributeValue(value: string): string | null {
  let decoded = "";
  let cursor = 0;
  while (cursor < value.length) {
    const start = value.indexOf("&", cursor);
    const end = start === -1 ? value.length : start;
    const literal = value.slice(cursor, end);
    if (/[<\0]/.test(literal)) return null;
    decoded += literal;
    if (start === -1) break;
    const semicolon = value.indexOf(";", start + 1);
    if (semicolon === -1) return null;
    const entity = value.slice(start + 1, semicolon);
    const predefined: Readonly<Record<string, string>> = {
      amp: "&", apos: "'", gt: ">", lt: "<", quot: '"',
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
    cursor = semicolon + 1;
  }
  return decoded;
}

export function rawXmlAttribute(tag: string, attribute: string): string | null {
  return tag.match(new RegExp(`(?:^|\\s)${attribute}\\s*=\\s*(["'])(.*?)\\1`, "i"))?.[2] ?? null;
}

export function xmlAttributeValue(tag: string, attribute: string): string | null {
  const raw = rawXmlAttribute(tag, attribute);
  return raw === null ? null : decodeXmlAttributeValue(raw);
}

export function opcPartComparisonKey(partPath: string): string {
  return partPath.replace(/[A-Z]/g, (character) => character.toLowerCase());
}

export function relationshipTargetPath(relsPath: string, target: string): string | null {
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
  return !resolved || resolved === ".." || resolved.startsWith("../") ? null : resolved;
}

export function referencedPackageTargets(entries: ReadonlyMap<string, Buffer>): {
  targets: ReadonlySet<string>;
  complete: boolean;
} {
  const targets = new Set<string>();
  let complete = true;
  for (const [relsPath, bytes] of entries) {
    if (!opcPartComparisonKey(relsPath).endsWith(".rels")) continue;
    for (const match of bytes.toString("utf8").matchAll(
      /<(?:[A-Za-z_][\w.-]*:)?Relationship\b[^>]*>/gi
    )) {
      const rawMode = rawXmlAttribute(match[0], "TargetMode");
      const mode = rawMode === null ? null : decodeXmlAttributeValue(rawMode);
      if (rawMode !== null && mode === null) { complete = false; continue; }
      if (mode?.toLowerCase() === "external") continue;
      const rawTarget = rawXmlAttribute(match[0], "Target");
      const target = rawTarget === null ? null : decodeXmlAttributeValue(rawTarget);
      const targetPath = target === null ? null : relationshipTargetPath(relsPath, target);
      if (targetPath) targets.add(opcPartComparisonKey(targetPath));
      else complete = false;
    }
  }
  return { targets, complete };
}

function xmlEscape(value: string): string {
  return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;")
    .replaceAll("\r", "&#xD;").replaceAll("\n", "&#xA;");
}

function appendXmlChild(xml: string, root: string, child: string): string {
  const close = new RegExp(`</(?:[A-Za-z_][\\w.-]*:)?${root}\\s*>`, "i");
  if (close.test(xml)) return xml.replace(close, (closing) => `${child}${closing}`);
  const empty = new RegExp(`<((?:[A-Za-z_][\\w.-]*:)?${root})\\b([^>]*)/\\s*>`, "i");
  if (empty.test(xml)) return xml.replace(empty, `<$1$2>${child}</$1>`);
  throw new Error(`PPTX ${root} document is malformed.`);
}

function nextRelationshipId(xml: string): string {
  let maximum = 0;
  for (const match of xml.matchAll(/<(?:[A-Za-z_][\w.-]*:)?Relationship\b[^>]*>/gi)) {
    const id = xmlAttributeValue(match[0], "Id");
    const number = id?.match(/^rId(\d+)$/i)?.[1];
    if (number !== undefined) {
      const value = Number(number);
      if (!Number.isSafeInteger(value)) throw new Error("PPTX relationship Id is out of range.");
      maximum = Math.max(maximum, value);
    }
  }
  return `rId${maximum + 1}`;
}

export interface PptxCustomPropertyInput {
  readonly name: string;
  readonly value: string | number;
}

/** Upsert scalar custom properties and all OPC declarations required by PowerPoint. */
export function upsertPptxCustomProperties(
  entries: Map<string, Buffer>,
  properties: readonly PptxCustomPropertyInput[]
): void {
  if (!properties.length) return;
  const names = new Set<string>();
  for (const property of properties) {
    if (!property.name || property.name.length > 255 || /[\0\r\n]/.test(property.name) || names.has(property.name)) {
      throw new Error("PPTX custom property name is invalid or duplicated.");
    }
    if (typeof property.value === "number" && !Number.isSafeInteger(property.value)) {
      throw new Error(`PPTX custom property ${property.name} is not a safe integer.`);
    }
    if (typeof property.value === "string" && (property.value.length > 4_096 || /\0/.test(property.value))) {
      throw new Error(`PPTX custom property ${property.name} is invalid.`);
    }
    names.add(property.name);
  }

  const contentTypesPath = "[Content_Types].xml";
  let contentTypes = entries.get(contentTypesPath)?.toString("utf8");
  if (!contentTypes) throw new Error("PPTX content types document is missing.");
  const customOverrides = [...contentTypes.matchAll(/<Override\b[^>]*>/gi)].filter(
    (match) => xmlAttributeValue(match[0], "PartName")?.toLowerCase() === "/docprops/custom.xml"
  );
  if (customOverrides.length > 1 || (customOverrides[0] && xmlAttributeValue(customOverrides[0][0], "ContentType") !== CUSTOM_PROPERTIES_CONTENT_TYPE)) {
    throw new Error("PPTX custom-properties content type is inconsistent.");
  }
  if (!customOverrides.length) {
    contentTypes = appendXmlChild(contentTypes, "Types", `<Override PartName="/docProps/custom.xml" ContentType="${CUSTOM_PROPERTIES_CONTENT_TYPE}"/>`);
  }

  const rootRelationshipsPath = "_rels/.rels";
  let rootRelationships = entries.get(rootRelationshipsPath)?.toString("utf8");
  if (!rootRelationships) throw new Error("PPTX root relationships document is missing.");
  const customRelationships = [...rootRelationships.matchAll(
    /<(?:[A-Za-z_][\w.-]*:)?Relationship\b[^>]*>/gi
  )].filter((match) => xmlAttributeValue(match[0], "Type") === CUSTOM_PROPERTIES_RELATIONSHIP);
  if (customRelationships.length > 1) throw new Error("PPTX has duplicate custom-properties relationships.");
  if (customRelationships[0]) {
    const target = xmlAttributeValue(customRelationships[0][0], "Target");
    if (!target || relationshipTargetPath(rootRelationshipsPath, target)?.toLowerCase() !== "docprops/custom.xml") {
      throw new Error("PPTX custom-properties relationship target is inconsistent.");
    }
  } else {
    const id = nextRelationshipId(rootRelationships);
    rootRelationships = appendXmlChild(rootRelationships, "Relationships", `<Relationship Id="${id}" Type="${CUSTOM_PROPERTIES_RELATIONSHIP}" Target="docProps/custom.xml"/>`);
  }

  const customPath = "docProps/custom.xml";
  let customXml = entries.get(customPath)?.toString("utf8") ??
    `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Properties xmlns="${CUSTOM_PROPERTIES_NAMESPACE}" xmlns:vt="${VT_NAMESPACE}"></Properties>`;
  const opening = customXml.match(/<(?:[A-Za-z_][\w.-]*:)?Properties\b[^>]*>/i)?.[0];
  if (!opening || !new RegExp(`xmlns(?:\\s*=|:[^=]+\\s*=)["']${CUSTOM_PROPERTIES_NAMESPACE.replaceAll("/", "\\/")}["']`, "i").test(opening)) {
    throw new Error("PPTX custom-properties document has an incompatible namespace.");
  }
  const vtBinding = opening.match(/\bxmlns:vt\s*=\s*(["'])(.*?)\1/i)?.[2];
  if (vtBinding && vtBinding !== VT_NAMESPACE) throw new Error("PPTX custom-properties vt namespace is incompatible.");
  if (!vtBinding) customXml = customXml.replace(opening, opening.replace(/>$/, ` xmlns:vt="${VT_NAMESPACE}">`));

  const existingPids = new Set<number>();
  const seenPids = new Set<number>();
  const existingNames = new Set<string>();
  const propertyPattern = /<((?:[A-Za-z_][\w.-]*:)?property)\b[^>]*>[\s\S]*?<\/\1\s*>/gi;
  customXml = customXml.replace(propertyPattern, (propertyXml) => {
    const propertyOpening = propertyXml.match(/<(?:[A-Za-z_][\w.-]*:)?property\b[^>]*>/i)?.[0] ?? "";
    const name = xmlAttributeValue(propertyOpening, "name");
    const pid = Number(xmlAttributeValue(propertyOpening, "pid"));
    if (!name || !Number.isSafeInteger(pid) || pid < 2 || seenPids.has(pid) || existingNames.has(name)) {
      throw new Error("PPTX custom-properties document has invalid or duplicate properties.");
    }
    seenPids.add(pid);
    existingNames.add(name);
    if (names.has(name)) return "";
    existingPids.add(pid);
    return propertyXml;
  });
  let nextPid = 2;
  const additions = properties.map((property) => {
    while (existingPids.has(nextPid)) nextPid += 1;
    const pid = nextPid++;
    const value = typeof property.value === "number"
      ? `<vt:i8>${property.value}</vt:i8>`
      : `<vt:lpwstr>${xmlEscape(property.value)}</vt:lpwstr>`;
    return `<property fmtid="${CUSTOM_PROPERTIES_FMTID}" pid="${pid}" name="${xmlEscape(property.name)}">${value}</property>`;
  }).join("");
  customXml = appendXmlChild(customXml, "Properties", additions);

  entries.set(contentTypesPath, Buffer.from(contentTypes, "utf8"));
  entries.set(rootRelationshipsPath, Buffer.from(rootRelationships, "utf8"));
  entries.set(customPath, Buffer.from(customXml, "utf8"));
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
