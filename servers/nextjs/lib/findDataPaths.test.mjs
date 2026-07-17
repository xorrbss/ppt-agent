// Regression test for occurrence-based text binding.
// Run: node lib/findDataPaths.test.mjs   (Node >=23 strips TS types natively)
import assert from "node:assert/strict";
import { collectMatchingPaths } from "./findDataPaths.ts";

let passed = 0;
const check = (name, fn) => {
  fn();
  console.log(`ok - ${name}`);
  passed++;
};

check("returns every matching path in traversal order (not just the first)", () => {
  const data = {
    title: "전략",
    bullets: [{ heading: "전략" }, { heading: "실행" }, { heading: "전략" }],
  };
  const matches = collectMatchingPaths(data, "전략");
  assert.deepEqual(matches, ["title", "bullets[0].heading", "bullets[2].heading"]);
});

check("distinct duplicate fields resolve by occurrence index", () => {
  const data = { a: "같음", b: "같음" };
  const matches = collectMatchingPaths(data, "같음");
  assert.equal(matches[0], "a"); // 1st on screen -> a
  assert.equal(matches[1], "b"); // 2nd on screen -> b (was the bug: always 'a')
});

check("trims both sides when comparing", () => {
  const data = { x: "  hello  " };
  assert.deepEqual(collectMatchingPaths(data, "hello"), ["x"]);
});

check("no match -> empty array", () => {
  assert.deepEqual(collectMatchingPaths({ x: "a" }, "z"), []);
});

check("string array elements are not matched (mirrors legacy findDataPath)", () => {
  // The legacy binding never matched bare string-array items, and export/editor
  // relied on that; keep it so behaviour doesn't shift beyond the dedup fix.
  assert.deepEqual(collectMatchingPaths({ cols: ["A", "B"] }, "A"), []);
});

check("non-object input is a safe no-op", () => {
  assert.deepEqual(collectMatchingPaths(null, "a"), []);
  assert.deepEqual(collectMatchingPaths("str", "a"), []);
});

console.log(`\n${passed} checks passed`);
