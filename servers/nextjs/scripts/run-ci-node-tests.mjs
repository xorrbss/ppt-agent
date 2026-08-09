#!/usr/bin/env node
// CI node-test runner with auto-discovery. The previous package.json script
// listed 58 files by hand, so newly added *.test.* files silently never ran
// (26 such files existed when this runner replaced it). Every discovered test
// runs unless a rule below excludes it — exclusions are printed each run.
import { readdirSync } from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";
import process from "node:process";

const ROOTS = ["app", "components", "lib", "scripts", "tests", "utils"];
const TEST_RE = /\.test\.(ts|tsx|mjs)$/;
const EXCLUDED = [
  {
    re: /^lib\/export-fidelity\//,
    reason: "covered by test:template-v2-export-fidelity (dedicated Chromium/LibreOffice workflow)",
  },
  {
    re: /\.e2e\.test\.mjs$/,
    reason: "end-to-end: spawns the export runtime / Chrome; run via its dedicated flow",
  },
];

function walk(dir, out) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules" || entry.name.startsWith(".")) continue;
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(p, out);
    else if (TEST_RE.test(entry.name)) out.push(p.split(path.sep).join("/"));
  }
}

const all = [];
for (const root of ROOTS) walk(root, all);
all.sort();

const skipped = [];
const included = all.filter((file) => {
  const rule = EXCLUDED.find(({ re }) => re.test(file));
  if (rule) {
    skipped.push(`${file} — ${rule.reason}`);
    return false;
  }
  return true;
});

// template-v2 suites share on-disk studio state (journal, drafts); keep them
// serial exactly as the hand-written script did with --test-concurrency=1.
const serial = included.filter((file) => file.includes("template-v2"));
const parallel = included.filter((file) => !file.includes("template-v2"));

function run(files, extraArgs) {
  if (!files.length) return;
  const result = spawnSync(
    process.execPath,
    ["--experimental-strip-types", "--test", ...extraArgs, ...files],
    { stdio: "inherit" },
  );
  if (result.status !== 0) process.exit(result.status ?? 1);
}

console.log(
  `ci-node: running ${parallel.length} parallel + ${serial.length} serial test files; ${skipped.length} excluded:`,
);
for (const line of skipped) console.log(`  excluded: ${line}`);
run(parallel, []);
run(serial, ["--test-concurrency=1"]);
