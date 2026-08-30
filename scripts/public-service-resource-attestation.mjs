#!/usr/bin/env node

import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const RUNNER_PATH = "scripts/public-service-resource-attestation.mjs";
const SHA256 = /^sha256:[a-f0-9]{64}$/;
const COMMIT = /^[a-f0-9]{40}$/;
const REQUIRED_CONTROLS = Object.freeze(["memory", "cpu", "pids", "openFiles", "outputBytes", "wallClock"]);
const EXPECTED_LAYER = process.platform === "win32" ? "windows-job-object" : "posix-rlimit";
const HANDLE_LIMIT_TEST_PATH = "servers/fastapi/tests/unit/test_isolated_public_workers.py";
const HANDLE_LIMIT_TEST_NAME = "test_export_subprocess_limit_kills_descendants_and_reports_recycle";
const METHODS_BY_LAYER = Object.freeze({
  "windows-job-object": Object.freeze({
    memory: "windows-job-object", cpu: "windows-job-object", pids: "windows-job-object",
    openFiles: "high-frequency-supervisor+job-termination",
    outputBytes: "worker-precommit-size+parent-postread/job-termination",
    wallClock: "monotonic-supervisor+job-termination",
  }),
  "posix-rlimit": Object.freeze({
    memory: "posix-rlimit-as", cpu: "posix-rlimit-cpu", pids: "posix-rlimit-nproc",
    openFiles: "posix-rlimit-nofile",
    outputBytes: "posix-rlimit-fsize+worker-precommit-size+parent-postread",
    wallClock: "monotonic-supervisor+process-group-termination",
  }),
});
const RESIDUAL_RISKS_BY_LAYER = Object.freeze({
  "windows-job-object": Object.freeze({
    openFiles: "A transient handle burst can occur between 50 ms supervisor samples; the next sample terminates the complete Job Object.",
  }),
  "posix-rlimit": Object.freeze({}),
});
const execFileAsync = promisify(execFile);

function fail(message) { throw new Error(message); }
function digest(bytes) { return `sha256:${createHash("sha256").update(bytes).digest("hex")}`; }
function repoRelative(absolute, label) {
  const resolved = path.resolve(absolute);
  const relative = path.relative(REPO, resolved);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative)) fail(`${label} must stay inside the RC worktree`);
  return relative.replaceAll("\\", "/");
}
function sampleArrays(root) {
  const found = [];
  const stack = [root];
  const visited = new Set();
  while (stack.length) {
    const value = stack.pop();
    if (!value || typeof value !== "object" || visited.has(value)) continue;
    visited.add(value);
    for (const [key, nested] of Object.entries(value)) {
      if (key === "worker_resource_samples" && Array.isArray(nested)) found.push(nested);
      else if (nested && typeof nested === "object") stack.push(nested);
    }
  }
  return found;
}
function durableExportOperations(result) {
  if (Array.isArray(result?.exportOperations) && result.exportOperations.length) {
    return result.exportOperations.map((item) => item?.exportOperation);
  }
  return result?.exportOperation ? [result.exportOperation] : [];
}
function validateSample(sample, expectedLayer) {
  if (!sample || typeof sample !== "object" || !["render", "export"].includes(sample.role)) {
    fail("durable worker sample has an invalid role");
  }
  if (sample.enforcementLayer !== expectedLayer || sample.resourceLimitsEnforced !== true) {
    fail("durable worker sample is not enforced by the expected OS layer");
  }
  if (sample.recycledWithinBaseline !== true || sample.finalProcessCount !== 0) {
    fail("durable worker sample did not recycle to zero processes");
  }
  for (const field of ["peakPrivateBytes", "peakHandleCount", "peakProcessCount", "peakCpuMillis"]) {
    if (!Number.isInteger(sample[field]) || sample[field] < 0) fail(`durable worker sample has invalid ${field}`);
  }
  if (!sample.resourceLimits || typeof sample.resourceLimits !== "object") fail("durable worker sample has no resource limits");
  for (const control of REQUIRED_CONTROLS) {
    if (!Number.isInteger(sample.resourceLimits[control]) || sample.resourceLimits[control] <= 0) {
      fail(`durable worker sample has invalid ${control} limit`);
    }
  }
  const expectedMethods = METHODS_BY_LAYER[expectedLayer];
  const methods = sample.controlEnforcementMethods;
  if (!methods || typeof methods !== "object" || Array.isArray(methods)
    || REQUIRED_CONTROLS.some((control) => methods[control] !== expectedMethods[control])
    || Object.keys(methods).length !== REQUIRED_CONTROLS.length) {
    fail("durable worker sample has missing or overstated control enforcement methods");
  }
  const expectedRisks = RESIDUAL_RISKS_BY_LAYER[expectedLayer];
  const risks = sample.residualRisks;
  if (!risks || typeof risks !== "object" || Array.isArray(risks)
    || JSON.stringify(risks) !== JSON.stringify(expectedRisks)) {
    fail("durable worker sample does not disclose the expected residual risks");
  }
  if (sample.supervisorPollIntervalMillis !== 50) {
    fail("durable worker sample has an invalid supervisor polling interval");
  }
  return Object.fromEntries([
    "role", "enforcementLayer", "resourceLimitsEnforced", "resourceLimits", "peakPrivateBytes",
    "peakHandleCount", "peakProcessCount", "peakCpuMillis", "recycledWithinBaseline", "finalProcessCount",
    "controlEnforcementMethods", "residualRisks", "supervisorPollIntervalMillis",
  ].map((field) => [field, sample[field]]));
}

