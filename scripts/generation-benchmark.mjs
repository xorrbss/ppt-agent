#!/usr/bin/env node
// generation-benchmark.mjs — E1 fixture benchmark (programme design doc
// docs/generation-programme-master-design-20260904.md, section 8). Runs a
// pinned generation-request fixture through the WORKER LANE (the async path
// the UI uses: POST .../generate/async, then poll GET /api/v1/async-tasks/...)
// repeatedly against a live backend, and records per run: outcome, wall
// time, tokens, fit-ladder rung histogram, normalisations fired,
// quality-loop variant, and fallbackElementIds count, read from the
// backend's durable receipts under <app data>/authored-quality-receipts/.
//
// This is the exit measurement for programme phase 1: "worker-lane success
// on the fixture >= 80%; 20-slide wall <= 7 min on the token profile."
//
// Usage:
//   node scripts/generation-benchmark.mjs \
//     --base http://127.0.0.1:8000 \
//     --fixture scripts/fixtures/generation-benchmark/f1-stage12-x2.json \
//     --app-data <APP_DATA_DIRECTORY> \
//     --runs 20 --profile token \
//     --out artifacts/e1-benchmark-<date>
//
// Exit code 0 if the phase-1 thresholds pass (--success-min 0.8
// --wall-max-seconds 420 by default), 1 otherwise, so this can run as a CI
// gate. Never starts or stops a backend itself.
//
// Flags:
//   --base <url>              backend base URL (default http://127.0.0.1:8000)
//   --fixture <path>           generate-request JSON body to submit (required)
//   --runs <n>                 number of sequential runs (default 5)
//   --profile <token|api>      recorded on every run, not enforced (default "token")
//   --out <dir>                 output directory for runs.jsonl / summary.json / summary.md (required)
//   --app-data <dir>            APP_DATA_DIRECTORY to read receipts from
//                                (default: $APP_DATA_DIRECTORY env var)
//   --user <u> --password <p>   HTTP Basic creds (omit for Electron DISABLE_AUTH)
//   --timeout <sec>             per-run poll timeout in seconds (default 900)
//   --poll-interval-ms <ms>     status poll interval (default 5000)
//   --success-min <0..1>        phase-1 success-rate threshold (default 0.8)
//   --wall-max-seconds <sec>    phase-1 wall p90 threshold (default 420)
//   -h, --help                  show this help
//
// No npm dependencies. Node 18+ (uses global fetch).

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { readRunReceipts } from "./generation-benchmark-receipts.mjs";
import { computeSummary, evaluateThresholds, formatMarkdown } from "./generation-benchmark-report.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));

export function parseArgs(argv) {
  const args = {
    base: "http://127.0.0.1:8000",
    fixture: null,
    runs: 5,
    profile: "token",
    out: null,
    appData: process.env.APP_DATA_DIRECTORY || null,
    user: null,
    password: null,
    timeoutMs: 900 * 1000,
    pollIntervalMs: 5000,
    successMin: 0.8,
    wallMaxSeconds: 420,
    help: false,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i];
    if (flag === "--base") args.base = argv[++i];
    else if (flag === "--fixture") args.fixture = argv[++i];
    else if (flag === "--runs") args.runs = Number.parseInt(argv[++i], 10);
    else if (flag === "--profile") args.profile = argv[++i];
    else if (flag === "--out") args.out = argv[++i];
    else if (flag === "--app-data") args.appData = argv[++i];
    else if (flag === "--user") args.user = argv[++i];
    else if (flag === "--password") args.password = argv[++i];
    else if (flag === "--timeout") args.timeoutMs = Number.parseFloat(argv[++i]) * 1000;
    else if (flag === "--poll-interval-ms") args.pollIntervalMs = Number.parseInt(argv[++i], 10);
    else if (flag === "--success-min") args.successMin = Number.parseFloat(argv[++i]);
    else if (flag === "--wall-max-seconds") args.wallMaxSeconds = Number.parseFloat(argv[++i]);
    else if (flag === "--help" || flag === "-h") args.help = true;
    else throw new Error(`unknown flag: ${flag}`);
  }
  return args;
}

function printHelp() {
  const source = fs.readFileSync(fileURLToPath(import.meta.url), "utf8");
  const header = source.split("\nimport ")[0];
  console.log(header.replace(/^#!.*\n/, ""));
}

function authHeader(user, password) {
  if (user && password) {
    const token = Buffer.from(`${user}:${password}`, "utf8").toString("base64");
    return { Authorization: `Basic ${token}` };
  }
  return {};
}

async function abortableFetch(url, init, timeoutMs, descr) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } catch (err) {
    if (err && err.name === "AbortError") {
      throw new Error(`${descr} timed out after ${Math.round(timeoutMs / 1000)}s`);
    }
    throw new Error(`network error reaching ${url}: ${err && err.message ? err.message : err}`);
  } finally {
    clearTimeout(timer);
  }
}

