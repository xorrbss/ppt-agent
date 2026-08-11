import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  assessSnapshot,
  classifyFiles,
  renderMarkdown,
} from "./intake-upstream-main.mjs";

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const fixtureDirectory = path.join(repoRoot, "scripts", "fixtures", "upstream-intake");
const policy = JSON.parse(
  await readFile(path.join(repoRoot, "compatibility", "upstream-intake-policy.json"), "utf8")
);
const historicalPolicy = {
  ...policy,
  acceptedBaseline: {
    sha: "968f7de6c2b3467f838330f319b992349805eeef",
    committedAt: "2026-08-10T17:12:35+05:45",
    subject: "Merge pull request #811 from presenton/feat/llmai-thinking-smart-html",
  },
};
const registry = JSON.parse(
  await readFile(path.join(repoRoot, "compatibility", "protected-local-patches.json"), "utf8")
);
const rangeFixture = "upstream-main-968f7de6-to-b940f7b4.json";

async function fixture(name) {
  return JSON.parse(await readFile(path.join(fixtureDirectory, name), "utf8"));
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

test("V2 preserves the completed historical intake range after baseline finalization", async () => {
  const result = assessSnapshot(await fixture(rangeFixture), historicalPolicy, registry);
  assert.equal(result.acceptedBaseline.sha, "968f7de6c2b3467f838330f319b992349805eeef");
  assert.equal(result.observedUpstream.sha, "b940f7b4a51d473af830d96d441276af3540dae6");
  assert.equal(result.range.exactPinnedObservation, true);
  assert.equal(result.compare.totalCommits, 6);
  assert.equal(result.risk, "contract-risk");
});

test("active accepted baseline advances only with recorded integration evidence", () => {
  assert.equal(policy.acceptedBaseline.sha, "b940f7b4a51d473af830d96d441276af3540dae6");
  assert.equal(policy.observedUpstream.sha, policy.acceptedBaseline.sha);
  assert.equal(
    policy.observedUpstream.finalizedFrom,
    "968f7de6c2b3467f838330f319b992349805eeef"
  );
  assert.equal(policy.observedUpstream.verificationRef, "focused-verification-2026-08-11");
  assert.equal(policy.finalization.candidateOnly, false);
});

test("same accepted SHA verifies pinned metadata and reports unchanged", async () => {
  const result = assessSnapshot(await fixture("v2-unchanged.json"), policy, registry);
  assert.equal(result.status, "unchanged");
  assert.equal(result.changeDetected, false);
  assert.equal(result.risk, "none");
  assert.equal(result.observedUpstream.subject, policy.acceptedBaseline.subject);
});

test("accepted committedAt compares ISO instants rather than timestamp spellings", async () => {
  const snapshot = await fixture("v2-unchanged.json");
  const equivalentOffsetPolicy = {
    ...policy,
    acceptedBaseline: {
      ...policy.acceptedBaseline,
      // This is the same instant as the fixture's +05:45 timestamp.
      committedAt: "2026-08-10T16:21:57Z",
    },
  };

  const equivalent = assessSnapshot(snapshot, equivalentOffsetPolicy, registry);
  assert.equal(equivalent.status, "unchanged");

  snapshot.commit.commit.committer.date = "2026-08-10T16:21:58Z";
  const differentInstant = assessSnapshot(snapshot, equivalentOffsetPolicy, registry);
  assert.equal(differentInstant.status, "intake-error");
  assert.match(differentInstant.metadataMismatches[0], /^committedAt expected /);
});

test("pinned SHA with unexpected metadata is an integrity error, not a change", async () => {
  const snapshot = await fixture("v2-unchanged.json");
  snapshot.commit.commit.message = "unexpected subject";
  const result = assessSnapshot(snapshot, policy, registry);
  assert.equal(result.status, "intake-error");
  assert.equal(result.changeDetected, false);
  assert.equal(result.error.code, "BASELINE_METADATA_MISMATCH");
});

test("intake output labels its accepted baseline separately from compatibility baselines", async () => {
  const result = assessSnapshot(await fixture("v2-unchanged.json"), policy, registry);
  const markdown = renderMarkdown(result);
  assert.match(markdown, /Intake accepted baseline/);
  assert.match(markdown, /Intake observed upstream/);
  assert.doesNotMatch(markdown, /^- Accepted baseline:/m);
});

test("documentation and proxy v2 traces are not backend routes", async () => {
  const result = assessSnapshot(
    await fixture("v2-changed-review.json"),
    historicalPolicy,
    registry
  );
  assert.equal(result.status, "change-detected");
  assert.equal(result.risk, "info");
  const ids = result.findings.flatMap((finding) =>
    finding.categories.map((category) => category.id)
  );
  assert.ok(!ids.includes("api-v2-backend-route"));
});

test("templates/v2 is template schema/compiler and never an /api/v2 backend route", () => {
  const result = classifyFiles(
    [{ filename: "servers/fastapi/templates/v2/schema.py", status: "modified", patch: "+group" }],
    policy,
    registry
  );
  const ids = result.findings[0].categories.map((category) => category.id);
  assert.ok(ids.includes("template-schema-compiler"));
  assert.ok(!ids.includes("api-v2-backend-route"));
  assert.equal(result.risk, "contract-risk");
});

test("backend v2, migration mutation, template schema, and exporter version are risky", async () => {
  const result = assessSnapshot(
    await fixture("v2-changed-contract-risk.json"),
    historicalPolicy,
    registry
  );
  assert.equal(result.risk, "contract-risk");
  const ids = new Set(
    result.findings.flatMap((finding) => finding.categories.map((category) => category.id))
  );
  assert.ok(ids.has("api-v2-backend-route"));
  assert.ok(ids.has("migrations"));
  assert.ok(ids.has("template-schema-compiler"));
  assert.ok(ids.has("renderer-export-runtime"));
});

test("API v1 route removal escalates while an addition remains review-only", () => {
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

test("a watched path without a patch fails closed as review", () => {
  const result = classifyFiles(
    [
      {
        filename: "servers/fastapi/templates/v2/models.py",
        status: "modified",
        additions: 0,
        deletions: 0,
      },
    ],
    policy,
    registry
  );
  assert.equal(result.risk, "review");
  assert.ok(
    result.findings[0].categories.some(
      (category) => category.id === "template-schema-compiler" && category.severity === "review"
    )
  );
});

test("protected path overlap remains contract-risk", () => {
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

test("rate limit, network, missing-ref, and remote-moved failures are not changes", async () => {
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

test("capped and non-forward compares fail closed as contract-risk", async () => {
  const cappedSnapshot = await fixture("v2-changed-review.json");
  cappedSnapshot.compare.files = Array.from(
    { length: policy.maximumCompareFiles },
    (_, index) => ({
      filename: `docs/change-${index}.md`,
      status: "modified",
      additions: 1,
      deletions: 0,
      patch: "+editorial",
    })
  );
  const capped = assessSnapshot(cappedSnapshot, historicalPolicy, registry);
  assert.equal(capped.classificationComplete, false);
  assert.equal(capped.risk, "contract-risk");

  const divergedSnapshot = await fixture("v2-changed-review.json");
  divergedSnapshot.compare.files = [];
  divergedSnapshot.compare.status = "diverged";
  const diverged = assessSnapshot(divergedSnapshot, historicalPolicy, registry);
  assert.equal(diverged.classificationComplete, false);
  assert.equal(diverged.risk, "contract-risk");
});

test("six upstream commits have the exact V2 domain and risk classifications", async () => {
  const result = assessSnapshot(await fixture(rangeFixture), historicalPolicy, registry);
  const actual = Object.fromEntries(
    result.commitClassifications.map((commit) => [
      commit.sha.slice(0, 8),
      { risk: commit.risk, domains: commit.domains },
    ])
  );
  assert.deepEqual(actual.a3352f3c, {
    risk: "contract-risk",
    domains: ["api", "presentation-profile-driver", "template-schema-compiler"],
  });
  assert.deepEqual(actual.a6552331, {
    risk: "review",
    domains: ["ui-composition", "renderer-export-runtime"],
  });
  assert.deepEqual(actual.e271359e, {
    risk: "contract-risk",
    domains: ["template-schema-compiler", "assets-security"],
  });
  assert.deepEqual(actual.aa23b143, { risk: "none", domains: [] });
  assert.deepEqual(actual["7131c4d1"], {
    risk: "contract-risk",
    domains: ["renderer-export-runtime", "desktop-runtime"],
  });
  assert.deepEqual(actual.b940f7b4, {
    risk: "review",
    domains: ["renderer-export-runtime", "desktop-runtime"],
  });
});

test("machine-readable contract diff reports every required contract dimension", async () => {
  const result = assessSnapshot(await fixture(rangeFixture), historicalPolicy, registry);
  assert.deepEqual(Object.keys(result.contractDiff), [
    "openapiRoutes",
    "alembicGraph",
    "jsonSchema",
    "providerRegistry",
    "templateCapability",
    "rendererFixtures",
    "exporterVersion",
    "forkOwnedOverlap",
  ]);
  assert.equal(result.contractDiff.openapiRoutes.status, "changed");
  assert.equal(result.contractDiff.alembicGraph.status, "unchanged");
  assert.equal(result.contractDiff.jsonSchema.status, "changed");
  assert.equal(result.contractDiff.exporterVersion.status, "changed");
});

test("CLI exit policy separates review, contract-risk, and operational errors", async () => {
  const temp = await mkdtemp(path.join(os.tmpdir(), "intake-v2-cli-"));
  const tempPolicy = path.join(temp, "policy.json");
  try {
    await writeFile(tempPolicy, JSON.stringify(historicalPolicy));
    const policyArgs = ["--policy", tempPolicy];
    const review = invoke("v2-changed-review.json", ...policyArgs, "--fail-on-risk");
    assert.equal(review.status, 0, review.stderr);

    const riskWithoutGate = invoke("v2-changed-contract-risk.json", ...policyArgs);
    assert.equal(riskWithoutGate.status, 0, riskWithoutGate.stderr);

    const gatedRisk = invoke(
      "v2-changed-contract-risk.json",
      ...policyArgs,
      "--fail-on-risk"
    );
    assert.equal(gatedRisk.status, 2, gatedRisk.stderr);

    const operational = invoke("rate-limited.json", ...policyArgs, "--fail-on-risk");
    assert.equal(operational.status, 1, operational.stderr);
    assert.equal(JSON.parse(operational.stdout).changeDetected, false);
  } finally {
    await rm(temp, { recursive: true, force: true });
  }
});

test("accepted SHA can only be staged into a separate policy after explicit finalization evidence", async () => {
  const temp = await mkdtemp(path.join(os.tmpdir(), "intake-v2-"));
  const output = path.join(temp, "candidate.json");
  const historicalPolicyPath = path.join(temp, "policy.json");
  try {
    await writeFile(historicalPolicyPath, JSON.stringify(historicalPolicy));
    const rejected = invoke(
      rangeFixture,
      "--policy",
      historicalPolicyPath,
      "--prepare-finalize"
    );
    assert.equal(rejected.status, 1);
    assert.equal(JSON.parse(rejected.stdout).error.code, "FINALIZATION_REQUIREMENTS_MISSING");

    const prepared = invoke(
      rangeFixture,
      "--policy",
      historicalPolicyPath,
      "--prepare-finalize",
      "--integration-verified",
      "--verification-ref",
      "focused-node-tests",
      "--authorization",
      "root",
      "--finalize-output",
      output
    );
    assert.equal(prepared.status, 0, prepared.stderr);
    const candidate = JSON.parse(await readFile(output, "utf8"));
    assert.equal(candidate.acceptedBaseline.sha, policy.observedUpstream.sha);
    assert.equal(
      historicalPolicy.acceptedBaseline.sha,
      "968f7de6c2b3467f838330f319b992349805eeef"
    );
  } finally {
    await rm(temp, { recursive: true, force: true });
  }
});
