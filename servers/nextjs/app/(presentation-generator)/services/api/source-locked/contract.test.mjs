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
