import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

import { canonicalSha256, validateCountContract, validateGate1Ledger } from "./authored-bounded-release-contracts.mjs";

function readJson(filePath) { return JSON.parse(fs.readFileSync(filePath, "utf8")); }
function writeFreshJson(filePath, value) { fs.mkdirSync(path.dirname(filePath), { recursive: true }); fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, { flag: "wx" }); }
function digestBytes(bytes) { return `sha256:${crypto.createHash("sha256").update(bytes).digest("hex")}`; }
function extension(mediaType) { return mediaType === "image/png" ? ".png" : mediaType === "application/vnd.openxmlformats-officedocument.presentationml.presentation" ? ".pptx" : ".json"; }
function requireFastApiAuthorityArtifact(artifactId, bytes) {
  if (!["evidence-graph-root-json", "completion-seal-json"].includes(artifactId)) return;
  let value; try { value = JSON.parse(bytes.toString("utf8")); } catch { throw new Error(`gate1-fastapi-provenance-json-invalid:${artifactId}`); }
  const isExchange = (entry, method, endpoint) => entry && entry.method === method && entry.endpoint === endpoint && Number.isInteger(entry.httpStatus) && entry.requestDigest === canonicalSha256(entry.request) && entry.responseDigest === canonicalSha256(entry.response);
  if (artifactId === "evidence-graph-root-json") {
    const nonce = value.readback?.response?.executionNonce; const query = `?executionNonce=${encodeURIComponent(nonce || "")}`; const nonceRequest = (entry) => canonicalSha256(entry?.request) === canonicalSha256({ executionNonce: nonce });
    if (typeof nonce !== "string" || nonce.length < 16 || value.schemaVersion !== "authored-fastapi-evidence-graph-root-provenance/v1" || value.authorityDomain !== "actual_product" || !isExchange(value.create, "POST", "/api/v1/ppt/authored-emission/evidence-graph-roots-v3") || !isExchange(value.readback, "GET", `/api/v1/ppt/authored-emission/evidence-graph-roots-v3/${value.create?.response?.graphRootId}${query}`) || !nonceRequest(value.readback) || !isExchange(value.comQualificationReadback, "GET", `/api/v1/ppt/authored-emission/com-qualification-jobs-v3/${value.create?.response?.comQualificationJobId}${query}`) || !nonceRequest(value.comQualificationReadback) || value.comQualificationReadback?.response?.executionNonce !== nonce || value.create.httpStatus !== 201 || value.readback.httpStatus !== 200 || value.comQualificationReadback.httpStatus !== 200 || !value.create?.response?.persistedRecordId || value.create.response.persistedRecordId !== value.create.response.graphRootId || value.comQualificationReadback?.response?.state !== "sealed") throw new Error("gate1-fastapi-root-provenance-required");
    return;
  }
  const completionId = value.create?.response?.completionId;
  const nonce = value.readback?.response?.executionNonce; const query = `?executionNonce=${encodeURIComponent(nonce || "")}`;
  const sealValid = value.seal === null || (isExchange(value.seal, "POST", `/api/v1/ppt/authored-emission/evidence-bundle-completions-v3/${completionId}/seal`) && value.seal.httpStatus === 200);
  if (typeof nonce !== "string" || nonce.length < 16 || value.schemaVersion !== "authored-fastapi-evidence-bundle-completion-provenance/v1" || value.authorityDomain !== "actual_product" || !isExchange(value.create, "POST", "/api/v1/ppt/authored-emission/evidence-bundle-completions-v3") || value.create.httpStatus !== 201 || !isExchange(value.readback, "GET", `/api/v1/ppt/authored-emission/evidence-bundle-completions-v3/${completionId}${query}`) || canonicalSha256(value.readback?.request) !== canonicalSha256({ executionNonce: nonce }) || value.readback.httpStatus !== 200 || !sealValid || !value.create?.response?.persistedRecordId || value.create.response.persistedRecordId !== completionId) throw new Error("gate1-fastapi-completion-provenance-required");
}

