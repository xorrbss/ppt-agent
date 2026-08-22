#!/usr/bin/env node

/** Read-only operational evidence gate. It never migrates, flips a flag, or
 * acknowledges an alert; those remain approved deployment actions. */
import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

function blocked(reasonCode) {
  return { status: "blocked", reasonCode, promotionReady: false };
}

export function evaluateEvidence(operation, evidence) {
  if (!evidence || evidence.schemaVersion !== "authored-release-readiness-evidence/v1") {
    return blocked("release_readiness_evidence_invalid");
  }
  if (operation === "migration-preflight") {
    if (evidence.migration?.additive !== true) return blocked("migration_not_additive");
    if (evidence.migration?.backupVerified !== true) return blocked("migration_backup_unverified");
    if (evidence.migration?.rollbackRehearsed !== true) return blocked("migration_rollback_unrehearsed");
  } else if (operation === "rollback-drain") {
    if (evidence.rollback?.killSwitchVerified !== true) return blocked("rollback_kill_switch_unverified");
    if (evidence.rollback?.activeJobsDrained !== true) return blocked("rollback_drain_incomplete");
  } else if (operation === "alert-evaluate") {
    if (evidence.alerts?.armed !== true) return blocked("release_alerts_unarmed");
    if (evidence.alerts?.active === true) return blocked("release_alert_active");
  } else {
    return blocked("release_readiness_operation_unknown");
  }
  if (evidence.requiredChecks?.verified !== true) return blocked("bitbucket_required_checks_unverified");
  if (evidence.rendererAttestation?.state !== "attested") return blocked("renderer_attestation_unverified");
  return { status: "ready-for-approved-operation", operation, promotionReady: false };
}

async function main(argv = process.argv.slice(2)) {
  const [operation, evidencePath] = argv;
  if (!operation || !evidencePath) throw new Error("Usage: authored-release-readiness-operations.mjs <migration-preflight|rollback-drain|alert-evaluate> <redacted-evidence.json>");
  const evidence = JSON.parse(await readFile(path.resolve(evidencePath), "utf8"));
  const result = evaluateEvidence(operation, evidence);
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  if (result.status !== "ready-for-approved-operation") process.exitCode = 2;
}

if (path.resolve(process.argv[1] ?? "") === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`AUTHORED_RELEASE_READINESS_BLOCKED: ${error.message}\n`);
    process.exitCode = 2;
  });
}
