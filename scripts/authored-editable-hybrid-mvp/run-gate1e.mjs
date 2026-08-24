import fs from "node:fs";
import path from "node:path";

import {
  artifactInventory,
  assertManifestDigest,
  canonicalSha256,
  digestExcluding,
  ensureFreshDirectory,
  loadArtifact,
  parsePairs,
  pngDimensions,
  pptxSlideCount,
  readJson,
  sameStrings,
  sha256Bytes,
  sortedUniqueStrings,
  verifySignedReceipt,
  writeFreshJson,
} from "./lib.mjs";

function parseJson(record, label) {
  try { return JSON.parse(record.bytes.toString("utf8")); } catch { throw new Error(`gate1e-json-invalid:${label}`); }
}

function collectQids(value, target = new Set()) {
  if (Array.isArray(value)) { for (const item of value) collectQids(item, target); return target; }
  if (!value || typeof value !== "object") return target;
  for (const key of ["qid", "dataQid", "elementId"]) if (typeof value[key] === "string" && value[key]) target.add(value[key]);
  if (Array.isArray(value.qids)) for (const qid of value.qids) if (typeof qid === "string" && qid) target.add(qid);
  for (const child of Object.values(value)) collectQids(child, target);
  return target;
}

function attemptPins(value) {
  for (const key of ["presentationId", "generationAttemptId", "hybridExportAttemptId", "sceneDigest", "activeManifestDigest", "gateMinus1eResultSetDigest", "gate0eResultSetDigest", "sourceHeadCommit", "sourceCensusDigest"]) {
    if (typeof value?.[key] !== "string" || !value[key]) throw new Error(`gate1e-attempt-pin-missing:${key}`);
  }
  if (!Number.isInteger(value.presentationRevision) || value.presentationRevision < 1) throw new Error("gate1e-presentation-revision-invalid");
  if (!Number.isInteger(value.sourceDirtyFileCount) || value.sourceDirtyFileCount < 1) throw new Error("gate1e-source-dirty-file-count-invalid");
  value.expectedQids = sortedUniqueStrings(value.expectedQids, "gate1e-expected-qids");
  return value;
}

function exactAttempt(value, attempt, label, fields = ["presentationId", "presentationRevision", "generationAttemptId", "hybridExportAttemptId"]) {
  for (const field of fields) if (value?.[field] !== attempt[field]) throw new Error(`gate1e-attempt-mismatch:${label}:${field}`);
}

function accepted(value, field, label) {
  if (value?.[field] !== "accepted" && value?.[field] !== true) throw new Error(`gate1e-status-not-accepted:${label}:${field}`);
}

function validateQuality(quality, profile, attempt) {
  exactAttempt(quality.atomicBinding || quality, attempt, "quality", ["presentationId", "presentationRevision"]);
  if (quality.exportAttemptId !== attempt.hybridExportAttemptId && quality.atomicBinding?.exportAttemptId !== attempt.hybridExportAttemptId) throw new Error("gate1e-quality-export-attempt-mismatch");
  const expected = profile.acceptance;
  for (const [field, value] of Object.entries(expected)) {
    if (field === "minimumNativeTextElementsPerSlide") continue;
    if (quality[field] !== value) throw new Error(`gate1e-quality-count-mismatch:${field}`);
  }
  if (!Array.isArray(quality.slides) || quality.slides.length !== 5 || quality.slides.some((slide) => slide.nativeTextElements < expected.minimumNativeTextElementsPerSlide)) throw new Error("gate1e-quality-per-slide-native-text-invalid");
}

