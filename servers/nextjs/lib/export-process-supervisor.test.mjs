import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";

import {
  BundledPresentationExportTimeoutError,
  resolveExportProcessLimits,
  superviseExportProcess,
} from "./export-process-supervisor.ts";

class FakeChild extends EventEmitter {
  pid = 12345;
  kill() {
    return true;
  }
}

test("uses conservative defaults and supports environment overrides", () => {
  assert.deepEqual(resolveExportProcessLimits({}), {
    deadlineMs: 600_000,
    terminationGraceMs: 5_000,
  });
  assert.deepEqual(
    resolveExportProcessLimits({
      PRESENTATION_EXPORT_DEADLINE_MS: "120000",
      PRESENTATION_EXPORT_TERMINATION_GRACE_MS: "2500",
    }),
    { deadlineMs: 120_000, terminationGraceMs: 2_500 }
  );
  assert.throws(
    () =>
      resolveExportProcessLimits({
        PRESENTATION_EXPORT_DEADLINE_MS: "0",
      }),
    /positive integer/
  );
});

test("returns a normal child exit before the deadline", async () => {
  const child = new FakeChild();
  const resultPromise = superviseExportProcess(child, {
    deadlineMs: 100,
    terminationGraceMs: 20,
  });
  child.emit("close", 0, null);

  assert.deepEqual(await resultPromise, { code: 0, signal: null });
});

test("deadline requests graceful termination before forced termination", async () => {
  const child = new FakeChild();
  const terminationModes = [];
  const resultPromise = superviseExportProcess(child, {
    deadlineMs: 10,
    terminationGraceMs: 10,
    terminate: async (_child, force) => {
      terminationModes.push(force);
      if (force) child.emit("close", null, "SIGKILL");
    },
  });

  await assert.rejects(resultPromise, BundledPresentationExportTimeoutError);
  assert.deepEqual(terminationModes, [false, true]);
});

test("graceful exit after deadline still reports a timeout", async () => {
  const child = new FakeChild();
  const resultPromise = superviseExportProcess(child, {
    deadlineMs: 10,
    terminationGraceMs: 100,
    terminate: async (_child, force) => {
      if (!force) child.emit("close", null, "SIGTERM");
    },
  });

  await assert.rejects(resultPromise, {
    code: "presentation_export_timeout",
  });
});
