import { createHash } from "node:crypto";
import { posix } from "node:path";

import {
  assertCanonicalOoxmlPartName,
  decodeAndValidateOoxmlXml,
  DEFAULT_OOXML_SECURITY_POLICY,
  immutablePackageEntries,
  inspectOoxmlEntriesSecurity,
  type OoxmlSecurityPolicy,
} from "./security.ts";
import {
  evaluateExternalRelationshipPolicy,
} from "./external-relationship-policy.ts";
import {
  PackageGraphError,
  type ContentTypeManifest,
  type PackageGraph,
  type PackageGraphOptions,
  type PackagePartClassification,
  type PackagePartRecord,
  type PackagePreservationManifest,
  type PackageRelationship,
} from "./package-graph-contracts.ts";
export * from "./package-graph-contracts.ts";
export { assertPackageGraphPrecondition, assertPackagePreservation } from "./package-preservation.ts";
export { buildPackageGraph } from "./package-graph-builder.ts";

const XML_ENTITIES = Object.freeze({
  amp: "&",
  apos: "'",
  gt: ">",
  lt: "<",
  quot: '"',
});

function reject(code: string, message: string): never {
  throw new PackageGraphError(code, message);
}

function sha256(value: string | Buffer): string {
  return createHash("sha256").update(value).digest("hex");
}

function stableDigest(records: readonly string[]): string {
  const hash = createHash("sha256");
  for (const record of [...records].sort()) {
    const bytes = Buffer.from(record, "utf8");
    hash.update(String(bytes.length)).update(":").update(bytes).update("\0");
  }
  return hash.digest("hex");
}

