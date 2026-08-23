import assert from "node:assert/strict";
import crypto from "node:crypto";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { executeGate0 } from "./run-authored-bounded-gate0-v3.mjs";

const repoRoot = path.resolve(new URL("..", import.meta.url).pathname.replace(/^\/(?:[A-Za-z]:)/, (match) => match.slice(1)));
const contracts = path.join(repoRoot, "docs", "authored-bounded-hybrid-release-v3-contracts");

function makeGitRepo(root) {
  fs.mkdirSync(root);
  execFileSync("git", ["init", "-q", root]);
  fs.writeFileSync(path.join(root, "source.txt"), "sealed source\n");
  execFileSync("git", ["-C", root, "add", "source.txt"]);
  execFileSync("git", ["-C", root, "-c", "user.name=Gate Test", "-c", "user.email=gate@example.invalid", "commit", "-qm", "fixture"]);
}

function writeDriver(filePath, productValidatorAccepted = false) {
  const { publicKey, privateKey } = crypto.generateKeyPairSync("ed25519");
  const signerKeyId = `sha256:${crypto.createHash("sha256").update(publicKey.export({ type: "spki", format: "der" })).digest("hex")}`;
  const trustPath = path.join(path.dirname(filePath), "trace-trust.json");
  fs.writeFileSync(trustPath, JSON.stringify({ authorityDomain: "structural_test", evidenceClass: "synthetic_structural_only", keyPurpose: "gate0-server-execution-trace", signerKeyId, publicKeySpkiBase64: publicKey.export({ type: "spki", format: "der" }).toString("base64"), revoked: false }));
  fs.writeFileSync(filePath, `
import fs from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { canonicalSha256, canonicalSha256Excluding } from ${JSON.stringify(new URL("./authored-bounded-release-contracts.mjs", import.meta.url).href)};
const privateKey = crypto.createPrivateKey({ key: Buffer.from(${JSON.stringify(privateKey.export({ type: "pkcs8", format: "der" }).toString("base64"))}, "base64"), type: "pkcs8", format: "der" });
export async function runGate0Case({ caseContract, manifest, policy, runRoot, caseRoot, sourceTreeCensus }) {
  const ordinal = String(caseContract.ordinal).padStart(2, "0");
  const request = path.join(caseRoot, "request.json");
  const response = path.join(caseRoot, "response.json");
  const tracePath = path.join(caseRoot, "trace.json");
  const completionPath = path.join(caseRoot, "completion.json");
  const subjectPath = path.join(caseRoot, "mutated-subject.json");
  fs.writeFileSync(request, JSON.stringify({ caseId: caseContract.caseId, mutation: caseContract.operator || null }));
  fs.writeFileSync(response, JSON.stringify({ status: caseContract.expectedHttpStatus, code: caseContract.expectedErrorCode || null }));
  const mutatedSubject = { caseId: caseContract.caseId, operator: caseContract.operator || "positive", nonce: ordinal };
  const subjectBytes = Buffer.from(JSON.stringify(mutatedSubject));
  const subjectArtifact = { artifactId: "gate0-" + ordinal + "-mutated-subject", versionId: "artifact-version-" + ordinal, sha256: "sha256:" + crypto.createHash("sha256").update(subjectBytes).digest("hex"), byteLength: subjectBytes.length, mediaType: "application/json", state: "bound" };
  fs.writeFileSync(subjectPath, subjectBytes);
  const mutationEvidence = caseContract.ordinal === 1 ? null : { jsonPaths: caseContract.jsonPaths, operator: caseContract.operator, phase: caseContract.mutationPhase || "payload-before-issue", subjectArtifactId: subjectArtifact.artifactId, baselineSubjectDigest: canonicalSha256({ caseId: caseContract.caseId, operator: "baseline" }), mutatedSubjectDigest: canonicalSha256(mutatedSubject), mutationDigest: "" };
  if (mutationEvidence) mutationEvidence.mutationDigest = canonicalSha256Excluding(mutationEvidence, ["$.mutationDigest"]);
  const seams = ["router", "database", "publicationService", "productionBindingService", "artifactEndpoint", "receiptEndpoint", "projectionEndpoint"];
  const spans = seams.map((seam, index) => {
    const evidenceId = "evidence-" + ordinal + "-" + index;
    const durableRecordId = seam === "database" ? "subject-record-" + ordinal : seam === "publicationService" ? "manifest-record-" + ordinal : seam === "productionBindingService" ? "binding-record-" + ordinal : seam === "artifactEndpoint" ? subjectArtifact.versionId : seam === "receiptEndpoint" ? evidenceId : undefined;
    return { seam, serverObserved: true, operationId: "operation-" + ordinal + "-" + index, inputDigest: canonicalSha256({ seam, direction: "input", ordinal: caseContract.ordinal }), outputDigest: canonicalSha256({ seam, direction: "output", ordinal: caseContract.ordinal }), evidenceRef: "record://gate0/" + ordinal + "/" + seam, ...(durableRecordId ? { durableRecordId } : {}) };
  });
  const postState = { boundArtifactCount: 2, structuralReceiptCount: caseContract.ordinal === 1 ? 1 : 0, actualProductReceiptCount: 0, sealedCompletionCount: 0, actualProductPromotionReady: false };
  const serverTrace = { schemaVersion: "authored-gate0-server-execution-trace/v1", source: "fastapi-server-issued", traceId: "12345678-1234-4000-8000-123456789abc", databaseTransactionId: "txn-gate0-1234", caseId: caseContract.caseId, manifestDigest: manifest.manifestDigest, evidencePolicyDigest: policy.policyDigest, authorityDomain: "structural_test", mutationEvidence, spans, postState, signerKeyId: ${JSON.stringify(signerKeyId)}, traceDigest: "", signatureBase64: "" };
  serverTrace.traceDigest = canonicalSha256Excluding(serverTrace, ["$.traceDigest", "$.signatureBase64"]);
  serverTrace.signatureBase64 = crypto.sign(null, Buffer.from(serverTrace.traceDigest, "ascii"), privateKey).toString("base64");
  fs.writeFileSync(tracePath, JSON.stringify(serverTrace));
  fs.writeFileSync(completionPath, JSON.stringify({ sealedCompletionCount: 0 }));
  const artifacts = [
    { artifactId: "gate0-" + ordinal + "-request", path: path.relative(runRoot, request), mediaType: "application/json" },
    { artifactId: "gate0-" + ordinal + "-response", path: path.relative(runRoot, response), mediaType: "application/json" },
    { artifactId: "gate0-" + ordinal + "-trace", path: path.relative(runRoot, tracePath), mediaType: "application/json" },
    { artifactId: "gate0-" + ordinal + "-completion", path: path.relative(runRoot, completionPath), mediaType: "application/json" },
  ];
  for (const [index, span] of spans.entries()) {
    const evidencePath = path.join(caseRoot, "server-evidence-" + span.seam + ".json");
    const payload = span.seam === "database" ? { subjectId: span.durableRecordId } : span.seam === "publicationService" ? { manifestRecordId: span.durableRecordId } : span.seam === "productionBindingService" ? { bindingRecordId: span.durableRecordId } : span.seam === "artifactEndpoint" ? { artifact: subjectArtifact, contentBase64: subjectBytes.toString("base64") } : {};
    const evidence = { schemaVersion: "authored-gate0-structural-evidence/v1", evidenceId: span.seam === "receiptEndpoint" ? span.durableRecordId : "evidence-" + ordinal + "-" + index, subjectId: "subject-record-" + ordinal, authorityDomain: "structural_test", seam: span.seam, operationId: span.operationId, inputDigest: span.inputDigest, outputDigest: span.outputDigest, payload };
    fs.writeFileSync(evidencePath, JSON.stringify(evidence));
    artifacts.push({ artifactId: "gate0-" + ordinal + "-server-evidence-" + span.seam, evidenceRef: span.evidenceRef, path: path.relative(runRoot, evidencePath), mediaType: "application/json" });
  }
  artifacts.push({ artifactId: subjectArtifact.artifactId, versionId: subjectArtifact.versionId, sha256: subjectArtifact.sha256, byteLength: subjectArtifact.byteLength, path: path.relative(runRoot, subjectPath), mediaType: subjectArtifact.mediaType });
  return {
    driverId: "authored-gate0-fastapi-isolated-db/v1",
    caseId: caseContract.caseId,
    ordinal: caseContract.ordinal,
    authorityDomain: "structural_test",
    evidenceClass: "synthetic_structural_only",
    productionAuthorityGranted: false,
    productionValidatorAccepted: ${productValidatorAccepted},
    serverTrace,
    sourceTreeCensusDigest: sourceTreeCensus.censusDigest,
    httpStatus: caseContract.expectedHttpStatus,
    errorCode: caseContract.expectedErrorCode,
    sealedCompletionCount: 0,
    isolatedProjectionPromotionReady: true,
    releaseReportStatus: "not-verified",
    mutation: { jsonPaths: caseContract.jsonPaths, operator: caseContract.operator, phase: caseContract.mutationPhase || "payload-before-issue" },
    artifacts,
  };
}
`);
  return trustPath;
}

