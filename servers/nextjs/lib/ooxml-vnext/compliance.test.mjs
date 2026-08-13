import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import { build } from "esbuild";

const here = path.dirname(fileURLToPath(import.meta.url));

async function importBoundary(t) {
  const temporaryDirectory = await mkdtemp(
    path.join(os.tmpdir(), "ooxml-vnext-compliance-"),
  );
  t.after(() => rm(temporaryDirectory, { force: true, recursive: true }));
  const stubPackageDirectory = path.join(
    temporaryDirectory,
    "node_modules",
    "@genoffice",
    "pptx-engine",
  );
  await mkdir(stubPackageDirectory, { recursive: true });
  await writeFile(
    path.join(stubPackageDirectory, "package.json"),
    JSON.stringify({
      name: "@genoffice/pptx-engine",
      version: "0.1.0",
      private: true,
      type: "module",
      exports: "./index.js",
    }),
  );
  await writeFile(
    path.join(stubPackageDirectory, "index.js"),
    [
      "export async function openPptx(input) {",
      "  return { mutableInternalDeck: Uint8Array.from(input) };",
      "}",
      "export async function savePptx(opened) {",
      "  return Uint8Array.from(opened.mutableInternalDeck);",
      "}",
      "export function replaceAllInDeck() {",
      "  throw new Error('mutation must remain unreachable');",
      "}",
      "export function savePptxToFile() {",
      "  throw new Error('streaming must remain unreachable');",
      "}",
    ].join("\n"),
  );
  const outfile = path.join(temporaryDirectory, "boundary.mjs");
  await build({
    absWorkingDir: here,
    bundle: true,
    external: ["@genoffice/pptx-engine"],
    format: "esm",
    outfile,
    platform: "node",
    stdin: {
      contents: [
        'export * from "./compliance";',
        'export * from "./genoffice-lab-oracle";',
      ].join("\n"),
      loader: "ts",
      resolveDir: here,
      sourcefile: "compliance-boundary.ts",
    },
  });
  return import(pathToFileURL(outfile).href);
}

function exactEvidence(policy) {
  return {
    schemaVersion: 1,
    usage: "LAB_BUFFERED_NOOP_PRESERVATION_ORACLE",
    upstream: {
      repository: policy.repository,
      commit: policy.commit,
      upstreamPinSha256: policy.upstreamPinSha256,
    },
    component: {
      name: policy.componentName,
      version: policy.componentVersion,
      path: policy.componentPath,
      license: policy.componentLicense,
    },
    apacheArtifacts: {
      licensePresent: true,
      licenseSha256: policy.licenseSha256,
      noticePresent: true,
      noticeSha256: policy.noticeSha256,
    },
    sbom: {
      present: true,
      sha256: policy.sbomSha256,
      spdxVersion: "SPDX-2.3",
      dataLicense: "CC0-1.0",
      documentNamespace: policy.sbomNamespace,
      documentDescribes: [policy.sbomDocumentDescribes],
      packageCount: policy.sbomPackageCount,
      relationshipCount: policy.sbomRelationshipCount,
      packages: [
        ["@genoffice/pptx-engine", "0.1.0", "Apache-2.0"],
        ["@nodable/entities", "3.0.0", "MIT"],
        ["anynum", "1.0.1", "MIT"],
        ["core-util-is", "1.0.3", "MIT"],
        ["fast-xml-builder", "1.3.0", "MIT"],
        ["fast-xml-parser", "5.10.1", "MIT"],
        ["immediate", "3.0.6", "MIT"],
        ["inherits", "2.0.4", "ISC"],
        ["is-unsafe", "2.0.0", "MIT"],
        ["isarray", "1.0.0", "MIT"],
        ["jszip", "3.10.1", "(MIT OR GPL-3.0-or-later)"],
        ["lie", "3.3.0", "MIT"],
        ["pako", "1.0.11", "(MIT AND Zlib)"],
        ["path-expression-matcher", "1.6.2", "MIT"],
        ["process-nextick-args", "2.0.1", "MIT"],
        ["readable-stream", "2.3.8", "MIT"],
        ["safe-buffer", "5.1.2", "MIT"],
        ["setimmediate", "1.0.5", "MIT"],
        ["string_decoder", "1.1.1", "MIT"],
        ["strnum", "2.4.1", "MIT"],
        ["util-deprecate", "1.0.2", "MIT"],
        ["xml-naming", "0.3.0", "MIT"],
      ].map(([name, version, licenseDeclared]) => ({
        name,
        version,
        licenseDeclared,
      })),
    },
    boundary: {
      dynamicImportSpecifier: policy.dynamicImportSpecifier,
      runtimeDependencies: [...policy.runtimeDependencies],
      sourceInventoryFileCount: policy.sourceInventoryFileCount,
      sourceInventoryDigestSha256: policy.sourceInventoryDigestSha256,
      materializedPaths: [
        "LICENSE",
        "NOTICE",
        "packages/pptx-engine",
        "packages/pptx-engine/package.json",
        "packages/pptx-engine/src/index.ts",
      ],
      complianceChecker: policy.complianceChecker,
      complianceCheckCount: policy.complianceCheckCount,
    },
  };
}

