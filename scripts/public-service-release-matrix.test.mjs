import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import {
  ACTUAL_EVIDENCE_CLASS,
  MATRIX_EVIDENCE_SCHEMA,
  buildReleaseMatrixPlan,
  digestReleaseMatrixEvidence,
  validateReleaseMatrixEvidence,
} from "./public-service-release-matrix.mjs";

const COMMIT = "56a6921cd1242e3ac35dee88bc7f07724e37120a";
const RC = `sha256:${"a".repeat(64)}`;
const DIGEST = `sha256:${"b".repeat(64)}`;
const digest = (value) => `sha256:${createHash("sha256").update(String(value)).digest("hex")}`;

function operation(index) {
  return {
    evidenceClass: ACTUAL_EVIDENCE_CLASS,
    releaseCandidate: RC,
    sourceTreeCommit: COMMIT,
    apiRequestId: `api-${index}`,
    workerOperationId: `worker-${index}`,
    providerUsageDigest: DIGEST,
    renderDigest: DIGEST,
    exporterDigest: DIGEST,
    fontDigest: DIGEST,
    databaseTerminalState: "succeeded",
    artifactDigest: DIGEST,
    redirectStatus: 303,
    rangeStatus: 206,
    phaseTimingsDigest: DIGEST,
    retryUsageDigest: DIGEST,
    evidenceBundleDigest: digest(`bundle-${index}`),
  };
}

function contractOnlyPassingReport() {
  const plan = buildReleaseMatrixPlan({ releaseCandidate: RC, sourceTreeCommit: COMMIT, repetitions: 1 });
  const report = {
    schemaVersion: MATRIX_EVIDENCE_SCHEMA,
    evidenceClass: ACTUAL_EVIDENCE_CLASS,
    releaseCandidate: RC,
    sourceTreeCommit: COMMIT,
    planDigest: plan.planDigest,
    deploymentAuthorized: false,
    provenance: {
      runnerPath: "scripts/public-service-release-matrix.mjs",
      runnerSha256: DIGEST,
      sourceManifestDigest: RC,
    },
    matrix: {
      repetitions: 1,
      cells: plan.matrix.cells.map((cell) => ({
        ...cell,
        evidenceClass: ACTUAL_EVIDENCE_CLASS,
        releaseCandidate: RC,
        sourceTreeCommit: COMMIT,
        cacheControlObserved: true,
        attempted: cell.concurrency,
        succeeded: cell.concurrency,
        failed: 0,
        operationEvidence: Array.from({ length: cell.concurrency }, (_, index) => operation(`${cell.cellId}-${index}`)),
      })),
    },
    faults: ["worker", "database", "s3", "provider"].map((faultType) => ({
      faultType,
      evidenceClass: ACTUAL_EVIDENCE_CLASS,
      releaseCandidate: RC,
      sourceTreeCommit: COMMIT,
      actualFaultObserved: true,
      injectionReceiptDigest: DIGEST,
      failClosed: true,
      recovered: true,
      terminalStateConverged: true,
      duplicatePublicationCount: 0,
      staleWriteAcceptedCount: 0,
      phaseTimingsPreserved: true,
      retryProviderUsagePreserved: true,
      ...({
        worker: { workerTerminationObserved: true, checkpointResumeObserved: true },
        database: { databaseUnavailableObserved: true, outboxRecoveryObserved: true },
        s3: { objectStoreUnavailableObserved: true, artifactPublicationRecovered: true },
        provider: { providerFailureObserved: true, boundedRetryObserved: true },
      }[faultType]),
      recoveryOperationEvidence: operation(`fault-${faultType}`),
    })),
    soak: {
      evidenceClass: ACTUAL_EVIDENCE_CLASS,
      releaseCandidate: RC,
      sourceTreeCommit: COMMIT,
      startedAt: "2026-08-01T00:00:00.000Z",
      endedAt: "2026-08-02T00:00:00.000Z",
      durationSeconds: 86_400,
      sampleCount: 1_440,
      attemptedOperations: 2,
      succeededOperations: 2,
      operationEvidenceDigests: [digest("soak-0"), digest("soak-1")],
      queueStarvationCount: 0,
      resourceLimitsEnforced: true,
      resourcesRecoveredWithinBaseline: true,
      terminalStateConverged: true,
    },
  };
  report.bundleDigest = digestReleaseMatrixEvidence(report);
  return report;
}

test("plan covers 32 matrix cells, four faults, and a 24-hour soak without granting readiness", () => {
  const plan = buildReleaseMatrixPlan({ releaseCandidate: RC, sourceTreeCommit: COMMIT, repetitions: 2 });
  assert.equal(plan.matrix.cells.length, 32);
  assert.equal(new Set(plan.matrix.cells.map((cell) => cell.cellId)).size, 32);
  assert.deepEqual(new Set(plan.matrix.cells.map((cell) => cell.slideCount)), new Set([5, 10, 20, 30]));
  assert.deepEqual(plan.faults.map((fault) => fault.faultType), ["worker", "database", "s3", "provider"]);
  assert.equal(plan.soak.minimumDurationSeconds, 86_400);
  assert.equal(plan.executionState, "not-executed");
  assert.equal(plan.releaseReadiness, "not-assessed");
  assert.equal(plan.deploymentAuthorized, false);
});

test("audit rejects plan and fixture labels instead of treating them as product evidence", () => {
  const plan = buildReleaseMatrixPlan({ releaseCandidate: RC, sourceTreeCommit: COMMIT });
  assert.equal(validateReleaseMatrixEvidence(plan).pass, false);

  const fixture = contractOnlyPassingReport();
  fixture.evidenceClass = "fixture";
  fixture.bundleDigest = digestReleaseMatrixEvidence(fixture);
  const audited = validateReleaseMatrixEvidence(fixture);
  assert.equal(audited.pass, false);
  assert.equal(audited.releaseReadiness, "blocked");
  assert.equal(audited.deploymentAuthorized, false);
  assert.equal(audited.checks.find((item) => item.id === "evidenceClass")?.pass, false);
});

test("complete contract object passes the pure audit but never authorizes deployment", () => {
  // This in-memory object tests the validator contract only. It is not persisted or release evidence.
  const audited = validateReleaseMatrixEvidence(contractOnlyPassingReport());
  assert.equal(audited.pass, true);
  assert.equal(audited.releaseReadiness, "ready");
  assert.equal(audited.deploymentAuthorized, false);
});
