import assert from "node:assert/strict";
import test from "node:test";

import { durableOperationAudit, resolveProcessTree } from "./authored-vnext-controlled-load.mjs";
import { requestFailureSummary, retryAudit } from "./authored-vnext-controlled-load-retry-audit.mjs";

test("terminal failure audit uses durable API events and preserves unavailable fields", async () => {
  const operationId = "3d440ee5-34cf-4978-aa18-fbf2e3d134d9";
  const presentationId = "ec0f6d21-17e1-4bd0-ae5c-d0d14fd8d2c7";
  const calls = [];
  const requestFn = async (url) => {
    calls.push(url);
    if (url.includes("/async-tasks/status/")) return { json: { presentation_id: presentationId } };
    if (url.includes("/timeline?")) return { json: { items: [{ operation_id: operationId, retryCount: 1, providerAttemptCount: 4, attempt_number: 2, recoveryStatus: "recovered", events: [] }] } };
    if (url.includes("/events?")) return { json: { events: [{ operation_id: operationId, type: "operation_requeued", recoveryReason: "lease_expired", recoveryStatus: "scheduled" }] } };
    throw new Error(`unexpected URL ${url}`);
  };

  const observed = await durableOperationAudit({
    baseUrl: "http://presenton.test", taskId: "task-1", operationId, headers: {}, requestFn,
  });
  assert.equal(calls.length, 3);
  assert.equal(observed.status, "observed");
  assert.equal(observed.presentationId, presentationId);
  assert.equal(observed.retryAudit.retryCount, 1);
  assert.equal(observed.retryAudit.maximumAttemptNumber, 2);
  assert.equal(observed.retryAudit.providerAttemptCount, 4);
  assert.equal(observed.retryAudit.recoveryStatus, "recovered");
  assert.deepEqual(observed.retryAudit.recoveryReasons, ["lease_expired"]);
  assert.equal(observed.retryAudit.status, "available");

  const summary = requestFailureSummary([{ classification: "operation_failed", retryAuditDetail: observed.retryAudit }]);
  assert.equal(summary.hiddenInternalRetries, 1);
  assert.equal(summary.automaticRetries, 1);
  assert.equal(summary.providerAttempts, 4);
  assert.equal(summary.retryAuditStatus, "observed");

  const unavailable = retryAudit([{ operation_id: operationId, type: "failed" }]);
  assert.equal(unavailable.retryCount, null);
  assert.equal(unavailable.maximumAttemptNumber, null);
  assert.equal(unavailable.providerAttemptCount, null);
  assert.equal(unavailable.recoveryReasons, null);
  assert.equal(unavailable.status, "unavailable");
  const unavailableSummary = requestFailureSummary([{ classification: "operation_failed", retryAuditDetail: unavailable }]);
  assert.equal(unavailableSummary.hiddenInternalRetries, null);
  assert.equal(unavailableSummary.automaticRetries, null);
  assert.equal(unavailableSummary.providerAttempts, null);
  assert.equal(unavailableSummary.retryAuditStatus, "unavailable");
});

test("process tree attribution includes only same-snapshot descendants", () => {
  const tree = resolveProcessTree([
    { pid: 100, parentPid: 1, imageName: "python.exe", workingSetBytes: 100 },
    { pid: 110, parentPid: 100, imageName: "chrome.exe", workingSetBytes: 50 },
    { pid: 111, parentPid: 110, imageName: "chrome.exe", workingSetBytes: 25 },
    { pid: 200, parentPid: 1, imageName: "chrome.exe", workingSetBytes: 999 },
  ], [100]);

  assert.equal(tree.status, "assessed");
  assert.equal(tree.jobExclusiveAttribution, "unverified");
  assert.deepEqual(tree.processes.map((process) => process.pid), [100, 110, 111]);
  assert.deepEqual(tree.processes.map((process) => process.descendantDepth), [0, 1, 2]);
  assert.equal(tree.processes[1].processRole, "chrome");
});
