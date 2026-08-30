#!/usr/bin/env node

import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const SCHEMA_VERSION = "public-service-release-evidence-bundle/v1";
export const CANONICALIZATION = "json-key-sort-v1";

const SHA256 = /^sha256:[a-f0-9]{64}$/u;
const COMMIT = /^[a-f0-9]{40}$/u;
const REQUIRED_COMPONENTS = Object.freeze([
  "api", "worker", "provider", "renderer", "exporter", "fonts",
]);
const REQUIRED_SUCCESS_PHASES = Object.freeze([
  "api", "queue", "worker", "provider", "render", "publication", "export", "download", "readback",
]);
const PHASE_STATUSES = new Set(["succeeded", "failed", "skipped"]);

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isNonEmptyString(value) {
  return typeof value === "string" && value.length > 0;
}

function isNonNegativeInteger(value) {
  return Number.isInteger(value) && value >= 0;
}

function isTimestamp(value) {
  return typeof value === "string" && Number.isFinite(Date.parse(value));
}

function isSafeRepositoryPath(value) {
  if (!isNonEmptyString(value) || value.includes("\\") || value.startsWith("/") || /^[a-z]:/iu.test(value)) return false;
  const normalized = path.posix.normalize(value);
  return normalized === value && !normalized.startsWith("../") && normalized !== "..";
}

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value));
}

function canonicalValue(value) {
  if (Array.isArray(value)) return value.map(canonicalValue);
  if (!isObject(value)) return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, canonicalValue(value[key])]));
}

export function canonicalJson(value) {
  return JSON.stringify(canonicalValue(value));
}

function digestPayload(bundle) {
  const payload = cloneJson(bundle);
  if (isObject(payload.integrity)) delete payload.integrity.bundleDigest;
  delete payload.bundleDigest;
  return payload;
}

export function computeBundleDigest(bundle) {
  return `sha256:${createHash("sha256").update(canonicalJson(digestPayload(bundle)), "utf8").digest("hex")}`;
}

function bindingMatches(binding, bundle) {
  return isObject(binding)
    && binding.runId === bundle?.run?.id
    && binding.operationId === bundle?.run?.operationId
    && binding.sourceCommit === bundle?.releaseCandidate?.sourceCommit
    && binding.releaseCandidateDigest === bundle?.releaseCandidate?.releaseCandidateDigest;
}

function evidenceEnvelopeValid(value, bundle) {
  return isObject(value)
    && value.evidenceClass === "actual_product_path"
    && SHA256.test(String(value.evidenceDigest || ""))
    && bindingMatches(value.binding, bundle);
}

function unavailableStateValid(value) {
  return value?.state === "unavailable" && isNonEmptyString(value?.unavailableReason);
}

function providerUsageValid(usage) {
  if (!isObject(usage)) return false;
  const counters = ["initialCalls", "retryCalls", "totalCalls", "estimatedTokens", "actualTokens",
    "estimatedCostMicrounits", "actualCostMicrounits"];
  if (!counters.every((key) => isNonNegativeInteger(usage[key]))) return false;
  if (usage.totalCalls !== usage.initialCalls + usage.retryCalls) return false;
  if (!["observed", "unavailable", "not_applicable"].includes(usage.actualCostState)) return false;
  if (usage.actualCostState === "observed") return usage.unavailableReason === null;
  return isNonEmptyString(usage.unavailableReason);
}

function phaseValid(phase, bundle) {
  if (!evidenceEnvelopeValid(phase, bundle) || !isNonEmptyString(phase.name)
    || !PHASE_STATUSES.has(phase.status) || !isTimestamp(phase.startedAt)
    || !isTimestamp(phase.finishedAt) || !isNonNegativeInteger(phase.durationMs)
    || !isNonNegativeInteger(phase.attemptCount) || !providerUsageValid(phase.providerUsage)) return false;
  const elapsed = Date.parse(phase.finishedAt) - Date.parse(phase.startedAt);
  if (elapsed < 0 || Math.abs(elapsed - phase.durationMs) > 1) return false;
  if (phase.status === "skipped") return phase.attemptCount === 0 && isNonEmptyString(phase.reason);
  return phase.attemptCount >= 1;
}

