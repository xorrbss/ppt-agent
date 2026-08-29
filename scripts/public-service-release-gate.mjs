#!/usr/bin/env node

import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const EVIDENCE = Object.freeze({
  controlledLoad: {
    option: "--controlled-load",
    schemaVersion: "public-service-controlled-load-evidence/v1",
  },
  reliability: {
    option: "--reliability",
    schemaVersion: "public-service-reliability-evidence/v1",
  },
  crashRecovery: {
    option: "--crash-recovery",
    schemaVersion: "public-service-crash-recovery-evidence/v1",
  },
  maliciousCorpus: {
    option: "--malicious-corpus",
    schemaVersion: "public-service-malicious-corpus-evidence/v1",
  },
  powerpointReadback: {
    option: "--powerpoint-readback",
    schemaVersion: "public-service-powerpoint-readback-evidence/v1",
  },
  soakResource: {
    option: "--soak-resource",
    schemaVersion: "public-service-soak-resource-evidence/v1",
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

function sourceChecks(reports) {
  const checks = [];
  for (const [name, config] of Object.entries(EVIDENCE)) {
    const report = reports?.[name];
    checks.push(check(`${name}.schema`, report, "schemaVersion", (value) => value === config.schemaVersion,
      config.schemaVersion));
    checks.push(check(`${name}.releaseCandidate`, report, "releaseCandidate",
      (value) => typeof value === "string" && value.trim().length > 0, "non-empty RC identifier"));
    checks.push(check(`${name}.productPath`, report, "evidenceClass",
      (value) => value === "product-path", "product-path (fixture/package-smoke are not promotable)"));
  }

  const releaseCandidates = Object.keys(EVIDENCE)
    .map((name) => reports?.[name]?.releaseCandidate)
    .filter(isPresent);
  const sameRc = releaseCandidates.length === Object.keys(EVIDENCE).length
    && new Set(releaseCandidates).size === 1;
  checks.push(customCheck("sameReleaseCandidate", sameRc, "all evidence from exactly one RC",
    [...new Set(releaseCandidates)], "release_candidate_mismatch"));
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

function securityChecks(report) {
  return [
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
  ];
}

function observabilityChecks(controlled, reliability) {
  const checks = [
    check("observability.executedOperations", controlled, "observability.executedOperationCount",
      (value) => Number.isInteger(value) && value > 0, "integer > 0"),
    check("observability.nullMetrics", controlled, "observability.criticalNullMetricCount", (value) => value === 0, "0"),
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
  const evidenceIntegrity = gate(sourceChecks(reports));
  const gates = {
    evidenceIntegrity,
    architecture: gate(architectureChecks(reports.controlledLoad), SCORE_TARGETS.architecture),
    businessReliability: gate(reliabilityChecks(reports.reliability, reports.crashRecovery,
      reports.powerpointReadback), SCORE_TARGETS.businessReliability),
    performance: gate(performanceChecks(reports.controlledLoad, reports.soakResource), SCORE_TARGETS.performance),
    security: gate(securityChecks(reports.maliciousCorpus), SCORE_TARGETS.security),
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
    const match = Object.entries(EVIDENCE).find(([, config]) => config.option === option);
    if (!match) continue;
    paths[match[0]] = argv[index + 1];
    index += 1;
  }
  return paths;
}

async function loadEvidence(evidencePath) {
  if (!evidencePath) return null;
  try {
    const bytes = await readFile(path.resolve(evidencePath));
    const parsed = JSON.parse(bytes.toString("utf8"));
    return { ...parsed, sourceDigest: `sha256:${createHash("sha256").update(bytes).digest("hex")}` };
  } catch (error) {
    return { loadError: error?.code || "invalid_json" };
  }
}

async function main(argv = process.argv.slice(2)) {
  const inputPaths = parseArgs(argv);
  const reports = Object.fromEntries(await Promise.all(Object.keys(EVIDENCE).map(async (name) => [
    name,
    await loadEvidence(inputPaths[name]),
  ])));
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
