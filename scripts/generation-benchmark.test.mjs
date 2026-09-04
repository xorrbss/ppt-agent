import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  attemptTimingReceiptFileName,
  slideQualityReceiptFileName,
  readAttemptTimingManifest,
  readSlideQualityRecords,
  pickFinalAttempt,
  extractAttemptFields,
  sumFallbackElementIds,
  readRunReceipts,
} from "./generation-benchmark-receipts.mjs";
import {
  percentile,
  computeSummary,
  evaluateThresholds,
  formatMarkdown,
} from "./generation-benchmark-report.mjs";
import { parseArgs } from "./generation-benchmark.mjs";

const CLI = fileURLToPath(new URL("./generation-benchmark.mjs", import.meta.url));

function tmpDir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "generation-benchmark-test-"));
}

function sha256Hex32(value) {
  return crypto.createHash("sha256").update(String(value), "utf8").digest("hex").slice(0, 32);
}

// ---------- receipts: filenames mirror the backend's hashing exactly ----------

test("attemptTimingReceiptFileName mirrors quality_receipt_manifest_path's attempt-timing sidecar", () => {
  const jobId = "11111111-2222-3333-4444-555555555555";
  const expected = `job-${sha256Hex32(jobId)}-attempt-timing.json`;
  assert.equal(attemptTimingReceiptFileName(jobId), expected);
});

test("slideQualityReceiptFileName mirrors slide_quality_receipt_path", () => {
  const presentationId = "9d6116e5-ebf1-4da3-9237-128e7ce36b79";
  const expected = `deck-${sha256Hex32(presentationId)}-slide-quality.json`;
  assert.equal(slideQualityReceiptFileName(presentationId), expected);
});

// ---------- receipts: reading synthetic manifests ----------

test("readAttemptTimingManifest returns null when the file is absent", () => {
  const dir = tmpDir();
  assert.equal(readAttemptTimingManifest(dir, "no-such-job"), null);
  assert.equal(readAttemptTimingManifest(null, "job"), null);
  assert.equal(readAttemptTimingManifest(dir, null), null);
});

test("readAttemptTimingManifest reads a manifest written at the hashed path", () => {
  const dir = tmpDir();
  const jobId = "job-abc";
  const manifest = {
    schemaVersion: "generation-attempt-timing-manifest/v1",
    receipts: { "a#1#0": { outcome: "succeeded", attemptNumber: 1, revision: 0 } },
  };
  fs.writeFileSync(path.join(dir, attemptTimingReceiptFileName(jobId)), JSON.stringify(manifest));
  assert.deepEqual(readAttemptTimingManifest(dir, jobId), manifest);
});

test("pickFinalAttempt picks the highest attemptNumber, then the highest revision", () => {
  const manifest = {
    receipts: {
      a: { attemptNumber: 1, revision: 0, outcome: "failed" },
      b: { attemptNumber: 3, revision: 0, outcome: "failed" },
      c: { attemptNumber: 3, revision: 1, outcome: "succeeded" },
      d: { attemptNumber: 2, revision: 5, outcome: "failed" },
    },
  };
  assert.equal(pickFinalAttempt(manifest).outcome, "succeeded");
  assert.equal(pickFinalAttempt(null), null);
  assert.equal(pickFinalAttempt({ receipts: {} }), null);
});

test("extractAttemptFields reads today's schema and leaves not-yet-landed fields null", () => {
  const attempt = {
    outcome: "succeeded",
    terminalError: null,
    wallMillis: 144600,
    providerUsage: {
      totals: {
        inputTokens: 40213,
        outputTokens: 17042,
        physicalCalls: 10,
        retryCalls: 1,
        latencyMs: 619000,
      },
    },
    // no fitLadder / normalizations / qualityLoop / providerRateProfile yet
  };
  const fields = extractAttemptFields(attempt);
  assert.equal(fields.outcome, "succeeded");
  assert.equal(fields.terminalError, null);
  assert.equal(fields.wallMillis, 144600);
  assert.deepEqual(fields.tokens, {
    inputTokens: 40213,
    outputTokens: 17042,
    physicalCalls: 10,
    retryCalls: 1,
    latencyMs: 619000,
  });
  assert.equal(fields.fitLadderTrail, null);
  assert.equal(fields.normalizations, null);
  assert.equal(fields.qualityLoopVariant, null);
  assert.equal(fields.providerRateProfile, null);
});

