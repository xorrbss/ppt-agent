#!/usr/bin/env node
/**
 * Validate an immutable Hybrid evidence bundle and report its retention state.
 * This command never deletes or rewrites supplied evidence directories.
 */
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const BUNDLE_SCHEMA = "presenton.authored-hybrid-atomic-evidence/v1";
const POLICY_SCHEMA = "presenton.authored-hybrid-evidence-retention/v1";

function fail(message) {
  throw new Error(`authored_hybrid_evidence_invalid:${message}`);
}

function record(value, name) {
  if (!value || typeof value !== "object" || Array.isArray(value)) fail(name);
  return value;
}

function text(value, name) {
  if (typeof value !== "string" || !value.trim()) fail(name);
  return value.trim();
}

function digest(value, name) {
  const normalized = text(value, name).toLowerCase();
  if (!/^[a-f0-9]{64}$/.test(normalized)) fail(`${name}_sha256`);
  return normalized;
}

function nonnegativeInteger(value, name) {
  if (!Number.isSafeInteger(value) || value < 0) fail(name);
  return value;
}

export function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export function sha256Canonical(value) {
  return createHash("sha256").update(canonicalJson(value)).digest("hex");
}

export function validateRetentionPolicy(value) {
  const policy = record(value, "policy");
  if (policy.schemaVersion !== POLICY_SCHEMA) fail("policy_schema");
  const expiryAction = text(policy.expiryAction, "policy_expiry_action");
  if (expiryAction !== "mark_expired_retain_metadata") fail("policy_expiry_action");
  if (policy.deletionAuthorized !== false) fail("policy_deletion_authorized");
  return {
    schemaVersion: POLICY_SCHEMA,
    attemptRetentionDays: nonnegativeInteger(policy.attemptRetentionDays, "policy_attempt_retention_days"),
    evidenceRetentionDays: nonnegativeInteger(policy.evidenceRetentionDays, "policy_evidence_retention_days"),
    expiryAction,
    deletionAuthorized: false,
    ciArtifactRetentionDays: nonnegativeInteger(policy.ciArtifactRetentionDays, "policy_ci_artifact_retention_days"),
  };
}

export function validateAtomicEvidenceBundle(value) {
  const bundle = record(value, "bundle");
  if (bundle.schemaVersion !== BUNDLE_SCHEMA) fail("bundle_schema");
  const attempt = record(bundle.attempt, "attempt");
  const source = record(bundle.source, "source");
  const artifact = record(bundle.artifact, "artifact");
  const render = record(bundle.render, "render");
  const report = record(bundle.report, "report");
  if (text(attempt.format, "attempt_format") !== "pptx") fail("attempt_format");
  const visualParity = text(report.visualParity, "report_visual_parity");
  if (!new Set(["passed", "failed", "not-verified", "preflight"]).has(visualParity)) {
    fail("report_visual_parity");
  }
  const pngDigests = Array.isArray(render.slidePngSha256)
    ? render.slidePngSha256.map((item, index) => digest(item, `render_slide_png_${index}`))
    : fail("render_slide_png");
  if (pngDigests.length === 0) fail("render_slide_png_empty");
  const evidenceDigest = digest(bundle.evidenceDigest, "evidence_digest");
  const unsigned = { ...bundle };
  delete unsigned.evidenceDigest;
  if (sha256Canonical(unsigned) !== evidenceDigest) fail("evidence_digest_mismatch");
  return {
    schemaVersion: BUNDLE_SCHEMA,
    attemptId: text(attempt.id, "attempt_id"),
    presentationId: text(attempt.presentationId, "presentation_id"),
    presentationRevision: nonnegativeInteger(attempt.presentationRevision, "presentation_revision"),
    createdAt: text(bundle.createdAt, "created_at"),
    canonicalSourceSha256: digest(source.canonicalSourceSha256, "canonical_source"),
    rendererSourceSha256: digest(source.rendererSourceSha256, "renderer_source"),
    pptxSha256: digest(artifact.pptxSha256, "pptx"),
    pptxSizeBytes: nonnegativeInteger(artifact.pptxSizeBytes, "pptx_size"),
    coverageManifestSha256: digest(artifact.coverageManifestSha256, "coverage_manifest"),
    renderManifestSha256: digest(render.manifestSha256, "render_manifest"),
    reportManifestSha256: digest(report.manifestSha256, "report_manifest"),
    slidePngSha256: pngDigests,
    visualParity,
    evidenceDigest,
  };
}

export function retentionDecision(bundle, policy, now = new Date()) {
  const created = new Date(bundle.createdAt);
  if (!Number.isFinite(created.getTime())) fail("created_at_timestamp");
  const expiresAt = new Date(created.getTime() + policy.evidenceRetentionDays * 86_400_000);
  return {
    status: now.getTime() >= expiresAt.getTime() ? "expired" : "retained",
    expiresAt: expiresAt.toISOString(),
    expiryAction: policy.expiryAction,
    deletionAuthorized: false,
  };
}

async function main(argv) {
  const bundlePath = argv[0];
  if (!bundlePath) fail("bundle_path_required");
  const nowIndex = argv.indexOf("--now");
  const now = nowIndex === -1 ? new Date() : new Date(argv[nowIndex + 1]);
  if (!Number.isFinite(now.getTime())) fail("now_timestamp");
  const root = fileURLToPath(new URL("..", import.meta.url));
  const [rawBundle, rawPolicy] = await Promise.all([
    readFile(resolve(bundlePath), "utf8"),
    readFile(resolve(root, "config/authored-hybrid-evidence-retention.json"), "utf8"),
  ]);
  const bundle = validateAtomicEvidenceBundle(JSON.parse(rawBundle));
  const policy = validateRetentionPolicy(JSON.parse(rawPolicy));
  process.stdout.write(`${JSON.stringify({ bundle, retention: retentionDecision(bundle, policy, now) })}\n`);
}

if (import.meta.main) {
  main(process.argv.slice(2)).catch((error) => {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  });
}
