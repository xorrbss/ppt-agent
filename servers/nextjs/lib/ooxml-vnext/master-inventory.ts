import { createHash } from "node:crypto";

import {
  buildPackageGraph,
  parseRelationships,
  type PackageGraph,
  type PackageRelationship,
} from "./package-graph.ts";
import {
  decodeAndValidateOoxmlXml,
  DEFAULT_OOXML_SECURITY_POLICY,
  type OoxmlSecurityPolicy,
} from "./security.ts";
import {
  CORPORATE_MASTER_OPAQUE_BUNDLE_VERSION,
  MASTER_LAYOUT_INVENTORY_VERSION,
  MasterInventoryError,
  type CorporateMasterOpaqueBundle,
  type CorporateMasterPublicationPolicy,
  type CorporateMasterSelector,
  type MasterInventoryOptions,
  type MasterLayoutInventory,
  type MasterLayoutReference,
  type MasterLayoutRelationshipRecord,
  type OpaqueCorporateMasterMember,
  type PresentationMasterReference,
  type SlideLayoutInventoryRecord,
  type SlideMasterInventoryRecord,
} from "./master-inventory-contracts.ts";
import {
  commonSlideDataName,
  isRelationshipType,
  orderedIdReferences,
  partNamesForContentType,
  relationshipPartFor,
  relationshipSnapshot,
  xmlFor,
} from "./master-inventory-xml.ts";
export * from "./master-inventory-contracts.ts";

const INVENTORY_VERSION = MASTER_LAYOUT_INVENTORY_VERSION;
const CORPORATE_MASTER_BUNDLE_VERSION = CORPORATE_MASTER_OPAQUE_BUNDLE_VERSION;
const PRESENTATION_PART = "ppt/presentation.xml";
const MASTER_CONTENT_TYPE =
  "application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml";
const LAYOUT_CONTENT_TYPE =
  "application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml";


function reject(code: string, message: string): never {
  throw new MasterInventoryError(code, message);
}

function sha256(bytes: Uint8Array | string): string {
  return createHash("sha256").update(bytes).digest("hex");
}

function stableDigest(records: readonly string[]): string {
  const hash = createHash("sha256");
  for (const record of [...records].sort((left, right) => left.localeCompare(right, "en"))) {
    const bytes = Buffer.from(record, "utf8");
    hash.update(String(bytes.length)).update(":").update(bytes).update("\0");
  }
  return hash.digest("hex");
}

