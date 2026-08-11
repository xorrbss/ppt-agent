import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, test } from "node:test";
import { checkRatchet, LINE_LIMIT } from "./check-file-size-ratchet.mjs";

const temporaryRoots = [];
afterEach(async () => Promise.all(temporaryRoots.splice(0).map((root) => rm(root, { recursive: true, force: true }))));

async function fixture(files, baseline) {
  const root = await mkdtemp(path.join(os.tmpdir(), "file-size-ratchet-"));
  temporaryRoots.push(root);
  for (const [name, source] of Object.entries(files)) {
    const filePath = path.join(root, name);
    await mkdir(path.dirname(filePath), { recursive: true });
    await writeFile(filePath, source);
  }
  const baselinePath = path.join(root, "baseline.json");
  await writeFile(baselinePath, JSON.stringify({ lineLimit: LINE_LIMIT, files: baseline }));
  return { root, baselinePath };
}

const lines = (count) => `${Array.from({ length: count }, () => "x").join("\n")}\n`;

test("allows an existing oversized file at its baseline", async () => {
  const { root, baselinePath } = await fixture({ "src/large.ts": lines(501), "src/at-limit.ts": lines(500) }, { "src/large.ts": 501 });
  assert.deepEqual(await checkRatchet(root, baselinePath), []);
});

test("rejects a new oversized source file and a missing baseline entry", async () => {
  const { root, baselinePath } = await fixture({ "src/new.ts": lines(501) }, {});
  assert.deepEqual(await checkRatchet(root, baselinePath), ["new oversized file: src/new.ts (501 lines)"]);
});

test("rejects growth of a baseline oversized file", async () => {
  const { root, baselinePath } = await fixture({ "src/large.ts": lines(502) }, { "src/large.ts": 501 });
  assert.deepEqual(await checkRatchet(root, baselinePath), ["oversized file grew: src/large.ts (501 -> 502 lines)"]);
});

test("accepts a machine-readable documented exception", async () => {
  const exception = {
    lines: 501,
    rationale: "Generated compatibility surface pending modular generator output.",
    owner: "provider-registry",
    removalCondition: "Remove when generated output is split below the line limit.",
  };
  const { root, baselinePath } = await fixture(
    { "src/large.py": lines(501) },
    { "src/large.py": exception }
  );
  assert.deepEqual(await checkRatchet(root, baselinePath), []);
});

test("rejects an undocumented object exception", async () => {
  const { root, baselinePath } = await fixture(
    { "src/large.py": lines(501) },
    { "src/large.py": { lines: 501 } }
  );
  assert.deepEqual(await checkRatchet(root, baselinePath), [
    "undocumented baseline exception: src/large.py",
  ]);
});

test("rejects a baseline entry that no longer maps to an oversized source", async () => {
  const { root, baselinePath } = await fixture({ "src/small.ts": lines(500) }, { "src/legacy.ts": 501 });
  assert.deepEqual(await checkRatchet(root, baselinePath), ["baseline entry no longer maps to an oversized source: src/legacy.ts"]);
});

test("requires portable POSIX baseline paths", async () => {
  const { root, baselinePath } = await fixture({ "src/large.ts": lines(501) }, { "src\\large.ts": 501 });
  assert.deepEqual(await checkRatchet(root, baselinePath), [
    "invalid baseline entry: src\\large.ts",
    "new oversized file: src/large.ts (501 lines)",
  ]);
});

test("uses git file discovery in a repository worktree", async () => {
  const { root, baselinePath } = await fixture({ "src/large.ts": lines(501) }, { "src/large.ts": 501 });
  execFileSync("git", ["init", "--quiet"], { cwd: root });
  execFileSync("git", ["add", "src/large.ts"], { cwd: root });
  assert.deepEqual(await checkRatchet(root, baselinePath), []);
});

test("ignores tracked files deleted from the working tree", async () => {
  const { root, baselinePath } = await fixture({ "src/deleted.ts": lines(501) }, {});
  execFileSync("git", ["init", "--quiet"], { cwd: root });
  execFileSync("git", ["add", "src/deleted.ts"], { cwd: root });
  await rm(path.join(root, "src/deleted.ts"));
  assert.deepEqual(await checkRatchet(root, baselinePath), []);
});

test("excludes tests, generated files, vendor files, and migrations", async () => {
  const { root, baselinePath } = await fixture({
    "src/example.test.ts": lines(501),
    "cypress/e2e/example.cy.ts": lines(501),
    "src/layout.generated.ts": lines(501),
    "src/provider_registry_generated.py": lines(501),
    "servers/nextjs/public/vendor/library.js": lines(501),
    "servers/fastapi/alembic/versions/0001_initial.py": lines(501),
  }, {});
  assert.deepEqual(await checkRatchet(root, baselinePath), []);
});
