import assert from "node:assert/strict";
import test from "node:test";

import {
  WindowsCleanupCoordinator,
  parseWindowsProfileScan,
} from "./windows-cleanup-coordinator.ts";

/** A scan that takes `durationMs` and records how many ran at once. */
function instrumentedScan(processes, durationMs = 20) {
  const state = { started: 0, peakConcurrent: 0, concurrent: 0 };
  const scan = async () => {
    state.started += 1;
    state.concurrent += 1;
    state.peakConcurrent = Math.max(state.peakConcurrent, state.concurrent);
    try {
      await new Promise((resolve) => setTimeout(resolve, durationMs));
      return processes;
    } finally {
      state.concurrent -= 1;
    }
  };
  return { scan, state };
}

test("concurrent teardowns share one in-flight process-table scan", async () => {
  // The regression this guards: each teardown ran its own
  // `Get-CimInstance Win32_Process`, so twelve concurrent captures could put
  // dozens of process-table scans on the host and starve the helper timeout
  // they were all measured against.
  const profiles = Array.from({ length: 12 }, (_, index) => `C:\\profiles\\capture-${index}`);
  const { scan, state } = instrumentedScan(
    profiles.map((userDataDir, index) => ({ pid: 1000 + index, userDataDir })),
  );
  const coordinator = new WindowsCleanupCoordinator({ scan, cacheTtlMs: 0 });

  const results = await Promise.all(
    profiles.map((profile) => coordinator.profileProcesses(profile)),
  );

  assert.equal(state.peakConcurrent, 1, "only one scan may be in flight");
  assert.equal(state.started, 1, "twelve teardowns must share a single scan");
  results.forEach((pids, index) => {
    assert.deepEqual(pids, [1000 + index], "each teardown reads only its own profile");
  });
  const telemetry = coordinator.telemetry();
  assert.equal(telemetry.scansStarted, 1);
  assert.equal(telemetry.scansJoined, profiles.length - 1);
  assert.equal(telemetry.peakScansInFlight, 1);
});

test("a caller that just issued kills is not answered from an older scan", async () => {
  let generation = 0;
  const scan = async () => {
    generation += 1;
    await new Promise((resolve) => setTimeout(resolve, 20));
    // The first scan sees the process; every later one sees it gone.
    return generation === 1 ? [{ pid: 4242, userDataDir: "C:\\p" }] : [];
  };
  const coordinator = new WindowsCleanupCoordinator({ scan, cacheTtlMs: 5_000 });

  const before = await coordinator.profileProcesses("C:\\p");
  assert.deepEqual(before, [4242]);

  // Cached and reusable for a caller that accepts a scan of that age.
  assert.deepEqual(await coordinator.profileProcesses("C:\\p"), [4242]);

  // A caller that has since killed the process must see a newer table, even
  // though a cached scan is still within its time-to-live.
  const after = await coordinator.profileProcesses("C:\\p", { notBefore: Date.now() });
  assert.deepEqual(after, [], "a post-kill scan must not be served from cache");
  assert.equal(generation, 2);
});

test("waiters needing a fresh scan coalesce onto one later scan", async () => {
  const { scan, state } = instrumentedScan([], 30);
  const coordinator = new WindowsCleanupCoordinator({ scan, cacheTtlMs: 0 });

  const first = coordinator.profileProcesses("C:\\p");
  await new Promise((resolve) => setTimeout(resolve, 5));
  // These arrive while the first scan runs but require a newer one.
  const notBefore = Date.now() + 1;
  const fresh = Array.from({ length: 6 }, () =>
    coordinator.profileProcesses("C:\\p", { notBefore }),
  );

  await Promise.all([first, ...fresh]);
  assert.equal(state.peakConcurrent, 1, "no two scans may overlap");
  assert.equal(state.started, 2, "six waiters must share the one scan that follows");
});

test("a scan that throws does not strand later callers on its rejection", async () => {
  let attempt = 0;
  const scan = async () => {
    attempt += 1;
    if (attempt === 1) throw new Error("CIM refused");
    return [{ pid: 7, userDataDir: "C:\\p" }];
  };
  const coordinator = new WindowsCleanupCoordinator({ scan, cacheTtlMs: 0 });

  await assert.rejects(coordinator.profileProcesses("C:\\p"), /CIM refused/);
  assert.deepEqual(await coordinator.profileProcesses("C:\\p"), [7]);
});