function xmlUnescape(value: string): string {
  if (/&(?!#x[0-9a-f]+;|#\d+;|amp;|apos;|gt;|lt;|quot;)/i.test(value)) {
    reject("MALFORMED_XML", "OOXML attribute contains an invalid entity reference.");
  }
  return value.replace(
    /&(#x[0-9a-f]+|#\d+|amp|apos|gt|lt|quot);/gi,
    (match, entity: string) => {
      if (entity.startsWith("#")) {
        const hexadecimal = entity[1]?.toLowerCase() === "x";
        const point = Number.parseInt(
          entity.slice(hexadecimal ? 2 : 1),
          hexadecimal ? 16 : 10
        );
        if (
          !Number.isInteger(point) ||
          point < 0 ||
          point > 0x10ffff ||
          (point >= 0xd800 && point <= 0xdfff)
        ) {
          reject("MALFORMED_XML", "OOXML attribute has an invalid character entity.");
        }
        return String.fromCodePoint(point);
      }
      return XML_ENTITIES[entity.toLowerCase() as keyof typeof XML_ENTITIES] ?? match;
    }
  );
}

function parseAttributes(fragment: string): Readonly<Record<string, string>> {
  const attributes: Record<string, string> = Object.create(null) as Record<
    string,
    string
  >;
  const pattern = /([A-Za-z_][\w:.-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;
  for (const match of fragment.matchAll(pattern)) {
    const name = match[1];
    if (Object.hasOwn(attributes, name)) {
      reject("MALFORMED_XML", `Duplicate OOXML attribute: ${name}`);
    }
    attributes[name] = xmlUnescape(match[2] ?? match[3] ?? "");
  }
  const residue = fragment
    .replace(pattern, "")
    .replace(/^\s+|\s+$/g, "")
    .replace(/\/$/, "")
    .trim();
  if (residue) {
    reject("MALFORMED_XML", "OOXML element contains malformed attributes.");
  }
  return Object.freeze(attributes);
}

function normalizeContentTypePartName(name: string): string {
  if (
    !name.startsWith("/") ||
    name.includes("\\") ||
    name.includes("\0") ||
    name.includes("?") ||
    name.includes("#")
  ) {
    reject("INVALID_CONTENT_TYPES", `Invalid content-type part name: ${name}`);
  }
  const withoutRoot = name.slice(1);
  const normalized = posix.normalize(withoutRoot);
  if (
    !withoutRoot ||
    normalized !== withoutRoot ||
    normalized === ".." ||
    normalized.startsWith("../")
  ) {
    reject("INVALID_CONTENT_TYPES", `Unsafe content-type part name: ${name}`);
  }
  return normalized;
}

function rootElementLooksValid(xml: string, localName: string): boolean {
  const withoutHeader = xml
    .replace(/^\uFEFF?\s*<\?xml\b[^?]*\?>/i, "")
    .replace(/<!--(?:[\s\S]*?)-->/g, "")
    .trim();
  const root = new RegExp(`^<(?:[A-Za-z_][\\w.-]*:)?${localName}\\b`, "i");
  const close = new RegExp(
    `<\\/(?:[A-Za-z_][\\w.-]*:)?${localName}\\s*>\\s*$`,
    "i"
  );
  return root.test(withoutHeader) && close.test(withoutHeader);
}

export function parseContentTypes(
  entries: ReadonlyMap<string, Buffer>,
  policy: Readonly<OoxmlSecurityPolicy> = DEFAULT_OOXML_SECURITY_POLICY
): ContentTypeManifest {
  const bytes = entries.get("[Content_Types].xml");
  if (!bytes) reject("MISSING_CONTENT_TYPES", "[Content_Types].xml is missing.");
  const xml = decodeAndValidateOoxmlXml("[Content_Types].xml", bytes, policy);
  if (!rootElementLooksValid(xml, "Types")) {
    reject("INVALID_CONTENT_TYPES", "Content types root element is malformed.");
  }
  const defaults = new Map<string, string>();
  const overrides = new Map<string, string>();
  let count = 0;
  const defaultCandidates = xml.match(/<(?:[A-Za-z_][\w.-]*:)?Default\b/gi) ?? [];
  const defaultMatches = [
    ...xml.matchAll(/<(?:[A-Za-z_][\w.-]*:)?Default\b([^>]*)>/gi),
  ];
  const overrideCandidates = xml.match(/<(?:[A-Za-z_][\w.-]*:)?Override\b/gi) ?? [];
  const overrideMatches = [
    ...xml.matchAll(/<(?:[A-Za-z_][\w.-]*:)?Override\b([^>]*)>/gi),
  ];
  if (
    defaultCandidates.length !== defaultMatches.length ||
    overrideCandidates.length !== overrideMatches.length
  ) {
    reject("INVALID_CONTENT_TYPES", "Content type entry is malformed.");
  }
  for (const match of defaultMatches) {
    const attributes = parseAttributes(match[1]);
    const extension = attributes.Extension?.toLowerCase();
    const contentType = attributes.ContentType;
    if (!extension || !contentType || /[/.\\\s]/.test(extension)) {
      reject("INVALID_CONTENT_TYPES", "Content type default is incomplete.");
    }
    if (defaults.has(extension)) {
      reject("INVALID_CONTENT_TYPES", `Duplicate content type default: ${extension}`);
    }
    defaults.set(extension, contentType);
    count += 1;
  }
  for (const match of overrideMatches) {
    const attributes = parseAttributes(match[1]);
    if (!attributes.PartName || !attributes.ContentType) {
      reject("INVALID_CONTENT_TYPES", "Content type override is incomplete.");
    }
    const partName = normalizeContentTypePartName(attributes.PartName);
    if (overrides.has(partName)) {
      reject("INVALID_CONTENT_TYPES", `Duplicate content type override: ${partName}`);
    }
    if (!entries.has(partName)) {
      reject("INVALID_CONTENT_TYPES", `Content type refers to a missing part: ${partName}`);
    }
    overrides.set(partName, attributes.ContentType);
    count += 1;
  }
  if (count > policy.maxContentTypeEntries) {
    reject("CONTENT_TYPE_LIMIT", "Content type manifest exceeds its entry limit.");
  }
  const records = [
    ...[...defaults].map(([extension, contentType]) =>
      ["default", extension, contentType].join("\u001f")
    ),
    ...[...overrides].map(([partName, contentType]) =>
      ["override", partName, contentType].join("\u001f")
    ),
  ];
  return Object.freeze({
    defaults,
    overrides,
    digest: stableDigest(records),
  });
}

export function contentTypeForPart(
  partName: string,
  contentTypes: ContentTypeManifest
): string | null {
  const override = contentTypes.overrides.get(partName);
  if (override) return override;
  const dot = partName.lastIndexOf(".");
  if (dot < 0) return null;
  return contentTypes.defaults.get(partName.slice(dot + 1).toLowerCase()) ?? null;
}

function relationshipSource(relsPart: string): string {
  if (relsPart === "_rels/.rels") return "";
  const match = /^(.*\/)?_rels\/([^/]+)\.rels$/.exec(relsPart);
  if (!match) {
    reject("INVALID_RELATIONSHIP_PART", `Invalid relationship part path: ${relsPart}`);
  }
  return `${match[1] ?? ""}${match[2]}`;
}

export function normalizeRelationshipTarget(
  source: string,
  target: string
): string {
  if (
    !target ||
    target.includes("\0") ||
    target.includes("\\") ||
    target.startsWith("//") ||
    /^[A-Za-z][A-Za-z0-9+.-]*:/.test(target)
  ) {
    reject("UNSAFE_RELATIONSHIP_TARGET", `Unsafe internal relationship target: ${target}`);
  }
  const partReference = target.split("#", 1)[0].split("?", 1)[0];
  const based = partReference.startsWith("/")
    ? partReference.slice(1)
    : posix.join(posix.dirname(source), partReference);
  const normalized = posix.normalize(based);
  if (
    !normalized ||
    normalized === "." ||
    normalized === ".." ||
    normalized.startsWith("../") ||
    normalized.startsWith("/")
  ) {
    reject("UNSAFE_RELATIONSHIP_TARGET", `Unsafe internal relationship target: ${target}`);
  }
  return normalized;
}

function relationshipDigest(record: {
  source: string;
  id: string;
  type: string;
  target: string;
  targetMode: string;
  targetModeRaw: string | null;
  normalizedTarget: string | null;
}): string {
  return sha256(
    [
      record.source,
      record.id,
      record.type,
      record.target,
      record.targetMode,
      record.targetModeRaw ?? "",
      record.normalizedTarget ?? "",
    ].join("\u001f")
  );
}

export function parseRelationships(
  entries: ReadonlyMap<string, Buffer>,
  policy: Readonly<OoxmlSecurityPolicy> = DEFAULT_OOXML_SECURITY_POLICY
): readonly PackageRelationship[] {
  const records: PackageRelationship[] = [];
  let externalCount = 0;
  for (const [relsPart, bytes] of [...entries].sort(([left], [right]) =>
    left.localeCompare(right)
  )) {
    if (!relsPart.endsWith(".rels")) continue;
    const source = relationshipSource(relsPart);
    if (source && !entries.has(source)) {
      reject(
        "MISSING_RELATIONSHIP_SOURCE",
        `Relationship source part is missing: ${source}`
      );
    }
    const xml = decodeAndValidateOoxmlXml(relsPart, bytes, policy);
    if (!rootElementLooksValid(xml, "Relationships")) {
      reject("INVALID_RELATIONSHIPS", `Relationship root is malformed: ${relsPart}`);
    }
    const candidates = xml.match(/<(?:[A-Za-z_][\w.-]*:)?Relationship\b/gi) ?? [];
    const matches = [
      ...xml.matchAll(/<(?:[A-Za-z_][\w.-]*:)?Relationship\b([^>]*)>/gi),
    ];
    if (candidates.length !== matches.length) {
      reject("INVALID_RELATIONSHIPS", `Relationship entry is malformed: ${relsPart}`);
    }
    if (matches.length > policy.maxRelationshipsPerPart) {
      reject("RELATIONSHIP_LIMIT", `Too many relationships in ${relsPart}`);
    }
    const seenIds = new Set<string>();
    for (const match of matches) {
      const attributes = parseAttributes(match[1]);
      const id = attributes.Id;
      const type = attributes.Type;
      const target = attributes.Target;
      if (!id || !type || target === undefined || !target) {
        reject("INVALID_RELATIONSHIPS", `Relationship is incomplete: ${relsPart}`);
      }
      if (seenIds.has(id)) {
        reject("DUPLICATE_RELATIONSHIP_ID", `Duplicate relationship id ${id}: ${relsPart}`);
      }
      seenIds.add(id);
      const targetModeRaw = attributes.TargetMode ?? null;
      if (
        targetModeRaw !== null &&
        targetModeRaw.toLowerCase() !== "external" &&
        targetModeRaw.toLowerCase() !== "internal"
      ) {
        reject("INVALID_RELATIONSHIPS", `Unknown TargetMode in ${relsPart}`);
      }
      const targetMode =
        targetModeRaw?.toLowerCase() === "external" ? "External" : "Internal";
      const normalizedTarget =
        targetMode === "External"
          ? null
          : normalizeRelationshipTarget(source, target);
      if (
        targetMode === "Internal" &&
        (!normalizedTarget || !entries.has(normalizedTarget))
      ) {
        reject(
          "MISSING_RELATIONSHIP_TARGET",
          `Relationship target is missing: ${source}#${id} -> ${normalizedTarget}`
        );
      }
      if (targetMode === "External") {
        externalCount += 1;
        if (externalCount > policy.maxExternalRelationships) {
          reject(
            "EXTERNAL_RELATIONSHIP_LIMIT",
            "External relationships exceed their limit."
          );
        }
      }
      const values = {
        source,
        relsPart,
        id,
        type,
        target,
        targetMode,
        targetModeRaw,
        normalizedTarget,
      } as const;
      records.push(Object.freeze({ ...values, digest: relationshipDigest(values) }));
      if (records.length > policy.maxRelationshipsTotal) {
        reject("RELATIONSHIP_LIMIT", "Package relationships exceed their total limit.");
      }
    }
  }
  records.sort((left, right) =>
    [left.source, left.id, left.type, left.target, left.targetModeRaw ?? ""]
      .join("\u001f")
      .localeCompare(
        [right.source, right.id, right.type, right.target, right.targetModeRaw ?? ""].join(
          "\u001f"
        )
      )
  );
  return Object.freeze(records);
}
