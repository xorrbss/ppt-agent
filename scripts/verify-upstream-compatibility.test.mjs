import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const verifier = path.join(repoRoot, "scripts", "verify-upstream-compatibility.mjs");
const manifest = JSON.parse(
  readFileSync(path.join(repoRoot, "compatibility", "upstream-compatibility.json"), "utf8")
);

test("U0 upstream compatibility contract is internally consistent and matches the tree", () => {
  const result = spawnSync(process.execPath, [verifier], {
    cwd: path.parse(repoRoot).root,
    encoding: "utf8",
  });

  assert.equal(
    result.status,
    0,
    `verifier failed\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`
  );
  assert.match(result.stdout, /Upstream compatibility verified:/);
  assert.match(
    result.stdout,
    new RegExp(`${manifest.templateV2Renderer.discriminators.length} discriminators`)
  );
  assert.match(
    result.stdout,
    new RegExp(`${manifest.api.keyEndpoints.length} key endpoints`)
  );
});
