import assert from "node:assert/strict";
import test from "node:test";

import { deriveResourceLimitAttestation } from "./public-service-resource-attestation.mjs";

const releaseCandidate = `sha256:${"a".repeat(64)}`;
const sourceDigest = "b3c77d6650929c667ff38e06c9d674db596bf915";
const provenance = { runnerPath: "scripts/public-service-resource-attestation.mjs",
  runnerSha256: `sha256:${"b".repeat(64)}`, sourceManifestDigest: releaseCandidate };
const limits = { memory: 1073741824, cpu: 600000, pids: 64, openFiles: 512,
  outputBytes: 536870912, wallClock: 600 };
const methods = { memory: "windows-job-object", cpu: "windows-job-object", pids: "windows-job-object",
  openFiles: "high-frequency-supervisor+job-termination",
  outputBytes: "worker-precommit-size+parent-postread/job-termination",
  wallClock: "monotonic-supervisor+job-termination" };
const residualRisks = { openFiles: "A transient handle burst can occur between 50 ms supervisor samples; the next sample terminates the complete Job Object." };
const handleLimitCleanupTestBinding = {
  testPath: "servers/fastapi/tests/unit/test_isolated_public_workers.py",
  testName: "test_export_subprocess_limit_kills_descendants_and_reports_recycle",
  testSourceSha256: `sha256:${"d".repeat(64)}`, executionRequired: true,
};
function sample(role, overrides = {}) {
  return { role, enforcementLayer: "windows-job-object", resourceLimitsEnforced: true,
    resourceLimits: limits, peakPrivateBytes: 1, peakHandleCount: 1, peakProcessCount: 1,
    peakCpuMillis: 1, recycledWithinBaseline: true, finalProcessCount: 0,
    controlEnforcementMethods: methods, residualRisks, supervisorPollIntervalMillis: 50, ...overrides };
}
function input(samples, extra = {}) {
  return { path: "artifacts/controlled/report.json", sha256: `sha256:${"c".repeat(64)}`,
    report: { schemaVersion: 2, kind: "authored-vnext-controlled-load",
      results: [{ exportOperation: { result: { worker_resource_samples: samples } } }], ...extra } };
}
function build(reports) {
  return deriveResourceLimitAttestation({ releaseCandidate, sourceDigest, provenance,
    handleLimitCleanupTestBinding, reports,
    expectedLayer: "windows-job-object" });
}

test("resource attestation derives only from complete durable worker samples", () => {
  const paired = input([sample("render"), sample("export")]);
  paired.report.results[0].exportOperations = ["pptx", "pdf"].map((format) => ({ format,
    exportOperation: { result: { worker_resource_samples: [sample("render"), sample("export")] } } }));
  delete paired.report.results[0].exportOperation;
  const attestation = build([paired]);
  assert.equal(attestation.enforcementLayer, "windows-job-object");
  assert.deepEqual(attestation.workerRoles, ["export", "render"]);
  assert.equal(attestation.actualSamples.length, 4);
  assert.equal(attestation.inputReports[0].operationCount, 2);
  assert.deepEqual(attestation.controlEnforcementMethods, methods);
  assert.deepEqual(attestation.residualRisks, residualRisks);
  assert.deepEqual(attestation.handleLimitCleanupTestBinding, handleLimitCleanupTestBinding);

  assert.throws(() => build([{ ...input([]), report: { schemaVersion: 2,
    kind: "authored-vnext-controlled-load", results: [], valid: true, enforced: true,
    resourceLimitsEnforced: true, actualSamples: [sample("render"), sample("export")] } }]),
  /actual controlled product report/);
  assert.throws(() => build([input([sample("render")])]), /render and export/);
  assert.throws(() => build([input([sample("render"), sample("export", { finalProcessCount: 1 })])]),
    /recycle to zero/);
});

test("resource attestation rejects method, residual-risk, and test-binding self assertions", () => {
  assert.throws(() => build([input([
    sample("render", { controlEnforcementMethods: {} }), sample("export"),
  ])]), /control enforcement methods/);
  assert.throws(() => build([input([
    sample("render", { residualRisks: {} }), sample("export"),
  ])]), /residual risks/);
  assert.throws(() => deriveResourceLimitAttestation({ releaseCandidate, sourceDigest, provenance,
    reports: [input([sample("render"), sample("export")])], expectedLayer: "windows-job-object" }),
  /cleanup test contract/);
});
