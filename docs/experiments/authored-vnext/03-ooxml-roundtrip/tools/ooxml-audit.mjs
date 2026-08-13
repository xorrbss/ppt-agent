#!/usr/bin/env node

import { createHash } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { basename, extname, posix, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { inflateRawSync } from "node:zlib";

const SCHEMA_VERSION = "ooxml-audit/v1";
const MAX_ENTRY_BYTES = 512 * 1024 * 1024;
const MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024;
const MAX_XML_SCAN_BYTES = 32 * 1024 * 1024;
const EOCD_SIGNATURE = 0x06054b50;
const CENTRAL_SIGNATURE = 0x02014b50;
const LOCAL_SIGNATURE = 0x04034b50;

const XML_ENTITIES = Object.freeze({ amp: "&", apos: "'", gt: ">", lt: "<", quot: '"' });

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function stableHash(values) {
  const hash = createHash("sha256");
  for (const value of values) hash.update(value).update("\0");
  return hash.digest("hex");
}

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n += 1) {
    let value = n;
    for (let bit = 0; bit < 8; bit += 1) value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    table[n] = value >>> 0;
  }
  return table;
})();

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function decodeZipName(buffer, utf8) {
  return buffer.toString(utf8 ? "utf8" : "latin1");
}

function validatePartName(name) {
  if (!name || name.includes("\0") || name.includes("\\") || name.startsWith("/") || /^[A-Za-z]:/.test(name)) {
    throw new Error(`Unsafe ZIP entry name: ${JSON.stringify(name)}`);
  }
  const logicalName = name.endsWith("/") ? name.slice(0, -1) : name;
  const normalized = posix.normalize(logicalName);
  if (!logicalName || normalized === "." || normalized === ".." || normalized.startsWith("../") || normalized !== logicalName) {
    throw new Error(`Non-canonical ZIP entry name: ${JSON.stringify(name)}`);
  }
}

function findEocd(zip) {
  const minimum = Math.max(0, zip.length - 65_557);
  for (let offset = zip.length - 22; offset >= minimum; offset -= 1) {
    if (zip.readUInt32LE(offset) === EOCD_SIGNATURE && offset + 22 + zip.readUInt16LE(offset + 20) === zip.length) return offset;
  }
  throw new Error("ZIP end-of-central-directory record was not found");
}