function validateProcessDelta(value, attempt) {
  exactAttempt(value, attempt, "process-delta", ["presentationId", "presentationRevision", "hybridExportAttemptId"]);
  if (value.schemaVersion !== "authored-powerpoint-owned-process-delta/v1" || !Array.isArray(value.baselineProcesses) || !Array.isArray(value.ownedProcessIds) || !Array.isArray(value.adoptedProcessIds) || !Array.isArray(value.finalProcesses)) throw new Error("gate1e-process-delta-schema-invalid");
  if (value.forcedNewInstanceSwitch !== "/x" || value.baselineBindingAttempted !== false || value.ownedApplicationBindingExact !== true || value.adoptedProcessIds.length !== 0) throw new Error("gate1e-process-delta-binding-not-owned");
  if (value.unexpectedPowerPointSurvivorCount !== 0 || value.ownedPowerPointSurvivorCount !== 0 || value.cleanupConfirmed !== true || value.unrelatedBaselinePreserved !== true || value.baselineProcessIdentityPreserved !== true || value.baselinePresentationWindowsPreserved !== true || value.unrelatedBaselineTerminatedByWorker !== false) throw new Error("gate1e-process-delta-not-clean");
  const baseline = new Set(value.baselineProcesses.map((entry) => entry.processId));
  if (value.ownedProcessIds.length !== 1 || value.ownedProcessIds.some((pid) => baseline.has(pid)) || value.adoptedProcessIds.some((pid) => baseline.has(pid))) throw new Error("gate1e-process-delta-baseline-adopted");
}

function validateSidecar(value, attempt, profile) {
  exactAttempt(value, attempt, "sidecar");
  if (value.schemaVersion !== "authored-editable-hybrid-attempt-sidecar/v1" || value.profileId !== profile.profileId) throw new Error("gate1e-sidecar-schema-invalid");
  for (const field of ["sceneMutationStatus", "nativeAdmissionStatus", "packageReadbackStatus", "comFirstOpenStatus", "comSaveCopyStatus", "comReopenStatus", "renderHashStabilityStatus", "visualParityStatus", "repairDialogStatus"]) accepted(value, field, "sidecar");
  if (value.unexpectedPowerPointSurvivorCount !== 0 || value.nativeEditableReady !== true || value.editableSlides !== 5 || value.rasterFallbackElements !== 2 || value.nativeRasterDuplicateOwners !== 0 || value.missingOrGhostQids !== 0) throw new Error("gate1e-sidecar-acceptance-invalid");
}

