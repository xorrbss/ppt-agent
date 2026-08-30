#!/usr/bin/env node

import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPOSITORY_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SOURCE_MANIFEST_SCHEMA = "public-service-source-manifest/v1";

const EVIDENCE = Object.freeze({
  controlledLoad: {
    option: "--controlled-load",
    schemaVersion: "public-service-controlled-load-evidence/v1",
    runnerPath: "scripts/public-service-product-evidence.mjs",
  },
  reliability: {
    option: "--reliability",
    schemaVersion: "public-service-reliability-evidence/v1",
    runnerPath: "scripts/public-service-product-evidence.mjs",
  },
  crashRecovery: {
    option: "--crash-recovery",
    schemaVersion: "public-service-crash-recovery-evidence/v1",
    runnerPath: "scripts/public-service-crash-recovery.py",
  },
  maliciousCorpus: {
    option: "--malicious-corpus",
    schemaVersion: "public-service-malicious-corpus-evidence/v1",
    runnerPath: "servers/fastapi/scripts/public_service_malicious_corpus_evidence.py",
  },
  powerpointReadback: {
    option: "--powerpoint-readback",
    schemaVersion: "public-service-powerpoint-readback-evidence/v1",
    runnerPath: "scripts/collect-public-service-powerpoint-readback.ps1",
  },
  soakResource: {
    option: "--soak-resource",
    schemaVersion: "public-service-soak-resource-evidence/v1",
    runnerPath: "scripts/public-service-soak-resource.py",
  },
});

const SCORE_TARGETS = Object.freeze({
  architecture: 88,
  businessReliability: 87,
  performance: 85,
  security: 90,
  observability: 85,
});

const REQUIRED_CONCURRENCY = Object.freeze([1, 2, 5, 10]);
const SLA_BY_SLIDE_COUNT_MS = Object.freeze({ 5: 180_000, 10: 360_000 });
const REQUIRED_METRIC_SAMPLE_COUNTS = Object.freeze([
  "queueWait",
  "queuePosition",
  "outlineDuration",
  "authorDuration",
  "illustrationDuration",
  "renderDuration",
  "qualityDuration",
  "checkpointDuration",
  "publicationDuration",
  "exportDuration",
  "providerInitialCalls",
  "providerRetryCalls",
  "providerLogicalOrdinal",
  "estimatedTokens",
  "actualTokens",
  "estimatedCost",
  "actualCost",
  "attemptNumber",
  "fencingOutcome",
  "chromeLaunch",
  "chromeRestart",
  "blockedRequest",
  "timeout",
  "crash",
  "workerPeakMemory",
  "workerPeakHandles",
  "artifactBytes",
  "cacheHit",
  "cacheMiss",
  "cacheEviction",
  "objectStoreLatency",
  "finalOutcome",
]);

function valueAt(object, dottedPath) {
  return dottedPath.split(".").reduce((value, key) => value?.[key], object);
}

function isPresent(value) {
  return value !== undefined && value !== null;
}

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function valueDigest(value) {
  return `sha256:${createHash("sha256").update(canonicalJson(value)).digest("hex")}`;
}

function isSha256(value) {
  return typeof value === "string" && /^sha256:[0-9a-f]{64}$/u.test(value);
}

function check(id, report, dottedPath, predicate, expected) {
  const actual = valueAt(report, dottedPath);
  if (!isPresent(actual)) {
    return { id, pass: false, expected, actual: null, reason: `missing_or_null:${dottedPath}` };
  }
  const pass = Boolean(predicate(actual));
  return { id, pass, expected, actual, ...(pass ? {} : { reason: `criterion_not_met:${dottedPath}` }) };
}

function customCheck(id, pass, expected, actual, reason = "criterion_not_met") {
  return { id, pass: Boolean(pass), expected, actual: isPresent(actual) ? actual : null,
    ...(pass ? {} : { reason: isPresent(actual) ? reason : "missing_or_null" }) };
}

function gate(checks, scoreTarget = null) {
  const pass = checks.every((item) => item.pass);
  return {
    status: pass ? "PASS" : "FAIL",
    ...(scoreTarget === null ? {} : { targetScore: scoreTarget, awardedScore: pass ? scoreTarget : 0 }),
    checks,
  };
}

