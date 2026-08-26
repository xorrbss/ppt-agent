import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { transform } from "esbuild";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const sourcePath = path.join(testDirectory, "contract.ts");
const dataModule = (source) =>
  `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;

async function loadContract() {
  const source = await readFile(sourcePath, "utf8");
  const headerUrl = dataModule("export const getHeaderForFormData = () => ({});");
  const errorHandlerUrl = dataModule("export const ApiResponseHandler = { handleResponse: (r) => r.json() };");
  const apiUrl = dataModule("export const getApiUrl = (path) => path;");
  const retryUrl = dataModule("export const fetchWithNetworkRetry = (...args) => fetch(...args);");
  const executable = source
    .replace('from "../header";', `from "${headerUrl}";`)
    .replace('from "../api-error-handler";', `from "${errorHandlerUrl}";`)
    .replace('from "@/utils/api";', `from "${apiUrl}";`)
    .replace('from "@/utils/fetchWithNetworkRetry";', `from "${retryUrl}";`);
  const compiled = await transform(executable, {
    format: "esm",
    loader: "ts",
    target: "esnext",
  });
  return import(dataModule(compiled.code));
}

const sourceFile = (name) => ({
  name,
  size: 4,
  lastModified: 7,
  type: "application/octet-stream",
  arrayBuffer: async () => new TextEncoder().encode("PPTX").buffer,
});

test("source-locked admits exactly one non-macro PowerPoint or PDF", async () => {
  const { validateSourceLockedFiles } = await loadContract();
  assert.equal(validateSourceLockedFiles([]), "source_locked_requires_exactly_one_file");
  assert.equal(validateSourceLockedFiles([sourceFile("a.pptx"), sourceFile("b.pdf")]), "source_locked_requires_exactly_one_file");
  assert.equal(validateSourceLockedFiles([sourceFile("a.pptm")]), "source_locked_rejects_pptm");
  assert.equal(validateSourceLockedFiles([sourceFile("a.docx")]), "source_locked_rejects_file_type");
  assert.equal(validateSourceLockedFiles([sourceFile("a.pdf")]), null);
});

test("source-locked summary fails closed unless source pages equal output slides", async () => {
  const { sourceLockedSummaryFromResponse } = await loadContract();
  const response = (slide_count) => ({
    admission_contract: {}, source_snapshot: {}, manifest: {}, analysis_seal: {},
    progress_stages: [], fallback_allowed: false,
    summary: {
      source_type: "pptx", page_count: 29, slide_count,
      media_asset_count: 7, image_occurrence_count: 10, inherited_occurrence_count: 2,
    },
  });
  assert.equal(sourceLockedSummaryFromResponse(response(28)), null);
  assert.deepEqual(sourceLockedSummaryFromResponse(response(29)), {
    source_format: "pptx", source_page_count: 29, output_slide_count: 29,
    media_asset_count: 7, image_occurrence_count: 10, inherited_image_occurrence_count: 2,
  });
});

test("source-locked payload digest is stable for the same source identity", async () => {
  const { sourceLockedPayloadDigest } = await loadContract();
  const file = sourceFile("original.pptx");
  assert.equal(await sourceLockedPayloadDigest([file]), await sourceLockedPayloadDigest([file]));
  assert.match(await sourceLockedPayloadDigest([file]), /^sha256:[0-9a-f]{64}$/);
});

const generatedResponse = (stages = [
  { stage: "source_analysis", status: "completed" },
  { stage: "slide_image_restoration", status: "completed" },
  { stage: "missing_content_validation", status: "completed" },
]) => ({
  admission_contract: {}, source_snapshot: {}, manifest: { missing_content_count: 0 }, analysis_seal: {},
  progress_stages: stages, fallback_allowed: false, generation_record_id: "record-1",
  idempotent_replay: false,
  summary: {
    source_type: "pptx", page_count: 2, slide_count: 2,
    media_asset_count: 1, image_occurrence_count: 2, inherited_occurrence_count: 0,
  },
  restoration_verification: {
    contract_version: "source-locked-restoration-verification/v1",
    output_package_sha256: `sha256:${"a".repeat(64)}`, slide_count: 2,
    rendered_page_count: 2, missing_content_count: 0, byte_exact_source_package: true, fallback_allowed: false,
  },
  artifact: {
    contract_version: "source-locked-artifact/v1", artifact_id: `sla_${"b".repeat(64)}`,
    filename: "source-locked-0123456789abcdef.pptx", media_type: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    byte_length: 1234, sha256: `sha256:${"a".repeat(64)}`,
    download_url: `/api/v1/ppt/source-locked/v1/artifacts/sla_${"b".repeat(64)}`,
    created_at: "2026-08-25T00:00:00Z", source_type: "pptx", slide_count: 2,
  },
});

test("source-locked generate posts the v1 route and exposes terminal artifact only after all stages", async () => {
  const { SourceLockedGenerationApiV1, sourceLockedGenerationFromResponse } = await loadContract();
  const originalFetch = globalThis.fetch;
  let request;
  globalThis.fetch = async (url, init) => {
    request = { url, init };
    return { ok: true, json: async () => generatedResponse() };
  };
  try {
    const file = new File(["PPTX"], "original.pptx", { type: "application/octet-stream" });
    const response = await SourceLockedGenerationApiV1.generate([file], "source-locked-generate:v1:test");
    assert.equal(request.url, "/api/v1/ppt/source-locked/v1/generate");
    assert.equal(request.init.headers["Idempotency-Key"], "source-locked-generate:v1:test");
    assert.match(request.init.headers["X-Idempotency-Payload-Digest"], /^sha256:[0-9a-f]{64}$/);
    assert.equal(request.init.body.get("generation_mode"), "source_locked");
    assert.match(sourceLockedGenerationFromResponse(response).artifact.artifact_id, /^sla_/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("source-locked async jobs use dedicated create, status, and cancel routes", async () => {
  const { SourceLockedGenerationApiV1 } = await loadContract();
  const originalFetch = globalThis.fetch;
  const requests = [];
  const job = {
    contract_version: "source-locked-job/v1",
    job_id: "slj_test",
    status: "running",
    source_filename: "original.pptx",
    cancel_requested: false,
    idempotent_replay: false,
    result: null,
    error: null,
    fallback_allowed: false,
    progress_stages: [{ stage: "source_analysis", status: "running" }],
    created_at: "2026-08-25T00:00:00Z",
    updated_at: "2026-08-25T00:00:01Z",
  };
  globalThis.fetch = async (url, init = {}) => {
    requests.push({ url, init });
    return { ok: true, json: async () => job };
  };
  try {
    const file = new File(["PPTX"], "original.pptx", { type: "application/octet-stream" });
    await SourceLockedGenerationApiV1.createJob([file], "source-locked-job:v1:test");
    await SourceLockedGenerationApiV1.listJobs(500);
    await SourceLockedGenerationApiV1.getJob("slj_test/a");
    await SourceLockedGenerationApiV1.cancelJob("slj_test/a");
    assert.equal(requests[0].url, "/api/v1/ppt/source-locked/v1/jobs");
    assert.equal(requests[0].init.headers["Idempotency-Key"], "source-locked-job:v1:test");
    assert.equal(requests[1].url, "/api/v1/ppt/source-locked/v1/jobs?limit=200");
    assert.equal(requests[1].init.method, "GET");
    assert.equal(requests[2].url, "/api/v1/ppt/source-locked/v1/jobs/slj_test%2Fa");
    assert.equal(requests[2].init.method, "GET");
    assert.equal(requests[3].url, "/api/v1/ppt/source-locked/v1/jobs/slj_test%2Fa/cancel");
    assert.equal(requests[3].init.method, "POST");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("source-locked async jobs expose a download only from a sealed nested result", async () => {
  const { sourceLockedGenerationFromJob } = await loadContract();
  const job = {
    contract_version: "source-locked-job/v1",
    job_id: "sljob_" + "c".repeat(64),
    status: "succeeded",
    source_filename: "original.pptx",
    progress_stages: generatedResponse().progress_stages,
    cancel_requested: false,
    idempotent_replay: false,
    fallback_allowed: false,
    result: generatedResponse(),
    error: null,
    created_at: "2026-08-25T00:00:00Z",
    updated_at: "2026-08-25T00:01:00Z",
  };
  assert.match(sourceLockedGenerationFromJob(job).artifact.artifact_id, /^sla_/);
  job.result.restoration_verification.missing_content_count = 1;
  assert.equal(sourceLockedGenerationFromJob(job), null);
});

test("source-locked refuses download state when restoration or validation is incomplete", async () => {
  const { sourceLockedGenerationFromResponse } = await loadContract();
  assert.equal(sourceLockedGenerationFromResponse(generatedResponse([
    { stage: "source_analysis", status: "completed" },
    { stage: "slide_image_restoration", status: "failed" },
    { stage: "missing_content_validation", status: "pending" },
  ])), null);
  const missing = generatedResponse();
  missing.restoration_verification.missing_content_count = 1;
  assert.equal(sourceLockedGenerationFromResponse(missing), null);
  const pptxNotByteExact = generatedResponse();
  pptxNotByteExact.restoration_verification.byte_exact_source_package = false;
  assert.equal(sourceLockedGenerationFromResponse(pptxNotByteExact), null);
  const pptReadbackIncomplete = generatedResponse();
  pptReadbackIncomplete.summary.source_type = "ppt";
  pptReadbackIncomplete.restoration_verification.byte_exact_source_package = false;
  pptReadbackIncomplete.restoration_verification.rendered_page_count = 1;
  assert.equal(sourceLockedGenerationFromResponse(pptReadbackIncomplete), null);
});
