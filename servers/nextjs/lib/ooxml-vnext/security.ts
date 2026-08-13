import { posix } from "node:path";

import { readPptxArchive } from "../authored-hybrid/pptx-archive.ts";
import { immutablePackageEntries } from "./security-map.ts";
export { immutablePackageEntries } from "./security-map.ts";

const EOCD_SIGNATURE = 0x06054b50;
const CENTRAL_SIGNATURE = 0x02014b50;
const LOCAL_SIGNATURE = 0x04034b50;
const MAX_EOCD_SEARCH = 65_557;

const ARCHIVE_READER_HARD_LIMITS = Object.freeze({
  maxMembers: 2_000,
  maxMemberUncompressedBytes: 128 * 1024 * 1024,
  maxTotalUncompressedBytes: 512 * 1024 * 1024,
  maxCompressionRatio: 1_000,
});

export interface OoxmlSecurityPolicy {
  maxArchiveBytes: number;
  maxMembers: number;
  maxMemberUncompressedBytes: number;
  maxTotalUncompressedBytes: number;
  maxCompressionRatio: number;
  maxXmlPartBytes: number;
  maxTotalXmlBytes: number;
  maxRelationshipsPerPart: number;
  maxRelationshipsTotal: number;
  maxExternalRelationships: number;
  maxContentTypeEntries: number;
  maxPartNameBytes: number;
}

export const DEFAULT_OOXML_SECURITY_POLICY: Readonly<OoxmlSecurityPolicy> =
  Object.freeze({
    maxArchiveBytes: 512 * 1024 * 1024,
    maxMembers: ARCHIVE_READER_HARD_LIMITS.maxMembers,
    maxMemberUncompressedBytes:
      ARCHIVE_READER_HARD_LIMITS.maxMemberUncompressedBytes,
    maxTotalUncompressedBytes:
      ARCHIVE_READER_HARD_LIMITS.maxTotalUncompressedBytes,
    maxCompressionRatio: 200,
    maxXmlPartBytes: 16 * 1024 * 1024,
    maxTotalXmlBytes: 64 * 1024 * 1024,
    maxRelationshipsPerPart: 4_096,
    maxRelationshipsTotal: 20_000,
    maxExternalRelationships: 1_024,
    maxContentTypeEntries: 4_096,
    maxPartNameBytes: 1_024,
  });

export interface ZipMemberSecurityRecord {
  readonly name: string;
  readonly isDirectory: boolean;
  readonly compressedBytes: number;
  readonly uncompressedBytes: number;
  readonly compressionRatio: number;
  readonly compressionMethod: "store" | "deflate";
}

export interface OoxmlSecurityInspection {
  readonly entries: ReadonlyMap<string, Buffer>;
  readonly members: readonly ZipMemberSecurityRecord[];
  readonly archiveBytes: number;
  readonly totalUncompressedBytes: number;
  readonly totalXmlBytes: number;
  readonly policy: Readonly<OoxmlSecurityPolicy>;
}

export interface OoxmlEntriesSecurityInspection {
  readonly entries: ReadonlyMap<string, Buffer>;
  readonly memberCount: number;
  readonly totalUncompressedBytes: number;
  readonly totalXmlBytes: number;
  readonly policy: Readonly<OoxmlSecurityPolicy>;
}

export class OoxmlSecurityError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "OoxmlSecurityError";
    this.code = code;
  }
}

function reject(code: string, message: string): never {
  throw new OoxmlSecurityError(code, message);
}

function positiveInteger(name: string, value: number): number {
  if (!Number.isSafeInteger(value) || value <= 0) {
    reject("INVALID_POLICY", `${name} must be a positive safe integer.`);
  }
  return value;
}

