import { createHash } from "node:crypto";
import { posix } from "node:path";

import { evaluateExternalRelationshipPolicy } from "./external-relationship-policy.ts";
import {
  contentTypeForPart,
  parseContentTypes,
  parseRelationships,
} from "./package-graph.ts";
import type {
  ContentTypeManifest,
  PackageGraph,
  PackageGraphOptions,
  PackageInventoryManifest,
  PackagePartClassification,
  PackagePartRecord,
  PackagePreservationManifest,
  PackageRelationship,
} from "./package-graph-contracts.ts";
import { PackageGraphError } from "./package-graph-contracts.ts";
import {
  assertCanonicalOoxmlPartName,
  DEFAULT_OOXML_SECURITY_POLICY,
  immutablePackageEntries,
  inspectOoxmlEntriesSecurity,
} from "./security.ts";

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

function macroEvidence(
  entries: ReadonlyMap<string, Buffer>,
  contentTypes: ContentTypeManifest,
  relationships: readonly PackageRelationship[]
): string[] {
  const evidence: string[] = [];
  for (const name of entries.keys()) {
    if (/(?:^|\/)vbaProject(?:Signature)?\.bin$/i.test(name)) evidence.push(`part:${name}`);
  }
  for (const [name, contentType] of contentTypes.overrides) {
    if (/macroEnabled|vbaProject/i.test(contentType)) evidence.push(`content-type:${name}`);
  }
  for (const contentType of contentTypes.defaults.values()) {
    if (/macroEnabled|vbaProject/i.test(contentType)) {
      evidence.push(`content-type-default:${contentType}`);
    }
  }
  for (const relationship of relationships) {
    if (/vbaProject|vbaProjectSignature/i.test(relationship.type)) {
      evidence.push(`relationship:${relationship.source}#${relationship.id}`);
    }
  }
  return [...new Set(evidence)].sort();
}

function unknownActiveContentEvidence(
  entries: ReadonlyMap<string, Buffer>,
  contentTypes: ContentTypeManifest,
  relationships: readonly PackageRelationship[]
): string[] {
  const evidence: string[] = [];
  for (const name of entries.keys()) {
    if (/(?:^|\/)activeX(?:\/|$)/i.test(name)) evidence.push(`active-part:${name}`);
  }
  for (const [name, contentType] of contentTypes.overrides) {
    if (/activeX|script|add-?in/i.test(contentType)) {
      evidence.push(`active-content-type:${name}`);
    }
  }
  for (const relationship of relationships) {
    if (/activeX|script|add-?in/i.test(relationship.type)) {
      evidence.push(`active-relationship:${relationship.relsPart}#${relationship.id}`);
    }
  }
  return [...new Set(evidence)].sort();
}

function toPartSet(
  value: ReadonlySet<string> | readonly string[] | undefined
): ReadonlySet<string> {
  return value instanceof Set ? value : new Set(value ?? []);
}

function relsPartForSource(source: string): string {
  if (!source) return "_rels/.rels";
  return posix.join(posix.dirname(source), "_rels", `${posix.basename(source)}.rels`);
}

