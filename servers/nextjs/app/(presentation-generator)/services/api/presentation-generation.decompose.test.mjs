import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { transform } from "esbuild";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const sourcePath = path.join(testDirectory, "presentation-generation.ts");

function dataModule(source) {
  return `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
}

const headerUrl = dataModule(`
  export const getHeader = () => ({ "Content-Type": "application/json" });
  export const getHeaderForFormData = () => ({});
`);
const paramsUrl = dataModule("export {};\n");
const errorHandlerUrl = dataModule(`
  export const apiErrorFromPayload = (payload, fallback) => {
    const error = new Error(payload?.message || fallback);
    error.code = payload?.code;
    return error;
  };
  export const ApiResponseHandler = {
    async handleResponse(response, fallback) {
      if (!response.ok) throw new Error(fallback);
      return response.json();
    },
  };
`);
const apiUrl = dataModule("export const getApiUrl = (path) => path; export const resolveBackendAssetUrl = (value) => value;\n");
const retryUrl = dataModule("export const fetchWithNetworkRetry = (...args) => fetch(...args);\n");

async function loadApi() {
  const source = await readFile(sourcePath, "utf8");
  const executableSource = source
    .replace('from "./header";', `from "${headerUrl}";`)
    .replace('from "./params";', `from "${paramsUrl}";`)
    .replace('from "./api-error-handler";', `from "${errorHandlerUrl}";`)
    .replace('from "@/utils/api";', `from "${apiUrl}";`)
    .replace('from "@/utils/fetchWithNetworkRetry";', `from "${retryUrl}";`);
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
  const originalWindow = globalThis.window;
  const originalConsoleError = console.error;
  globalThis.fetch = fakeFetch;
  globalThis.window = { setTimeout, clearTimeout };
  console.error = () => {};
  try {
    return await callback();
  } finally {
    globalThis.fetch = originalFetch;
    globalThis.window = originalWindow;
    console.error = originalConsoleError;
  }
}

test("decomposeDocuments starts a job and polls it to its result", async () => {
  const { PresentationGenerationApi } = await loadApi();
  const calls = [];
  await withFetch(async (url, init) => {
    calls.push([url, init?.method]);
    if (init?.method === "POST") {
      return new Response(JSON.stringify({
        job_id: "job-1",
        status: "queued",
        progress: { completed_files: 0, total_files: 1 },
        result: null,
        error: null,
      }), { status: 202 });
    }
    return new Response(JSON.stringify({
      job_id: "job-1",
      status: "succeeded",
      progress: { completed_files: 1, total_files: 1 },
      result: [{ name: "source.pdf", file_path: "/tmp/source.txt" }],
      error: null,
    }), { status: 200 });
  }, async () => {
    const progress = [];
    const result = await PresentationGenerationApi.decomposeDocuments(
      ["source.pdf"],
      "ko",
      { pollIntervalMs: 0, onProgress: (job) => progress.push(job.status) },
    );
    assert.deepEqual(result, [{ name: "source.pdf", file_path: "/tmp/source.txt" }]);
    assert.deepEqual(calls, [
      ["/api/decompose-documents/jobs", "POST"],
      ["/api/decompose-documents/jobs/job-1", "GET"],
    ]);
    assert.deepEqual(progress, ["queued", "succeeded"]);
  });
});

test("decomposeDocuments cancels its server job when its signal is aborted", async () => {
  const { PresentationGenerationApi } = await loadApi();
  const controller = new AbortController();
  const calls = [];
  await withFetch(async (url, init) => {
    calls.push([url, init?.method]);
    if (init?.method === "POST") {
      return new Response(JSON.stringify({
        job_id: "job-cancel",
        status: "queued",
        progress: { completed_files: 0, total_files: 1 },
        result: null,
        error: null,
      }), { status: 202 });
    }
    return new Response(JSON.stringify({
      job_id: "job-cancel",
      status: "cancelling",
      progress: { completed_files: 0, total_files: 1 },
      result: null,
      error: null,
    }), { status: 202 });
  }, async () => {
    await assert.rejects(
      PresentationGenerationApi.decomposeDocuments(["source.pdf"], null, {
        signal: controller.signal,
        onProgress: () => controller.abort(),
      }),
      { name: "AbortError" },
    );
  });
  assert.deepEqual(calls, [
    ["/api/decompose-documents/jobs", "POST"],
    ["/api/decompose-documents/jobs/job-cancel", "DELETE"],
  ]);
});
