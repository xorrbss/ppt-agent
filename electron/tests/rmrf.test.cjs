const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const test = require("node:test");

const rmrfScript = path.join(__dirname, "..", "scripts", "rmrf.cjs");

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "presenton-rmrf-test-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  return root;
}

function run(cwd, target) {
  return spawnSync(process.execPath, [rmrfScript, target], {
    cwd,
    encoding: "utf8",
  });
}

test("removes a normal build output below the working directory", (t) => {
  const root = fixture(t);
  const output = path.join(root, "app_dist");
  fs.mkdirSync(output);
  fs.writeFileSync(path.join(output, "artifact.js"), "generated");

  const result = run(root, "app_dist");
  assert.equal(result.status, 0, result.stderr);
  assert.equal(fs.existsSync(output), false);
});

test("refuses targets outside the working directory and preserves them", (t) => {
  const root = fixture(t);
  const outside = path.join(os.tmpdir(), `presenton-rmrf-outside-${process.pid}-${Date.now()}`);
  fs.mkdirSync(outside);
  fs.writeFileSync(path.join(outside, "keep.txt"), "user state");
  t.after(() => fs.rmSync(outside, { recursive: true, force: true }));

  const result = run(root, outside);
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /refusing unsafe target/i);
  assert.equal(fs.existsSync(path.join(outside, "keep.txt")), true);
});

test("refuses .tmp worktree paths and the working directory itself", (t) => {
  const root = fixture(t);
  const protectedDirectory = path.join(root, ".tmp-user-worktree");
  fs.mkdirSync(protectedDirectory);
  fs.writeFileSync(path.join(protectedDirectory, "keep.txt"), "user state");

  const temporaryResult = run(root, ".tmp-user-worktree");
  assert.notEqual(temporaryResult.status, 0);
  assert.equal(fs.existsSync(path.join(protectedDirectory, "keep.txt")), true);

  const rootResult = run(root, ".");
  assert.notEqual(rootResult.status, 0);
  assert.equal(fs.existsSync(path.join(protectedDirectory, "keep.txt")), true);
});

test("refuses cleanup when invoked from inside a .tmp worktree", (t) => {
  const root = fixture(t);
  const protectedWorktree = path.join(root, ".tmp-user-worktree");
  const output = path.join(protectedWorktree, "app_dist");
  fs.mkdirSync(output, { recursive: true });
  fs.writeFileSync(path.join(output, "keep.txt"), "user state");

  const result = run(protectedWorktree, "app_dist");
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /refusing unsafe target/i);
  assert.equal(fs.existsSync(path.join(output, "keep.txt")), true);
});