function inventoryFromGraph(
  graph: PackageGraph,
  policy: Readonly<OoxmlSecurityPolicy>,
): MasterLayoutInventory {
  const masterParts = partNamesForContentType(graph, MASTER_CONTENT_TYPE);
  const layoutParts = partNamesForContentType(graph, LAYOUT_CONTENT_TYPE);
  const masterSet = new Set(masterParts);
  const layoutSet = new Set(layoutParts);
  const structural = graph.relationships
    .filter(
      (relationship) =>
        isRelationshipType(relationship, "slideMaster") ||
        isRelationshipType(relationship, "slideLayout"),
    )
    .map(relationshipSnapshot);

  const presentationXml = graph.entries.has(PRESENTATION_PART)
    ? xmlFor(graph, PRESENTATION_PART, policy)
    : null;
  const presentationList = presentationXml
    ? orderedIdReferences(presentationXml, "sldMasterId")
    : [];
  const presentationRels = graph.relationships.filter(
    (relationship) =>
      relationship.source === PRESENTATION_PART &&
      relationship.targetMode === "Internal" &&
      isRelationshipType(relationship, "slideMaster"),
  );

  const masters: SlideMasterInventoryRecord[] = masterParts.map((partName) => {
    const part = graph.parts.find((candidate) => candidate.name === partName)!;
    const xml = xmlFor(graph, partName, policy);
    const list = orderedIdReferences(xml, "sldLayoutId");
    const outgoing = graph.relationships.filter(
      (relationship) =>
        relationship.source === partName &&
        relationship.targetMode === "Internal" &&
        isRelationshipType(relationship, "slideLayout"),
    );
    const byId = new Map(outgoing.map((relationship) => [relationship.id, relationship]));
    const listed = new Set(list.map((reference) => reference.relationshipId));
    const layouts: MasterLayoutReference[] = [];
    const issues: string[] = [];

    for (const reference of list) {
      const relationship = byId.get(reference.relationshipId);
      if (!relationship?.normalizedTarget || !layoutSet.has(relationship.normalizedTarget)) {
        issues.push("MASTER_LIST_RELATION_MISSING_OR_NOT_LAYOUT");
        continue;
      }
      layouts.push(
        Object.freeze({
          relationshipId: relationship.id,
          masterLayoutId: reference.numericId,
          layoutPartName: relationship.normalizedTarget,
          order: reference.order,
          listedInMasterXml: true,
        }),
      );
    }
    for (const relationship of outgoing) {
      if (!relationship.normalizedTarget || listed.has(relationship.id)) continue;
      issues.push("MASTER_LAYOUT_RELATION_NOT_LISTED");
      layouts.push(
        Object.freeze({
          relationshipId: relationship.id,
          masterLayoutId: null,
          layoutPartName: relationship.normalizedTarget,
          order: null,
          listedInMasterXml: false,
        }),
      );
    }

    const refs = presentationRels
      .filter((relationship) => relationship.normalizedTarget === partName)
      .map((relationship): PresentationMasterReference => {
        const listedReference = presentationList.find(
          (reference) => reference.relationshipId === relationship.id,
        );
        if (!listedReference) issues.push("PRESENTATION_MASTER_RELATION_NOT_LISTED");
        return Object.freeze({
          relationshipId: relationship.id,
          presentationMasterId: listedReference?.numericId ?? null,
          order: listedReference?.order ?? null,
        });
      });
    if (refs.length === 0) issues.push("ORPHAN_MASTER");

    return Object.freeze({
      partName,
      digest: part.digest,
      contentType: part.contentType,
      name: commonSlideDataName(xml),
      presentationReferences: Object.freeze(refs),
      layouts: Object.freeze(layouts),
      relationshipPartName: graph.entries.has(relationshipPartFor(partName))
        ? relationshipPartFor(partName)
        : null,
      issueCodes: Object.freeze([...new Set(issues)].sort()),
    });
  });

  const layouts: SlideLayoutInventoryRecord[] = layoutParts.map((partName) => {
    const part = graph.parts.find((candidate) => candidate.name === partName)!;
    const xml = xmlFor(graph, partName, policy);
    const parentMasterParts = masters
      .filter((master) => master.layouts.some((layout) => layout.layoutPartName === partName))
      .map((master) => master.partName)
      .sort((left, right) => left.localeCompare(right, "en"));
    const backlinks = graph.relationships.filter(
      (relationship) =>
        relationship.source === partName &&
        relationship.targetMode === "Internal" &&
        isRelationshipType(relationship, "slideMaster"),
    );
    const backlinkTargets = new Set(
      backlinks.map((relationship) => relationship.normalizedTarget).filter(Boolean),
    );
    const issues: string[] = [];
    if (parentMasterParts.length === 0) issues.push("ORPHAN_LAYOUT");
    if (parentMasterParts.length > 1) issues.push("LAYOUT_MULTIPLE_PARENT_MASTERS");
    if (
      backlinks.length !== 1 ||
      parentMasterParts.length !== 1 ||
      backlinkTargets.size !== 1 ||
      !backlinkTargets.has(parentMasterParts[0])
    ) {
      issues.push("LAYOUT_MASTER_BACKLINK_MISSING_OR_INCONSISTENT");
    }
    return Object.freeze({
      partName,
      digest: part.digest,
      contentType: part.contentType,
      name: commonSlideDataName(xml),
      parentMasterParts: Object.freeze(parentMasterParts),
      forwardMasterRelationshipIds: Object.freeze(
        backlinks.map((relationship) => relationship.id).sort((left, right) => left.localeCompare(right, "en")),
      ),
      relationshipPartName: graph.entries.has(relationshipPartFor(partName))
        ? relationshipPartFor(partName)
        : null,
      issueCodes: Object.freeze(issues.sort()),
    });
  });

  const issueCodes = [
    ...masters.flatMap((master) => master.issueCodes.map((code) => `${master.partName}:${code}`)),
    ...layouts.flatMap((layout) => layout.issueCodes.map((code) => `${layout.partName}:${code}`)),
  ].sort((left, right) => left.localeCompare(right, "en"));
  for (const reference of presentationList) {
    const relationship = presentationRels.find((candidate) => candidate.id === reference.relationshipId);
    if (!relationship?.normalizedTarget || !masterSet.has(relationship.normalizedTarget)) {
      issueCodes.push(`${PRESENTATION_PART}:${reference.relationshipId}:MASTER_LIST_RELATION_MISSING_OR_NOT_MASTER`);
    }
  }

  return Object.freeze({
    version: INVENTORY_VERSION,
    packageDigest: graph.packageDigest,
    relationshipDigest: graph.relationshipDigest,
    masters: Object.freeze(masters),
    layouts: Object.freeze(layouts),
    structuralRelationships: Object.freeze(structural),
    issueCodes: Object.freeze(issueCodes.sort((left, right) => left.localeCompare(right, "en"))),
  });
}

