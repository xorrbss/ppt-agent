#!/usr/bin/env node

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { computeBundleDigest, sealImmutableBundle, validateImmutableBundle } from "./public-service-release-evidence-bundle.mjs";

const sha = (value) => `sha256:${String(value).repeat(64).slice(0, 64)}`;
const commit = "1".repeat(40);
const releaseCandidateDigest = sha("2");
const runId = "run-same-rc-001";
const operationId = "operation-001";
const binding = () => ({ runId, operationId, sourceCommit: commit, releaseCandidateDigest });
const envelope = (value) => ({ evidenceClass: "actual_product_path", evidenceDigest: sha(value), binding: binding() });
const usage = (retryCalls = 0) => ({ initialCalls: 1, retryCalls, totalCalls: 1 + retryCalls,
  estimatedTokens: 100, actualTokens: 90, estimatedCostMicrounits: 20, actualCostMicrounits: 18,
  actualCostState: "observed", unavailableReason: null });

function passingDraft() {
  const phases = ["api", "queue", "worker", "provider", "render", "publication", "export", "download", "readback"]
    .map((name, index) => ({ ...envelope(String(index + 3)), name, status: "succeeded",
      startedAt: `2026-08-30T00:00:${String(index * 2).padStart(2, "0")}.000Z`,
      finishedAt: `2026-08-30T00:00:${String(index * 2 + 1).padStart(2, "0")}.000Z`,
      durationMs: 1_000, attemptCount: 1, providerUsage: usage(name === "provider" ? 1 : 0) }));
  const artifactSha = sha("d");
  return {
    schemaVersion: "public-service-release-evidence-bundle/v1",
    evidenceClass: "actual_product_path",
    executionMode: "same_rc_product_path",
    bundleId: "bundle-001",
    releaseCandidate: { sourceCommit: commit, releaseCandidateDigest,
      components: { api: sha("a"), worker: sha("b"), provider: sha("c"), renderer: sha("d"),
        exporter: sha("e"), fonts: sha("f") } },
    provenance: { runnerPath: "scripts/public-service-release-evidence-bundle.mjs",
      runnerSha256: sha("1"), sourceManifestDigest: releaseCandidateDigest },
    operationEvidence: { ...envelope("0"), apiOperationId: "api-001", apiDigest: sha("a"),
      workerOperationId: "worker-001", workerDigest: sha("b"), providerOperationId: "provider-001",
      providerDigest: sha("c"), renderOperationId: "render-001", renderDigest: sha("d"),
      exportOperationId: "export-001", exportDigest: sha("e") },
    run: { id: runId, operationId, startedAt: "2026-08-30T00:00:00.000Z",
      finishedAt: "2026-08-30T00:00:18.000Z", outcome: "succeeded", releaseReadiness: "ready",
      deploymentAuthorized: false },
    phases,
    terminalEvidence: {
      database: { ...envelope("e"), operationState: "succeeded", exportState: "succeeded",
        artifactState: "published", observedAt: "2026-08-30T00:00:18.000Z", stateDigest: sha("1"), recordDigest: sha("2") },
      artifact: { ...envelope("f"), state: "available", artifactId: "artifact-001", sha256: artifactSha,
        bytes: 4096, mediaType: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        unavailableReason: null },
      downloads: {
        redirect303: { ...envelope("3"), state: "observed", httpStatus: 303, locationDigest: sha("4"),
          contentRange: null, responseSha256: null, artifactSha256: artifactSha, unavailableReason: null },
        range206: { ...envelope("5"), state: "observed", httpStatus: 206, locationDigest: null,
          contentRange: "bytes 0-1023/4096", responseSha256: sha("6"), artifactSha256: artifactSha,
          unavailableReason: null },
      },
      readback: {
        powerpoint: { ...envelope("7"), state: "passed", artifactSha256: artifactSha,
          toolIdentityDigest: sha("8"), readbackDigest: sha("9"), slideCount: 5, unavailableReason: null },
        libreoffice: { ...envelope("a"), state: "passed", artifactSha256: artifactSha,
          toolIdentityDigest: sha("b"), readbackDigest: sha("c"), slideCount: 5, unavailableReason: null },
      },
    },
    failure: null,
  };
}

