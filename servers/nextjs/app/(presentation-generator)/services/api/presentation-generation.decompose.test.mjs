import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { transform } from "esbuild";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const sourcePath = path.join(testDirectory, "presentation-generation.ts");
const admissionSourcePath = path.join(
  testDirectory,
  "canonical-document-admission.ts",
);

function dataModule(source) {
  return `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
}

const headerUrl = dataModule(`
  export const getHeader = () => ({ "Content-Type": "application/json" });
  export const getHeaderForFormData = () => ({});
`);
const paramsUrl = dataModule("export {};\n");
const errorHandlerUrl = dataModule(`
  export const apiErrorFromPayload = (payload, fallback, _status, requestId) => {
    const error = new Error(payload?.message || fallback);
    error.code = payload?.code;
    error.requestId = payload?.request_id || requestId;
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
  const admissionSource = await readFile(admissionSourcePath, "utf8");
  const admissionCompiled = await transform(
    admissionSource
      .replace('from "./header";', `from "${headerUrl}";`)
      .replace('from "@/utils/api";', `from "${apiUrl}";`)
      .replace('from "@/utils/fetchWithNetworkRetry";', `from "${retryUrl}";`),
    {
      format: "esm",
      loader: "ts",
      target: "esnext",
    },
  );
  const admissionUrl = dataModule(admissionCompiled.code);
  const executableSource = source
    .replace('from "./header";', `from "${headerUrl}";`)
    .replace('from "./params";', `from "${paramsUrl}";`)
    .replace('from "./api-error-handler";', `from "${errorHandlerUrl}";`)
    .replace('from "@/utils/api";', `from "${apiUrl}";`)
    .replace('from "@/utils/fetchWithNetworkRetry";', `from "${retryUrl}";`)
    .replace(
      'from "./canonical-document-admission";',
      `from "${admissionUrl}";`,
    );
  const compiled = await transform(executableSource, {
    format: "esm",
    loader: "ts",
    target: "esnext",
  });
  return import(
    `data:text/javascript;base64,${Buffer.from(compiled.code).toString("base64")}`
  );
}

async function withFetch(fakeFetch, callback, { onConsoleError = () => {} } = {}) {
  const originalFetch = globalThis.fetch;
  const originalWindow = globalThis.window;
  const originalConsoleError = console.error;
  globalThis.fetch = fakeFetch;
  globalThis.window = { setTimeout, clearTimeout };
  console.error = (...args) => onConsoleError(...args);
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

test("canonical upload, decompose, and create use only asset and snapshot identities", async () => {
  const { PresentationGenerationApi } = await loadApi();
  const calls = [];
  let uploadIndex = 0;
  await withFetch(async (url, init) => {
    calls.push([url, init?.method, init?.body]);
    if (url === "/api/v1/ppt/assets/documents") {
      uploadIndex += 1;
      return new Response(JSON.stringify({
        asset_id: `asset-${uploadIndex}`,
        kind: "document",
        state: "ready",
        size_bytes: 10,
        sha256: String(uploadIndex).repeat(64),
        version: 1,
      }), { status: 200 });
    }
    if (url === "/api/v1/ppt/references/snapshots") {
      assert.deepEqual(JSON.parse(init.body), {
        provider_id: "uploaded_document",
        source: { asset_ids: ["asset-1", "asset-2"] },
      });
      return new Response(JSON.stringify({ snapshot_id: "snapshot-1" }), {
        status: 200,
      });
    }
    if (url === "/api/decompose-documents/jobs") {
      assert.deepEqual(JSON.parse(init.body), {
        reference_snapshot_id: "snapshot-1",
        language: "ko",
      });
      return new Response(JSON.stringify({
        job_id: "canonical-job",
        status: "succeeded",
        progress: { completed_files: 2, total_files: 2 },
        result: [
          { name: "one.pdf", asset_id: "asset-1", content_sha256: "a".repeat(64) },
          { name: "two.pdf", asset_id: "asset-2", content_sha256: "b".repeat(64) },
        ],
        error: null,
      }), { status: 202 });
    }
    if (url === "/api/v1/ppt/presentation/create") {
      const body = JSON.parse(init.body);
      assert.deepEqual(body.reference_snapshot_ids, ["snapshot-1"]);
      assert.equal("file_paths" in body, false);
      return new Response(JSON.stringify({ id: "presentation-1" }), {
        status: 200,
      });
    }
    throw new Error(`unexpected request: ${url}`);
  }, async () => {
    const admission = await PresentationGenerationApi.uploadDoc([
      new File(["one"], "one.pdf", { type: "application/pdf" }),
      new File(["two"], "two.pdf", { type: "application/pdf" }),
    ]);
    assert.deepEqual(admission.assetIds, ["asset-1", "asset-2"]);
    assert.equal(admission.referenceSnapshotId, "snapshot-1");

    const decomposed = await PresentationGenerationApi.decomposeDocuments(
      { referenceSnapshotId: admission.referenceSnapshotId, documentCount: 2 },
      "ko",
    );
    assert.equal(decomposed.every((item) => !("file_path" in item)), true);

    await PresentationGenerationApi.createPresentation({
      presentation_id: "presentation-1",
      idempotency_key: "create-1",
      content: "",
      n_slides: 2,
      reference_snapshot_ids: [admission.referenceSnapshotId],
      language: "ko",
    });
  });

  assert.equal(
    calls.some(([, , body]) => typeof body === "string" && body.includes("file_paths")),
    false,
  );
});

test("canonical upload sends stable idempotency keys", async () => {
  const { PresentationGenerationApi } = await loadApi();
  const observedKeys = [];
  let assetCounter = 0;
  await withFetch(async (url, init) => {
    observedKeys.push(init?.headers?.["Idempotency-Key"]);
    if (url === "/api/v1/ppt/assets/documents") {
      assetCounter += 1;
      return new Response(JSON.stringify({
        asset_id: `asset-${assetCounter}`,
        kind: "document",
        state: "ready",
        size_bytes: 3,
        sha256: "a".repeat(64),
        version: 1,
      }), { status: 200 });
    }
    return new Response(JSON.stringify({ snapshot_id: `snapshot-${assetCounter}` }), {
      status: 200,
    });
  }, async () => {
    const first = new File(["one"], "one.txt", {
      type: "text/plain",
      lastModified: 42,
    });
    const second = new File(["one"], "one.txt", {
      type: "text/plain",
      lastModified: 42,
    });
    await PresentationGenerationApi.uploadDoc([first]);
    await PresentationGenerationApi.uploadDoc([second]);
  });
  assert.equal(observedKeys[0], observedKeys[2]);
  assert.equal(observedKeys[1], observedKeys[3]);
  assert.match(observedKeys[0], /^document-upload:v1:[a-f0-9]{64}:0$/);
  assert.match(observedKeys[1], /^document-snapshot:v1:[a-f0-9]{64}$/);
});

test("partial canonical admission compensates uploaded assets", async () => {
  const { PresentationGenerationApi } = await loadApi();
  const calls = [];
  let uploadCount = 0;
  await withFetch(async (url, init) => {
    calls.push([url, init?.method]);
    if (url === "/api/v1/ppt/assets/documents") {
      uploadCount += 1;
      if (uploadCount === 2) {
        return new Response(JSON.stringify({ detail: "rejected" }), { status: 422 });
      }
      return new Response(JSON.stringify({
        asset_id: "asset-admitted",
        kind: "document",
        state: "ready",
        size_bytes: 3,
        sha256: "a".repeat(64),
        version: 1,
      }), { status: 200 });
    }
    if (url === "/api/v1/ppt/assets/documents/asset-admitted") {
      return new Response(JSON.stringify({
        state: "expired",
        asset_ids: ["asset-admitted"],
      }), { status: 200 });
    }
    throw new Error(`unexpected request: ${url}`);
  }, async () => {
    await assert.rejects(
      PresentationGenerationApi.uploadDoc([
        new File(["one"], "one.txt", { type: "text/plain" }),
        new File(["two"], "two.txt", { type: "text/plain" }),
      ]),
      /Failed to upload document/,
    );
  });
  assert.equal(
    calls.some(
      ([url, method]) =>
        url === "/api/v1/ppt/assets/documents/asset-admitted" && method === "DELETE",
    ),
    true,
  );
});

test("failed presentation creation compensates its canonical snapshot", async () => {
  const { PresentationGenerationApi } = await loadApi();
  const calls = [];
  await withFetch(async (url, init) => {
    calls.push([url, init?.method]);
    if (url === "/api/v1/ppt/assets/documents") {
      return new Response(JSON.stringify({
        asset_id: "asset-create-failure",
        kind: "document",
        state: "ready",
        size_bytes: 3,
        sha256: "a".repeat(64),
        version: 1,
      }), { status: 200 });
    }
    if (url === "/api/v1/ppt/references/snapshots") {
      return new Response(JSON.stringify({ snapshot_id: "snapshot-create-failure" }), {
        status: 200,
      });
    }
    if (url === "/api/v1/ppt/presentation/create") {
      return new Response(JSON.stringify({ detail: "rejected" }), { status: 409 });
    }
    return new Response(JSON.stringify({ state: "expired", asset_ids: [] }), {
      status: 200,
    });
  }, async () => {
    const admission = await PresentationGenerationApi.uploadDoc([
      new File(["one"], "one.txt", { type: "text/plain" }),
    ]);
    await assert.rejects(
      PresentationGenerationApi.createPresentation({
        presentation_id: "presentation-create-failure",
        idempotency_key: "presentation-create-failure",
        content: "",
        n_slides: 1,
        reference_snapshot_ids: [admission.referenceSnapshotId],
        language: "ko",
      }),
      /Failed to create presentation/,
    );
  });
  assert.equal(
    calls.some(
      ([url, method]) =>
        url ===
          "/api/v1/ppt/references/snapshots/snapshot-create-failure" &&
        method === "DELETE",
    ),
    true,
  );
  assert.equal(
    calls.some(
      ([url, method]) =>
        url === "/api/v1/ppt/assets/documents/asset-create-failure" &&
        method === "DELETE",
    ),
    true,
  );
});

test("decomposeDocuments falls back to the synchronous route when jobs are unavailable", async () => {
  const { PresentationGenerationApi } = await loadApi();
  const calls = [];
  await withFetch(async (url, init) => {
    calls.push([url, init?.method, init?.body]);
    if (url === "/api/decompose-documents/jobs") {
      return new Response(JSON.stringify({ detail: "Not Found" }), {
        status: 404,
      });
    }
    return new Response(JSON.stringify([
      { name: "source.pdf", file_path: "/tmp/source.txt" },
    ]), { status: 200 });
  }, async () => {
    const progress = [];
    const result = await PresentationGenerationApi.decomposeDocuments(
      ["source.pdf"],
      "ko",
      { onProgress: (job) => progress.push(job.status) },
    );
    assert.deepEqual(result, [
      { name: "source.pdf", file_path: "/tmp/source.txt" },
    ]);
    assert.deepEqual(progress, ["succeeded"]);
  });
  assert.deepEqual(calls, [
    [
      "/api/decompose-documents/jobs",
      "POST",
      JSON.stringify({ file_paths: ["source.pdf"], language: "ko" }),
    ],
    [
      "/api/decompose-documents",
      "POST",
      JSON.stringify({ file_paths: ["source.pdf"], language: "ko" }),
    ],
  ]);
});

test("decomposeDocuments exposes the failed job id as a safe diagnostic reference", async () => {
  const { PresentationGenerationApi } = await loadApi();
  await withFetch(async (_url, init) => {
    if (init?.method === "POST") {
      return new Response(JSON.stringify({
        job_id: "job-failed-47",
        status: "queued",
        progress: { completed_files: 0, total_files: 1 },
        result: null,
        error: null,
      }), { status: 202 });
    }
    return new Response(JSON.stringify({
      job_id: "job-failed-47",
      status: "failed",
      progress: {
        completed_files: 0,
        total_files: 1,
        current_file: "broken.pdf",
      },
      result: null,
      error: {
        code: "DOCUMENT_DECOMPOSE_FAILED",
        message: "Document processing failed. Please try again.",
        filename: "broken.pdf",
      },
    }), { status: 200 });
  }, async () => {
    await assert.rejects(
      PresentationGenerationApi.decomposeDocuments(["broken.pdf"], null, {
        pollIntervalMs: 0,
      }),
      (error) => {
        assert.equal(error.message, "Document processing failed. Please try again.");
        assert.equal(error.requestId, "job-failed-47");
        return true;
      },
    );
  });
});

test("decomposeDocuments cancels its server job when its signal is aborted", async () => {
  const { PresentationGenerationApi } = await loadApi();
  const controller = new AbortController();
  const calls = [];
  const consoleErrors = [];
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
  }, { onConsoleError: (...args) => consoleErrors.push(args) });
  assert.deepEqual(calls, [
    ["/api/decompose-documents/jobs", "POST"],
    ["/api/decompose-documents/jobs/job-cancel", "DELETE"],
  ]);
  assert.deepEqual(consoleErrors, []);
});
