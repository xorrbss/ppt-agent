#!/usr/bin/env node

import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPOSITORY_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SCHEMA_VERSION = "public-service-source-manifest/v1";
const EXCLUDED_PREFIXES = [
  "app_data/",
  "artifacts/",
  ".tmp/",
  "servers/nextjs/.next",
];
const REQUIRED_SOURCE_PATHS = Object.freeze([
  "scripts/create-public-service-source-manifest.mjs",
  "servers/fastapi/scripts/public_service_malicious_corpus_evidence.py",
  "servers/fastapi/services/isolated_worker_contracts.py",
  "servers/fastapi/services/isolated_worker_process.py",
  "servers/fastapi/services/artifact_store.py",
  "servers/fastapi/workers/public_worker_runtime.py",
  "servers/fastapi/workers/public_render_worker.py",
  "servers/fastapi/templates/v2/pptx/package_reader.py",
  "servers/fastapi/api/v1/ppt/endpoints/authored_vnext/export/router.py",
  "scripts/public-service-release-gate.mjs",
  "scripts/public-service-release-evidence-bundle.mjs",
  "scripts/public-service-trusted-collector.mjs",
  "contracts/public-service-release-evidence/trusted-collector-attestations.schema.json",
  "scripts/public-service-render-range-evidence.mjs",
  "scripts/public-service-release-matrix.mjs",
  "docs/experiments/authored-vnext/04-renderer-security/harness/run-harness.ps1",
  "docs/experiments/authored-vnext/04-renderer-security/harness/verify_container_inspect.py",
  "docs/experiments/authored-vnext/04-renderer-security/harness/verify_product_resilience.py",
  "docs/experiments/authored-vnext/04-renderer-security/ci/security_gate.py",
  "contracts/authored-renderer/attestation.py",
  "contracts/authored-renderer/invoke.py",
  "contracts/authored-renderer/compose.authored-renderer.json",
  "Dockerfile",
]);

function git(...args) {
  return execFileSync("git", args, { cwd: REPOSITORY_ROOT, encoding: "utf8" }).trim();
}

function repositoryPath(relativePath) {
  if (!relativePath || relativePath.includes("\\")) throw new Error(`invalid repository path: ${relativePath}`);
  const normalized = path.posix.normalize(relativePath);
  if (normalized !== relativePath || normalized.startsWith("../") || path.posix.isAbsolute(normalized)) {
    throw new Error(`unsafe repository path: ${relativePath}`);
  }
  const absolute = path.resolve(REPOSITORY_ROOT, ...relativePath.split("/"));
  if (!absolute.startsWith(`${REPOSITORY_ROOT}${path.sep}`)) throw new Error(`path escaped repository: ${relativePath}`);
  return absolute;
}

function isRuntimePath(relativePath) {
  return EXCLUDED_PREFIXES.some((prefix) => relativePath.startsWith(prefix));
}

export function mergeChangedSourcePaths({ unstaged = [], staged = [], untracked = [],
  deletedUnstaged = [], deletedStaged = [], outputRelativePath }) {
  const deleted = [...new Set([...deletedUnstaged, ...deletedStaged])].filter(Boolean).sort();
  if (deleted.length) throw new Error(`source manifest cannot bind deleted paths: ${deleted.join(",")}`);
  const paths = [...new Set([...REQUIRED_SOURCE_PATHS, ...unstaged, ...staged, ...untracked])]
    .filter((item) => item !== outputRelativePath && !isRuntimePath(item))
    .sort((left, right) => left.localeCompare(right));
  if (paths.length === 0) throw new Error("source manifest cannot describe an empty change set");
  return paths;
}

function changedSourcePaths(outputRelativePath) {
  const lines = (value) => value.split(/\r?\n/u).filter(Boolean);
  return mergeChangedSourcePaths({
    unstaged: lines(git("diff", "--name-only", "--diff-filter=ACMRTUXB")),
    staged: lines(git("diff", "--cached", "--name-only", "--diff-filter=ACMRTUXB")),
    untracked: lines(git("ls-files", "--others", "--exclude-standard")),
    deletedUnstaged: lines(git("diff", "--name-only", "--diff-filter=D")),
    deletedStaged: lines(git("diff", "--cached", "--name-only", "--diff-filter=D")),
    outputRelativePath,
  });
}

async function sha256File(relativePath) {
  const bytes = await readFile(repositoryPath(relativePath));
  return `sha256:${createHash("sha256").update(bytes).digest("hex")}`;
}

function outputRelativePath(outputPath) {
  const absolute = path.resolve(outputPath);
  const relative = path.relative(REPOSITORY_ROOT, absolute).split(path.sep).join("/");
  if (!relative || relative.startsWith("../") || path.isAbsolute(relative)) {
    throw new Error("source manifest output must stay below the repository root");
  }
  return relative;
}

async function main(argv = process.argv.slice(2)) {
  const outputIndex = argv.indexOf("--output");
  if (outputIndex < 0 || !argv[outputIndex + 1]) throw new Error("Usage: --output <repository-relative-path>");
  const output = path.resolve(REPOSITORY_ROOT, argv[outputIndex + 1]);
  const relativeOutput = outputRelativePath(output);
  if (!relativeOutput.startsWith("artifacts/")) throw new Error("source manifest output must be under artifacts/");

  const sourceTreeCommit = git("rev-parse", "HEAD");
  if (!/^[0-9a-f]{40}$/u.test(sourceTreeCommit)) throw new Error("git HEAD is not a full commit SHA");
  const paths = changedSourcePaths(relativeOutput);
  const files = await Promise.all(paths.map(async (filePath) => ({
    path: filePath,
    sha256: await sha256File(filePath),
  })));
  const manifest = { schemaVersion: SCHEMA_VERSION, sourceTreeCommit, files };
  const bytes = Buffer.from(`${JSON.stringify(manifest, null, 2)}\n`, "utf8");
  const releaseCandidate = `sha256:${createHash("sha256").update(bytes).digest("hex")}`;

  await mkdir(path.dirname(output), { recursive: true });
  const temporary = `${output}.tmp-${process.pid}`;
  await writeFile(temporary, bytes, { flag: "wx" });
  await rename(temporary, output);
  process.stdout.write(`${JSON.stringify({
    schemaVersion: SCHEMA_VERSION,
    sourceTreeCommit,
    releaseCandidate,
    fileCount: files.length,
    output: relativeOutput,
  })}\n`);
}

if (path.resolve(process.argv[1] || "") === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`${error?.stack || error}\n`);
    process.exitCode = 1;
  });
}