test("seals and validates one same-RC actual product-path bundle", async () => {
  const schema = JSON.parse(await readFile(new URL("../contracts/public-service-release-evidence/release-evidence-bundle.schema.json", import.meta.url), "utf8"));
  assert.equal(schema.properties.schemaVersion.const, "public-service-release-evidence-bundle/v1");
  const bundle = sealImmutableBundle(passingDraft(), { sealedAt: "2026-08-30T00:00:19.000Z" });
  const result = validateImmutableBundle(bundle);
  assert.equal(result.pass, true, JSON.stringify(result.checks));
  assert.equal(bundle.integrity.bundleDigest, computeBundleDigest(bundle));
  assert.equal(bundle.bundleDigest, bundle.integrity.bundleDigest);
  assert.deepEqual(bundle.dbTerminalState, bundle.terminalEvidence.database);
  assert.equal(bundle.artifactDigest, bundle.terminalEvidence.artifact.sha256);
  assert.deepEqual(bundle.download303, bundle.terminalEvidence.downloads.redirect303);
  assert.deepEqual(bundle.download206, bundle.terminalEvidence.downloads.range206);
  assert.equal(bundle.run.deploymentAuthorized, false);
});

test("fixture/plan and cross-run evidence cannot pass", () => {
  const fixture = sealImmutableBundle({ ...passingDraft(), evidenceClass: "fixture" },
    { sealedAt: "2026-08-30T00:00:19.000Z" });
  assert.equal(validateImmutableBundle(fixture).pass, false);
  assert.equal(validateImmutableBundle(fixture).checks.find((item) => item.id === "actual-product-path").pass, false);

  const crossRun = passingDraft();
  crossRun.terminalEvidence.downloads.range206.binding.runId = "other-run";
  const crossRunBundle = sealImmutableBundle(crossRun, { sealedAt: "2026-08-30T00:00:19.000Z" });
  assert.equal(validateImmutableBundle(crossRunBundle).checks.find((item) => item.id === "same-run-same-rc-binding").pass, false);

  const unboundProducer = passingDraft();
  unboundProducer.provenance.sourceManifestDigest = sha("f");
  const unboundProducerBundle = sealImmutableBundle(unboundProducer, { sealedAt: "2026-08-30T00:00:19.000Z" });
  assert.equal(validateImmutableBundle(unboundProducerBundle).checks.find((item) => item.id === "schema-and-source-identity").pass, false);
});

test("failed runs preserve phase timing, retries, and provider usage without becoming release-ready", () => {
  const draft = passingDraft();
  draft.run.outcome = "failed";
  draft.run.releaseReadiness = "not_ready";
  draft.failure = { code: "provider_timeout", messageDigest: sha("0"), phase: "provider" };
  draft.phases = draft.phases.slice(0, 4);
  draft.phases[3].status = "failed";
  draft.phases[3].attemptCount = 3;
  draft.phases[3].providerUsage = usage(2);
  draft.terminalEvidence.database.operationState = "failed";
  draft.terminalEvidence.database.exportState = "not_created";
  draft.terminalEvidence.database.artifactState = "not_published";
  draft.terminalEvidence.artifact = { ...envelope("f"), state: "unavailable", artifactId: null, sha256: null,
    bytes: null, mediaType: null, unavailableReason: "run_failed_before_publication" };
  for (const key of ["redirect303", "range206"]) draft.terminalEvidence.downloads[key] = {
    ...envelope(key === "redirect303" ? "3" : "5"), state: "unavailable", httpStatus: null,
    locationDigest: null, contentRange: null, responseSha256: null, artifactSha256: null,
    unavailableReason: "artifact_not_published",
  };
  for (const key of ["powerpoint", "libreoffice"]) draft.terminalEvidence.readback[key] = {
    ...envelope(key === "powerpoint" ? "7" : "a"), state: "unavailable", artifactSha256: null,
    toolIdentityDigest: null, readbackDigest: null, slideCount: null, unavailableReason: "artifact_not_published",
  };
  const bundle = sealImmutableBundle(draft, { sealedAt: "2026-08-30T00:00:19.000Z" });
  const result = validateImmutableBundle(bundle);
  assert.equal(result.pass, false);
  assert.equal(result.checks.find((item) => item.id === "phase-timing-retry-provider-telemetry").pass, true);
  assert.equal(result.checks.find((item) => item.id === "terminal-artifact-download-readback").pass, true);
  assert.equal(bundle.phases[3].durationMs, 1_000);
  assert.equal(bundle.phases[3].attemptCount, 3);
  assert.deepEqual(bundle.phases[3].providerUsage, usage(2));
});

test("tampering after sealing invalidates the canonical digest", () => {
  const bundle = sealImmutableBundle(passingDraft(), { sealedAt: "2026-08-30T00:00:19.000Z" });
  bundle.terminalEvidence.artifact.bytes += 1;
  const result = validateImmutableBundle(bundle);
  assert.equal(result.pass, false);
  assert.equal(result.checks.find((item) => item.id === "immutable-canonical-digest").pass, false);
});
