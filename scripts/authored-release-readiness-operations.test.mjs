import assert from "node:assert/strict";
import test from "node:test";

import { evaluateEvidence } from "./authored-release-readiness-operations.mjs";
import {
  verifyConfigOnly,
  verifyLive,
} from "./verify-authored-release-required-checks.mjs";

const readyEvidence = {
  schemaVersion: "authored-release-readiness-evidence/v1",
  migration: {
    additive: true,
    backupVerified: true,
    rollbackRehearsed: true,
  },
  rollback: {
    killSwitchVerified: true,
    activeJobsDrained: true,
  },
  alerts: { armed: true, active: false },
  requiredChecks: { verified: true },
  rendererAttestation: { state: "attested" },
};

test("repository pipeline contains every configured authored release gate", async () => {
  const result = await verifyConfigOnly();
  assert.equal(result.status, "configured");
});

test("live merge-check verification requires every protected branch", async () => {
  const missing = async () => ({
    ok: true,
    json: async () => ({ values: [] }),
  });
  await assert.rejects(
    verifyLive(missing, { BITBUCKET_API_TOKEN: "redacted-test-token" }),
    /missing for: main/,
  );

  const present = async () => ({
    ok: true,
    json: async () => ({
      values: [{ kind: "require_passing_builds_to_merge", pattern: "main" }],
    }),
  });
  const result = await verifyLive(present, {
    BITBUCKET_API_TOKEN: "redacted-test-token",
  });
  assert.equal(result.status, "verified");
});

test("operational evidence stays non-promotable and fails closed", () => {
  assert.deepEqual(evaluateEvidence("migration-preflight", readyEvidence), {
    status: "ready-for-approved-operation",
    operation: "migration-preflight",
    promotionReady: false,
  });
  assert.equal(
    evaluateEvidence("rollback-drain", {
      ...readyEvidence,
      rollback: { ...readyEvidence.rollback, activeJobsDrained: false },
    }).reasonCode,
    "rollback_drain_incomplete",
  );
});

