import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { canonicalJson, canonicalSha256, bytesSha256 } from "../contracts/authored_vnext/semantic/fixtures/canonical-json.mjs";
import { verifyFixture } from "./verify-authored-semantic-fixtures.mjs";

const root = path.resolve("contracts/authored_vnext/semantic/fixtures");

test("canonical JSON fixture is stable and NFC-normalized", () => {
  const value = JSON.parse(fs.readFileSync(path.join(root, "canonical-fixture.json"), "utf8"));
  const expected = JSON.parse(fs.readFileSync(path.join(root, "canonical-fixture.expected.json"), "utf8"));
  assert.equal(canonicalJson(value), expected.canonicalJson);
  assert.equal(canonicalSha256(value), expected.sha256);
});

test("eight-slide fixture derives ordered bundle and manifest digests", () => {
  const result = verifyFixture();
  assert.equal(result.status, "not-verified");
  assert.equal(result.promotionReady, false);
  assert.equal(result.fullNativeParityReady, false);
  assert.equal(result.independentVnextGenerationReady, false);
  assert.equal(result.bundleDigests.length, 8);
  assert.match(result.canonicalDigest, /^sha256:[0-9a-f]{64}$/);
});

test("evidence verifier binds fresh mode artifacts and rejects reuse", () => {
  const result = verifyFixture();
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "authored-semantic-evidence-"));
  try {
    const modes = ["fidelity", "hybrid"].map((mode) => {
      const attemptId = `fixture-${mode}-attempt-0007`;
      const rendererSourceDigest = canonicalSha256({ canonicalDigest: result.canonicalDigest, mode, attemptId });
      const artifacts = ["pptx", ...Array.from({ length: 8 }, (_, index) => `slide-${index + 1}`)].map((kind) => {
        const relative = `${mode}/${kind}.artifact`;
        const absolute = path.join(temp, relative);
        fs.mkdirSync(path.dirname(absolute), { recursive: true });
        fs.writeFileSync(absolute, `${mode}:${kind}:fresh-artifact\n`, "utf8");
        return { path: relative, sha256: bytesSha256(fs.readFileSync(absolute)) };
      });
      return {
        mode,
        attemptId,
        presentationRevision: 8,
        canonicalDigest: result.canonicalDigest,
        rendererSourceDigest,
        visualParity: "not-verified",
        status: "not-verified",
        artifacts,
        slides: Array.from({ length: 8 }, (_, index) => ({ slideId: `fixture-slide-${String(index + 1).padStart(2, "0")}` })),
      };
    });
    const evidence = {
      schemaVersion: "authored-semantic-atomic-evidence/v2",
      fixtureId: "authored-semantic-dual-output-eight-slide-v1",
      presentationId: "fixture-presentation-8slide",
      presentationRevision: 8,
      operationId: "fixture-operation-0008",
      generationAttemptId: "fixture-generation-0008",
      canonicalDigest: result.canonicalDigest,
      promotionReady: false,
      fullNativeParityReady: false,
      independentVnextGenerationReady: false,
      modes,
    };
    const evidencePath = path.join(temp, "evidence.json");
    fs.writeFileSync(evidencePath, JSON.stringify(evidence), "utf8");
    assert.equal(verifyFixture(path.join(root, "vertical-slice-fixture.json"), evidencePath).status, "not-verified");

    const reused = structuredClone(evidence);
    reused.modes[1].artifacts[0].path = reused.modes[0].artifacts[0].path;
    fs.writeFileSync(evidencePath, JSON.stringify(reused), "utf8");
    assert.throws(() => verifyFixture(path.join(root, "vertical-slice-fixture.json"), evidencePath), /artifact-reused-across-modes/);
  } finally {
    fs.rmSync(temp, { recursive: true, force: true });
  }
});
