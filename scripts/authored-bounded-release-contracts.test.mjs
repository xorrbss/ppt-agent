import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
  assertSourceTreeCensusUnchanged,
  canonicalJson,
  canonicalSha256Excluding,
  decodeDigestCarrier,
  decodeUuidCarrier,
  encodeDigestCarrier,
  encodeUuidCarrier,
  projectExcluding,
  validateCountContract,
  validateGate1Artifacts,
  validateGate1Ledger,
  validatePolicyBinding,
  captureSourceTreeCensus,
} from "./authored-bounded-release-contracts.mjs";

test("canonical v2 rejects fractional and negative-zero JSON numbers", () => {
  assert.throws(() => canonicalJson({ value: 1.25 }), /canonical-number-fractional/);
  assert.throws(() => canonicalJson({ value: -0 }), /canonical-number-negative-zero/);
  assert.equal(canonicalJson({ "é": "a\r\nb", a: "a\rb" }), '{"a":"a\\nb","é":"a\\nb"}');
});

test("digest projection excludes only the exact self field", () => {
  const value = { manifestDigest: "sha256:" + "0".repeat(64), payload: { manifestDigest: "retained", value: 1 } };
  const projected = projectExcluding(value, ["$.manifestDigest"]);
  assert.deepEqual(projected, { payload: { manifestDigest: "retained", value: 1 } });
  assert.match(canonicalSha256Excluding(value, ["$.manifestDigest"]), /^sha256:[0-9a-f]{64}$/);
});

test("digest and UUID carriers are uppercase-safe and reject raw/lowercase carriers", () => {
  const digest = "sha256:" + "ab".repeat(32);
  const uuid = "01234567-89ab-cdef-0123-456789abcdef";
  const encodedDigest = encodeDigestCarrier(digest);
  const encodedUuid = encodeUuidCarrier(uuid);
  assert.equal(encodedDigest, `D2_${"AB".repeat(32)}`);
  assert.equal(decodeDigestCarrier(encodedDigest), digest);
  assert.equal(decodeUuidCarrier(encodedUuid), uuid);
  assert.throws(() => decodeDigestCarrier(digest), /carrier-uppercase-required/);
  assert.throws(() => decodeDigestCarrier(encodedDigest.toLowerCase()), /carrier-uppercase-required/);
});

test("manifest/result count contracts and Gate 1 ledger are exact", () => {
  const make = (count, key) => ({ [key]: Array.from({ length: count }, (_, index) => ({ ordinal: index + 1, [key === "phases" ? "phaseId" : "caseId"]: `id-${index}` })) });
  assert.equal(validateCountContract("gate-minus1", make(18, "cases")), 18);
  assert.equal(validateCountContract("gate0", make(34, "cases")), 34);
  assert.equal(validateCountContract("gate1", make(37, "phases")), 37);
  const ledger = [];
  for (let slide = 1; slide <= 8; slide += 1) for (const exportMode of ["fidelity", "hybrid"]) for (const comPhase of ["first-open", "reopen"]) for (const defectFieldId of ["repair-dialog", "qid-missing", "qid-duplicate-or-unexpected", "clipping", "overlap", "ghosting", "native-raster-collision", "line-break-drift", "font-substitution", "chart-data-or-style-mismatch", "crop-or-sentinel-mismatch", "css-paint-or-isolation-drift"]) ledger.push({ slideNumber: slide, exportMode, comPhase, defectFieldId });
  assert.equal(validateGate1Ledger(ledger), 384);
  assert.throws(() => validateGate1Ledger(ledger.slice(1)), /ledger-cardinality/);
});

