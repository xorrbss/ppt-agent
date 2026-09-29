import assert from "node:assert/strict";
import test, { mock } from "node:test";

import { fetchTelemetryStatus } from "./telemetry-status";

function withFetch(implementation: () => Promise<unknown>, run: () => Promise<void>) {
  const original = globalThis.fetch;
  globalThis.fetch = mock.fn(implementation) as unknown as typeof fetch;
  return run().finally(() => {
    globalThis.fetch = original;
  });
}

test("reports enabled only when the probe says so", async () => {
  await withFetch(
    async () => ({ json: async () => ({ telemetryEnabled: true }) }),
    async () => assert.equal(await fetchTelemetryStatus(), true),
  );
  await withFetch(
    async () => ({ json: async () => ({ telemetryEnabled: "yes" }) }),
    async () => assert.equal(await fetchTelemetryStatus(), false),
  );
});

test("fails closed when the probe is unreachable or malformed", async () => {
  await withFetch(
    async () => {
      throw new Error("offline");
    },
    async () => assert.equal(await fetchTelemetryStatus(), false),
  );
  await withFetch(
    async () => ({ json: async () => null }),
    async () => assert.equal(await fetchTelemetryStatus(), false),
  );
});
