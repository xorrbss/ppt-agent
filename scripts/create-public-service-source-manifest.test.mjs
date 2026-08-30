import assert from "node:assert/strict";
import test from "node:test";

import { mergeChangedSourcePaths } from "./create-public-service-source-manifest.mjs";

test("source manifest unions staged, unstaged, and untracked source paths", () => {
  const paths = mergeChangedSourcePaths({
    unstaged: ["servers/fastapi/a.py"],
    staged: ["servers/fastapi/b.py"],
    untracked: ["scripts/new-runner.mjs", "artifacts/runtime.json"],
    outputRelativePath: "artifacts/rc/source-manifest.json",
  });
  assert.ok(paths.includes("servers/fastapi/a.py"));
  assert.ok(paths.includes("servers/fastapi/b.py"));
  assert.ok(paths.includes("scripts/new-runner.mjs"));
  assert.ok(paths.includes("scripts/create-public-service-source-manifest.mjs"));
  assert.ok(paths.includes("docs/experiments/authored-vnext/04-renderer-security/harness/run-harness.ps1"));
  assert.ok(!paths.includes("artifacts/runtime.json"));
});

test("source manifest fails closed for staged or unstaged deletions", () => {
  assert.throws(() => mergeChangedSourcePaths({
    deletedStaged: ["servers/fastapi/deleted.py"],
    outputRelativePath: "artifacts/rc/source-manifest.json",
  }), /cannot bind deleted paths/);
  assert.throws(() => mergeChangedSourcePaths({
    deletedUnstaged: ["scripts/deleted.mjs"],
    outputRelativePath: "artifacts/rc/source-manifest.json",
  }), /cannot bind deleted paths/);
});
