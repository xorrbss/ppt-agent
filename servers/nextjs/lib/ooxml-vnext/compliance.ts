/**
 * Fail-closed third-party intake policy for the optional GenOffice lab oracle.
 *
 * This module intentionally performs no file-system or module-loading work. The
 * intake scanner supplies a JSON-safe evidence DTO; this pure gate only compares
 * it with the independently recorded 03 round-trip experiment pin.
 */

export type GenOfficeLabCapabilityDto = Readonly<{
  inspect: true;
  bufferedNoopSave: true;
  preservationOracle: true;
  productionCompiler: false;
  textMutation: false;
  streamingSave: false;
  masterCreate: false;
  masterUpdate: false;
  masterDelete: false;
  layoutCreate: false;
  layoutUpdate: false;
  layoutDelete: false;
}>;

export interface SpdxPackageEvidenceDto {
  readonly name: string;
  readonly version: string;
  readonly licenseDeclared: string;
}

export interface GenOfficeComplianceEvidenceDto {
  readonly schemaVersion: 1;
  readonly usage: "LAB_BUFFERED_NOOP_PRESERVATION_ORACLE";
  readonly upstream: Readonly<{
    repository: string;
    commit: string;
    upstreamPinSha256: string;
  }>;
  readonly component: Readonly<{
    name: string;
    version: string;
    path: string;
    license: string;
  }>;
  readonly apacheArtifacts: Readonly<{
    licensePresent: boolean;
    licenseSha256: string;
    noticePresent: boolean;
    noticeSha256: string;
  }>;
  readonly sbom: Readonly<{
    present: boolean;
    sha256: string;
    spdxVersion: string;
    dataLicense: string;
    documentNamespace: string;
    documentDescribes: readonly string[];
    packageCount: number;
    relationshipCount: number;
    packages: readonly SpdxPackageEvidenceDto[];
  }>;
  readonly boundary: Readonly<{
    dynamicImportSpecifier: string;
    runtimeDependencies: readonly string[];
    sourceInventoryFileCount: number;
    sourceInventoryDigestSha256: string;
    materializedPaths: readonly string[];
    complianceChecker: string;
    complianceCheckCount: number;
  }>;
}

export type GenOfficeComplianceViolationCode =
  | "MALFORMED_EVIDENCE"
  | "UNAPPROVED_USAGE"
  | "EXACT_PIN_MISMATCH"
  | "COMPONENT_MISMATCH"
  | "APACHE_ARTIFACT_MISSING"
  | "APACHE_ARTIFACT_DIGEST_MISMATCH"
  | "SBOM_MISSING"
  | "SBOM_MISMATCH"
  | "SOURCE_INVENTORY_MISMATCH"
  | "RUNTIME_DEPENDENCY_MISMATCH"
  | "DYNAMIC_IMPORT_NOT_ALLOWLISTED"
  | "FORBIDDEN_SOURCE_BOUNDARY"
  | "FORBIDDEN_PPTIST_OR_AGPL";

export interface GenOfficeComplianceViolationDto {
  readonly code: GenOfficeComplianceViolationCode;
  readonly field: string;
  readonly message: string;
}

export interface GenOfficeComplianceDecisionDto {
  readonly schemaVersion: 1;
  readonly policyId: "ooxml-vnext/genoffice-lab-oracle/dc4d7e592786";
  readonly accepted: boolean;
  readonly decision: "ALLOW_OPTIONAL_LAB_ORACLE" | "REJECT";
  readonly approvedCommit: string;
  readonly capabilities: GenOfficeLabCapabilityDto;
  readonly violations: readonly GenOfficeComplianceViolationDto[];
}

const EXPECTED_SBOM_PACKAGES = Object.freeze([
  "@genoffice/pptx-engine@0.1.0|Apache-2.0",
  "@nodable/entities@3.0.0|MIT",
  "anynum@1.0.1|MIT",
  "core-util-is@1.0.3|MIT",
  "fast-xml-builder@1.3.0|MIT",
  "fast-xml-parser@5.10.1|MIT",
  "immediate@3.0.6|MIT",
  "inherits@2.0.4|ISC",
  "is-unsafe@2.0.0|MIT",
  "isarray@1.0.0|MIT",
  "jszip@3.10.1|(MIT OR GPL-3.0-or-later)",
  "lie@3.3.0|MIT",
  "pako@1.0.11|(MIT AND Zlib)",
  "path-expression-matcher@1.6.2|MIT",
  "process-nextick-args@2.0.1|MIT",
  "readable-stream@2.3.8|MIT",
  "safe-buffer@5.1.2|MIT",
  "setimmediate@1.0.5|MIT",
  "string_decoder@1.1.1|MIT",
  "strnum@2.4.1|MIT",
  "util-deprecate@1.0.2|MIT",
  "xml-naming@0.3.0|MIT",
].sort());

