import assert from "node:assert/strict";
import test from "node:test";

import { verifyConfigOnly, verifyLive } from "./verify-authored-release-required-checks.mjs";

const commit = "a".repeat(40);
const ok = (body) => ({ ok: true, status: 200, json: async () => body });

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
      "Run shared Next.js CI Node suite",
      "Workspace chat-first browser E2E",
    ].map((name) => ({ name, state: { result: { name: "SUCCESSFUL" } } })) });
  }
  throw new Error(`unexpected URL ${url}`);
}

test("Bitbucket required-check verifier validates configuration and sealed live receipt", async () => {
  assert.equal((await verifyConfigOnly()).status, "configured");
  const result = await verifyLive(fetchFixture, {
    BITBUCKET_API_TOKEN: "redacted",
    BITBUCKET_COMMIT: commit,
  });
  assert.equal(result.status, "verified");
  assert.match(result.receiptDigest, /^sha256:[a-f0-9]{64}$/);
  assert.equal(result.pipelineUuid, "{pipeline-1}");
});

test("Bitbucket verifier rejects missing authentication and a zero merge-check value", async () => {
  await assert.rejects(() => verifyLive(fetchFixture, { BITBUCKET_COMMIT: commit }), /authentication is required/);
  await assert.rejects(
    () => verifyLive(async (url) => url.endsWith("/branch-restrictions") ? ok({ values: [{ kind: "require_passing_builds_to_merge", pattern: "main", value: 0 }] }) : fetchFixture(url), {
      BITBUCKET_API_TOKEN: "redacted", BITBUCKET_COMMIT: commit,
    }),
    /passing-build merge check is missing/,
  );
});

test("Bitbucket verifier rejects stale pipeline and commit-status success", async () => {
  const environment = { BITBUCKET_API_TOKEN: "redacted", BITBUCKET_COMMIT: commit };
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
