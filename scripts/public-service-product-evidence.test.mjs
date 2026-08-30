import assert from "node:assert/strict";
import test from "node:test";

import { parsePrometheusMetrics, workspaceMetricsDelta } from "./authored-vnext-controlled-load.mjs";
import { buildProductEvidence } from "./public-service-product-evidence.mjs";

const rc = "b3c77d6650929c667ff38e06c9d674db596bf915";
const metricText = (successes, initial, retry, estimated, actual, unavailable) => `
presenton_public_metrics_available 1
presenton_public_operations{kind="generation",state="succeeded"} ${successes}
presenton_public_operations{kind="generation",state="failed"} 0
presenton_public_operations{kind="generation",state="cancelled"} 0
presenton_public_operations{kind="generation",state="budget_exhausted"} 0
presenton_public_provider_requests_total{kind="generation",class="initial"} ${initial}
presenton_public_provider_requests_total{kind="generation",class="retry"} ${retry}
presenton_public_provider_cost_microunits{kind="generation",class="estimated_consumed"} ${estimated}
presenton_public_provider_cost_microunits{kind="generation",class="actual_consumed"} ${actual}
presenton_public_provider_actual_cost_unavailable_total{kind="generation"} ${unavailable}
`;
function result(id, latencyMs = 1000) {
  return { deckId: id, operationId: `operation-${id}`, presentationId: `presentation-${id}`, latencyMs,
    completedAt: "2026-08-30T00:00:00.000Z", exportAs: "pptx",
    output: { path: `artifacts/test/${id}.pptx`, bytes: 123, sha256: "a".repeat(64) },
    phaseTimingsMs: { outline: 1, author: 2, repair: 0, checkpoint: 1, publication: 1, export: 1 },
    operation: { result: { generation_timing_receipt: { observability: {
      schemaVersion: "generation-observability/v1",
      metrics: { fencingOutcome: { state: "observed", value: "publish_committed" } },
    } } } },
    stages: [{ result: { fence_outcome: "publish_committed", attempt_number: 1,
      stage: "author", slide_index: 0, logical_call_ordinal: 0 } }] };
}
function report(concurrency, slideCount, count) {
  const before = { schemaVersion: "public-workspace-metrics-snapshot/v1", values: parsePrometheusMetrics(metricText(0, 0, 0, 0, 0, 0)) };
  const after = { schemaVersion: "public-workspace-metrics-snapshot/v1", values: parsePrometheusMetrics(metricText(count, count * 2, 0, 20, 18, 0)) };
  return { scenario: { concurrency, nSlides: slideCount }, concurrency, requestedOperations: count,
    results: Array.from({ length: count }, (_, index) => ({
      ...result(`${concurrency}-${slideCount}-${index}`), nSlides: slideCount,
    })), failures: [],
    resources: { samples: [] }, cacheTelemetry: { delta: { counters: {} } },
    workspaceMetrics: { before, after, delta: workspaceMetricsDelta(before, after) } };
}

test("schema-v1 controlled reports derive their scenario from durable result fields", () => {
  const schemaOne = report(10, 10, 10);
  delete schemaOne.scenario;
  const evidence = buildProductEvidence({ releaseCandidate: rc,
    controlledReports: [schemaOne], reliabilityReport: report(5, 5, 1) });
  const derived = evidence.controlledLoad.scenarios.find((item) =>
    item.concurrency === 10 && item.slideCount === 10);
  assert.equal(derived?.attempted, 10);
});

