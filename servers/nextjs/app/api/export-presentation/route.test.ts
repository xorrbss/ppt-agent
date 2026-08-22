import assert from "node:assert/strict";
import test from "node:test";

import {
  executeExportPresentationRouteRequest,
  ExportPresentationRequestError,
  fetchPersistedPresentationForExport,
  fetchPersistedPresentationProjectionForExport,
  readExportPresentationRouteBody,
} from "../../../lib/export-presentation-route.ts";
import {
  PRESENTATION_SOURCE_SHA256,
  type PresentationExportExecutionParams,
  type PersistedPresentation,
} from "../../../lib/presentation-export-strategy.ts";
import {
  PRESENTATION_DRIVER_REGISTRY,
  type PresentationDriverId,
} from "../../../lib/presentation-profile.ts";

const TEST_SOURCE_SHA256 = "d".repeat(64);

function templateV2Slide() {
  return {
    id: "template-slide",
    ui: {
      components: [
        {
          id: "body",
          position: { x: 0, y: 0 },
          elements: [
            {
              type: "text",
              name: "title",
              position: { x: 40, y: 40 },
              size: { width: 600, height: 80 },
              runs: [{ text: "Canonical Template V2" }],
            },
          ],
        },
      ],
    },
    html_content: null,
  };
}

function canonicalServerPresentation(
  input: Record<string, unknown>,
): PersistedPresentation {
  const driverId: PresentationDriverId =
    input.version === "v2-standard" ? "template-v2" : "authored";
  const driver = PRESENTATION_DRIVER_REGISTRY[driverId];
  const presentation = {
    id: "persisted-id",
    revision: 4,
    lifecycle_status: "published",
    ...input,
    profile: {
      schema_version: 1 as const,
      driver_id: driverId,
      content_model: driver.content_model,
      generation_strategy: driver.generation_strategy,
      style_source: driverId === "template-v2" ? "imported" : "authored",
      generation_mode: "standard",
    },
    capabilities: {
      schema_version: 1 as const,
      driver_id: driverId,
      renderer_adapter_id: driver.renderer_adapter_id,
      editor_adapter_id: driver.editor_adapter_id,
      editor: driver.editor,
      agent_tool_capability: driver.agent_tool_capability,
      export_adapter_id: driver.export_adapter_id,
      export_editable_pptx: driver.export_editable_pptx,
    },
  };
  Object.defineProperty(presentation, PRESENTATION_SOURCE_SHA256, {
    enumerable: false,
    value: TEST_SOURCE_SHA256,
  });
  return presentation as PersistedPresentation;
}

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
  const canonicalPresentation = canonicalServerPresentation(presentation);
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
        return canonicalPresentation;
      },
      registry: {
        async general(params: PresentationExportExecutionParams) {
          void params;
          calls.general += 1;
          return { path: "general.pptx" };
        },
        async hybrid(params: PresentationExportExecutionParams) {
          void params;
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
    slides: [templateV2Slide()],
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
  assert.equal(
    (result as { renderArtifact?: { driver_id: string; target: string } })
      .renderArtifact?.driver_id,
    "template-v2",
  );
  assert.deepEqual(calls, { fetch: 1, general: 1, hybrid: 0 });
});

