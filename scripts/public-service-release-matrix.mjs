#!/usr/bin/env node

import { createHash } from "node:crypto";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const MATRIX_PLAN_SCHEMA = "public-service-release-matrix-plan/v1";
export const MATRIX_EVIDENCE_SCHEMA = "public-service-release-matrix-evidence/v1";
export const ACTUAL_EVIDENCE_CLASS = "actual_product_path";

const SCRIPT_PATH = "scripts/public-service-release-matrix.mjs";
const SLIDE_COUNTS = Object.freeze([5, 10, 20, 30]);
const CONCURRENCY_LEVELS = Object.freeze([1, 2, 5, 10]);
const CACHE_STATES = Object.freeze(["cold", "warm"]);
const FAULT_TYPES = Object.freeze(["worker", "database", "s3", "provider"]);
const FAULT_REQUIRED_OBSERVATIONS = Object.freeze({
  worker: Object.freeze(["workerTerminationObserved", "checkpointResumeObserved"]),
  database: Object.freeze(["databaseUnavailableObserved", "outboxRecoveryObserved"]),
  s3: Object.freeze(["objectStoreUnavailableObserved", "artifactPublicationRecovered"]),
  provider: Object.freeze(["providerFailureObserved", "boundedRetryObserved"]),
});
const SHA256 = /^sha256:[0-9a-f]{64}$/u;
const COMMIT = /^[0-9a-f]{40}$/u;
const RELEASE_CANDIDATE = SHA256;

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function digestValue(value) {
  return `sha256:${createHash("sha256").update(canonicalJson(value)).digest("hex")}`;
}

function withoutDigest(value, field) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return value;
  const copy = { ...value };
  delete copy[field];
  delete copy._audit;
  return copy;
}

export function digestReleaseMatrixEvidence(report) {
  return digestValue(withoutDigest(report, "bundleDigest"));
}

function matrixCellId(cacheState, slideCount, concurrency) {
  return `${cacheState}-s${slideCount}-c${concurrency}`;
}

export function buildReleaseMatrixPlan({ releaseCandidate, sourceTreeCommit, repetitions = 1, generatedAt = null }) {
  if (!RELEASE_CANDIDATE.test(String(releaseCandidate ?? ""))) {
    throw new Error("releaseCandidate must be the exact source-manifest sha256:<64 lowercase hex>");
  }
  if (!COMMIT.test(String(sourceTreeCommit ?? ""))) {
    throw new Error("sourceTreeCommit must be a 40-character lowercase Git commit");
  }
  if (!Number.isInteger(repetitions) || repetitions < 1) throw new Error("repetitions must be an integer >= 1");

  const cells = [];
  for (const cacheState of CACHE_STATES) {
    for (const slideCount of SLIDE_COUNTS) {
      for (const concurrency of CONCURRENCY_LEVELS) {
        cells.push({
          cellId: matrixCellId(cacheState, slideCount, concurrency),
          cacheState,
          slideCount,
          concurrency,
          repetitions,
          minimumAttemptedOperations: concurrency * repetitions,
          executionRequired: true,
          acceptedEvidenceClass: ACTUAL_EVIDENCE_CLASS,
        });
      }
    }
  }
  const plan = {
    schemaVersion: MATRIX_PLAN_SCHEMA,
    releaseCandidate,
    sourceTreeCommit,
    generatedAt,
    executionState: "not-executed",
    releaseReadiness: "not-assessed",
    deploymentAuthorized: false,
    matrix: { repetitions, cells },
    faults: FAULT_TYPES.map((faultType) => ({
      faultId: `fault-${faultType}`,
      faultType,
      executionRequired: true,
      acceptedEvidenceClass: ACTUAL_EVIDENCE_CLASS,
      requiredOutcome: "fail-closed, bounded recovery, terminal-state convergence, no duplicate publication",
      requiredObservations: FAULT_REQUIRED_OBSERVATIONS[faultType],
    })),
    soak: {
      executionRequired: true,
      acceptedEvidenceClass: ACTUAL_EVIDENCE_CLASS,
      minimumDurationSeconds: 86_400,
      minimumSampleCount: 1_440,
      requiredOutcome: "no starvation, bounded resources, terminal-state convergence",
    },
    evidenceContract: {
      schemaVersion: MATRIX_EVIDENCE_SCHEMA,
      producerPath: SCRIPT_PATH,
      immutableBundleDigestRequired: true,
      sameReleaseCandidateRequired: true,
      sameSourceCommitRequired: true,
      operationFields: [
        "apiRequestId", "workerOperationId", "providerUsageDigest", "renderDigest",
        "exporterDigest", "fontDigest", "databaseTerminalState", "artifactDigest",
        "redirectStatus", "rangeStatus", "phaseTimingsDigest", "retryUsageDigest",
        "evidenceBundleDigest",
      ],
    },
  };
  return { ...plan, planDigest: digestValue(plan) };
}

