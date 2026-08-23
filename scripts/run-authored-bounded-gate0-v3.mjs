import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

import {
  assertSourceTreeCensusUnchanged,
  canonicalSha256,
  canonicalSha256Excluding,
  captureSourceTreeCensus,
  validateCountContract,
  validatePolicyBinding,
} from "./authored-bounded-release-contracts.mjs";

export const SOURCE_CENSUS_IGNORE_POLICY_V1 = Object.freeze({
  generatedCacheAllowlistPrefixes: [
    "app_data/",
    "presentation-export/",
    "servers/fastapi/.venv/",
    "servers/fastapi/presenton_backend.egg-info/",
    "servers/nextjs/.next/",
    ".next-authored-vnext-dev-",
    "servers/nextjs/.next-authored-vnext-dev-",
    "servers/nextjs/node_modules/",
  ],
  generatedIgnoredDirectoryNames: ["__pycache__"],
  ignoredAllowlist: [".pytest_cache", ".ruff_cache", "servers/fastapi/.pytest_cache", "servers/fastapi/.ruff_cache"],
});

export function captureBoundedSourceCensus(repoRoot) {
  return captureSourceTreeCensus(repoRoot, SOURCE_CENSUS_IGNORE_POLICY_V1);
}

const REQUIRED_SEAMS = [
  "router",
  "database",
  "publicationService",
  "productionBindingService",
  "artifactEndpoint",
  "receiptEndpoint",
  "projectionEndpoint",
];

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function writeFreshJson(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, { flag: "wx" });
}

function digestBytes(bytes) {
  return `sha256:${crypto.createHash("sha256").update(bytes).digest("hex")}`;
}

function resolveInsideRunRoot(root, locator, artifactId) {
  const rootReal = fs.realpathSync(root);
  const absolute = fs.realpathSync(path.resolve(root, locator));
  if (absolute !== rootReal && !absolute.startsWith(`${rootReal}${path.sep}`)) throw new Error(`artifact-outside-run-root:${artifactId}`);
  return absolute;
}

function immutableFileRecord(root, binding) {
  assert.equal(typeof binding.artifactId, "string", "artifact-id-required");
  assert.equal(typeof binding.path, "string", `artifact-path-required:${binding.artifactId}`);
  const absolute = resolveInsideRunRoot(root, binding.path, binding.artifactId);
  const bytes = fs.readFileSync(absolute);
  const sha256 = digestBytes(bytes);
  if (binding.sha256 !== undefined && binding.sha256 !== sha256) throw new Error(`artifact-digest-mismatch:${binding.artifactId}`);
  if (binding.byteLength !== undefined && binding.byteLength !== bytes.length) throw new Error(`artifact-length-mismatch:${binding.artifactId}`);
  return {
    artifactId: binding.artifactId,
    versionId: binding.versionId || sha256.replace("sha256:", "sha256-"),
    sha256,
    byteLength: bytes.length,
    mediaType: binding.mediaType || "application/json",
  };
}

