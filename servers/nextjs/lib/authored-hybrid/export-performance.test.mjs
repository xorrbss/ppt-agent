import assert from "node:assert/strict";
import test from "node:test";

import {
  mapWithConcurrency,
  parseBoundedPositiveInt,
} from "./export-performance.ts";

test("parseBoundedPositiveInt applies fallback and bounds", () => {
  assert.equal(parseBoundedPositiveInt(undefined, 4, 1, 8), 4);
  assert.equal(parseBoundedPositiveInt("invalid", 4, 1, 8), 4);
  assert.equal(parseBoundedPositiveInt("0", 4, 1, 8), 1);
  assert.equal(parseBoundedPositiveInt("99", 4, 1, 8), 8);
  assert.equal(parseBoundedPositiveInt("6", 4, 1, 8), 6);
});

test("mapWithConcurrency limits work and preserves result order", async () => {
  let active = 0;
  let peak = 0;
  const values = [30, 5, 20, 1, 10, 2];

  const results = await mapWithConcurrency(values, 3, async (delay, index) => {
    active += 1;
    peak = Math.max(peak, active);
    await new Promise((resolve) => setTimeout(resolve, delay));
    active -= 1;
    return `result-${index}`;
  });

  assert.equal(peak, 3);
  assert.deepEqual(results, values.map((_, index) => `result-${index}`));
});

test("mapWithConcurrency uses at least one worker", async () => {
  const results = await mapWithConcurrency([1, 2], 0, async (value) => value * 2);
  assert.deepEqual(results, [2, 4]);
});
