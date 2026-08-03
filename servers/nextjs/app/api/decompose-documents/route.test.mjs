import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import { transform } from "esbuild";
import { NextRequest } from "next/server.js";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const routePath = path.join(testDirectory, "route.ts");
const fastApiInternalUrl = pathToFileURL(
  path.join(testDirectory, "../../../lib/fastapi-internal.ts"),
).href;
const nextServerUrl = pathToFileURL(
  path.join(testDirectory, "../../../node_modules/next/server.js"),
).href;
const undiciUrl = pathToFileURL(
  path.join(testDirectory, "../../../node_modules/undici/index.js"),
).href;

async function loadRoute() {
  const source = await readFile(routePath, "utf8");
  const executableSource = source
    .replace('"next/server"', JSON.stringify(nextServerUrl))
    .replace('"undici"', JSON.stringify(undiciUrl))
    .replace('"@/lib/fastapi-internal"', JSON.stringify(fastApiInternalUrl));
  const compiled = await transform(executableSource, {
    format: "esm",
    loader: "ts",
    target: "esnext",
  });
  const encoded = Buffer.from(compiled.code).toString("base64");
  return import(`data:text/javascript;base64,${encoded}`);
}

const { POST } = await loadRoute();

function request() {
  return new NextRequest("http://localhost/api/decompose-documents", {
    method: "POST",
    headers: {
      authorization: "Bearer user-token",
      cookie: "session=abc",
      "content-type": "application/json",
    },
    body: '{"file_paths":["upload.png"]}',
  });
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

test("decompose route preserves an upstream validation response", async () => {
  await withFetch(async (url, init) => {
    assert.equal(url, "http://127.0.0.1:8000/api/v1/ppt/files/decompose");
    assert.equal(init?.method, "POST");
    assert.equal(init?.body, '{"file_paths":["upload.png"]}');
    assert.equal(new Headers(init?.headers).get("authorization"), "Bearer user-token");

    return new Response(
      JSON.stringify({
        detail: {
          code: "IMAGE_CONVERTER_UNAVAILABLE",
          message: "Image converter is unavailable",
          filename: "upload.png",
          retryable: false,
        },
      }),
      {
        status: 422,
        headers: {
          "content-type": "application/json",
          "x-request-id": "req-validation",
        },
      },
    );
  }, async () => {
    const response = await POST(request());
    assert.equal(response.status, 422);
    assert.equal(response.headers.get("x-request-id"), "req-validation");
    assert.deepEqual(await response.json(), {
      detail: {
        code: "IMAGE_CONVERTER_UNAVAILABLE",
        message: "Image converter is unavailable",
        filename: "upload.png",
        retryable: false,
      },
    });
  });
});

test("decompose route maps an upstream header timeout to a 504 response", async () => {
  await withFetch(async () => {
    const error = new TypeError("fetch failed");
    error.cause = { code: "UND_ERR_HEADERS_TIMEOUT" };
    throw error;
  }, async () => {
    const response = await POST(request());
    assert.equal(response.status, 504);
    assert.equal(
      (await response.json()).error.code,
      "DOCUMENT_DECOMPOSE_TIMEOUT",
    );
  });
});

test("decompose route maps a connection failure to a 502 response", async () => {
  await withFetch(async () => {
    throw new TypeError("fetch failed");
  }, async () => {
    const response = await POST(request());
    assert.equal(response.status, 502);
    assert.equal(
      (await response.json()).error.code,
      "DOCUMENT_SERVICE_UNAVAILABLE",
    );
  });
});

test("decompose route passes through a successful upstream response", async () => {
  await withFetch(async () => new Response(
    JSON.stringify({ documents: [{ name: "upload.png", text: "parsed" }] }),
    {
      status: 200,
      headers: {
        "content-type": "application/json",
        "x-request-id": "req-success",
      },
    },
  ), async () => {
    const response = await POST(request());
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("x-request-id"), "req-success");
    assert.deepEqual(await response.json(), {
      documents: [{ name: "upload.png", text: "parsed" }],
    });
  });
});