export function buildPackageGraph(
  entries: ReadonlyMap<string, Buffer>,
  options: PackageGraphOptions = {}
): PackageGraph {
  if (entries.size === 0) reject("EMPTY_PACKAGE", "OOXML package has no parts.");
  const secured = inspectOoxmlEntriesSecurity(
    entries,
    options.policy ?? DEFAULT_OOXML_SECURITY_POLICY
  );
  const immutableEntries = secured.entries;
  const policy = secured.policy;
  const contentTypes = parseContentTypes(immutableEntries, policy);
  const relationships = parseRelationships(immutableEntries, policy);
  if (!immutableEntries.has("_rels/.rels")) {
    reject("MISSING_ROOT_RELATIONSHIPS", "The package root relationship part is missing.");
  }
  for (const partName of immutableEntries.keys()) {
    if (partName === "[Content_Types].xml") continue;
    if (contentTypeForPart(partName, contentTypes) === null) {
      reject("MISSING_PART_CONTENT_TYPE", `No content type is declared for package part: ${partName}`);
    }
  }
  const officeDocuments = relationships.filter(
    (relationship) => relationship.source === "" &&
      relationship.targetMode === "Internal" &&
      /\/officeDocument$/.test(relationship.type)
  );
  if (officeDocuments.length !== 1) {
    reject(
      "INVALID_OFFICE_DOCUMENT_RELATIONSHIP",
      "The package must contain exactly one internal root officeDocument relationship."
    );
  }
  const generatedParts = toPartSet(options.generatedParts);
  const patchSliceParts = toPartSet(options.patchSliceParts);
  for (const partName of [...generatedParts, ...patchSliceParts]) {
    assertCanonicalOoxmlPartName(partName, policy);
    if (!immutableEntries.has(partName)) {
      reject("UNKNOWN_CLASSIFICATION_PART", `Classification refers to a missing part: ${partName}`);
    }
    if (generatedParts.has(partName) && patchSliceParts.has(partName)) {
      reject("AMBIGUOUS_CLASSIFICATION", `Part has two mutation classifications: ${partName}`);
    }
  }
  const externalRelationshipPolicy = evaluateExternalRelationshipPolicy(relationships);
  const macroReasons = macroEvidence(immutableEntries, contentTypes, relationships);
  const unknownActiveReasons = unknownActiveContentEvidence(
    immutableEntries,
    contentTypes,
    relationships,
  );
  const quarantineReasons = [
    ...macroReasons,
    ...unknownActiveReasons,
    ...externalRelationshipPolicy.quarantineReasons,
  ].sort();
  const macroDetected = macroReasons.length > 0;
  const quarantined = quarantineReasons.length > 0;
  const parts: PackagePartRecord[] = [...immutableEntries]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([name, bytes]) => {
      const classification: PackagePartClassification = quarantined
        ? "QUARANTINED"
        : generatedParts.has(name)
          ? "GENERATED"
          : patchSliceParts.has(name)
            ? "PATCH_SLICE"
            : "PRESERVE_EXACT";
      return Object.freeze({
        name,
        bytes: bytes.length,
        digest: sha256(bytes),
        contentType: contentTypeForPart(name, contentTypes),
        classification,
      });
    });
  const partDigestsMutable: Record<string, string> = Object.create(null) as Record<string, string>;
  for (const part of parts) partDigestsMutable[part.name] = part.digest;
  const partDigests = Object.freeze(partDigestsMutable);
  const packageDigest = stableDigest(
    parts.map((part) => [part.name, part.bytes, part.digest].join("\u001f"))
  );
  const relationshipDigestValue = stableDigest(
    relationships.map((relationship) =>
      [
        relationship.source,
        relationship.id,
        relationship.type,
        relationship.target,
        relationship.targetMode,
        relationship.targetModeRaw ?? "",
        relationship.normalizedTarget ?? "",
      ].join("\u001f")
    )
  );

  const outgoing = new Map<string, PackageRelationship[]>();
  for (const relationship of relationships) {
    if (relationship.targetMode !== "Internal") continue;
    const list = outgoing.get(relationship.source) ?? [];
    list.push(relationship);
    outgoing.set(relationship.source, list);
  }
  const visited = new Set<string>([""]);
  const queue = [""];
  const closureRelationships: PackageRelationship[] = [];
  while (queue.length > 0) {
    const source = queue.shift()!;
    for (const relationship of outgoing.get(source) ?? []) {
      closureRelationships.push(relationship);
      const target = relationship.normalizedTarget!;
      if (!visited.has(target)) {
        visited.add(target);
        queue.push(target);
      }
    }
  }
  visited.delete("");
  const reachableParts = [...visited].sort();
  const closurePartNames = new Set(reachableParts);
  for (const source of ["", ...reachableParts]) {
    const relsPart = relsPartForSource(source);
    if (immutableEntries.has(relsPart)) closurePartNames.add(relsPart);
  }
  const closureDigest = stableDigest([
    ...[...closurePartNames].map((name) => `part\u001f${name}\u001f${partDigests[name]}`),
    ...closureRelationships.map((relationship) => `relationship\u001f${relationship.digest}`),
  ]);
  const externalRelationships = Object.freeze(
    relationships.filter((relationship) => relationship.targetMode === "External")
  );
  const frozenParts = Object.freeze(parts);
  const inventoryDigest = stableDigest(
    parts.map((part) =>
      [part.name, part.bytes, part.digest, part.contentType ?? "", part.classification].join("\u001f")
    )
  );
  const inventory: PackageInventoryManifest = Object.freeze({
    version: "ooxml-package-inventory/v1",
    partCount: parts.length,
    totalBytes: secured.totalUncompressedBytes,
    packageDigest,
    inventoryDigest,
    parts: frozenParts,
  });
  const packageClassification: PackagePartClassification = quarantined
    ? "QUARANTINED"
    : "PRESERVE_EXACT";
  const manifestDigest = stableDigest([
    "ooxml-preservation-manifest/v1",
    packageDigest,
    relationshipDigestValue,
    closureDigest,
    inventoryDigest,
    packageClassification,
    ...quarantineReasons.map((reason) => `quarantine\u001f${reason}`),
  ]);
  const preservationManifest: PackagePreservationManifest = Object.freeze({
    version: "ooxml-preservation-manifest/v1",
    sourcePackageDigest: packageDigest,
    sourceRelationshipDigest: relationshipDigestValue,
    sourceClosureDigest: closureDigest,
    sourceInventoryDigest: inventoryDigest,
    classification: packageClassification,
    parts: frozenParts,
    partDigests,
    quarantineReasons: Object.freeze(quarantineReasons),
    manifestDigest,
  });
  return Object.freeze({
    entries: immutablePackageEntries(immutableEntries),
    parts: frozenParts,
    partDigests,
    contentTypes,
    relationships,
    externalRelationships,
    externalRelationshipPolicy,
    reachableParts: Object.freeze(reachableParts),
    packageDigest,
    relationshipDigest: relationshipDigestValue,
    closureDigest,
    inventory,
    preservationManifest,
    classification: packageClassification,
    macroDetected,
    quarantineReasons: Object.freeze(quarantineReasons),
  });
}