function codes(decision) {
  return new Set(decision.violations.map(({ code }) => code));
}

test("exact Apache pin and SPDX closure allow only the optional lab oracle", async (t) => {
  const boundary = await importBoundary(t);
  const evidence = exactEvidence(boundary.GENOFFICE_LAB_ORACLE_POLICY);
  const decision = boundary.evaluateGenOfficeCompliance(evidence);

  assert.equal(decision.accepted, true);
  assert.equal(decision.decision, "ALLOW_OPTIONAL_LAB_ORACLE");
  assert.deepEqual(decision.violations, []);
  assert.equal(Object.isFrozen(decision), true);
  assert.equal(Object.isFrozen(decision.capabilities), true);
  assert.equal(decision.capabilities.bufferedNoopSave, true);
  assert.equal(decision.capabilities.preservationOracle, true);
  for (const forbiddenCapability of [
    "productionCompiler",
    "textMutation",
    "streamingSave",
    "masterCreate",
    "masterUpdate",
    "masterDelete",
    "layoutCreate",
    "layoutUpdate",
    "layoutDelete",
  ]) {
    assert.equal(decision.capabilities[forbiddenCapability], false);
  }
  assert.doesNotThrow(() => JSON.stringify(decision));
});

test("pin, NOTICE, SBOM, dependency, and inventory drift fail closed", async (t) => {
  const boundary = await importBoundary(t);
  const evidence = exactEvidence(boundary.GENOFFICE_LAB_ORACLE_POLICY);
  evidence.upstream.commit = "0000000000000000000000000000000000000000";
  evidence.apacheArtifacts.noticeSha256 = "0".repeat(64);
  evidence.sbom.relationshipCount = 24;
  evidence.boundary.runtimeDependencies.push("@genoffice/ai");
  evidence.boundary.sourceInventoryFileCount = 36;

  const decision = boundary.evaluateGenOfficeCompliance(evidence);
  const actualCodes = codes(decision);
  assert.equal(decision.accepted, false);
  assert.equal(decision.decision, "REJECT");
  for (const expected of [
    "EXACT_PIN_MISMATCH",
    "APACHE_ARTIFACT_DIGEST_MISMATCH",
    "SBOM_MISMATCH",
    "RUNTIME_DEPENDENCY_MISMATCH",
    "SOURCE_INVENTORY_MISMATCH",
  ]) {
    assert.equal(actualCodes.has(expected), true, expected);
  }
});

test("ee/apps/AI/Electron and sibling package paths are denied", async (t) => {
  const boundary = await importBoundary(t);
  for (const forbiddenPath of [
    "ee/server/index.ts",
    "apps/slides/index.ts",
    "packages/pptx-engine/AI/agent.ts",
    "packages/pptx-engine/Electron/main.ts",
    "packages/pptx-engine/src/pptist.ts",
    "packages/other/index.ts",
    "../packages/pptx-engine/src/index.ts",
    "packages/pptx-engine/src//index.ts",
    "packages\\pptx-engine\\src\\index.ts",
    "packages/pptx-engine/src/%2e%2e/ee.ts",
  ]) {
    const evidence = exactEvidence(boundary.GENOFFICE_LAB_ORACLE_POLICY);
    evidence.boundary.materializedPaths.push(forbiddenPath);
    const decision = boundary.evaluateGenOfficeCompliance(evidence);
    assert.equal(decision.accepted, false, forbiddenPath);
    assert.equal(codes(decision).has("FORBIDDEN_SOURCE_BOUNDARY"), true);
  }
});

test("non-canonical or duplicate materialized-path evidence is denied", async (t) => {
  const boundary = await importBoundary(t);
  for (const extraPath of [
    "packages/pptx-engine/src/./index.ts",
    "packages/pptx-engine/src/index.ts/",
    "C:/packages/pptx-engine/src/index.ts",
  ]) {
    const evidence = exactEvidence(boundary.GENOFFICE_LAB_ORACLE_POLICY);
    evidence.boundary.materializedPaths.push(extraPath);
    const decision = boundary.evaluateGenOfficeCompliance(evidence);
    assert.equal(decision.accepted, false, extraPath);
    assert.equal(codes(decision).has("FORBIDDEN_SOURCE_BOUNDARY"), true);
  }

  const duplicate = exactEvidence(boundary.GENOFFICE_LAB_ORACLE_POLICY);
  duplicate.boundary.materializedPaths.push("NOTICE");
  const decision = boundary.evaluateGenOfficeCompliance(duplicate);
  assert.equal(decision.accepted, false);
  assert.equal(codes(decision).has("FORBIDDEN_SOURCE_BOUNDARY"), true);
});

