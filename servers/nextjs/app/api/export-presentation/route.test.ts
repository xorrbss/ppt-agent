import assert from "node:assert/strict";
import test from "node:test";

import {
  executeExportPresentationRouteRequest,
  ExportPresentationRequestError,
  fetchPersistedPresentationForExport,
  readExportPresentationRouteBody,
} from "../../../lib/export-presentation-route.ts";
import {
  PRESENTATION_SOURCE_SHA256,
  type PresentationExportExecutionParams,
} from "../../../lib/presentation-export-strategy.ts";

async function rejectedRequestError(
  operation: Promise<unknown>
): Promise<ExportPresentationRequestError> {
  try {
    await operation;
  } catch (error) {
    assert.ok(error instanceof ExportPresentationRequestError);
    return error;
  }
  assert.fail("expected request error");
}

function boundarySpies(presentation: Record<string, unknown>) {
  const calls = { fetch: 0, general: 0, hybrid: 0 };
  return {
    calls,
    dependencies: {
      async packageAvailable() {
        return true;
      },
      async fetchPresentation(presentationId: string, cookieHeader: string) {
        calls.fetch += 1;
        assert.equal(presentationId, "persisted-id");
        assert.equal(cookieHeader, "session=test");
        return presentation;
      },
      registry: {
        async general(_params: PresentationExportExecutionParams) {
          calls.general += 1;
          return { path: "general.pptx" };
        },
        async hybrid(_params: PresentationExportExecutionParams) {
          calls.hybrid += 1;
          return { path: "hybrid.pptx" };
        },
      },
    },
  };
}

test("route request fetches persisted V2 identity and calls only general", async () => {
  const { calls, dependencies } = boundarySpies({
    version: "v2-standard",
    mode: "template",
    slides: [{ ui: { id: "native" }, html_content: null }],
  });

  const result = await executeExportPresentationRouteRequest(
    {
      format: "pptx",
      id: "persisted-id",
      pptxMode: "hybrid",
    },
    "session=test",
    dependencies
  );

  assert.equal(result.path, "general.pptx");
  assert.deepEqual(calls, { fetch: 1, general: 1, hybrid: 0 });
});

test("route request keeps legacy identity on general even with hybrid hint", async () => {
  const { calls, dependencies } = boundarySpies({
    version: "v1-standard",
    mode: "template",
    slides: [{ ui: null, html_content: null }],
  });

  const result = await executeExportPresentationRouteRequest(
    {
      format: "pptx",
      id: "persisted-id",
      pptxMode: "hybrid",
    },
    "session=test",
    dependencies
  );

  assert.equal(result.path, "general.pptx");
  assert.deepEqual(calls, { fetch: 1, general: 1, hybrid: 0 });
});

test("route request sends authored to hybrid only for explicit hybrid", async () => {
  const { calls, dependencies } = boundarySpies({
    version: "v1-standard",
    mode: null,
    slides: [
      {
        layout_group: "authored",
        content: { __authored__: true },
        ui: null,
        html_content: "<section>editable</section>",
      },
    ],
  });

  const result = await executeExportPresentationRouteRequest(
    {
      format: "pptx",
      id: "persisted-id",
      pptxMode: "hybrid",
    },
    "session=test",
    dependencies
  );

  assert.equal(result.path, "hybrid.pptx");
  assert.deepEqual(calls, { fetch: 1, general: 0, hybrid: 1 });
});

test("route request forwards explicit font embedding only to authored hybrid", async () => {
  let receivedFontEmbedding: boolean | undefined;
  const { dependencies } = boundarySpies({
    version: "v1-standard",
    mode: "authored",
    slides: [{ ui: null, html_content: "<section>editable</section>" }],
  });
  dependencies.registry.hybrid = async (
    params: PresentationExportExecutionParams
  ) => {
    receivedFontEmbedding = params.fontEmbedding;
    return { path: "hybrid-embedded.pptx" };
  };

  const result = await executeExportPresentationRouteRequest(
    {
      format: "pptx",
      id: "persisted-id",
      pptxMode: "hybrid",
      fontEmbedding: true,
    },
    "session=test",
    dependencies
  );

  assert.equal(result.path, "hybrid-embedded.pptx");
  assert.equal(receivedFontEmbedding, true);
});

