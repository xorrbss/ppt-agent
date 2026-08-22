#!/usr/bin/env node

/**
 * Verify a fresh, atomic 2-slide Fidelity/Hybrid evidence descriptor.
 *
 * This command deliberately verifies artifacts; it does not infer a visual
 * pass, run PowerPoint, or turn a missing report into success.  Every file is
 * hash-bound to the descriptor and the descriptor is bound to the seed receipt
 * emitted by seed-authored-semantic-vertical-slice.py.
 */

import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { canonicalSha256 } from "../contracts/authored_vnext/semantic/fixtures/canonical-json.mjs";

export const EVIDENCE_SCHEMA = "authored-semantic-vertical-slice-evidence/v1";
export const VERIFICATION_SCHEMA = "authored-semantic-vertical-slice-evidence-verification/v1";
const DIGEST = /^sha256:[0-9a-f]{64}$/;

function fail(code, detail = "") {
  throw new Error(detail ? `${code}:${detail}` : code);
}

function requiredString(value, label) {
  if (typeof value !== "string" || value.length === 0) fail("missing-string", label);
  return value;
}

function normalizeDigest(value, label) {
  const candidate = typeof value === "string" && value.startsWith("sha256:") ? value : `sha256:${value ?? ""}`;
  if (!DIGEST.test(candidate)) fail("invalid-digest", label);
  return candidate;
}

function readJson(filePath, label) {
  try {
    const value = JSON.parse(fs.readFileSync(filePath, "utf8"));
    if (!value || typeof value !== "object" || Array.isArray(value)) fail("json-object-required", label);
    return value;
  } catch (error) {
    if (error?.message?.startsWith("json-object-required")) throw error;
    fail("invalid-json", `${label}:${error.message}`);
  }
}

function sha256File(filePath) {
  return `sha256:${crypto.createHash("sha256").update(fs.readFileSync(filePath)).digest("hex")}`;
}