async function readJsonSafely(res) {
  const text = await res.text();
  try {
    return { json: JSON.parse(text), text };
  } catch {
    return { json: null, text };
  }
}

/** POST the fixture body to the worker-lane async generate endpoint. */
async function submitAsyncGeneration(base, body, opts) {
  const headers = {
    "Content-Type": "application/json",
    Accept: "application/json",
    ...authHeader(opts.user, opts.password),
  };
  const res = await abortableFetch(
    `${base}/api/v1/ppt/presentation/generate/async`,
    { method: "POST", headers, body: JSON.stringify(body) },
    opts.timeoutMs,
    "async generate request"
  );
  const { json, text } = await readJsonSafely(res);
  if (!res.ok || !json || !json.id) {
    throw new Error(
      `async generate request failed HTTP ${res.status}: ${(text || "").slice(0, 500)}`
    );
  }
  return json.id;
}

/**
 * Poll GET /api/v1/async-tasks/status/{id} (services/async_task_read_service.py's
 * AsyncTaskReadModel) until `terminal` is true or the deadline passes. This is
 * the durable, job-state-backed read — it carries `job_id`/`job_state`, which
 * the legacy `/presentation/status/{id}` response does not, and `job_id` is
 * what the receipt filenames are hashed from.
 */
async function pollAsyncTask(base, taskId, opts, deadline) {
  const headers = { Accept: "application/json", ...authHeader(opts.user, opts.password) };
  const url = `${base}/api/v1/async-tasks/status/${encodeURIComponent(taskId)}`;
  while (true) {
    const remaining = deadline - Date.now();
    if (remaining <= 0) {
      return { timedOut: true };
    }
    const res = await abortableFetch(url, { method: "GET", headers }, Math.max(1, remaining), "status poll");
    const { json, text } = await readJsonSafely(res);
    if (!res.ok || !json) {
      throw new Error(`status poll failed HTTP ${res.status}: ${(text || "").slice(0, 500)}`);
    }
    if (json.terminal) {
      return { timedOut: false, task: json };
    }
    const wait = Math.min(opts.pollIntervalMs, Math.max(1, deadline - Date.now()));
    await new Promise((resolve) => setTimeout(resolve, wait));
  }
}

/**
 * The receipt writer runs in the same worker process as publication but is
 * not guaranteed to have flushed the instant the async task flips terminal
 * (both observe the job's DB row, not each other). Give it a few short
 * retries before recording the receipt as absent.
 */
async function readRunReceiptsWithRetry(receiptsDir, ids, { retries = 5, delayMs = 1000 } = {}) {
  if (!receiptsDir) {
    return readRunReceipts(null, ids);
  }
  let last = readRunReceipts(receiptsDir, ids);
  for (let attempt = 0; attempt < retries && last.outcome === null; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, delayMs));
    last = readRunReceipts(receiptsDir, ids);
  }
  return last;
}

export async function runOnce(base, body, runIndex, opts, receiptsDir) {
  const submittedAt = Date.now();
  const deadline = submittedAt + opts.timeoutMs;
  const record = {
    runIndex,
    fixture: opts.fixture,
    profile: opts.profile,
    submittedAt: new Date(submittedAt).toISOString(),
    taskId: null,
    jobId: null,
    presentationId: null,
    pollStatus: null,
    jobState: null,
    outcome: null,
    terminalError: null,
    measuredWallMillis: null,
    receiptWallMillis: null,
    tokens: null,
    fitLadderTrail: null,
    normalizations: null,
    qualityLoopVariant: null,
    providerRateProfile: null,
    fallbackElementIdsCount: null,
    slidesMeasured: null,
    attemptsRecorded: null,
    harnessError: null,
  };

  try {
    record.taskId = await submitAsyncGeneration(base, body, opts);
    const polled = await pollAsyncTask(base, record.taskId, opts, deadline);
    record.measuredWallMillis = Date.now() - submittedAt;
    if (polled.timedOut) {
      record.pollStatus = "timeout";
      record.harnessError = `poll did not reach a terminal state within ${opts.timeoutMs / 1000}s`;
      return record;
    }
    const task = polled.task;
    record.pollStatus = task.status;
    record.jobId = task.job_id || null;
    record.presentationId = task.presentation_id || null;
    record.jobState = task.job_state || null;

    const receipts = await readRunReceiptsWithRetry(receiptsDir, {
      jobId: record.jobId,
      presentationId: record.presentationId,
    });
    record.outcome = receipts.outcome;
    record.terminalError = receipts.terminalError;
    record.receiptWallMillis = receipts.wallMillis;
    record.tokens = receipts.tokens;
    record.fitLadderTrail = receipts.fitLadderTrail;
    record.normalizations = receipts.normalizations;
    record.qualityLoopVariant = receipts.qualityLoopVariant;
    record.providerRateProfile = receipts.providerRateProfile;
    record.fallbackElementIdsCount = receipts.fallbackElementIdsCount;
    record.slidesMeasured = receipts.slidesMeasured;
    record.attemptsRecorded = receipts.attemptsRecorded;

    // Fall back to the job's own terminal state when no receipt was found
    // (e.g. an infrastructure-class failure before authoring ever started).
    if (record.outcome === null) {
      if (record.jobState === "published") record.outcome = "succeeded";
      else if (record.jobState === "failed") record.outcome = "failed";
    }
  } catch (err) {
    record.measuredWallMillis = record.measuredWallMillis ?? Date.now() - submittedAt;
    record.harnessError = err && err.message ? err.message : String(err);
  }
  return record;
}