test("route request defaults font embedding off and rejects invalid values", async () => {
  let receivedFontEmbedding: boolean | undefined;
  const { dependencies } = boundarySpies({
    version: "v1-standard",
    mode: "authored",
    slides: [{ ui: null, html_content: "<section>editable</section>" }],
  });
  dependencies.registry.hybrid = async (
    params: PresentationExportExecutionParams
  ) => {
    receivedFontEmbedding = params.fontEmbedding;
    return { path: "hybrid.pptx" };
  };

  await executeExportPresentationRouteRequest(
    { format: "pptx", id: "persisted-id", pptxMode: "hybrid" },
    "session=test",
    dependencies
  );
  assert.equal(receivedFontEmbedding, false);

  for (const fontEmbedding of ["true", 1, null, {}, []]) {
    const error = await rejectedRequestError(
      executeExportPresentationRouteRequest(
        {
          format: "pptx",
          id: "persisted-id",
          pptxMode: "hybrid",
          fontEmbedding,
        },
        "session=test",
        dependencies
      )
    );
    assert.equal(error.status, 400);
    assert.equal(error.message, "Invalid font embedding option");
  }
});

test("route request rejects font embedding outside hybrid PPTX", async () => {
  const { dependencies } = boundarySpies({
    version: "v1-standard",
    mode: "authored",
    slides: [{ ui: null, html_content: "<section>editable</section>" }],
  });
  for (const request of [
    { format: "pptx", pptxMode: "fidelity" },
    { format: "pdf", pptxMode: "hybrid" },
  ] as const) {
    const error = await rejectedRequestError(
      executeExportPresentationRouteRequest(
        {
          ...request,
          id: "persisted-id",
          fontEmbedding: true,
        },
        "session=test",
        dependencies
      )
    );
    assert.equal(error.status, 400);
    assert.equal(
      error.message,
      "Font embedding requires hybrid PPTX export"
    );
  }
});

test("persisted identity cannot redirect an embedding request to general export", async () => {
  const { calls, dependencies } = boundarySpies({
    version: "v2-standard",
    mode: "template",
    slides: [{ ui: { id: "native" }, html_content: null }],
  });
  await assert.rejects(
    executeExportPresentationRouteRequest(
      {
        format: "pptx",
        id: "persisted-id",
        pptxMode: "hybrid",
        fontEmbedding: true,
      },
      "session=test",
      dependencies
    ),
    /font_embedding_requires_authored_hybrid_export/
  );
  assert.deepEqual(calls, { fetch: 1, general: 0, hybrid: 0 });
});

test("route request preserves additive hybrid quality metadata", async () => {
  const quality = {
    schemaVersion: "presenton.export-quality/v1",
    mode: "hybrid",
    status: "partially-editable",
    nativeGroupElements: 2,
    fallbackReasonCounts: { "clip-path": 1 },
    fontEmbeddingStatus: {
      policy: "opt-in",
      requested: false,
      applied: false,
      embeddedFontFiles: 0,
      reason: "not-requested",
    },
  };
  const { dependencies } = boundarySpies({
    version: "v1-standard",
    mode: "authored",
    slides: [{ ui: null, html_content: "<section>editable</section>" }],
  });
  dependencies.registry.hybrid = async () => ({
    path: "hybrid.pptx",
    quality,
  });

  const result = await executeExportPresentationRouteRequest(
    {
      format: "pptx",
      id: "persisted-id",
      pptxMode: "hybrid",
    },
    "session=test",
    dependencies
  );

  assert.equal(result.path, "hybrid.pptx");
  assert.deepEqual(
    (result as { quality?: unknown }).quality,
    quality
  );
});

