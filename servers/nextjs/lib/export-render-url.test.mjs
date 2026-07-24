import assert from "node:assert/strict";
import test from "node:test";

import {
  buildExportRenderUrl,
  resolveExportRenderBaseUrl,
} from "./export-render-url.ts";

test("explicit public export URL has highest priority", () => {
  assert.equal(
    resolveExportRenderBaseUrl({
      NEXT_PUBLIC_URL: " https://presenton.example/ ",
      NEXT_INTERNAL_URL: "http://localhost:5000",
      PROXY_PORT: "5100",
    }),
    "https://presenton.example"
  );
});

test("local single-origin proxy can be selected through NEXT_INTERNAL_URL", () => {
  assert.equal(
    resolveExportRenderBaseUrl({
      NEXT_INTERNAL_URL: " http://localhost:5000/ ",
    }),
    "http://localhost:5000"
  );
});

test("native development falls back to the single-origin proxy", () => {
  assert.equal(resolveExportRenderBaseUrl({}), "http://127.0.0.1:5000");
  assert.equal(
    resolveExportRenderBaseUrl({ PROXY_PORT: "5100" }),
    "http://127.0.0.1:5100"
  );
});

test("invalid fallback port fails before spawning the exporter", () => {
  assert.throws(
    () => resolveExportRenderBaseUrl({ PROXY_PORT: "not-a-port" }),
    /numeric TCP port/
  );
});

test("export render URL contains no session or cookie material", () => {
  const url = buildExportRenderUrl(
    "http://127.0.0.1:5000/",
    "12345678-1234-1234-1234-123456789abc"
  );

  assert.equal(
    url,
    "http://127.0.0.1:5000/pdf-maker?id=12345678-1234-1234-1234-123456789abc"
  );
  assert.doesNotMatch(url, /cookie|session|token/i);
});

test("snapshot-bound render URL carries only the expected payload hash", () => {
  const sha256 = "a".repeat(64);
  const url = buildExportRenderUrl(
    "http://127.0.0.1:5000",
    "deck-1",
    sha256
  );

  assert.equal(
    url,
    `http://127.0.0.1:5000/pdf-maker?id=deck-1&source_sha256=${sha256}`
  );
  assert.doesNotMatch(url, /cookie|session|token|slides|html_content/i);
});