function check(id, pass, expected, actual, reason = "criterion_not_met") {
  return {
    id,
    pass: Boolean(pass),
    expected,
    actual: actual === undefined ? null : actual,
    ...(pass ? {} : { reason }),
  };
}

function nonEmpty(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function operationChecks(operation, prefix, report) {
  const checks = [];
  checks.push(check(`${prefix}.evidenceClass`, operation?.evidenceClass === ACTUAL_EVIDENCE_CLASS,
    ACTUAL_EVIDENCE_CLASS, operation?.evidenceClass, "fixture_or_non_product_evidence"));
  checks.push(check(`${prefix}.releaseCandidate`, operation?.releaseCandidate === report?.releaseCandidate,
    report?.releaseCandidate, operation?.releaseCandidate, "release_candidate_mismatch"));
  checks.push(check(`${prefix}.sourceTreeCommit`, operation?.sourceTreeCommit === report?.sourceTreeCommit,
    report?.sourceTreeCommit, operation?.sourceTreeCommit, "source_commit_mismatch"));
  for (const field of ["apiRequestId", "workerOperationId"]) {
    checks.push(check(`${prefix}.${field}`, nonEmpty(operation?.[field]), "non-empty actual identifier",
      operation?.[field]));
  }
  for (const field of ["providerUsageDigest", "renderDigest", "exporterDigest", "fontDigest",
    "artifactDigest", "phaseTimingsDigest", "retryUsageDigest", "evidenceBundleDigest"]) {
    checks.push(check(`${prefix}.${field}`, SHA256.test(String(operation?.[field] ?? "")),
      "sha256:<64 lowercase hex>", operation?.[field]));
  }
  checks.push(check(`${prefix}.databaseTerminalState`, operation?.databaseTerminalState === "succeeded",
    "succeeded", operation?.databaseTerminalState));
  checks.push(check(`${prefix}.redirectStatus`, operation?.redirectStatus === 303, "303", operation?.redirectStatus));
  checks.push(check(`${prefix}.rangeStatus`, operation?.rangeStatus === 206, "206", operation?.rangeStatus));
  return checks;
}

function validateMatrixCells(report, checks) {
  const cells = Array.isArray(report?.matrix?.cells) ? report.matrix.cells : [];
  const repetitions = report?.matrix?.repetitions;
  checks.push(check("matrix.repetitions", Number.isInteger(repetitions) && repetitions >= 1,
    "integer >= 1", repetitions));
  const expectedIds = [];
  for (const cacheState of CACHE_STATES) {
    for (const slideCount of SLIDE_COUNTS) {
      for (const concurrency of CONCURRENCY_LEVELS) {
        const cellId = matrixCellId(cacheState, slideCount, concurrency);
        expectedIds.push(cellId);
        const matches = cells.filter((cell) => cell?.cellId === cellId);
        const prefix = `matrix.${cellId}`;
        checks.push(check(`${prefix}.unique`, matches.length === 1, "exactly one cell", matches.length,
          "matrix_cell_coverage_invalid"));
        if (matches.length !== 1) continue;
        const cell = matches[0];
        checks.push(check(`${prefix}.coordinates`, cell.cacheState === cacheState && cell.slideCount === slideCount
          && cell.concurrency === concurrency, { cacheState, slideCount, concurrency },
        { cacheState: cell.cacheState, slideCount: cell.slideCount, concurrency: cell.concurrency }));
        checks.push(check(`${prefix}.evidenceClass`, cell.evidenceClass === ACTUAL_EVIDENCE_CLASS,
          ACTUAL_EVIDENCE_CLASS, cell.evidenceClass, "fixture_or_non_product_evidence"));
        checks.push(check(`${prefix}.releaseCandidate`, cell.releaseCandidate === report.releaseCandidate,
          report.releaseCandidate, cell.releaseCandidate, "release_candidate_mismatch"));
        checks.push(check(`${prefix}.sourceTreeCommit`, cell.sourceTreeCommit === report.sourceTreeCommit,
          report.sourceTreeCommit, cell.sourceTreeCommit, "source_commit_mismatch"));
        checks.push(check(`${prefix}.cacheControlObserved`, cell.cacheControlObserved === true, true,
          cell.cacheControlObserved));
        const minimumAttempted = Number.isInteger(repetitions) ? concurrency * repetitions : null;
        checks.push(check(`${prefix}.attempted`, Number.isInteger(cell.attempted)
          && minimumAttempted !== null && cell.attempted >= minimumAttempted,
        minimumAttempted === null ? "valid repetitions required" : `integer >= ${minimumAttempted}`, cell.attempted));
        checks.push(check(`${prefix}.succeeded`, Number.isInteger(cell.succeeded)
          && cell.succeeded === cell.attempted, "equals attempted", cell.succeeded));
        checks.push(check(`${prefix}.failed`, cell.failed === 0, 0, cell.failed));
        const operations = Array.isArray(cell.operationEvidence) ? cell.operationEvidence : [];
        checks.push(check(`${prefix}.operationEvidenceCount`, operations.length === cell.attempted,
          "one immutable evidence reference per attempted operation", operations.length));
        operations.forEach((operation, index) => checks.push(...operationChecks(operation,
          `${prefix}.operation.${index}`, report)));
      }
    }
  }
  checks.push(check("matrix.exactCellSet", cells.length === expectedIds.length
    && new Set(cells.map((cell) => cell?.cellId)).size === expectedIds.length,
  `${expectedIds.length} unique required cells`, cells.map((cell) => cell?.cellId), "matrix_cell_set_invalid"));
}

function validateFaults(report, checks) {
  const faults = Array.isArray(report?.faults) ? report.faults : [];
  for (const faultType of FAULT_TYPES) {
    const matches = faults.filter((fault) => fault?.faultType === faultType);
    const prefix = `fault.${faultType}`;
    checks.push(check(`${prefix}.unique`, matches.length === 1, "exactly one actual fault observation",
      matches.length, "fault_coverage_invalid"));
    if (matches.length !== 1) continue;
    const fault = matches[0];
    checks.push(check(`${prefix}.evidenceClass`, fault.evidenceClass === ACTUAL_EVIDENCE_CLASS,
      ACTUAL_EVIDENCE_CLASS, fault.evidenceClass, "fixture_or_injected_unit_evidence"));
    checks.push(check(`${prefix}.releaseCandidate`, fault.releaseCandidate === report.releaseCandidate,
      report.releaseCandidate, fault.releaseCandidate, "release_candidate_mismatch"));
    checks.push(check(`${prefix}.sourceTreeCommit`, fault.sourceTreeCommit === report.sourceTreeCommit,
      report.sourceTreeCommit, fault.sourceTreeCommit, "source_commit_mismatch"));
    checks.push(check(`${prefix}.actualFaultObserved`, fault.actualFaultObserved === true, true,
      fault.actualFaultObserved, "planned_or_simulated_fault_is_not_evidence"));
    checks.push(check(`${prefix}.injectionReceiptDigest`, SHA256.test(String(fault.injectionReceiptDigest ?? "")),
      "sha256:<64 lowercase hex>", fault.injectionReceiptDigest));
    checks.push(check(`${prefix}.failClosed`, fault.failClosed === true, true, fault.failClosed));
    checks.push(check(`${prefix}.recovered`, fault.recovered === true, true, fault.recovered));
    checks.push(check(`${prefix}.terminalStateConverged`, fault.terminalStateConverged === true, true,
      fault.terminalStateConverged));
    checks.push(check(`${prefix}.duplicatePublicationCount`, fault.duplicatePublicationCount === 0, 0,
      fault.duplicatePublicationCount));
    checks.push(check(`${prefix}.staleWriteAcceptedCount`, fault.staleWriteAcceptedCount === 0, 0,
      fault.staleWriteAcceptedCount));
    checks.push(check(`${prefix}.phaseTimingsPreserved`, fault.phaseTimingsPreserved === true, true,
      fault.phaseTimingsPreserved));
    checks.push(check(`${prefix}.retryProviderUsagePreserved`, fault.retryProviderUsagePreserved === true, true,
      fault.retryProviderUsagePreserved));
    for (const field of FAULT_REQUIRED_OBSERVATIONS[faultType]) {
      checks.push(check(`${prefix}.${field}`, fault[field] === true, true, fault[field],
        "fault_specific_actual_observation_missing"));
    }
    checks.push(...operationChecks(fault.recoveryOperationEvidence, `${prefix}.recoveryOperation`, report));
  }
  checks.push(check("fault.exactSet", faults.length === FAULT_TYPES.length
    && new Set(faults.map((fault) => fault?.faultType)).size === FAULT_TYPES.length,
  [...FAULT_TYPES], faults.map((fault) => fault?.faultType), "fault_set_invalid"));
}

function validateSoak(report, checks) {
  const soak = report?.soak;
  checks.push(check("soak.evidenceClass", soak?.evidenceClass === ACTUAL_EVIDENCE_CLASS,
    ACTUAL_EVIDENCE_CLASS, soak?.evidenceClass, "fixture_or_short_soak_is_not_evidence"));
  checks.push(check("soak.releaseCandidate", soak?.releaseCandidate === report?.releaseCandidate,
    report?.releaseCandidate, soak?.releaseCandidate, "release_candidate_mismatch"));
  checks.push(check("soak.sourceTreeCommit", soak?.sourceTreeCommit === report?.sourceTreeCommit,
    report?.sourceTreeCommit, soak?.sourceTreeCommit, "source_commit_mismatch"));
  const started = Date.parse(soak?.startedAt ?? "");
  const ended = Date.parse(soak?.endedAt ?? "");
  const observedDuration = Number.isFinite(started) && Number.isFinite(ended) ? (ended - started) / 1000 : null;
  checks.push(check("soak.wallClockDuration", observedDuration !== null && observedDuration >= 86_400,
    ">= 86400 seconds from timestamps", observedDuration));
  checks.push(check("soak.reportedDuration", Number.isFinite(soak?.durationSeconds)
    && soak.durationSeconds >= 86_400 && soak.durationSeconds <= observedDuration,
  ">= 86400 and <= observed wall clock", soak?.durationSeconds));
  checks.push(check("soak.sampleCount", Number.isInteger(soak?.sampleCount) && soak.sampleCount >= 1_440,
    ">= 1440", soak?.sampleCount));
  checks.push(check("soak.attemptedOperations", Number.isInteger(soak?.attemptedOperations)
    && soak.attemptedOperations > 0, "integer > 0", soak?.attemptedOperations));
  checks.push(check("soak.succeededOperations", Number.isInteger(soak?.succeededOperations)
    && soak.succeededOperations === soak.attemptedOperations, "equals attemptedOperations",
  soak?.succeededOperations));
  const operationDigests = Array.isArray(soak?.operationEvidenceDigests) ? soak.operationEvidenceDigests : [];
  checks.push(check("soak.operationEvidenceDigests", operationDigests.length === soak?.attemptedOperations
    && operationDigests.every((digest) => SHA256.test(String(digest))),
  "one immutable SHA-256 evidence digest per attempted operation", operationDigests.length));
  checks.push(check("soak.queueStarvationCount", soak?.queueStarvationCount === 0, 0,
    soak?.queueStarvationCount));
  checks.push(check("soak.resourceLimitsEnforced", soak?.resourceLimitsEnforced === true, true,
    soak?.resourceLimitsEnforced));
  checks.push(check("soak.resourcesRecoveredWithinBaseline", soak?.resourcesRecoveredWithinBaseline === true,
    true, soak?.resourcesRecoveredWithinBaseline));
  checks.push(check("soak.terminalStateConverged", soak?.terminalStateConverged === true, true,
    soak?.terminalStateConverged));
}

function validateIndependentEvidenceIdentities(report, checks) {
  const matrixOperations = (Array.isArray(report?.matrix?.cells) ? report.matrix.cells : [])
    .flatMap((cell) => Array.isArray(cell?.operationEvidence) ? cell.operationEvidence : []);
  const faultOperations = (Array.isArray(report?.faults) ? report.faults : [])
    .map((fault) => fault?.recoveryOperationEvidence).filter(Boolean);
  const operations = [...matrixOperations, ...faultOperations];
  for (const field of ["apiRequestId", "workerOperationId", "evidenceBundleDigest"]) {
    const values = operations.map((operation) => operation?.[field]);
    checks.push(check(`independentIdentity.${field}`, values.length > 0
      && values.every((value) => nonEmpty(value)) && new Set(values).size === values.length,
    "one unique value per matrix/fault operation", values.length === 0 ? 0 : new Set(values).size,
    "reused_or_missing_operation_evidence_identity"));
  }
  const soakDigests = Array.isArray(report?.soak?.operationEvidenceDigests)
    ? report.soak.operationEvidenceDigests : [];
  const allBundleDigests = [...operations.map((operation) => operation?.evidenceBundleDigest), ...soakDigests];
  checks.push(check("independentIdentity.allEvidenceBundleDigests", allBundleDigests.length > 0
    && allBundleDigests.every((value) => SHA256.test(String(value)))
    && new Set(allBundleDigests).size === allBundleDigests.length,
  "unique immutable bundle digest for every matrix, fault, and soak operation",
  { total: allBundleDigests.length, unique: new Set(allBundleDigests).size },
  "reused_or_missing_immutable_bundle"));
}

/**
 * Read-only, fail-closed audit. The result is derived from immutable actual-product-path
 * observations; it never grants deployment authority.
 */
export function validateReleaseMatrixEvidence(report) {
  const checks = [];
  checks.push(check("schemaVersion", report?.schemaVersion === MATRIX_EVIDENCE_SCHEMA,
    MATRIX_EVIDENCE_SCHEMA, report?.schemaVersion));
  checks.push(check("evidenceClass", report?.evidenceClass === ACTUAL_EVIDENCE_CLASS,
    ACTUAL_EVIDENCE_CLASS, report?.evidenceClass, "fixture_plan_or_simulation_not_accepted"));
  checks.push(check("releaseCandidate", RELEASE_CANDIDATE.test(String(report?.releaseCandidate ?? "")),
    "exact source-manifest sha256:<64 lowercase hex>", report?.releaseCandidate));
  checks.push(check("sourceTreeCommit", COMMIT.test(String(report?.sourceTreeCommit ?? "")),
    "40-character lowercase Git commit", report?.sourceTreeCommit));
  checks.push(check("planDigest", SHA256.test(String(report?.planDigest ?? "")),
    "sha256:<64 lowercase hex>", report?.planDigest));
  checks.push(check("producerPath", report?.provenance?.runnerPath === SCRIPT_PATH,
    SCRIPT_PATH, report?.provenance?.runnerPath));
  checks.push(check("producerDigest", SHA256.test(String(report?.provenance?.runnerSha256 ?? "")),
    "sha256:<64 lowercase hex>", report?.provenance?.runnerSha256));
  checks.push(check("sourceManifestDigest", report?.provenance?.sourceManifestDigest === report?.releaseCandidate,
    "equals releaseCandidate", report?.provenance?.sourceManifestDigest, "source_manifest_binding_mismatch"));
  checks.push(check("deploymentAuthorityInput", report?.deploymentAuthorized !== true,
    "not true; deployment requires a separate approval", report?.deploymentAuthorized,
    "deployment_authority_cannot_be_granted_by_evidence"));

  validateMatrixCells(report, checks);
  validateFaults(report, checks);
  validateSoak(report, checks);
  validateIndependentEvidenceIdentities(report, checks);

  const expectedDigest = digestReleaseMatrixEvidence(report);
  checks.push(check("immutableBundleDigest", report?.bundleDigest === expectedDigest,
    expectedDigest, report?.bundleDigest, "evidence_bundle_digest_mismatch"));
  const pass = checks.every((item) => item.pass);
  return {
    schemaVersion: "public-service-release-matrix-audit/v1",
    pass,
    status: pass ? "PASS" : "FAIL",
    releaseReadiness: pass ? "ready" : "blocked",
    deploymentAuthorized: false,
    checks,
  };
}

function parseArgs(argv) {
  const [command, ...rest] = argv;
  const options = {};
  for (let index = 0; index < rest.length; index += 1) {
    const key = rest[index];
    if (!key.startsWith("--")) throw new Error(`unexpected argument: ${key}`);
    const value = rest[index + 1];
    if (value === undefined || value.startsWith("--")) throw new Error(`missing value for ${key}`);
    options[key.slice(2)] = value;
    index += 1;
  }
  return { command, options };
}

async function writeJsonAtomic(outputPath, value) {
  const target = path.resolve(outputPath);
  await mkdir(path.dirname(target), { recursive: true });
  const temporary = `${target}.${process.pid}.tmp`;
  await writeFile(temporary, `${JSON.stringify(value, null, 2)}\n`, "utf8");
  await rename(temporary, target);
}

async function main(argv = process.argv.slice(2)) {
  const { command, options } = parseArgs(argv);
  let result;
  if (command === "plan") {
    result = buildReleaseMatrixPlan({
      releaseCandidate: options["release-candidate"],
      sourceTreeCommit: options["source-commit"],
      repetitions: options.repetitions === undefined ? 1 : Number(options.repetitions),
      generatedAt: new Date().toISOString(),
    });
  } else if (command === "audit") {
    if (!options.input) throw new Error("audit requires --input <actual-evidence.json>");
    result = validateReleaseMatrixEvidence(JSON.parse(await readFile(path.resolve(options.input), "utf8")));
    if (!result.pass) process.exitCode = 2;
  } else {
    throw new Error("Usage: public-service-release-matrix.mjs plan --release-candidate <id> --source-commit <sha> [--repetitions N] [--output file] | audit --input <actual-evidence.json> [--output file]");
  }
  if (options.output) await writeJsonAtomic(options.output, result);
  else process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}

if (path.resolve(process.argv[1] ?? "") === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`PUBLIC_SERVICE_RELEASE_MATRIX_BLOCKED: ${error.message}\n`);
    process.exitCode = 2;
  });
}
