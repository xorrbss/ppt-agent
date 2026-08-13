import { createHash } from "node:crypto";

import { buildPackageGraph } from "./package-graph.ts";
import {
  PackageGraphPreconditionError,
  PackagePreservationError,
  type PackageGraph,
  type PackageGraphPrecondition,
  type PackageMutationAuthorization,
  type PackagePartClassification,
  type PackagePreservationManifest,
  type PackagePreservationReceipt,
} from "./package-graph-contracts.ts";
import {
  assertCanonicalOoxmlPartName,
  DEFAULT_OOXML_SECURITY_POLICY,
  type OoxmlSecurityPolicy,
} from "./security.ts";

function stableDigest(records: readonly string[]): string {
  const hash = createHash("sha256");
  for (const record of [...records].sort()) {
    const bytes = Buffer.from(record, "utf8");
    hash.update(String(bytes.length)).update(":").update(bytes).update("\0");
  }
  return hash.digest("hex");
}

function classifiedPartNames(
  manifest: PackagePreservationManifest,
  classification: PackagePartClassification
): Set<string> {
  return new Set(
    manifest.parts
      .filter((part) => part.classification === classification)
      .map((part) => part.name)
  );
}

function addAuthorizedParts(
  target: Set<string>,
  values: ReadonlySet<string> | readonly string[] | undefined,
  policy: Readonly<OoxmlSecurityPolicy>
): void {
  for (const name of values ?? []) {
    assertCanonicalOoxmlPartName(name, policy);
    target.add(name);
  }
}

function preservationManifestFailures(
  manifest: PackagePreservationManifest
): string[] {
  const failures: string[] = [];
  if (manifest.version !== "ooxml-preservation-manifest/v1") {
    failures.push("manifestVersion");
  }
  const names = new Set<string>();
  for (const part of manifest.parts) {
    if (names.has(part.name)) failures.push(`manifestDuplicate:${part.name}`);
    names.add(part.name);
    if (manifest.partDigests[part.name] !== part.digest) {
      failures.push(`manifestPartDigest:${part.name}`);
    }
    if (
      manifest.classification === "QUARANTINED" &&
      part.classification !== "QUARANTINED"
    ) {
      failures.push(`manifestQuarantineClassification:${part.name}`);
    }
    if (
      manifest.classification !== "QUARANTINED" &&
      part.classification === "QUARANTINED"
    ) {
      failures.push(`manifestUnexpectedQuarantine:${part.name}`);
    }
  }
  for (const name of Object.keys(manifest.partDigests)) {
    if (!names.has(name)) failures.push(`manifestUnknownDigest:${name}`);
  }
  const packageDigest = stableDigest(
    manifest.parts.map((part) => [part.name, part.bytes, part.digest].join("\u001f"))
  );
  if (packageDigest !== manifest.sourcePackageDigest) failures.push("manifestPackageDigest");
  const inventoryDigest = stableDigest(
    manifest.parts.map((part) =>
      [part.name, part.bytes, part.digest, part.contentType ?? "", part.classification].join(
        "\u001f"
      )
    )
  );
  if (inventoryDigest !== manifest.sourceInventoryDigest) failures.push("manifestInventoryDigest");
  const manifestDigest = stableDigest([
    "ooxml-preservation-manifest/v1",
    manifest.sourcePackageDigest,
    manifest.sourceRelationshipDigest,
    manifest.sourceClosureDigest,
    manifest.sourceInventoryDigest,
    manifest.classification,
    ...manifest.quarantineReasons.map((reason) => `quarantine\u001f${reason}`),
  ]);
  if (manifestDigest !== manifest.manifestDigest) failures.push("manifestDigest");
  return failures;
}

/**
 * Enforces a captured import manifest against a publication candidate. Exact
 * parts cannot change or disappear, PATCH_SLICE parts must remain present, and
 * only explicitly GENERATED names may be added or removed. Quarantine is a
 * hard publication boundary.
 */
