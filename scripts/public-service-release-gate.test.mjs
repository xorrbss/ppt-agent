import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import { evaluateReleaseEvidence } from "./public-service-release-gate.mjs";

const RELEASE_CANDIDATE = `sha256:${"c".repeat(64)}`;
const SOURCE_TREE_COMMIT = "b3c77d6650929c667ff38e06c9d674db596bf915";
const RUNNERS = {
  product: { path: "scripts/public-service-product-evidence.mjs", sha256: `sha256:${"1".repeat(64)}` },
  crash: { path: "scripts/public-service-crash-recovery.py", sha256: `sha256:${"2".repeat(64)}` },
  security: {
    path: "servers/fastapi/scripts/public_service_malicious_corpus_evidence.py",
    sha256: `sha256:${"a".repeat(64)}`,
  },
  powerpoint: {
    path: "scripts/collect-public-service-powerpoint-readback.ps1",
    sha256: `sha256:${"3".repeat(64)}`,
  },
  soak: { path: "scripts/public-service-soak-resource.py", sha256: `sha256:${"4".repeat(64)}` },
};

const runnerProvenance = (runner) => ({
  runnerPath: runner.path,
  runnerSha256: runner.sha256,
  sourceManifestDigest: RELEASE_CANDIDATE,
});

const common = (schemaVersion) => ({
  schemaVersion,
  releaseCandidate: RELEASE_CANDIDATE,
  evidenceClass: "product-path",
});

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