export function resolveOoxmlSecurityPolicy(
  override: Partial<OoxmlSecurityPolicy> = {}
): Readonly<OoxmlSecurityPolicy> {
  const policy = {
    ...DEFAULT_OOXML_SECURITY_POLICY,
    ...override,
  };
  for (const [name, value] of Object.entries(policy)) {
    positiveInteger(name, value);
  }
  if (policy.maxMembers > ARCHIVE_READER_HARD_LIMITS.maxMembers) {
    reject("INVALID_POLICY", "maxMembers cannot exceed the archive reader hard limit.");
  }
  if (
    policy.maxMemberUncompressedBytes >
    ARCHIVE_READER_HARD_LIMITS.maxMemberUncompressedBytes
  ) {
    reject(
      "INVALID_POLICY",
      "maxMemberUncompressedBytes cannot exceed the archive reader hard limit."
    );
  }
  if (
    policy.maxTotalUncompressedBytes >
    ARCHIVE_READER_HARD_LIMITS.maxTotalUncompressedBytes
  ) {
    reject(
      "INVALID_POLICY",
      "maxTotalUncompressedBytes cannot exceed the archive reader hard limit."
    );
  }
  if (
    policy.maxCompressionRatio >
    ARCHIVE_READER_HARD_LIMITS.maxCompressionRatio
  ) {
    reject(
      "INVALID_POLICY",
      "maxCompressionRatio cannot exceed the archive reader hard limit."
    );
  }
  return Object.freeze(policy);
}

function findEndOfCentralDirectory(bytes: Buffer): number {
  const start = Math.max(0, bytes.length - MAX_EOCD_SEARCH);
  for (let offset = bytes.length - 22; offset >= start; offset -= 1) {
    if (
      bytes.readUInt32LE(offset) === EOCD_SIGNATURE &&
      offset + 22 + bytes.readUInt16LE(offset + 20) === bytes.length
    ) {
      return offset;
    }
  }
  reject("INVALID_ZIP", "PPTX ZIP end record was not found.");
}

export function assertCanonicalOoxmlPartName(
  name: string,
  policy: Readonly<OoxmlSecurityPolicy> = DEFAULT_OOXML_SECURITY_POLICY
): void {
  if (Buffer.byteLength(name, "utf8") > policy.maxPartNameBytes) {
    reject("PART_NAME_LIMIT", `PPTX member name is too long: ${name}`);
  }
  if (
    !name ||
    name.includes("\0") ||
    name.includes("\\") ||
    name.startsWith("/") ||
    name.includes("?") ||
    name.includes("#") ||
    /^[A-Za-z]:/.test(name) ||
    posix.normalize(name) !== name ||
    name.split("/").some((segment) => segment === "." || segment === "..") ||
    /%(?:00|2e|2f|5c)/i.test(name) ||
    /%(?![0-9a-f]{2})/i.test(name)
  ) {
    reject("UNSAFE_PART_NAME", `PPTX member name is unsafe: ${name}`);
  }
}

function assertCanonicalZipMemberName(
  name: string,
  policy: Readonly<OoxmlSecurityPolicy>
): void {
  if (!name.endsWith("/")) {
    assertCanonicalOoxmlPartName(name, policy);
    return;
  }
  const directoryName = name.slice(0, -1);
  if (!directoryName || directoryName.endsWith("/")) {
    reject("UNSAFE_PART_NAME", `PPTX member name is unsafe: ${name}`);
  }
  assertCanonicalOoxmlPartName(directoryName, policy);
}

function decodeMemberName(nameBytes: Buffer): string {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(nameBytes);
  } catch {
    reject("INVALID_PART_NAME_ENCODING", "PPTX member name is not valid UTF-8.");
  }
}

