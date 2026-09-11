import assert from "node:assert/strict";
import test from "node:test";

import {
  WeightedExportAdmission,
  defaultExportProcessWeightLimit,
} from "./export-admission.ts";

test("the admission default stays at the measured throughput optimum", () => {
  // Pinned because lowering this is a measured regression, not a fix: on a
  // 22-CPU host the run-11 deck attempted 24 slides at 12, 16 at 6 and 14 at 4
  // (w17-throughput.md). A future "reduce contention" change must update the
  // measurement before it updates this number.
  assert.equal(defaultExportProcessWeightLimit(22), 12);
  assert.equal(defaultExportProcessWeightLimit(14), 12);
  assert.equal(defaultExportProcessWeightLimit(12), 10);
  assert.equal(defaultExportProcessWeightLimit(8), 6);
  assert.equal(defaultExportProcessWeightLimit(36), 12, "ceiling still holds");
  assert.equal(defaultExportProcessWeightLimit(96), 12);

  // Small hosts keep making progress instead of admitting fewer than a deck
  // needs to move at all.
  for (const cpuCount of [1, 2, 3, 4]) {
    assert.equal(
      defaultExportProcessWeightLimit(cpuCount),
      2,
      `cpuCount ${cpuCount}`,
    );
  }

  // A container may report a nonsense CPU count; the limit must stay legal.
  for (const bogus of [0, -4, Number.NaN, 1.5, Number.POSITIVE_INFINITY]) {
    const limit = defaultExportProcessWeightLimit(bogus);
    assert.ok(
      Number.isSafeInteger(limit) && limit >= 2 && limit <= 12,
      `bogus cpuCount ${bogus} produced ${limit}`,
    );
  }

  // The result must always be a legal capacity for the admission itself.
  for (const cpuCount of [1, 2, 5, 11, 22, 64]) {
    const limit = defaultExportProcessWeightLimit(cpuCount);
    assert.doesNotThrow(() => new WeightedExportAdmission(limit));
  }
});

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
