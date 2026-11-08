import assert from "node:assert/strict";
import { once } from "node:events";
import test from "node:test";

import {
  combineAbortSignals,
  fetchTimeoutSignal,
} from "./fetch-timeout-signal.ts";

/**
 * Waits for `signal` to abort, failing if it has not within `limitMs`.
 *
 * AbortSignal.timeout uses an unref'd timer, so awaiting only the signal lets
 * the test process's event loop drain and node:test cancels the test (and
 * every test after it) with "Promise resolution is still pending". The ref'd
 * guard timer keeps the loop alive and turns a missing abort into a failure.
 */
async function abortWithin(signal: AbortSignal, limitMs = 5_000): Promise<void> {
  let guard: ReturnType<typeof setTimeout> | undefined;
  try {
    await Promise.race([
      once(signal, "abort"),
      new Promise<never>((_resolve, reject) => {
        guard = setTimeout(
          () => reject(new Error(`signal did not abort within ${limitMs}ms`)),
          limitMs,
        );
      }),
    ]);
  } finally {
    clearTimeout(guard);
  }
}

test("a bare timeout signal aborts with TimeoutError after the deadline", async () => {
  const signal = fetchTimeoutSignal(20);
  assert.equal(signal.aborted, false);
  await abortWithin(signal);
  assert.equal(signal.aborted, true);
  assert.equal((signal.reason as { name?: string }).name, "TimeoutError");
});

test("a parent abort cancels before the deadline and keeps its reason", async () => {
  const parent = new AbortController();
  const signal = fetchTimeoutSignal(60_000, parent.signal);
  assert.equal(signal.aborted, false);
  const reason = new Error("client disconnected");
  parent.abort(reason);
  if (!signal.aborted) await once(signal, "abort");
  assert.equal(signal.reason, reason);
});

test("the deadline still fires when the parent never aborts", async () => {
  const parent = new AbortController();
  const signal = fetchTimeoutSignal(20, parent.signal);
  await abortWithin(signal);
  assert.equal((signal.reason as { name?: string }).name, "TimeoutError");
});

test("an already-aborted parent is reflected immediately", () => {
  const parent = new AbortController();
  parent.abort("gone");
  const signal = fetchTimeoutSignal(60_000, parent.signal);
  assert.equal(signal.aborted, true);
  assert.equal(signal.reason, "gone");
});

test("the portable fallback mirrors AbortSignal.any semantics", async () => {
  const first = new AbortController();
  const second = new AbortController();
  const combined = combineAbortSignals([first.signal, second.signal]);
  assert.equal(combined.aborted, false);
  second.abort("second");
  assert.equal(combined.aborted, true);
  assert.equal(combined.reason, "second");
  first.abort("first");
  assert.equal(combined.reason, "second", "the first abort wins");

  const preAborted = new AbortController();
  preAborted.abort("early");
  assert.equal(combineAbortSignals([new AbortController().signal, preAborted.signal]).reason, "early");
});

test("a non-positive timeout is a programming error", () => {
  assert.throws(() => fetchTimeoutSignal(0), RangeError);
  assert.throws(() => fetchTimeoutSignal(Number.NaN), RangeError);
});

test("a fetch against a silent server is rejected by the timeout", async () => {
  const { createServer } = await import("node:http");
  const server = createServer(() => {
    // Never respond.
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  assert.ok(address && typeof address === "object");
  try {
    await assert.rejects(
      fetch(`http://127.0.0.1:${address.port}/`, {
        signal: fetchTimeoutSignal(50),
      }),
      (error: unknown) =>
        (error as { name?: string }).name === "TimeoutError" ||
        (error as { cause?: { name?: string } }).cause?.name === "TimeoutError",
    );
  } finally {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  }
});