function terminalEvidenceEnvelopes(bundle) {
  return [
    bundle?.terminalEvidence?.database,
    bundle?.terminalEvidence?.artifact,
    bundle?.terminalEvidence?.downloads?.redirect303,
    bundle?.terminalEvidence?.downloads?.range206,
    bundle?.terminalEvidence?.readback?.powerpoint,
    bundle?.terminalEvidence?.readback?.libreoffice,
  ];
}

function successTerminalEvidenceValid(bundle) {
  const terminal = bundle?.terminalEvidence;
  const database = terminal?.database;
  const artifact = terminal?.artifact;
  const redirect = terminal?.downloads?.redirect303;
  const range = terminal?.downloads?.range206;
  const powerpoint = terminal?.readback?.powerpoint;
  const libreoffice = terminal?.readback?.libreoffice;
  if (!terminalEvidenceEnvelopes(bundle).every((item) => evidenceEnvelopeValid(item, bundle))) return false;
  if (database.operationState !== "succeeded" || database.exportState !== "succeeded"
    || database.artifactState !== "published" || !isTimestamp(database.observedAt)
    || !SHA256.test(String(database.stateDigest || "")) || !SHA256.test(String(database.recordDigest || ""))) return false;
  if (artifact.state !== "available" || !isNonEmptyString(artifact.artifactId)
    || !SHA256.test(String(artifact.sha256 || "")) || !Number.isInteger(artifact.bytes) || artifact.bytes <= 0
    || artifact.mediaType !== "application/vnd.openxmlformats-officedocument.presentationml.presentation") return false;
  if (redirect.state !== "observed" || redirect.httpStatus !== 303
    || !SHA256.test(String(redirect.locationDigest || "")) || redirect.artifactSha256 !== artifact.sha256) return false;
  if (range.state !== "observed" || range.httpStatus !== 206 || !isNonEmptyString(range.contentRange)
    || !SHA256.test(String(range.responseSha256 || "")) || range.artifactSha256 !== artifact.sha256) return false;
  return [powerpoint, libreoffice].every((item) => item.state === "passed"
    && item.artifactSha256 === artifact.sha256 && SHA256.test(String(item.toolIdentityDigest || ""))
    && SHA256.test(String(item.readbackDigest || "")) && Number.isInteger(item.slideCount) && item.slideCount > 0);
}

function failedTerminalEvidenceValid(bundle) {
  const terminal = bundle?.terminalEvidence;
  const database = terminal?.database;
  if (!terminalEvidenceEnvelopes(bundle).every((item) => evidenceEnvelopeValid(item, bundle))) return false;
  if (!["succeeded", "failed"].includes(database?.operationState)
    || !["succeeded", "failed", "not_created"].includes(database?.exportState)
    || !["published", "not_published"].includes(database?.artifactState)
    || !isTimestamp(database?.observedAt) || !SHA256.test(String(database?.stateDigest || ""))
    || !SHA256.test(String(database?.recordDigest || ""))) return false;
  const artifact = terminal.artifact;
  if (artifact.state === "available") {
    if (!SHA256.test(String(artifact.sha256 || "")) || !Number.isInteger(artifact.bytes) || artifact.bytes <= 0) return false;
  } else if (!unavailableStateValid(artifact)) return false;
  for (const item of [terminal.downloads.redirect303, terminal.downloads.range206]) {
    if (item.state !== "observed" && !unavailableStateValid(item)) return false;
  }
  for (const item of [terminal.readback.powerpoint, terminal.readback.libreoffice]) {
    if (!["passed", "failed"].includes(item.state) && !unavailableStateValid(item)) return false;
  }
  return true;
}

function check(id, pass, reason) {
  return { id, pass: Boolean(pass), reason: pass ? "verified" : reason };
}

/**
 * Validate a sealed evidence bundle for release-quality success. A failed run can
 * be structurally well formed and retain telemetry, but `pass` remains false.
 */