test("route request sends authored to hybrid only for explicit hybrid", async () => {
  const { calls, dependencies } = boundarySpies({
    version: "v1-standard",
    mode: "authored",
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
  assert.equal(
    (result as { renderArtifact?: { driver_id: string } }).renderArtifact
      ?.driver_id,
    "authored",
  );
  assert.deepEqual(calls, { fetch: 1, general: 0, hybrid: 1 });
});

test("route request forwards explicit font embedding only to authored hybrid", async () => {
  let receivedFontEmbedding: boolean | undefined;
  let receivedExpectedRevision: number | undefined;
  const { dependencies } = boundarySpies({
    version: "v1-standard",
    mode: "authored",
    slides: [{ ui: null, html_content: "<section>editable</section>" }],
  });
  dependencies.registry.hybrid = async (
    params: PresentationExportExecutionParams
  ) => {
    receivedFontEmbedding = params.fontEmbedding;
    receivedExpectedRevision = params.expectedPresentationRevision;
    return { path: "hybrid-embedded.pptx" };
  };

  const result = await executeExportPresentationRouteRequest(
    {
      format: "pptx",
      id: "persisted-id",
      pptxMode: "hybrid",
      fontEmbedding: true,
      expectedPresentationRevision: 4,
    },
    "session=test",
    dependencies
  );

  assert.equal(result.path, "hybrid-embedded.pptx");
  assert.equal(receivedFontEmbedding, true);
  assert.equal(receivedExpectedRevision, 4);
});

test("route request defaults Hybrid font embedding on; explicit false is compatibility mode", async () => {
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
  assert.equal(receivedFontEmbedding, true);

  await executeExportPresentationRouteRequest(
    {
      format: "pptx",
      id: "persisted-id",
      pptxMode: "hybrid",
      fontEmbedding: false,
    },
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
    slides: [templateV2Slide()],
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

test("route request preserves additive authored-hybrid export metadata", async () => {
  const exportMetadata = {
    version: 1,
    strategy: "authored-hybrid",
    totalSlides: 3,
    editableSlides: 2,
    imageFallbackSlides: 1,
    fallbackReasons: [{ reason: "preflight-unsafe-css-url", slideCount: 1 }],
  };
  const { dependencies } = boundarySpies({
    version: "v1-standard",
    mode: "authored",
    slides: [{ ui: null, html_content: "<section>editable</section>" }],
  });
  dependencies.registry.hybrid = async () => ({
    path: "hybrid.pptx",
    exportMetadata,
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
    (result as { exportMetadata?: unknown }).exportMetadata,
    exportMetadata
  );
});

test("route request uses the default general mode for authored PPTX", async () => {
  const { calls, dependencies } = boundarySpies({
    version: "v1-standard",
    mode: "authored",
    slides: [{ ui: null, html_content: "<section>editable</section>" }],
  });

  const result = await executeExportPresentationRouteRequest(
    { format: "pptx", id: "persisted-id" },
    "session=test",
    dependencies
  );

  assert.equal(result.path, "general.pptx");
  assert.deepEqual(calls, { fetch: 1, general: 1, hybrid: 0 });
});

test("route request forwards a complete supplied Hybrid attempt binding", async () => {
  let received: PresentationExportExecutionParams | undefined;
  const { dependencies } = boundarySpies({
    version: "v1-standard",
    mode: "authored",
    slides: [{ ui: null, html_content: "<section>editable</section>" }],
  });
  dependencies.registry.hybrid = async (params) => {
    received = params;
    return { path: "hybrid.pptx" };
  };

  await executeExportPresentationRouteRequest(
    {
      format: "pptx",
      id: "persisted-id",
      pptxMode: "hybrid",
      exportAttemptId: "attempt-1",
      canonicalSourceDigest: "c".repeat(64),
    },
    "session=test",
    dependencies,
  );

  assert.equal(received?.exportAttemptId, "attempt-1");
  assert.equal(received?.canonicalSourceDigest, "c".repeat(64));
  assert.equal(received?.fontEmbedding, true);
});

test("route request rejects incomplete or malformed Hybrid attempt bindings", async () => {
  const { dependencies } = boundarySpies({
    version: "v1-standard",
    mode: "authored",
    slides: [{ ui: null, html_content: "<section>editable</section>" }],
  });
  for (const request of [
    { exportAttemptId: "attempt-1" },
    { canonicalSourceDigest: "c".repeat(64) },
    { exportAttemptId: "", canonicalSourceDigest: "c".repeat(64) },
    { exportAttemptId: "attempt-1", canonicalSourceDigest: "not-a-digest" },
  ]) {
    const error = await rejectedRequestError(
      executeExportPresentationRouteRequest(
        {
          format: "pptx",
          id: "persisted-id",
          pptxMode: "hybrid",
          ...request,
        },
        "session=test",
        dependencies,
      ),
    );
    assert.equal(error.status, 400);
  }
});

test("route request rejects an invalid expected revision", async () => {
  const { calls, dependencies } = boundarySpies({
    version: "v1-standard",
    mode: "authored",
    slides: [{ ui: null, html_content: "<section>editable</section>" }],
  });

  const error = await rejectedRequestError(
    executeExportPresentationRouteRequest(
      {
        format: "pptx",
        id: "persisted-id",
        expectedPresentationRevision: -1,
      },
      "session=test",
      dependencies
    )
  );

  assert.equal(error.status, 400);
  assert.deepEqual(calls, { fetch: 0, general: 0, hybrid: 0 });
});

for (const [format, pptxMode] of [
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
    slides: [templateV2Slide()],
  });

  await assert.rejects(
    executeExportPresentationRouteRequest(
      { format: "pptx", id: "persisted-id", pptxMode: "hybrid" },
      "session=test",
      dependencies
    ),
    /unsupported_presentation_identity/
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
            "http://fastapi.test/api/v1/ppt/presentation/persisted%2Fid/export-projection"
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

  assert.equal(error.status, 413);
  assert.equal(
    error.message,
    "Presentation export projection exceeded the 16 byte wire limit"
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
          "http://fastapi.test/api/v1/ppt/presentation/persisted-id/export-projection"
        );
        assert.deepEqual(init?.headers, {
          Accept: "application/json",
          Authorization: "Basic test",
          Cookie: "session=test",
        });
        return Response.json(
          {
            version: "v2-standard",
            slides: [{ ui: { id: "native" } }],
          },
          {
            headers: {
              "X-Presenton-Presentation-Projection":
                "authored-composition-dedup-v1",
            },
          }
        );
      },
    }
  );
  assert.equal(presentation.version, "v2-standard");
  assert.match(presentation[PRESENTATION_SOURCE_SHA256] ?? "", /^[0-9a-f]{64}$/);
});

test("general export receives the exact persisted snapshot digest", async () => {
  const body = JSON.stringify(canonicalServerPresentation({
    version: "v2-standard",
    mode: "template",
    slides: [templateV2Slide()],
  }));
  const presentation = await fetchPersistedPresentationForExport(
    "persisted-id",
    {
      baseUrl: "http://fastapi.test",
      timeoutMs: 100,
      maxResponseBytes: 1024,
      async fetchImpl() {
        return new Response(body, {
          headers: {
            "X-Presenton-Presentation-Projection":
              "authored-composition-dedup-v1",
          },
        });
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

test("export projection rehydrates repeated composition pins after the bounded wire read", async () => {
  const compositionPin = {
    digest: "a".repeat(64),
    retainedPayload: "x".repeat(4096),
  };
  const reference = {
    $presentonRef: "theme.authored_vnext.compositionPin",
    schemaVersion: "presenton.export-composition-ref/v1",
  };
  const projected = {
    mode: "authored",
    theme: {
      authored_vnext: {
        compositionPin,
        scene: { compositionPin: reference },
      },
    },
    slides: [
      { content: { compositionPin: reference }, html_content: "<main>one</main>" },
      { content: { compositionPin: reference }, html_content: "<main>two</main>" },
    ],
  };
  const wireBody = JSON.stringify(projected);
  const wireLimit = Buffer.byteLength(wireBody, "utf8") + 16;

  const fetched = await fetchPersistedPresentationProjectionForExport(
    "persisted-id",
    {
      baseUrl: "http://fastapi.test",
      timeoutMs: 100,
      maxResponseBytes: wireLimit,
      async fetchImpl() {
        return new Response(wireBody, {
          headers: {
            "X-Presenton-Presentation-Projection":
              "authored-composition-dedup-v1",
          },
        });
      },
    }
  );

  const hydrated = fetched.presentation as Record<string, any>;
  assert.deepEqual(
    hydrated.theme.authored_vnext.scene.compositionPin,
    compositionPin
  );
  assert.deepEqual(hydrated.slides[0].content.compositionPin, compositionPin);
  assert.deepEqual(hydrated.slides[1].content.compositionPin, compositionPin);
  assert.ok(fetched.wireBytes < fetched.rendererBytes);
  assert.ok(fetched.wireBytes <= wireLimit);
  assert.ok(fetched.rendererBytes > wireLimit);
  assert.equal(
    fetched.presentation[PRESENTATION_SOURCE_SHA256],
    fetched.sourceSha256
  );
});