test("extractAttemptFields reads the future E4 fields once they exist", () => {
  const attempt = {
    outcome: "succeeded",
    terminalError: null,
    wallMillis: 100,
    providerUsage: { totals: { inputTokens: 1, outputTokens: 2, physicalCalls: 1, retryCalls: 0, latencyMs: 5 } },
    fitLadder: { trail: [0, 1, "terminal"] },
    normalizations: ["stray_style_attr", { id: "empty_bullet" }],
    qualityLoop: { variant: "n4" },
    providerRateProfile: "codex-personal",
  };
  const fields = extractAttemptFields(attempt);
  assert.deepEqual(fields.fitLadderTrail, [0, 1, "terminal"]);
  assert.deepEqual(fields.normalizations, ["stray_style_attr", { id: "empty_bullet" }]);
  assert.equal(fields.qualityLoopVariant, "n4");
  assert.equal(fields.providerRateProfile, "codex-personal");
});

test("extractAttemptFields(null) returns an all-null record, not a thrown error", () => {
  const fields = extractAttemptFields(null);
  assert.equal(fields.outcome, null);
  assert.equal(fields.tokens, null);
});

test("sumFallbackElementIds sums fallbackCount across measured slides only", () => {
  const slides = [
    { fallbackCount: 1 },
    { fallbackCount: null },
    { fallbackCount: 3 },
  ];
  assert.deepEqual(sumFallbackElementIds(slides), {
    fallbackElementIdsCount: 4,
    slidesMeasured: 3,
  });
  assert.deepEqual(sumFallbackElementIds([]), { fallbackElementIdsCount: null, slidesMeasured: 0 });
  assert.deepEqual(sumFallbackElementIds(null), { fallbackElementIdsCount: null, slidesMeasured: 0 });
});

test("readRunReceipts combines both receipt files end to end", () => {
  const dir = tmpDir();
  const jobId = "22222222-3333-4444-5555-666666666666";
  const presentationId = "77777777-8888-9999-aaaa-bbbbbbbbbbbb";
  fs.writeFileSync(
    path.join(dir, attemptTimingReceiptFileName(jobId)),
    JSON.stringify({
      receipts: {
        "x#1#0": {
          attemptNumber: 1,
          revision: 0,
          outcome: "succeeded",
          terminalError: null,
          wallMillis: 5000,
          providerUsage: {
            totals: { inputTokens: 10, outputTokens: 20, physicalCalls: 1, retryCalls: 0, latencyMs: 100 },
          },
        },
      },
    })
  );
  fs.writeFileSync(
    path.join(dir, slideQualityReceiptFileName(presentationId)),
    JSON.stringify({
      slides: {
        "0": { slideIndex: 0, fallbackCount: 0 },
        "1": { slideIndex: 1, fallbackCount: 2 },
      },
    })
  );
  const result = readRunReceipts(dir, { jobId, presentationId });
  assert.equal(result.outcome, "succeeded");
  assert.equal(result.attemptsRecorded, 1);
  assert.equal(result.fallbackElementIdsCount, 2);
  assert.equal(result.slidesMeasured, 2);
});

test("readRunReceipts is fail-open when receipts are missing (never throws)", () => {
  const dir = tmpDir();
  const result = readRunReceipts(dir, { jobId: "missing", presentationId: "missing" });
  assert.equal(result.outcome, null);
  assert.equal(result.attemptsRecorded, null);
  assert.equal(result.fallbackElementIdsCount, null);
  assert.equal(result.slidesMeasured, null);
});