function validateJsonArtifacts(byId, attempt, profile) {
  const source = parseJson(byId.get("source-prompt-json"), "source-prompt-json");
  const census = source.sourceCensus;
  if (census?.schemaVersion !== "authored-editable-hybrid-source-census/v1" || census.sourceHeadCommit !== attempt.sourceHeadCommit || census.workingTreeDirty !== true || census.fileCount !== attempt.sourceDirtyFileCount || !Array.isArray(census.files) || census.files.length !== census.fileCount || census.inventoryDigest !== canonicalSha256(census.files)) throw new Error("gate1e-source-census-invalid");
  const { sourceCensusDigest, ...censusCore } = census;
  if (sourceCensusDigest !== attempt.sourceCensusDigest || sourceCensusDigest !== canonicalSha256(censusCore)) throw new Error("gate1e-source-census-digest-invalid");
  for (const entry of census.files) {
    if (typeof entry?.path !== "string" || !entry.path || !Number.isInteger(entry.byteLength) || entry.byteLength < 0 || (entry.status === "deleted" ? entry.sha256 !== null : typeof entry.sha256 !== "string" || !entry.sha256.startsWith("sha256:"))) throw new Error("gate1e-source-census-entry-invalid");
  }
  const prerequisites = source.gatePrerequisites;
  const gateMinus1 = prerequisites?.gateMinus1eResultSet;
  const gate0 = prerequisites?.gate0eResultSet;
  if (prerequisites?.schemaVersion !== "authored-editable-hybrid-gate-prerequisites/v1" || gateMinus1?.resultSetDigest !== attempt.gateMinus1eResultSetDigest || gate0?.resultSetDigest !== attempt.gate0eResultSetDigest || gate0?.attempt?.gateMinus1eResultSetDigest !== gateMinus1?.resultSetDigest) throw new Error("gate1e-prerequisite-digest-chain-invalid");
  for (const field of ["presentationId", "presentationRevision", "generationAttemptId", "hybridExportAttemptId", "sceneDigest", "activeManifestDigest", "sourceHeadCommit"]) {
    if (gateMinus1?.attempt?.[field] !== attempt[field] || gate0?.attempt?.[field] !== attempt[field]) throw new Error(`gate1e-prerequisite-attempt-mismatch:${field}`);
  }
  const semantic = parseJson(byId.get("semantic-source-json"), "semantic-source-json");
  if (!sameStrings(collectQids(semantic), attempt.expectedQids)) throw new Error("gate1e-semantic-qid-set-mismatch");
  const sceneAfter = parseJson(byId.get("scene-after-json"), "scene-after-json");
  if (sceneAfter.schemaVersion !== "deck-scene-ir/v1" || (sceneAfter.sceneDigest ?? sceneAfter.digest) !== attempt.sceneDigest || !sameStrings(collectQids(sceneAfter), attempt.expectedQids)) throw new Error("gate1e-scene-after-binding-invalid");
  const sceneBefore = parseJson(byId.get("scene-before-json"), "scene-before-json");
  if (sceneBefore.schemaVersion !== "deck-scene-ir/v1" || canonicalSha256(sceneBefore) === canonicalSha256(sceneAfter)) throw new Error("gate1e-scene-mutation-not-observed");
  const mutation = parseJson(byId.get("scene-mutation-receipt-json"), "scene-mutation-receipt-json");
  exactAttempt(mutation, attempt, "scene-mutation", ["presentationId", "presentationRevision", "generationAttemptId"]);
  if (mutation.schemaVersion !== "scene-mutation-result/v1" || mutation.sceneDigest !== attempt.sceneDigest || mutation.publishedByCas !== true || mutation.state !== "accepted") throw new Error("gate1e-scene-mutation-receipt-invalid");
  const active = parseJson(byId.get("active-manifest-json"), "active-manifest-json");
  if ((active.manifestDigest ?? active.digest) !== attempt.activeManifestDigest) throw new Error("gate1e-active-manifest-binding-invalid");
  const admission = parseJson(byId.get("native-admission-json"), "native-admission-json");
  exactAttempt(admission, attempt, "native-admission", ["presentationId", "presentationRevision", "generationAttemptId"]);
  if (admission.schemaVersion !== "authored-editable-hybrid-native-admission/v1" || admission.nativeEditableReady !== true || admission.unexpectedRasterFallbacks !== 0 || admission.nativeRasterDuplicateOwners !== 0 || admission.missingOrGhostQids !== 0 || !sameStrings(admission.qids, attempt.expectedQids)) throw new Error("gate1e-native-admission-invalid");
  validateQuality(parseJson(byId.get("hybrid-quality-json"), "hybrid-quality-json"), profile, attempt);

  const inputPptx = byId.get("hybrid-input-pptx"); const savedPptx = byId.get("hybrid-saved-pptx");
  for (const [id, state, packageRecord] of [["input-ooxml-readback-json", "input", inputPptx], ["saved-ooxml-readback-json", "saved", savedPptx]]) {
    const readback = parseJson(byId.get(id), id); exactAttempt(readback, attempt, id, ["presentationId", "presentationRevision", "hybridExportAttemptId"]);
    if (readback.schemaVersion !== "authored-editable-hybrid-ooxml-readback/v1" || readback.packageState !== state || readback.slideCount !== 5 || readback.packageDigest !== packageRecord.sha256 || !sameStrings(readback.qids, attempt.expectedQids) || readback.repairRequired !== false) throw new Error(`gate1e-ooxml-readback-invalid:${state}`);
  }
  for (const [id, phase] of [["first-open-com-json", "first-open"], ["reopen-com-json", "reopen"]]) {
    const snapshot = parseJson(byId.get(id), id); exactAttempt(snapshot, attempt, id, ["presentationId", "presentationRevision", "hybridExportAttemptId"]);
    if (snapshot.schemaVersion !== "authored-editable-hybrid-com-snapshot/v1" || snapshot.phase !== phase || snapshot.readbackPassed !== true || snapshot.nativeEditsPreserved !== true || !sameStrings(snapshot.qids, attempt.expectedQids)) throw new Error(`gate1e-com-snapshot-invalid:${phase}`);
  }
  const report = parseJson(byId.get("powerpoint-com-report-json"), "powerpoint-com-report-json");
  const reportPresentation = (report.Presentations || []).find((entry) => entry.ExportMode === "hybrid" && entry.ExportAttemptId === attempt.hybridExportAttemptId);
  if (!reportPresentation || reportPresentation.ReadbackPassed !== true || reportPresentation.ExportedPngs !== 5 || reportPresentation.RepairWarningObserved !== false) throw new Error("gate1e-powerpoint-report-invalid");
  const repair = parseJson(byId.get("repair-observation-json"), "repair-observation-json");
  if (repair.repairDialogObserved !== false && repair.RepairDialogObserved !== false) throw new Error("gate1e-repair-dialog-observed");
  validateProcessDelta(parseJson(byId.get("powerpoint-process-delta-json"), "powerpoint-process-delta-json"), attempt);
  const visual = parseJson(byId.get("independent-visual-metrics-json"), "independent-visual-metrics-json"); exactAttempt(visual, attempt, "visual-metrics", ["presentationId", "presentationRevision", "generationAttemptId", "hybridExportAttemptId"]);
  if (!Array.isArray(visual.comparisons) || visual.comparisons.length !== 15 || visual.comparisons.some((entry) => entry.state !== "accepted" || entry.thresholdPassed !== true)) throw new Error("gate1e-visual-metrics-invalid");
  validateSidecar(parseJson(byId.get("attempt-sidecar-json"), "attempt-sidecar-json"), attempt, profile);
  const completion = parseJson(byId.get("completion-seal-json"), "completion-seal-json"); exactAttempt(completion, attempt, "completion-seal");
  if (completion.schemaVersion !== "authored-editable-hybrid-mvp-completion/v1" || completion.state !== "sealed" || completion.casBefore !== 0 || completion.casAfter !== 1 || completion.nativeEditableReady !== true || completion.promotionReady !== false || completion.deploymentAuthorized !== false) throw new Error("gate1e-completion-seal-invalid");
}