export function deriveResourceLimitAttestation({ releaseCandidate, sourceDigest, provenance, reports,
  handleLimitCleanupTestBinding, expectedLayer = EXPECTED_LAYER }) {
  if (!SHA256.test(String(releaseCandidate || "")) || !COMMIT.test(String(sourceDigest || ""))) {
    fail("immutable source-manifest release candidate and source digest are required");
  }
  if (!provenance || provenance.runnerPath !== RUNNER_PATH || !SHA256.test(String(provenance.runnerSha256 || ""))
    || provenance.sourceManifestDigest !== releaseCandidate) fail("verified attestation runner provenance is required");
  if (!["windows-job-object", "posix-rlimit"].includes(expectedLayer)) fail("unsupported worker enforcement layer");
  if (!handleLimitCleanupTestBinding
    || handleLimitCleanupTestBinding.testPath !== HANDLE_LIMIT_TEST_PATH
    || handleLimitCleanupTestBinding.testName !== HANDLE_LIMIT_TEST_NAME
    || !SHA256.test(String(handleLimitCleanupTestBinding.testSourceSha256 || ""))
    || handleLimitCleanupTestBinding.executionRequired !== true) {
    fail("source-bound handle-limit descendant cleanup test contract is required");
  }
  if (!Array.isArray(reports) || !reports.length) fail("controlled product reports are required");
  const actualSamples = [];
  const inputReports = [];
  for (const input of reports) {
    if (typeof input?.path !== "string" || !input.path || !SHA256.test(String(input?.sha256 || ""))) {
      fail("controlled report provenance is invalid");
    }
    const report = input?.report;
    if (!report || report.schemaVersion !== 2 || report.kind !== "authored-vnext-controlled-load"
      || !Array.isArray(report.results) || !report.results.length) fail("input is not an actual controlled product report");
    let operationCount = 0;
    for (const result of report.results) {
      const operations = durableExportOperations(result);
      if (!operations.length) fail("result has no durable export operation");
      for (const operation of operations) {
        const arrays = sampleArrays(operation);
        if (arrays.length !== 1 || arrays[0].length === 0) fail("export operation has no unique durable worker sample set");
        const samples = arrays[0].map((sample) => validateSample(sample, expectedLayer));
        const roles = new Set(samples.map((sample) => sample.role));
        if (!roles.has("render") || !roles.has("export")) fail("each export operation must contain render and export worker samples");
        actualSamples.push(...samples);
        operationCount += 1;
      }
    }
    inputReports.push({ path: input.path, sha256: input.sha256, operationCount });
  }
  return {
    schemaVersion: "public-service-resource-limit-attestation/v1",
    releaseCandidate,
    sourceDigest,
    evidenceClass: "product-path",
    generatedAt: new Date().toISOString(),
    valid: true,
    enforced: true,
    resourceLimitsEnforced: true,
    enforcementLayer: expectedLayer,
    enforcedControls: [...REQUIRED_CONTROLS],
    controlEnforcementMethods: { ...METHODS_BY_LAYER[expectedLayer] },
    residualRisks: { ...RESIDUAL_RISKS_BY_LAYER[expectedLayer] },
    supervisorPollIntervalMillis: 50,
    handleLimitCleanupTestBinding,
    workerRoles: [...new Set(actualSamples.map((sample) => sample.role))].sort(),
    actualSamples,
    inputReports,
    provenance,
  };
}