function inspectCentralDirectory(
  bytes: Buffer,
  policy: OoxmlSecurityPolicy
): {
  members: ZipMemberSecurityRecord[];
  totalUncompressedBytes: number;
} {
  if (bytes.length < 22) reject("INVALID_ZIP", "PPTX ZIP is truncated.");
  if (bytes.length > policy.maxArchiveBytes) {
    reject("ARCHIVE_SIZE_LIMIT", "PPTX ZIP exceeds the archive byte limit.");
  }
  const eocd = findEndOfCentralDirectory(bytes);
  const disk = bytes.readUInt16LE(eocd + 4);
  const centralDisk = bytes.readUInt16LE(eocd + 6);
  const diskEntries = bytes.readUInt16LE(eocd + 8);
  const entryCount = bytes.readUInt16LE(eocd + 10);
  const centralSize = bytes.readUInt32LE(eocd + 12);
  const centralOffset = bytes.readUInt32LE(eocd + 16);
  if (disk !== 0 || centralDisk !== 0 || diskEntries !== entryCount) {
    reject("UNSUPPORTED_ZIP", "Multi-disk PPTX ZIP packages are unsupported.");
  }
  if (
    entryCount === 0xffff ||
    centralSize === 0xffffffff ||
    centralOffset === 0xffffffff
  ) {
    reject("UNSUPPORTED_ZIP", "ZIP64 PPTX packages are unsupported.");
  }
  if (entryCount === 0 || entryCount > policy.maxMembers) {
    reject("MEMBER_COUNT_LIMIT", "PPTX ZIP member count is outside the limit.");
  }
  if (centralOffset + centralSize !== eocd) {
    reject("INVALID_ZIP", "PPTX ZIP central directory is malformed.");
  }

  const members: ZipMemberSecurityRecord[] = [];
  const seen = new Set<string>();
  let totalUncompressedBytes = 0;
  let offset = centralOffset;
  for (let index = 0; index < entryCount; index += 1) {
    if (offset + 46 > eocd || bytes.readUInt32LE(offset) !== CENTRAL_SIGNATURE) {
      reject("INVALID_ZIP", "PPTX ZIP central directory entry is malformed.");
    }
    const flags = bytes.readUInt16LE(offset + 8);
    const method = bytes.readUInt16LE(offset + 10);
    const expectedCrc = bytes.readUInt32LE(offset + 16);
    const compressedBytes = bytes.readUInt32LE(offset + 20);
    const uncompressedBytes = bytes.readUInt32LE(offset + 24);
    const nameLength = bytes.readUInt16LE(offset + 28);
    const extraLength = bytes.readUInt16LE(offset + 30);
    const commentLength = bytes.readUInt16LE(offset + 32);
    const entryDisk = bytes.readUInt16LE(offset + 34);
    const localOffset = bytes.readUInt32LE(offset + 42);
    const recordEnd = offset + 46 + nameLength + extraLength + commentLength;
    if (recordEnd > eocd) {
      reject("INVALID_ZIP", "PPTX ZIP central directory entry is truncated.");
    }
    if (
      entryDisk !== 0 ||
      flags & 0x0001 ||
      flags & ~0x080e ||
      (method !== 0 && method !== 8)
    ) {
      reject("UNSUPPORTED_ZIP", "Encrypted or unsupported PPTX ZIP member.");
    }
    const nameBytes = bytes.subarray(offset + 46, offset + 46 + nameLength);
    const name = decodeMemberName(nameBytes);
    assertCanonicalZipMemberName(name, policy);
    const isDirectory = name.endsWith("/");
    if (seen.has(name)) reject("DUPLICATE_MEMBER", `Duplicate PPTX member: ${name}`);
    seen.add(name);

    if (
      localOffset + 30 > centralOffset ||
      bytes.readUInt32LE(localOffset) !== LOCAL_SIGNATURE
    ) {
      reject("INVALID_ZIP", `PPTX local header is malformed: ${name}`);
    }
    const localFlags = bytes.readUInt16LE(localOffset + 6);
    const localMethod = bytes.readUInt16LE(localOffset + 8);
    const localCrc = bytes.readUInt32LE(localOffset + 14);
    const localCompressedBytes = bytes.readUInt32LE(localOffset + 18);
    const localUncompressedBytes = bytes.readUInt32LE(localOffset + 22);
    const localNameLength = bytes.readUInt16LE(localOffset + 26);
    const localExtraLength = bytes.readUInt16LE(localOffset + 28);
    const localNameEnd = localOffset + 30 + localNameLength;
    const dataStart = localNameEnd + localExtraLength;
    const dataEnd = dataStart + compressedBytes;
    if (localNameEnd > centralOffset || dataEnd > centralOffset) {
      reject("INVALID_ZIP", `PPTX local member is truncated: ${name}`);
    }
    const localName = decodeMemberName(bytes.subarray(localOffset + 30, localNameEnd));
    if (localFlags !== flags || localMethod !== method || localName !== name) {
      reject("INVALID_ZIP", `PPTX central and local metadata disagree: ${name}`);
    }
    if (
      !(flags & 0x0008) &&
      (localCrc !== expectedCrc ||
        localCompressedBytes !== compressedBytes ||
        localUncompressedBytes !== uncompressedBytes)
    ) {
      reject("INVALID_ZIP", `PPTX central and local sizes or CRC disagree: ${name}`);
    }
    if (
      isDirectory &&
      (compressedBytes !== 0 || uncompressedBytes !== 0 || method !== 0)
    ) {
      reject("INVALID_ZIP", `PPTX directory member is not an empty stored entry: ${name}`);
    }
    if (uncompressedBytes > policy.maxMemberUncompressedBytes) {
      reject("MEMBER_SIZE_LIMIT", `PPTX member exceeds its byte limit: ${name}`);
    }
    totalUncompressedBytes += uncompressedBytes;
    if (totalUncompressedBytes > policy.maxTotalUncompressedBytes) {
      reject("TOTAL_SIZE_LIMIT", "PPTX members exceed the total byte limit.");
    }
    const compressionRatio =
      compressedBytes === 0
        ? uncompressedBytes === 0
          ? 1
          : Number.POSITIVE_INFINITY
        : uncompressedBytes / compressedBytes;
    if (compressionRatio > policy.maxCompressionRatio) {
      reject(
        "COMPRESSION_RATIO_LIMIT",
        `PPTX member exceeds the compression ratio limit: ${name}`
      );
    }
    members.push(Object.freeze({
      name,
      isDirectory,
      compressedBytes,
      uncompressedBytes,
      compressionRatio,
      compressionMethod: method === 0 ? "store" : "deflate",
    }));
    offset = recordEnd;
  }
  if (offset !== centralOffset + centralSize) {
    reject("INVALID_ZIP", "PPTX ZIP central directory size is inconsistent.");
  }
  return { members, totalUncompressedBytes };
}

