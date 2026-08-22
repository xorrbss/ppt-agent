import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { canonicalSha256 } from "../contracts/authored_vnext/semantic/fixtures/canonical-json.mjs";
import { verifyEvidenceManifest } from "./verify-authored-semantic-vertical-slice-evidence.mjs";

function sha(bytes) {
  return `sha256:${crypto.createHash("sha256").update(bytes).digest("hex")}`;
}

function write(root, name, bytes) {
  const filePath = path.join(root, name);
  fs.writeFileSync(filePath, bytes);
  return { path: name, sha256: sha(bytes) };
}

function makeBundle() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "authored-semantic-evidence-"));
  const presentationId = "11111111-1111-4111-8111-111111111111";
  const operationId = "22222222-2222-4222-8222-222222222222";
  const generationAttemptId = "33333333-3333-4333-8333-333333333333";
  const manifestDigest = "sha256:" + "a".repeat(64);
  const seedValue = {
    schemaVersion: "authored-semantic-vertical-slice-seed/v2",
    fixtureId: "authored-semantic-dual-output-eight-slide-v1",
    presentationId,
    presentationRevision: 1,
    manifestDigest,
    operationId,
    generationAttemptId,
    slideIds: Array.from({ length: 8 }, (_, index) => `44444444-4444-4444-8444-44444444444${index}`),
    promotionReady: false,
    fullNativeParityReady: false,
    independentVnextGenerationReady: false,
  };
  const seedBytes = Buffer.from(JSON.stringify(seedValue));
  const seed = write(root, "seed.json", seedBytes);
  const paths = { seed };
  const modes = ["authored", "fidelity", "hybrid"].map((mode, index) => {
    const pptxBytes = Buffer.concat([Buffer.from("PK\x03\x04"), Buffer.from(mode)]);
    const pptxName = `${mode}.pptx`;
    const pptx = write(root, pptxName, pptxBytes);
    const comValue = { Presentations: [{ Path: path.resolve(root, pptxName), ReadbackPassed: true, Reopened: true }] };
    const exportAttemptId = `${index + 1}eeeeeee-eeee-4eee-8eee-eeeeeeeeeee${index}`;
    const renderValue = { exportAttemptId, sourcePptx: { sha256: pptx.sha256 }, slideCount: 8 };
    const qualityValue = { exportAttemptId, pptxSha256: pptx.sha256, status: "not-verified" };
    comValue.exportAttemptId = exportAttemptId;
    const com = write(root, `${mode}-com.json`, Buffer.from(JSON.stringify(comValue)));
    const render = write(root, `${mode}-render.json`, Buffer.from(JSON.stringify(renderValue)));
    const quality = write(root, `${mode}-quality.json`, Buffer.from(JSON.stringify(qualityValue)));
    const pngs = Array.from({ length: 8 }, (_, slideIndex) => write(root, `${mode}-slide-${slideIndex + 1}.png`, Buffer.from(`${mode}-png-${slideIndex + 1}`)));
    const firstOpenPngs = Array.from({ length: 8 }, (_, slideIndex) => write(root, `${mode}-first-open-${slideIndex + 1}.png`, Buffer.from(`${mode}-powerpoint-png-${slideIndex + 1}`)));
    const reopenPngs = Array.from({ length: 8 }, (_, slideIndex) => write(root, `${mode}-reopen-${slideIndex + 1}.png`, Buffer.from(`${mode}-powerpoint-png-${slideIndex + 1}`)));
    return {
      mode,
      attemptId: `${index + 1}6666666-6666-4666-8666-66666666666${index}`,
      exportAttemptId,
      presentationId,
      presentationRevision: 1,
      manifestDigest,
      operationId,
      generationAttemptId,
      status: "not-verified",
      pptx,
      reports: { com: { ...com, pptxSha256: pptx.sha256 }, render: { ...render, pptxSha256: pptx.sha256 }, quality: { ...quality, pptxSha256: pptx.sha256 } },
      renderedPngs: pngs,
      powerPoint: { firstOpen: true, reopened: true, pptxSha256: pptx.sha256, firstOpenPngs, reopenPngs },
      checks: { repairObserved: false, overflowGt5Pt: 0, overflowGt10Pt: 0, lineBreakChanges: 0, clipping: 0, overlap: 0, ghosting: 0, nativeBackplateCollision: 0 },
    };
  });
  const manifest = {
    schemaVersion: "authored-semantic-vertical-slice-evidence/v3",
    fixtureId: "authored-semantic-dual-output-eight-slide-v1",
    evidenceBundleId: "77777777-7777-4777-8777-777777777777",
    status: "not-verified",
    presentationId,
    presentationRevision: 1,
    manifestDigest,
    operationId,
    generationAttemptId,
    seedReceipt: { ...seed, path: "seed.json" },
    freshness: { runId: "run-001", generatedAt: "2026-08-23T00:00:00Z", reusedFrom: null, previousEvidenceDigest: null },
    modes,
    slideRegistry: [
      { slideId: "slide-01", language: "ko", archetypes: ["card", "metric", "table"] },
      { slideId: "slide-02", language: "zh", archetypes: ["process", "architecture"] },
      { slideId: "slide-03", language: "ja", archetypes: ["timeline", "roadmap"] },
      { slideId: "slide-04", language: "ko", archetypes: ["comparison"] },
      { slideId: "slide-05", language: "zh", archetypes: ["infographic"] },
      { slideId: "slide-06", language: "ja", archetypes: ["chart"] },
      { slideId: "slide-07", language: "ko", archetypes: ["image"] },
      { slideId: "slide-08", language: "zh", archetypes: ["card"] },
    ],
    registryDigest: "",
    diffPolicy: { maxChangedPixelRatio: 0.02, minSsim: 0.98 },
    perSlideDiffs: [],
    manualReviewLedger: { entries: [{ reviewer: "qa-reviewer-01", reviewedAt: "2026-08-23T00:00:00Z", decision: "accept", scope: ["all-slides", "all-modes"] }] },
    recoveryAttempts: [],
    promotionReady: false,
    fullNativeParityReady: false,
    independentVnextGenerationReady: false,
  };
  for (const [kind, attemptId, generationAttemptId, rejectionCode] of [
    ["remake", "88888888-8888-4888-8888-888888888888", "99999999-9999-4999-8999-999999999999", "STALE_REVISION"],
    ["rollback", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "STALE_ATTEMPT"],
  ]) {
    const request = { kind, expectedRevision: 1, generationAttemptId };
    const response = { kind, code: rejectionCode, accepted: false };
    manifest.recoveryAttempts.push({ kind, attemptId, operationId, generationAttemptId, observedRevision: 0, outcome: "rejected", rejectionCode, request, response, requestDigest: canonicalSha256(request), responseDigest: canonicalSha256(response) });
  }
  manifest.registryDigest = canonicalSha256(manifest.slideRegistry);
  for (let slideNumber = 1; slideNumber <= 8; slideNumber += 1) {
    manifest.perSlideDiffs.push({ slideNumber, pairs: Object.fromEntries(["authored-fidelity", "authored-hybrid", "fidelity-hybrid"].map((pair) => [pair, { status: "pass", changedPixelRatio: 0.001, ssim: 0.999 }])) });
  }
  const exportAttemptIds = modes.map((mode) => mode.exportAttemptId).sort();
  const receiptValue = { schemaVersion: "authored-db-attempt-receipt/v1", presentationId, presentationRevision: 1, operationId, generationAttemptId, attemptId: "db-attempt-001", status: "succeeded", exportAttemptIds };
  const receipt = write(root, "db-attempt-receipt.json", Buffer.from(JSON.stringify(receiptValue)));
  manifest.dbAttemptResolution = { schemaVersion: "authored-db-attempt-resolution/v1", authority: "database", status: "resolved", presentationId, operationId, generationAttemptId, attemptId: "db-attempt-001", receipt: { ...receipt, pptxSha256: undefined }, receiptSha256: receipt.sha256 };
  manifest.atomicBinding = { fixtureId: manifest.fixtureId, presentationId, presentationRevision: 1, operationId, generationAttemptId, manifestDigest, registryDigest: manifest.registryDigest, exportAttemptIds };
  manifest.atomicBindingDigest = canonicalSha256(manifest.atomicBinding);
  const manifestPath = path.join(root, "evidence.json");
  fs.writeFileSync(manifestPath, JSON.stringify(manifest));
  return { root, manifest, manifestPath };
}