async function verifySourceManifest(sourceManifestPath, releaseCandidate) {
  const absolute = path.resolve(sourceManifestPath);
  repoRelative(absolute, "source manifest");
  const bytes = await readFile(absolute);
  if (digest(bytes) !== releaseCandidate) fail("source manifest bytes do not match release candidate");
  let manifest;
  try { manifest = JSON.parse(bytes.toString("utf8")); } catch { fail("source manifest JSON is invalid"); }
  if (manifest?.schemaVersion !== "public-service-source-manifest/v1" || !COMMIT.test(String(manifest?.sourceTreeCommit || ""))
    || !Array.isArray(manifest.files) || !manifest.files.length) fail("source manifest contract is invalid");
  const { stdout } = await execFileAsync("git", ["rev-parse", "HEAD"], { cwd: REPO, windowsHide: true });
  if (stdout.trim() !== manifest.sourceTreeCommit) fail("source manifest sourceTreeCommit does not match HEAD");
  const files = new Map();
  for (const entry of manifest.files) {
    const sourcePath = String(entry?.path || "").replaceAll("\\", "/");
    if (!sourcePath || files.has(sourcePath) || !SHA256.test(String(entry?.sha256 || ""))) fail("invalid source manifest file entry");
    if (/^(?:artifacts|app_data|\.git)(?:\/|$)/.test(sourcePath)) fail("runtime artifacts are forbidden in source manifest");
    const sourceAbsolute = path.resolve(REPO, sourcePath);
    repoRelative(sourceAbsolute, "source manifest file");
    if (digest(await readFile(sourceAbsolute)) !== entry.sha256) fail(`source manifest file digest mismatch: ${sourcePath}`);
    files.set(sourcePath, entry.sha256);
  }
  if (!files.has(RUNNER_PATH)) fail("source manifest omits the resource attestation producer");
  if (!files.has(HANDLE_LIMIT_TEST_PATH)) fail("source manifest omits the handle-limit cleanup test contract");
  return { sourceTreeCommit: manifest.sourceTreeCommit,
    provenance: { runnerPath: RUNNER_PATH, runnerSha256: files.get(RUNNER_PATH), sourceManifestDigest: releaseCandidate },
    handleLimitCleanupTestBinding: { testPath: HANDLE_LIMIT_TEST_PATH, testName: HANDLE_LIMIT_TEST_NAME,
      testSourceSha256: files.get(HANDLE_LIMIT_TEST_PATH), executionRequired: true } };
}

async function readControlledReport(reportPath) {
  const absolute = path.resolve(reportPath);
  const relative = repoRelative(absolute, "controlled report");
  const bytes = await readFile(absolute);
  let report;
  try { report = JSON.parse(bytes.toString("utf8")); } catch { fail(`controlled report JSON is invalid: ${relative}`); }
  return { path: relative, sha256: digest(bytes), report };
}
function parseArgs(argv) {
  const args = { report: [] };
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index]; const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined) fail(`invalid argument: ${key}`);
    if (key === "--report") args.report.push(value); else args[key.slice(2)] = value;
  }
  return args;
}
async function atomicJson(target, payload) {
  await mkdir(path.dirname(target), { recursive: true });
  const temporary = `${target}.${process.pid}.tmp`;
  await writeFile(temporary, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  await rename(temporary, target);
}
async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args["release-candidate"] || !args["source-manifest"] || !args.output || !args.report.length) {
    fail("required: --release-candidate <sha256> --source-manifest <json> --report <controlled.json> [--report ...] --output <json>");
  }
  const output = path.resolve(args.output);
  const outputRelative = repoRelative(output, "attestation output");
  if (!outputRelative.startsWith("artifacts/")) fail("attestation output must stay below RC artifacts/");
  const verified = await verifySourceManifest(args["source-manifest"], args["release-candidate"]);
  const reports = await Promise.all(args.report.map(readControlledReport));
  const attestation = deriveResourceLimitAttestation({ releaseCandidate: args["release-candidate"],
    sourceDigest: verified.sourceTreeCommit, provenance: verified.provenance,
    handleLimitCleanupTestBinding: verified.handleLimitCleanupTestBinding, reports });
  await atomicJson(output, attestation);
  process.stdout.write(`${JSON.stringify({ output: outputRelative, sampleCount: attestation.actualSamples.length,
    workerRoles: attestation.workerRoles, enforcementLayer: attestation.enforcementLayer })}\n`);
}

if (path.resolve(process.argv[1] || "") === fileURLToPath(import.meta.url)) {
  main().catch((error) => { console.error(error?.stack || error); process.exitCode = 1; });
}