export function assertPackagePreservation(
  manifest: PackagePreservationManifest,
  candidateEntries: ReadonlyMap<string, Buffer>,
  authorization: PackageMutationAuthorization = {}
): PackagePreservationReceipt {
  const invalidManifest = preservationManifestFailures(manifest);
  if (invalidManifest.length > 0) {
    throw new PackagePreservationError([...new Set(invalidManifest)].sort());
  }
  const policy = authorization.policy ?? DEFAULT_OOXML_SECURITY_POLICY;
  const patchSliceParts = classifiedPartNames(manifest, "PATCH_SLICE");
  const generatedParts = classifiedPartNames(manifest, "GENERATED");
  addAuthorizedParts(patchSliceParts, authorization.patchSliceParts, policy);
  addAuthorizedParts(generatedParts, authorization.generatedParts, policy);
  const overlap = [...patchSliceParts].filter((name) => generatedParts.has(name)).sort();
  if (overlap.length > 0) {
    throw new PackagePreservationError(
      overlap.map((name) => `ambiguousClassification:${name}`)
    );
  }

  const candidate = buildPackageGraph(candidateEntries, {
    policy,
    patchSliceParts: [...patchSliceParts].filter((name) => candidateEntries.has(name)),
    generatedParts: [...generatedParts].filter((name) => candidateEntries.has(name)),
  });
  const failures: string[] = [];
  if (manifest.classification === "QUARANTINED") failures.push("sourceQuarantined");
  if (candidate.classification === "QUARANTINED") failures.push("candidateQuarantined");

  const originalNames = new Set(manifest.parts.map((part) => part.name));
  const candidateNames = new Set(candidate.parts.map((part) => part.name));
  const addedParts = [...candidateNames].filter((name) => !originalNames.has(name)).sort();
  const removedParts = [...originalNames].filter((name) => !candidateNames.has(name)).sort();
  for (const name of addedParts) {
    if (!generatedParts.has(name)) failures.push(`unexpectedAdded:${name}`);
  }
  for (const name of removedParts) {
    if (!generatedParts.has(name)) failures.push(`unexpectedMissing:${name}`);
  }

  let preservedExactPartCount = 0;
  for (const part of manifest.parts) {
    if (generatedParts.has(part.name)) continue;
    if (patchSliceParts.has(part.name)) {
      if (!candidateNames.has(part.name)) failures.push(`patchSliceMissing:${part.name}`);
      continue;
    }
    const candidateDigest = candidate.partDigests[part.name];
    if (candidateDigest === undefined) continue;
    if (candidateDigest !== part.digest) failures.push(`preserveExactChanged:${part.name}`);
    else preservedExactPartCount += 1;
  }
  if (failures.length > 0) {
    throw new PackagePreservationError([...new Set(failures)].sort());
  }

  const changedParts = [...new Set([...addedParts, ...removedParts, ...manifest.parts
    .filter((part) => candidate.partDigests[part.name] !== part.digest)
    .map((part) => part.name)])].sort();
  return Object.freeze({
    candidate,
    changedParts: Object.freeze(changedParts),
    addedParts: Object.freeze(addedParts),
    removedParts: Object.freeze(removedParts),
    preservedExactPartCount,
    patchSlicePartCount: patchSliceParts.size,
    generatedPartCount: generatedParts.size,
  });
}

function expectedParts(
  value: PackageGraphPrecondition["partDigests"]
): readonly [string, string][] {
  if (!value) return [];
  return value instanceof Map ? [...value] : Object.entries(value);
}

export function assertPackageGraphPrecondition(
  graph: PackageGraph,
  expected: PackageGraphPrecondition
): void {
  const failures: string[] = [];
  if (
    expected.packageDigest !== undefined &&
    expected.packageDigest !== graph.packageDigest
  ) {
    failures.push("packageDigest");
  }
  if (
    expected.relationshipDigest !== undefined &&
    expected.relationshipDigest !== graph.relationshipDigest
  ) {
    failures.push("relationshipDigest");
  }
  if (
    expected.closureDigest !== undefined &&
    expected.closureDigest !== graph.closureDigest
  ) {
    failures.push("closureDigest");
  }
  for (const [name, digest] of expectedParts(expected.partDigests)) {
    if (!Object.hasOwn(graph.partDigests, name)) failures.push(`partMissing:${name}`);
    else if (graph.partDigests[name] !== digest) failures.push(`partDigest:${name}`);
  }
  if (failures.length > 0) throw new PackageGraphPreconditionError(failures);
}