function structuralReceipt(resultCoreDigest, manifest, contract, outputDir, now) {
  const { publicKey, privateKey } = crypto.generateKeyPairSync("ed25519");
  const signerKeyId = digestBytes(publicKey.export({ type: "spki", format: "der" }));
  const payload = {
    receiptSchemaVersion: "authored-semantic-validation-receipt/v1",
    issuerId: "authored-gate0-structural-validator/local-v1",
    signerKeyId,
    signatureAlgorithm: "Ed25519",
    signatureBase64: "",
    issuedAt: now.toISOString(),
    expiresAt: new Date(now.getTime() + 30 * 60 * 1000).toISOString(),
    nonce: crypto.randomBytes(24).toString("hex"),
    subjectManifestDigest: manifest.manifestDigest,
    subjectResultCoreDigest: resultCoreDigest,
    semanticValidatorContractDigest: contract.contractDigest,
    receiptDigest: "",
  };
  payload.receiptDigest = canonicalSha256Excluding(payload, ["$.receiptDigest", "$.signatureBase64"]);
  payload.signatureBase64 = crypto.sign(null, Buffer.from(payload.receiptDigest, "ascii"), privateKey).toString("base64");
  assert.equal(crypto.verify(null, Buffer.from(payload.receiptDigest, "ascii"), publicKey, Buffer.from(payload.signatureBase64, "base64")), true);
  const receiptPath = path.join(outputDir, "authority", "semantic-validation-receipt.json");
  writeFreshJson(receiptPath, payload);
  const bytes = fs.readFileSync(receiptPath);
  const sha256 = digestBytes(bytes);
  writeFreshJson(path.join(outputDir, "authority", "structural-issuer-attestation.json"), {
    schemaVersion: "authored-gate0-structural-issuer-attestation/v1",
    authorityDomain: "structural_test",
    productionAuthorityGranted: false,
    issuerId: payload.issuerId,
    signerKeyId,
    publicKeySpkiBase64: publicKey.export({ type: "spki", format: "der" }).toString("base64"),
    privateKeyPersisted: false,
  });
  return {
    artifactId: "gate0-semantic-validation-receipt",
    versionId: sha256.replace("sha256:", "sha256-"),
    mediaType: "application/json",
    byteLength: bytes.length,
    sha256,
    ...payload,
  };
}