export function collectGate1E(options) {
  const manifest = readJson(path.resolve(options.manifestPath));
  const profile = readJson(path.resolve(options.profilePath));
  const sourceMap = readJson(path.resolve(options.artifactSourceMapPath));
  assertManifestDigest(manifest, "gate1e"); assertManifestDigest(profile, "editable-hybrid-profile");
  if (manifest.schemaVersion !== "authored-editable-hybrid-gate1e-artifact-manifest/v1" || manifest.expectedSlideCount !== 5 || manifest.requiredArtifactCount !== 35 || manifest.noSkip !== true) throw new Error("gate1e-manifest-shape-invalid");
  if (sourceMap.schemaVersion !== "authored-editable-hybrid-gate1e-artifact-source-map/v1" || sourceMap.authorityDomain !== "actual_product") throw new Error("gate1e-source-map-invalid");
  const attempt = attemptPins(sourceMap.attempt);
  const records = sourceMap.artifacts.map((binding) => loadArtifact(binding, "gate1e-artifact"));
  const byId = new Map(records.map((entry) => [entry.artifactId, entry]));
  if (byId.size !== 35 || records.length !== 35 || !sameStrings(byId.keys(), manifest.requiredArtifactIds)) throw new Error("gate1e-artifact-id-set-mismatch");
  if (pptxSlideCount(byId.get("hybrid-input-pptx").bytes, "hybrid-input") !== 5 || pptxSlideCount(byId.get("hybrid-saved-pptx").bytes, "hybrid-saved") !== 5) throw new Error("gate1e-pptx-slide-count-invalid");
  const pngRecords = records.filter((entry) => entry.artifactId.endsWith("-png"));
  if (pngRecords.length !== 15 || pngRecords.some((entry) => { const dimensions = pngDimensions(entry.bytes, entry.artifactId); return dimensions.width !== 1280 || dimensions.height !== 720; })) throw new Error("gate1e-png-policy-invalid");
  validateJsonArtifacts(byId, attempt, profile);
  ensureFreshDirectory(path.resolve(options.outputDir), "gate1e-output");
  const inventory = artifactInventory(records); const inventoryDigest = canonicalSha256(inventory);
  const result = {
    schemaVersion: "authored-editable-hybrid-gate1e-result-set/v1",
    profileId: profile.profileId,
    authorityDomain: "actual_product",
    manifestDigest: manifest.manifestDigest,
    profileDigest: profile.manifestDigest,
    attempt,
    suiteStatus: "accepted",
    executedCount: 35,
    skippedCount: 0,
    orderedResults: manifest.requiredArtifactIds.map((artifactId, index) => ({ ordinal: index + 1, artifactId, state: "accepted", artifactDigest: byId.get(artifactId).sha256 })),
    artifactInventory: inventory,
    artifactInventoryDigest: inventoryDigest,
    semanticValidationReceiptRef: null,
    resultCoreDigest: "",
    resultSetDigest: "",
  };
  result.resultCoreDigest = digestExcluding(result, ["semanticValidationReceiptRef", "resultCoreDigest", "resultSetDigest"]);
  writeFreshJson(path.join(path.resolve(options.outputDir), "gate1e-result-core.json"), result);
  writeFreshJson(path.join(path.resolve(options.outputDir), "gate1e-validation-request.json"), { schemaVersion: "authored-editable-hybrid-validation-request/v1", subjectManifestDigest: result.manifestDigest, subjectProfileDigest: result.profileDigest, subjectResultCoreDigest: result.resultCoreDigest, subjectArtifactInventoryDigest: result.artifactInventoryDigest, ...attempt });
  return result;
}

