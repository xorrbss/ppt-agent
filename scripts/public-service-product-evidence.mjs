#!/usr/bin/env node

import { createHash } from "node:crypto";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SHA256 = /^sha256:[a-f0-9]{64}$/;
const COMMIT = /^[a-f0-9]{40}$/;
const RELEASE_CANDIDATE = /^(?:[a-f0-9]{40}|sha256:[a-f0-9]{64})$/;
const execFileAsync = promisify(execFile);
const REQUIRED_METRICS = Object.freeze([
  "queueWait", "queuePosition", "outlineDuration", "authorDuration", "illustrationDuration",
  "renderDuration", "qualityDuration", "checkpointDuration", "publicationDuration", "exportDuration",
  "providerInitialCalls", "providerRetryCalls", "providerLogicalOrdinal", "estimatedTokens", "actualTokens",
  "estimatedCost", "actualCost", "attemptNumber", "fencingOutcome", "chromeLaunch", "chromeRestart",
  "blockedRequest", "timeout", "crash", "workerPeakMemory", "workerPeakHandles", "artifactBytes",
  "cacheHit", "cacheMiss", "cacheEviction", "objectStoreLatency", "finalOutcome",
]);

function fail(message) { throw new Error(message); }
function finite(value) { return typeof value === "number" && Number.isFinite(value) && value >= 0; }
function percentile(values, quantile) {
  if (!values.length) return null;
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.ceil(sorted.length * quantile) - 1];
}
function normalizeKey(key) { return String(key).replace(/[^a-z0-9]/gi, "").toLowerCase(); }
function objectValues(root) {
  const output = [];
  const stack = [root];
  const visited = new Set();
  while (stack.length) {
    const value = stack.pop();
    if (!value || typeof value !== "object" || visited.has(value)) continue;
    visited.add(value);
    output.push(value);
    for (const nested of Object.values(value)) if (nested && typeof nested === "object") stack.push(nested);
  }
  return output;
}
function hasKeyValue(root, aliases, predicate = (value) => value !== null && value !== undefined) {
  const normalized = new Set(aliases.map(normalizeKey));
  return objectValues(root).some((object) => Object.entries(object)
    .some(([key, value]) => normalized.has(normalizeKey(key)) && predicate(value)));
}
function countResultsWith(results, aliases, predicate) {
  return results.filter((result) => hasKeyValue(result, aliases, predicate)).length;
}
function metricDelta(report, metricPrefix) {
  const values = report?.workspaceMetrics?.delta?.values;
  if (!values || typeof values !== "object") return null;
  const matches = Object.entries(values).filter(([key, value]) => key.startsWith(metricPrefix) && finite(value));
  return matches.length ? matches.reduce((sum, [, value]) => sum + value, 0) : null;
}
function publicGenerationMetric(report, metric, labels = "") {
  return metricDelta(report, `${metric}{${labels ? `${labels},` : ""}kind=generation}`)
    ?? metricDelta(report, `${metric}{kind=generation${labels ? `,${labels}` : ""}}`);
}
function publicStateDelta(report, state) {
  return publicGenerationMetric(report, "presenton_public_operations", `state=${state}`);
}
function reportAttempted(report) {
  return Number.isInteger(report?.requestedOperations)
    ? report.requestedOperations
    : (report?.results?.length || 0) + (report?.failures?.length || 0);
}
function scenarioKey(report) {
  const resultSlideCounts = [...new Set((report?.results || [])
    .map((result) => Number(result?.nSlides)).filter((value) => Number.isInteger(value)))];
  const slideCount = Number(report?.scenario?.nSlides ?? report?.scenario?.slideCount
    ?? (resultSlideCounts.length === 1 ? resultSlideCounts[0] : Number.NaN));
  const concurrency = Number(report?.scenario?.concurrency ?? report?.concurrency);
  if (![5, 10].includes(slideCount) || ![1, 2, 5, 10].includes(concurrency)) {
    fail("controlled reports must identify one C1/C2/C5/C10 x 5/10-slide scenario");
  }
  return `${concurrency}:${slideCount}`;
}
function allResults(reports) { return reports.flatMap((report) => report?.results || []); }
function allFailures(reports) { return reports.flatMap((report) => report?.failures || []); }
function observabilityMetricState(result, metric) {
  let unavailable = false;
  for (const object of objectValues(result)) {
    if (object?.schemaVersion !== "generation-observability/v1"
      || !object.metrics || typeof object.metrics !== "object") continue;
    const entry = object.metrics[metric];
    if (!entry || typeof entry !== "object") continue;
    if (entry.state === "observed" && entry.value !== null && entry.value !== undefined) return "observed";
    if (["unavailable", "not_applicable"].includes(entry.state)
      && typeof entry.reason === "string" && entry.reason.length > 0) unavailable = true;
  }
  return unavailable ? "unavailable" : "missing";
}
function hasObservabilityMetric(result, metric) {
  return observabilityMetricState(result, metric) === "observed";
}
function scenarioMetricUnavailableSamples(report, results, metric) {
  const contracted = results.filter((result) => observabilityMetricState(result, metric) === "unavailable").length;
  if (contracted > 0) return contracted;
  if (metric === "actualCost") {
    return publicGenerationMetric(report, "presenton_public_provider_actual_cost_unavailable_total") ?? 0;
  }
  return 0;
}
function observedPerOperationMetric(results, metric) {
  const mappings = {
    queueWait: ["queueWaitMs", "queue_wait_ms"],
    queuePosition: ["queuePosition", "queue_position"],
    outlineDuration: ["outline"], authorDuration: ["author"], illustrationDuration: ["illustration"],
    renderDuration: ["render"], qualityDuration: ["quality"], checkpointDuration: ["checkpoint"],
    publicationDuration: ["publication"], exportDuration: ["export"],
    providerLogicalOrdinal: ["logicalCallOrdinal", "logical_call_ordinal"],
    estimatedTokens: ["estimatedTokens", "estimated_tokens"], actualTokens: ["actualTokens", "actual_tokens"],
    attemptNumber: ["attemptNumber", "attempt_number"], fencingOutcome: ["fencingOutcome", "fenceOutcome", "fence_outcome"],
    objectStoreLatency: ["objectStoreLatencyMs", "object_store_latency_ms"],
  };
  const aliases = mappings[metric];
  if (!aliases) return 0;
  return countResultsWith(results, aliases, metric.endsWith("Duration") || metric.endsWith("Tokens")
    || metric === "queueWait" || metric === "queuePosition" || metric === "attemptNumber" || metric === "objectStoreLatency"
    ? finite : (value) => value !== null && value !== undefined);
}
function scenarioMetricSamples(report, results, metric) {
  const contracted = results.filter((result) => hasObservabilityMetric(result, metric)).length;
  if (contracted > 0) return contracted;
  const attempted = reportAttempted(report);
  if (["providerInitialCalls", "providerRetryCalls", "estimatedCost"].includes(metric)) {
    const kind = metric === "providerInitialCalls" ? ["presenton_public_provider_requests_total", "class=initial"]
      : metric === "providerRetryCalls" ? ["presenton_public_provider_requests_total", "class=retry"]
        : ["presenton_public_provider_cost_microunits", "class=estimated_consumed"];
    return publicGenerationMetric(report, kind[0], kind[1]) === null ? 0 : attempted;
  }
  if (metric === "actualCost") {
    const cost = publicGenerationMetric(report, "presenton_public_provider_cost_microunits", "class=actual_consumed");
    const unavailable = publicGenerationMetric(report, "presenton_public_provider_actual_cost_unavailable_total");
    return cost === null || unavailable === null ? 0 : Math.max(0, attempted - unavailable);
  }
  if (metric === "artifactBytes") return results.filter((result) => finite(result?.output?.bytes)).length;
  if (metric === "finalOutcome") return attempted;
  if (["workerPeakMemory", "workerPeakHandles"].includes(metric)) {
    const samples = Array.isArray(report?.resources?.samples) ? report.resources.samples : [];
    const field = metric === "workerPeakMemory" ? "privateBytes" : "handles";
    return samples.filter((sample) => Array.isArray(sample?.processes)
      && sample.processes.some((process) => finite(process?.[field]))).length;
  }
  const cacheCounter = {
    chromeLaunch: "browser_launches", chromeRestart: "browser_restarts", blockedRequest: "blocked_requests",
    timeout: "timeouts", crash: "browser_crashes", cacheHit: "cache_hits", cacheMiss: "cache_misses",
    cacheEviction: "cache_evictions",
  }[metric];
  if (cacheCounter) {
    return finite(report?.cacheTelemetry?.delta?.counters?.[cacheCounter]) ? 1 : 0;
  }
  return observedPerOperationMetric(results, metric);
}
function durableWorkerSamples(exportOperation) {
  const arrays = [];
  for (const object of objectValues(exportOperation)) {
    if (Array.isArray(object?.worker_resource_samples)) arrays.push(object.worker_resource_samples);
  }
  return arrays.length === 1 ? arrays[0] : null;
}
function resultExportOperations(result) {
  if (Array.isArray(result?.exportOperations) && result.exportOperations.length) return result.exportOperations;
  return result?.exportOperation ? [{ format: result.exportAs, exportOperationId: result.exportOperationId,
    artifactId: result.artifactId, exportOperation: result.exportOperation,
    singleFlightReceipt: result.singleFlightReceipt }] : [];
}
function resultOutputs(result) {
  if (Array.isArray(result?.outputs) && result.outputs.length) return result.outputs;
  return result?.output ? [{ format: result.exportAs, mediaType: result.exportAs === "pdf" ? "application/pdf"
    : "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  path: result.output.path, bytes: result.output.bytes, sha256: result.output.sha256,
  exportOperationId: result.exportOperationId, artifactId: result.artifactId }] : [];
}
function validDurableWorkerSample(sample, expectedLayer) {
  return sample && ["render", "export"].includes(sample.role)
    && sample.enforcementLayer === expectedLayer && sample.resourceLimitsEnforced === true
    && sample.recycledWithinBaseline === true && sample.finalProcessCount === 0
    && ["peakPrivateBytes", "peakHandleCount", "peakProcessCount", "peakCpuMillis"]
      .every((key) => Number.isInteger(sample[key]) && sample[key] >= 0)
    && ["memory", "cpu", "pids", "openFiles", "outputBytes", "wallClock"]
      .every((key) => Number.isInteger(sample?.resourceLimits?.[key]) && sample.resourceLimits[key] > 0);
}
function architecture(reports, runtimeEvidence) {
  const results = allResults(reports);
  const attempted = reports.reduce((sum, report) => sum + reportAttempted(report), 0);
  const complete = results.length > 0 && results.length === attempted;
  const attestation = runtimeEvidence?.resourceLimitAttestation;
  const expectedLayer = attestation?.verifiedDerived === true ? attestation.enforcementLayer : null;
  const modularPptCore = complete && results.every((result) => {
    const exports = resultExportOperations(result); const outputs = resultOutputs(result);
    return typeof result.operationId === "string" && exports.length > 0 && outputs.length === exports.length
      && exports.every((item) => typeof item.exportOperationId === "string"
        && item.exportOperationId !== result.operationId && typeof item.artifactId === "string")
      && outputs.every((item) => finite(item.bytes) && /^[a-f0-9]{64}$/.test(String(item.sha256 || "")));
  });
  const roleIsolatedWorkers = Boolean(expectedLayer) && complete && results.every((result) =>
    resultExportOperations(result).length > 0 && resultExportOperations(result).every((item) => {
      const samples = durableWorkerSamples(item.exportOperation);
      const roles = new Set((samples || []).map((sample) => sample?.role));
      return Array.isArray(samples) && samples.length >= 2 && roles.has("render") && roles.has("export")
        && samples.every((sample) => validDurableWorkerSample(sample, expectedLayer));
    }));
  const artifactRefObjectStorage = complete && results.every((result) => resultOutputs(result).length > 0
    && resultOutputs(result).every((item) => typeof item.artifactId === "string" && item.artifactId.length > 0
      && typeof item.path === "string" && !path.relative(REPO, path.resolve(item.path)).startsWith("..")
      && finite(item.bytes)));
  const clusterSingleFlight = complete && results.every((result) => resultExportOperations(result).length > 0
    && resultExportOperations(result).every((item) => item?.singleFlightReceipt?.duplicate === true
      && item.singleFlightReceipt.operationId === item.exportOperationId));
  return {
    modularPptCore, roleIsolatedWorkers, artifactRefObjectStorage, clusterSingleFlight,
  };
}
function buildControlledEvidence({ releaseCandidate, reports, runtimeEvidence = {}, provenance = null }) {
  const grouped = new Map();
  const derivedArchitecture = architecture(reports, runtimeEvidence);
  const resourceLimitsEnforced = derivedArchitecture.roleIsolatedWorkers
    && runtimeEvidence?.resourceLimitAttestation?.verifiedDerived === true;
  for (const report of reports) {
    const key = scenarioKey(report);
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(report);
  }
  const scenarios = [];
  for (const concurrency of [1, 2, 5, 10]) for (const slideCount of [5, 10]) {
    const cell = grouped.get(`${concurrency}:${slideCount}`) || [];
    const results = allResults(cell); const failures = allFailures(cell);
    const attempted = cell.reduce((sum, report) => sum + reportAttempted(report), 0);
    const starvationCount = failures.filter((failure) => failure?.classification === "request_timeout"
      || /starvation|capacity_waiting|timed out/i.test(String(failure?.error || ""))).length;
    scenarios.push({ concurrency, slideCount, attempted, succeeded: results.length,
      generationToPptxP95Ms: percentile(results.map((result) => result?.latencyMs).filter(finite), 0.95),
      starvationCount,
      resourceLimitsEnforced });
  }
  const executedOperationCount = reports.reduce((sum, report) => sum + reportAttempted(report), 0);
  const metricSampleCounts = Object.fromEntries(REQUIRED_METRICS.map((metric) => [metric,
    reports.reduce((sum, report) => sum + scenarioMetricSamples(report, report?.results || [], metric), 0)]));
  const metricUnavailableCounts = Object.fromEntries(REQUIRED_METRICS.map((metric) => [metric,
    reports.reduce((sum, report) => sum + scenarioMetricUnavailableSamples(report, report?.results || [], metric), 0)]));
  const criticalNullMetricCount = REQUIRED_METRICS.reduce((sum, metric) =>
    sum + Math.max(0, executedOperationCount - metricSampleCounts[metric]), 0);
  return {
    schemaVersion: "public-service-controlled-load-evidence/v1", releaseCandidate,
    evidenceClass: "product-path", generatedAt: new Date().toISOString(),
    provenance, architecture: derivedArchitecture, scenarios,
    resourceLimitAttestation: runtimeEvidence.resourceLimitAttestation || null,
    observability: { executedOperationCount, criticalNullMetricCount, metricSampleCounts,
      metricUnavailableCounts },
    rawReportCount: reports.length,
  };
}
function eventIdentityDuplicates(results, report = null) {
  const audited = report?.databaseAudit;
  if (audited?.schemaVersion === "public-service-load-database-audit/v1"
    && audited.operationCount === results.length
    && audited.operationsWithProviderCalls === results.length
    && Number.isInteger(audited.duplicateProviderLogicalCallCount)
    && audited.duplicateProviderLogicalCallCount >= 0) {
    return audited.duplicateProviderLogicalCallCount;
  }
  const identities = [];
  for (const result of results) for (const object of objectValues(result)) {
    const entries = Object.fromEntries(Object.entries(object).map(([key, value]) => [normalizeKey(key), value]));
    const ordinal = entries.logicalcallordinal;
    if (!Number.isInteger(ordinal)) continue;
    const stage = String(entries.stage || "").trim();
    const slide = entries.slideindex;
    if (stage && Number.isInteger(slide)) identities.push(`${result.operationId}:${stage}:${slide}:${ordinal}`);
  }
  return identities.length ? identities.length - new Set(identities).size : null;
}
function publicationDuplicates(results) {
  const observed = results.filter((result) => objectValues(result).some((object) =>
    object?.schemaVersion === "generation-observability/v1"
    && object?.metrics?.fencingOutcome?.state === "observed"
    && object.metrics.fencingOutcome.value === "publish_committed")).length;
  return observed === results.length ? 0 : null;
}
function exportDuplicates(results) {
  let duplicates = 0; let observed = 0;
  for (const result of results) {
    const operations = resultExportOperations(result);
    if (operations.length && operations.every((item) => item?.singleFlightReceipt?.duplicate === true
      && item.singleFlightReceipt.operationId === item.exportOperationId)) {
      observed += 1;
      continue;
    }
    const exportReceipt = result?.pptxExport || result?.pdfExport;
    if (exportReceipt && typeof exportReceipt.duplicate === "boolean"
      && typeof exportReceipt.operationId === "string") {
      observed += 1;
      duplicates += exportReceipt.duplicate ? 1 : 0;
      continue;
    }
    const identities = [];
    for (const object of objectValues(result)) for (const [key, value] of Object.entries(object)) {
      if (/^(exportidentity|exportidentitydigest|canonicalexportdigest)$/.test(normalizeKey(key)) && typeof value === "string") identities.push(value);
    }
    if (identities.length) observed += 1;
    duplicates += identities.length - new Set(identities).size;
  }
  return observed === results.length ? duplicates : null;
}
function failedArtifactCount(failures, aliases) {
  if (!failures.length) return 0;
  const audited = failures.filter((failure) => failure?.durableOperationAudit?.status === "observed");
  if (audited.length !== failures.length) return null;
  return audited.filter((failure) => hasKeyValue(failure.durableOperationAudit, aliases,
    (value) => typeof value === "string" && value.length > 0)).length;
}
function publicJobCount(report) {
  const states = ["succeeded", "failed", "cancelled", "budget_exhausted"]
    .map((state) => publicStateDelta(report, state));
  return states.some((value) => value === null) ? null : states.reduce((sum, value) => sum + value, 0);
}
function buildReliabilityEvidence({ releaseCandidate, report, provenance = null }) {
  const results = report?.results || []; const failures = report?.failures || [];
  const fixedDeckCount = reportAttempted(report);
  const jobs = publicJobCount(report);
  const unavailableCosts = publicGenerationMetric(report, "presenton_public_provider_actual_cost_unavailable_total");
  const budgetExhausted = publicStateDelta(report, "budget_exhausted");
  return {
    schemaVersion: "public-service-reliability-evidence/v1", releaseCandidate,
    evidenceClass: "product-path", generatedAt: new Date().toISOString(), provenance,
    fixedDeckCount, successfulDeckCount: results.length,
    e2eSuccessRate: fixedDeckCount ? results.length / fixedDeckCount : null,
    failedDecksWithPublishedArtifact: failedArtifactCount(failures, ["publishedArtifactId", "published_artifact_id"]),
    failedDecksWithExportArtifact: failedArtifactCount(failures, ["exportArtifactId", "export_artifact_id"]),
    casPublicationVerified: results.length > 0 && publicationDuplicates(results) !== null,
    duplicates: { providerLogicalCalls: eventIdentityDuplicates(results, report),
      publications: publicationDuplicates(results), exports: exportDuplicates(results) },
    providerBudget: {
      publicJobCount: jobs,
      jobsWithoutRequestCap: jobs === fixedDeckCount ? 0 : null,
      jobsWithoutEstimatedCostCap: jobs === fixedDeckCount ? 0 : null,
      jobsWithoutActualCostMetric: unavailableCosts,
      jobsExceedingRequestCap: budgetExhausted,
      jobsExceedingEstimatedCostCap: budgetExhausted,
      jobsExceedingActualCostCap: budgetExhausted,
    },
  };
}
function artifactManifest({ releaseCandidate, reports }) {
  const artifacts = reports.flatMap((report) => (report?.results || []).flatMap((result) => resultOutputs(result).map((output) => {
    const absolute = path.resolve(String(output?.path || ""));
    const relative = path.relative(REPO, absolute);
    if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) fail("artifact path must stay inside the RC worktree");
    const digest = String(output?.sha256 || "").toLowerCase();
    if (!/^[a-f0-9]{64}$/.test(digest) || !finite(output?.bytes)) fail("artifact digest/size is invalid");
    return { deckId: result.deckId,
      slideCount: Number(result?.nSlides ?? report?.scenario?.nSlides ?? report?.scenario?.slideCount ?? 0) || null,
      operationId: result.operationId || null, presentationId: result.presentationId || null,
      exportOperationId: output.exportOperationId || null, artifactId: output.artifactId || null,
      mediaType: output.mediaType || (output.format === "pdf" ? "application/pdf"
        : "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
      path: relative.replaceAll("\\", "/"), bytes: output.bytes, sha256: `sha256:${digest}`,
      completedAt: result.completedAt || null };
  })));
  return { schemaVersion: "public-service-product-artifacts/v1", releaseCandidate,
    evidenceClass: "product-path", generatedAt: new Date().toISOString(), artifacts };
}
export function buildProductEvidence({ releaseCandidate, controlledReports, reliabilityReport, runtimeEvidence = {} }) {
  if (!RELEASE_CANDIDATE.test(String(releaseCandidate || ""))) {
    fail("release candidate must be a full lowercase commit SHA or immutable sha256 source-manifest digest");
  }
  if (!Array.isArray(controlledReports) || !controlledReports.length) fail("controlled reports are required");
  if (!reliabilityReport || typeof reliabilityReport !== "object") fail("reliability report is required");
  if (runtimeEvidence.releaseCandidate && runtimeEvidence.releaseCandidate !== releaseCandidate) fail("runtime evidence RC mismatch");
  const provenance = {
    sourceTreeCommit: runtimeEvidence.sourceTreeCommit || null,
    sourceManifestPath: runtimeEvidence.sourceManifestPath || null,
    sourceManifestSha256: runtimeEvidence.sourceManifestSha256 || null,
  };
  if (SHA256.test(releaseCandidate) && (
    !COMMIT.test(String(provenance.sourceTreeCommit || ""))
    || provenance.sourceManifestSha256 !== releaseCandidate
    || typeof provenance.sourceManifestPath !== "string"
    || !provenance.sourceManifestPath
  )) fail("source-manifest release candidate requires verified source provenance");
  const genericProvenance = runtimeEvidence.provenance || null;
  return {
    controlledLoad: buildControlledEvidence({ releaseCandidate, reports: controlledReports, runtimeEvidence, provenance: genericProvenance }),
    reliability: buildReliabilityEvidence({ releaseCandidate, report: reliabilityReport, provenance: genericProvenance }),
    artifacts: { ...artifactManifest({ releaseCandidate, reports: [reliabilityReport, ...controlledReports] }),
      provenance: genericProvenance, ...provenance },
  };
}
function parseArgs(argv) {
  const args = { controlled: [] };
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index]; const value = argv[index + 1];
    if (!key.startsWith("--") || value === undefined) fail(`invalid argument: ${key}`);
    if (key === "--controlled") args.controlled.push(value);
    else args[key.slice(2)] = value;
    index += 1;
  }
  return args;
}
async function atomicJson(target, payload) {
  await mkdir(path.dirname(target), { recursive: true });
  const temporary = `${target}.${process.pid}.tmp`;
  await writeFile(temporary, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  await rename(temporary, target);
}
async function verifiedRuntimeEvidence(releaseCandidate, sourceManifestPath, resourceAttestationPath, reportPaths, runtimeEvidence) {
  if (!SHA256.test(releaseCandidate)) return runtimeEvidence;
  const absolute = path.resolve(String(sourceManifestPath || ""));
  const relative = path.relative(REPO, absolute);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) {
    fail("runtime evidence sourceManifestPath must stay inside the RC worktree");
  }
  const bytes = await readFile(absolute);
  const digest = `sha256:${createHash("sha256").update(bytes).digest("hex")}`;
  if (digest !== releaseCandidate) fail("source manifest bytes do not match release candidate");
  let manifest;
  try { manifest = JSON.parse(bytes.toString("utf8")); } catch { fail("source manifest JSON is invalid"); }
  if (manifest?.schemaVersion !== "public-service-source-manifest/v1"
    || !COMMIT.test(String(manifest?.sourceTreeCommit || ""))
    || !Array.isArray(manifest?.files) || !manifest.files.length) {
    fail("source manifest contract is invalid");
  }
  const { stdout: headOutput } = await execFileAsync("git", ["rev-parse", "HEAD"], { cwd: REPO, windowsHide: true });
  if (manifest.sourceTreeCommit !== headOutput.trim()) fail("source manifest tree commit does not match HEAD");
  const sourcePaths = new Set();
  const sourceDigests = new Map();
  for (const entry of manifest.files) {
    const sourcePath = String(entry?.path || "").replaceAll("\\", "/");
    if (!sourcePath || sourcePaths.has(sourcePath) || !SHA256.test(String(entry?.sha256 || ""))) {
      fail("source manifest file entry is invalid or duplicated");
    }
    const sourceAbsolute = path.resolve(REPO, sourcePath);
    const sourceRelative = path.relative(REPO, sourceAbsolute);
    if (sourceRelative.startsWith("..") || path.isAbsolute(sourceRelative)
      || /^(?:artifacts|app_data|\.git)(?:\/|$)/.test(sourcePath)) {
      fail("source manifest file path is outside the source boundary");
    }
    const sourceBytes = await readFile(sourceAbsolute);
    const sourceDigest = `sha256:${createHash("sha256").update(sourceBytes).digest("hex")}`;
    if (sourceDigest !== entry.sha256) fail(`source manifest file digest mismatch: ${sourcePath}`);
    sourcePaths.add(sourcePath);
    sourceDigests.set(sourcePath, sourceDigest);
  }
  for (const required of (
    ["scripts/authored-vnext-controlled-load.mjs", "scripts/public-service-product-evidence.mjs",
      "scripts/public-service-load-auth-fixture.py", "scripts/public-service-resource-attestation.mjs"]
  )) if (!sourcePaths.has(required)) fail(`source manifest omits evidence producer: ${required}`);
  const runnerPath = "scripts/public-service-product-evidence.mjs";
  const runnerEntry = manifest.files.find((entry) => String(entry?.path || "").replaceAll("\\", "/") === runnerPath);
  const resourceAbsolute = path.resolve(String(resourceAttestationPath || ""));
  const resourceRelative = path.relative(REPO, resourceAbsolute);
  if (!resourceRelative || resourceRelative.startsWith("..") || path.isAbsolute(resourceRelative)) {
    fail("resource attestation path must stay inside the RC worktree");
  }
  const resourceBytes = await readFile(resourceAbsolute);
  let resource;
  try { resource = JSON.parse(resourceBytes.toString("utf8")); } catch { fail("resource attestation JSON is invalid"); }
  const controls = Array.isArray(resource?.enforcedControls)
    ? new Set(resource.enforcedControls)
    : new Set(Object.entries(resource?.enforcedControls || {}).filter(([, value]) => value === true).map(([key]) => key));
  const requiredControls = ["memory", "cpu", "pids", "openFiles", "outputBytes", "wallClock"];
  const samples = Array.isArray(resource?.actualSamples) ? resource.actualSamples : [];
  const roles = new Set(samples.map((sample) => sample?.role));
  const declaredRoles = new Set(Array.isArray(resource?.workerRoles) ? resource.workerRoles : []);
  const attestationRunner = String(resource?.provenance?.runnerPath || "").replaceAll("\\", "/");
  const expectedLayer = process.platform === "win32" ? "windows-job-object" : "posix-rlimit";
  const expectedReportInputs = new Map();
  for (const reportPath of reportPaths) {
    const reportAbsolute = path.resolve(reportPath);
    const reportRelative = path.relative(REPO, reportAbsolute);
    if (!reportRelative || reportRelative.startsWith("..") || path.isAbsolute(reportRelative)) {
      fail("controlled report path must stay inside the RC worktree");
    }
    const normalized = reportRelative.replaceAll("\\", "/");
    if (expectedReportInputs.has(normalized)) fail("controlled report path is duplicated");
    expectedReportInputs.set(normalized,
      `sha256:${createHash("sha256").update(await readFile(reportAbsolute)).digest("hex")}`);
  }
  const attestedReportInputs = new Map();
  for (const input of Array.isArray(resource?.inputReports) ? resource.inputReports : []) {
    const inputPath = String(input?.path || "").replaceAll("\\", "/");
    if (!inputPath || attestedReportInputs.has(inputPath) || !SHA256.test(String(input?.sha256 || ""))
      || !Number.isInteger(input?.operationCount) || input.operationCount <= 0) {
      fail("resource attestation input report binding is invalid");
    }
    attestedReportInputs.set(inputPath, input.sha256);
  }
  const reportsMatch = expectedReportInputs.size === attestedReportInputs.size
    && [...expectedReportInputs].every(([reportPath, reportDigest]) => attestedReportInputs.get(reportPath) === reportDigest);
  if (resource?.schemaVersion !== "public-service-resource-limit-attestation/v1"
    || resource.releaseCandidate !== releaseCandidate
    || resource.sourceDigest !== manifest.sourceTreeCommit
    || resource.evidenceClass !== "product-path" || resource.valid !== true
    || resource.enforced !== true || resource.resourceLimitsEnforced !== true
    || resource.enforcementLayer !== expectedLayer
    || requiredControls.some((control) => !controls.has(control))
    || !declaredRoles.has("render") || !declaredRoles.has("export")
    || !roles.has("render") || !roles.has("export") || !samples.length
    || samples.some((sample) => !["render", "export"].includes(sample?.role)
      || !Number.isInteger(sample?.peakPrivateBytes) || sample.peakPrivateBytes < 0
      || !Number.isInteger(sample?.peakHandleCount) || sample.peakHandleCount < 0
      || !Number.isInteger(sample?.peakProcessCount) || sample.peakProcessCount < 0
      || !Number.isInteger(sample?.peakCpuMillis) || sample.peakCpuMillis < 0
      || sample?.enforcementLayer !== expectedLayer
      || requiredControls.some((control) => !Number.isInteger(sample?.resourceLimits?.[control])
        || sample.resourceLimits[control] <= 0)
      || sample?.finalProcessCount !== 0 || sample?.recycledWithinBaseline !== true
      || sample?.resourceLimitsEnforced !== true)
    || !reportsMatch
    || resource?.provenance?.sourceManifestDigest !== releaseCandidate
    || attestationRunner !== "scripts/public-service-resource-attestation.mjs"
    || !sourceDigests.has(attestationRunner)
    || resource?.provenance?.runnerSha256 !== sourceDigests.get(attestationRunner)) {
    fail("resource attestation is not exact product-path evidence for this RC");
  }
  const resourceDigest = `sha256:${createHash("sha256").update(resourceBytes).digest("hex")}`;
  return { ...runtimeEvidence, sourceTreeCommit: manifest.sourceTreeCommit,
    sourceManifestPath: relative.replaceAll("\\", "/"), sourceManifestSha256: digest,
    provenance: { runnerPath, runnerSha256: runnerEntry.sha256, sourceManifestDigest: digest },
    resourceLimitAttestation: { verifiedDerived: true, path: resourceRelative.replaceAll("\\", "/"),
      digest: resourceDigest, runnerPath: attestationRunner, sampleCount: samples.length,
      reportCount: attestedReportInputs.size, roles: [...roles].sort(), enforcementLayer: expectedLayer } };
}
async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args["release-candidate"] || !args["source-manifest"] || !args["resource-attestation"]
    || !args["output-directory"] || !args.reliability || !args.controlled.length) {
    fail("required: --release-candidate <source-manifest-digest> --source-manifest <json> --resource-attestation <json> --output-directory <dir> --reliability <json> --controlled <json> [--controlled ...] [--runtime-evidence <json>]");
  }
  const outputDirectory = path.resolve(args["output-directory"]);
  const relative = path.relative(path.join(REPO, "artifacts"), outputDirectory);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) fail("output directory must be below RC artifacts/");
  const readJson = async (file) => JSON.parse(await readFile(path.resolve(file), "utf8"));
  const runtimeEvidence = await verifiedRuntimeEvidence(args["release-candidate"], args["source-manifest"], args["resource-attestation"],
    [...args.controlled, args.reliability],
    args["runtime-evidence"] ? await readJson(args["runtime-evidence"]) : {});
  const evidence = buildProductEvidence({ releaseCandidate: args["release-candidate"],
    controlledReports: await Promise.all(args.controlled.map(readJson)), reliabilityReport: await readJson(args.reliability),
    runtimeEvidence });
  await Promise.all([
    atomicJson(path.join(outputDirectory, "controlled-load.json"), evidence.controlledLoad),
    atomicJson(path.join(outputDirectory, "reliability.json"), evidence.reliability),
    atomicJson(path.join(outputDirectory, "product-artifacts.json"), evidence.artifacts),
  ]);
  process.stdout.write(`${JSON.stringify({ outputDirectory, controlledLoad: evidence.controlledLoad.observability,
    reliability: { fixedDeckCount: evidence.reliability.fixedDeckCount, successfulDeckCount: evidence.reliability.successfulDeckCount } })}\n`);
}
if (path.resolve(process.argv[1] || "") === fileURLToPath(import.meta.url)) {
  main().catch((error) => { console.error(error?.stack || error); process.exitCode = 1; });
}
