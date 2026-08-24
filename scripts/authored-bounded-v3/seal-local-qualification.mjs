import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import {
  canonicalSha256,
  canonicalSha256Excluding,
} from "../authored-bounded-release-contracts.mjs";
import { captureBoundedSourceCensus } from "../run-authored-bounded-gate0-v3.mjs";

const DIGEST = /^sha256:[0-9a-f]{64}$/;
const REMOTE_PHASES = [
  "g1.authority.ci-current-commit",
  "g1.authority.required-receipts",
  "g1.authority.completion-seal",
  "g1.authority.active-manifest-at-seal",
  "g1.projection.promotion-ready",
].sort();

function fail(code) {
  throw new Error(`bounded-v3-local-seal:${code}`);
}

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function fileBinding(artifactId, filePath) {
  const absolute = fs.realpathSync(path.resolve(filePath));
  const bytes = fs.readFileSync(absolute);
  if (!bytes.length) fail(`artifact-empty:${artifactId}`);
  return {
    artifactId,
    path: absolute,
    sha256: `sha256:${crypto.createHash("sha256").update(bytes).digest("hex")}`,
    byteLength: bytes.length,
    mediaType: "application/json",
  };
}

function writeFreshJson(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, { flag: "wx" });
}

function sourceIdentity(repoRoot) {
  const census = captureBoundedSourceCensus(repoRoot);
  if (!/^[0-9a-f]{40}$/.test(census.head || "") || !DIGEST.test(census.censusDigest || "") || !DIGEST.test(census.ignoredAllowlistPolicyDigest || "") || !DIGEST.test(census.generatedCachePolicyDigest || "")) fail("source-census-invalid");
  return {
    repoRoot: fs.realpathSync(repoRoot),
    sourceHeadCommit: census.head,
    sourceTreeCensusDigest: census.censusDigest,
    ignoredAllowlistPolicyDigest: census.ignoredAllowlistPolicyDigest,
    generatedCachePolicyDigest: census.generatedCachePolicyDigest,
    dirtyEntryCount: Array.isArray(census.dirtyPaths) ? census.dirtyPaths.length : 0,
  };
}

function verifyGateMinus1(result) {
  if (result.schemaVersion !== "authored-gate-minus1-result-set/v1" || result.suiteStatus !== "feasible" || result.executedCaseCount !== 18 || result.skippedCaseCount !== 0 || result.failedCaseCount !== 0 || result.blockedCaseCount !== 0) fail("gate-minus1-not-18-feasible");
  if (!DIGEST.test(result.resultSetDigest || "")) fail("gate-minus1-digest");
}

function verifyGate0(result) {
  if (result.schemaVersion !== "authored-gate0-result-set/v1" || result.suiteStatus !== "structurally-reachable" || result.executedCaseCount !== 34 || result.acceptedCaseCount !== 34 || result.skippedCaseCount !== 0 || result.failedCaseCount !== 0 || result.blockedCaseCount !== 0) fail("gate0-not-34-accepted");
  if (result.semanticValidation?.orderedCaseIdSetExact !== true || !DIGEST.test(result.resultSetDigest || "")) fail("gate0-binding");
}

function verifyGate1(result, summary, source) {
  if (result.schemaVersion !== "authored-gate1-result-set/v1" || result.suiteStatus !== "not-verified" || result.executedPhaseCount !== 37 || result.acceptedPhaseCount !== 32 || result.notVerifiedPhaseCount !== 5 || result.blockedPhaseCount !== 0 || result.failedPhaseCount !== 0) fail("gate1-state");
  if (!Array.isArray(result.artifactInventory) || result.artifactInventory.length !== 70 || new Set(result.artifactInventory.map((entry) => entry.artifactId)).size !== 70 || result.ledgerCellCount !== 384) fail("gate1-cardinality");
  const remote = result.orderedResults.filter((entry) => entry.state === "not-verified").map((entry) => entry.phaseId).sort();
  if (JSON.stringify(remote) !== JSON.stringify(REMOTE_PHASES)) fail(`gate1-remote-phase-set:${remote.join(",")}`);
  const deploymentBoundary = result.orderedResults.find((entry) => entry.phaseId === "g1.projection.deployment-not-authorized");
  if (deploymentBoundary?.state !== "accepted") fail("gate1-deployment-boundary");
  if (result.sourceHeadCommit !== source.sourceHeadCommit) fail(`gate1-source-head:${result.sourceHeadCommit}:${source.sourceHeadCommit}`);
  if (result.sourceTreeCensusDigest !== source.sourceTreeCensusDigest) fail(`gate1-source-census:${result.sourceTreeCensusDigest}:${source.sourceTreeCensusDigest}`);
  if (summary.finalResultSealed !== true || summary.executedPhaseCount !== 37 || summary.artifactCount !== 70 || summary.ledgerCellCount !== 384 || summary.resultSetDigest !== result.resultSetDigest) fail("gate1-final-summary");
  for (const key of ["artifactInventoryDigest", "ledgerDigest", "resultSetDigest"]) if (!DIGEST.test(result[key] || "")) fail(`gate1-digest:${key}`);
}