function sourceChecks(reports, sourceManifest) {
  const checks = [];
  const manifestFiles = Array.isArray(sourceManifest?.files)
    ? new Map(sourceManifest.files.map((item) => [item?.path, item?.sha256]))
    : new Map();
  for (const [name, config] of Object.entries(EVIDENCE)) {
    const report = reports?.[name];
    checks.push(check(`${name}.schema`, report, "schemaVersion", (value) => value === config.schemaVersion,
      config.schemaVersion));
    checks.push(check(`${name}.releaseCandidate`, report, "releaseCandidate",
      (value) => typeof value === "string" && value.trim().length > 0, "non-empty RC identifier"));
    checks.push(check(`${name}.productPath`, report, "evidenceClass",
      (value) => value === "product-path", "product-path (fixture/package-smoke are not promotable)"));
    checks.push(check(`${name}.runnerPath`, report, "provenance.runnerPath",
      (value) => value === config.runnerPath, config.runnerPath));
    checks.push(check(`${name}.runnerSha256`, report, "provenance.runnerSha256",
      isSha256, "sha256:<64 lowercase hex>"));
    checks.push(customCheck(`${name}.runnerManifestBinding`,
      manifestFiles.get(report?.provenance?.runnerPath) === report?.provenance?.runnerSha256,
      "runner exact-byte SHA-256 equals source manifest entry",
      report?.provenance?.runnerSha256));
    checks.push(check(`${name}.sourceManifestDigest`, report, "provenance.sourceManifestDigest",
      (value) => value === sourceManifest?.exactByteSha256,
      "equals exact source manifest byte SHA-256"));
  }

  const releaseCandidates = Object.keys(EVIDENCE)
    .map((name) => reports?.[name]?.releaseCandidate)
    .filter(isPresent);
  const sameRc = releaseCandidates.length === Object.keys(EVIDENCE).length
    && new Set(releaseCandidates).size === 1;
  checks.push(customCheck("sameReleaseCandidate", sameRc, "all evidence from exactly one RC",
    [...new Set(releaseCandidates)], "release_candidate_mismatch"));
  checks.push(check("sourceManifest.schema", sourceManifest, "schemaVersion",
    (value) => value === SOURCE_MANIFEST_SCHEMA, SOURCE_MANIFEST_SCHEMA));
  checks.push(check("sourceManifest.sourceTreeCommit", sourceManifest, "sourceTreeCommit",
    (value) => /^[0-9a-f]{40}$/u.test(value), "40-character lowercase Git commit"));
  checks.push(check("sourceManifest.exactByteSha256", sourceManifest, "exactByteSha256",
    isSha256, "sha256:<64 lowercase hex>"));
  checks.push(check("sourceManifest.fileSet", sourceManifest, "_verified.fileSetValid",
    (value) => value === true, "true"));
  checks.push(check("sourceManifest.currentFiles", sourceManifest, "_verified.currentDigestsMatch",
    (value) => value === true, "true"));
  checks.push(customCheck("sourceManifest.releaseCandidateAuthority",
    sameRc && releaseCandidates[0] === sourceManifest?.exactByteSha256,
    "all evidence releaseCandidate equals exact manifest byte SHA-256",
    releaseCandidates[0] ?? null,
    "release_candidate_not_source_manifest_digest"));
  return checks;
}

function controlledScenarioChecks(report) {
  const scenarios = report?.scenarios;
  if (!Array.isArray(scenarios)) {
    return [customCheck("controlledLoad.scenarios", false, "scenario array", scenarios)];
  }

  const checks = [];
  for (const concurrency of REQUIRED_CONCURRENCY) {
    for (const [slideCountText, limitMs] of Object.entries(SLA_BY_SLIDE_COUNT_MS)) {
      const slideCount = Number(slideCountText);
      const matches = scenarios.filter((scenario) => scenario?.concurrency === concurrency
        && scenario?.slideCount === slideCount);
      const prefix = `controlledLoad.C${concurrency}.${slideCount}slides`;
      checks.push(customCheck(`${prefix}.uniqueScenario`, matches.length === 1,
        "exactly one product-path scenario", matches.length, "scenario_coverage_invalid"));
      const scenario = matches[0];
      checks.push(check(`${prefix}.attempted`, scenario, "attempted",
        (value) => Number.isInteger(value) && value > 0, "integer > 0"));
      checks.push(check(`${prefix}.succeeded`, scenario, "succeeded",
        (value) => Number.isInteger(value) && value >= 0 && value <= scenario?.attempted, "0..attempted"));
      checks.push(check(`${prefix}.p95`, scenario, "generationToPptxP95Ms",
        (value) => Number.isFinite(value) && value >= 0 && value <= limitMs, `<= ${limitMs}ms`));
      checks.push(check(`${prefix}.starvation`, scenario, "starvationCount",
        (value) => Number.isInteger(value) && value === 0, "0"));
      checks.push(check(`${prefix}.resourceLimits`, scenario, "resourceLimitsEnforced",
        (value) => value === true, "true"));
    }
  }

  const attempted = scenarios.reduce((sum, item) => sum + (Number.isInteger(item?.attempted) ? item.attempted : 0), 0);
  const succeeded = scenarios.reduce((sum, item) => sum + (Number.isInteger(item?.succeeded) ? item.succeeded : 0), 0);
  const successRate = attempted > 0 ? succeeded / attempted : null;
  checks.push(customCheck("controlledLoad.aggregateSuccessRate", successRate !== null && successRate >= 0.95,
    ">= 0.95", successRate));
  return checks;
}

