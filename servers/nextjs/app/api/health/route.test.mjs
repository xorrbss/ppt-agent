import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import { transform } from "esbuild";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const routePath = path.join(testDirectory, "route.ts");
const fastApiInternalUrl = pathToFileURL(
  path.join(testDirectory, "../../../lib/fastapi-internal.ts"),
).href;
const fetchTimeoutSignalUrl = pathToFileURL(
  path.join(testDirectory, "../../../lib/fetch-timeout-signal.ts"),
).href;
const nextServerUrl = pathToFileURL(
  path.join(testDirectory, "../../../node_modules/next/server.js"),
).href;

async function loadRoute() {
  const source = await readFile(routePath, "utf8");
  const executableSource = source
    .replace('"next/server"', JSON.stringify(nextServerUrl))
    .replace('"@/lib/fastapi-internal"', JSON.stringify(fastApiInternalUrl))
    .replace(
      '"@/lib/fetch-timeout-signal"',
      JSON.stringify(fetchTimeoutSignalUrl),
    );
  const compiled = await transform(executableSource, {
    format: "esm",
    loader: "ts",
    target: "esnext",
  });
  const encoded = Buffer.from(compiled.code).toString("base64");
  return import(`data:text/javascript;base64,${encoded}`);
}

const { GET } = await loadRoute();

async function withFetch(fakeFetch, callback) {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = fakeFetch;
  try {
    return await callback();
  } finally {
    globalThis.fetch = originalFetch;
  }
}

// This route is what a container healthcheck curling
// `http://localhost/api/health` through nginx actually reaches (see the
// route's own comment and nginx.conf). It must not report "ok" on its own
// Next.js liveness alone: a dead or hung FastAPI has to fail the probe.

test("health route reports ok when FastAPI answers 200", async () => {
  await withFetch(async (url) => {
    assert.equal(url, "http://127.0.0.1:8000/health");
    return new Response(JSON.stringify({ status: "ok" }), { status: 200 });
  }, async () => {
    const response = await GET();
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { status: "ok" });
  });
});

test("health route reports degraded when FastAPI answers non-2xx", async () => {
  await withFetch(async () => {
    return new Response("", { status: 503 });
  }, async () => {
    const response = await GET();
    assert.equal(response.status, 503);
    const body = await response.json();
    assert.equal(body.status, "degraded");
    assert.equal(body.fastapi, "unhealthy");
  });
});

test("health route reports degraded when FastAPI is unreachable (dead or hung process)", async () => {
  await withFetch(async () => {
    throw new Error("connect ECONNREFUSED 127.0.0.1:8000");
  }, async () => {
    const response = await GET();
    assert.equal(response.status, 503);
    const body = await response.json();
    assert.equal(body.status, "degraded");
    assert.equal(body.fastapi, "unreachable");
  });
});
