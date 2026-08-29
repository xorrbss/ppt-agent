import assert from "node:assert/strict";
import test from "node:test";

import { WeightedExportAdmission } from "./export-admission.ts";

test("weighted admission bounds mixed fan-out and drains FIFO", async () => {
  const admission = new WeightedExportAdmission(3);
  const first = await admission.acquire(2);
  const secondPromise = admission.acquire(2);
  const thirdPromise = admission.acquire(1);

  assert.deepEqual(admission.snapshot(), {
    capacityWeight: 3,
    activeWeight: 2,
    activeProcesses: 1,
    queuedRequests: 2,
    peakActiveWeight: 2,
    peakActiveProcesses: 1,
    admittedRequests: 1,
    totalQueueWaitMs: admission.snapshot().totalQueueWaitMs,
    maximumQueueWaitMs: admission.snapshot().maximumQueueWaitMs,
  });

  first.release();
  const second = await secondPromise;
  const third = await thirdPromise;
  const active = admission.snapshot();
  assert.equal(active.activeWeight, 3);
  assert.equal(active.activeProcesses, 2);
  assert.equal(active.queuedRequests, 0);
  assert.equal(active.peakActiveWeight, 3);
  assert.equal(active.peakActiveProcesses, 2);

  second.release();
  second.release();
  third.release();
  assert.equal(admission.snapshot().activeWeight, 0);
  assert.equal(admission.snapshot().activeProcesses, 0);
});

test("weighted admission rejects impossible weights", async () => {
  const admission = new WeightedExportAdmission(2);
  await assert.rejects(admission.acquire(3), {
    message: "authored_export_admission_weight_invalid",
  });
});
