import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  assessSnapshot,
  classifyFiles,
  renderMarkdown,
} from "./intake-upstream-main.mjs";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const fixtureDir = path.join(repoRoot, "scripts", "fixtures", "upstream-intake");
const policy = JSON.parse(
  await readFile(path.join(repoRoot, "compatibility", "upstream-intake-policy.json"), "utf8")
);
const registry = JSON.parse(
  await readFile(path.join(repoRoot, "compatibility", "protected-local-patches.json"), "utf8")
);

async function fixture(name) {
  return JSON.parse(await readFile(path.join(fixtureDir, name), "utf8"));
}

function invoke(name, ...extra) {
  return spawnSync(
    process.execPath,
    [
      "scripts/intake-upstream-main.mjs",
      "--fixture",
      `scripts/fixtures/upstream-intake/${name}`,
      "--json",
      ...extra,
    ],
    { cwd: repoRoot, encoding: "utf8" }
  );
}

test("same SHA verifies pinned metadata and reports unchanged", async () => {
  const result = assessSnapshot(await fixture("unchanged.json"), policy, registry);
  assert.equal(result.status, "unchanged");
  assert.equal(result.changeDetected, false);
  assert.equal(result.risk, "none");
  assert.equal(result.metadata.subject, policy.baselineMetadata.subject);
});

test("documentation and proxy v2 traces are not backend routes", async () => {
  const result = assessSnapshot(await fixture("changed-review.json"), policy, registry);
  assert.equal(result.status, "change-detected");
  assert.equal(result.risk, "info");
  const ids = result.findings.flatMap((finding) =>
    finding.categories.map((category) => category.id)
  );
  assert.ok(ids.includes("api-v2-experimental-doc"));
  assert.ok(ids.includes("api-v2-proxy-trace"));
  assert.ok(!ids.includes("api-v2-backend-route"));
});

test("backend v2, migration mutation, discriminator removal, and export version are risky", async () => {
  const result = assessSnapshot(
    await fixture("changed-contract-risk.json"),
    policy,
    registry
  );
  assert.equal(result.risk, "contract-risk");
  const ids = new Set(
    result.findings.flatMap((finding) =>
      finding.categories.map((category) => category.id)
    )
  );
  assert.ok(ids.has("api-v2-backend-route"));
  assert.ok(ids.has("alembic-contract"));
  assert.ok(ids.has("template-v2-schema"));
  assert.ok(ids.has("presentation-export-version"));
});

test("API v1 removal escalates while an addition remains review-only", () => {
  const base = {
    filename: "servers/fastapi/api/v1/ppt/router.py",
    status: "modified",
    additions: 1,
    deletions: 0,
  };
  const addition = classifyFiles(
    [{ ...base, patch: '+@ROUTER.post("/api/v1/example")' }],
    policy,
    registry
  );
  const removal = classifyFiles(
    [{ ...base, deletions: 1, patch: '-@ROUTER.delete("/api/v1/example")' }],
    policy,
    registry
  );
  assert.equal(addition.risk, "review");
  assert.equal(removal.risk, "contract-risk");
});

test("protected path intersection is contract-risk", () => {
  const result = classifyFiles(
    [
      {
        filename: "servers/nextjs/lib/authored-hybrid/export.ts",
        status: "modified",
        patch: "+editorial change",
      },
    ],
    policy,
    registry
  );
  assert.equal(result.risk, "contract-risk");
  assert.ok(
    result.findings[0].categories.some(
      (category) => category.id === "protected-local-patch-overlap"
    )
  );
});

test("rate limit and network failures are intake errors, never changes", async () => {
  for (const name of [
    "rate-limited.json",
    "network-error.json",
    "missing-ref.json",
    "remote-moved.json",
  ]) {
    const result = assessSnapshot(await fixture(name), policy, registry);
    assert.equal(result.status, "intake-error");
    assert.equal(result.changeDetected, false);
    assert.match(renderMarkdown(result), /not an upstream change/i);
  }
});

test("pinned SHA with unexpected metadata is an integrity error, not a change", async () => {
  const snapshot = await fixture("unchanged.json");
  snapshot.commit.commit.message = "unexpected subject";
  const result = assessSnapshot(snapshot, policy, registry);
  assert.equal(result.status, "intake-error");
  assert.equal(result.changeDetected, false);
  assert.equal(result.error.code, "BASELINE_METADATA_MISMATCH");
});

test("a capped or non-forward compare fails closed as contract-risk", async () => {
  const snapshot = await fixture("changed-review.json");
  snapshot.compare.files = Array.from(
    { length: policy.maximumCompareFiles },
    (_, index) => ({
      filename: `docs/change-${index}.md`,
      status: "modified",
      additions: 1,
      deletions: 0,
      patch: "+editorial",
    })
  );
  const capped = assessSnapshot(snapshot, policy, registry);
  assert.equal(capped.classificationComplete, false);
  assert.equal(capped.risk, "contract-risk");

  snapshot.compare.files = [];
  snapshot.compare.status = "diverged";
  const diverged = assessSnapshot(snapshot, policy, registry);
  assert.equal(diverged.classificationComplete, false);
  assert.equal(diverged.risk, "contract-risk");
});

test("CLI exit policy separates change detection, risk, and operational errors", () => {
  const review = invoke("changed-review.json", "--fail-on-risk");
  assert.equal(review.status, 0, review.stderr);
  const riskWithoutGate = invoke("changed-contract-risk.json");
  assert.equal(riskWithoutGate.status, 0, riskWithoutGate.stderr);
  const gatedRisk = invoke("changed-contract-risk.json", "--fail-on-risk");
  assert.equal(gatedRisk.status, 2, gatedRisk.stderr);
  const operational = invoke("rate-limited.json", "--fail-on-risk");
  assert.equal(operational.status, 1, operational.stderr);
  assert.equal(JSON.parse(operational.stdout).changeDetected, false);
});