export function assembleGate1Bundle(options) {
  const manifest = readJson(options.manifestPath); validateCountContract("gate1", manifest);
  const sourceMap = readJson(options.artifactSourceMapPath); const manual = readJson(options.manualObservationsPath);
  if (sourceMap.schemaVersion !== "authored-gate1-actual-artifact-source-map/v1" || sourceMap.authorityDomain !== "actual_product" || !Array.isArray(sourceMap.artifacts) || !Array.isArray(sourceMap.phaseEvidence)) throw new Error("gate1-source-map-invalid");
  if (manual.schemaVersion !== "authored-gate1-manual-observations/v1" || manual.authorityDomain !== "actual_product" || !Array.isArray(manual.cells)) throw new Error("gate1-manual-observations-invalid");
  for (const key of ["presentationId", "generationAttemptId", "fidelityExportAttemptId", "hybridExportAttemptId"]) if (typeof sourceMap[key] !== "string" || sourceMap[key].length < 8) throw new Error(`gate1-source-map-identity-missing:${key}`);
  if (!Number.isInteger(sourceMap.presentationRevision) || sourceMap.presentationRevision < 1 || manual.presentationId !== sourceMap.presentationId || manual.presentationRevision !== sourceMap.presentationRevision || manual.generationAttemptId !== sourceMap.generationAttemptId || manual.fidelityExportAttemptId !== sourceMap.fidelityExportAttemptId || manual.hybridExportAttemptId !== sourceMap.hybridExportAttemptId) throw new Error("gate1-manual-observation-attempt-mismatch");
  validateGate1Ledger(manual.cells, manifest.expectedSlideCount);
  const reviewNow = Date.now();
  if (manual.cells.some((cell) => { const reviewed = Date.parse(cell?.reviewedAt); return cell.state !== "accepted" || typeof cell.reviewerId !== "string" || !/^[A-Za-z0-9._/-]{3,128}$/.test(cell.reviewerId) || !Number.isFinite(reviewed) || reviewed > reviewNow + 30_000 || reviewNow - reviewed > 900_000 || !Array.isArray(cell.evidenceArtifactIds); })) throw new Error("gate1-manual-observation-unreviewed");
  for (const cell of manual.cells) { const number = String(cell.slideNumber).padStart(2, "0"); const expected = [`${cell.exportMode}-browser-slide-${number}-png`, `${cell.exportMode}-${cell.comPhase}-slide-${number}-png`, `${cell.exportMode}-${cell.comPhase}-com-json`].sort(); if (JSON.stringify([...cell.evidenceArtifactIds].sort()) !== JSON.stringify(expected)) throw new Error(`gate1-manual-observation-evidence-mismatch:${cell.slideNumber}:${cell.exportMode}:${cell.comPhase}:${cell.defectFieldId}`); }
  if (fs.existsSync(options.bundleDir) || fs.existsSync(options.outputDir)) throw new Error("gate1-assembly-output-must-be-fresh");
  fs.mkdirSync(options.bundleDir, { recursive: true }); fs.mkdirSync(options.outputDir, { recursive: true });

  const requiredWithoutLedger = manifest.requiredArtifactIds.filter((id) => id !== "manual-ledger-json");
  const sourceIds = sourceMap.artifacts.map((entry) => entry.artifactId);
  if (new Set(sourceIds).size !== sourceIds.length || sourceIds.length !== 69 || requiredWithoutLedger.some((id) => !sourceIds.includes(id)) || sourceIds.some((id) => !requiredWithoutLedger.includes(id))) throw new Error("gate1-source-artifact-id-set-mismatch");
  const bindings = [];
  for (const source of sourceMap.artifacts) {
    const absolute = fs.realpathSync(path.resolve(source.path)); const before = fs.readFileSync(absolute); const sha256 = digestBytes(before);
    if (source.sha256 !== sha256 || source.byteLength !== before.length || typeof source.versionId !== "string" || !source.versionId || typeof source.mediaType !== "string") throw new Error(`gate1-source-artifact-binding-invalid:${source.artifactId}`);
    requireFastApiAuthorityArtifact(source.artifactId, before);
    const locator = `${source.artifactId}${extension(source.mediaType)}`; const destination = path.join(options.bundleDir, locator);
    fs.copyFileSync(absolute, destination, fs.constants.COPYFILE_EXCL); const after = fs.readFileSync(destination);
    if (digestBytes(after) !== sha256 || after.length !== before.length) throw new Error(`gate1-source-artifact-copy-mismatch:${source.artifactId}`);
    bindings.push({ artifactId: source.artifactId, versionId: source.versionId, locator, sha256, byteLength: after.length, mediaType: source.mediaType });
  }
  const ledger = { schemaVersion: "authored-manual-ledger/v1", authorityDomain: "actual_product", presentationId: sourceMap.presentationId, presentationRevision: sourceMap.presentationRevision, generationAttemptId: sourceMap.generationAttemptId, fidelityExportAttemptId: sourceMap.fidelityExportAttemptId, hybridExportAttemptId: sourceMap.hybridExportAttemptId, cells: manual.cells };
  const ledgerLocator = "manual-ledger-json.json"; writeFreshJson(path.join(options.bundleDir, ledgerLocator), ledger); const ledgerBytes = fs.readFileSync(path.join(options.bundleDir, ledgerLocator));
  bindings.push({ artifactId: "manual-ledger-json", versionId: `sha256-${digestBytes(ledgerBytes).slice(7)}`, locator: ledgerLocator, sha256: digestBytes(ledgerBytes), byteLength: ledgerBytes.length, mediaType: "application/json" });
  bindings.sort((a, b) => manifest.requiredArtifactIds.indexOf(a.artifactId) - manifest.requiredArtifactIds.indexOf(b.artifactId));

  const phaseIds = sourceMap.phaseEvidence.map((entry) => entry.phaseId); const requiredPhaseIds = manifest.phases.map((entry) => entry.phaseId);
  if (new Set(phaseIds).size !== 37 || phaseIds.length !== 37 || requiredPhaseIds.some((id) => !phaseIds.includes(id))) throw new Error("gate1-source-phase-id-set-mismatch");
  const artifactIds = new Set(bindings.map((entry) => entry.artifactId));
  const phases = manifest.phases.map((phase) => {
    const source = sourceMap.phaseEvidence.find((entry) => entry.phaseId === phase.phaseId);
    if (source.ordinal !== phase.ordinal || !["accepted", "not-verified", "blocked", "failed"].includes(source.observedState) || !Array.isArray(source.evidenceArtifactIds) || source.evidenceArtifactIds.length === 0 || source.evidenceArtifactIds.some((id) => !artifactIds.has(id))) throw new Error(`gate1-source-phase-evidence-invalid:${phase.phaseId}`);
    return { ordinal: phase.ordinal, phaseId: phase.phaseId, authorityDomain: "actual_product", evidenceClass: "actual_product_release_qualification", state: source.observedState, evidenceArtifactIds: source.evidenceArtifactIds };
  });
  const inventoryProjection = bindings.map(({ artifactId, versionId, sha256, byteLength, mediaType }) => ({ artifactId, versionId, sha256, byteLength, mediaType })).sort((a, b) => a.artifactId.localeCompare(b.artifactId) || a.versionId.localeCompare(b.versionId));
  writeFreshJson(path.join(options.outputDir, "artifact-bindings.json"), { schemaVersion: "authored-gate1-artifact-bindings/v1", authorityDomain: "actual_product", manifestDigest: manifest.manifestDigest, requiredArtifactIds: manifest.requiredArtifactIds, requiredArtifactIdsDigest: manifest.requiredArtifactIdsDigest, artifactInventoryDigest: canonicalSha256(inventoryProjection), presentationId: sourceMap.presentationId, presentationRevision: sourceMap.presentationRevision, generationAttemptId: sourceMap.generationAttemptId, fidelityExportAttemptId: sourceMap.fidelityExportAttemptId, hybridExportAttemptId: sourceMap.hybridExportAttemptId, artifacts: bindings });
  writeFreshJson(path.join(options.outputDir, "phase-observations.json"), { schemaVersion: "authored-gate1-phase-observations/v1", authorityDomain: "actual_product", presentationId: sourceMap.presentationId, presentationRevision: sourceMap.presentationRevision, generationAttemptId: sourceMap.generationAttemptId, fidelityExportAttemptId: sourceMap.fidelityExportAttemptId, hybridExportAttemptId: sourceMap.hybridExportAttemptId, phases });
  writeFreshJson(path.join(options.outputDir, "assembly-summary.json"), { schemaVersion: "authored-gate1-assembly-summary/v1", authorityDomain: "actual_product", artifactCount: bindings.length, phaseCount: phases.length, ledgerCellCount: manual.cells.length, bundleDir: path.resolve(options.bundleDir) });
  return { artifactCount: 70, phaseCount: 37, ledgerCellCount: 384 };
}

function parseArgs(argv) { const options = {}; for (let index = 0; index < argv.length; index += 2) { if (!argv[index]?.startsWith("--") || argv[index + 1] === undefined) throw new Error(`invalid-argument:${argv[index]}`); options[argv[index].slice(2).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = path.resolve(argv[index + 1]); } return options; }
const invokedPath = process.argv[1] ? path.resolve(process.argv[1]) : ""; const modulePath = path.resolve(new URL(import.meta.url).pathname.replace(/^\/(?:[A-Za-z]:)/, (match) => match.slice(1)));
if (invokedPath === modulePath) { try { process.stdout.write(`${JSON.stringify(assembleGate1Bundle(parseArgs(process.argv.slice(2))))}\n`); } catch (error) { process.stderr.write(`${error.stack || error}\n`); process.exitCode = 2; } }
