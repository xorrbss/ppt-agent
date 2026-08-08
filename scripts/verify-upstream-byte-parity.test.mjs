import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const manifest = JSON.parse(readFileSync(path.join(repoRoot, "compatibility", "upstream-byte-parity.json"), "utf8"));

test("every upstream-owned path is byte-identical or a documented exception", () => {
  const result = spawnSync(process.execPath, ["scripts/verify-upstream-byte-parity.mjs"], {
    cwd: repoRoot,
    encoding: "utf8",
  });
  assert.equal(result.status, 0, `verifier failed\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
  assert.match(result.stdout, new RegExp(`${manifest.counts.identical} identical`));
  assert.match(result.stdout, new RegExp(`${manifest.counts.exceptions} documented exceptions`));
  assert.equal(manifest.counts.identical + manifest.counts.exceptions, manifest.counts.upstreamOwned);
  assert.ok(manifest.counts.identical > 0);
});
