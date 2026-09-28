import assert from "node:assert/strict";
import test from "node:test";

import {
  ExportPresentationRequestError,
  fetchPersistedPresentationForExport,
  fetchPersistedPresentationProjectionForExport,
  readExportPresentationRouteBody,
} from "../../../lib/export-presentation-route.ts";
import { PRESENTATION_SOURCE_SHA256 } from "../../../lib/presentation-export-strategy.ts";

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
          "X-Document-Canvas-Contracts": "document-canvas/v1",
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
