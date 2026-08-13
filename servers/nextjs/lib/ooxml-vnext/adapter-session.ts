import { createHash } from "node:crypto";

import { OOXML_RELATIONSHIP_TYPES } from "./package-ops.ts";
import {
  assertPackageGraphPrecondition,
  buildPackageGraph,
  type PackageGraph,
  type PackagePartClassification,
} from "./package-graph.ts";
import type {
  OoxmlAdapterSession,
  OriginalPackageInventoryReceipt,
  PackageDigestPrecondition,
  PackageInspectionReceipt,
  PreservationPartManifestEntry,
  PreservationPartStatus,
  PreservationReadBackReceipt,
} from "./adapter-contracts.ts";

const PRESENTATION_RELS = "ppt/_rels/presentation.xml.rels";

export function asBuffer(bytes: Uint8Array): Buffer {
  return Buffer.from(bytes.buffer, bytes.byteOffset, bytes.byteLength);
}

function sha256(bytes: Uint8Array): string {
  return createHash("sha256").update(bytes).digest("hex");
}

export function originalPackageInventory(
  archiveBytes: Uint8Array,
  graph: PackageGraph,
): OriginalPackageInventoryReceipt {
  return Object.freeze({
    schemaVersion: 1 as const,
    archiveSha256: sha256(archiveBytes),
    archiveByteLength: archiveBytes.byteLength,
    packageDigest: graph.packageDigest,
    relationshipDigest: graph.relationshipDigest,
    closureDigest: graph.closureDigest,
    parts: Object.freeze(
      graph.parts.map(({ name, bytes, digest }) =>
        Object.freeze({ name, byteLength: bytes, sha256: digest }),
      ),
    ),
  });
}

export function graphFor(session: OoxmlAdapterSession): PackageGraph {
  return buildPackageGraph(session.entries, {
    generatedParts: session.generatedParts,
    patchSliceParts: session.patchSliceParts,
  });
}

function countParts(graph: PackageGraph, expression: RegExp): number {
  return graph.parts.filter(({ name }) => expression.test(name)).length;
}

export function inspectionReceipt(graph: PackageGraph): PackageInspectionReceipt {
  const partClassifications: Record<PackagePartClassification, number> = {
    PRESERVE_EXACT: 0,
    PATCH_SLICE: 0,
    GENERATED: 0,
    QUARANTINED: 0,
  };
  for (const part of graph.parts) partClassifications[part.classification] += 1;
  return Object.freeze({
    classification: graph.classification,
    partClassifications: Object.freeze(partClassifications),
    partCount: graph.parts.length,
    slideCount: countParts(graph, /^ppt\/slides\/slide\d+\.xml$/),
    masterCount: countParts(graph, /^ppt\/slideMasters\/slideMaster\d+\.xml$/),
    layoutCount: countParts(graph, /^ppt\/slideLayouts\/slideLayout\d+\.xml$/),
    externalRelationshipCount: graph.externalRelationships.length,
    externalRelationshipDecisions: graph.externalRelationshipPolicy.decisions,
    externalRelationshipWarnings: graph.externalRelationshipPolicy.warnings,
    packageDigest: graph.packageDigest,
    relationshipDigest: graph.relationshipDigest,
    closureDigest: graph.closureDigest,
    macroDetected: graph.macroDetected,
    quarantineReasons: graph.quarantineReasons,
  });
}

export function changedPartNames(
  before: PackageGraph,
  after: PackageGraph,
): readonly string[] {
  return Object.freeze(
    [...new Set([...Object.keys(before.partDigests), ...Object.keys(after.partDigests)])]
      .filter((name) => before.partDigests[name] !== after.partDigests[name])
      .sort(),
  );
}

export function assertOperationPrecondition(
  session: OoxmlAdapterSession,
  graph: PackageGraph,
  precondition: PackageDigestPrecondition,
): void {
  if (!precondition || precondition.revision !== session.revision) {
    throw new Error("OOXML operation revision precondition failed");
  }
  assertPackageGraphPrecondition(graph, precondition);
  if (graph.classification === "QUARANTINED") {
    throw new Error("Quarantined OOXML packages cannot be mutated or published");
  }
}