const valueDigest = (value) => `sha256:${createHash("sha256").update(canonicalJson(value)).digest("hex")}`;

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
  const securityObservations = {
    safeHtmlControl: { pass: true, outputMediaType: "image/png" },
    networkIsolation: {
      osNetworkNamespaceObserved: true,
      containerNetworkMode: "none",
      egressSinkConnectionCount: 0,
      cdpBlockedRequestCount: 3,
      producerSourceBindingObserved: true,
      evidenceExactByteChainObserved: true,
      immutableImageIdentityObserved: true,
      imageId: `sha256:${"d".repeat(64)}`,
      sourceTreeDigestMatches: true,
      contractDigestMatches: true,
      sourceManifestDigest: RELEASE_CANDIDATE,
      pass: true,
    },
    htmlCorpus: [
      { caseId: "network", category: "network", expectedErrorCode: "public_render_policy_rejected",
        observedErrorCode: "public_render_policy_rejected", accessSucceeded: false, pass: true },
    ],
    pptxCorpus: [
      { caseId: "xxe", expectedErrorCode: "unsafe_xml_declaration",
        observedErrorCode: "unsafe_xml_declaration", pass: true },
    ],
    crossOwner: [
      { caseId: "api-read", operation: "read", accessSucceeded: false, pass: true },
      { caseId: "api-write", operation: "write", accessSucceeded: false, pass: true },
    ],
    capabilityBinding: [{ caseId: "capability-digest", pass: true }],
    workerBoundary: { sessionlessEnvironmentAllowlistVerified: true },
    ownerBoundArtifactControl: { ownerBound: true },
  };
  const securityProvenance = {
    gitHead: SOURCE_TREE_COMMIT,
    observationDigest: valueDigest(securityObservations),
    ...runnerProvenance(RUNNERS.security),
    runnerIncludedInSourceManifest: true,
    sourceDigests: { "workers/public_render_worker.py": `sha256:${"b".repeat(64)}` },
    sourcesMatchSourceManifest: true,
  };
  securityProvenance.provenanceDigest = valueDigest(securityProvenance);
  return {
    sourceManifest: {
      schemaVersion: "public-service-source-manifest/v1",
      sourceTreeCommit: SOURCE_TREE_COMMIT,
      files: [
        ...Object.values(RUNNERS).map((runner) => ({ path: runner.path, sha256: runner.sha256 })),
        { path: "workers/public_render_worker.py", sha256: `sha256:${"b".repeat(64)}` },
      ],
      exactByteSha256: RELEASE_CANDIDATE,
      _verified: { fileSetValid: true, currentDigestsMatch: true },
    },
    controlledLoad: {
      ...common("public-service-controlled-load-evidence/v1"),
      provenance: runnerProvenance(RUNNERS.product),
      architecture: { modularPptCore: true, roleIsolatedWorkers: true,
        artifactRefObjectStorage: true, clusterSingleFlight: true },
      scenarios,
      observability: { executedOperationCount: 100, criticalNullMetricCount: 0,
        metricSampleCounts: Object.fromEntries(metricNames.map((name) => [name, 100])),
        metricUnavailableCounts: Object.fromEntries(metricNames.map((name) => [name, 0])) },
    },
    reliability: {
      ...common("public-service-reliability-evidence/v1"),
      provenance: runnerProvenance(RUNNERS.product),
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
      provenance: runnerProvenance(RUNNERS.crash),
      injectedCrashCount: 10, recoveredCrashCount: 10, automaticConvergenceRate: 1,
      checkpointResumeVerified: true, staleWorkerCheckpointAccepted: 0,
      staleWorkerPublicationAccepted: 0, dbOutboxRecoveryAuthorityVerified: true,
    },
    maliciousCorpus: {
      ...common("public-service-malicious-corpus-evidence/v1"),
      status: "PASS",
      maliciousHtmlCaseCount: 1, maliciousPptxCaseCount: 1,
      successfulNetworkAccessCount: 0, successfulFileAccessCount: 0,
      successfulMetadataAccessCount: 0, crossOwnerAttemptCount: 2,
      successfulCrossOwnerReadCount: 0, successfulCrossOwnerWriteCount: 0,
      sessionlessWorkerVerified: true, networkNoneRenderVerified: true,
      operationDigestCapabilityBindingVerified: true, artifactOwnerBindingVerified: true,
      observations: securityObservations,
      provenance: securityProvenance,
      _verifiedProvenance: {
        runnerDigestMatches: true,
        sourceDigestsMatch: true,
      },
    },
    powerpointReadback: {
      ...common("public-service-powerpoint-readback-evidence/v1"),
      provenance: runnerProvenance(RUNNERS.powerpoint),
      testedPptxCount: 20, reopenPassedCount: 20, packageReadbackFailureCount: 0,
      digestReadbackMismatchCount: 0, supportedRendererMatrixPassed: true,
    },
    soakResource: {
      ...common("public-service-soak-resource-evidence/v1"),
      provenance: runnerProvenance(RUNNERS.soak),
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

  const missingManifest = passingEvidence();
  delete missingManifest.sourceManifest;
  assert.equal(evaluateReleaseEvidence(missingManifest).gates.evidenceIntegrity.status, "FAIL");

  const staleManifestFile = passingEvidence();
  staleManifestFile.sourceManifest._verified.currentDigestsMatch = false;
  assert.equal(evaluateReleaseEvidence(staleManifestFile).gates.evidenceIntegrity.status, "FAIL");

  const failedReport = passingEvidence();
  failedReport.maliciousCorpus.status = "FAIL";
  assert.equal(evaluateReleaseEvidence(failedReport).gates.security.status, "FAIL");

  const inconsistentObservation = passingEvidence();
  inconsistentObservation.maliciousCorpus.observations.htmlCorpus[0].observedErrorCode =
    "public_render_failed";
  assert.equal(evaluateReleaseEvidence(inconsistentObservation).gates.security.status, "FAIL");

  const uncommittedRunner = passingEvidence();
  uncommittedRunner.sourceManifest.files = uncommittedRunner.sourceManifest.files.filter(
    (item) => item.path !== uncommittedRunner.maliciousCorpus.provenance.runnerPath,
  );
  assert.equal(evaluateReleaseEvidence(uncommittedRunner).gates.security.status, "FAIL");

  const selfAssertedNetworkNone = passingEvidence();
  selfAssertedNetworkNone.maliciousCorpus.observations.networkIsolation.producerSourceBindingObserved = false;
  selfAssertedNetworkNone.maliciousCorpus.observations.networkIsolation.evidenceExactByteChainObserved = false;
  selfAssertedNetworkNone.maliciousCorpus.observations.networkIsolation.immutableImageIdentityObserved = false;
  assert.equal(evaluateReleaseEvidence(selfAssertedNetworkNone).gates.security.status, "FAIL");
});

test("missing, null, threshold, duplicate, cost, and 24h evidence fail closed in their gates", () => {
  const evidence = passingEvidence();
  evidence.controlledLoad.scenarios.find((item) => item.concurrency === 5 && item.slideCount === 5)
    .generationToPptxP95Ms = 180_001;
  evidence.controlledLoad.observability.metricSampleCounts.actualCost = null;
  evidence.controlledLoad.observability.metricUnavailableCounts.actualCost = 1;
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