function xmlEncoding(bytes: Buffer): "utf-8" | "utf-16le" | "utf-16be" {
  if (bytes.length >= 2 && bytes[0] === 0xff && bytes[1] === 0xfe) {
    return "utf-16le";
  }
  if (bytes.length >= 2 && bytes[0] === 0xfe && bytes[1] === 0xff) {
    return "utf-16be";
  }
  return "utf-8";
}

export function decodeAndValidateOoxmlXml(
  name: string,
  bytes: Buffer,
  policy: Readonly<OoxmlSecurityPolicy> = DEFAULT_OOXML_SECURITY_POLICY
): string {
  if (bytes.length > policy.maxXmlPartBytes) {
    reject("XML_SIZE_LIMIT", `OOXML part exceeds the XML byte limit: ${name}`);
  }
  let xml: string;
  try {
    xml = new TextDecoder(xmlEncoding(bytes), { fatal: true }).decode(bytes);
  } catch {
    reject("INVALID_XML_ENCODING", `OOXML part is not valid encoded XML: ${name}`);
  }
  if (/<!\s*(?:DOCTYPE|ENTITY)\b/i.test(xml)) {
    reject("ACTIVE_XML", `OOXML DTD and entity declarations are rejected: ${name}`);
  }
  if (/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/.test(xml)) {
    reject("INVALID_XML", `OOXML part contains forbidden control characters: ${name}`);
  }
  const declaration = /^\uFEFF?\s*<\?xml\b([^?]*)\?>/i.exec(xml);
  const declaredEncoding = declaration?.[1].match(
    /\bencoding\s*=\s*(?:"([^"]+)"|'([^']+)')/i
  );
  if (declaredEncoding) {
    const normalized = (declaredEncoding[1] ?? declaredEncoding[2]).toLowerCase();
    const actual = xmlEncoding(bytes);
    const compatible =
      (actual === "utf-8" && ["utf-8", "utf8"].includes(normalized)) ||
      (actual === "utf-16le" && ["utf-16", "utf-16le"].includes(normalized)) ||
      (actual === "utf-16be" && ["utf-16", "utf-16be"].includes(normalized));
    if (!compatible) {
      reject("INVALID_XML_ENCODING", `OOXML encoding declaration disagrees: ${name}`);
    }
  }
  return xml;
}