function die(message) {
  console.error(`generation-benchmark: ${message}`);
  process.exit(2);
}

export async function main(argv) {
  const args = parseArgs(argv);
  if (args.help) {
    printHelp();
    return 0;
  }
  if (!args.fixture) die("--fixture <path> is required");
  if (!args.out) die("--out <dir> is required");
  if (!["token", "api"].includes(args.profile)) die(`--profile must be "token" or "api"`);
  if (!Number.isInteger(args.runs) || args.runs <= 0) die("--runs must be a positive integer");

  const fixturePath = path.resolve(args.fixture);
  let body;
  try {
    body = JSON.parse(fs.readFileSync(fixturePath, "utf8"));
  } catch (err) {
    die(`cannot read --fixture "${fixturePath}": ${err && err.message ? err.message : err}`);
  }

  const outDir = path.resolve(args.out);
  fs.mkdirSync(outDir, { recursive: true });
  const runsFile = path.join(outDir, "runs.jsonl");
  fs.writeFileSync(runsFile, "");

  const receiptsDir = args.appData ? path.join(path.resolve(args.appData), "authored-quality-receipts") : null;
  if (!receiptsDir) {
    console.warn(
      "generation-benchmark: no --app-data (or $APP_DATA_DIRECTORY); receipts will not be read, " +
        "runs will only record the async task's own terminal state"
    );
  }

  const base = args.base.replace(/\/+$/, "");
  const opts = { ...args, base, fixture: args.fixture };

  const runs = [];
  for (let i = 1; i <= args.runs; i += 1) {
    console.log(`[${i}/${args.runs}] submitting ${fixturePath} to ${base} ...`);
    const record = await runOnce(base, body, i, opts, receiptsDir);
    runs.push(record);
    fs.appendFileSync(runsFile, JSON.stringify(record) + "\n");
    console.log(
      `[${i}/${args.runs}] outcome=${record.outcome ?? "(none)"} ` +
        `wall=${record.measuredWallMillis ? (record.measuredWallMillis / 1000).toFixed(1) + "s" : "n/a"}` +
        `${record.harnessError ? ` harnessError=${record.harnessError}` : ""}`
    );
  }

  const summary = computeSummary(runs, { fixture: args.fixture, profile: args.profile });
  const verdict = evaluateThresholds(summary, {
    successMin: args.successMin,
    wallMaxSeconds: args.wallMaxSeconds,
  });
  const summaryOut = { ...summary, ...verdict };

  fs.writeFileSync(path.join(outDir, "summary.json"), JSON.stringify(summaryOut, null, 2) + "\n");
  fs.writeFileSync(path.join(outDir, "summary.md"), formatMarkdown(summary, verdict));

  console.log("");
  console.log(formatMarkdown(summary, verdict));
  console.log(`wrote ${runsFile}`);
  console.log(`wrote ${path.join(outDir, "summary.json")}`);
  console.log(`wrote ${path.join(outDir, "summary.md")}`);

  return verdict.passed ? 0 : 1;
}

const invokedDirectly =
  process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (invokedDirectly) {
  main(process.argv.slice(2)).then(
    (code) => process.exit(code),
    (err) => {
      console.error(err && err.stack ? err.stack : err);
      process.exit(1);
    }
  );
}