test("a scan that throws synchronously is not installed as the shared scan", async () => {
  let attempt = 0;
  const coordinator = new WindowsCleanupCoordinator({
    // Not an async function: it throws before returning a promise at all.
    scan: () => {
      attempt += 1;
      if (attempt === 1) throw new Error("spawn refused");
      return Promise.resolve([{ pid: 9, userDataDir: "C:\\p" }]);
    },
    cacheTtlMs: 0,
  });

  await assert.rejects(coordinator.profileProcesses("C:\\p"), /spawn refused/);
  assert.deepEqual(await coordinator.profileProcesses("C:\\p"), [9]);
});

test("cleanup helpers run through a small bounded queue and never time out waiting", async () => {
  // The helper timeout is only honest when it is spent on a helper that is
  // running. A queued helper must not be able to expire before it starts, so
  // the timeout is started by the task and the queue bounds how many tasks
  // hold the host at once.
  const helperConcurrency = 4;
  const coordinator = new WindowsCleanupCoordinator({
    scan: async () => [],
    helperConcurrency,
  });
  const HELPER_TIMEOUT_MS = 10_000;
  const HELPER_DURATION_MS = 15;

  let concurrent = 0;
  let peakConcurrent = 0;
  const timedOut = [];
  const helper = (id) =>
    coordinator.runHelper(async () => {
      concurrent += 1;
      peakConcurrent = Math.max(peakConcurrent, concurrent);
      const startedAt = Date.now();
      try {
        await new Promise((resolve) => setTimeout(resolve, HELPER_DURATION_MS));
        // The timeout a real helper carries is measured from its own start.
        if (Date.now() - startedAt >= HELPER_TIMEOUT_MS) timedOut.push(id);
        return id;
      } finally {
        concurrent -= 1;
      }
    });

  // Sixty helpers is the wave the isolated lane produced: twelve concurrent
  // captures, each killing the handful of processes sharing its profile.
  const ids = Array.from({ length: 60 }, (_, index) => index);
  const completed = await Promise.all(ids.map(helper));

  assert.deepEqual(completed, ids, "every helper must still run");
  assert.equal(timedOut.length, 0, "no helper may expire against contention");
  assert.ok(
    peakConcurrent <= helperConcurrency,
    `helpers in flight ${peakConcurrent} must stay within ${helperConcurrency}`,
  );
  assert.equal(coordinator.telemetry().peakHelpersInFlight, helperConcurrency);
});

test("a failing helper releases its queue slot", async () => {
  const coordinator = new WindowsCleanupCoordinator({ scan: async () => [], helperConcurrency: 1 });
  await assert.rejects(
    coordinator.runHelper(async () => {
      throw new Error("taskkill exited with code 128");
    }),
    /code 128/,
  );
  assert.equal(await coordinator.runHelper(async () => "next"), "next");
});

test("the shared scan output parses into exact profile identities", () => {
  const withTab = "C:\\Users\\a\tb\\profile";
  const parsed = parseWindowsProfileScan(
    [
      "1234\tC:\\Users\\ibiz\\AppData\\Local\\Temp\\presenton\\hybrid-1",
      "5678\tC:\\Users\\ibiz\\AppData\\Local\\Temp\\presenton\\hybrid 2",
      `4242\t${withTab}`,
      "not-a-pid\tC:\\x",
      "0\tC:\\x",
      "9999\t",
      "",
      "garbage",
    ].join("\r\n"),
  );
  assert.deepEqual(parsed, [
    { pid: 1234, userDataDir: "C:\\Users\\ibiz\\AppData\\Local\\Temp\\presenton\\hybrid-1" },
    { pid: 5678, userDataDir: "C:\\Users\\ibiz\\AppData\\Local\\Temp\\presenton\\hybrid 2" },
    // Only the first tab separates, so a directory containing one survives.
    { pid: 4242, userDataDir: withTab },
  ]);
});

test("profile matching stays case sensitive and prefix safe", async () => {
  const coordinator = new WindowsCleanupCoordinator({
    scan: async () => [
      { pid: 1, userDataDir: "C:\\profiles\\Capture" },
      { pid: 2, userDataDir: "C:\\profiles\\capture" },
      { pid: 3, userDataDir: "C:\\profiles\\capture-extra" },
      { pid: 4, userDataDir: "C:\\profiles\\capture" },
    ],
    cacheTtlMs: 0,
  });
  assert.deepEqual(
    await coordinator.profileProcesses("C:\\profiles\\capture"),
    [2, 4],
    "a sibling profile differing only in case or suffix is not this capture's",
  );
});