function architectureChecks(report) {
  return [
    check("architecture.modularPptCore", report, "architecture.modularPptCore", (value) => value === true, "true"),
    check("architecture.roleIsolatedWorkers", report, "architecture.roleIsolatedWorkers", (value) => value === true, "true"),
    check("architecture.artifactRefObjectStorage", report, "architecture.artifactRefObjectStorage", (value) => value === true, "true"),
    check("architecture.clusterSingleFlight", report, "architecture.clusterSingleFlight", (value) => value === true, "true"),
  ];
}

function reliabilityChecks(report, crash, powerpoint) {
  return [
    check("reliability.fixedDeckCount", report, "fixedDeckCount", (value) => Number.isInteger(value) && value >= 20, ">= 20"),
    check("reliability.successfulDeckCount", report, "successfulDeckCount",
      (value) => Number.isInteger(value) && value >= 19 && value <= report?.fixedDeckCount, ">= 19 and <= fixedDeckCount"),
    check("reliability.e2eSuccessRate", report, "e2eSuccessRate", (value) => Number.isFinite(value) && value >= 0.95, ">= 0.95"),
    customCheck("reliability.rateMatchesCounts",
      Number.isFinite(report?.e2eSuccessRate) && Number.isInteger(report?.fixedDeckCount)
        && report.fixedDeckCount > 0 && Number.isInteger(report?.successfulDeckCount)
        && Math.abs(report.e2eSuccessRate - (report.successfulDeckCount / report.fixedDeckCount)) < 1e-9,
      "e2eSuccessRate equals successfulDeckCount/fixedDeckCount", report?.e2eSuccessRate,
      "reported_rate_does_not_match_counts"),
    check("reliability.failedDeckPublishedArtifacts", report, "failedDecksWithPublishedArtifact", (value) => value === 0, "0"),
    check("reliability.failedDeckExportArtifacts", report, "failedDecksWithExportArtifact", (value) => value === 0, "0"),
    check("reliability.casPublication", report, "casPublicationVerified", (value) => value === true, "true"),
    check("reliability.duplicateProviderLogicalCalls", report, "duplicates.providerLogicalCalls", (value) => value === 0, "0"),
    check("reliability.duplicatePublications", report, "duplicates.publications", (value) => value === 0, "0"),
    check("reliability.duplicateExports", report, "duplicates.exports", (value) => value === 0, "0"),
    check("crash.injected", crash, "injectedCrashCount", (value) => Number.isInteger(value) && value > 0, "integer > 0"),
    check("crash.recovered", crash, "recoveredCrashCount",
      (value) => Number.isInteger(value) && value === crash?.injectedCrashCount, "equals injectedCrashCount"),
    check("crash.convergence", crash, "automaticConvergenceRate", (value) => value === 1, "1.0"),
    check("crash.resume", crash, "checkpointResumeVerified", (value) => value === true, "true"),
    check("crash.staleCheckpoint", crash, "staleWorkerCheckpointAccepted", (value) => value === 0, "0"),
    check("crash.stalePublication", crash, "staleWorkerPublicationAccepted", (value) => value === 0, "0"),
    check("crash.dbOutboxAuthority", crash, "dbOutboxRecoveryAuthorityVerified", (value) => value === true, "true"),
    check("powerpoint.tested", powerpoint, "testedPptxCount", (value) => Number.isInteger(value) && value > 0, "integer > 0"),
    check("powerpoint.reopen", powerpoint, "reopenPassedCount",
      (value) => Number.isInteger(value) && value === powerpoint?.testedPptxCount, "equals testedPptxCount"),
    check("powerpoint.packageReadback", powerpoint, "packageReadbackFailureCount", (value) => value === 0, "0"),
    check("powerpoint.digestReadback", powerpoint, "digestReadbackMismatchCount", (value) => value === 0, "0"),
    check("powerpoint.rendererMatrix", powerpoint, "supportedRendererMatrixPassed", (value) => value === true, "true"),
  ];
}