function readZipParts(filePath) {
  const zip = readFileSync(filePath);
  const eocd = findEocd(zip);
  const diskNumber = zip.readUInt16LE(eocd + 4);
  const centralDisk = zip.readUInt16LE(eocd + 6);
  const entriesOnDisk = zip.readUInt16LE(eocd + 8);
  const entryCount = zip.readUInt16LE(eocd + 10);
  const centralSize = zip.readUInt32LE(eocd + 12);
  const centralOffset = zip.readUInt32LE(eocd + 16);
  const commentLength = zip.readUInt16LE(eocd + 20);

  if (eocd + 22 + commentLength !== zip.length) throw new Error("Trailing or truncated data after ZIP end record");
  if (diskNumber !== 0 || centralDisk !== 0 || entriesOnDisk !== entryCount) throw new Error("Multi-disk ZIP packages are not supported");
  if (entryCount === 0xffff || centralSize === 0xffffffff || centralOffset === 0xffffffff) {
    throw new Error("ZIP64 packages are rejected by this audit harness");
  }
  if (centralOffset + centralSize !== eocd) throw new Error("ZIP central directory does not end at the archive end record");

  const parts = [];
  const retainedText = new Map();
  const seenNames = new Set();
  let cursor = centralOffset;
  let totalUncompressedBytes = 0;

  for (let index = 0; index < entryCount; index += 1) {
    if (cursor + 46 > zip.length || zip.readUInt32LE(cursor) !== CENTRAL_SIGNATURE) {
      throw new Error(`Invalid central-directory entry at index ${index}`);
    }
    const flags = zip.readUInt16LE(cursor + 8);
    const method = zip.readUInt16LE(cursor + 10);
    const expectedCrc = zip.readUInt32LE(cursor + 16);
    const compressedSize = zip.readUInt32LE(cursor + 20);
    const uncompressedSize = zip.readUInt32LE(cursor + 24);
    const nameLength = zip.readUInt16LE(cursor + 28);
    const extraLength = zip.readUInt16LE(cursor + 30);
    const entryCommentLength = zip.readUInt16LE(cursor + 32);
    const localOffset = zip.readUInt32LE(cursor + 42);
    const recordEnd = cursor + 46 + nameLength + extraLength + entryCommentLength;
    if (recordEnd > zip.length) throw new Error(`Truncated central-directory entry at index ${index}`);

    const name = decodeZipName(zip.subarray(cursor + 46, cursor + 46 + nameLength), Boolean(flags & 0x0800));
    validatePartName(name);
    if (seenNames.has(name)) throw new Error(`Duplicate ZIP entry is rejected: ${name}`);
    seenNames.add(name);
    if (flags & 0x0001) throw new Error(`Encrypted ZIP entry is rejected: ${name}`);
    if (compressedSize === 0xffffffff || uncompressedSize === 0xffffffff || localOffset === 0xffffffff) {
      throw new Error(`ZIP64 entry is rejected: ${name}`);
    }
    if (method !== 0 && method !== 8) throw new Error(`Unsupported ZIP compression method ${method} for ${name}`);
    if (uncompressedSize > MAX_ENTRY_BYTES) throw new Error(`ZIP entry exceeds ${MAX_ENTRY_BYTES} bytes: ${name}`);
    totalUncompressedBytes += uncompressedSize;
    if (totalUncompressedBytes > MAX_TOTAL_BYTES) throw new Error(`ZIP package exceeds ${MAX_TOTAL_BYTES} uncompressed bytes`);
    if (localOffset + 30 > zip.length || zip.readUInt32LE(localOffset) !== LOCAL_SIGNATURE) {
      throw new Error(`Invalid local header for ${name}`);
    }
    const localFlags = zip.readUInt16LE(localOffset + 6);
    const localMethod = zip.readUInt16LE(localOffset + 8);
    const localNameLength = zip.readUInt16LE(localOffset + 26);
    const localExtraLength = zip.readUInt16LE(localOffset + 28);
    if ((localFlags & 0x0001) !== (flags & 0x0001) || localMethod !== method) throw new Error(`Central/local header mismatch for ${name}`);
    const localNameStart = localOffset + 30;
    const localName = decodeZipName(zip.subarray(localNameStart, localNameStart + localNameLength), Boolean(localFlags & 0x0800));
    if (localName !== name) throw new Error(`Central/local entry name mismatch for ${name}`);
    const dataStart = localNameStart + localNameLength + localExtraLength;
    const dataEnd = dataStart + compressedSize;
    if (dataEnd > zip.length) throw new Error(`Truncated compressed payload for ${name}`);
    const compressed = zip.subarray(dataStart, dataEnd);
    const payload = method === 0 ? Buffer.from(compressed) : inflateRawSync(compressed, { maxOutputLength: MAX_ENTRY_BYTES });
    if (payload.length !== uncompressedSize) throw new Error(`Uncompressed size mismatch for ${name}`);
    const actualCrc = crc32(payload);
    if (actualCrc !== expectedCrc) throw new Error(`CRC-32 mismatch for ${name}`);

    const directory = name.endsWith("/");
    parts.push({
      name,
      directory,
      uncompressedSize,
      compressedSize,
      compressionMethod: method === 0 ? "store" : "deflate",
      crc32: actualCrc.toString(16).padStart(8, "0"),
      sha256: sha256(payload),
    });
    if (!directory && payload.length <= MAX_XML_SCAN_BYTES && (/\.xml$/i.test(name) || /\.rels$/i.test(name) || name === "[Content_Types].xml")) {
      retainedText.set(name, payload.toString("utf8"));
    }
    cursor = recordEnd;
  }
  if (cursor !== centralOffset + centralSize) throw new Error("Central-directory size does not match parsed entries");

  return { zip, parts, retainedText, totalUncompressedBytes };
}

function xmlUnescape(value) {
  return value.replace(/&(#x[0-9a-f]+|#\d+|amp|apos|gt|lt|quot);/gi, (match, entity) => {
    if (entity[0] === "#") {
      const hexadecimal = entity[1].toLowerCase() === "x";
      const point = Number.parseInt(entity.slice(hexadecimal ? 2 : 1), hexadecimal ? 16 : 10);
      return Number.isInteger(point) && point >= 0 && point <= 0x10ffff ? String.fromCodePoint(point) : match;
    }
    return XML_ENTITIES[entity.toLowerCase()] ?? match;
  });
}