export function slidePartNames(
  entries: ReadonlyMap<string, Buffer>,
): readonly string[] {
  return [...entries.keys()]
    .filter((name) => /^ppt\/slides\/slide\d+\.xml$/.test(name))
    .sort((left, right) => Number(left.match(/\d+/)?.[0]) - Number(right.match(/\d+/)?.[0]));
}

export function presentationMasterRelationshipId(
  entries: ReadonlyMap<string, Buffer>,
): string | null {
  const xml = entries.get(PRESENTATION_RELS)?.toString("utf8") ?? "";
  const match = [...xml.matchAll(/<Relationship\b([^>]*)\/>/g)].find((candidate) =>
    candidate[1].includes(`Type="${OOXML_RELATIONSHIP_TYPES.master}"`) &&
    /\bTarget="slideMasters\/slideMaster1\.xml"/.test(candidate[1]),
  );
  return match?.[1].match(/\bId="([^"]+)"/)?.[1] ?? null;
}

export function preservationReceipt(
  session: OoxmlAdapterSession,
  graph: PackageGraph,
): PreservationReadBackReceipt {
  const missingParts: string[] = [];
  const unexpectedAddedParts: string[] = [];
  const unexpectedChangedParts: string[] = [];
  let preservedExactPartCount = 0;
  for (const [name, digest] of Object.entries(session.baselineDigests)) {
    if (!Object.hasOwn(graph.partDigests, name)) missingParts.push(name);
    else if (session.generatedParts.has(name) || session.patchSliceParts.has(name)) continue;
    else if (graph.partDigests[name] !== digest) unexpectedChangedParts.push(name);
    else preservedExactPartCount += 1;
  }
  for (const name of Object.keys(graph.partDigests)) {
    if (
      !Object.hasOwn(session.baselineDigests, name) &&
      !session.generatedParts.has(name) &&
      !session.patchSliceParts.has(name)
    ) {
      unexpectedAddedParts.push(name);
    }
  }
  const partNames = [...new Set([
    ...Object.keys(session.baselineDigests),
    ...Object.keys(graph.partDigests),
  ])].sort();
  const currentByName = new Map(graph.parts.map((part) => [part.name, part]));
  const parts = partNames.map((name): PreservationPartManifestEntry => {
    const originalSha256 = session.baselineDigests[name] ?? null;
    const currentSha256 = graph.partDigests[name] ?? null;
    const currentPart = currentByName.get(name);
    const classification: PackagePartClassification = graph.macroDetected
      ? "QUARANTINED"
      : session.generatedParts.has(name)
        ? "GENERATED"
        : session.patchSliceParts.has(name)
          ? "PATCH_SLICE"
          : currentPart?.classification ?? "PRESERVE_EXACT";
    let status: PreservationPartStatus;
    if (classification === "QUARANTINED") status = "QUARANTINED";
    else if (currentSha256 === null) {
      status = classification === "GENERATED" ? "REMOVED_APPROVED" : "MISSING";
    } else if (originalSha256 === null) {
      status = classification === "GENERATED" ? "GENERATED" : "UNAPPROVED_ADDITION";
    } else if (classification === "PATCH_SLICE") status = "PATCHED_SLICE";
    else if (classification === "GENERATED") status = "GENERATED";
    else if (originalSha256 === currentSha256) status = "PRESERVED_EXACT";
    else status = "UNAPPROVED_CHANGE";
    return Object.freeze({ name, classification, status, originalSha256, currentSha256 });
  });
  return Object.freeze({
    schemaVersion: 1 as const,
    originalPackage: session.originalPackage,
    baselinePartCount: Object.keys(session.baselineDigests).length,
    preservedExactPartCount,
    generatedPartCount: session.generatedParts.size,
    patchSlicePartCount: session.patchSliceParts.size,
    missingParts: Object.freeze(missingParts.sort()),
    unexpectedAddedParts: Object.freeze(unexpectedAddedParts.sort()),
    unexpectedChangedParts: Object.freeze(unexpectedChangedParts.sort()),
    silentLossCount:
      missingParts.length + unexpectedAddedParts.length + unexpectedChangedParts.length,
    parts: Object.freeze(parts),
  });
}