test("verifies fresh eight-slide three-mode evidence and keeps promotion flags false", () => {
  const bundle = makeBundle();
  const result = verifyEvidenceManifest(bundle.manifestPath);
  assert.equal(result.status, "not-verified");
  assert.equal(result.modes.length, 3);
  assert.equal(result.promotionReady, false);
  assert.equal(typeof result.evidenceDigest, "string");
});

test("rejects remake or rollback evidence that claims success", () => {
  const bundle = makeBundle();
  bundle.manifest.recoveryAttempts[1].accepted = true;
  fs.writeFileSync(bundle.manifestPath, JSON.stringify(bundle.manifest));
  assert.throws(() => verifyEvidenceManifest(bundle.manifestPath), /recovery-success-claim/);
});

test("rejects reused attempt ids and artifact digests", () => {
  const bundle = makeBundle();
  bundle.manifest.modes[1].attemptId = bundle.manifest.modes[0].attemptId;
  fs.writeFileSync(bundle.manifestPath, JSON.stringify(bundle.manifest));
  assert.throws(() => verifyEvidenceManifest(bundle.manifestPath), /attempt-reused/);
});

test("rejects stale seed revision or manifest binding", () => {
  const bundle = makeBundle();
  bundle.manifest.presentationRevision = 2;
  fs.writeFileSync(bundle.manifestPath, JSON.stringify(bundle.manifest));
  assert.throws(() => verifyEvidenceManifest(bundle.manifestPath), /seed-binding-mismatch/);
});

test("rejects any promotion success claim", () => {
  const bundle = makeBundle();
  bundle.manifest.promotionReady = true;
  fs.writeFileSync(bundle.manifestPath, JSON.stringify(bundle.manifest));
  assert.throws(() => verifyEvidenceManifest(bundle.manifestPath), /promotion-flag/);
});