function performanceChecks(controlled, soak) {
  return [
    ...controlledScenarioChecks(controlled),
    check("soak.completed", soak, "completed", (value) => value === true, "true"),
    check("soak.duration", soak, "durationSeconds", (value) => Number.isFinite(value) && value >= 86_400, ">= 86400"),
    check("soak.samples", soak, "sampleCount", (value) => Number.isInteger(value) && value > 0, "integer > 0"),
    check("soak.memoryRecovered", soak, "memoryRecoveredWithinBaseline", (value) => value === true, "true"),
    check("soak.handlesRecovered", soak, "handlesRecoveredWithinBaseline", (value) => value === true, "true"),
    check("soak.finalMemory", soak, "finalWorkerMemoryBytes",
      (value) => Number.isFinite(value) && Number.isFinite(soak?.memoryPolicyLimitBytes)
        && value <= soak.memoryPolicyLimitBytes, "<= memoryPolicyLimitBytes"),
    check("soak.memoryLimit", soak, "memoryPolicyLimitBytes", (value) => Number.isFinite(value) && value > 0, "> 0"),
    check("soak.finalHandles", soak, "finalWorkerHandleCount",
      (value) => Number.isFinite(value) && Number.isFinite(soak?.handlePolicyLimit)
        && value <= soak.handlePolicyLimit, "<= handlePolicyLimit"),
    check("soak.handleLimit", soak, "handlePolicyLimit", (value) => Number.isFinite(value) && value > 0, "> 0"),
    check("soak.starvation", soak, "queueStarvationCount", (value) => value === 0, "0"),
    check("soak.resourceLimits", soak, "resourceLimitsEnforced", (value) => value === true, "true"),
    check("soak.recycleFailures", soak, "workerRecycleFailureCount", (value) => value === 0, "0"),
  ];
}

