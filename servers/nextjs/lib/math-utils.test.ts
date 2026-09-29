import assert from "node:assert/strict";
import test from "node:test";

import { clamp, finiteNumber } from "./math-utils.ts";

test("clamp bounds the value and lets the upper bound win when bounds cross", () => {
  assert.equal(clamp(5, 0, 10), 5);
  assert.equal(clamp(-1, 0, 10), 0);
  assert.equal(clamp(11, 0, 10), 10);
  assert.equal(clamp(5, 10, 0), 0);
  assert.ok(Number.isNaN(clamp(Number.NaN, 0, 1)));
});

test("finiteNumber accepts only finite numbers and never coerces", () => {
  assert.equal(finiteNumber(3.5), 3.5);
  assert.equal(finiteNumber(0), 0);
  assert.equal(finiteNumber(Number.NaN), undefined);
  assert.equal(finiteNumber(Number.POSITIVE_INFINITY), undefined);
  assert.equal(finiteNumber("3"), undefined);
  assert.equal(finiteNumber(null), undefined);
  assert.equal(finiteNumber(true), undefined);
});