for (const [format, pptxMode] of [
  ["pptx", undefined],
  ["pptx", "fidelity"],
  ["pdf", "hybrid"],
] as const) {
  test(`route request keeps authored ${format}/${String(pptxMode)} on general`, async () => {
    const { calls, dependencies } = boundarySpies({
      version: "v1-standard",
      mode: "authored",
      slides: [{ ui: null, html_content: "<section>editable</section>" }],
    });

    const result = await executeExportPresentationRouteRequest(
      { format, id: "persisted-id", pptxMode },
      "session=test",
      dependencies
    );

    assert.equal(result.path, "general.pptx");
    assert.deepEqual(calls, { fetch: 1, general: 1, hybrid: 0 });
  });
}

test("route request rejects conflicting identity before either runner", async () => {
  const { calls, dependencies } = boundarySpies({
    version: "v2-standard",
    mode: "template",
    theme: { mode: "adaptive" },
    slides: [{ ui: { id: "native" }, html_content: null }],
  });

  await assert.rejects(
    executeExportPresentationRouteRequest(
      { format: "pptx", id: "persisted-id", pptxMode: "hybrid" },
      "session=test",
      dependencies
    ),
    /presentation_identity_conflict/
  );
  assert.deepEqual(calls, { fetch: 1, general: 0, hybrid: 0 });
});

test("route body parsing rejects malformed and non-object JSON with 400", async () => {
  const malformed = await rejectedRequestError(
    readExportPresentationRouteBody({
      async json() {
        throw new SyntaxError("unexpected token");
      },
    })
  );
  assert.equal(malformed.status, 400);
  assert.equal(malformed.message, "Invalid JSON body");

  for (const value of [null, [], "pptx", 1, true]) {
    const error = await rejectedRequestError(
      readExportPresentationRouteBody({
        async json() {
          return value;
        },
      })
    );
    assert.equal(error.status, 400);
    assert.equal(error.message, "Invalid JSON body");
  }

  assert.deepEqual(
    await readExportPresentationRouteBody({
      async json() {
        return { format: "pptx", id: "persisted-id" };
      },
    }),
    { format: "pptx", id: "persisted-id" }
  );
});

test("presentation fetch preserves safe client status without exposing backend body", async () => {
  for (const [backendStatus, expectedStatus] of [
    [400, 400],
    [401, 401],
    [403, 403],
    [404, 404],
    [422, 400],
    [429, 429],
    [500, 502],
    [503, 502],
  ] as const) {
    const error = await rejectedRequestError(
      fetchPersistedPresentationForExport("persisted/id", {
        baseUrl: "http://fastapi.test",
        timeoutMs: 100,
        maxResponseBytes: 1024,
        async fetchImpl(input) {
          assert.equal(
            String(input),
            "http://fastapi.test/api/v1/ppt/presentation/persisted%2Fid"
          );
          return new Response("private backend diagnostic", {
            status: backendStatus,
          });
        },
      })
    );
    assert.equal(error.status, expectedStatus);
    assert.doesNotMatch(error.message, /private backend diagnostic/);
  }
});