function securityChecks(report, sourceManifest) {
  const htmlCases = report?.observations?.htmlCorpus;
  const pptxCases = report?.observations?.pptxCorpus;
  const crossOwner = report?.observations?.crossOwner;
  const capability = report?.observations?.capabilityBinding;
  const provenance = report?.provenance;
  const manifestFiles = Array.isArray(sourceManifest?.files)
    ? new Map(sourceManifest.files.map((item) => [item?.path, item?.sha256]))
    : new Map();
  const withoutDigest = provenance && typeof provenance === "object"
    ? Object.fromEntries(Object.entries(provenance).filter(([key]) => key !== "provenanceDigest"))
    : null;
  const expectedObservationDigest = valueDigest(report?.observations ?? null);
  const expectedProvenanceDigest = valueDigest(withoutDigest);
  const htmlConsistent = Array.isArray(htmlCases)
    && htmlCases.length === report?.maliciousHtmlCaseCount
    && htmlCases.every((item) => item?.pass === true
      && typeof item?.expectedErrorCode === "string"
      && item.observedErrorCode === item.expectedErrorCode
      && item.accessSucceeded === false);
  const pptxConsistent = Array.isArray(pptxCases)
    && pptxCases.length === report?.maliciousPptxCaseCount
    && pptxCases.every((item) => item?.pass === true
      && typeof item?.expectedErrorCode === "string"
      && item.observedErrorCode === item.expectedErrorCode);
  const crossOwnerConsistent = Array.isArray(crossOwner)
    && crossOwner.length === report?.crossOwnerAttemptCount
    && crossOwner.every((item) => item?.pass === true && item.accessSucceeded === false)
    && crossOwner.some((item) => item?.operation === "read")
    && crossOwner.some((item) => item?.operation === "write");
  const capabilityConsistent = Array.isArray(capability) && capability.length > 0
    && capability.every((item) => item?.pass === true);
  const actualCrossOwnerReads = Array.isArray(crossOwner)
    ? crossOwner.filter((item) => item?.operation === "read" && item?.accessSucceeded === true).length : null;
  const actualCrossOwnerWrites = Array.isArray(crossOwner)
    ? crossOwner.filter((item) => item?.operation === "write" && item?.accessSucceeded === true).length : null;
  const accessCount = (category) => Array.isArray(htmlCases)
    ? htmlCases.filter((item) => item?.category === category && item?.accessSucceeded === true).length : null;
  const runnerManifestMatches = typeof provenance?.runnerPath === "string"
    && isSha256(provenance?.runnerSha256)
    && manifestFiles.get(provenance.runnerPath) === provenance.runnerSha256;
  const sourcesManifestMatch = provenance?.sourceDigests
    && typeof provenance.sourceDigests === "object"
    && !Array.isArray(provenance.sourceDigests)
    && Object.keys(provenance.sourceDigests).length > 0
    && Object.entries(provenance.sourceDigests).every(
      ([sourcePath, digest]) => isSha256(digest) && manifestFiles.get(sourcePath) === digest,
    );
  return [
    check("security.reportStatus", report, "status", (value) => value === "PASS", "PASS"),
    check("security.maliciousHtmlCorpus", report, "maliciousHtmlCaseCount", (value) => Number.isInteger(value) && value > 0, "integer > 0"),
    check("security.maliciousPptxCorpus", report, "maliciousPptxCaseCount", (value) => Number.isInteger(value) && value > 0, "integer > 0"),
    check("security.networkAccess", report, "successfulNetworkAccessCount", (value) => value === 0, "0"),
    check("security.fileAccess", report, "successfulFileAccessCount", (value) => value === 0, "0"),
    check("security.metadataAccess", report, "successfulMetadataAccessCount", (value) => value === 0, "0"),
    check("security.crossOwnerAttempts", report, "crossOwnerAttemptCount", (value) => Number.isInteger(value) && value > 0, "integer > 0"),
    check("security.crossOwnerReads", report, "successfulCrossOwnerReadCount", (value) => value === 0, "0"),
    check("security.crossOwnerWrites", report, "successfulCrossOwnerWriteCount", (value) => value === 0, "0"),
    check("security.sessionlessWorker", report, "sessionlessWorkerVerified", (value) => value === true, "true"),
    check("security.networkNoneRender", report, "networkNoneRenderVerified", (value) => value === true, "true"),
    check("security.capabilityBinding", report, "operationDigestCapabilityBindingVerified", (value) => value === true, "true"),
    check("security.ownerBinding", report, "artifactOwnerBindingVerified", (value) => value === true, "true"),
    check("security.safeHtmlControl", report, "observations.safeHtmlControl.pass", (value) => value === true, "true"),
    check("security.osNetworkNamespace", report, "observations.networkIsolation.osNetworkNamespaceObserved", (value) => value === true, "true"),
    check("security.containerNetworkMode", report, "observations.networkIsolation.containerNetworkMode", (value) => value === "none", "none"),
    check("security.egressSink", report, "observations.networkIsolation.egressSinkConnectionCount", (value) => value === 0, "0"),
    check("security.cdpBlock", report, "observations.networkIsolation.cdpBlockedRequestCount", (value) => Number.isInteger(value) && value > 0, "integer > 0"),
    check("security.networkProducerBinding", report,
      "observations.networkIsolation.producerSourceBindingObserved", (value) => value === true, "true"),
    check("security.networkEvidenceByteChain", report,
      "observations.networkIsolation.evidenceExactByteChainObserved", (value) => value === true, "true"),
    check("security.immutableImageIdentity", report,
      "observations.networkIsolation.immutableImageIdentityObserved", (value) => value === true, "true"),
    check("security.imageId", report, "observations.networkIsolation.imageId", isSha256,
      "sha256:<64 lowercase hex>"),
    check("security.rendererSourceTreeDigest", report,
      "observations.networkIsolation.sourceTreeDigestMatches", (value) => value === true, "true"),
    check("security.rendererContractDigest", report,
      "observations.networkIsolation.contractDigestMatches", (value) => value === true, "true"),
    check("security.networkSourceManifestDigest", report,
      "observations.networkIsolation.sourceManifestDigest",
      (value) => value === sourceManifest?.exactByteSha256,
      "equals exact source manifest byte SHA-256"),
    customCheck("security.htmlObservationConsistency", htmlConsistent,
      "every HTML case has an exact expected error and failed access", htmlCases),
    customCheck("security.pptxObservationConsistency", pptxConsistent,
      "every PPTX case has an exact expected error", pptxCases),
    customCheck("security.crossOwnerObservationConsistency", crossOwnerConsistent,
      "actual HTTP read and write attacks are denied", crossOwner),
    customCheck("security.capabilityObservationConsistency", capabilityConsistent,
      "all capability tamper and replay cases pass", capability),
    customCheck("security.crossOwnerReadCountConsistency",
      actualCrossOwnerReads === report?.successfulCrossOwnerReadCount,
      "reported successful reads equal observations", actualCrossOwnerReads),
    customCheck("security.crossOwnerWriteCountConsistency",
      actualCrossOwnerWrites === report?.successfulCrossOwnerWriteCount,
      "reported successful writes equal observations", actualCrossOwnerWrites),
    customCheck("security.networkCountConsistency",
      accessCount("network") === report?.successfulNetworkAccessCount,
      "reported successful network access equals observations", accessCount("network")),
    customCheck("security.fileCountConsistency",
      accessCount("file") === report?.successfulFileAccessCount,
      "reported successful file access equals observations", accessCount("file")),
    customCheck("security.metadataCountConsistency",
      accessCount("metadata") === report?.successfulMetadataAccessCount,
      "reported successful metadata access equals observations", accessCount("metadata")),
    customCheck("security.networkNoneFlagConsistency",
      report?.networkNoneRenderVerified === report?.observations?.networkIsolation?.pass,
      "networkNoneRenderVerified equals networkIsolation.pass", report?.networkNoneRenderVerified),
    customCheck("security.sessionlessFlagConsistency",
      report?.sessionlessWorkerVerified
        === report?.observations?.workerBoundary?.sessionlessEnvironmentAllowlistVerified,
      "sessionlessWorkerVerified equals worker observation", report?.sessionlessWorkerVerified),
    customCheck("security.capabilityFlagConsistency",
      report?.operationDigestCapabilityBindingVerified === capabilityConsistent,
      "capability flag equals observations", report?.operationDigestCapabilityBindingVerified),
    customCheck("security.ownerFlagConsistency",
      report?.artifactOwnerBindingVerified
        === (crossOwnerConsistent && report?.observations?.ownerBoundArtifactControl?.ownerBound === true),
      "owner binding flag equals HTTP and artifact observations", report?.artifactOwnerBindingVerified),
    check("security.observationDigest", report, "provenance.observationDigest",
      (value) => value === expectedObservationDigest, expectedObservationDigest),
    check("security.provenanceDigest", report, "provenance.provenanceDigest",
      (value) => value === expectedProvenanceDigest, expectedProvenanceDigest),
    check("security.provenanceGitHead", report, "provenance.gitHead",
      (value) => value === sourceManifest?.sourceTreeCommit, "equals sourceManifest.sourceTreeCommit"),
    check("security.runnerDigest", report, "provenance.runnerSha256", isSha256, "sha256:<64 lowercase hex>"),
    check("security.runnerDigestVerified", report, "_verifiedProvenance.runnerDigestMatches",
      (value) => value === true, "true"),
    check("security.sourceDigestsVerified", report, "_verifiedProvenance.sourceDigestsMatch",
      (value) => value === true, "true"),
    customCheck("security.runnerIncludedInSourceManifest", runnerManifestMatches,
      "runner path and exact-byte SHA-256 appear in source manifest",
      provenance?.runnerPath),
    customCheck("security.sourcesIncludedInSourceManifest", sourcesManifestMatch,
      "every security source path and exact-byte SHA-256 appears in source manifest",
      Object.keys(provenance?.sourceDigests ?? {})),
    customCheck("security.reportedRunnerManifestConsistency",
      report?.provenance?.runnerIncludedInSourceManifest === runnerManifestMatches,
      "reported runner manifest inclusion equals direct manifest verification",
      report?.provenance?.runnerIncludedInSourceManifest),
    customCheck("security.reportedSourceManifestConsistency",
      report?.provenance?.sourcesMatchSourceManifest === sourcesManifestMatch,
      "reported source manifest inclusion equals direct manifest verification",
      report?.provenance?.sourcesMatchSourceManifest),
  ];
}

