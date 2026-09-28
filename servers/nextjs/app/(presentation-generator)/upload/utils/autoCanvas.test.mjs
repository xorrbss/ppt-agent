import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { transform } from "esbuild";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const sourcePath = path.join(testDirectory, "autoCanvas.ts");

async function loadModule() {
  const source = await readFile(sourcePath, "utf8");
  const { code } = await transform(source, { loader: "ts", format: "esm" });
  return import(
    `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`
  );
}

test("falls back to the default row when no source canvas is known", async () => {
  const { nearestEnabledCanvasRow } = await loadModule();
  const result = nearestEnabledCanvasRow(
    ["wide-16x9", "tall-9x16", "a4-landscape"],
    null,
    "a4-landscape",
  );
  assert.equal(result, "a4-landscape");
});

test("falls back to the first enabled row when the default is disabled", async () => {
  const { nearestEnabledCanvasRow } = await loadModule();
  const result = nearestEnabledCanvasRow(
    ["wide-16x9", "tall-9x16"],
    null,
    "a4-landscape",
  );
  assert.equal(result, "wide-16x9");
});

test("returns null when nothing is enabled", async () => {
  const { nearestEnabledCanvasRow } = await loadModule();
  assert.equal(nearestEnabledCanvasRow([], { width: 1000, height: 1000 }, "a4-landscape"), null);
});

test("picks the exact ratio match when present", async () => {
  const { nearestEnabledCanvasRow } = await loadModule();
  const result = nearestEnabledCanvasRow(
    ["wide-16x9", "tall-9x16", "square-1x1", "a4-landscape", "a4-portrait"],
    { width: 1920, height: 1080 },
    "a4-landscape",
  );
  assert.equal(result, "wide-16x9");
});

test("a near-square document snaps to 1:1 over 21:9 or 9:16", async () => {
  const { nearestEnabledCanvasRow } = await loadModule();
  const result = nearestEnabledCanvasRow(
    ["wide-16x9", "tall-9x16", "square-1x1", "ultrawide-21x9"],
    { width: 1000, height: 900 },
    "wide-16x9",
  );
  assert.equal(result, "square-1x1");
});

test("a 9:13-shaped document picks the enabled row nearest in log-ratio, not tall-9x16 by default", async () => {
  const { nearestEnabledCanvasRow } = await loadModule();
  // 9:13's ratio sits closer to a4-portrait (840x1188) than to tall-9x16
  // (720x1280) in log-space, so a4-portrait must win when both are enabled.
  const result = nearestEnabledCanvasRow(
    ["wide-16x9", "tall-9x16", "a4-portrait"],
    { width: 720, height: 1040 },
    "wide-16x9",
  );
  assert.equal(result, "a4-portrait");
});

test("a landscape A4 document prefers a4-landscape over 16:9 when both are enabled", async () => {
  const { nearestEnabledCanvasRow } = await loadModule();
  const result = nearestEnabledCanvasRow(
    ["wide-16x9", "a4-landscape", "ultrawide-21x9"],
    { width: 1188, height: 840 },
    "wide-16x9",
  );
  assert.equal(result, "a4-landscape");
});

test("an ultrawide source picks 21:9 over 16:9", async () => {
  const { nearestEnabledCanvasRow } = await loadModule();
  const result = nearestEnabledCanvasRow(
    ["wide-16x9", "ultrawide-21x9"],
    { width: 1920, height: 823 },
    "wide-16x9",
  );
  assert.equal(result, "ultrawide-21x9");
});

test("zero or negative source dimensions are treated as unknown", async () => {
  const { nearestEnabledCanvasRow } = await loadModule();
  const result = nearestEnabledCanvasRow(
    ["wide-16x9", "tall-9x16"],
    { width: 0, height: 1000 },
    "tall-9x16",
  );
  assert.equal(result, "tall-9x16");
});
