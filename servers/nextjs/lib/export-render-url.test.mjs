import assert from "node:assert/strict";
import test from "node:test";

import { resolveExportRenderBaseUrl } from "./export-render-url.ts";

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