function observabilityChecks(controlled, reliability) {
  const checks = [
    check("observability.executedOperations", controlled, "observability.executedOperationCount",
      (value) => Number.isInteger(value) && value > 0, "integer > 0"),
    check("observability.nullMetrics", controlled, "observability.criticalNullMetricCount", (value) => value === 0, "0"),
    customCheck("observability.unavailableMetrics",
      controlled?.observability?.metricUnavailableCounts
        && Object.values(controlled.observability.metricUnavailableCounts)
          .every((value) => Number.isInteger(value) && value === 0),
      "all unavailable/not_applicable metric counts equal 0",
      controlled?.observability?.metricUnavailableCounts ?? null),
  ];
  for (const metric of REQUIRED_METRIC_SAMPLE_COUNTS) {
    checks.push(check(`observability.metricSamples.${metric}`, controlled,
      `observability.metricSampleCounts.${metric}`,
      (value) => Number.isInteger(value) && value >= controlled?.observability?.executedOperationCount,
      ">= executedOperationCount"));
  }
  checks.push(
    check("cost.publicJobs", reliability, "providerBudget.publicJobCount", (value) => Number.isInteger(value) && value > 0, "integer > 0"),
    check("cost.requestCapMissing", reliability, "providerBudget.jobsWithoutRequestCap", (value) => value === 0, "0"),
    check("cost.estimatedCapMissing", reliability, "providerBudget.jobsWithoutEstimatedCostCap", (value) => value === 0, "0"),
    check("cost.actualMetricMissing", reliability, "providerBudget.jobsWithoutActualCostMetric", (value) => value === 0, "0"),
    check("cost.requestCapExceeded", reliability, "providerBudget.jobsExceedingRequestCap", (value) => value === 0, "0"),
    check("cost.estimatedCapExceeded", reliability, "providerBudget.jobsExceedingEstimatedCostCap", (value) => value === 0, "0"),
    check("cost.actualCapExceeded", reliability, "providerBudget.jobsExceedingActualCostCap", (value) => value === 0, "0"),
  );
  return checks;
}