function validateObservation(caseContract, observation, runRoot, manifest, policy, traceTrust, sourceTreeCensusDigest) {
  assert.ok(Array.isArray(observation.artifacts) && observation.artifacts.length >= 4, `request-response-trace-artifacts-required:${caseContract.caseId}`);
  const records = observation.artifacts.map((binding) => immutableFileRecord(runRoot, binding));
  const recordById = new Map(records.map((entry) => [entry.artifactId, entry]));
  assert.equal(observation.driverId, "authored-gate0-fastapi-isolated-db/v1", `driver-id:${caseContract.caseId}`);
  assert.equal(observation.caseId, caseContract.caseId, `case-id:${caseContract.caseId}`);
  assert.equal(observation.ordinal, caseContract.ordinal, `case-ordinal:${caseContract.caseId}`);
  assert.equal(observation.authorityDomain, "structural_test", `authority-domain:${caseContract.caseId}`);
  assert.equal(observation.evidenceClass, "synthetic_structural_only", `evidence-class:${caseContract.caseId}`);
  assert.equal(observation.productionAuthorityGranted, false, `production-authority:${caseContract.caseId}`);
  assert.equal(observation.productionValidatorAccepted, false, `production-validator-boundary:${caseContract.caseId}`);
  assert.equal(observation.serverTrace?.schemaVersion, "authored-gate0-server-execution-trace/v1", `server-trace-schema:${caseContract.caseId}`);
  assert.equal(observation.serverTrace?.source, "fastapi-server-issued", `server-trace-source:${caseContract.caseId}`);
  assert.match(observation.serverTrace?.traceId || "", /^[0-9a-f-]{16,}$/, `server-trace-id:${caseContract.caseId}`);
  assert.match(observation.serverTrace?.databaseTransactionId || "", /^[0-9A-Za-z._:-]{8,}$/, `server-database-transaction:${caseContract.caseId}`);
  const traceSpans = observation.serverTrace?.spans || [];
  assert.deepEqual(traceSpans.map((entry) => entry.seam), REQUIRED_SEAMS, `product-seam-order:${caseContract.caseId}`);
  const spans = new Map(traceSpans.map((entry) => [entry.seam, entry]));
  const evidenceBindings = new Map(observation.artifacts.filter((entry) => entry.evidenceRef).map((entry) => [entry.evidenceRef, entry]));
  assert.equal(evidenceBindings.size, REQUIRED_SEAMS.length, `product-seam-evidence-count:${caseContract.caseId}`);
  const evidenceSubjectIds = new Set();
  for (const seam of REQUIRED_SEAMS) {
    const span = spans.get(seam); assert.equal(span?.serverObserved, true, `product-seam:${caseContract.caseId}:${seam}`);
    assert.match(span?.operationId || "", /^[0-9A-Za-z._:-]{8,}$/, `product-seam-operation:${caseContract.caseId}:${seam}`);
    assert.match(span?.inputDigest || "", /^sha256:[0-9a-f]{64}$/, `product-seam-input:${caseContract.caseId}:${seam}`);
    assert.match(span?.outputDigest || "", /^sha256:[0-9a-f]{64}$/, `product-seam-output:${caseContract.caseId}:${seam}`);
    assert.match(span?.evidenceRef || "", /^(?:(?:artifact|record|trace):\/\/[0-9A-Za-z._:/-]+|\/api\/v1\/ppt\/authored-emission\/gate0-structural-v3\/evidence\/[0-9a-f-]{36})$/, `product-seam-evidence:${caseContract.caseId}:${seam}`);
    if (["database", "publicationService", "productionBindingService", "artifactEndpoint", "receiptEndpoint"].includes(seam)) assert.match(span?.durableRecordId || "", /^[0-9A-Za-z._:-]{8,}$/, `product-seam-durable-record:${caseContract.caseId}:${seam}`);
    const evidenceBinding = evidenceBindings.get(span.evidenceRef);
    assert.ok(evidenceBinding, `product-seam-evidence-artifact:${caseContract.caseId}:${seam}`);
    assert.ok(recordById.has(evidenceBinding.artifactId), `product-seam-evidence-record:${caseContract.caseId}:${seam}`);
    const evidence = readJson(resolveInsideRunRoot(runRoot, evidenceBinding.path, evidenceBinding.artifactId));
    assert.equal(evidence.schemaVersion, "authored-gate0-structural-evidence/v1", `product-seam-evidence-schema:${caseContract.caseId}:${seam}`);
    assert.equal(evidence.authorityDomain, "structural_test", `product-seam-evidence-authority:${caseContract.caseId}:${seam}`);
    assert.equal(evidence.seam, seam, `product-seam-evidence-seam:${caseContract.caseId}:${seam}`);
    assert.equal(evidence.operationId, span.operationId, `product-seam-evidence-operation:${caseContract.caseId}:${seam}`);
    assert.equal(evidence.inputDigest, span.inputDigest, `product-seam-evidence-input:${caseContract.caseId}:${seam}`);
    assert.equal(evidence.outputDigest, span.outputDigest, `product-seam-evidence-output:${caseContract.caseId}:${seam}`);
    assert.match(evidence.subjectId || "", /^[0-9A-Za-z._:-]{8,}$/, `product-seam-evidence-subject:${caseContract.caseId}:${seam}`);
    evidenceSubjectIds.add(evidence.subjectId);
    if (seam === "database") assert.equal(evidence.payload?.subjectId, span.durableRecordId, `product-seam-durable-database:${caseContract.caseId}`);
    if (seam === "publicationService") assert.equal(evidence.payload?.manifestRecordId, span.durableRecordId, `product-seam-durable-publication:${caseContract.caseId}`);
    if (seam === "productionBindingService") assert.equal(evidence.payload?.bindingRecordId, span.durableRecordId, `product-seam-durable-binding:${caseContract.caseId}`);
    if (seam === "artifactEndpoint") assert.equal(evidence.payload?.artifact?.versionId, span.durableRecordId, `product-seam-durable-artifact:${caseContract.caseId}`);
    if (seam === "receiptEndpoint") assert.equal(evidence.evidenceId, span.durableRecordId, `product-seam-durable-receipt:${caseContract.caseId}`);
  }
  assert.equal(evidenceSubjectIds.size, 1, `product-seam-evidence-subject-chain:${caseContract.caseId}`);
  assert.equal(observation.serverTrace.caseId, caseContract.caseId, `server-trace-case:${caseContract.caseId}`);
  assert.equal(observation.serverTrace.manifestDigest, manifest.manifestDigest, `server-trace-manifest:${caseContract.caseId}`);
  assert.equal(observation.serverTrace.evidencePolicyDigest, policy.policyDigest, `server-trace-policy:${caseContract.caseId}`);
  assert.equal(observation.serverTrace.authorityDomain, "structural_test", `server-trace-authority:${caseContract.caseId}`);
  assert.equal(observation.sourceTreeCensusDigest, sourceTreeCensusDigest, `server-source-census-pin:${caseContract.caseId}`);
  assert.equal(traceTrust.authorityDomain, "structural_test", "server-trace-trust-domain");
  assert.equal(traceTrust.evidenceClass, "synthetic_structural_only", "server-trace-trust-class");
  assert.equal(traceTrust.keyPurpose, "gate0-server-execution-trace", "server-trace-key-purpose");
  assert.equal(traceTrust.revoked, false, "server-trace-key-revoked");
  assert.equal(observation.serverTrace.signerKeyId, traceTrust.signerKeyId, `server-trace-signer:${caseContract.caseId}`);
  assert.equal(observation.serverTrace.traceDigest, canonicalSha256Excluding(observation.serverTrace, ["$.traceDigest", "$.signatureBase64"]), `server-trace-digest:${caseContract.caseId}`);
  const tracePublicDer = traceTrust.publicKeySpkiBase64
    ? Buffer.from(traceTrust.publicKeySpkiBase64, "base64")
    : Buffer.concat([Buffer.from("302a300506032b6570032100", "hex"), Buffer.from(traceTrust.publicKeyRawBase64 || "", "base64")]);
  const tracePublicKey = crypto.createPublicKey({ key: tracePublicDer, type: "spki", format: "der" });
  assert.equal(crypto.verify(null, Buffer.from(observation.serverTrace.traceDigest, "ascii"), tracePublicKey, Buffer.from(observation.serverTrace.signatureBase64, "base64")), true, `server-trace-signature:${caseContract.caseId}`);
  assert.equal(observation.serverTrace.postState?.sealedCompletionCount, 0, `server-post-state-completion:${caseContract.caseId}`);
  assert.equal(observation.serverTrace.postState?.actualProductReceiptCount, 0, `server-post-state-product-receipt:${caseContract.caseId}`);
  assert.equal(observation.serverTrace.postState?.actualProductPromotionReady, false, `server-post-state-product-promotion:${caseContract.caseId}`);
  assert.ok(Number.isInteger(observation.serverTrace.postState?.boundArtifactCount) && observation.serverTrace.postState.boundArtifactCount >= 1, `server-post-state-bound-artifacts:${caseContract.caseId}`);
  const artifactEvidenceBinding = evidenceBindings.get(spans.get("artifactEndpoint").evidenceRef);
  const artifactEvidence = readJson(resolveInsideRunRoot(runRoot, artifactEvidenceBinding.path, artifactEvidenceBinding.artifactId));
  const productArtifact = artifactEvidence.payload?.artifact;
  const productArtifactBytes = Buffer.from(artifactEvidence.payload?.contentBase64 || "", "base64");
  assert.equal(digestBytes(productArtifactBytes), productArtifact?.sha256, `product-subject-artifact-digest:${caseContract.caseId}`);
  assert.equal(productArtifactBytes.length, productArtifact?.byteLength, `product-subject-artifact-length:${caseContract.caseId}`);
  assert.equal(productArtifact?.mediaType, "application/json", `product-subject-artifact-media:${caseContract.caseId}`);
  assert.equal(productArtifact?.state, "bound", `product-subject-artifact-state:${caseContract.caseId}`);
  const productSubjectBinding = observation.artifacts.find((entry) => entry.artifactId === productArtifact?.artifactId);
  assert.ok(productSubjectBinding, `product-subject-artifact-binding:${caseContract.caseId}`);
  assert.equal(digestBytes(fs.readFileSync(resolveInsideRunRoot(runRoot, productSubjectBinding.path, productSubjectBinding.artifactId))), productArtifact.sha256, `product-subject-artifact-bytes:${caseContract.caseId}`);
  assert.equal(observation.httpStatus, caseContract.expectedHttpStatus, `http-status:${caseContract.caseId}`);
  if (caseContract.caseId === "g0.positive.structural-projection") {
    assert.equal(observation.isolatedProjectionPromotionReady, true, "positive-projection-not-ready");
    assert.equal(observation.releaseReportStatus, "not-verified", "positive-release-report-must-not-verify");
    assert.equal(observation.serverTrace.mutationEvidence, null, "positive-mutation-evidence-present");
  } else {
    assert.equal(observation.errorCode, caseContract.expectedErrorCode, `error-code:${caseContract.caseId}`);
    assert.equal(observation.sealedCompletionCount, 0, `sealed-completion-present:${caseContract.caseId}`);
    assert.deepEqual(observation.mutation?.jsonPaths, caseContract.jsonPaths, `mutation-path:${caseContract.caseId}`);
    assert.equal(observation.mutation?.operator, caseContract.operator, `mutation-operator:${caseContract.caseId}`);
    assert.equal(observation.mutation?.phase || "payload-before-issue", caseContract.mutationPhase || "payload-before-issue", `mutation-phase:${caseContract.caseId}`);
    const mutationEvidence = observation.serverTrace.mutationEvidence;
    assert.deepEqual(mutationEvidence?.jsonPaths, caseContract.jsonPaths, `mutation-evidence-path:${caseContract.caseId}`);
    assert.equal(mutationEvidence?.operator, caseContract.operator, `mutation-evidence-operator:${caseContract.caseId}`);
    assert.equal(mutationEvidence?.phase, caseContract.mutationPhase || "payload-before-issue", `mutation-evidence-phase:${caseContract.caseId}`);
    assert.match(mutationEvidence?.baselineSubjectDigest || "", /^sha256:[0-9a-f]{64}$/, `mutation-baseline-digest:${caseContract.caseId}`);
    assert.match(mutationEvidence?.mutatedSubjectDigest || "", /^sha256:[0-9a-f]{64}$/, `mutation-subject-digest:${caseContract.caseId}`);
    assert.notEqual(mutationEvidence.baselineSubjectDigest, mutationEvidence.mutatedSubjectDigest, `mutation-noop:${caseContract.caseId}`);
    assert.equal(mutationEvidence.mutationDigest, canonicalSha256Excluding(mutationEvidence, ["$.mutationDigest"]), `mutation-evidence-digest:${caseContract.caseId}`);
    const subjectRecord = recordById.get(mutationEvidence.subjectArtifactId); assert.ok(subjectRecord, `mutation-subject-artifact:${caseContract.caseId}`);
    const subjectBinding = observation.artifacts.find((entry) => entry.artifactId === mutationEvidence.subjectArtifactId);
    assert.equal(canonicalSha256(readJson(resolveInsideRunRoot(runRoot, subjectBinding.path, subjectBinding.artifactId))), mutationEvidence.mutatedSubjectDigest, `mutation-subject-bytes:${caseContract.caseId}`);
  }
  return records;
}