export function validateImmutableBundle(bundle) {
  const checks = [];
  const actualEvidence = bundle?.evidenceClass === "actual_product_path"
    && bundle?.executionMode === "same_rc_product_path";
  checks.push(check("actual-product-path", actualEvidence,
    "fixture, plan, synthetic, or non-same-RC evidence is never release evidence"));

  const schemaValid = bundle?.schemaVersion === SCHEMA_VERSION && isNonEmptyString(bundle?.bundleId)
    && isObject(bundle?.run) && isNonEmptyString(bundle.run.id) && isNonEmptyString(bundle.run.operationId)
    && isTimestamp(bundle.run.startedAt) && isTimestamp(bundle.run.finishedAt)
    && ["succeeded", "failed"].includes(bundle.run.outcome)
    && ["ready", "not_ready"].includes(bundle.run.releaseReadiness)
    && bundle.run.deploymentAuthorized === false
    && isObject(bundle?.releaseCandidate) && COMMIT.test(String(bundle.releaseCandidate.sourceCommit || ""))
    && SHA256.test(String(bundle.releaseCandidate.releaseCandidateDigest || ""))
    && REQUIRED_COMPONENTS.every((key) => SHA256.test(String(bundle.releaseCandidate.components?.[key] || "")))
    && isObject(bundle?.provenance) && isSafeRepositoryPath(bundle.provenance.runnerPath)
    && SHA256.test(String(bundle.provenance.runnerSha256 || ""))
    && bundle.provenance.sourceManifestDigest === bundle.releaseCandidate.releaseCandidateDigest
    && evidenceEnvelopeValid(bundle?.operationEvidence, bundle)
    && ["api", "worker", "provider", "render", "export"].every((name) =>
      isNonEmptyString(bundle.operationEvidence[`${name}OperationId`])
        && SHA256.test(String(bundle.operationEvidence[`${name}Digest`] || "")));
  checks.push(check("schema-and-source-identity", schemaValid,
    "schema, run identity, producer provenance, source commit, deployment boundary, or component digests are invalid"));

  const integrityValid = bundle?.integrity?.canonicalization === CANONICALIZATION
    && bundle?.integrity?.immutable === true && isTimestamp(bundle?.integrity?.sealedAt)
    && SHA256.test(String(bundle?.integrity?.bundleDigest || ""))
    && bundle?.integrity?.bundleDigest === computeBundleDigest(bundle)
    && bundle?.bundleDigest === bundle.integrity.bundleDigest;
  checks.push(check("immutable-canonical-digest", integrityValid,
    "bundle digest is absent or does not match canonical content"));

  const phases = Array.isArray(bundle?.phases) ? bundle.phases : [];
  const uniquePhaseNames = new Set(phases.map((phase) => phase?.name)).size === phases.length;
  const telemetryValid = phases.length > 0 && uniquePhaseNames
    && phases.every((phase) => phaseValid(phase, bundle));
  checks.push(check("phase-timing-retry-provider-telemetry", telemetryValid,
    "phase timing, attempt count, provider usage, or same-run binding is incomplete"));

  const envelopes = [...phases, ...terminalEvidenceEnvelopes(bundle)];
  const sameRun = envelopes.length >= 7 && envelopes.every((item) => evidenceEnvelopeValid(item, bundle))
    && evidenceEnvelopeValid(bundle?.operationEvidence, bundle);
  checks.push(check("same-run-same-rc-binding", sameRun,
    "one or more evidence records are not bound to the bundle run and release candidate"));

  const succeeded = bundle?.run?.outcome === "succeeded" && bundle?.run?.releaseReadiness === "ready"
    && bundle?.failure === null && phases.every((phase) => phase.status === "succeeded");
  checks.push(check("release-outcome-ready", succeeded,
    "only a succeeded run with releaseReadiness=ready can pass"));

  const presentPhaseNames = new Set(phases.map((phase) => phase.name));
  const successPhasesComplete = REQUIRED_SUCCESS_PHASES.every((name) => presentPhaseNames.has(name));
  checks.push(check("successful-product-path-phases", successPhasesComplete,
    "successful release evidence must contain every required product-path phase"));

  const terminalValid = bundle?.run?.outcome === "succeeded"
    ? successTerminalEvidenceValid(bundle) : failedTerminalEvidenceValid(bundle);
  checks.push(check("terminal-artifact-download-readback", terminalValid,
    "DB terminal state, artifact, 303/206 download, or PowerPoint/LibreOffice readback evidence is incomplete"));

  const aliasesValid = canonicalJson(bundle?.dbTerminalState) === canonicalJson(bundle?.terminalEvidence?.database)
    && bundle?.artifactDigest === (bundle?.terminalEvidence?.artifact?.sha256 ?? null)
    && canonicalJson(bundle?.download303) === canonicalJson(bundle?.terminalEvidence?.downloads?.redirect303)
    && canonicalJson(bundle?.download206) === canonicalJson(bundle?.terminalEvidence?.downloads?.range206);
  checks.push(check("gate-compatible-terminal-aliases", aliasesValid,
    "dbTerminalState, artifactDigest, download303, or download206 drifted from canonical terminal evidence"));

  const failureShapeValid = bundle?.run?.outcome === "failed"
    ? isObject(bundle?.failure) && isNonEmptyString(bundle.failure.code)
      && SHA256.test(String(bundle.failure.messageDigest || "")) && isNonEmptyString(bundle.failure.phase)
    : bundle?.failure === null;
  checks.push(check("failure-retention", failureShapeValid,
    "failed runs require a digest-only failure record; succeeded runs require failure=null"));

  return { pass: checks.every((item) => item.pass), checks };
}