export function evaluateReleaseEvidence(reports = {}) {
  const sourceManifest = reports.sourceManifest;
  const evidenceIntegrity = gate(sourceChecks(reports, sourceManifest));
  const gates = {
    evidenceIntegrity,
    architecture: gate(architectureChecks(reports.controlledLoad), SCORE_TARGETS.architecture),
    businessReliability: gate(reliabilityChecks(reports.reliability, reports.crashRecovery,
      reports.powerpointReadback), SCORE_TARGETS.businessReliability),
    performance: gate(performanceChecks(reports.controlledLoad, reports.soakResource), SCORE_TARGETS.performance),
    security: gate(securityChecks(reports.maliciousCorpus, sourceManifest), SCORE_TARGETS.security),
    observability: gate(observabilityChecks(reports.controlledLoad, reports.reliability), SCORE_TARGETS.observability),
  };
  const scoredGates = Object.entries(SCORE_TARGETS).map(([name]) => gates[name]);
  const compositeScore = Math.floor(scoredGates.reduce((sum, item) => sum + item.awardedScore, 0)
    / scoredGates.length);
  const allGatesPass = Object.values(gates).every((item) => item.status === "PASS");
  const promotionReady = allGatesPass && compositeScore >= 85;
  const releaseCandidate = evidenceIntegrity.status === "PASS"
    ? reports.controlledLoad.releaseCandidate : null;

  return {
    schemaVersion: "public-service-release-gate-result/v1",
    status: promotionReady ? "PASS" : "FAIL",
    releaseCandidate,
    compositeScore,
    requiredCompositeScore: 85,
    promotionReady,
    deploymentAuthorized: false,
    deploymentAuthorizationReason: "separate_operator_authorization_required",
    gates,
    sourceManifest: {
      schemaVersion: sourceManifest?.schemaVersion ?? null,
      exactByteSha256: sourceManifest?.exactByteSha256 ?? null,
      sourceTreeCommit: sourceManifest?.sourceTreeCommit ?? null,
      currentDigestsVerified: sourceManifest?._verified?.currentDigestsMatch === true,
    },
    evidenceSources: Object.fromEntries(Object.keys(EVIDENCE).map((name) => [name, {
      schemaVersion: reports?.[name]?.schemaVersion ?? null,
      evidenceClass: reports?.[name]?.evidenceClass ?? null,
      releaseCandidate: reports?.[name]?.releaseCandidate ?? null,
      sourceDigest: reports?.[name]?.sourceDigest ?? null,
    }])),
  };
}

