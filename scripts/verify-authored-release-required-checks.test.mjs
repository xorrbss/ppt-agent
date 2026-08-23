import assert from "node:assert/strict";
import { createHmac } from "node:crypto";
import test from "node:test";

import { canonicalJson, canonicalSha256 } from "./authored-bounded-release-contracts.mjs";
import { verifyConfigOnly, verifyLive, verifyLiveReceipt } from "./verify-authored-release-required-checks.mjs";

const commit = "a".repeat(40);
const evidenceKey = "test-only-ci-evidence-key-32-bytes-minimum";
const ok = (body) => ({ ok: true, status: 200, json: async () => body });
const attemptBinding = {
  schemaVersion: "authored-ci-attempt-binding/v3",
  presentationId: "00000000-0000-4000-8000-000000000001",
  presentationRevision: 7,
  generationAttemptId: "00000000-0000-4000-8000-000000000002",
  fidelityExportAttemptId: "00000000-0000-4000-8000-000000000003",
  hybridExportAttemptId: "00000000-0000-4000-8000-000000000004",
  activeManifestDigest: `sha256:${"1".repeat(64)}`,
  sourceTreeCensusDigest: `sha256:${"2".repeat(64)}`,
  sourceHeadCommit: commit,
  evidencePolicyDigest: `sha256:${"3".repeat(64)}`,
  executionNonce: "attempt_nonce_0000000000000001",
};

function liveEnvironment(overrides = {}) {
  return {
    BITBUCKET_API_TOKEN: "redacted",
    BITBUCKET_COMMIT: commit,
    AUTHORED_RELEASE_CI_EVIDENCE_HMAC_KEY: evidenceKey,
    AUTHORED_RELEASE_CI_ATTEMPT_BINDING_JSON: JSON.stringify(attemptBinding),
    ...overrides,
  };
}

function resign(receipt) {
  const value = structuredClone(receipt);
  delete value.receiptDigest;
  delete value.authoritySignature;
  const digest = canonicalSha256(value);
  return {
    ...value,
    receiptDigest: digest,
    authoritySignature: `hmac-sha256:${createHmac("sha256", evidenceKey).update(digest, "utf8").digest("hex")}`,
  };
}

function fetchFixture(url) {
  if (url.endsWith("/branch-restrictions")) {
    return ok({ values: [], next: "https://fixture.test/restrictions-page-2" });
  }
  if (url.endsWith("restrictions-page-2")) {
    return ok({ values: [{ kind: "require_passing_builds_to_merge", pattern: "main", value: 1 }] });
  }
  if (url.includes("/pipelines/?")) {
    return ok({ values: [{ uuid: "{pipeline-1}", target: { commit: { hash: commit } }, state: { result: { name: "SUCCESSFUL" } } }] });
  }
  if (url.includes(`/commit/${commit}/statuses`)) {
    return ok({ values: [{ key: "PIPELINE", state: "SUCCESSFUL" }] });
  }
  if (url.includes("/pipelines/%7Bpipeline-1%7D/steps/")) {
    return ok({ values: [
      "Authored release readiness configuration",
      "Dashboard component gate",
      "Run shared Next.js CI Node suite",
      "Upstream byte-parity verification",
      "Workspace chat-first browser E2E",
    ].map((name) => ({ name, state: { result: { name: "SUCCESSFUL" } } })) });
  }
  throw new Error(`unexpected URL ${url}`);
}

test("Bitbucket required-check verifier validates configuration and sealed live receipt", async () => {
  const now = new Date("2026-08-23T20:00:00.987Z");
  assert.equal((await verifyConfigOnly()).status, "configured");
  const result = await verifyLive(fetchFixture, liveEnvironment(), now);
  assert.equal(result.status, "verified");
  assert.match(result.receiptDigest, /^sha256:[a-f0-9]{64}$/);
  assert.match(result.authoritySignature, /^hmac-sha256:[a-f0-9]{64}$/);
  assert.equal(result.pipelineUuid, "{pipeline-1}");
  assert.equal(result.verifiedAt, "2026-08-23T20:00:00Z");
  assert.equal(result.expiresAt, "2026-08-23T20:15:00Z");
  assert.deepEqual(result.requiredPipelineSteps, [
    "Authored release readiness configuration",
    "Dashboard component gate",
    "Run shared Next.js CI Node suite",
    "Upstream byte-parity verification",
    "Workspace chat-first browser E2E",
  ]);
  assert.deepEqual(result.successfulStepNames, result.requiredPipelineSteps);
  assert.equal(result.paginationComplete, true);
  assert.equal(result.latestStatePerStepSuccessful, true);
  assert.deepEqual(result.attemptBinding, attemptBinding);
  assert.equal(await verifyLiveReceipt(result, liveEnvironment(), new Date("2026-08-23T20:14:59.999Z")), true);
});