test("Gate 1 immutable artifact inventory is exactly 70 and policy is self-bound", () => {
  const requiredArtifactIds = Array.from({ length: 70 }, (_, index) => `artifact-${index}`);
  const manifest = { requiredArtifactIds };
  const inventory = requiredArtifactIds.map((artifactId, index) => ({ artifactId, versionId: `v-${index}`, sha256: `sha256:${String(index).padStart(64, "0")}`, byteLength: 1, mediaType: "application/octet-stream" }));
  assert.equal(validateGate1Artifacts(manifest, { artifactInventory: inventory }), 70);
  assert.throws(() => validateGate1Artifacts(manifest, { artifactInventory: inventory.slice(1) }), /artifact-result-cardinality/);
  const policy = { digestProjection: { excludedJsonPaths: ["$.policyDigest"] }, policyId: "test", policyDigest: "" };
  policy.policyDigest = canonicalSha256Excluding(policy, ["$.policyDigest"]);
  assert.equal(validatePolicyBinding({ evidencePolicyDigest: policy.policyDigest }, policy), true);
  assert.throws(() => validatePolicyBinding({ evidencePolicyDigest: policy.policyDigest }, { ...policy, policyDigest: `sha256:${"f".repeat(64)}` }), /policy-digest-mismatch/);
});

test("Gate 1 server readback authority and sealed digest cascade are exact", () => {
  const contractRoot = path.resolve("docs/authored-bounded-hybrid-release-v3-contracts");
  const manifest = JSON.parse(fs.readFileSync(path.join(contractRoot, "gate1-phase-manifest.v1.json"), "utf8"));
  const manifestSchema = JSON.parse(fs.readFileSync(path.join(contractRoot, "gate1-phase-manifest.v1.schema.json"), "utf8"));
  const validator = JSON.parse(fs.readFileSync(path.join(contractRoot, "gate1-semantic-validator-contract.v1.json"), "utf8"));
  const resultSchema = JSON.parse(fs.readFileSync(path.join(contractRoot, "gate1-result-set.v1.schema.json"), "utf8"));
  assert.deepEqual(manifest.serverReadbackAuthority, {
    signerKeyId: "authored-release-readback-v3-20260823",
    publicKeyDigest: "sha256:4e757e6f367fda82dc57b42d952d6d7bd5a75de53b4cff806f7434eb54e75f87",
    keyPurpose: "fastapi-release-readback",
    maxTtlSeconds: 300,
    executionNonceRequired: true,
  });
  assert.deepEqual(manifest.runtimePinsRequiredBeforeExecution, manifestSchema.properties.runtimePinsRequiredBeforeExecution.const);
  assert.ok(manifest.runtimePinsRequiredBeforeExecution.includes("executionNonce"));
  assert.equal(validator.contractDigest, canonicalSha256Excluding(validator, ["$.contractDigest"]));
  assert.equal(manifest.manifestDigest, canonicalSha256Excluding(manifest, ["$.manifestDigest"]));
  assert.equal(manifest.resultEnvelopeContract.semanticValidatorContractDigest, validator.contractDigest);
  assert.equal(resultSchema.properties.semanticValidatorContractDigest.const, validator.contractDigest);
});

test("source census binding is fail-closed", () => {
  const opening = { head: "a", censusDigest: "sha256:" + "1".repeat(64) };
  assertSourceTreeCensusUnchanged(opening, { ...opening });
  assert.throws(() => assertSourceTreeCensusUnchanged(opening, { ...opening, head: "b" }), /source-census-changed/);
});