export function sealGate1E(options) {
  const core = readJson(path.resolve(options.resultCorePath)); const receiptPath = path.resolve(options.receiptPath);
  const receipt = readJson(receiptPath); const trust = readJson(path.resolve(options.trustPath));
  if (core.semanticValidationReceiptRef !== null || core.resultSetDigest !== "" || core.resultCoreDigest !== digestExcluding(core, ["semanticValidationReceiptRef", "resultCoreDigest", "resultSetDigest"])) throw new Error("gate1e-result-core-invalid");
  verifySignedReceipt(receipt, trust, { subjectManifestDigest: core.manifestDigest, subjectProfileDigest: core.profileDigest, subjectResultCoreDigest: core.resultCoreDigest, subjectArtifactInventoryDigest: core.artifactInventoryDigest, presentationId: core.attempt.presentationId, presentationRevision: core.attempt.presentationRevision, generationAttemptId: core.attempt.generationAttemptId, hybridExportAttemptId: core.attempt.hybridExportAttemptId });
  const issuedAt = Date.parse(receipt.issuedAt); const expiresAt = Date.parse(receipt.expiresAt); const now = Date.now();
  if (!Number.isFinite(issuedAt) || !Number.isFinite(expiresAt) || issuedAt > now + 30_000 || expiresAt <= now || expiresAt - issuedAt > 1_800_000) throw new Error("gate1e-receipt-freshness-invalid");
  const bytes = fs.readFileSync(receiptPath); const result = structuredClone(core);
  result.semanticValidationReceiptRef = { artifactId: "gate1e-semantic-validation-receipt", versionId: `sha256-${sha256Bytes(bytes).slice(7)}`, sha256: sha256Bytes(bytes), byteLength: bytes.length, mediaType: "application/json", receiptDigest: receipt.receiptDigest, issuerId: receipt.issuerId, signerKeyId: receipt.signerKeyId, authorityScope: receipt.authorityScope, promotionAuthorized: false, deploymentAuthorized: false };
  result.resultSetDigest = digestExcluding(result, ["resultSetDigest"]);
  const resultPath = path.resolve(options.resultPath); writeFreshJson(resultPath, result); writeFreshJson(`${resultPath}.summary.json`, { suiteStatus: result.suiteStatus, executedCount: result.executedCount, skippedCount: 0, resultSetDigest: result.resultSetDigest, finalResultSealed: true });
  return result;
}

const invoked = process.argv[1] ? path.resolve(process.argv[1]) : ""; const self = path.resolve(new URL(import.meta.url).pathname.replace(/^\/(?:[A-Za-z]:)/, (match) => match.slice(1)));
if (invoked === self) {
  try {
    const command = process.argv[2]; const options = parsePairs(process.argv.slice(3));
    const result = command === "collect" ? collectGate1E(options) : command === "seal" ? sealGate1E(options) : (() => { throw new Error("usage: run-gate1e.mjs collect|seal --..."); })();
    process.stdout.write(`${JSON.stringify({ suiteStatus: result.suiteStatus, executedCount: result.executedCount, resultCoreDigest: result.resultCoreDigest, resultSetDigest: result.resultSetDigest || null })}\n`);
  } catch (error) { process.stderr.write(`${error.stack || error}\n`); process.exitCode = 2; }
}