test("Bitbucket verifier rejects missing authentication and a zero merge-check value", async () => {
  await assert.rejects(() => verifyLive(fetchFixture, { BITBUCKET_COMMIT: commit }), /authentication is required/);
  await assert.rejects(
    () => verifyLive(async (url) => url.endsWith("/branch-restrictions") ? ok({ values: [{ kind: "require_passing_builds_to_merge", pattern: "main", value: 0 }] }) : fetchFixture(url), {
      ...liveEnvironment(),
    }),
    /passing-build merge check is missing/,
  );
});

test("Bitbucket verifier rejects stale pipeline and commit-status success", async () => {
  const environment = liveEnvironment();
  await assert.rejects(
    () => verifyLive(async (url) => {
      if (url.includes("/pipelines/?")) return ok({ values: [
        { uuid: "{old-success}", created_on: "2026-08-20T00:00:00Z", target: { commit: { hash: commit } }, state: { result: { name: "SUCCESSFUL" } } },
        { uuid: "{new-failure}", created_on: "2026-08-21T00:00:00Z", target: { commit: { hash: commit } }, state: { result: { name: "FAILED" } } },
      ] });
      return fetchFixture(url);
    }, environment),
    /latest Bitbucket pipeline.*not successful/,
  );
  await assert.rejects(
    () => verifyLive(async (url) => {
      if (url.includes(`/commit/${commit}/statuses`)) return ok({ values: [
        { key: "PIPELINE", created_on: "2026-08-20T00:00:00Z", state: "SUCCESSFUL" },
        { key: "PIPELINE", created_on: "2026-08-21T00:00:00Z", state: "FAILED" },
      ] });
      return fetchFixture(url);
    }, environment),
    /no successful commit status/,
  );
});

test("required step authority uses only the latest entry for each exact name", async () => {
  const now = new Date("2026-08-23T20:00:00.000Z");
  const stepUrl = "/pipelines/%7Bpipeline-1%7D/steps/";
  const replaceDashboard = (latestState) => async (url) => {
    if (url.includes(stepUrl)) {
      const ordinary = [
        "Authored release readiness configuration",
        "Run shared Next.js CI Node suite",
        "Upstream byte-parity verification",
        "Workspace chat-first browser E2E",
      ].map((name) => ({ name, updated_on: "2026-08-23T19:59:00Z", state: { result: { name: "SUCCESSFUL" } } }));
      return ok({ values: [
        ...ordinary,
        { name: "Dashboard component gate", updated_on: "2026-08-23T19:58:00Z", state: { result: { name: latestState === "FAILED" ? "SUCCESSFUL" : "FAILED" } } },
        { name: "Dashboard component gate", updated_on: "2026-08-23T19:59:00Z", state: { result: { name: latestState } } },
      ] });
    }
    return fetchFixture(url);
  };

  await assert.rejects(
    () => verifyLive(replaceDashboard("FAILED"), liveEnvironment(), now),
    /missing successful required steps: Dashboard component gate/,
  );
  const accepted = await verifyLive(replaceDashboard("SUCCESSFUL"), liveEnvironment(), now);
  assert.equal(accepted.latestStatePerStepSuccessful, true);
  assert.deepEqual(accepted.successfulStepNames, accepted.requiredPipelineSteps);
});

test("receipt digest uses canonical JSON v2 NFC and LF normalization", async () => {
  const now = new Date("2026-08-23T20:00:00.000Z");
  const decomposedStatusKey = "RELEASE-e\u0301\r\nSTATUS";
  const fetchWithUnicodeStatus = async (url) => {
    if (url.includes(`/commit/${commit}/statuses`)) {
      return ok({ values: [{ key: decomposedStatusKey, state: "SUCCESSFUL" }] });
    }
    return fetchFixture(url);
  };
  const receipt = await verifyLive(fetchWithUnicodeStatus, liveEnvironment(), now);
  const unsigned = structuredClone(receipt);
  delete unsigned.receiptDigest;
  delete unsigned.authoritySignature;
  const canonical = canonicalJson(unsigned);
  assert.equal(canonical.includes("\\r"), false);
  assert.equal(canonical.includes("RELEASE-é\\nSTATUS"), true);
  assert.equal(receipt.receiptDigest, canonicalSha256(unsigned));
  const pythonActualProductCanonicalV2Digest = "sha256:f5169d1b850c350e1a48f2ec42ec0b6230a351e0b8f0d4c062379e9cc7d0e965";
  assert.equal(receipt.receiptDigest, pythonActualProductCanonicalV2Digest);
});

