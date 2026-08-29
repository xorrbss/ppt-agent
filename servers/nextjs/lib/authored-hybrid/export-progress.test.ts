import assert from "node:assert/strict";
import test from "node:test";

import {
  beginExportProgress,
  readExportProgress,
  recordExportProgress,
  resetExportProgressForTesting,
} from "./export-progress.ts";

const CACHE_KEY = "cache-key-1";
const OPERATION = "11111111-2222-3333-4444-555555555555";

test("progress is readable by operation id and tracks the per-slide pass", () => {
  resetExportProgressForTesting();
  beginExportProgress(CACHE_KEY, OPERATION, Date.now());

  assert.equal(readExportProgress(OPERATION)?.phase, "queued");

  recordExportProgress(CACHE_KEY, { phase: "fidelity" });
  assert.equal(readExportProgress(OPERATION)?.phase, "fidelity");

  recordExportProgress(CACHE_KEY, {
    phase: "slides",
    completedSlides: 0,
    totalSlides: 100,
  });
  recordExportProgress(CACHE_KEY, { completedSlides: 40 });
  const snapshot = readExportProgress(OPERATION);
  assert.equal(snapshot?.phase, "slides");
  assert.equal(snapshot?.completedSlides, 40);
  assert.equal(snapshot?.totalSlides, 100);
  assert.ok((snapshot?.elapsedMs ?? -1) >= 0);
});

test("workspace operation identity is not aliased to export attempt identity", () => {
  resetExportProgressForTesting();
  const exportAttemptId = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";
  beginExportProgress(CACHE_KEY, OPERATION, Date.now());

  assert.equal(readExportProgress(OPERATION)?.phase, "queued");
  assert.equal(readExportProgress(exportAttemptId), null);
});

test("a late slide callback never moves the count backwards", () => {
  resetExportProgressForTesting();
  beginExportProgress(CACHE_KEY, OPERATION, Date.now());
  recordExportProgress(CACHE_KEY, { phase: "slides", totalSlides: 10 });

  recordExportProgress(CACHE_KEY, { completedSlides: 7 });
  // Slides finish concurrently, so an out-of-order callback is expected.
  recordExportProgress(CACHE_KEY, { completedSlides: 3 });

  assert.equal(readExportProgress(OPERATION)?.completedSlides, 7);
});

test("operations that joined one export job share its progress", () => {
  resetExportProgressForTesting();
  const joiner = "99999999-8888-7777-6666-555555555555";
  beginExportProgress(CACHE_KEY, OPERATION, Date.now());
  recordExportProgress(CACHE_KEY, {
    phase: "slides",
    completedSlides: 12,
    totalSlides: 30,
  });

  // The second request was collapsed onto the running job by cache key.
  beginExportProgress(CACHE_KEY, joiner, Date.now());

  const shared = readExportProgress(joiner);
  assert.equal(shared?.completedSlides, 12);
  assert.equal(shared?.totalSlides, 30);
  // Joining must not restart the record the running export is writing to.
  assert.equal(readExportProgress(OPERATION)?.completedSlides, 12);
});

test("an unknown operation reads as absent rather than as zero progress", () => {
  resetExportProgressForTesting();
  assert.equal(readExportProgress(OPERATION), null);

  // Recording against an untracked export is a no-op, never a phantom record.
  recordExportProgress("never-started", { phase: "slides" });
  assert.equal(readExportProgress(OPERATION), null);
});

test("separate Next route module graphs share operation progress", async () => {
  const writer = await import(`./export-progress.ts?writer=${Date.now()}`);
  const reader = await import(`./export-progress.ts?reader=${Date.now()}`);
  writer.resetExportProgressForTesting();
  writer.beginExportProgress(CACHE_KEY, OPERATION, Date.now());
  writer.recordExportProgress(CACHE_KEY, {
    phase: "slides",
    completedSlides: 2,
    totalSlides: 3,
  });

  const shared = reader.readExportProgress(OPERATION);
  assert.equal(shared?.phase, "slides");
  assert.equal(shared?.completedSlides, 2);
  assert.equal(shared?.totalSlides, 3);
});