test("SourceTreeCensusV1 separates index, tracked, untracked and reviewed ignored paths", () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "authored-census-"));
  try {
    const git = (...args) => execFileSync("git", ["-C", root, ...args], { stdio: "ignore" });
    git("init", "-q");
    git("config", "user.email", "test@example.invalid");
    git("config", "user.name", "test");
    fs.writeFileSync(path.join(root, "tracked.txt"), "base\n");
    fs.writeFileSync(path.join(root, ".gitignore"), "cache/\nignoredpkg/\n");
    fs.mkdirSync(path.join(root, "tracked", "__pycache__"), { recursive: true });
    fs.writeFileSync(path.join(root, "tracked", "__pycache__", "tracked.pyc"), "tracked runtime\n");
    fs.mkdirSync(path.join(root, "cache"));
    fs.writeFileSync(path.join(root, "cache", "tracked.txt"), "tracked cache\n");
    git("add", ".");
    git("add", "-f", "cache/tracked.txt");
    git("commit", "-qm", "base");
    fs.writeFileSync(path.join(root, "tracked.txt"), "modified\n");
    fs.writeFileSync(path.join(root, "tracked", "__pycache__", "tracked.pyc"), "tracked runtime changed\n");
    fs.writeFileSync(path.join(root, "cache", "tracked.txt"), "tracked cache changed\n");
    fs.writeFileSync(path.join(root, "staged.txt"), "staged\n");
    git("add", "staged.txt");
    fs.writeFileSync(path.join(root, "untracked.txt"), "untracked\n");
    fs.writeFileSync(path.join(root, "cache", "generated.bin"), "cache\n");
    fs.mkdirSync(path.join(root, "ignoredpkg", "nested", "__pycache__"), { recursive: true });
    fs.writeFileSync(path.join(root, "ignoredpkg", "nested", "__pycache__", "compiled.pyc"), "ignored runtime\n");
    fs.mkdirSync(path.join(root, "untracked", "__pycache__"), { recursive: true });
    fs.writeFileSync(path.join(root, "untracked", "__pycache__", "source.py"), "untracked source\n");
    assert.throws(() => captureSourceTreeCensus(root), /ignored-path-not-allowlisted/);
    const censusOptions = { generatedCacheAllowlistPrefixes: ["cache/"], generatedIgnoredDirectoryNames: ["__pycache__"] };
    const opening = captureSourceTreeCensus(root, censusOptions);
    fs.writeFileSync(path.join(root, "cache", "generated.bin"), "cache bytes changed\n");
    const closing = captureSourceTreeCensus(root, censusOptions);
    assert.equal(opening.schemaVersion, "SourceTreeCensusV1");
    assert.equal(opening.indexTreeDigest.startsWith("sha256:"), true);
    assert.equal(opening.stagedPathDigests.some((entry) => entry.path === "staged.txt"), true);
    assert.equal(opening.trackedModifiedPathDigests.some((entry) => entry.path === "tracked.txt"), true);
    assert.equal(opening.untrackedPathDigests.some((entry) => entry.path === "untracked.txt"), true);
    assert.equal(opening.ignoredAllowlistPathDigests.some((entry) => entry.path === "cache/generated.bin"), false);
    assert.deepEqual(opening.generatedCacheObservedPathSet, ["cache/generated.bin", "ignoredpkg/nested/__pycache__/compiled.pyc"]);
    assert.equal(opening.generatedIgnoredDirectoryNames.includes("__pycache__"), true);
    assert.equal(opening.untrackedPathDigests.some((entry) => entry.path === "untracked/__pycache__/source.py"), true);
    assert.equal(opening.trackedModifiedPathDigests.some((entry) => entry.path === "tracked/__pycache__/tracked.pyc"), true);
    assert.equal(opening.trackedModifiedPathDigests.some((entry) => entry.path === "cache/tracked.txt"), true);
    assert.equal(opening.generatedCacheObservedPathSetDigest, closing.generatedCacheObservedPathSetDigest);
    assertSourceTreeCensusUnchanged(opening, closing);
    fs.writeFileSync(path.join(root, "cache", "second.bin"), "new cache path\n");
    assert.throws(() => assertSourceTreeCensusUnchanged(opening, captureSourceTreeCensus(root, censusOptions)), /source-census-changed/);
    fs.writeFileSync(path.join(root, "tracked.txt"), "tracked changed\n");
    assert.throws(() => assertSourceTreeCensusUnchanged(opening, captureSourceTreeCensus(root, censusOptions)), /source-census-changed/);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