function verifyGate1Bundle(result, bindingsPath, bundleDir) {
  const bindings = readJson(bindingsPath);
  const realBundle = fs.realpathSync(path.resolve(bundleDir));
  if (bindings.schemaVersion !== "authored-gate1-artifact-bindings/v1" || bindings.authorityDomain !== "actual_product" || bindings.manifestDigest !== result.manifestDigest || bindings.artifactInventoryDigest !== result.artifactInventoryDigest || !Array.isArray(bindings.artifacts) || bindings.artifacts.length !== 70) fail("gate1-bindings-envelope");
  const resultById = new Map(result.artifactInventory.map((entry) => [entry.artifactId, entry]));
  const verified = [];
  for (const binding of bindings.artifacts) {
    const expected = resultById.get(binding.artifactId);
    if (!expected || typeof binding.locator !== "string") fail(`gate1-binding-id:${binding.artifactId}`);
    const absolute = fs.realpathSync(path.resolve(realBundle, binding.locator));
    if (absolute !== realBundle && !absolute.startsWith(`${realBundle}${path.sep}`)) fail(`gate1-binding-outside-bundle:${binding.artifactId}`);
    const bytes = fs.readFileSync(absolute);
    const actualDigest = `sha256:${crypto.createHash("sha256").update(bytes).digest("hex")}`;
    for (const field of ["versionId", "sha256", "byteLength", "mediaType"]) if (binding[field] !== expected[field]) fail(`gate1-binding-inventory:${binding.artifactId}:${field}`);
    if (actualDigest !== expected.sha256 || bytes.length !== expected.byteLength) fail(`gate1-binding-bytes:${binding.artifactId}`);
    verified.push({ artifactId: expected.artifactId, versionId: expected.versionId, sha256: actualDigest, byteLength: bytes.length, mediaType: expected.mediaType });
  }
  verified.sort((left, right) => left.artifactId.localeCompare(right.artifactId) || left.versionId.localeCompare(right.versionId));
  if (new Set(verified.map((entry) => entry.artifactId)).size !== 70 || canonicalSha256(verified) !== result.artifactInventoryDigest) fail("gate1-bundle-rehash-digest");
  return { bundleRoot: realBundle, bindingsDigest: fileBinding("gate1-artifact-bindings-json", bindingsPath).sha256, artifactCount: 70, artifactInventoryDigest: canonicalSha256(verified), verifiedArtifacts: verified };
}

function verifyCalibration(result, gate1) {
  if (result.schemaVersion !== "authored-bounded-v3-cross-renderer-calibration/v1" || result.state !== "accepted" || result.visualSlides?.length !== 8 || result.structuralSlides?.length !== 8 || result.thresholdFailureCount !== 0 || result.structuralFailureCount !== 0 || result.promotionReady !== false || result.deploymentAuthorized !== false) fail("calibration-state");
  if (result.presentationId !== gate1.presentationId || result.presentationRevision !== gate1.presentationRevision || result.generationAttemptId !== gate1.generationAttemptId || result.fidelityExportAttemptId !== gate1.fidelityExportAttemptId || result.hybridExportAttemptId !== gate1.hybridExportAttemptId) fail("calibration-gate1-identity");
}