test("Gate 0 executes the exact 1 positive plus 33 mutations and remains structural-only", async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "gate0-v3-"));
  const source = path.join(temp, "source");
  makeGitRepo(source);
  const driver = path.join(temp, "driver.mjs");
  const serverTraceTrustPath = writeDriver(driver);
  const result = await executeGate0({
    repoRoot: source,
    manifestPath: path.join(contracts, "gate0-case-manifest.v1.json"),
    policyPath: path.join(contracts, "evidence-policy.v2.json"),
    validatorContractPath: path.join(contracts, "gate0-semantic-validator-contract.v1.json"),
    driverModulePath: driver,
    serverTraceTrustPath,
    outputDir: path.join(temp, "evidence"),
    now: new Date("2026-08-23T00:00:00.000Z"),
  });
  assert.equal(result.suiteStatus, "structurally-reachable");
  assert.equal(result.executedCaseCount, 34);
  assert.equal(result.acceptedCaseCount, 34);
  assert.equal(result.skippedCaseCount, 0);
  assert.equal(result.artifactInventory.length, 408);
  assert.equal(result.semanticValidationReceiptRef.issuerId, "authored-gate0-structural-validator/local-v1");
});

test("Gate 0 fails closed if its structural evidence is claimed accepted by production", async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "gate0-v3-boundary-"));
  const source = path.join(temp, "source");
  makeGitRepo(source);
  const driver = path.join(temp, "driver.mjs");
  const serverTraceTrustPath = writeDriver(driver, true);
  const result = await executeGate0({
    repoRoot: source,
    manifestPath: path.join(contracts, "gate0-case-manifest.v1.json"),
    policyPath: path.join(contracts, "evidence-policy.v2.json"),
    validatorContractPath: path.join(contracts, "gate0-semantic-validator-contract.v1.json"),
    driverModulePath: driver,
    serverTraceTrustPath,
    outputDir: path.join(temp, "evidence"),
  });
  assert.equal(result.suiteStatus, "failed");
  assert.equal(result.failedCaseCount, 34);
});
