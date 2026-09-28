import assert from "node:assert/strict";
import test from "node:test";

import { isRecord, recordOrNull } from "./guards.ts";

test("isRecord accepts only plain non-null, non-array objects", () => {
  assert.equal(isRecord({}), true);
  assert.equal(isRecord({ a: 1 }), true);
  assert.equal(isRecord(Object.create(null)), true);
  assert.equal(isRecord(new Date()), true);

  assert.equal(isRecord(null), false);
  assert.equal(isRecord(undefined), false);
  assert.equal(isRecord([]), false);
  assert.equal(isRecord(["a"]), false);
  assert.equal(isRecord("text"), false);
  assert.equal(isRecord(42), false);
  assert.equal(isRecord(true), false);
  assert.equal(isRecord(() => undefined), false);
});

test("recordOrNull returns the same object or null", () => {
  const value = { nested: { ok: true } };
  assert.equal(recordOrNull(value), value);
  assert.equal(recordOrNull([]), null);
  assert.equal(recordOrNull(null), null);
  assert.equal(recordOrNull("x"), null);
  assert.equal(recordOrNull(undefined), null);
});