function isWithin(root, candidate) {
  const relative = path.relative(root, candidate);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function resolveEvidencePath(root, value, label) {
  const resolved = path.resolve(root, requiredString(value, label));
  if (!isWithin(root, resolved)) fail("artifact-path-escapes-evidence-root", label);
  if (!fs.existsSync(resolved) || !fs.statSync(resolved).isFile()) fail("artifact-missing", label);
  return resolved;
}

function verifyFile(root, descriptor, label, paths, expectedPptxDigest = null) {
  if (!descriptor || typeof descriptor !== "object") fail("artifact-descriptor-required", label);
  const resolved = resolveEvidencePath(root, descriptor.path, `${label}:path`);
  if (paths.has(resolved)) fail("artifact-reused", resolved);
  paths.add(resolved);
  const expected = normalizeDigest(descriptor.sha256, `${label}:sha256`);
  const actual = sha256File(resolved);
  if (expected !== actual) fail("artifact-digest-mismatch", label);
  if (expectedPptxDigest !== null && normalizeDigest(descriptor.pptxSha256, `${label}:pptxSha256`) !== expectedPptxDigest) {
    fail("report-pptx-binding-mismatch", label);
  }
  return { path: resolved, sha256: actual };
}

function verifySeed(root, descriptor, expected, paths) {
  const seed = verifyFile(root, descriptor, "seedReceipt", paths);
  const value = readJson(seed.path, "seedReceipt");
  if (value.schemaVersion !== "authored-semantic-vertical-slice-seed/v1") fail("seed-schema-unsupported");
  for (const [key, expectedValue] of Object.entries(expected)) {
    if (value[key] !== expectedValue) fail("seed-binding-mismatch", key);
  }
  for (const key of ["promotionReady", "fullNativeParityReady", "independentVnextGenerationReady"]) {
    if (value[key] !== false) fail("promotion-flag-not-false", `seed:${key}`);
  }
  if (!Array.isArray(value.slideIds) || value.slideIds.length !== 2) fail("seed-two-slide-contract-required");
  return seed;
}

function verifyComReport(root, descriptor, pptxDigest, pptxPath, paths) {
  const report = verifyFile(root, descriptor, "comReport", paths, pptxDigest);
  const value = readJson(report.path, "comReport");
  const entries = Array.isArray(value.Presentations) ? value.Presentations : Array.isArray(value.presentations) ? value.presentations : null;
  if (entries) {
    const matching = entries.find((entry) => {
      const candidate = entry?.Path ?? entry?.path;
      return typeof candidate === "string" && path.resolve(candidate) === pptxPath;
    });
    if (!matching) fail("com-report-pptx-not-found", pptxPath);
    const readback = matching.ReadbackPassed ?? matching.readbackPassed;
    if (readback !== true) fail("com-readback-not-verified", pptxPath);
    if (matching.Reopened !== true && matching.reopened !== true) fail("com-reopen-not-verified", pptxPath);
  } else if ((value.pptxSha256 ?? value.sourcePptxSha256) !== pptxDigest) {
    fail("com-report-pptx-binding-missing", pptxPath);
  }
  return report;
}

function verifyRenderReport(root, descriptor, pptxDigest, paths) {
  const report = verifyFile(root, descriptor, "renderReport", paths, pptxDigest);
  const value = readJson(report.path, "renderReport");
  const sourceDigest = value?.sourcePptx?.sha256 ?? value?.sourcePptxSha256 ?? value?.pptxSha256;
  if (sourceDigest !== undefined && normalizeDigest(sourceDigest, "renderReport:sourcePptx") !== pptxDigest) fail("render-report-pptx-binding-mismatch");
  return report;
}

function verifyQualityReport(root, descriptor, pptxDigest, paths) {
  const report = verifyFile(root, descriptor, "qualityReport", paths, pptxDigest);
  const value = readJson(report.path, "qualityReport");
  const sourceDigest = value?.sourcePptx?.sha256 ?? value?.sourcePptxSha256 ?? value?.pptxSha256;
  if (sourceDigest !== undefined && normalizeDigest(sourceDigest, "qualityReport:sourcePptx") !== pptxDigest) fail("quality-report-pptx-binding-mismatch");
  return report;
}

function verifyMode(root, mode, expected, paths) {
  if (!mode || typeof mode !== "object") fail("mode-descriptor-required");
  if (!["fidelity", "hybrid"].includes(mode.mode)) fail("unsupported-mode", String(mode.mode));
  for (const key of ["attemptId", "presentationId", "operationId", "generationAttemptId"]) requiredString(mode[key], `${mode.mode}:${key}`);
  if (mode.presentationId !== expected.presentationId || mode.presentationRevision !== expected.presentationRevision || mode.manifestDigest !== expected.manifestDigest || mode.operationId !== expected.operationId || mode.generationAttemptId !== expected.generationAttemptId) {
    fail("mode-binding-mismatch", mode.mode);
  }
  if (mode.status === "passed" || mode.visualParity === "passed") fail("unverified-success-claim", mode.mode);
  const pptx = verifyFile(root, mode.pptx, `${mode.mode}:pptx`, paths);
  if (!fs.readFileSync(pptx.path).subarray(0, 2).equals(Buffer.from("PK"))) fail("pptx-zip-signature-missing", mode.mode);
  const pptxDigest = pptx.sha256;
  const reports = mode.reports;
  if (!reports || typeof reports !== "object") fail("reports-required", mode.mode);
  const com = verifyComReport(root, reports.com, pptxDigest, pptx.path, paths);
  const render = verifyRenderReport(root, reports.render, pptxDigest, paths);
  const quality = verifyQualityReport(root, reports.quality, pptxDigest, paths);
  if (!Array.isArray(mode.renderedPngs) || mode.renderedPngs.length !== 2) fail("two-rendered-pngs-required", mode.mode);
  const pngs = mode.renderedPngs.map((item, index) => verifyFile(root, item, `${mode.mode}:png:${index + 1}`, paths));
  const checks = mode.checks;
  if (!checks || typeof checks !== "object") fail("checks-required", mode.mode);
  if (checks.repairObserved === true) fail("repair-observed", mode.mode);
  for (const key of ["overflowGt5Pt", "overflowGt10Pt", "lineBreakChanges", "clipping", "overlap", "ghosting", "nativeBackplateCollision"]) {
    if (!Number.isInteger(checks[key]) || checks[key] < 0) fail("invalid-check-count", `${mode.mode}:${key}`);
  }
  return { mode: mode.mode, attemptId: mode.attemptId, pptxSha256: pptxDigest, comReportSha256: com.sha256, renderReportSha256: render.sha256, qualityReportSha256: quality.sha256, pngSha256: pngs.map((item) => item.sha256), checks };
}

export function verifyEvidenceManifest(manifestPath) {
  const absoluteManifest = path.resolve(manifestPath);
  const root = path.dirname(absoluteManifest);
  const evidence = readJson(absoluteManifest, "evidence");
  if (evidence.schemaVersion !== EVIDENCE_SCHEMA) fail("evidence-schema-unsupported");
  requiredString(evidence.evidenceBundleId, "evidenceBundleId");
  if (evidence.status === "passed" || evidence.visualParity === "passed") fail("unverified-success-claim", "evidence");
  for (const key of ["promotionReady", "fullNativeParityReady", "independentVnextGenerationReady"]) {
    if (evidence[key] !== false) fail("promotion-flag-not-false", `evidence:${key}`);
  }
  const expected = {};
  for (const key of ["presentationId", "operationId", "generationAttemptId", "manifestDigest"]) expected[key] = requiredString(evidence[key], key);
  if (!Number.isInteger(evidence.presentationRevision) || evidence.presentationRevision < 1) fail("invalid-presentation-revision");
  expected.presentationRevision = evidence.presentationRevision;
  normalizeDigest(expected.manifestDigest, "manifestDigest");
  const paths = new Set([absoluteManifest]);
  verifySeed(root, evidence.seedReceipt, expected, paths);
  if (!Array.isArray(evidence.modes) || evidence.modes.length !== 2) fail("fidelity-hybrid-pair-required");
  const modes = new Set();
  const attempts = new Set();
  const verifiedModes = [];
  for (const mode of evidence.modes) {
    if (modes.has(mode?.mode)) fail("duplicate-mode", String(mode?.mode));
    modes.add(mode?.mode);
    if (attempts.has(mode?.attemptId)) fail("attempt-reused", String(mode?.attemptId));
    attempts.add(mode?.attemptId);
    verifiedModes.push(verifyMode(root, mode, expected, paths));
  }
  if (!modes.has("fidelity") || !modes.has("hybrid")) fail("fidelity-hybrid-pair-missing");
  if (verifiedModes[0].pptxSha256 === verifiedModes[1].pptxSha256) fail("pptx-reused-across-modes");
  const freshness = evidence.freshness;
  if (!freshness || freshness.reusedFrom !== null || freshness.previousEvidenceDigest !== null) fail("fresh-evidence-required");
  const result = {
    schemaVersion: VERIFICATION_SCHEMA,
    evidenceBundleId: evidence.evidenceBundleId,
    status: "not-verified",
    presentationId: expected.presentationId,
    presentationRevision: expected.presentationRevision,
    manifestDigest: normalizeDigest(expected.manifestDigest, "manifestDigest"),
    operationId: expected.operationId,
    generationAttemptId: expected.generationAttemptId,
    modes: verifiedModes,
    promotionReady: false,
    fullNativeParityReady: false,
    independentVnextGenerationReady: false,
  };
  return { ...result, evidenceDigest: canonicalSha256(result) };
}

function parseArgs(argv) {
  const manifestIndex = argv.indexOf("--manifest");
  const outputIndex = argv.indexOf("--output");
  if (manifestIndex < 0 || outputIndex < 0 || !argv[manifestIndex + 1] || !argv[outputIndex + 1]) fail("usage", "--manifest <json> --output <json>");
  return { manifest: path.resolve(argv[manifestIndex + 1]), output: path.resolve(argv[outputIndex + 1]) };
}

if (process.argv[1] && path.resolve(fileURLToPath(import.meta.url)) === path.resolve(process.argv[1])) {
  try {
    const args = parseArgs(process.argv.slice(2));
    if (fs.existsSync(args.output)) fail("output-must-be-new", args.output);
    const result = verifyEvidenceManifest(args.manifest);
    const temporary = `${args.output}.${process.pid}.tmp`;
    fs.writeFileSync(temporary, `${JSON.stringify(result, null, 2)}\n`, { encoding: "utf8", flag: "wx" });
    fs.renameSync(temporary, args.output);
    console.log(JSON.stringify(result, null, 2));
  } catch (error) {
    console.error(`authored-semantic-evidence-invalid:${error.message}`);
    process.exitCode = 1;
  }
}
