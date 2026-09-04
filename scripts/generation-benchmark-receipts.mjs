// generation-benchmark-receipts.mjs — read the backend's durable quality
// receipts for one generation job/presentation and normalise them into the
// fields the E1 fixture benchmark records per run.
//
// Two receipt files, both written under
// `<app data>/authored-quality-receipts/` (services/authored_generation_timing_receipt.py,
// services/authored_slide_quality_receipt.py):
//
//   job-<sha256(jobId)[:32]>-attempt-timing.json      one manifest per generation
//                                                      job, keyed by attempt.
//   deck-<sha256(presentationId)[:32]>-slide-quality.json  one manifest per
//                                                      presentation, keyed by
//                                                      slide index.
//
// The hashing (sha256, first 32 hex chars) must mirror
// quality_receipt_manifest_path / slide_quality_receipt_path exactly. No
// npm dependencies. Node 18+.

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

function sha256Hex32(value) {
  return crypto.createHash("sha256").update(String(value), "utf8").digest("hex").slice(0, 32);
}

/** Mirror of `generation_attempt_timing_receipt_path` in the backend. */
export function attemptTimingReceiptFileName(jobId) {
  return `job-${sha256Hex32(jobId)}-attempt-timing.json`;
}

/** Mirror of `slide_quality_receipt_path` in the backend. */
export function slideQualityReceiptFileName(presentationId) {
  return `deck-${sha256Hex32(presentationId)}-slide-quality.json`;
}