function isXmlMember(name: string): boolean {
  const normalized = name.toLowerCase();
  return (
    normalized === "[content_types].xml" ||
    normalized.endsWith(".xml") ||
    normalized.endsWith(".rels")
  );
}

/**
 * Applies the limits that remain meaningful after ZIP inflation. This is also
 * used by package-graph callers so constructing a graph directly cannot bypass
 * path, member, aggregate, or XML limits.
 */
export function inspectOoxmlEntriesSecurity(
  entries: ReadonlyMap<string, Buffer>,
  override: Partial<OoxmlSecurityPolicy> = {}
): OoxmlEntriesSecurityInspection {
  const policy = resolveOoxmlSecurityPolicy(override);
  if (entries.size === 0 || entries.size > policy.maxMembers) {
    reject("MEMBER_COUNT_LIMIT", "OOXML member count is outside the limit.");
  }
  let totalUncompressedBytes = 0;
  let totalXmlBytes = 0;
  for (const [name, content] of entries) {
    assertCanonicalOoxmlPartName(name, policy);
    if (content.length > policy.maxMemberUncompressedBytes) {
      reject("MEMBER_SIZE_LIMIT", `OOXML member exceeds its byte limit: ${name}`);
    }
    totalUncompressedBytes += content.length;
    if (totalUncompressedBytes > policy.maxTotalUncompressedBytes) {
      reject("TOTAL_SIZE_LIMIT", "OOXML members exceed the total byte limit.");
    }
    if (!isXmlMember(name)) continue;
    totalXmlBytes += content.length;
    if (totalXmlBytes > policy.maxTotalXmlBytes) {
      reject("XML_TOTAL_SIZE_LIMIT", "OOXML parts exceed the total XML byte limit.");
    }
    decodeAndValidateOoxmlXml(name, content, policy);
  }
  return Object.freeze({
    entries: immutablePackageEntries(entries),
    memberCount: entries.size,
    totalUncompressedBytes,
    totalXmlBytes,
    policy,
  });
}

/**
 * Performs bounded central-directory checks before delegating CRC-checked ZIP
 * inflation to the existing archive reader. No member is extracted to disk.
 */
export function inspectPptxPackageSecurity(
  bytes: Buffer,
  override: Partial<OoxmlSecurityPolicy> = {}
): OoxmlSecurityInspection {
  const policy = resolveOoxmlSecurityPolicy(override);
  const central = inspectCentralDirectory(bytes, policy);
  let entries: Map<string, Buffer>;
  try {
    entries = readPptxArchive(bytes);
  } catch (error) {
    reject(
      "INVALID_ZIP",
      `PPTX archive validation failed: ${
        error instanceof Error ? error.message : "unknown archive error"
      }`
    );
  }
  for (const member of central.members) {
    if (member.isDirectory) entries.delete(member.name);
  }
  const inflated = inspectOoxmlEntriesSecurity(entries, policy);
  const fileMembers = central.members.filter((member) => !member.isDirectory);
  if (inflated.memberCount !== fileMembers.length) {
    reject("INVALID_ZIP", "PPTX inflated member inventory disagrees with the ZIP directory.");
  }
  for (const member of fileMembers) {
    if (inflated.entries.get(member.name)?.length !== member.uncompressedBytes) {
      reject("INVALID_ZIP", `PPTX inflated member size disagrees: ${member.name}`);
    }
  }
  return Object.freeze({
    entries: inflated.entries,
    members: Object.freeze(central.members),
    archiveBytes: bytes.length,
    totalUncompressedBytes: central.totalUncompressedBytes,
    totalXmlBytes: inflated.totalXmlBytes,
    policy,
  });
}
