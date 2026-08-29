import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import { transform } from "esbuild";
import { NextRequest } from "next/server.js";

const directory = path.dirname(fileURLToPath(import.meta.url));
const nextServerUrl = pathToFileURL(
  path.join(directory, "../../../node_modules/next/server.js"),
).href;

async function loadRoute() {
  const source = (await readFile(path.join(directory, "route.ts"), "utf8"))
    .replace('"next/server"', JSON.stringify(nextServerUrl))
    .replace(
      'import sharp from "sharp";',
      'const sharp = () => ({ resize() { return this; }, png() { return this; }, async toBuffer() { return Buffer.from("png"); } });',
    );
  const compiled = await transform(source, { format: "esm", loader: "ts", target: "esnext" });
  return import(`data:text/javascript;base64,${Buffer.from(compiled.code).toString("base64")}`);
}

const { GET } = await loadRoute();

test("private SVG rasterization forwards owner auth and disables caching", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    assert.equal(url, "http://127.0.0.1:8000/app_data/images/users/u/icon.svg");
    const headers = new Headers(init?.headers);
    assert.equal(headers.get("cookie"), "presenton_session=session-token");
    assert.equal(headers.get("authorization"), "Bearer sk-presenton-owner");
    assert.equal(init?.redirect, "error");
    return new Response("<svg/>", {
      status: 200,
      headers: { "content-type": "image/svg+xml", "content-length": "6" },
    });
  };
  try {
    const response = await GET(
      new NextRequest(
        "http://localhost/api/rasterize-icon?src=/app_data/images/users/u/icon.svg",
        {
          headers: {
            cookie: "presenton_session=session-token",
            authorization: "Bearer sk-presenton-owner",
          },
        },
      ),
    );
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("cache-control"), "private, no-store");
    assert.equal(response.headers.get("x-content-type-options"), "nosniff");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("non-SVG upstream content fails with a generalized no-store error", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response("secret", {
    status: 200,
    headers: { "content-type": "text/plain" },
  });
  try {
    const response = await GET(
      new NextRequest("http://localhost/api/rasterize-icon?src=/static/icon.svg"),
    );
    assert.equal(response.status, 415);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.deepEqual(await response.json(), { error: "Icon rasterization failed" });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
