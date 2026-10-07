import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { build, transform } from "esbuild";

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
  export class ApiError extends Error {
    constructor(message, options = {}) {
      super(message);
      this.status = typeof options === "number" ? options : options.status ?? 0;
      this.code = options.code;
      this.detail = options.detail;
    }
  }
  export const apiErrorFromPayload = (payload, fallback, _status, requestId) => {
    const error = new Error(payload?.message || fallback);
    error.code = payload?.code;
    error.requestId = payload?.request_id || requestId;
    return error;
  };
  export const ApiResponseHandler = {
    async handleResponse(response, fallback) {
      if (!response.ok) {
        const payload = await response.json();
        throw new ApiError(fallback, { status: response.status, code: payload.error?.code || payload.detail?.code || payload.detail });
      }
      return response.json();
    },
  };
`);
const apiUrl = dataModule("export const getApiUrl = (path) => path; export const buildAbsoluteApiRequestUrl = (path) => 'http://slide-order.test' + path; export const resolveBackendAssetUrl = (value) => value;\n");
const retryUrl = dataModule("export const fetchWithNetworkRetry = (...args) => fetch(...args);\n");

async function loadApi({ realErrors = false } = {}) {
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
  // Bundle the existing HTTP timeline client and its real projection so these
  // cases exercise the snake_case wire contract, pagination, and error parser.
  const workspace = await build({
    entryPoints: [path.join(testDirectory, "workspace-commands.ts")],
    bundle: true, write: false, format: "esm", platform: "browser",
    plugins: [{ name: "test-aliases", setup(builder) {
      builder.onResolve({ filter: /^@\// }, ({ path: modulePath }) => {
        if (modulePath === "@/utils/api") return { path: "api", namespace: "fixture" };
        return { path: path.join(testDirectory, "../../../..", modulePath.slice(2) + ".ts") };
      });
      builder.onResolve({ filter: /^\.\/header$/ }, () => ({ path: "header", namespace: "fixture" }));
      builder.onLoad({ filter: /.*/, namespace: "fixture" }, ({ path: modulePath }) => ({
        contents: modulePath === "header"
          ? 'export const getHeader = () => ({"Content-Type":"application/json"});'
          : 'export const getApiUrl = path => path; export const buildAbsoluteApiRequestUrl = path => "http://slide-order.test" + path;',
      }));
    } }],
  });
  const workspaceUrl = dataModule(workspace.outputFiles[0].text);
  let selectedErrorHandlerUrl = errorHandlerUrl;
  if (realErrors) {
    const messageUrl = dataModule('export const pickBackendMessage = value => value; export const translateBackendMessage = value => typeof value === "string" ? value : value?.message;');
    const errorSource = await readFile(path.join(testDirectory, "api-error-handler.ts"), "utf8");
    const errorCompiled = await transform(errorSource
      .replace('from "@/lib/backend-messages";', `from "${messageUrl}";`)
      .replace('from "@/lib/backend-messages-intl";', `from "${messageUrl}";`), { loader: "ts", format: "esm" });
    selectedErrorHandlerUrl = dataModule(errorCompiled.code);
  }
  const executableSource = source
    .replace('from "./header";', `from "${headerUrl}";`)
    .replace('from "./params";', `from "${paramsUrl}";`)
    .replace('from "./api-error-handler";', `from "${selectedErrorHandlerUrl}";`)
    .replace('from "./workspace-commands";', `from "${workspaceUrl}";`)
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

const orderOperationId = "22222222-2222-4222-8222-222222222222";
const orderBody = { slide_ids: ["slide-b", "slide-a"], expected_revision: 3 };
const orderPollOptions = { pollIntervalMs: 0, timeoutMs: 500 };
const orderJson = (data, status = 200, headers = {}) =>
  new Response(JSON.stringify(data), { status, headers });
const orderPending = () => orderJson({ detail: "slide_order_still_running", error: { code: "slide_order_still_running" } }, 503, {
  "X-Workspace-Operation-ID": orderOperationId,
});
const orderAnchor = (status = "running", overrides = {}) => ({
  schema_version: 1, anchor_type: "operation", id: orderOperationId,
  operation_id: orderOperationId, position: 10, operation_type: "presentation.slides.reorder",
  title: "Reorder slides", status, version: 1, operation_version: 1,
  expected_revision: 3, result_revision: status === "succeeded" ? 4 : null,
  target: { presentation_id: "deck-a", summary: "Slides", context: {} },
  result: status === "succeeded" ? { order: orderBody.slide_ids } : null,
  events: [], allowed_actions: [], ...overrides,
});
const orderPage = (items, extra = {}) => orderJson({ schema_version: 1, items, has_more: false, revision: 4, ...extra });

test("reorder: pending follows the admitted operation through owner-scoped GET without resubmitting", async () => {
  const { PresentationGenerationApi } = await loadApi({ realErrors: true });
  const calls = [];
  let reads = 0;
  let pendingId;
  await withFetch(async (url, init) => {
    const method = init?.method ?? "GET";
    calls.push(method);
    if (method === "PATCH") return orderPending();
    assert.equal(new URL(url).pathname, "/api/v1/ppt/workspace/presentations/deck-a/timeline");
    reads++;
    return orderPage([orderAnchor("succeeded", { id: "foreign", operation_id: "foreign" }),
      orderAnchor(reads === 1 ? "running" : "succeeded")]);
  }, async () => {
    const result = await PresentationGenerationApi.reorderSlides("deck-a", orderBody, {
      ...orderPollOptions, onPending: id => { pendingId = id; },
    });
    assert.equal(pendingId, orderOperationId);
    assert.deepEqual(result, { presentation_id: "deck-a", revision: 4, order: orderBody.slide_ids, operation_id: orderOperationId });
  });
  assert.deepEqual(calls, ["PATCH", "GET", "GET"]);
});

test("reorder: fast 200 and existing 409 retain their original paths", async () => {
  const { PresentationGenerationApi } = await loadApi({ realErrors: true });
  for (const status of [200, 409, 503]) {
    let calls = 0;
    await withFetch(async (_url, init) => {
      calls++;
      assert.equal(init.method, "PATCH");
      return orderJson(status === 200 ? { revision: 4 } : { detail: status === 409 ? "revision_conflict" : "slide_order_still_running" }, status);
    }, async () => {
      if (status === 200) assert.equal((await PresentationGenerationApi.reorderSlides("deck-a", orderBody)).revision, 4);
      else await assert.rejects(PresentationGenerationApi.reorderSlides("deck-a", orderBody), { status });
    });
    assert.equal(calls, 1);
  }
});

test("reorder: terminal failure, cancellation, deadline and dead letter never become success", async () => {
  const { PresentationGenerationApi } = await loadApi({ realErrors: true });
  for (const state of ["failed", "cancelled", "deadline_exceeded", "dead_letter"]) {
    let patches = 0;
    await withFetch(async (_url, init) => {
      if (init?.method === "PATCH") { patches++; return orderPending(); }
      return orderPage([orderAnchor(state)]);
    }, async () => {
      await assert.rejects(PresentationGenerationApi.reorderSlides("deck-a", orderBody, orderPollOptions), error => {
        assert.equal(error.status, 422);
        assert.equal(error.detail.state, state);
        return true;
      });
    });
    assert.equal(patches, 1);
  }
});

test("reorder: foreign scope, revision and order are rejected before constructing a result", async () => {
  const { PresentationGenerationApi } = await loadApi({ realErrors: true });
  for (const overrides of [
    { target: { presentation_id: "foreign" } }, { expected_revision: 99 },
    { result: { order: [...orderBody.slide_ids].reverse() } }, { result_revision: null },
  ]) {
    await withFetch(async (_url, init) => init?.method === "PATCH" ? orderPending() : orderPage([orderAnchor("succeeded", overrides)]), async () => {
      await assert.rejects(PresentationGenerationApi.reorderSlides("deck-a", orderBody, orderPollOptions), error => {
        assert.match(error.code, /^slide_order_(operation|result)_mismatch$/);
        return true;
      });
    });
  }
});

test("reorder: bounded wait and GET failure remain unconfirmed; checking again uses GET only", async () => {
  const { PresentationGenerationApi } = await loadApi({ realErrors: true });
  let patches = 0;
  await withFetch(async (_url, init) => {
    if (init?.method === "PATCH") { patches++; return orderPending(); }
    return orderPage([orderAnchor()]);
  }, async () => {
    await assert.rejects(PresentationGenerationApi.reorderSlides("deck-a", orderBody, { timeoutMs: 5, pollIntervalMs: 1 }), { code: "slide_order_poll_timeout" });
  });
  await withFetch(async () => orderJson({ detail: "presentation_not_found" }, 404), async () => {
    await assert.rejects(PresentationGenerationApi.waitForSlideOrderOperation("deck-a", orderBody, orderOperationId, orderPollOptions), { code: "slide_order_status_unavailable" });
  });
  await withFetch(async (_url, init) => {
    assert.notEqual(init?.method, "PATCH");
    return orderPage([orderAnchor("succeeded")]);
  }, async () => {
    assert.equal((await PresentationGenerationApi.waitForSlideOrderOperation("deck-a", orderBody, orderOperationId, orderPollOptions)).revision, 4);
  });
  assert.equal(patches, 1);
});

test("reorder: an older admitted operation is found through timeline pagination", async () => {
  const { PresentationGenerationApi } = await loadApi({ realErrors: true });
  const positions = [];
  await withFetch(async (url, init) => {
    if (init?.method === "PATCH") return orderPending();
    const before = new URL(url).searchParams.get("before_position");
    positions.push(before);
    return before === null
      ? orderPage([], { has_more: true, next_before_position: 10 })
      : orderPage([orderAnchor("succeeded")]);
  }, async () => {
    assert.equal((await PresentationGenerationApi.reorderSlides("deck-a", orderBody, orderPollOptions)).revision, 4);
  });
  assert.deepEqual(positions, [null, "10"]);
});

test("reorder: SidePanel blocks duplicate drags and keeps edits made while the save is pending", async () => {
  const sidePanelPath = path.join(testDirectory, "../../presentation/components/SidePanel.tsx");
  const source = await readFile(sidePanelPath, "utf8");
  const bindings = new Set();
  for (const match of source.matchAll(/import\s+([\s\S]*?)\s+from\s+["'][^"']+["'];/g)) {
    const named = match[1].match(/\{([\s\S]*?)\}/)?.[1] ?? "";
    for (const binding of named.split(",")) {
      const name = binding.trim().replace(/^type\s+/, "").split(/\s+as\s+/)[0];
      if (/^\w+$/.test(name)) bindings.add(name);
    }
  }
  const compiled = await build({
    entryPoints: [sidePanelPath], bundle: true, write: false, format: "esm", jsx: "transform",
    plugins: [{ name: "component-fixtures", setup(builder) {
      builder.onResolve({ filter: /.*/ }, args => args.kind === "entry-point" ? undefined : { path: args.path, namespace: "fixture" });
      builder.onLoad({ filter: /.*/, namespace: "fixture" }, () => ({ contents:
        'const fixture = globalThis.__orderSidePanel; export default fixture.React; export const jsx = (type, props) => fixture.React.createElement(type, props, props.children); export const jsxs = jsx;\n' +
        [...bindings].map(name => `export const ${name} = fixture.${name} ?? "${name}";`).join("\n"),
      }));
    } }],
  });
  let resolveSave;
  let patchCalls = 0;
  let current = { id: "deck-a", revision: 3, theme: { color: "original" },
    slides: [{ id: "slide-a", index: 0, content: "before" }, { id: "slide-b", index: 1, content: "before" }] };
  const previous = globalThis.__orderSidePanel;
  const previousAnimationFrame = globalThis.requestAnimationFrame;
  globalThis.requestAnimationFrame = callback => { callback(); return 0; };
  globalThis.__orderSidePanel = {
    React: { createElement: (type, props, ...children) => ({ type, props, children }) },
    useEffect: () => {}, useRef: value => ({ current: value }), useState: value => [value, () => {}],
    useTranslations: () => key => key, usePathname: () => "/presentation",
    useSelector: () => ({ presentationData: current, isStreaming: false, flaggedSlides: {} }),
    useStore: () => ({ getState: () => ({ presentationGeneration: { presentationData: current } }) }),
    useDispatch: () => action => { current = action.payload; },
    setPresentationData: payload => ({ payload }), isTemplateFreePresentation: () => false,
    useSensors: () => [], useSensor: () => null, trackEvent: () => {}, MixpanelEvent: {},
    arrayMove: (items, from, to) => { const result = [...items]; result.splice(to, 0, result.splice(from, 1)[0]); return result; },
    flaggedSlideKey: () => "", notify: { error: () => assert.fail("pending save must not show failure") },
    PresentationGenerationApi: { reorderSlides: (_id, _body, options) => {
      patchCalls++;
      options.onPending(orderOperationId);
      return new Promise(resolve => { resolveSave = resolve; });
    } },
  };
  try {
    const { default: SidePanel } = await import(dataModule(compiled.outputFiles[0].text));
    const tree = SidePanel({ selectedSlide: 0, onSlideClick: () => {}, presentationId: "deck-a", loading: false });
    const find = node => {
      if (node?.type === "DndContext") return node;
      for (const child of node?.children?.flat(Infinity) ?? []) {
        const result = find(child); if (result) return result;
      }
    };
    const drag = find(tree).props.onDragEnd;
    drag({ active: { id: "slide-b" }, over: { id: "slide-a" } });
    drag({ active: { id: "slide-a" }, over: { id: "slide-b" } });
    assert.equal(patchCalls, 1);
    current = { ...current, theme: { color: "edited" }, slides: current.slides.map(slide => ({ ...slide, content: "edited" })) };
    resolveSave({ presentation_id: "deck-a", revision: 4 });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(current.revision, 4);
    assert.equal(current.theme.color, "edited");
    assert.deepEqual(current.slides.map(slide => slide.content), ["edited", "edited"]);
    assert.deepEqual(current.slides.map(slide => slide.id), orderBody.slide_ids);
  } finally {
    globalThis.__orderSidePanel = previous;
    globalThis.requestAnimationFrame = previousAnimationFrame;
  }
});

test("uploadDoc isolates idempotency keys between presentation workspaces", async () => {
  const { PresentationGenerationApi } = await loadApi();
  const uploadKeys = [];
  const snapshotKeys = [];
  let requestNumber = 0;
  const document = new File(["same document"], "reference.pdf", {
    type: "application/pdf",
    lastModified: 1_700_000_000_000,
  });

  await withFetch(async (url, init) => {
    const key = init?.headers?.["Idempotency-Key"];
    if (url === "/api/v1/ppt/assets/documents") {
      uploadKeys.push(key);
      requestNumber += 1;
      return new Response(JSON.stringify({
        asset_id: `asset-${requestNumber}`,
        kind: "document",
        state: "ready",
        size_bytes: document.size,
        sha256: "a".repeat(64),
        version: 1,
      }), { status: 200 });
    }
    if (url === "/api/v1/ppt/references/snapshots") {
      snapshotKeys.push(key);
      return new Response(
        JSON.stringify({ snapshot_id: `snapshot-${requestNumber}` }),
        { status: 200 },
      );
    }
    throw new Error(`Unexpected URL: ${url}`);
  }, async () => {
    await PresentationGenerationApi.uploadDoc([document], "presentation-a");
    await PresentationGenerationApi.uploadDoc([document], "presentation-a");
    await PresentationGenerationApi.uploadDoc([document], "presentation-b");
  });

  assert.equal(uploadKeys[0], uploadKeys[1]);
  assert.notEqual(uploadKeys[0], uploadKeys[2]);
  assert.equal(snapshotKeys[0], snapshotKeys[1]);
  assert.notEqual(snapshotKeys[0], snapshotKeys[2]);
});

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
    ], "presentation-1");
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
    await PresentationGenerationApi.uploadDoc([first], "presentation-stable");
    await PresentationGenerationApi.uploadDoc([second], "presentation-stable");
  });
  assert.equal(observedKeys[0], observedKeys[2]);
  assert.equal(observedKeys[1], observedKeys[3]);
  assert.match(observedKeys[0], /^document-upload:v2:[a-f0-9]{64}:0$/);
  assert.match(observedKeys[1], /^document-snapshot:v2:[a-f0-9]{64}$/);
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
      ], "presentation-partial-failure"),
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
    ], "presentation-create-failure");
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
