import assert from "node:assert/strict";
import test from "node:test";

import { mergeChangedSourcePaths } from "./create-public-service-source-manifest.mjs";

test("source manifest unions staged, unstaged, and untracked source paths", () => {
  const paths = mergeChangedSourcePaths({
    unstaged: ["servers/fastapi/a.py"],
    staged: ["servers/fastapi/b.py"],
    untracked: ["scripts/new-runner.mjs", "artifacts/runtime.json", "output/rc-report.md"],
    outputRelativePath: "artifacts/rc/source-manifest.json",
  });
  assert.ok(paths.includes("servers/fastapi/a.py"));
  assert.ok(paths.includes("servers/fastapi/b.py"));
  assert.ok(paths.includes("scripts/new-runner.mjs"));
  assert.ok(paths.includes("scripts/create-public-service-source-manifest.mjs"));
  assert.ok(paths.includes("docs/experiments/authored-vnext/04-renderer-security/harness/run-harness.ps1"));
  const cleanPaths = mergeChangedSourcePaths({
    outputRelativePath: "artifacts/rc/source-manifest.json",
  });
  for (const requiredQaPath of [
    "scripts/collect-public-service-powerpoint-readback.ps1",
    "servers/nextjs/scripts/qa-powerpoint-desktop.ps1",
    "docs/experiments/authored-vnext/03-ooxml-roundtrip/tools/windows-reopen.ps1",
  ]) {
    assert.ok(paths.includes(requiredQaPath));
    assert.equal(cleanPaths.filter((value) => value === requiredQaPath).length, 1);
  }
  assert.ok(!paths.includes("artifacts/runtime.json"));
  assert.ok(!paths.includes("output/rc-report.md"));
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
