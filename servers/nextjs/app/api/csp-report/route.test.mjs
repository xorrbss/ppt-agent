import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

import { transform } from "esbuild";
import { NextRequest } from "next/server.js";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const routePath = path.join(testDirectory, "route.ts");
const nextServerUrl = pathToFileURL(
  path.join(testDirectory, "../../../node_modules/next/server.js"),
).href;

async function loadRoute() {
  const source = await readFile(routePath, "utf8");
  const executableSource = source.replace(
    '"next/server"',
    JSON.stringify(nextServerUrl),
  );
  const compiled = await transform(executableSource, {
    format: "esm",
    loader: "ts",
    target: "esnext",
  });
  const encoded = Buffer.from(compiled.code).toString("base64");
  return import(`data:text/javascript;base64,${encoded}`);
}

const { POST } = await loadRoute();

function request(body) {
  return new NextRequest("http://localhost/api/csp-report", {
    method: "POST",
    headers: { "content-type": "application/csp-report" },
    body,
  });
}

async function withConsoleWarn(callback) {
  const originalWarn = console.warn;
  const calls = [];
  console.warn = (...args) => calls.push(args);
  try {
    await callback(calls);
  } finally {
    console.warn = originalWarn;
  }
}

test("csp-report logs a violation report and answers 204", async () => {
  await withConsoleWarn(async (calls) => {
    const body = JSON.stringify({
      "csp-report": { "violated-directive": "img-src", "blocked-uri": "http://evil.example" },
    });
    const response = await POST(request(body));
    assert.equal(response.status, 204);
    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], "[csp-report]");
    assert.match(calls[0][1], /violated-directive/);
  });
});

test("csp-report answers 204 even for an unreadable or empty body", async () => {
  await withConsoleWarn(async (calls) => {
    const response = await POST(request(""));
    assert.equal(response.status, 204);
    assert.equal(calls.length, 0);
  });
});

test("csp-report drops and logs a report over the size limit instead of buffering it", async () => {
  await withConsoleWarn(async (calls) => {
    const oversized = "x".repeat(64 * 1024);
    const response = await POST(request(oversized));
    assert.equal(response.status, 204);
    assert.equal(calls.length, 1);
    assert.match(calls[0][0], /exceeded size limit/);
  });
});