export const GENOFFICE_LAB_ORACLE_CAPABILITIES: GenOfficeLabCapabilityDto =
  Object.freeze({
    inspect: true,
    bufferedNoopSave: true,
    preservationOracle: true,
    productionCompiler: false,
    textMutation: false,
    streamingSave: false,
    masterCreate: false,
    masterUpdate: false,
    masterDelete: false,
    layoutCreate: false,
    layoutUpdate: false,
    layoutDelete: false,
  });

export const GENOFFICE_LAB_ORACLE_POLICY = Object.freeze({
  policyId: "ooxml-vnext/genoffice-lab-oracle/dc4d7e592786" as const,
  repository: "https://github.com/genspark-ai/genoffice.git",
  commit: "dc4d7e5927864498913b7ba42d0da06cc7cf628e",
  upstreamPinSha256:
    "f51b4e210b36449026bf2c9ac181cca86f89ec30d37c023f43b65433dbf9db69",
  componentName: "@genoffice/pptx-engine",
  componentVersion: "0.1.0",
  componentPath: "packages/pptx-engine",
  componentLicense: "Apache-2.0",
  dynamicImportSpecifier: "@genoffice/pptx-engine",
  licenseSha256:
    "68e32334df324ef4fc79fad3a26487e5abac36055a484bb6c8d7ecf9d8350885",
  noticeSha256:
    "ff1ad79ed52b0f5c1d0e10a9370c22c0c0e6ee83074b6a9b644b25ab7b129f94",
  sbomSha256:
    "b2bb107770d56e2e61d596516b4394390c1f32d6d32edf20a21dabc133e755c2",
  sbomNamespace:
    "https://github.com/genspark-ai/genoffice/tree/dc4d7e5927864498913b7ba42d0da06cc7cf628e/sbom/pptx-engine-dc4d7e5927864498913b7ba42d0da06cc7cf628e",
  sbomDocumentDescribes: "SPDXRef-Package-genoffice-pptx-engine",
  sbomPackageCount: 22,
  sbomRelationshipCount: 25,
  runtimeDependencies: Object.freeze(["fast-xml-parser", "jszip"].sort()),
  sourceInventoryFileCount: 37,
  // sha256(sorted `path\0file-sha256\n` records) for package.json,
  // tsconfig.json, and src/** in the exact pin.
  sourceInventoryDigestSha256:
    "ae9e6d166524982d85901f08f95a91a6426a194902826645bdeac8b3b1e95669",
  complianceChecker: "isolated-pptx-engine-compliance/1.0",
  complianceCheckCount: 17,
});

