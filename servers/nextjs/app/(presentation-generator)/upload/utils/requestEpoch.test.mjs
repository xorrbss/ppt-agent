import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const transform = await import("esbuild").then(
  (module) => module.transform,
  async (error) => {
    if (error.code !== "ERR_MODULE_NOT_FOUND") throw error;
    const { stripTypeScriptTypes } = await import("node:module");
    return (source) => ({ code: stripTypeScriptTypes(source, { mode: "transform" }) });
  },
);

async function loadModule() {
  const directory = path.dirname(fileURLToPath(import.meta.url));
  const source = await readFile(path.join(directory, "requestEpoch.ts"), "utf8");
  const { code } = await transform(source, { loader: "ts", format: "esm" });
  return import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
}

test("a newer request or a close makes an earlier request stale", async () => {
  const { beginEpochRequest } = await loadModule();
  const epoch = { current: 0 };
  const first = beginEpochRequest(epoch, () => true);
  assert.equal(first(), true);
  const second = beginEpochRequest(epoch, () => true);
  assert.equal(first(), false, "the first request's response must be ignored");
  assert.equal(second(), true);
  epoch.current += 1; // Close
  assert.equal(second(), false, "a closed panel ignores the in-flight response and its finally");
});

test("a request stops being current when its owner is no longer live", async () => {
  const { beginEpochRequest } = await loadModule();
  let live = true;
  const current = beginEpochRequest({ current: 0 }, () => live);
  assert.equal(current(), true);
  live = false;
  assert.equal(current(), false);
});