test("PPTist and AGPL evidence is explicitly blocked", async (t) => {
  const boundary = await importBoundary(t);
  const evidence = exactEvidence(boundary.GENOFFICE_LAB_ORACLE_POLICY);
  evidence.sbom.packages[0] = {
    name: "PPTist",
    version: "1.0.0",
    licenseDeclared: "AGPL-3.0-only",
  };

  const decision = boundary.evaluateGenOfficeCompliance(evidence);
  assert.equal(decision.accepted, false);
  assert.equal(codes(decision).has("FORBIDDEN_PPTIST_OR_AGPL"), true);
  assert.equal(codes(decision).has("SBOM_MISMATCH"), true);
});

test("unallowlisted dynamic imports reject before module loading", async (t) => {
  const boundary = await importBoundary(t);
  const evidence = exactEvidence(boundary.GENOFFICE_LAB_ORACLE_POLICY);
  evidence.boundary.dynamicImportSpecifier =
    "data:text/javascript,throw new Error('must-not-load')";

  const decision = boundary.evaluateGenOfficeCompliance(evidence);
  assert.equal(decision.accepted, false);
  assert.equal(codes(decision).has("DYNAMIC_IMPORT_NOT_ALLOWLISTED"), true);
  await assert.rejects(
    boundary.loadGenOfficeBufferedNoopLabOracle(evidence),
    (error) => {
      assert.equal(error.code, "COMPLIANCE_REJECTED");
      assert.equal(error.decision.accepted, false);
      return true;
    },
  );
});

test("approved oracle executes only buffered open-save and returns detached bytes plus DTO", async (t) => {
  const boundary = await importBoundary(t);
  const evidence = exactEvidence(boundary.GENOFFICE_LAB_ORACLE_POLICY);
  const oracle = await boundary.loadGenOfficeBufferedNoopLabOracle(evidence);

  assert.deepEqual(Object.keys(oracle).sort(), [
    "capabilities",
    "compliance",
    "runBufferedNoop",
  ]);
  const input = Uint8Array.from([1, 2, 3, 4]);
  const result = await oracle.runBufferedNoop(input);
  assert.deepEqual([...result.outputBytes], [...input]);
  assert.notEqual(result.outputBytes, input);
  assert.deepEqual(result.receipt, {
    schemaVersion: 1,
    operation: "BUFFERED_NOOP_OPEN_SAVE",
    engineCommit: boundary.GENOFFICE_LAB_ORACLE_POLICY.commit,
    inputSha256:
      "9f64a747e1b97f131fabb6b447296c9b6f0201e79fb3c5356e6c77e89b6a806a",
    inputByteLength: 4,
    outputSha256:
      "9f64a747e1b97f131fabb6b447296c9b6f0201e79fb3c5356e6c77e89b6a806a",
    outputByteLength: 4,
    streamingUsed: false,
    mutationRequested: false,
  });
  assert.equal(Object.isFrozen(result.receipt), true);
  assert.equal("mutableInternalDeck" in result, false);
  assert.equal("engine" in oracle, false);
  assert.equal("savePptxToFile" in oracle, false);
  assert.equal("replaceAllInDeck" in oracle, false);
});

test("the public oracle surface has no mutation, streaming, CRUD, or engine escape hatch", async (t) => {
  const boundary = await importBoundary(t);
  assert.deepEqual(
    Object.keys(boundary).filter((name) =>
      /replace|mutat|stream|master|layout|opened|archive|engineSession/i.test(
        name,
      ),
    ),
    [],
  );
  assert.equal(
    typeof boundary.loadGenOfficeBufferedNoopLabOracle,
    "function",
  );
  assert.equal(typeof boundary.evaluateGenOfficeCompliance, "function");
});

test("ordinary malformed DTO input is rejected deterministically", async (t) => {
  const boundary = await importBoundary(t);
  const first = boundary.evaluateGenOfficeCompliance(null);
  const second = boundary.evaluateGenOfficeCompliance(null);
  assert.deepEqual(first, second);
  assert.equal(first.accepted, false);
  assert.deepEqual([...codes(first)], ["MALFORMED_EVIDENCE"]);
});