export function inventoryMasterLayouts(
  entries: ReadonlyMap<string, Buffer>,
  options: MasterInventoryOptions = {},
): MasterLayoutInventory {
  const policy = options.policy ?? DEFAULT_OOXML_SECURITY_POLICY;
  return inventoryFromGraph(buildPackageGraph(entries, { policy }), policy);
}

function resolveMaster(
  inventory: MasterLayoutInventory,
  selector: CorporateMasterSelector,
): SlideMasterInventoryRecord {
  const matches = inventory.masters.filter((master) => {
    if ("partName" in selector) return master.partName === selector.partName;
    if ("presentationRelationshipId" in selector) {
      return master.presentationReferences.some(
        (reference) => reference.relationshipId === selector.presentationRelationshipId,
      );
    }
    return master.presentationReferences.some(
      (reference) => reference.presentationMasterId === selector.presentationMasterId,
    );
  });
  if (matches.length !== 1) {
    reject(
      "MASTER_SELECTOR_NOT_UNIQUE",
      `Corporate master selector resolved to ${matches.length} masters; exactly one is required.`,
    );
  }
  return matches[0];
}

function closureForMaster(graph: PackageGraph, masterPartName: string): {
  readonly partNames: readonly string[];
  readonly relationships: readonly PackageRelationship[];
} {
  const visited = new Set<string>([masterPartName]);
  const queue = [masterPartName];
  const relationships: PackageRelationship[] = [];
  while (queue.length > 0) {
    const source = queue.shift()!;
    for (const relationship of graph.relationships.filter((candidate) => candidate.source === source)) {
      relationships.push(relationship);
      if (
        relationship.targetMode === "Internal" &&
        relationship.normalizedTarget &&
        !visited.has(relationship.normalizedTarget)
      ) {
        visited.add(relationship.normalizedTarget);
        queue.push(relationship.normalizedTarget);
      }
    }
  }
  const partNames = new Set(visited);
  for (const source of visited) {
    const relsPart = relationshipPartFor(source);
    if (graph.entries.has(relsPart)) partNames.add(relsPart);
  }
  return {
    partNames: Object.freeze([...partNames].sort((left, right) => left.localeCompare(right, "en"))),
    relationships: Object.freeze(relationships),
  };
}

const PUBLICATION_POLICY: CorporateMasterPublicationPolicy = Object.freeze({
  mutationClassification: "PRESERVE_EXACT",
  partNames: "IDENTITY_ONLY",
  collisions: "ALLOW_IDENTICAL_REJECT_DIFFERENT",
  externalRelationships: "PRESERVE_INERT_NEVER_DEREFERENCE",
  missingRelationshipTargets: "REJECT",
});

function captureDigestFor(
  masterPartName: string,
  members: readonly OpaqueCorporateMasterMember[],
  relationships: readonly MasterLayoutRelationshipRecord[],
): string {
  return stableDigest([
    `master\u001f${masterPartName}`,
    ...members.map(
      (member) =>
        `part\u001f${member.partName}\u001f${member.bytes}\u001f${member.sha256}\u001f${member.contentType ?? ""}`,
    ),
    ...relationships.map(
      (relationship) =>
        `relationship\u001f${relationship.source}\u001f${relationship.id}\u001f${relationship.digest}`,
    ),
  ]);
}

function relationshipComparisonKey(
  relationship: MasterLayoutRelationshipRecord | PackageRelationship,
): string {
  return [
    relationship.source,
    relationship.relsPart,
    relationship.id,
    relationship.type,
    relationship.target,
    relationship.targetMode,
    relationship.targetModeRaw ?? "",
    relationship.normalizedTarget ?? "",
    relationship.digest,
  ].join("\u001f");
}