// ---------- report: pure aggregation ----------

test("percentile picks nearest-rank p50/p90 over a small sample", () => {
  const values = [100, 200, 300, 400, 500];
  assert.equal(percentile(values, 50), 300);
  assert.equal(percentile(values, 90), 500);
  assert.equal(percentile([], 50), null);
});

function makeRun(overrides) {
  return {
    outcome: null,
    measuredWallMillis: null,
    tokens: null,
    fitLadderTrail: null,
    normalizations: null,
    qualityLoopVariant: null,
    terminalError: null,
    fallbackElementIdsCount: null,
    ...overrides,
  };
}

test("computeSummary: success rate, wall p50/p90, tokens per successful deck", () => {
  const runs = [
    makeRun({ outcome: "succeeded", measuredWallMillis: 100000, tokens: { inputTokens: 40000, outputTokens: 17000 } }),
    makeRun({ outcome: "succeeded", measuredWallMillis: 200000, tokens: { inputTokens: 44000, outputTokens: 19000 } }),
    makeRun({ outcome: "failed", measuredWallMillis: 50000, terminalError: "authored_composition_safety_gate_failed" }),
    makeRun({ outcome: "failed", measuredWallMillis: 400000, terminalError: "authored_composition_safety_gate_failed" }),
  ];
  const summary = computeSummary(runs, { fixture: "f1-stage12.json", profile: "token" });
  assert.equal(summary.runs, 4);
  assert.equal(summary.succeeded, 2);
  assert.equal(summary.successRate, 0.5);
  assert.equal(summary.wall.measuredRuns, 4);
  assert.equal(summary.tokensPerSuccessfulDeck.decksMeasured, 2);
  assert.equal(summary.tokensPerSuccessfulDeck.inputTokensAvg, 42000);
  assert.deepEqual(summary.terminalErrorHistogram, { authored_composition_safety_gate_failed: 2 });
});

test("computeSummary: rung histogram and normalisation counts only populate when observed", () => {
  const withData = computeSummary([
    makeRun({ fitLadderTrail: [0, 1, 1] }),
    makeRun({ fitLadderTrail: [0] }),
    makeRun({ normalizations: ["stray_style_attr", "stray_style_attr", { id: "empty_bullet" }] }),
  ]);
  assert.deepEqual(withData.rungHistogram, { "0": 2, "1": 2 });
  assert.deepEqual(withData.normalizationCounts, { stray_style_attr: 2, empty_bullet: 1 });

  const withoutData = computeSummary([makeRun({}), makeRun({})]);
  assert.equal(withoutData.rungHistogram, null);
  assert.equal(withoutData.normalizationCounts, null);
  assert.equal(withoutData.tokensPerSuccessfulDeck, null);
});

test("computeSummary: fallbackElementIds average only over measured runs", () => {
  const summary = computeSummary([
    makeRun({ fallbackElementIdsCount: 0 }),
    makeRun({ fallbackElementIdsCount: 4 }),
    makeRun({ fallbackElementIdsCount: null }),
  ]);
  assert.equal(summary.fallbackElementIdsCountAvg, 2);
  assert.equal(summary.fallbackElementIdsMeasuredRuns, 2);
});

test("evaluateThresholds: phase-1 defaults (0.8 success, 420s wall p90)", () => {
  const passingSummary = computeSummary(
    Array.from({ length: 10 }, (_, i) =>
      makeRun({ outcome: i < 9 ? "succeeded" : "failed", measuredWallMillis: 300000 })
    )
  );
  const pass = evaluateThresholds(passingSummary);
  assert.equal(pass.passed, true);
  assert.deepEqual(pass.reasons, []);

  const failingSummary = computeSummary(
    Array.from({ length: 10 }, (_, i) =>
      makeRun({ outcome: i < 5 ? "succeeded" : "failed", measuredWallMillis: 500000 })
    )
  );
  const fail = evaluateThresholds(failingSummary);
  assert.equal(fail.passed, false);
  assert.equal(fail.successPass, false);
  assert.equal(fail.wallPass, false);
  assert.equal(fail.reasons.length, 2);
});