function parseArgs(argv) {
  const paths = {};
  for (let index = 0; index < argv.length; index += 1) {
    const option = argv[index];
    if (option === "--source-manifest") {
      paths.sourceManifest = argv[index + 1];
      index += 1;
      continue;
    }
    const match = Object.entries(EVIDENCE).find(([, config]) => config.option === option);
    if (!match) continue;
    paths[match[0]] = argv[index + 1];
    index += 1;
  }
  return paths;
}

function safeRepositoryPath(relativePath) {
  if (typeof relativePath !== "string" || !relativePath || relativePath.includes("\\")) return null;
  const normalized = path.posix.normalize(relativePath);
  if (normalized !== relativePath || normalized.startsWith("../") || path.posix.isAbsolute(normalized)) return null;
  const absolute = path.resolve(REPOSITORY_ROOT, ...normalized.split("/"));
  return absolute.startsWith(`${REPOSITORY_ROOT}${path.sep}`) ? absolute : null;
}

async function fileDigest(relativePath) {
  const absolute = safeRepositoryPath(relativePath);
  if (!absolute) return null;
  try {
    return `sha256:${createHash("sha256").update(await readFile(absolute)).digest("hex")}`;
  } catch {
    return null;
  }
}

async function verifyProvenance(parsed) {
  const provenance = parsed?.provenance;
  if (!provenance || typeof provenance !== "object") {
    return { runnerDigestMatches: false, sourceDigestsMatch: false };
  }
  const runnerDigest = await fileDigest(provenance.runnerPath);
  const sources = provenance.sourceDigests;
  let sourceDigestsMatch = Boolean(sources && typeof sources === "object"
    && !Array.isArray(sources) && Object.keys(sources).length > 0);
  if (sourceDigestsMatch) {
    for (const [sourcePath, expected] of Object.entries(sources)) {
      if (!isSha256(expected) || await fileDigest(sourcePath) !== expected) {
        sourceDigestsMatch = false;
        break;
      }
    }
  }
  return {
    runnerDigestMatches: isSha256(provenance.runnerSha256)
      && runnerDigest === provenance.runnerSha256,
    sourceDigestsMatch,
  };
}

async function loadSourceManifest(manifestPath) {
  if (!manifestPath) return null;
  try {
    const bytes = await readFile(path.resolve(manifestPath));
    const parsed = JSON.parse(bytes.toString("utf8"));
    const files = parsed?.files;
    const paths = Array.isArray(files) ? files.map((item) => item?.path) : [];
    const fileSetValid = Array.isArray(files) && files.length > 0
      && new Set(paths).size === files.length
      && files.every((item) => safeRepositoryPath(item?.path) && isSha256(item?.sha256));
    let currentDigestsMatch = fileSetValid;
    if (currentDigestsMatch) {
      for (const item of files) {
        if (await fileDigest(item.path) !== item.sha256) {
          currentDigestsMatch = false;
          break;
        }
      }
    }
    return {
      ...parsed,
      exactByteSha256: `sha256:${createHash("sha256").update(bytes).digest("hex")}`,
      _verified: { fileSetValid, currentDigestsMatch },
    };
  } catch (error) {
    return { loadError: error?.code || "invalid_json" };
  }
}

async function loadEvidence(evidencePath) {
  if (!evidencePath) return null;
  try {
    const bytes = await readFile(path.resolve(evidencePath));
    const parsed = JSON.parse(bytes.toString("utf8"));
    return {
      ...parsed,
      sourceDigest: `sha256:${createHash("sha256").update(bytes).digest("hex")}`,
      _verifiedProvenance: await verifyProvenance(parsed),
    };
  } catch (error) {
    return { loadError: error?.code || "invalid_json" };
  }
}

async function main(argv = process.argv.slice(2)) {
  const inputPaths = parseArgs(argv);
  const sourceManifest = await loadSourceManifest(inputPaths.sourceManifest);
  const reports = Object.fromEntries(await Promise.all(Object.keys(EVIDENCE).map(async (name) => [
    name,
    await loadEvidence(inputPaths[name]),
  ])));
  reports.sourceManifest = sourceManifest;
  const result = evaluateReleaseEvidence(reports);
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  if (!result.promotionReady) process.exitCode = 2;
}

if (path.resolve(process.argv[1] ?? "") === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stdout.write(`${JSON.stringify({
      schemaVersion: "public-service-release-gate-result/v1",
      status: "FAIL",
      promotionReady: false,
      deploymentAuthorized: false,
      reason: error?.message || String(error),
    }, null, 2)}\n`);
    process.exitCode = 2;
  });
}