function parseAttributes(fragment) {
  const attributes = {};
  const pattern = /([A-Za-z_][\w:.-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;
  for (const match of fragment.matchAll(pattern)) attributes[match[1]] = xmlUnescape(match[2] ?? match[3] ?? "");
  return attributes;
}

function normalizePartName(name) {
  const value = name.split("#", 1)[0].split("?", 1)[0].replace(/^\/+/, "");
  const normalized = posix.normalize(value);
  return normalized === "." ? "" : normalized;
}

function parseContentTypes(xml) {
  if (!xml) throw new Error("[Content_Types].xml is missing from the package");
  const defaults = {};
  const overrides = {};
  for (const match of xml.matchAll(/<(?:\w+:)?Default\b([^>]*)\/?\s*>/gi)) {
    const attributes = parseAttributes(match[1]);
    if (attributes.Extension && attributes.ContentType) defaults[attributes.Extension.toLowerCase()] = attributes.ContentType;
  }
  for (const match of xml.matchAll(/<(?:\w+:)?Override\b([^>]*)\/?\s*>/gi)) {
    const attributes = parseAttributes(match[1]);
    if (attributes.PartName && attributes.ContentType) overrides[normalizePartName(attributes.PartName)] = attributes.ContentType;
  }
  return { defaults, overrides };
}

function contentTypeFor(name, contentTypes) {
  if (contentTypes.overrides[name]) return contentTypes.overrides[name];
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? contentTypes.defaults[name.slice(dot + 1).toLowerCase()] ?? null : null;
}

function relationshipSource(relsName) {
  if (relsName === "_rels/.rels") return "";
  const match = /^(.*\/)?_rels\/([^/]+)\.rels$/.exec(relsName);
  if (!match) return null;
  return `${match[1] ?? ""}${match[2]}`;
}

function resolveRelationshipTarget(source, target) {
  if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(target)) return null;
  const raw = target.split("#", 1)[0].split("?", 1)[0];
  const based = raw.startsWith("/") ? raw.slice(1) : posix.join(posix.dirname(source), raw);
  return normalizePartName(based);
}

function parseRelationships(retainedText, partNames) {
  const relationships = [];
  const malformedRelationshipParts = [];
  for (const [name, xml] of retainedText) {
    if (!name.endsWith(".rels")) continue;
    const source = relationshipSource(name);
    if (source === null) {
      malformedRelationshipParts.push(name);
      continue;
    }
    const seenIds = new Set();
    for (const match of xml.matchAll(/<(?:\w+:)?Relationship\b([^>]*)\/?\s*>/gi)) {
      const attributes = parseAttributes(match[1]);
      if (!attributes.Id || !attributes.Type || attributes.Target === undefined) continue;
      const targetMode = attributes.TargetMode?.toLowerCase() === "external" ? "External" : "Internal";
      const duplicateId = seenIds.has(attributes.Id);
      seenIds.add(attributes.Id);
      relationships.push({
        source,
        relsPart: name,
        id: attributes.Id,
        type: attributes.Type,
        target: attributes.Target,
        targetMode,
        normalizedTarget: targetMode === "Internal" ? resolveRelationshipTarget(source, attributes.Target) : null,
        duplicateId,
      });
    }
  }
  relationships.sort((a, b) => relationshipKey(a).localeCompare(relationshipKey(b)));

  const internal = relationships.filter((item) => item.targetMode === "Internal");
  const external = relationships.filter((item) => item.targetMode === "External");
  const missingTargets = internal
    .filter((item) => !item.normalizedTarget || !partNames.has(item.normalizedTarget))
    .map((item) => ({ source: item.source, id: item.id, target: item.target, normalizedTarget: item.normalizedTarget }));
  const missingSources = [
    ...new Map(
      relationships
        .filter((item) => item.source && !partNames.has(item.source))
        .map((item) => [item.relsPart, { source: item.source, relsPart: item.relsPart }]),
    ).values(),
  ];

  const outgoing = new Map();
  for (const item of internal) {
    if (!outgoing.has(item.source)) outgoing.set(item.source, []);
    if (item.normalizedTarget && partNames.has(item.normalizedTarget)) outgoing.get(item.source).push(item.normalizedTarget);
  }
  const visited = new Set([""]);
  const queue = [""];
  while (queue.length) {
    const source = queue.shift();
    for (const target of outgoing.get(source) ?? []) {
      if (!visited.has(target)) {
        visited.add(target);
        queue.push(target);
      }
    }
  }
  visited.delete("");
  const reachableParts = [...visited].sort();
  const canonicalRecords = relationships.map(relationshipKey);
  return {
    count: relationships.length,
    internalCount: internal.length,
    externalCount: external.length,
    records: relationships,
    canonicalRecords,
    closureSha256: stableHash(canonicalRecords),
    reachableParts,
    reachablePartsSha256: stableHash(reachableParts),
    missingTargets,
    missingSources,
    malformedRelationshipParts,
  };
}

function relationshipKey(item) {
  return [item.source, item.id, item.type, item.targetMode, item.target, item.normalizedTarget ?? ""].join("\u001f");
}

function categoryFor(name, contentType) {
  if (name === "[Content_Types].xml") return "package-metadata";
  if (name.endsWith(".rels")) return "relationships";
  if (name.startsWith("docProps/")) return "document-properties";
  if (name.startsWith("_xmlsignatures/")) return "digital-signatures";
  if (name.startsWith("customXml/")) return "custom-xml";
  if (/^ppt\/slides\//.test(name)) return "slides";
  if (/^ppt\/slideLayouts\//.test(name)) return "slide-layouts";
  if (/^ppt\/slideMasters\//.test(name)) return "slide-masters";
  if (/^ppt\/notesSlides\//.test(name)) return "notes-slides";
  if (/^ppt\/notesMasters\//.test(name)) return "notes-masters";
  if (/^ppt\/handoutMasters\//.test(name)) return "handout-masters";
  if (/^ppt\/media\//.test(name)) return "media";
  if (/^ppt\/theme\//.test(name)) return "themes";
  if (/^ppt\/charts\//.test(name)) return "charts";
  if (/^ppt\/diagrams\//.test(name)) return "smartart";
  if (/^ppt\/(?:embeddings|activeX|ctrlProps)\//.test(name)) return "embedded-objects";
  if (/^ppt\/(?:comments\/|threadedComments\/|commentAuthors(?:\.xml|\/)|people(?:\.xml|\/))/i.test(name)) return "comments";
  if (/^ppt\/(?:tags|fonts|printerSettings|xmlData)\//.test(name)) return "presentation-resources";
  if (/^ppt\/(?:presentation|presProps|viewProps|tableStyles)\b/.test(name)) return "presentation-core";
  if (/^ppt\/vbaProject(?:Signature)?\.bin$/i.test(name) || /vbaProject/i.test(contentType ?? "")) return "macros";
  return "unknown";
}

function evidenceFor({ parts, relationships, contentTypes, retainedText }) {
  const allContentTypes = [...Object.values(contentTypes.defaults), ...Object.values(contentTypes.overrides)];
  const relationshipTypeEvidence = (pattern) => [...new Set(relationships.records.filter((item) => pattern.test(item.type)).map((item) => item.type))].sort();
  const contentTypeEvidence = (pattern) => [...new Set(allContentTypes.filter((item) => pattern.test(item)))].sort();
  const partEvidence = (pattern) => parts.filter((part) => pattern.test(part.name)).map((part) => part.name).sort();
  const markerEvidence = (pattern) => {
    const found = [];
    for (const [name, xml] of retainedText) if (!name.endsWith(".rels") && pattern.test(xml)) found.push(name);
    return found.sort();
  };
  const feature = (evidence) => ({ present: Object.values(evidence).some((items) => items.length > 0), evidence });

  return {
    macros: feature({
      parts: partEvidence(/(?:^|\/)vbaProject(?:Signature)?\.bin$/i),
      relationshipTypes: relationshipTypeEvidence(/vbaProject|vbaProjectSignature/i),
      contentTypes: contentTypeEvidence(/macroEnabled|vbaProject/i),
      xmlMarkers: [],
    }),
    externalRelationships: feature({
      parts: [],
      relationshipTypes: [...new Set(relationships.records.filter((item) => item.targetMode === "External").map((item) => item.type))].sort(),
      contentTypes: [],
      xmlMarkers: relationships.records.filter((item) => item.targetMode === "External").map((item) => `${item.source}#${item.id}`).sort(),
    }),
    smartArt: feature({
      parts: partEvidence(/^ppt\/diagrams\//i),
      relationshipTypes: relationshipTypeEvidence(/\/diagram(?:Data|Layout|QuickStyle|Colors)?$/i),
      contentTypes: contentTypeEvidence(/drawingml\.diagram/i),
      xmlMarkers: markerEvidence(/<(?:\w+:)?relIds\b/i),
    }),
    ole: feature({
      parts: partEvidence(/^ppt\/(?:embeddings|activeX|ctrlProps)\//i),
      relationshipTypes: relationshipTypeEvidence(/oleObject|package|activeX/i),
      contentTypes: contentTypeEvidence(/oleObject|activeX|officedocument\.package/i),
      xmlMarkers: markerEvidence(/<(?:\w+:)?oleObj\b/i),
    }),
    animation: feature({
      parts: [],
      relationshipTypes: [],
      contentTypes: [],
      xmlMarkers: markerEvidence(/<(?:p:)?(?:timing|anim|animMotion|animRot|animScale|cmd|set)\b/i),
    }),
    comments: feature({
      parts: partEvidence(/^ppt\/(?:comments\/|threadedComments\/|commentAuthors(?:\.xml|\/)|people(?:\.xml|\/))/i),
      relationshipTypes: relationshipTypeEvidence(/comments?|commentAuthors?|threadedComments?|person$/i),
      contentTypes: contentTypeEvidence(/comments?|commentAuthors?|threadedComments?|person/i),
      xmlMarkers: [],
    }),
    customXml: feature({
      parts: partEvidence(/^customXml\//i),
      relationshipTypes: relationshipTypeEvidence(/customXml/i),
      contentTypes: contentTypeEvidence(/customXml/i),
      xmlMarkers: [],
    }),
  };
}

function countBy(items, keySelector) {
  const counts = {};
  for (const item of items) {
    const key = keySelector(item);
    counts[key] = (counts[key] ?? 0) + 1;
  }
  return Object.fromEntries(Object.entries(counts).sort(([a], [b]) => a.localeCompare(b)));
}

export function inspectPackage(inputPath) {
  const absolutePath = resolve(inputPath);
  const { zip, parts: rawParts, retainedText, totalUncompressedBytes } = readZipParts(absolutePath);
  const contentTypes = parseContentTypes(retainedText.get("[Content_Types].xml"));
  const fileParts = rawParts.filter((part) => !part.directory);
  const partNames = new Set(fileParts.map((part) => part.name));
  const relationships = parseRelationships(retainedText, partNames);
  const parts = fileParts.map((part) => {
    const contentType = contentTypeFor(part.name, contentTypes);
    return { ...part, contentType, category: categoryFor(part.name, contentType) };
  });
  const unknownParts = parts.filter((part) => part.category === "unknown").map((part) => part.name);
  const relationshipParts = new Set(parts.filter((part) => part.category === "relationships").map((part) => part.name));
  const metadataParts = new Set(["[Content_Types].xml", ...relationshipParts]);
  const orphanParts = parts
    .filter((part) => !metadataParts.has(part.name) && !relationships.reachableParts.includes(part.name))
    .map((part) => part.name)
    .sort();
  const partsWithoutContentType = parts
    .filter((part) => part.name !== "[Content_Types].xml" && !part.contentType)
    .map((part) => part.name);
  const extension = extname(absolutePath).toLowerCase();
  const packageKind = extension === ".pptm" ? "pptm" : extension === ".pptx" ? "pptx" : "opc-zip";
  const officeDocumentRelationships = relationships.records.filter(
    (item) => item.source === "" && item.targetMode === "Internal" && /\/officeDocument$/i.test(item.type),
  );
  const duplicateRelationshipIdCount = relationships.records.filter((item) => item.duplicateId).length;

  return {
    schemaVersion: SCHEMA_VERSION,
    mode: "inspect",
    input: {
      basename: basename(absolutePath),
      packageKind,
      byteLength: zip.length,
      zipSha256: sha256(zip),
    },
    limits: {
      maxEntryBytes: MAX_ENTRY_BYTES,
      maxTotalUncompressedBytes: MAX_TOTAL_BYTES,
      maxXmlScanBytes: MAX_XML_SCAN_BYTES,
      zip64Accepted: false,
      encryptedEntriesAccepted: false,
    },
    inventory: {
      partCount: parts.length,
      directoryEntryCount: rawParts.length - parts.length,
      totalUncompressedBytes,
      uncompressedPayloadSha256: stableHash(parts.sort((a, b) => a.name.localeCompare(b.name)).map((part) => `${part.name}\u001f${part.sha256}`)),
      countsByCategory: countBy(parts, (part) => part.category),
      countsByContentType: countBy(parts, (part) => part.contentType ?? "(missing)"),
      parts,
      unknownParts,
      partsWithoutContentType,
    },
    contentTypes: {
      defaults: contentTypes.defaults,
      overrides: contentTypes.overrides,
      defaultsSha256: stableHash(Object.entries(contentTypes.defaults).sort().map(([key, value]) => `${key}\u001f${value}`)),
      overridesSha256: stableHash(Object.entries(contentTypes.overrides).sort().map(([key, value]) => `${key}\u001f${value}`)),
    },
    relationships,
    topology: {
      slideCount: parts.filter((part) => part.category === "slides" && /\/slide\d+\.xml$/i.test(part.name)).length,
      slideMasterCount: parts.filter((part) => part.category === "slide-masters" && /\/slideMaster\d+\.xml$/i.test(part.name)).length,
      slideLayoutCount: parts.filter((part) => part.category === "slide-layouts" && /\/slideLayout\d+\.xml$/i.test(part.name)).length,
      mediaCount: parts.filter((part) => part.category === "media").length,
      unknownPartCount: unknownParts.length,
      orphanParts,
    },
    features: evidenceFor({ parts, relationships, contentTypes, retainedText }),
    validation: {
      validOpcReadBack:
        relationships.missingTargets.length === 0 &&
        relationships.missingSources.length === 0 &&
        relationships.malformedRelationshipParts.length === 0 &&
        partsWithoutContentType.length === 0 &&
        duplicateRelationshipIdCount === 0 &&
        (packageKind === "opc-zip" || officeDocumentRelationships.length === 1),
      missingRelationshipTargetCount: relationships.missingTargets.length,
      missingRelationshipSourceCount: relationships.missingSources.length,
      duplicateRelationshipIdCount,
      officeDocumentRelationshipCount: officeDocumentRelationships.length,
    },
  };
}

function setDifference(left, right) {
  return [...left].filter((value) => !right.has(value)).sort();
}

function globPattern(pattern) {
  const escaped = pattern.replace(/[.+^${}()|[\]\\]/g, "\\$&").replace(/\*\*/g, "\u0000").replace(/\*/g, "[^/]*").replace(/\u0000/g, ".*").replace(/\?/g, "[^/]");
  return new RegExp(`^${escaped}$`);
}

function isAllowed(name, patterns) {
  return patterns.some((pattern) => globPattern(pattern).test(name));
}

function byteDiffSummary(beforeText, afterText) {
  const before = Buffer.from(beforeText, "utf8");
  const after = Buffer.from(afterText, "utf8");
  let prefix = 0;
  while (prefix < before.length && prefix < after.length && before[prefix] === after[prefix]) prefix += 1;
  let suffix = 0;
  while (
    suffix < before.length - prefix &&
    suffix < after.length - prefix &&
    before[before.length - 1 - suffix] === after[after.length - 1 - suffix]
  ) {
    suffix += 1;
  }
  const beforeChanged = before.subarray(prefix, before.length - suffix);
  const afterChanged = after.subarray(prefix, after.length - suffix);
  return {
    commonPrefixBytes: prefix,
    commonSuffixBytes: suffix,
    beforeChangedBytes: beforeChanged.length,
    afterChangedBytes: afterChanged.length,
    beforeChangedSha256: sha256(beforeChanged),
    afterChangedSha256: sha256(afterChanged),
  };
}

function tokenizeDrawingText(xml) {
  const texts = [];
  let unsafeTextBody = false;
  let ordinal = 0;
  const skeleton = xml.replace(/(<a:t\b[^>]*>)([\s\S]*?)(<\/a:t\s*>)/g, (_match, open, text, close) => {
    if (text.includes("<")) unsafeTextBody = true;
    texts.push(text);
    const marker = `__OOXML_A_TEXT_${ordinal}__`;
    ordinal += 1;
    return `${open}${marker}${close}`;
  });
  return { skeleton, texts, unsafeTextBody };
}

function analyzeXmlRewrite(beforeXml, afterXml) {
  if (beforeXml === undefined || afterXml === undefined) {
    return { classification: "binary-or-xml-over-scan-limit", textOnly: false };
  }
  const byteDiff = byteDiffSummary(beforeXml, afterXml);
  const before = tokenizeDrawingText(beforeXml);
  const after = tokenizeDrawingText(afterXml);
  const changedTextNodes = [];
  const count = Math.max(before.texts.length, after.texts.length);
  for (let index = 0; index < count; index += 1) {
    if (before.texts[index] !== after.texts[index]) {
      const beforeText = before.texts[index] ?? "";
      const afterText = after.texts[index] ?? "";
      changedTextNodes.push({
        index,
        beforeUtf8Bytes: Buffer.byteLength(beforeText, "utf8"),
        afterUtf8Bytes: Buffer.byteLength(afterText, "utf8"),
        beforeSha256: sha256(beforeText),
        afterSha256: sha256(afterText),
      });
    }
  }
  const textOnly =
    changedTextNodes.length > 0 &&
    !before.unsafeTextBody &&
    !after.unsafeTextBody &&
    before.texts.length === after.texts.length &&
    before.skeleton === after.skeleton;
  return {
    classification: textOnly ? "drawing-text-content-only" : "xml-structural-or-formatting",
    textOnly,
    drawingTextNodeCountBefore: before.texts.length,
    drawingTextNodeCountAfter: after.texts.length,
    changedTextNodeCount: changedTextNodes.length,
    changedTextNodes,
    byteDiff,
  };
}

export function comparePackages(beforePath, afterPath, options = {}) {
  const allowedChanges = [...new Set(options.allowedChanges ?? [])].sort();
  const allowedTextChanges = [...new Set(options.allowedTextChanges ?? [])].sort();
  const before = inspectPackage(beforePath);
  const after = inspectPackage(afterPath);
  const beforeText = readZipParts(resolve(beforePath)).retainedText;
  const afterText = readZipParts(resolve(afterPath)).retainedText;
  const beforeByName = new Map(before.inventory.parts.map((part) => [part.name, part]));
  const afterByName = new Map(after.inventory.parts.map((part) => [part.name, part]));
  const beforeNames = new Set(beforeByName.keys());
  const afterNames = new Set(afterByName.keys());
  const added = setDifference(afterNames, beforeNames).map((name) => afterByName.get(name));
  const removed = setDifference(beforeNames, afterNames).map((name) => beforeByName.get(name));
  const changed = [...beforeNames]
    .filter((name) => afterByName.has(name) && beforeByName.get(name).sha256 !== afterByName.get(name).sha256)
    .sort()
    .map((name) => ({
      name,
      category: beforeByName.get(name).category,
      beforeSha256: beforeByName.get(name).sha256,
      afterSha256: afterByName.get(name).sha256,
      beforeBytes: beforeByName.get(name).uncompressedSize,
      afterBytes: afterByName.get(name).uncompressedSize,
      rewrite: analyzeXmlRewrite(beforeText.get(name), afterText.get(name)),
    }));
  const unchangedCount = [...beforeNames].filter((name) => afterByName.get(name)?.sha256 === beforeByName.get(name).sha256).length;
  const changedIsAllowed = (part) =>
    isAllowed(part.name, allowedChanges) || (isAllowed(part.name, allowedTextChanges) && part.rewrite.textOnly);
  const untouchedChanges = [
    ...added.filter((part) => !isAllowed(part.name, allowedChanges)).map((part) => ({ name: part.name, change: "added" })),
    ...changed.filter((part) => !changedIsAllowed(part)).map((part) => ({ name: part.name, change: "changed", rewrite: part.rewrite })),
  ].sort((a, b) => a.name.localeCompare(b.name));
  const allowedObservedChanges = [
    ...added.filter((part) => isAllowed(part.name, allowedChanges)).map((part) => ({ name: part.name, change: "added" })),
    ...changed.filter(changedIsAllowed).map((part) => ({
      name: part.name,
      change: "changed",
      rule: isAllowed(part.name, allowedChanges) ? "part" : "drawing-text-content-only",
      rewrite: part.rewrite,
    })),
  ].sort((a, b) => a.name.localeCompare(b.name));
  const strictTextChangeViolations = [
    ...added.filter((part) => isAllowed(part.name, allowedTextChanges) && !isAllowed(part.name, allowedChanges)).map((part) => ({ name: part.name, reason: "part-added" })),
    ...changed
      .filter((part) => isAllowed(part.name, allowedTextChanges) && !isAllowed(part.name, allowedChanges) && !part.rewrite.textOnly)
      .map((part) => ({ name: part.name, reason: part.rewrite.classification, rewrite: part.rewrite })),
  ].sort((a, b) => a.name.localeCompare(b.name));

  const beforeRelationshipSet = new Set(before.relationships.canonicalRecords);
  const afterRelationshipSet = new Set(after.relationships.canonicalRecords);
  const relationshipRecordsAdded = setDifference(afterRelationshipSet, beforeRelationshipSet);
  const relationshipRecordsRemoved = setDifference(beforeRelationshipSet, afterRelationshipSet);
  const beforeReachableSet = new Set(before.relationships.reachableParts);
  const afterReachableSet = new Set(after.relationships.reachableParts);
  const reachablePartsAdded = setDifference(afterReachableSet, beforeReachableSet);
  const reachablePartsRemoved = setDifference(beforeReachableSet, afterReachableSet);
  const relationshipClosureChangeCount =
    relationshipRecordsAdded.length + relationshipRecordsRemoved.length + reachablePartsAdded.length + reachablePartsRemoved.length;
  const invariants = {
    missingPartCount: removed.length,
    untouchedPayloadChangeCount: untouchedChanges.length,
    relationshipClosureChangeCount,
    missingRelationshipTargetCountAfter: after.relationships.missingTargets.length,
    packageReadBackValidAfter: after.validation.validOpcReadBack,
  };
  invariants.pass =
    invariants.missingPartCount === 0 &&
    invariants.untouchedPayloadChangeCount === 0 &&
    invariants.relationshipClosureChangeCount === 0 &&
    invariants.missingRelationshipTargetCountAfter === 0 &&
    invariants.packageReadBackValidAfter;

  return {
    schemaVersion: SCHEMA_VERSION,
    mode: "compare",
    inputs: { before: before.input, after: after.input },
    comparisonPolicy: { allowedChanges, allowedTextChanges },
    summary: {
      beforePartCount: before.inventory.partCount,
      afterPartCount: after.inventory.partCount,
      unchangedPartCount: unchangedCount,
      addedPartCount: added.length,
      removedPartCount: removed.length,
      changedPartCount: changed.length,
      allowedObservedChangeCount: allowedObservedChanges.length,
      strictTextChangeViolationCount: strictTextChangeViolations.length,
      untouchedPayloadChangeCount: untouchedChanges.length,
      relationshipClosureChangeCount,
    },
    partDiff: { added, removed, changed, allowedObservedChanges, untouchedChanges, strictTextChangeViolations },
    relationshipClosureDiff: {
      beforeClosureSha256: before.relationships.closureSha256,
      afterClosureSha256: after.relationships.closureSha256,
      relationshipRecordsAdded,
      relationshipRecordsRemoved,
      reachablePartsAdded,
      reachablePartsRemoved,
      missingTargetsBefore: before.relationships.missingTargets,
      missingTargetsAfter: after.relationships.missingTargets,
    },
    contentTypesDiff: {
      defaultsChanged: before.contentTypes.defaultsSha256 !== after.contentTypes.defaultsSha256,
      overridesChanged: before.contentTypes.overridesSha256 !== after.contentTypes.overridesSha256,
    },
    topology: { before: before.topology, after: after.topology },
    features: { before: before.features, after: after.features },
    invariants,
  };
}

function usage() {
  return `Usage:
  node ooxml-audit.mjs inspect <file.pptx|file.pptm> [--output report.json] [--pretty] [--force]
  node ooxml-audit.mjs compare <before.pptx|pptm> <after.pptx|pptm> [--allow-change <part-glob>]... [--allow-text-change <part-glob>]... [--fail-on-violation] [--output report.json] [--pretty] [--force]

The command never extracts package entries and never performs network I/O. Output paths are create-only unless --force is explicit.`;
}

function parseCli(argv) {
  const positional = [];
  const options = { allowedChanges: [], allowedTextChanges: [], output: null, pretty: false, force: false, failOnViolation: false };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--allow-change") {
      const pattern = argv[++index];
      if (!pattern) throw new Error("--allow-change requires a part-name glob");
      options.allowedChanges.push(pattern);
    } else if (value === "--allow-text-change") {
      const pattern = argv[++index];
      if (!pattern) throw new Error("--allow-text-change requires a part-name glob");
      options.allowedTextChanges.push(pattern);
    } else if (value === "--output") {
      options.output = argv[++index];
      if (!options.output) throw new Error("--output requires a path");
    } else if (value === "--pretty") options.pretty = true;
    else if (value === "--force") options.force = true;
    else if (value === "--fail-on-violation") options.failOnViolation = true;
    else if (value === "--help" || value === "-h") options.help = true;
    else if (value.startsWith("--")) throw new Error(`Unknown option: ${value}`);
    else positional.push(value);
  }
  return { positional, options };
}

function writeReport(report, options) {
  const json = `${JSON.stringify(report, null, options.pretty ? 2 : 0)}\n`;
  if (!options.output) {
    process.stdout.write(json);
    return;
  }
  const outputPath = resolve(options.output);
  if (existsSync(outputPath) && !options.force) throw new Error(`Refusing to overwrite existing report without --force: ${outputPath}`);
  writeFileSync(outputPath, json, { encoding: "utf8", flag: options.force ? "w" : "wx" });
}

export function runCli(argv = process.argv.slice(2)) {
  const { positional, options } = parseCli(argv);
  if (options.help) {
    process.stdout.write(`${usage()}\n`);
    return 0;
  }
  const [command, ...files] = positional;
  let report;
  if (command === "inspect" && files.length === 1) report = inspectPackage(files[0]);
  else if (command === "compare" && files.length === 2) report = comparePackages(files[0], files[1], options);
  else throw new Error(usage());
  writeReport(report, options);
  return options.failOnViolation && report.mode === "compare" && !report.invariants.pass ? 2 : 0;
}

const invokedPath = process.argv[1] ? pathToFileURL(resolve(process.argv[1])).href : null;
if (invokedPath === import.meta.url) {
  try {
    process.exitCode = runCli();
  } catch (error) {
    process.stderr.write(`${JSON.stringify({ schemaVersion: SCHEMA_VERSION, error: error.message })}\n`);
    process.exitCode = 1;
  }
}