test("evaluateThresholds: custom thresholds override the phase-1 defaults", () => {
  const summary = computeSummary([makeRun({ outcome: "succeeded", measuredWallMillis: 600000 })]);
  const verdict = evaluateThresholds(summary, { successMin: 1, wallMaxSeconds: 700 });
  assert.equal(verdict.passed, true);
});

test("formatMarkdown renders every required section without throwing on empty data", () => {
  const summary = computeSummary([]);
  const verdict = evaluateThresholds(summary);
  const md = formatMarkdown(summary, verdict);
  assert.match(md, /# E1 fixture benchmark summary/);
  assert.match(md, /Fit-ladder rung histogram/);
  assert.match(md, /Normalisations fired/);
  assert.match(md, /Quality-loop variant/);
  assert.match(md, /fallbackElementIds count/);
});

// ---------- CLI: arg parsing and --help ----------

test("parseArgs applies documented defaults", () => {
  const args = parseArgs(["--fixture", "f.json", "--out", "out"]);
  assert.equal(args.base, "http://127.0.0.1:8000");
  assert.equal(args.runs, 5);
  assert.equal(args.profile, "token");
  assert.equal(args.timeoutMs, 900 * 1000);
  assert.equal(args.pollIntervalMs, 5000);
  assert.equal(args.successMin, 0.8);
  assert.equal(args.wallMaxSeconds, 420);
});

test("parseArgs overrides every flag it accepts", () => {
  const args = parseArgs([
    "--base", "http://example.test:9000",
    "--fixture", "fixtures/x2.json",
    "--runs", "12",
    "--profile", "api",
    "--out", "artifacts/e1",
    "--app-data", "C:/app_data",
    "--user", "admin",
    "--password", "secret",
    "--timeout", "60",
    "--poll-interval-ms", "1000",
    "--success-min", "0.9",
    "--wall-max-seconds", "300",
  ]);
  assert.equal(args.base, "http://example.test:9000");
  assert.equal(args.fixture, "fixtures/x2.json");
  assert.equal(args.runs, 12);
  assert.equal(args.profile, "api");
  assert.equal(args.out, "artifacts/e1");
  assert.equal(args.appData, "C:/app_data");
  assert.equal(args.user, "admin");
  assert.equal(args.password, "secret");
  assert.equal(args.timeoutMs, 60 * 1000);
  assert.equal(args.pollIntervalMs, 1000);
  assert.equal(args.successMin, 0.9);
  assert.equal(args.wallMaxSeconds, 300);
});

test("parseArgs rejects an unknown flag", () => {
  assert.throws(() => parseArgs(["--bogus"]), /unknown flag/);
});

function runCli(args) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [CLI, ...args], { stdio: ["ignore", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => (stdout += chunk));
    child.stderr.on("data", (chunk) => (stderr += chunk));
    child.on("error", reject);
    child.on("close", (code) => resolve({ code, stdout, stderr }));
  });
}

test("CLI --help exits 0 and prints usage without touching the network", async () => {
  const { code, stdout } = await runCli(["--help"]);
  assert.equal(code, 0);
  assert.match(stdout, /generation-benchmark\.mjs/);
  assert.match(stdout, /--fixture/);
  assert.match(stdout, /--wall-max-seconds/);
});

test("CLI exits 2 with a clear message when --fixture is missing", async () => {
  const { code, stderr } = await runCli(["--out", "out"]);
  assert.equal(code, 2);
  assert.match(stderr, /--fixture/);
});

test("CLI exits 2 with a clear message when --out is missing", async () => {
  const { code, stderr } = await runCli(["--fixture", "f.json"]);
  assert.equal(code, 2);
  assert.match(stderr, /--out/);
});