export function captureCorporateMaster(
  entries: ReadonlyMap<string, Buffer>,
  selector: CorporateMasterSelector,
  options: MasterInventoryOptions = {},
): CorporateMasterOpaqueBundle {
  const policy = options.policy ?? DEFAULT_OOXML_SECURITY_POLICY;
  const graph = buildPackageGraph(entries, { policy });
  if (graph.macroDetected) {
    reject("QUARANTINED_PACKAGE", "Corporate masters cannot be captured from a macro-quarantined package.");
  }
  const inventory = inventoryFromGraph(graph, policy);
  const master = resolveMaster(inventory, selector);
  if (master.issueCodes.length > 0) {
    reject(
      "UNSAFE_MASTER_TOPOLOGY",
      `Corporate master topology is not safe to export: ${master.issueCodes.join(", ")}`,
    );
  }
  const selectedLayouts = inventory.layouts.filter((layout) =>
    master.layouts.some((reference) => reference.layoutPartName === layout.partName),
  );
  const layoutIssues = selectedLayouts.flatMap((layout) => layout.issueCodes);
  if (layoutIssues.length > 0 || selectedLayouts.length !== master.layouts.length) {
    reject(
      "UNSAFE_LAYOUT_TOPOLOGY",
      `Corporate master layouts are not safe to export: ${layoutIssues.join(", ") || "inventory mismatch"}`,
    );
  }

  const closure = closureForMaster(graph, master.partName);
  const members = closure.partNames.map((partName): OpaqueCorporateMasterMember => {
    const bytes = graph.entries.get(partName);
    if (!bytes) reject("MISSING_CLOSURE_PART", `Corporate master closure part is missing: ${partName}`);
    const part = graph.parts.find((candidate) => candidate.name === partName)!;
    return Object.freeze({
      partName,
      bytes: bytes.length,
      sha256: sha256(bytes),
      contentType: part.contentType,
      payloadBase64: bytes.toString("base64"),
    });
  });
  const relationships = closure.relationships.map(relationshipSnapshot);
  const captureDigest = captureDigestFor(master.partName, members, relationships);
  return Object.freeze({
    version: CORPORATE_MASTER_BUNDLE_VERSION,
    sourcePackageDigest: graph.packageDigest,
    sourceRelationshipDigest: graph.relationshipDigest,
    masterPartName: master.partName,
    masterDigest: master.digest,
    layoutPartNames: Object.freeze(master.layouts.map((layout) => layout.layoutPartName)),
    closurePartNames: Object.freeze(closure.partNames),
    members: Object.freeze(members),
    relationships: Object.freeze(relationships),
    externalRelationshipCount: relationships.filter(
      (relationship) => relationship.targetMode === "External",
    ).length,
    publicationPolicy: PUBLICATION_POLICY,
    captureDigest,
  });
}

export function materializeCorporateMasterBundle(
  bundle: CorporateMasterOpaqueBundle,
  destinationEntries?: ReadonlyMap<string, Buffer>,
): Map<string, Buffer> {
  if (bundle.version !== CORPORATE_MASTER_BUNDLE_VERSION) {
    reject("UNSUPPORTED_BUNDLE_VERSION", `Unsupported corporate master bundle: ${bundle.version}`);
  }
  if (bundle.publicationPolicy.partNames !== "IDENTITY_ONLY") {
    reject("UNSAFE_PUBLICATION_POLICY", "Corporate master part-name remapping is not supported.");
  }
  const memberNames = bundle.members.map((member) => member.partName);
  if (
    new Set(memberNames).size !== memberNames.length ||
    [...memberNames].sort((left, right) => left.localeCompare(right, "en")).join("\0") !==
      [...bundle.closurePartNames].sort((left, right) => left.localeCompare(right, "en")).join("\0")
  ) {
    reject("INVALID_BUNDLE_MEMBERS", "Corporate master bundle member inventory is inconsistent.");
  }
  const result = new Map<string, Buffer>();
  for (const member of bundle.members) {
    const bytes = Buffer.from(member.payloadBase64, "base64");
    if (bytes.length !== member.bytes || sha256(bytes) !== member.sha256) {
      reject("BUNDLE_MEMBER_DIGEST_MISMATCH", `Corporate master member digest mismatch: ${member.partName}`);
    }
    const existing = destinationEntries?.get(member.partName);
    if (existing && !existing.equals(bytes)) {
      reject("PART_COLLISION", `Corporate master member collides with a different destination part: ${member.partName}`);
    }
    result.set(member.partName, bytes);
  }
  if (captureDigestFor(bundle.masterPartName, bundle.members, bundle.relationships) !== bundle.captureDigest) {
    reject("BUNDLE_CAPTURE_DIGEST_MISMATCH", "Corporate master bundle capture digest does not match.");
  }
  if (sha256(result.get(bundle.masterPartName) ?? Buffer.alloc(0)) !== bundle.masterDigest) {
    reject("BUNDLE_MASTER_DIGEST_MISMATCH", "Corporate master payload does not match its master digest.");
  }
  for (const relationship of bundle.relationships) {
    if (
      relationship.targetMode === "Internal" &&
      relationship.normalizedTarget &&
      !result.has(relationship.normalizedTarget)
    ) {
      reject(
        "BUNDLE_RELATIONSHIP_TARGET_MISSING",
        `Corporate master relationship target is absent: ${relationship.source}#${relationship.id}`,
      );
    }
  }
  const encodedRelationships = bundle.relationships
    .map(relationshipComparisonKey)
    .sort((left, right) => left.localeCompare(right, "en"));
  const payloadRelationships = parseRelationships(result)
    .map(relationshipComparisonKey)
    .sort((left, right) => left.localeCompare(right, "en"));
  if (encodedRelationships.join("\0") !== payloadRelationships.join("\0")) {
    reject(
      "BUNDLE_RELATIONSHIP_INVENTORY_MISMATCH",
      "Corporate master relationship inventory does not match its opaque relationship payloads.",
    );
  }
  return result;
}
