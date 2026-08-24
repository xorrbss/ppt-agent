#!/usr/bin/env node

/**
 * Verify a fresh, atomic eight-slide Authored/Fidelity/Hybrid evidence descriptor.
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

export const EVIDENCE_SCHEMA = "authored-semantic-vertical-slice-evidence/v3";
export const VERIFICATION_SCHEMA = "authored-semantic-vertical-slice-evidence-verification/v1";
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const SLIDE_COUNT = 8;
const MODES = ["authored", "fidelity", "hybrid"];
const REQUIRED_LANGUAGES = new Set(["ko", "zh", "ja"]);
const REQUIRED_ARCHETYPES = new Set(["card", "process", "timeline", "comparison", "metric", "infographic", "table", "chart", "image", "architecture", "roadmap"]);

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
  if (value.schemaVersion !== "authored-semantic-vertical-slice-seed/v2") fail("seed-schema-unsupported");
  for (const [key, expectedValue] of Object.entries(expected)) {
    if (value[key] !== expectedValue) fail("seed-binding-mismatch", key);
  }
  for (const key of ["promotionReady", "fullNativeParityReady", "independentVnextGenerationReady"]) {
    if (value[key] !== false) fail("promotion-flag-not-false", `seed:${key}`);
  }
  if (!Array.isArray(value.slideIds) || value.slideIds.length !== SLIDE_COUNT) fail("seed-eight-slide-contract-required");
  return seed;
}

function verifyComReport(root, descriptor, pptxDigest, pptxPath, exportAttemptId, paths) {
  const report = verifyFile(root, descriptor, "comReport", paths, pptxDigest);
  const value = readJson(report.path, "comReport");
  if (value.exportAttemptId !== exportAttemptId) fail("com-report-attempt-binding-mismatch");
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

function verifyRenderReport(root, descriptor, pptxDigest, exportAttemptId, paths) {
  const report = verifyFile(root, descriptor, "renderReport", paths, pptxDigest);
  const value = readJson(report.path, "renderReport");
  if (value.exportAttemptId !== exportAttemptId) fail("render-report-attempt-binding-mismatch");
  if (value.slideCount !== SLIDE_COUNT) fail("render-report-slide-count-mismatch");
  const sourceDigest = value?.sourcePptx?.sha256 ?? value?.sourcePptxSha256 ?? value?.pptxSha256;
  if (sourceDigest !== undefined && normalizeDigest(sourceDigest, "renderReport:sourcePptx") !== pptxDigest) fail("render-report-pptx-binding-mismatch");
  return report;
}

function verifyQualityReport(root, descriptor, pptxDigest, exportAttemptId, paths) {
  const report = verifyFile(root, descriptor, "qualityReport", paths, pptxDigest);
  const value = readJson(report.path, "qualityReport");
  if (value.exportAttemptId !== exportAttemptId) fail("quality-report-attempt-binding-mismatch");
  const sourceDigest = value?.sourcePptx?.sha256 ?? value?.sourcePptxSha256 ?? value?.pptxSha256;
  if (sourceDigest !== undefined && normalizeDigest(sourceDigest, "qualityReport:sourcePptx") !== pptxDigest) fail("quality-report-pptx-binding-mismatch");
  return report;
}

function verifyRegistry(evidence) {
  if (!Array.isArray(evidence.slideRegistry) || evidence.slideRegistry.length !== SLIDE_COUNT) fail("eight-slide-registry-required");
  const languages = new Set();
  const archetypes = new Set();
  const ids = new Set();
  for (const slide of evidence.slideRegistry) {
    requiredString(slide?.slideId, "slideRegistry:slideId");
    if (ids.has(slide.slideId)) fail("slide-registry-id-reused", slide.slideId);
    ids.add(slide.slideId);
    const language = requiredString(slide.language, `slideRegistry:${slide.slideId}:language`).toLowerCase().split("-")[0];
    languages.add(language);
    if (!Array.isArray(slide.archetypes) || slide.archetypes.length === 0) fail("slide-registry-archetypes-required", slide.slideId);
    slide.archetypes.forEach((archetype) => archetypes.add(requiredString(archetype, `slideRegistry:${slide.slideId}:archetype`)));
  }
  for (const language of REQUIRED_LANGUAGES) if (!languages.has(language)) fail("slide-registry-language-missing", language);
  for (const archetype of REQUIRED_ARCHETYPES) if (!archetypes.has(archetype)) fail("slide-registry-archetype-missing", archetype);
  const registryDigest = canonicalSha256(evidence.slideRegistry);
  if (evidence.registryDigest !== registryDigest) fail("slide-registry-digest-mismatch");
  return { registryDigest, languages: [...languages].sort(), archetypes: [...archetypes].sort() };
}

function verifyDiffPolicy(evidence) {
  const policy = evidence.diffPolicy;
  if (!policy || typeof policy !== "object") fail("diff-policy-required");
  if (typeof policy.maxChangedPixelRatio !== "number" || policy.maxChangedPixelRatio < 0 || policy.maxChangedPixelRatio > 1) fail("diff-policy-ratio-invalid");
  if (typeof policy.minSsim !== "number" || policy.minSsim < 0 || policy.minSsim > 1) fail("diff-policy-ssim-invalid");
  if (!Array.isArray(evidence.perSlideDiffs) || evidence.perSlideDiffs.length !== SLIDE_COUNT) fail("per-slide-diffs-eight-required");
  const slides = new Set();
  for (const entry of evidence.perSlideDiffs) {
    if (!Number.isInteger(entry?.slideNumber) || entry.slideNumber < 1 || entry.slideNumber > SLIDE_COUNT || slides.has(entry.slideNumber)) fail("per-slide-diff-number-invalid");
    slides.add(entry.slideNumber);
    if (!entry.pairs || typeof entry.pairs !== "object" || Object.keys(entry.pairs).sort().join(",") !== "authored-fidelity,authored-hybrid,fidelity-hybrid") fail("per-slide-diff-pairs-required", String(entry.slideNumber));
    for (const [pair, value] of Object.entries(entry.pairs)) {
      if (value?.status !== "pass") fail("per-slide-diff-not-passed", `${entry.slideNumber}:${pair}`);
      if (typeof value.changedPixelRatio !== "number" || value.changedPixelRatio < 0 || value.changedPixelRatio > policy.maxChangedPixelRatio) fail("per-slide-diff-ratio-exceeded", `${entry.slideNumber}:${pair}`);
      if (typeof value.ssim !== "number" || value.ssim < policy.minSsim || value.ssim > 1) fail("per-slide-diff-ssim-below-policy", `${entry.slideNumber}:${pair}`);
    }
  }
  if (slides.size !== SLIDE_COUNT) fail("per-slide-diff-slides-incomplete");
  return { policy, slides: evidence.perSlideDiffs };
}

function verifyManualLedger(evidence) {
  if (!evidence.manualReviewLedger || !Array.isArray(evidence.manualReviewLedger.entries) || evidence.manualReviewLedger.entries.length === 0) fail("manual-review-ledger-required");
  const reviewers = new Set();
  for (const entry of evidence.manualReviewLedger.entries) {
    const reviewer = requiredString(entry?.reviewer, "manual-review:reviewer");
    if (reviewers.has(reviewer)) fail("manual-review-reviewer-reused");
    reviewers.add(reviewer);
    requiredString(entry.reviewedAt, "manual-review:reviewedAt");
    if (entry.decision !== "accept" || !Array.isArray(entry.scope) || entry.scope.length === 0) fail("manual-review-not-accepted");
  }
  return evidence.manualReviewLedger.entries;
}

function verifyDbAttemptResolution(root, evidence, expected, paths, exportAttemptIds) {
  const resolution = evidence.dbAttemptResolution;
  if (!resolution || resolution.schemaVersion !== "authored-db-attempt-resolution/v1" || resolution.authority !== "database" || resolution.status !== "resolved") fail("db-attempt-resolution-required");
  for (const key of ["presentationId", "operationId", "generationAttemptId", "attemptId"]) requiredString(resolution[key], `db-resolution:${key}`);
  for (const key of ["presentationId", "operationId", "generationAttemptId"]) if (resolution[key] !== expected[key]) fail("db-resolution-binding-mismatch", key);
  const receipt = verifyFile(root, resolution.receipt, "dbAttemptResolution:receipt", paths);
  if (resolution.receiptSha256 !== receipt.sha256) fail("db-resolution-receipt-digest-mismatch");
  const receiptValue = readJson(receipt.path, "dbAttemptResolution:receipt");
  if (receiptValue.status !== "succeeded" || receiptValue.attemptId !== resolution.attemptId) fail("db-resolution-attempt-not-succeeded");
  for (const key of ["presentationId", "operationId", "generationAttemptId"]) if (receiptValue[key] !== expected[key]) fail("db-receipt-binding-mismatch", key);
  if (!(new Set(receiptValue.exportAttemptIds || []).size === exportAttemptIds.size && new Set(receiptValue.exportAttemptIds || []).size === [...exportAttemptIds].filter((id) => receiptValue.exportAttemptIds.includes(id)).length)) fail("db-receipt-export-attempts-mismatch");
  return { attemptId: resolution.attemptId, receiptSha256: receipt.sha256 };
}

function verifyMode(root, mode, expected, paths) {
  if (!mode || typeof mode !== "object") fail("mode-descriptor-required");
  if (!MODES.includes(mode.mode)) fail("unsupported-mode", String(mode.mode));
  for (const key of ["attemptId", "exportAttemptId", "presentationId", "operationId", "generationAttemptId"]) requiredString(mode[key], `${mode.mode}:${key}`);
  if (mode.presentationId !== expected.presentationId || mode.presentationRevision !== expected.presentationRevision || mode.manifestDigest !== expected.manifestDigest || mode.operationId !== expected.operationId || mode.generationAttemptId !== expected.generationAttemptId) {
    fail("mode-binding-mismatch", mode.mode);
  }
  if (mode.status === "passed" || mode.visualParity === "passed") fail("unverified-success-claim", mode.mode);
  const pptx = verifyFile(root, mode.pptx, `${mode.mode}:pptx`, paths);
  if (!fs.readFileSync(pptx.path).subarray(0, 2).equals(Buffer.from("PK"))) fail("pptx-zip-signature-missing", mode.mode);
  const pptxDigest = pptx.sha256;
  const reports = mode.reports;
  if (!reports || typeof reports !== "object") fail("reports-required", mode.mode);
  const com = verifyComReport(root, reports.com, pptxDigest, pptx.path, mode.exportAttemptId, paths);
  const render = verifyRenderReport(root, reports.render, pptxDigest, mode.exportAttemptId, paths);
  const quality = verifyQualityReport(root, reports.quality, pptxDigest, mode.exportAttemptId, paths);
  if (!Array.isArray(mode.renderedPngs) || mode.renderedPngs.length !== SLIDE_COUNT) fail("eight-rendered-pngs-required", mode.mode);
  const pngs = mode.renderedPngs.map((item, index) => verifyFile(root, item, `${mode.mode}:png:${index + 1}`, paths));
  const powerPoint = mode.powerPoint;
  if (!powerPoint || powerPoint.firstOpen !== true || powerPoint.reopened !== true || powerPoint.pptxSha256 !== pptxDigest) fail("powerpoint-first-open-reopen-required", mode.mode);
  if (!Array.isArray(powerPoint.firstOpenPngs) || powerPoint.firstOpenPngs.length !== SLIDE_COUNT || !Array.isArray(powerPoint.reopenPngs) || powerPoint.reopenPngs.length !== SLIDE_COUNT) fail("powerpoint-first-open-reopen-pngs-required", mode.mode);
  const firstOpenPngs = powerPoint.firstOpenPngs.map((item, index) => verifyFile(root, item, `${mode.mode}:first-open-png:${index + 1}`, paths));
  const reopenPngs = powerPoint.reopenPngs.map((item, index) => verifyFile(root, item, `${mode.mode}:reopen-png:${index + 1}`, paths));
  if (firstOpenPngs.map((item) => item.sha256).join(",") !== reopenPngs.map((item) => item.sha256).join(",")) fail("powerpoint-reopen-render-hash-mismatch", mode.mode);
  const checks = mode.checks;
  if (!checks || typeof checks !== "object") fail("checks-required", mode.mode);
  if (checks.repairObserved === true) fail("repair-observed", mode.mode);
  for (const key of ["overflowGt5Pt", "overflowGt10Pt", "lineBreakChanges", "clipping", "overlap", "ghosting", "nativeBackplateCollision"]) {
    if (!Number.isInteger(checks[key]) || checks[key] < 0) fail("invalid-check-count", `${mode.mode}:${key}`);
  }
  return { mode: mode.mode, attemptId: mode.attemptId, exportAttemptId: mode.exportAttemptId, pptxSha256: pptxDigest, comReportSha256: com.sha256, renderReportSha256: render.sha256, qualityReportSha256: quality.sha256, pngSha256: pngs.map((item) => item.sha256), firstOpenPngSha256: firstOpenPngs.map((item) => item.sha256), reopenPngSha256: reopenPngs.map((item) => item.sha256), checks };
}

function verifyRecoveryAttempts(evidence, expected) {
  if (!Array.isArray(evidence.recoveryAttempts) || evidence.recoveryAttempts.length !== 2) fail("remake-rollback-recovery-pair-required");
  const kinds = new Set();
  for (const attempt of evidence.recoveryAttempts) {
    if (!attempt || typeof attempt !== "object") fail("recovery-attempt-required");
    if (!["remake", "rollback"].includes(attempt.kind)) fail("unsupported-recovery-kind", String(attempt.kind));
    if (kinds.has(attempt.kind)) fail("duplicate-recovery-kind", attempt.kind);
    kinds.add(attempt.kind);
    for (const key of ["attemptId", "operationId", "generationAttemptId", "requestDigest", "responseDigest", "rejectionCode"]) requiredString(attempt[key], `recovery:${attempt.kind}:${key}`);
    if (!attempt.request || typeof attempt.request !== "object" || !attempt.response || typeof attempt.response !== "object") fail("recovery-payloads-required", attempt.kind);
    if (attempt.operationId !== expected.operationId) fail("recovery-lineage-mismatch", attempt.kind);
    if (attempt.kind === "rollback" && attempt.generationAttemptId === expected.generationAttemptId) fail("rollback-stale-attempt-lineage-required");
    if (attempt.outcome !== "rejected" || attempt.status === "passed" || attempt.accepted === true) fail("recovery-success-claim", attempt.kind);
    if (attempt.kind === "remake" && attempt.rejectionCode !== "STALE_REVISION") fail("remake-stale-revision-evidence-required");
    if (attempt.kind === "rollback" && attempt.rejectionCode !== "STALE_ATTEMPT") fail("rollback-stale-attempt-evidence-required");
    if (!Number.isInteger(attempt.observedRevision) || attempt.observedRevision >= expected.presentationRevision) fail("recovery-revision-not-stale", attempt.kind);
    if (normalizeDigest(attempt.requestDigest, `recovery:${attempt.kind}:requestDigest`) !== canonicalSha256(attempt.request)) fail("recovery-request-digest-mismatch", attempt.kind);
    if (normalizeDigest(attempt.responseDigest, `recovery:${attempt.kind}:responseDigest`) !== canonicalSha256(attempt.response)) fail("recovery-response-digest-mismatch", attempt.kind);
  }
  if (!kinds.has("remake") || !kinds.has("rollback")) fail("remake-rollback-recovery-pair-missing");
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
  for (const key of ["fixtureId", "presentationId", "operationId", "generationAttemptId", "manifestDigest"]) expected[key] = requiredString(evidence[key], key);
  if (!Number.isInteger(evidence.presentationRevision) || evidence.presentationRevision < 1) fail("invalid-presentation-revision");
  expected.presentationRevision = evidence.presentationRevision;
  normalizeDigest(expected.manifestDigest, "manifestDigest");
  const paths = new Set([absoluteManifest]);
  verifySeed(root, evidence.seedReceipt, expected, paths);
  const registry = verifyRegistry(evidence);
  const diffs = verifyDiffPolicy(evidence);
  const ledger = verifyManualLedger(evidence);
  if (!Array.isArray(evidence.modes) || evidence.modes.length !== MODES.length) fail("authored-fidelity-hybrid-mode-set-required");
  const modes = new Set();
  const attempts = new Set();
  const exportAttempts = new Set();
  const verifiedModes = [];
  for (const mode of evidence.modes) {
    if (modes.has(mode?.mode)) fail("duplicate-mode", String(mode?.mode));
    modes.add(mode?.mode);
    if (attempts.has(mode?.attemptId)) fail("attempt-reused", String(mode?.attemptId));
    attempts.add(mode?.attemptId);
    if (exportAttempts.has(mode?.exportAttemptId)) fail("export-attempt-reused", String(mode?.exportAttemptId));
    exportAttempts.add(mode?.exportAttemptId);
    verifiedModes.push(verifyMode(root, mode, expected, paths));
  }
  if (modes.size !== MODES.length || MODES.some((mode) => !modes.has(mode))) fail("authored-fidelity-hybrid-mode-set-incomplete");
  verifyRecoveryAttempts(evidence, expected);
  if (new Set(verifiedModes.map((mode) => mode.pptxSha256)).size !== MODES.length) fail("pptx-reused-across-modes");
  const dbResolution = verifyDbAttemptResolution(root, evidence, expected, paths, exportAttempts);
  const atomicBinding = evidence.atomicBinding;
  if (!atomicBinding || typeof atomicBinding !== "object") fail("atomic-binding-required");
  for (const [key, value] of Object.entries({ ...expected, registryDigest: registry.registryDigest, exportAttemptIds: [...exportAttempts].sort() })) if (JSON.stringify(atomicBinding[key]) !== JSON.stringify(value)) fail("atomic-binding-mismatch", key);
  if (evidence.atomicBindingDigest !== canonicalSha256(atomicBinding)) fail("atomic-binding-digest-mismatch");
  const freshness = evidence.freshness;
  if (!freshness || freshness.reusedFrom !== null || freshness.previousEvidenceDigest !== null) fail("fresh-evidence-required");
  requiredString(freshness.runId, "freshness:runId");
  requiredString(freshness.generatedAt, "freshness:generatedAt");
  const result = {
    schemaVersion: VERIFICATION_SCHEMA,
    evidenceBundleId: evidence.evidenceBundleId,
    status: "not-verified",
    fixtureId: expected.fixtureId,
    presentationId: expected.presentationId,
    presentationRevision: expected.presentationRevision,
    manifestDigest: normalizeDigest(expected.manifestDigest, "manifestDigest"),
    operationId: expected.operationId,
    generationAttemptId: expected.generationAttemptId,
    modes: verifiedModes,
    registry,
    diffs,
    manualReviewLedger: ledger,
    dbAttemptResolution: dbResolution,
    atomicBindingDigest: evidence.atomicBindingDigest,
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