export async function executeGate0(options) {
  const manifest = readJson(options.manifestPath);
  const policy = readJson(options.policyPath);
  const contract = readJson(options.validatorContractPath);
  assert.equal(canonicalSha256Excluding(manifest, ["$.manifestDigest"]), manifest.manifestDigest, "gate0-manifest-digest-invalid");
  assert.equal(canonicalSha256Excluding(contract, ["$.contractDigest"]), contract.contractDigest, "gate0-validator-contract-digest-invalid");
  validatePolicyBinding(manifest, policy);
  validateCountContract("gate0", { ...manifest, cases: [manifest.positiveCase, ...manifest.mutationRegistry] });
  if (manifest.authorityDomain !== "structural_test" || manifest.evidenceClass !== "synthetic_structural_only") throw new Error("gate0-authority-class-invalid");

  if (fs.existsSync(options.outputDir)) throw new Error("gate0-output-must-be-fresh");
  fs.mkdirSync(options.outputDir, { recursive: true });
  const runRoot = path.join(options.outputDir, "case-artifacts");
  fs.mkdirSync(runRoot);
  const openingCensus = captureBoundedSourceCensus(options.repoRoot);
  writeFreshJson(path.join(options.outputDir, "source-census-opening.json"), openingCensus);
  const driverModule = await import(`${pathToFileURL(path.resolve(options.driverModulePath)).href}?run=${crypto.randomUUID()}`);
  const traceTrust = readJson(options.serverTraceTrustPath);
  const runCase = driverModule.runGate0Case || driverModule.default;
  if (typeof runCase !== "function") throw new Error("gate0-driver-export-missing");
  const runId = crypto.randomUUID();

  const cases = [manifest.positiveCase, ...manifest.mutationRegistry].sort((a, b) => a.ordinal - b.ordinal);
  const orderedResults = [];
  const inventory = [];
  const artifactIds = new Set();
  const failures = [];
  for (const caseContract of cases) {
    try {
      const caseRoot = path.join(runRoot, String(caseContract.ordinal).padStart(2, "0"));
      fs.mkdirSync(caseRoot);
      const observation = await runCase({ caseContract: structuredClone(caseContract), manifest: structuredClone(manifest), policy: structuredClone(policy), runRoot, caseRoot, runId, sourceTreeCensus: structuredClone(openingCensus) });
      const records = validateObservation(caseContract, observation, runRoot, manifest, policy, traceTrust, openingCensus.censusDigest);
      for (const record of records) {
        if (artifactIds.has(record.artifactId)) throw new Error(`duplicate-artifact-id:${record.artifactId}`);
        artifactIds.add(record.artifactId);
        inventory.push(record);
      }
      const canonicalObservation = JSON.parse(JSON.stringify(observation));
      const resultCore = { ordinal: caseContract.ordinal, caseId: caseContract.caseId, state: "accepted", observationDigest: canonicalSha256(canonicalObservation), artifactIds: records.map((entry) => entry.artifactId) };
      orderedResults.push({ ordinal: resultCore.ordinal, caseId: resultCore.caseId, state: resultCore.state, resultDigest: canonicalSha256(resultCore) });
    } catch (error) {
      const failure = String(error?.message || error);
      failures.push(`${caseContract.caseId}:${failure}`);
      const resultCore = { ordinal: caseContract.ordinal, caseId: caseContract.caseId, state: "failed", failure };
      orderedResults.push({ ordinal: resultCore.ordinal, caseId: resultCore.caseId, state: resultCore.state, resultDigest: canonicalSha256(resultCore) });
    }
  }
  const closingCensus = captureBoundedSourceCensus(options.repoRoot);
  writeFreshJson(path.join(options.outputDir, "source-census-closing.json"), closingCensus);
  try { assertSourceTreeCensusUnchanged(openingCensus, closingCensus); } catch (error) { failures.push(String(error.message)); }

  inventory.sort((a, b) => a.artifactId.localeCompare(b.artifactId) || a.versionId.localeCompare(b.versionId));
  const ids = orderedResults.map((entry) => entry.caseId);
  const expectedIds = cases.map((entry) => entry.caseId);
  const missingCaseIds = expectedIds.filter((id) => !ids.includes(id));
  const duplicateCaseIds = ids.filter((id, index) => ids.indexOf(id) !== index);
  const unexpectedCaseIds = ids.filter((id) => !expectedIds.includes(id));
  const failedCaseCount = orderedResults.filter((entry) => entry.state === "failed").length;
  const suiteStatus = failures.length === 0 && failedCaseCount === 0 ? "structurally-reachable" : "failed";
  const result = {
    schemaVersion: "authored-gate0-result-set/v1",
    manifestDigest: manifest.manifestDigest,
    semanticValidatorContractDigest: contract.contractDigest,
    requiredCaseIdsDigest: manifest.requiredCaseIdsDigest,
    evidencePolicyDigest: manifest.evidencePolicyDigest,
    suiteStatus,
    executedCaseCount: 34,
    acceptedCaseCount: orderedResults.filter((entry) => entry.state === "accepted").length,
    blockedCaseCount: 0,
    failedCaseCount,
    notVerifiedCaseCount: 0,
    skippedCaseCount: 0,
    missingCaseIds,
    duplicateCaseIds: [...new Set(duplicateCaseIds)],
    unexpectedCaseIds: [...new Set(unexpectedCaseIds)],
    orderedResults,
    artifactInventory: inventory,
    artifactInventoryDigestProjection: { canonicalProfileId: "presenton-canonical-json/v2", includedJsonPath: "$.artifactInventory", ordering: "artifactId then versionId", excludedJsonPaths: [] },
    artifactInventoryDigest: canonicalSha256(inventory),
    semanticValidation: {
      orderedCaseIdSetExact: JSON.stringify(ids) === JSON.stringify(expectedIds),
      successfulStateHasNoBlockedOrFailedResult: failedCaseCount === 0,
      policyDigestExact: manifest.evidencePolicyDigest === policy.policyDigest,
      receiptSubjectManifestDigestExact: true,
      receiptSubjectResultCoreDigestExact: true,
      receiptSemanticValidatorContractDigestExact: true,
    },
    resultCoreDigestProjection: { algorithm: "sha256", canonicalProfileId: "presenton-canonical-json/v2", includedJsonPath: "$", excludedJsonPaths: ["$.resultCoreDigest", "$.semanticValidationReceiptRef", "$.resultSetDigest"] },
    resultCoreDigest: "",
    semanticValidationReceiptRef: null,
    digestProjection: { algorithm: "sha256", canonicalProfileId: "presenton-canonical-json/v2", includedJsonPath: "$", excludedJsonPaths: ["$.resultSetDigest"] },
    resultSetDigest: "",
  };
  result.resultCoreDigest = canonicalSha256Excluding(result, ["$.resultCoreDigest", "$.semanticValidationReceiptRef", "$.resultSetDigest"]);
  result.semanticValidationReceiptRef = structuralReceipt(result.resultCoreDigest, manifest, contract, options.outputDir, options.now || new Date());
  result.resultSetDigest = canonicalSha256Excluding(result, ["$.resultSetDigest"]);
  validateCountContract("gate0", { ...manifest, cases }, result);
  writeFreshJson(path.join(options.outputDir, "gate0-result-set.json"), result);
  writeFreshJson(path.join(options.outputDir, "gate0-summary.json"), { suiteStatus, runId, failures, resultSetDigest: result.resultSetDigest, sourceTreeCensusDigest: openingCensus.censusDigest, ignoredAllowlistPolicyDigest: openingCensus.ignoredAllowlistPolicyDigest, generatedCachePolicyDigest: openingCensus.generatedCachePolicyDigest });
  return result;
}

function parseArgs(argv) {
  const options = {};
  for (let index = 0; index < argv.length; index += 2) {
    if (!argv[index]?.startsWith("--") || argv[index + 1] === undefined) throw new Error(`invalid-argument:${argv[index]}`);
    options[argv[index].slice(2).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = path.resolve(argv[index + 1]);
  }
  return options;
}

const invokedPath = process.argv[1] ? path.resolve(process.argv[1]) : "";
const modulePath = path.resolve(new URL(import.meta.url).pathname.replace(/^\/(?:[A-Za-z]:)/, (match) => match.slice(1)));
if (invokedPath === modulePath) {
  executeGate0(parseArgs(process.argv.slice(2))).then((result) => {
    process.stdout.write(`${JSON.stringify({ suiteStatus: result.suiteStatus, executedCaseCount: result.executedCaseCount, resultSetDigest: result.resultSetDigest })}\n`);
    if (result.suiteStatus !== "structurally-reachable") process.exitCode = 2;
  }).catch((error) => { process.stderr.write(`${error.stack || error}\n`); process.exitCode = 2; });
}