export function sealImmutableBundle(draft, { sealedAt = new Date().toISOString() } = {}) {
  const bundle = cloneJson(draft);
  bundle.dbTerminalState = cloneJson(bundle?.terminalEvidence?.database);
  bundle.artifactDigest = bundle?.terminalEvidence?.artifact?.sha256 ?? null;
  bundle.download303 = cloneJson(bundle?.terminalEvidence?.downloads?.redirect303);
  bundle.download206 = cloneJson(bundle?.terminalEvidence?.downloads?.range206);
  bundle.integrity = {
    ...(isObject(bundle.integrity) ? bundle.integrity : {}),
    canonicalization: CANONICALIZATION,
    immutable: true,
    sealedAt,
  };
  const priorDigest = bundle.integrity.bundleDigest;
  const priorTopLevelDigest = bundle.bundleDigest;
  delete bundle.integrity.bundleDigest;
  delete bundle.bundleDigest;
  const bundleDigest = computeBundleDigest(bundle);
  if ((priorDigest !== undefined && priorDigest !== bundleDigest)
    || (priorTopLevelDigest !== undefined && priorTopLevelDigest !== bundleDigest)) {
    throw new Error("refusing to reseal content whose existing bundleDigest does not match");
  }
  bundle.integrity.bundleDigest = bundleDigest;
  bundle.bundleDigest = bundleDigest;
  return bundle;
}

function argument(argv, name) {
  const index = argv.indexOf(name);
  return index >= 0 ? argv[index + 1] : null;
}

async function writeNewFile(outputPath, contents) {
  await mkdir(path.dirname(outputPath), { recursive: true });
  // Exclusive creation is intentional: sealed evidence is append-only and is
  // never replaced in place. A partial file cannot validate its bundle digest.
  await writeFile(outputPath, contents, { flag: "wx" });
}

async function main(argv = process.argv.slice(2)) {
  const inputArg = argument(argv, "--input");
  if (!inputArg) throw new Error("Usage: --input <bundle.json> [--output <new-sealed-bundle.json>]");
  const input = path.resolve(inputArg);
  const parsed = JSON.parse(await readFile(input, "utf8"));
  const outputArg = argument(argv, "--output");
  const bundle = outputArg ? sealImmutableBundle(parsed) : parsed;
  if (outputArg) await writeNewFile(path.resolve(outputArg), `${JSON.stringify(bundle, null, 2)}\n`);
  const result = validateImmutableBundle(bundle);
  process.stdout.write(`${JSON.stringify({ schemaVersion: SCHEMA_VERSION,
    bundleDigest: bundle?.integrity?.bundleDigest || null, ...result })}\n`);
  if (!result.pass) process.exitCode = 1;
}

if (path.resolve(process.argv[1] || "") === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`${error?.stack || error}\n`);
    process.exitCode = 1;
  });
}
