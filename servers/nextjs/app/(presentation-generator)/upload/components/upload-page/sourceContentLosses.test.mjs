import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { transform } from "esbuild";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const sourcePath = path.join(testDirectory, "sourceContentLosses.ts");

async function loadModule() {
  const source = await readFile(sourcePath, "utf8");
  const { code } = await transform(source, { loader: "ts", format: "esm" });
  return import(
    `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`
  );
}

const summary = (overrides = {}) => ({
  sourceType: "pptx",
  sourceUnits: 12,
  textBlocks: 90,
  tables: 0,
  charts: 0,
  uniqueImages: 8,
  imageOccurrences: 11,
  rasterFallbackBlocks: 0,
  ...overrides,
});

test("a deck that loses nothing needs no confirmation", async () => {
  const { sourceContentLosses } = await loadModule();
  // The empty list is what lets the redesign run in a single click; anything
  // reported here stops the run to ask the user first.
  assert.deepEqual(sourceContentLosses(summary()), []);
});

test("dropped tables and charts are each reported", async () => {
  const { sourceContentLosses } = await loadModule();
  const losses = sourceContentLosses(summary({ tables: 3, charts: 1 }));
  assert.equal(losses.length, 2);
  assert.ok(losses[0].includes("3"), losses[0]);
  assert.ok(losses[1].includes("1"), losses[1]);
});

test("blocks kept as flat images are reported as a loss of editability", async () => {
  const { sourceContentLosses } = await loadModule();
  const losses = sourceContentLosses(summary({ rasterFallbackBlocks: 5 }));
  assert.equal(losses.length, 1);
  assert.ok(losses[0].includes("5"), losses[0]);
});

test("every kind of loss is listed together", async () => {
  const { sourceContentLosses } = await loadModule();
  assert.equal(
    sourceContentLosses(
      summary({ tables: 2, charts: 4, rasterFallbackBlocks: 1 }),
    ).length,
    3,
  );
});
