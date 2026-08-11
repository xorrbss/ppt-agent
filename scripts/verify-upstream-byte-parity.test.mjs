import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { chmodSync, existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const verifier = path.join(repoRoot, "scripts", "verify-upstream-byte-parity.mjs");
const manifest = JSON.parse(readFileSync(path.join(repoRoot, "compatibility", "upstream-byte-parity.json"), "utf8"));

function runGit(cwd, args) {
  return execFileSync("git", args, { cwd, encoding: "utf8" }).trim();
}

function write(root, relativePath, content) {
  const target = path.join(root, relativePath);
  mkdirSync(path.dirname(target), { recursive: true });
  writeFileSync(target, content);
}

function runVerifier(root, args) {
  return spawnSync(process.execPath, [verifier, ...args], {
    cwd: root,
    encoding: "utf8",
    env: { ...process.env, UPSTREAM_BYTE_PARITY_REPO: root },
  });
}

function fixture(t) {
  const root = mkdtempSync(path.join(os.tmpdir(), "byte-parity-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  runGit(root, ["init", "--quiet"]);
  runGit(root, ["config", "user.email", "byte-parity@example.test"]);
  runGit(root, ["config", "user.name", "Byte Parity Test"]);
  write(root, "same.txt", "same\n");
  write(root, "remove-me.txt", "remove\n");
  write(root, "script.sh", "#!/bin/sh\necho base\n");
  if (process.platform !== "win32") chmodSync(path.join(root, "script.sh"), 0o755);
  runGit(root, ["add", "."]);
  // Git's index can retain an executable bit even when Windows cannot expose
  // one in lstat. This makes both platform branches exercise the same contract.
  runGit(root, ["update-index", "--chmod=+x", "script.sh"]);
  runGit(root, ["commit", "--quiet", "-m", "upstream baseline"]);
  const baseline = runGit(root, ["rev-parse", "HEAD"]);
  write(root, "compatibility/upstream-compatibility.json", `${JSON.stringify({ baseline: { repository: "fixture/upstream", upstreamSha: baseline } })}\n`);
  write(root, ".gitignore", "ignored.txt\n");
  runGit(root, ["add", "compatibility/upstream-compatibility.json", ".gitignore"]);
  runGit(root, ["commit", "--quiet", "-m", "local compatibility setup"]);
  return root;
}

function writeManifest(root, args = ["--index"]) {
  const manifestPath = path.join(root, "compatibility", "upstream-byte-parity.json");
  if (!existsSync(manifestPath)) write(root, "compatibility/upstream-byte-parity.json", "{}\n");
  const result = runVerifier(root, [...args, "--write"]);
  assert.equal(result.status, 0, `manifest write failed\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
}

test("the checked-in policy verifies its recorded mutable source and classifies itself by path", () => {
  const result = runVerifier(repoRoot, manifest.receipt.source.mode === "working-tree" ? ["--working-tree"] : ["--tree", "HEAD"]);
  assert.equal(result.status, 0, `verifier failed\nstdout:\n${result.stdout}\nstderr:\n${result.stderr}`);
  assert.ok(manifest.policy.localOnly.includes("compatibility/upstream-byte-parity.json"));
  assert.equal(manifest.schemaVersion, 2);
  assert.equal(manifest.receipt.counts.identical + manifest.receipt.counts.exceptions, manifest.receipt.counts.upstreamOwned);
});

test("index ignores unstaged upstream edits and deletions while working-tree observes both", (t) => {
  const root = fixture(t);
  writeManifest(root);
  const headBefore = runGit(root, ["rev-parse", "HEAD"]);
  const indexBefore = runGit(root, ["write-tree"]);
  write(root, "same.txt", "unstaged change\n");
  const indexResult = runVerifier(root, ["--index"]);
  const workingResult = runVerifier(root, ["--working-tree"]);
  assert.equal(indexResult.status, 0, indexResult.stderr);
  assert.notEqual(workingResult.status, 0);
  assert.match(workingResult.stderr, /policy is stale/);
  write(root, "same.txt", "same\n");
  rmSync(path.join(root, "remove-me.txt"));
  assert.equal(runVerifier(root, ["--index"]).status, 0);
  const deletionResult = runVerifier(root, ["--working-tree"]);
  assert.notEqual(deletionResult.status, 0);
  assert.match(deletionResult.stderr, /policy is stale/);
  assert.equal(runGit(root, ["rev-parse", "HEAD"]), headBefore);
  assert.equal(runGit(root, ["write-tree"]), indexBefore);
});

test("working-tree includes nonignored untracked files and excludes ignored files", (t) => {
  const root = fixture(t);
  writeManifest(root, ["--working-tree"]);
  write(root, "ignored.txt", "not observed\n");
  assert.equal(runVerifier(root, ["--working-tree"]).status, 0);
  write(root, "included.txt", "observed\n");
  const result = runVerifier(root, ["--working-tree"]);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /policy is stale/);
});

test("working-tree accepts tracked executable index modes, and POSIX chmod drift is stale", (t) => {
  const root = fixture(t);
  writeManifest(root, ["--working-tree"]);
  const executable = runGit(root, ["ls-files", "--stage", "script.sh"]);
  assert.match(executable, /^100755 /, "fixture must retain an executable Git index mode");
  // On Windows this proves working-tree verification preserves the tracked
  // executable mode instead of deriving 100644 from NTFS metadata.
  assert.equal(runVerifier(root, ["--working-tree"]).status, 0);

  if (process.platform === "win32") return;
  chmodSync(path.join(root, "script.sh"), 0o644);
  assert.equal(runVerifier(root, ["--index"]).status, 0);
  const chmodDrift = runVerifier(root, ["--working-tree"]);
  assert.notEqual(chmodDrift.status, 0);
  assert.match(chmodDrift.stderr, /policy is stale/);
});

test("tree HEAD is a fail-closed clean CI check", (t) => {
  const root = fixture(t);
  write(root, "compatibility/upstream-byte-parity.json", "{}\n");
  runGit(root, ["add", "compatibility/upstream-byte-parity.json"]);
  runGit(root, ["commit", "--quiet", "-m", "seed parity manifest path"]);
  writeManifest(root, ["--tree", "HEAD"]);
  runGit(root, ["add", "compatibility/upstream-byte-parity.json"]);
  runGit(root, ["commit", "--quiet", "-m", "record parity policy"]);
  assert.equal(runVerifier(root, ["--tree", "HEAD"]).status, 0);
  write(root, "same.txt", "dirty\n");
  const dirty = runVerifier(root, ["--tree", "HEAD"]);
  assert.notEqual(dirty.status, 0);
  assert.match(dirty.stderr, /requires a clean index-equivalent working tree/);
});

test("source selection is explicit and mutually exclusive", () => {
  const conflicting = runVerifier(repoRoot, ["--tree", "HEAD", "--index"]);
  assert.notEqual(conflicting.status, 0);
  assert.match(conflicting.stderr, /choose exactly one source/);
  const missingTree = runVerifier(repoRoot, ["--tree"]);
  assert.notEqual(missingTree.status, 0);
  assert.match(missingTree.stderr, /--tree requires a Git tree-ish/);
});
