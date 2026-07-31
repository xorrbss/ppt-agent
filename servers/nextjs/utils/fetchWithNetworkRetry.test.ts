import assert from "node:assert/strict";
import test from "node:test";

import { fetchWithNetworkRetry } from "./fetchWithNetworkRetry.ts";

test("retries transient fetch TypeErrors and returns the successful response", async () => {
  let attempts = 0;
  const delays: number[] = [];
  const response = new Response("ok", { status: 200 });

  const result = await fetchWithNetworkRetry(
    "http://localhost/upload",
    { method: "POST" },
    {
      fetchImpl: (async () => {
        attempts += 1;
        if (attempts < 3) {
          throw new TypeError("Failed to fetch");
        }
        return response;
      }) as typeof fetch,
      retryDelaysMs: [10, 20],
      sleep: async (delayMs) => {
        delays.push(delayMs);
      },
    }
  );

  assert.equal(result, response);
  assert.equal(attempts, 3);
  assert.deepEqual(delays, [10, 20]);
});

test("does not retry an HTTP error response", async () => {
  let attempts = 0;

  const result = await fetchWithNetworkRetry(
    "http://localhost/upload",
    {},
    {
      fetchImpl: (async () => {
        attempts += 1;
        return new Response("unavailable", { status: 503 });
      }) as typeof fetch,
      sleep: async () => {
        throw new Error("sleep should not run");
      },
    }
  );

  assert.equal(result.status, 503);
  assert.equal(attempts, 1);
});

test("does not retry non-network errors or an aborted request", async () => {
  const nonNetworkError = new Error("invalid request");
  let attempts = 0;

  await assert.rejects(
    fetchWithNetworkRetry("http://localhost/upload", {}, {
      fetchImpl: (async () => {
        attempts += 1;
        throw nonNetworkError;
      }) as typeof fetch,
      sleep: async () => {},
    }),
    nonNetworkError
  );
  assert.equal(attempts, 1);

  const controller = new AbortController();
  controller.abort();
  attempts = 0;

  await assert.rejects(
    fetchWithNetworkRetry(
      "http://localhost/upload",
      { signal: controller.signal },
      {
        fetchImpl: (async () => {
          attempts += 1;
          throw new TypeError("Failed to fetch");
        }) as typeof fetch,
        sleep: async () => {},
      }
    ),
    TypeError
  );
  assert.equal(attempts, 1);
});