test("presentation fetch maps network and timeout failures safely", async () => {
  const networkError = await rejectedRequestError(
    fetchPersistedPresentationForExport("persisted-id", {
      baseUrl: "http://fastapi.test",
      timeoutMs: 100,
      maxResponseBytes: 1024,
      async fetchImpl() {
        throw new Error("connect ECONNREFUSED private-host");
      },
    })
  );
  assert.equal(networkError.status, 502);
  assert.equal(networkError.message, "Presentation service request failed");
  assert.doesNotMatch(networkError.message, /private-host/);

  const timeoutError = await rejectedRequestError(
    fetchPersistedPresentationForExport("persisted-id", {
      baseUrl: "http://fastapi.test",
      timeoutMs: 0,
      maxResponseBytes: 1024,
      async fetchImpl(_input, init) {
        return new Promise((_resolve, reject) => {
          init?.signal?.addEventListener(
            "abort",
            () => {
              const error = new Error("internal timeout detail");
              error.name = "AbortError";
              reject(error);
            },
            { once: true }
          );
        });
      },
    })
  );
  assert.equal(timeoutError.status, 504);
  assert.equal(timeoutError.message, "Presentation service timed out");
  assert.doesNotMatch(timeoutError.message, /internal timeout detail/);

  const streamTimeoutError = await rejectedRequestError(
    fetchPersistedPresentationForExport("persisted-id", {
      baseUrl: "http://fastapi.test",
      timeoutMs: 0,
      maxResponseBytes: 1024,
      async fetchImpl(_input, init) {
        return new Response(
          new ReadableStream({
            start(controller) {
              init?.signal?.addEventListener(
                "abort",
                () => {
                  const error = new Error("private stream timeout detail");
                  error.name = "AbortError";
                  controller.error(error);
                },
                { once: true }
              );
            },
          })
        );
      },
    })
  );
  assert.equal(streamTimeoutError.status, 504);
  assert.equal(streamTimeoutError.message, "Presentation service timed out");
  assert.doesNotMatch(streamTimeoutError.message, /private stream timeout detail/);
});

test("presentation fetch enforces a streamed byte limit before JSON parsing", async () => {
  let cancelled = false;
  const streamed = new ReadableStream<Uint8Array>({
    cancel() {
      cancelled = true;
    },
    start(controller) {
      controller.enqueue(new TextEncoder().encode('{"slides":"'));
      controller.enqueue(new Uint8Array(64));
    },
  });

  const error = await rejectedRequestError(
    fetchPersistedPresentationForExport("persisted-id", {
      baseUrl: "http://fastapi.test",
      timeoutMs: 100,
      maxResponseBytes: 16,
      async fetchImpl() {
        return new Response(streamed);
      },
    })
  );

  assert.equal(error.status, 502);
  assert.equal(
    error.message,
    "Presentation service returned an invalid response"
  );
  assert.equal(cancelled, true);
});

test("presentation fetch forwards only configured auth and cookie headers", async () => {
  const presentation = await fetchPersistedPresentationForExport(
    "persisted-id",
    {
      baseUrl: "http://fastapi.test/",
      authHeaders: { Authorization: "Basic test" },
      cookieHeader: "session=test",
      timeoutMs: 100,
      maxResponseBytes: 1024,
      async fetchImpl(input, init) {
        assert.equal(
          String(input),
          "http://fastapi.test/api/v1/ppt/presentation/persisted-id"
        );
        assert.deepEqual(init?.headers, {
          Accept: "application/json",
          Authorization: "Basic test",
          Cookie: "session=test",
        });
        return Response.json({
          version: "v2-standard",
          slides: [{ ui: { id: "native" } }],
        });
      },
    }
  );
  assert.equal(presentation.version, "v2-standard");
  assert.match(presentation[PRESENTATION_SOURCE_SHA256] ?? "", /^[0-9a-f]{64}$/);
});

test("general export receives the exact persisted snapshot digest", async () => {
  const body = JSON.stringify({
    version: "v2-standard",
    mode: "template",
    slides: [{ ui: { id: "native" }, html_content: null }],
  });
  const presentation = await fetchPersistedPresentationForExport(
    "persisted-id",
    {
      baseUrl: "http://fastapi.test",
      timeoutMs: 100,
      maxResponseBytes: 1024,
      async fetchImpl() {
        return new Response(body);
      },
    }
  );
  let renderedDigest: string | undefined;

  await executeExportPresentationRouteRequest(
    { format: "pptx", id: "persisted-id" },
    "session=test",
    {
      async packageAvailable() {
        return true;
      },
      async fetchPresentation() {
        return presentation;
      },
      registry: {
        async general(params) {
          renderedDigest = params.expectedPresentationSha256;
          return { path: "general.pptx" };
        },
        async hybrid() {
          throw new Error("hybrid must not run");
        },
      },
    }
  );

  assert.equal(
    renderedDigest,
    presentation[PRESENTATION_SOURCE_SHA256]
  );
});
