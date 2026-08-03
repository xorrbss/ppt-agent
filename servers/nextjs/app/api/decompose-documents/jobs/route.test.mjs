import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import { transform } from "esbuild";
import { NextRequest } from "next/server.js";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const fastApiInternalUrl = pathToFileURL(
  path.join(testDirectory, "../../../../lib/fastapi-internal.ts"),
).href;
const nextServerUrl = pathToFileURL(
  path.join(testDirectory, "../../../../node_modules/next/server.js"),
).href;

async function loadRoute(relativePath) {
  const source = await readFile(path.join(testDirectory, relativePath), "utf8");
  const executableSource = source
    .replace('"next/server"', JSON.stringify(nextServerUrl))
    .replace('"@/lib/fastapi-internal"', JSON.stringify(fastApiInternalUrl));
  const compiled = await transform(executableSource, {
    format: "esm",
    loader: "ts",
    target: "esnext",
  });
  return import(
    `data:text/javascript;base64,${Buffer.from(compiled.code).toString("base64")}`
  );
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

test("decompose job start proxies the short queue request", async () => {
  const { POST } = await loadRoute("route.ts");
  await withFetch(async (url, init) => {
    assert.equal(url, "http://127.0.0.1:8000/api/v1/ppt/files/decompose/jobs");
    assert.equal(init?.method, "POST");
    assert.equal(init?.body, '{"file_paths":["upload.png"],"language":"ko"}');
    assert.equal(new Headers(init?.headers).get("idempotency-key"), "same-job");
    return new Response(JSON.stringify({ job_id: "job-1", status: "queued" }), {
      status: 202,
      headers: { "content-type": "application/json", "x-request-id": "req-1" },
    });
  }, async () => {
    const response = await POST(new NextRequest("http://localhost/api/decompose-documents/jobs", {
      method: "POST",
      headers: { "content-type": "application/json", "idempotency-key": "same-job" },
      body: '{"file_paths":["upload.png"],"language":"ko"}',
    }));
    assert.equal(response.status, 202);
    assert.equal(response.headers.get("x-request-id"), "req-1");
    assert.deepEqual(await response.json(), { job_id: "job-1", status: "queued" });
  });
});

test("decompose job status and cancellation proxy the job URL", async () => {
  const { GET, DELETE } = await loadRoute("[jobId]/route.ts");
  const methods = [];
  await withFetch(async (url, init) => {
    assert.equal(url, "http://127.0.0.1:8000/api/v1/ppt/files/decompose/jobs/job%201");
    methods.push(init?.method);
    return new Response(JSON.stringify({ job_id: "job 1", status: "running" }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  }, async () => {
    const context = { params: Promise.resolve({ jobId: "job 1" }) };
    const statusResponse = await GET(
      new NextRequest("http://localhost/api/decompose-documents/jobs/job%201"),
      context,
    );
    const cancelResponse = await DELETE(
      new NextRequest("http://localhost/api/decompose-documents/jobs/job%201", { method: "DELETE" }),
      context,
    );
    assert.equal(statusResponse.status, 200);
    assert.equal(cancelResponse.status, 200);
  });
  assert.deepEqual(methods, ["GET", "DELETE"]);
});