const FORBIDDEN_PATH_SEGMENTS = new Set([
  "ai",
  "apps",
  "ee",
  "electron",
  "pptist",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringsEqualAsSet(actual: readonly string[], expected: readonly string[]) {
  return (
    JSON.stringify([...actual].sort()) === JSON.stringify([...expected].sort())
  );
}

function pathSegments(path: string): string[] {
  return path
    .replaceAll("\\", "/")
    .split("/")
    .filter(Boolean)
    .map((segment) => segment.toLowerCase());
}

function isAllowedMaterializedPath(path: string): boolean {
  // Evidence paths are canonical repository-relative POSIX paths. Accepting
  // alternative spellings would let a scanner and this gate disagree about
  // which bytes a path names.
  if (
    path.length === 0 ||
    path !== path.replaceAll("\\", "/") ||
    path.startsWith("/") ||
    path.endsWith("/") ||
    path.includes("//") ||
    /(^|\/)\.{1,2}(\/|$)/.test(path) ||
    /[%:\u0000-\u001f\u007f]/.test(path) ||
    !/^[A-Za-z0-9@._+/-]+$/.test(path)
  ) {
    return false;
  }
  const normalized = path;
  if (
    pathSegments(normalized).some(
      (part) =>
        FORBIDDEN_PATH_SEGMENTS.has(part) ||
        /(^|[-_.])pptist([-_.]|$)/i.test(part),
    )
  ) {
    return false;
  }
  return (
    normalized === "LICENSE" ||
    normalized === "NOTICE" ||
    normalized === "LICENSE-UNICODE.txt" ||
    normalized === "package.json" ||
    normalized === "package-lock.json" ||
    normalized === "tsconfig.base.json" ||
    normalized === "vitest.config.ts" ||
    normalized === GENOFFICE_LAB_ORACLE_POLICY.componentPath ||
    normalized.startsWith(`${GENOFFICE_LAB_ORACLE_POLICY.componentPath}/`)
  );
}

function packageKey(value: unknown): string | null {
  if (!isRecord(value)) return null;
  const { name, version, licenseDeclared } = value;
  if (
    typeof name !== "string" ||
    typeof version !== "string" ||
    typeof licenseDeclared !== "string"
  ) {
    return null;
  }
  return `${name}@${version}|${licenseDeclared}`;
}

function violation(
  violations: GenOfficeComplianceViolationDto[],
  code: GenOfficeComplianceViolationCode,
  field: string,
  message: string,
) {
  violations.push(Object.freeze({ code, field, message }));
}

/** Evaluate an untrusted JSON value without throwing on ordinary malformed input. */
export function evaluateGenOfficeCompliance(
  candidate: unknown,
): GenOfficeComplianceDecisionDto {
  const violations: GenOfficeComplianceViolationDto[] = [];

  if (!isRecord(candidate)) {
    violation(
      violations,
      "MALFORMED_EVIDENCE",
      "$",
      "Compliance evidence must be a JSON object.",
    );
    return makeDecision(violations);
  }

  if (candidate.schemaVersion !== 1) {
    violation(
      violations,
      "MALFORMED_EVIDENCE",
      "schemaVersion",
      "Only compliance evidence schema version 1 is accepted.",
    );
  }
  if (candidate.usage !== "LAB_BUFFERED_NOOP_PRESERVATION_ORACLE") {
    violation(
      violations,
      "UNAPPROVED_USAGE",
      "usage",
      "GenOffice is restricted to the buffered no-op preservation lab oracle.",
    );
  }

  const upstream = isRecord(candidate.upstream) ? candidate.upstream : {};
  if (
    upstream.repository !== GENOFFICE_LAB_ORACLE_POLICY.repository ||
    upstream.commit !== GENOFFICE_LAB_ORACLE_POLICY.commit ||
    upstream.upstreamPinSha256 !==
      GENOFFICE_LAB_ORACLE_POLICY.upstreamPinSha256
  ) {
    violation(
      violations,
      "EXACT_PIN_MISMATCH",
      "upstream",
      "Official repository, exact commit, and upstream-pin digest must all match.",
    );
  }

  const component = isRecord(candidate.component) ? candidate.component : {};
  if (
    component.name !== GENOFFICE_LAB_ORACLE_POLICY.componentName ||
    component.version !== GENOFFICE_LAB_ORACLE_POLICY.componentVersion ||
    component.path !== GENOFFICE_LAB_ORACLE_POLICY.componentPath ||
    component.license !== GENOFFICE_LAB_ORACLE_POLICY.componentLicense
  ) {
    violation(
      violations,
      "COMPONENT_MISMATCH",
      "component",
      "Only the exact Apache-2.0 packages/pptx-engine component is accepted.",
    );
  }

  const apache = isRecord(candidate.apacheArtifacts)
    ? candidate.apacheArtifacts
    : {};
  if (apache.licensePresent !== true || apache.noticePresent !== true) {
    violation(
      violations,
      "APACHE_ARTIFACT_MISSING",
      "apacheArtifacts",
      "Exact Apache LICENSE and NOTICE copies are mandatory.",
    );
  }
  if (
    apache.licenseSha256 !== GENOFFICE_LAB_ORACLE_POLICY.licenseSha256 ||
    apache.noticeSha256 !== GENOFFICE_LAB_ORACLE_POLICY.noticeSha256
  ) {
    violation(
      violations,
      "APACHE_ARTIFACT_DIGEST_MISMATCH",
      "apacheArtifacts",
      "Apache LICENSE or NOTICE bytes drifted from the exact pin.",
    );
  }

  const sbom = isRecord(candidate.sbom) ? candidate.sbom : {};
  if (sbom.present !== true) {
    violation(
      violations,
      "SBOM_MISSING",
      "sbom.present",
      "The pinned SPDX SBOM is mandatory.",
    );
  }
  const rawDescribes = Array.isArray(sbom.documentDescribes)
    ? sbom.documentDescribes
    : [];
  const describes = rawDescribes.filter(
    (value): value is string => typeof value === "string",
  );
  const packages = Array.isArray(sbom.packages) ? sbom.packages : [];
  const packageKeys = packages.map(packageKey);
  if (
    sbom.sha256 !== GENOFFICE_LAB_ORACLE_POLICY.sbomSha256 ||
    sbom.spdxVersion !== "SPDX-2.3" ||
    sbom.dataLicense !== "CC0-1.0" ||
    sbom.documentNamespace !== GENOFFICE_LAB_ORACLE_POLICY.sbomNamespace ||
    describes.length !== rawDescribes.length ||
    !stringsEqualAsSet(describes, [
      GENOFFICE_LAB_ORACLE_POLICY.sbomDocumentDescribes,
    ]) ||
    sbom.packageCount !== GENOFFICE_LAB_ORACLE_POLICY.sbomPackageCount ||
    sbom.relationshipCount !==
      GENOFFICE_LAB_ORACLE_POLICY.sbomRelationshipCount ||
    packageKeys.some((entry) => entry === null) ||
    !stringsEqualAsSet(packageKeys as string[], EXPECTED_SBOM_PACKAGES)
  ) {
    violation(
      violations,
      "SBOM_MISMATCH",
      "sbom",
      "SPDX metadata or the exact 22-package/25-relationship closure drifted.",
    );
  }
  for (const entry of packages) {
    if (!isRecord(entry)) continue;
    const name = typeof entry.name === "string" ? entry.name : "";
    const license =
      typeof entry.licenseDeclared === "string"
        ? entry.licenseDeclared
        : "";
    if (/pptist/i.test(name) || /\bAGPL(?:-|\b)/i.test(license)) {
      violation(
        violations,
        "FORBIDDEN_PPTIST_OR_AGPL",
        "sbom.packages",
        "PPTist and AGPL-licensed components are outside the permitted boundary.",
      );
      break;
    }
  }

  const boundary = isRecord(candidate.boundary) ? candidate.boundary : {};
  if (
    boundary.dynamicImportSpecifier !==
    GENOFFICE_LAB_ORACLE_POLICY.dynamicImportSpecifier
  ) {
    violation(
      violations,
      "DYNAMIC_IMPORT_NOT_ALLOWLISTED",
      "boundary.dynamicImportSpecifier",
      "The optional import must use the one exact-pinned engine specifier.",
    );
  }
  const rawRuntimeDependencies = Array.isArray(boundary.runtimeDependencies)
    ? boundary.runtimeDependencies
    : [];
  const runtimeDependencies = rawRuntimeDependencies.filter(
    (value): value is string => typeof value === "string",
  );
  if (
    runtimeDependencies.length !== rawRuntimeDependencies.length ||
    !stringsEqualAsSet(
      runtimeDependencies,
      GENOFFICE_LAB_ORACLE_POLICY.runtimeDependencies,
    )
  ) {
    violation(
      violations,
      "RUNTIME_DEPENDENCY_MISMATCH",
      "boundary.runtimeDependencies",
      "Only fast-xml-parser and jszip are allowed runtime dependencies.",
    );
  }
  if (
    boundary.sourceInventoryFileCount !==
      GENOFFICE_LAB_ORACLE_POLICY.sourceInventoryFileCount ||
    boundary.sourceInventoryDigestSha256 !==
      GENOFFICE_LAB_ORACLE_POLICY.sourceInventoryDigestSha256 ||
    boundary.complianceChecker !==
      GENOFFICE_LAB_ORACLE_POLICY.complianceChecker ||
    boundary.complianceCheckCount !==
      GENOFFICE_LAB_ORACLE_POLICY.complianceCheckCount
  ) {
    violation(
      violations,
      "SOURCE_INVENTORY_MISMATCH",
      "boundary",
      "The 37-file exact-byte inventory and 17-check intake receipt are required.",
    );
  }
  const materializedPaths = Array.isArray(boundary.materializedPaths)
    ? boundary.materializedPaths
    : [];
  const requiredMaterializedPaths = [
    "LICENSE",
    "NOTICE",
    GENOFFICE_LAB_ORACLE_POLICY.componentPath,
    `${GENOFFICE_LAB_ORACLE_POLICY.componentPath}/package.json`,
    `${GENOFFICE_LAB_ORACLE_POLICY.componentPath}/src/index.ts`,
  ];
  if (
    !requiredMaterializedPaths.every((required) =>
      materializedPaths.includes(required),
    ) ||
    new Set(materializedPaths).size !== materializedPaths.length ||
    materializedPaths.some(
      (path) => typeof path !== "string" || !isAllowedMaterializedPath(path),
    )
  ) {
    violation(
      violations,
      "FORBIDDEN_SOURCE_BOUNDARY",
      "boundary.materializedPaths",
      "ee/apps/AI/Electron/PPTist, sibling packages, escapes, and unknown roots are denied.",
    );
  }

  return makeDecision(violations);
}

function makeDecision(
  violations: readonly GenOfficeComplianceViolationDto[],
): GenOfficeComplianceDecisionDto {
  const accepted = violations.length === 0;
  return Object.freeze({
    schemaVersion: 1 as const,
    policyId: GENOFFICE_LAB_ORACLE_POLICY.policyId,
    accepted,
    decision: accepted ? "ALLOW_OPTIONAL_LAB_ORACLE" : "REJECT",
    approvedCommit: GENOFFICE_LAB_ORACLE_POLICY.commit,
    capabilities: GENOFFICE_LAB_ORACLE_CAPABILITIES,
    violations: Object.freeze([...violations]),
  });
}