function readJsonFile(filePath) {
  let text;
  try {
    text = fs.readFileSync(filePath, "utf8");
  } catch {
    return null;
  }
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

/**
 * Read the attempt-timing manifest for one job. Returns null if the file
 * does not exist or cannot be parsed (receipts are best-effort/fail-open on
 * the backend, so a missing file is expected, not an error).
 */
export function readAttemptTimingManifest(receiptsDir, jobId) {
  if (!receiptsDir || !jobId) return null;
  const file = path.join(receiptsDir, attemptTimingReceiptFileName(jobId));
  return readJsonFile(file);
}

/**
 * Read the per-slide quality manifest for one presentation. Returns the
 * slide records sorted by numeric index, or null if unavailable.
 */
export function readSlideQualityRecords(receiptsDir, presentationId) {
  if (!receiptsDir || !presentationId) return null;
  const file = path.join(receiptsDir, slideQualityReceiptFileName(presentationId));
  const payload = readJsonFile(file);
  const slides = payload && payload.slides;
  if (!slides || typeof slides !== "object") return null;
  return Object.keys(slides)
    .sort((a, b) => Number(a) - Number(b))
    .map((key) => slides[key]);
}

/**
 * Pick the final attempt from an attempt-timing manifest: the one with the
 * highest attemptNumber, tie-broken by the highest revision. A job can be
 * retried, so several attempts may be recorded; the last one reflects the
 * job's terminal state.
 */
export function pickFinalAttempt(manifest) {
  const receipts = manifest && manifest.receipts;
  if (!receipts || typeof receipts !== "object") return null;
  const entries = Object.values(receipts);
  if (entries.length === 0) return null;
  return entries.reduce((best, entry) => {
    if (!best) return entry;
    const bestNum = Number(best.attemptNumber) || 0;
    const entryNum = Number(entry.attemptNumber) || 0;
    if (entryNum !== bestNum) return entryNum > bestNum ? entry : best;
    const bestRev = Number(best.revision) || 0;
    const entryRev = Number(entry.revision) || 0;
    return entryRev > bestRev ? entry : best;
  }, null);
}

function optionalArray(value) {
  return Array.isArray(value) ? value : null;
}

function optionalNumber(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * Normalise one attempt receipt into the fields E1 records. Fields the
 * schema does not carry yet (`fitLadder.trail`, `normalizations`,
 * `qualityLoop.variant`, `providerRateProfile` — E4, landing separately in
 * this same programme) are read defensively and reported as null when
 * absent, never invented.
 */
export function extractAttemptFields(attempt) {
  if (!attempt) {
    return {
      outcome: null,
      terminalError: null,
      wallMillis: null,
      tokens: null,
      fitLadderTrail: null,
      normalizations: null,
      qualityLoopVariant: null,
      providerRateProfile: null,
    };
  }
  const totals = attempt.providerUsage && attempt.providerUsage.totals;
  const tokens = totals
    ? {
        inputTokens: optionalNumber(totals.inputTokens),
        outputTokens: optionalNumber(totals.outputTokens),
        physicalCalls: optionalNumber(totals.physicalCalls),
        retryCalls: optionalNumber(totals.retryCalls),
        latencyMs: optionalNumber(totals.latencyMs),
      }
    : null;
  return {
    outcome: typeof attempt.outcome === "string" ? attempt.outcome : null,
    terminalError: typeof attempt.terminalError === "string" ? attempt.terminalError : null,
    wallMillis: optionalNumber(attempt.wallMillis),
    tokens,
    fitLadderTrail: optionalArray(attempt.fitLadder && attempt.fitLadder.trail),
    normalizations: optionalArray(attempt.normalizations),
    qualityLoopVariant:
      attempt.qualityLoop && typeof attempt.qualityLoop.variant === "string"
        ? attempt.qualityLoop.variant
        : null,
    providerRateProfile:
      typeof attempt.providerRateProfile === "string" ? attempt.providerRateProfile : null,
  };
}

/**
 * Sum `fallbackCount` (the length of the exporter's per-slide
 * `fallbackElementIds`, precomputed by the backend — see
 * services/authored_export_quality_flag.py) across measured slides.
 */
export function sumFallbackElementIds(slideRecords) {
  if (!Array.isArray(slideRecords) || slideRecords.length === 0) {
    return { fallbackElementIdsCount: null, slidesMeasured: 0 };
  }
  let sum = 0;
  let measured = 0;
  for (const record of slideRecords) {
    const count = record && optionalNumber(record.fallbackCount);
    if (count !== null) {
      sum += count;
      measured += 1;
    }
  }
  return {
    fallbackElementIdsCount: measured > 0 ? sum : null,
    slidesMeasured: slideRecords.length,
  };
}

/**
 * Collect the per-slide `normalizations` entries (A2 admission rules that
 * fired, written by services/authored_slide_quality_receipt.py) into one flat
 * list. Slides whose record carries no list are "not measured", not empty.
 */
export function collectSlideNormalizations(slideRecords) {
  if (!Array.isArray(slideRecords)) return null;
  const entries = [];
  let measured = 0;
  for (const record of slideRecords) {
    if (record && Array.isArray(record.normalizations)) {
      measured += 1;
      entries.push(...record.normalizations);
    }
  }
  return measured > 0 ? entries : null;
}

/**
 * Read both receipt files for one completed run and return the normalised
 * fields plus bookkeeping (how many attempts were recorded).
 */
export function readRunReceipts(receiptsDir, { jobId, presentationId }) {
  const manifest = readAttemptTimingManifest(receiptsDir, jobId);
  const finalAttempt = pickFinalAttempt(manifest);
  const attemptFields = extractAttemptFields(finalAttempt);
  const attemptsRecorded =
    manifest && manifest.receipts ? Object.keys(manifest.receipts).length : null;

  const slideRecords = readSlideQualityRecords(receiptsDir, presentationId);
  const { fallbackElementIdsCount, slidesMeasured } = sumFallbackElementIds(slideRecords);
  const slideNormalizations = collectSlideNormalizations(slideRecords);

  return {
    ...attemptFields,
    // The attempt-level list is reserved; today the rules are recorded per
    // slide, so the per-slide collection wins whenever it exists.
    normalizations: slideNormalizations ?? attemptFields.normalizations,
    attemptsRecorded,
    fallbackElementIdsCount,
    slidesMeasured: slideRecords === null ? null : slidesMeasured,
  };
}