export function sealLocalQualification(options) {
  const outputDir = path.resolve(options.outputDir);
  if (fs.existsSync(outputDir)) fail("output-must-be-fresh");
  const source = sourceIdentity(path.resolve(options.repoRoot));
  const inputs = {
    gateMinus1: readJson(options.gateMinus1Path),
    gate0: readJson(options.gate0Path),
    gate1: readJson(options.gate1Path),
    gate1Summary: readJson(options.gate1SummaryPath),
    calibration: readJson(options.calibrationPath),
  };
  verifyGateMinus1(inputs.gateMinus1);
  verifyGate0(inputs.gate0);
  verifyGate1(inputs.gate1, inputs.gate1Summary, source);
  verifyCalibration(inputs.calibration, inputs.gate1);
  const gate1ArtifactRehash = verifyGate1Bundle(inputs.gate1, options.gate1BindingsPath, options.gate1BundleDir);
  const artifacts = [
    fileBinding("gate-minus1-result-set-json", options.gateMinus1Path),
    fileBinding("gate0-result-set-json", options.gate0Path),
    fileBinding("gate1-result-set-json", options.gate1Path),
    fileBinding("gate1-result-summary-json", options.gate1SummaryPath),
    fileBinding("gate1-artifact-bindings-json", options.gate1BindingsPath),
    fileBinding("cross-renderer-calibration-json", options.calibrationPath),
  ].sort((left, right) => left.artifactId.localeCompare(right.artifactId));
  const core = {
    schemaVersion: "authored-bounded-v3-local-qualification-core/v1",
    authorityDomain: "local_non_promotable",
    state: "local-complete-non-promotable",
    source,
    presentationId: inputs.gate1.presentationId,
    presentationRevision: inputs.gate1.presentationRevision,
    generationAttemptId: inputs.gate1.generationAttemptId,
    fidelityExportAttemptId: inputs.gate1.fidelityExportAttemptId,
    hybridExportAttemptId: inputs.gate1.hybridExportAttemptId,
    gates: {
      gateMinus1: { suiteStatus: "feasible", executedCaseCount: 18, skippedCaseCount: 0, resultSetDigest: inputs.gateMinus1.resultSetDigest },
      gate0: { suiteStatus: "structurally-reachable", executedCaseCount: 34, acceptedCaseCount: 34, resultSetDigest: inputs.gate0.resultSetDigest },
      gate1: { suiteStatus: "not-verified", executedPhaseCount: 37, acceptedPhaseCount: 32, remoteNotVerifiedPhaseIds: REMOTE_PHASES, artifactCount: 70, ledgerCellCount: 384, resultSetDigest: inputs.gate1.resultSetDigest },
    },
    artifacts,
    artifactInventoryDigest: canonicalSha256(artifacts),
    gate1ArtifactRehash,
    crossRendererCalibrationDigest: inputs.calibration.resultDigest,
    externalCiAuthority: "not-verified",
    promotionReady: false,
    deploymentAuthorized: false,
    coreDigest: "",
  };
  core.coreDigest = canonicalSha256Excluding(core, ["$.coreDigest"]);
  const { publicKey, privateKey } = crypto.generateKeyPairSync("ed25519");
  const publicKeyDer = publicKey.export({ type: "spki", format: "der" });
  const signerKeyId = `sha256:${crypto.createHash("sha256").update(publicKeyDer).digest("hex")}`;
  const seal = {
    schemaVersion: "authored-bounded-v3-local-qualification-seal/v1",
    authorityDomain: "local_non_promotable",
    subjectCoreDigest: core.coreDigest,
    signerKeyId,
    publicKeySpkiBase64: publicKeyDer.toString("base64"),
    signatureBase64: crypto.sign(null, Buffer.from(core.coreDigest, "ascii"), privateKey).toString("base64"),
    promotionAuthorized: false,
    deploymentAuthorized: false,
    sealDigest: "",
  };
  seal.sealDigest = canonicalSha256Excluding(seal, ["$.sealDigest"]);
  if (!crypto.verify(null, Buffer.from(core.coreDigest, "ascii"), publicKey, Buffer.from(seal.signatureBase64, "base64"))) fail("signature-self-verification");
  const manifest = {
    schemaVersion: "authored-bounded-v3-local-qualification-manifest/v1",
    state: "sealed-local-non-promotable",
    coreDigest: core.coreDigest,
    sealDigest: seal.sealDigest,
    sourceHeadCommit: source.sourceHeadCommit,
    gateMinus1: "18/18",
    gate0: "34/34",
    gate1: "32 accepted + 5 remote not-verified / 37",
    artifactCount: 70,
    ledgerCellCount: 384,
    promotionReady: false,
    deploymentAuthorized: false,
    manifestDigest: "",
  };
  manifest.manifestDigest = canonicalSha256Excluding(manifest, ["$.manifestDigest"]);
  fs.mkdirSync(outputDir, { recursive: true });
  writeFreshJson(path.join(outputDir, "qualification-core.json"), core);
  writeFreshJson(path.join(outputDir, "qualification-local-seal.json"), seal);
  writeFreshJson(path.join(outputDir, "qualification-manifest.json"), manifest);
  return { outputDir, core, seal, manifest };
}

function parseArgs(argv) {
  const options = {};
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined) fail(`invalid-argument:${key}`);
    options[key.slice(2).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = value;
  }
  for (const key of ["repoRoot", "gateMinus1Path", "gate0Path", "gate1Path", "gate1SummaryPath", "gate1BindingsPath", "gate1BundleDir", "calibrationPath", "outputDir"]) if (!options[key]) fail(`argument-missing:${key}`);
  return options;
}

const invoked = process.argv[1] ? path.resolve(process.argv[1]) : "";
const self = path.resolve(new URL(import.meta.url).pathname.replace(/^\/(?:[A-Za-z]:)/, (match) => match.slice(1)));
if (invoked === self) {
  try {
    const result = sealLocalQualification(parseArgs(process.argv.slice(2)));
    process.stdout.write(`${JSON.stringify({ state: result.manifest.state, outputDir: result.outputDir, gateMinus1: result.manifest.gateMinus1, gate0: result.manifest.gate0, gate1: result.manifest.gate1, artifactCount: result.manifest.artifactCount, ledgerCellCount: result.manifest.ledgerCellCount, manifestDigest: result.manifest.manifestDigest, promotionReady: false, deploymentAuthorized: false })}\n`);
  } catch (error) {
    process.stderr.write(`${error.stack || error}\n`);
    process.exitCode = 2;
  }
}