test("product evidence is fail-closed for unproven architecture and missing per-operation metrics", () => {
  const reports = [];
  for (const concurrency of [1, 2, 5, 10]) for (const slides of [5, 10]) reports.push(report(concurrency, slides, concurrency));
  const reliability = report(5, 5, 20);
  const selfAsserted = {
    evidenceClass: "product-path",
    architectureProof: Object.fromEntries(["modularPptCore", "roleIsolatedWorkers",
      "artifactRefObjectStorage", "clusterSingleFlight"].map((field) =>
      [field, { verified: true, digest: `sha256:${"f".repeat(64)}` }])),
    performanceProof: { resourceLimitsEnforced: { verified: true, digest: `sha256:${"e".repeat(64)}` } },
  };
  const evidence = buildProductEvidence({ releaseCandidate: rc, controlledReports: reports,
    reliabilityReport: reliability, runtimeEvidence: selfAsserted });
  assert.equal(evidence.controlledLoad.scenarios.length, 8);
  assert.equal(evidence.controlledLoad.scenarios.find((item) => item.concurrency === 10 && item.slideCount === 10).attempted, 10);
  assert.equal(evidence.controlledLoad.architecture.modularPptCore, false);
  assert.deepEqual(evidence.controlledLoad.architecture, {
    modularPptCore: false, roleIsolatedWorkers: false,
    artifactRefObjectStorage: false, clusterSingleFlight: false,
  });
  assert.ok(evidence.controlledLoad.scenarios.every((item) => item.resourceLimitsEnforced === false));
  assert.ok(evidence.controlledLoad.observability.criticalNullMetricCount > 0);
  assert.equal(evidence.controlledLoad.observability.metricSampleCounts.actualCost, 36);
  assert.equal(evidence.reliability.fixedDeckCount, 20);
  assert.equal(evidence.reliability.successfulDeckCount, 20);
  assert.equal(evidence.reliability.providerBudget.jobsWithoutActualCostMetric, 0);
  assert.equal(evidence.reliability.duplicates.publications, 0);
  assert.equal(evidence.reliability.duplicates.exports, null);
  assert.equal(evidence.artifacts.artifacts.length, 56);
  assert.ok(evidence.artifacts.artifacts.every((item) => !pathIsAbsolute(item.path)));
});

function pathIsAbsolute(value) { return /^[A-Za-z]:[\\/]|^[\\/]{1,2}/.test(value); }

test("Prometheus parser canonicalizes labels and delta retains only public metrics", () => {
  const before = { values: parsePrometheusMetrics(metricText(1, 2, 0, 3, 2, 1)) };
  const after = { values: parsePrometheusMetrics(metricText(3, 8, 1, 9, 7, 1)) };
  const delta = workspaceMetricsDelta(before, after);
  assert.equal(delta.values["presenton_public_operations{kind=generation,state=succeeded}"], 2);
  assert.equal(delta.values["presenton_public_provider_requests_total{class=initial,kind=generation}"], 6);
});

test("unavailable and not_applicable observability states remain release-blocking", () => {
  const unavailable = report(1, 5, 1);
  const metrics = unavailable.results[0].operation.result.generation_timing_receipt.observability.metrics;
  metrics.queueWait = { state: "unavailable", reason: "queue receipt missing" };
  metrics.renderDuration = { state: "not_applicable", reason: "render was not observed" };
  const evidence = buildProductEvidence({ releaseCandidate: rc, controlledReports: [unavailable],
    reliabilityReport: report(5, 5, 1) });
  assert.equal(evidence.controlledLoad.observability.metricSampleCounts.queueWait, 0);
  assert.equal(evidence.controlledLoad.observability.metricSampleCounts.renderDuration, 0);
  assert.equal(evidence.controlledLoad.observability.metricUnavailableCounts.queueWait, 1);
  assert.equal(evidence.controlledLoad.observability.metricUnavailableCounts.renderDuration, 1);
  assert.ok(evidence.controlledLoad.observability.criticalNullMetricCount >= 2);
});

test("product artifacts emit one workspace PPTX and PDF pair for the same deck lineage", () => {
  const paired = report(1, 5, 1);
  const target = paired.results[0];
  target.exportOperations = ["pptx", "pdf"].map((format) => ({ format,
    exportOperationId: `${format}-operation`, artifactId: `${format}-artifact`,
    exportOperation: {}, singleFlightReceipt: { duplicate: true, operationId: `${format}-operation` } }));
  target.outputs = [
    { format: "pptx", mediaType: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
      path: "artifacts/test/paired.pptx", bytes: 111, sha256: "b".repeat(64),
      exportOperationId: "pptx-operation", artifactId: "pptx-artifact" },
    { format: "pdf", mediaType: "application/pdf", path: "artifacts/test/paired.pdf", bytes: 222,
      sha256: "c".repeat(64), exportOperationId: "pdf-operation", artifactId: "pdf-artifact" },
  ];
  const evidence = buildProductEvidence({ releaseCandidate: rc, controlledReports: [paired],
    reliabilityReport: report(5, 5, 1) });
  const pair = evidence.artifacts.artifacts.filter((artifact) => artifact.deckId === target.deckId);
  assert.equal(pair.length, 2);
  assert.deepEqual(new Set(pair.map((artifact) => artifact.mediaType)), new Set([
    "application/vnd.openxmlformats-officedocument.presentationml.presentation", "application/pdf",
  ]));
  assert.ok(pair.every((artifact) => artifact.presentationId === target.presentationId));
  assert.equal(new Set(pair.map((artifact) => artifact.artifactId)).size, 2);
});
