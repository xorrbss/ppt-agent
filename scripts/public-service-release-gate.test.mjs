import assert from "node:assert/strict";
import test from "node:test";

import { evaluateReleaseEvidence } from "./public-service-release-gate.mjs";

const common = (schemaVersion) => ({
  schemaVersion,
  releaseCandidate: "public-service-rc-20260830.1",
  evidenceClass: "product-path",
});

function passingEvidence() {
  const metricNames = [
    "queueWait", "queuePosition", "outlineDuration", "authorDuration", "illustrationDuration",
    "renderDuration", "qualityDuration", "checkpointDuration", "publicationDuration", "exportDuration",
    "providerInitialCalls", "providerRetryCalls", "providerLogicalOrdinal", "estimatedTokens", "actualTokens",
    "estimatedCost", "actualCost", "attemptNumber", "fencingOutcome", "chromeLaunch", "chromeRestart",
    "blockedRequest", "timeout", "crash", "workerPeakMemory", "workerPeakHandles", "artifactBytes",
    "cacheHit", "cacheMiss", "cacheEviction", "objectStoreLatency", "finalOutcome",
  ];
  const scenarios = [];
  for (const concurrency of [1, 2, 5, 10]) {
    scenarios.push({ concurrency, slideCount: 5, attempted: 20, succeeded: 20,
      generationToPptxP95Ms: 179_999, starvationCount: 0, resourceLimitsEnforced: true });
    scenarios.push({ concurrency, slideCount: 10, attempted: 20, succeeded: 19,
      generationToPptxP95Ms: 359_999, starvationCount: 0, resourceLimitsEnforced: true });
  }
  return {
    controlledLoad: {
      ...common("public-service-controlled-load-evidence/v1"),
      architecture: { modularPptCore: true, roleIsolatedWorkers: true,
        artifactRefObjectStorage: true, clusterSingleFlight: true },
      scenarios,
      observability: { executedOperationCount: 100, criticalNullMetricCount: 0,
        metricSampleCounts: Object.fromEntries(metricNames.map((name) => [name, 100])) },
    },
    reliability: {
      ...common("public-service-reliability-evidence/v1"),
      fixedDeckCount: 20, successfulDeckCount: 19, e2eSuccessRate: 0.95,
      failedDecksWithPublishedArtifact: 0, failedDecksWithExportArtifact: 0,
      casPublicationVerified: true,
      duplicates: { providerLogicalCalls: 0, publications: 0, exports: 0 },
      providerBudget: { publicJobCount: 20, jobsWithoutRequestCap: 0,
        jobsWithoutEstimatedCostCap: 0, jobsWithoutActualCostMetric: 0,
        jobsExceedingRequestCap: 0, jobsExceedingEstimatedCostCap: 0,
        jobsExceedingActualCostCap: 0 },
    },
    crashRecovery: {
      ...common("public-service-crash-recovery-evidence/v1"),
      injectedCrashCount: 10, recoveredCrashCount: 10, automaticConvergenceRate: 1,
      checkpointResumeVerified: true, staleWorkerCheckpointAccepted: 0,
      staleWorkerPublicationAccepted: 0, dbOutboxRecoveryAuthorityVerified: true,
    },
    maliciousCorpus: {
      ...common("public-service-malicious-corpus-evidence/v1"),
      maliciousHtmlCaseCount: 20, maliciousPptxCaseCount: 20,
      successfulNetworkAccessCount: 0, successfulFileAccessCount: 0,
      successfulMetadataAccessCount: 0, crossOwnerAttemptCount: 20,
      successfulCrossOwnerReadCount: 0, successfulCrossOwnerWriteCount: 0,
      sessionlessWorkerVerified: true, networkNoneRenderVerified: true,
      operationDigestCapabilityBindingVerified: true, artifactOwnerBindingVerified: true,
    },
    powerpointReadback: {
      ...common("public-service-powerpoint-readback-evidence/v1"),
      testedPptxCount: 20, reopenPassedCount: 20, packageReadbackFailureCount: 0,
      digestReadbackMismatchCount: 0, supportedRendererMatrixPassed: true,
    },
    soakResource: {
      ...common("public-service-soak-resource-evidence/v1"),
      completed: true, durationSeconds: 86_400, sampleCount: 1_440,
      memoryRecoveredWithinBaseline: true, handlesRecoveredWithinBaseline: true,
      finalWorkerMemoryBytes: 300_000_000, memoryPolicyLimitBytes: 400_000_000,
      finalWorkerHandleCount: 200, handlePolicyLimit: 300, queueStarvationCount: 0,
      resourceLimitsEnforced: true, workerRecycleFailureCount: 0,
    },
  };
}

test("public service release gate passes only complete same-RC product-path evidence", () => {
  const passed = evaluateReleaseEvidence(passingEvidence());
  assert.equal(passed.status, "PASS");
  assert.equal(passed.compositeScore, 87);
  assert.equal(passed.promotionReady, true);
  assert.equal(passed.deploymentAuthorized, false);
  assert.deepEqual(Object.fromEntries(Object.entries(passed.gates).map(([name, item]) => [name, item.status])), {
    evidenceIntegrity: "PASS", architecture: "PASS", businessReliability: "PASS",
    performance: "PASS", security: "PASS", observability: "PASS",
  });

  const fixture = passingEvidence();
  fixture.controlledLoad.evidenceClass = "fixture";
  const fixtureResult = evaluateReleaseEvidence(fixture);
  assert.equal(fixtureResult.status, "FAIL");
  assert.equal(fixtureResult.promotionReady, false);
  assert.equal(fixtureResult.gates.evidenceIntegrity.status, "FAIL");

  const wrongRc = passingEvidence();
  wrongRc.soakResource.releaseCandidate = "different-rc";
  assert.equal(evaluateReleaseEvidence(wrongRc).gates.evidenceIntegrity.status, "FAIL");
});

test("missing, null, threshold, duplicate, cost, and 24h evidence fail closed in their gates", () => {
  const evidence = passingEvidence();
  evidence.controlledLoad.scenarios.find((item) => item.concurrency === 5 && item.slideCount === 5)
    .generationToPptxP95Ms = 180_001;
  evidence.controlledLoad.observability.metricSampleCounts.actualCost = null;
  evidence.reliability.duplicates.exports = 1;
  evidence.reliability.providerBudget.jobsWithoutEstimatedCostCap = 1;
  evidence.maliciousCorpus.successfulCrossOwnerReadCount = 1;
  evidence.crashRecovery.automaticConvergenceRate = 0.9;
  evidence.soakResource.durationSeconds = 86_399;
  const result = evaluateReleaseEvidence(evidence);

  assert.equal(result.status, "FAIL");
  assert.equal(result.promotionReady, false);
  assert.equal(result.deploymentAuthorized, false);
  assert.equal(result.gates.performance.status, "FAIL");
  assert.equal(result.gates.businessReliability.status, "FAIL");
  assert.equal(result.gates.security.status, "FAIL");
  assert.equal(result.gates.observability.status, "FAIL");
  assert.match(result.gates.observability.checks.find((item) => item.id.endsWith("actualCost")).reason,
    /missing_or_null/);

  const absentReport = passingEvidence();
  delete absentReport.powerpointReadback;
  const absentResult = evaluateReleaseEvidence(absentReport);
  assert.equal(absentResult.gates.evidenceIntegrity.status, "FAIL");
  assert.equal(absentResult.gates.businessReliability.status, "FAIL");
  assert.equal(absentResult.promotionReady, false);
});
