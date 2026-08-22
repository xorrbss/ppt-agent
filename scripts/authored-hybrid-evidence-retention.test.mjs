import assert from "node:assert/strict";
import test from "node:test";

import {
  retentionDecision,
  sha256Canonical,
  validateAtomicEvidenceBundle,
  validateRetentionPolicy,
} from "./authored-hybrid-evidence-retention.mjs";

const digest = (seed) => seed.repeat(64).slice(0, 64);

function bundle() {
  const value = {
    schemaVersion: "presenton.authored-hybrid-atomic-evidence/v1",
    createdAt: "2026-08-22T00:00:00.000Z",
    attempt: { id: "attempt-1", presentationId: "presentation-1", presentationRevision: 7, format: "pptx" },
    source: { canonicalSourceSha256: digest("a"), rendererSourceSha256: digest("b") },
    artifact: { pptxSha256: digest("c"), pptxSizeBytes: 2048, coverageManifestSha256: digest("d") },
    render: { manifestSha256: digest("e"), slidePngSha256: [digest("f")] },
    report: { manifestSha256: digest("1"), visualParity: "not-verified" },
  };
  return { ...value, evidenceDigest: sha256Canonical(value) };
}

test("expired evidence remains metadata-only instead of authorizing deletion", () => {
  const evidence = validateAtomicEvidenceBundle(bundle());
  const policy = validateRetentionPolicy({
    schemaVersion: "presenton.authored-hybrid-evidence-retention/v1",
    attemptRetentionDays: 180,
    evidenceRetentionDays: 30,
    expiryAction: "mark_expired_retain_metadata",
    deletionAuthorized: false,
    ciArtifactRetentionDays: 30,
  });
  assert.deepEqual(retentionDecision(evidence, policy, new Date("2026-09-22T00:00:00.000Z")), {
    status: "expired",
    expiresAt: "2026-09-21T00:00:00.000Z",
    expiryAction: "mark_expired_retain_metadata",
    deletionAuthorized: false,
  });
});