test("live receipt rejects stale, relabeled, and overlong TTL authority", async () => {
  const verifiedAt = new Date("2026-08-23T20:00:00.000Z");
  const environment = liveEnvironment();
  const receipt = await verifyLive(fetchFixture, environment, verifiedAt);

  await assert.rejects(
    () => verifyLiveReceipt(receipt, environment, new Date(receipt.expiresAt)),
    /stale or has an invalid TTL/,
  );

  const relabeledCommit = resign({ ...receipt, commit: "b".repeat(40) });
  assert.notEqual(relabeledCommit.receiptDigest, receipt.receiptDigest);
  await assert.rejects(
    () => verifyLiveReceipt(relabeledCommit, environment, verifiedAt),
    /authority scope is invalid/,
  );

  const relabeledSteps = resign({
    ...receipt,
    requiredPipelineSteps: [...receipt.requiredPipelineSteps.slice(0, -1), "Relabeled required step"].sort(),
  });
  assert.notEqual(relabeledSteps.receiptDigest, receipt.receiptDigest);
  await assert.rejects(
    () => verifyLiveReceipt(relabeledSteps, environment, verifiedAt),
    /authority scope is invalid/,
  );

  const overlongTtl = resign({
    ...receipt,
    expiresAt: new Date(verifiedAt.getTime() + 15 * 60 * 1000 + 1).toISOString(),
  });
  await assert.rejects(
    () => verifyLiveReceipt(overlongTtl, environment, verifiedAt),
    /stale or has an invalid TTL/,
  );

  const relabeledPagination = resign({ ...receipt, paginationComplete: false });
  assert.notEqual(relabeledPagination.receiptDigest, receipt.receiptDigest);
  await assert.rejects(
    () => verifyLiveReceipt(relabeledPagination, environment, verifiedAt),
    /authority scope is invalid/,
  );

  for (const [field, fractionalValue] of [
    ["verifiedAt", "2026-08-23T20:00:00.000Z"],
    ["verifiedAt", "2026-08-23T20:00:00.123Z"],
    ["expiresAt", "2026-08-23T20:15:00.000Z"],
    ["expiresAt", "2026-08-23T20:15:00.123Z"],
  ]) {
    const fractionalTimestamp = resign({ ...receipt, [field]: fractionalValue });
    await assert.rejects(
      () => verifyLiveReceipt(fractionalTimestamp, environment, verifiedAt),
      /stale or has an invalid TTL/,
    );
  }
});

test("live receipt rejects attempt relabel and attempt HEAD mismatch", async () => {
  const now = new Date("2026-08-23T20:00:00.000Z");
  const environment = liveEnvironment();
  const receipt = await verifyLive(fetchFixture, environment, now);
  const relabeledBinding = {
    ...attemptBinding,
    generationAttemptId: "00000000-0000-4000-8000-000000000099",
  };
  const relabeledReceipt = resign({ ...receipt, attemptBinding: relabeledBinding });
  assert.notEqual(relabeledReceipt.receiptDigest, receipt.receiptDigest);
  await assert.rejects(
    () => verifyLiveReceipt(relabeledReceipt, environment, now),
    /authority scope is invalid/,
  );
  await assert.rejects(
    () => verifyLive(fetchFixture, liveEnvironment({
      AUTHORED_RELEASE_CI_ATTEMPT_BINDING_JSON: JSON.stringify({
        ...attemptBinding,
        sourceHeadCommit: "b".repeat(40),
      }),
    }), now),
    /attempt binding does not match current commit/,
  );
  await assert.rejects(
    () => verifyLive(fetchFixture, liveEnvironment({
      AUTHORED_RELEASE_CI_ATTEMPT_BINDING_JSON: JSON.stringify({
        ...attemptBinding,
        unexpectedRelabel: "forbidden",
      }),
    }), now),
    /attempt binding schema is not exact/,
  );
});
