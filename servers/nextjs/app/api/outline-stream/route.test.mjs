import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import { transform } from "esbuild";
import { NextRequest } from "next/server.js";
import { Agent } from "undici";

// This test lives at app/api/outline-stream/route.test.mjs — a sibling of the
// `[id]` route directory, not inside it. Node's `--test` CLI file matching
// treats `[...]` in a path as a glob character class, so a *.test.mjs file
// placed inside `[id]/` is silently never discovered/run by
// `scripts/run-ci-node-tests.mjs`. Loading `route.ts` from `[id]/` here via
// `readFile`/`pathToFileURL` (plain fs/URL APIs, not CLI glob matching) works
// fine; only the test file's own path must avoid the bracket segment.
const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const routePath = path.join(testDirectory, "[id]", "route.ts");
const fastApiInternalUrl = pathToFileURL(
  path.join(testDirectory, "../../../lib/fastapi-internal.ts"),
).href;
const nextServerUrl = pathToFileURL(
  path.join(testDirectory, "../../../node_modules/next/server.js"),
).href;
const undiciUrl = pathToFileURL(
  path.join(testDirectory, "../../../node_modules/undici/index.js"),
).href;

async function loadRoute() {
  const source = await readFile(routePath, "utf8");
  const executableSource = source
    .replace('"next/server"', JSON.stringify(nextServerUrl))
    .replace('"undici"', JSON.stringify(undiciUrl))
    .replace('"@/lib/fastapi-internal"', JSON.stringify(fastApiInternalUrl));
  const compiled = await transform(executableSource, {
    format: "esm",
    loader: "ts",
    target: "esnext",
  });
  const encoded = Buffer.from(compiled.code).toString("base64");
  return import(`data:text/javascript;base64,${encoded}`);
}

const { GET } = await loadRoute();

function request() {
  return new NextRequest("http://localhost/api/outline-stream/pres-1", {
    method: "GET",
    headers: {
      authorization: "Bearer user-token",
      cookie: "session=abc",
    },
  });
}

function context() {
  return { params: Promise.resolve({ id: "pres-1" }) };
}

async function withFetch(fakeFetch, callback) {
  const originalFetch = globalThis.fetch;
  const originalConsoleError = console.error;
  globalThis.fetch = fakeFetch;
  console.error = () => {};
  try {
    return await callback();
  } finally {
    globalThis.fetch = originalFetch;
    console.error = originalConsoleError;
  }
}

/**
 * Undici's Agent does not expose its constructor options as an enumerable
 * property; they live behind an internal `Symbol(options)`. Reading the
 * symbol (from the same undici build the route imports) is the only way to
 * assert the dispatcher was actually configured with disabled timeouts,
 * short of connecting a real socket and waiting out the timeout.
 */
function readAgentOptions(dispatcher) {
  const optionsSymbol = Object.getOwnPropertySymbols(dispatcher).find(
    (symbol) => symbol.toString() === "Symbol(options)",
  );
  assert.ok(optionsSymbol, "expected the Agent instance to expose Symbol(options)");
  return dispatcher[optionsSymbol];
}

test("outline-stream route passes a dispatcher with disabled body and headers timeouts", async () => {
  let capturedDispatcher;
  await withFetch(
    async (url, init) => {
      assert.equal(
        url,
        "http://127.0.0.1:8000/api/v1/ppt/outlines/stream/pres-1",
      );
      assert.equal(init?.method, "GET");
      assert.equal(new Headers(init?.headers).get("authorization"), "Bearer user-token");
      capturedDispatcher = init?.dispatcher;
      return new Response(
        new ReadableStream({
          start(controller) {
            controller.close();
          },
        }),
        {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        },
      );
    },
    async () => {
      const response = await GET(request(), context());
      assert.equal(response.status, 200);
    },
  );

  assert.ok(capturedDispatcher instanceof Agent, "expected an undici Agent dispatcher");
  const options = readAgentOptions(capturedDispatcher);
  assert.equal(options.headersTimeout, 0);
  assert.equal(options.bodyTimeout, 0);
});

test("outline-stream route errors the outgoing stream instead of ending cleanly when the upstream body errors mid-flight", async () => {
  const upstreamError = new Error("simulated UND_ERR_BODY_TIMEOUT");

  const response = await withFetch(
    async () =>
      new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(new TextEncoder().encode("event: response\ndata: {}\n\n"));
          },
          pull(controller) {
            controller.error(upstreamError);
          },
        }),
        {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        },
      ),
    async () => GET(request(), context()),
  );

  assert.equal(response.status, 200);
  assert.ok(response.body, "expected the route to return a streaming body");

  const reader = response.body.getReader();
  const first = await reader.read();
  assert.equal(first.done, false);

  await assert.rejects(() => reader.read());
});
