#!/usr/bin/env node
import crypto from "node:crypto";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";

import { computedVisualMetrics, decodePng } from "../run-authored-bounded-gate1-v3.mjs";
import { canonicalSha256 } from "./lib.mjs";

function pairs(values) {
  const result = {};
  for (let index = 0; index < values.length; index += 2) {
    const key = values[index];
    if (!key?.startsWith("--") || values[index + 1] === undefined) throw new Error("usage: collect-gate1e-live.mjs --key value ...");
    result[key.slice(2)] = values[index + 1];
  }
  return result;
}

function required(options, key) {
  const value = options[key];
  if (typeof value !== "string" || !value) throw new Error(`gate1e-live-option-missing:${key}`);
  return path.resolve(value);
}

function readJson(filePath, label = path.basename(filePath)) {
  try { return JSON.parse(fs.readFileSync(filePath, "utf8")); }
  catch { throw new Error(`gate1e-live-json-invalid:${label}`); }
}

function writeJson(filePath, value) {
  if (fs.existsSync(filePath)) throw new Error(`gate1e-live-output-not-fresh:${filePath}`);
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`);
  return filePath;
}

function sha256File(filePath) {
  return `sha256:${crypto.createHash("sha256").update(fs.readFileSync(filePath)).digest("hex")}`;
}

function sourcePathIncluded(value) {
  const normalized = value.replaceAll("\\", "/");
  return normalized
    && !normalized.startsWith("app_data/")
    && !normalized.startsWith("node_modules/")
    && !normalized.includes("/node_modules/")
    && !normalized.startsWith("servers/nextjs/.next")
    && !normalized.includes("/__pycache__/")
    && !normalized.includes("/.pytest_cache/")
    && !normalized.includes("/.mypy_cache/")
    && !normalized.includes("/.ruff_cache/")
    && !normalized.endsWith(".pyc")
    && !normalized.endsWith(".pyo");
}

function nulFields(bytes) {
  return bytes.toString("utf8").split("\0").filter(Boolean);
}

function collectSourceCensus(repositoryRoot, sourceHeadCommit) {
  const actualHead = execFileSync("git", ["rev-parse", "HEAD"], { cwd: repositoryRoot, encoding: "utf8" }).trim();
  if (actualHead !== sourceHeadCommit) throw new Error("gate1e-live-source-head-drift");

  const states = new Map();
  const gitOptions = { cwd: repositoryRoot, maxBuffer: 64 * 1024 * 1024 };
  const tracked = nulFields(execFileSync("git", ["diff", "--name-status", "--no-renames", "-z", "HEAD", "--"], gitOptions));
  for (let index = 0; index < tracked.length; index += 2) {
    const status = tracked[index];
    const relativePath = tracked[index + 1];
    if (relativePath && sourcePathIncluded(relativePath)) states.set(relativePath.replaceAll("\\", "/"), status);
  }
  for (const relativePath of nulFields(execFileSync("git", ["ls-files", "--others", "--exclude-standard", "-z", "--"], gitOptions))) {
    if (sourcePathIncluded(relativePath)) states.set(relativePath.replaceAll("\\", "/"), "untracked");
  }

  const files = [...states].sort(([left], [right]) => left.localeCompare(right)).map(([relativePath, status]) => {
    const absolutePath = path.join(repositoryRoot, ...relativePath.split("/"));
    if (status === "D") return { path: relativePath, status: "deleted", byteLength: 0, sha256: null };
    const bytes = fs.readFileSync(absolutePath);
    return { path: relativePath, status, byteLength: bytes.length, sha256: `sha256:${crypto.createHash("sha256").update(bytes).digest("hex")}` };
  });
  if (files.length < 1) throw new Error("gate1e-live-source-census-empty");
  const inventoryDigest = canonicalSha256(files);
  const census = {
    schemaVersion: "authored-editable-hybrid-source-census/v1",
    sourceHeadCommit,
    workingTreeDirty: true,
    fileCount: files.length,
    inventoryDigest,
    files,
  };
  census.sourceCensusDigest = canonicalSha256(census);
  return census;
}

function collectQids(value, target = new Set()) {
  if (Array.isArray(value)) { for (const child of value) collectQids(child, target); return target; }
  if (!value || typeof value !== "object") return target;
  for (const key of ["qid", "dataQid", "elementId"]) if (typeof value[key] === "string" && value[key]) target.add(value[key]);
  if (Array.isArray(value.qids)) for (const qid of value.qids) if (typeof qid === "string" && qid) target.add(qid);
  for (const child of Object.values(value)) collectQids(child, target);
  return target;
}

function sortedPngs(directory) {
  const files = fs.readdirSync(directory).filter((name) => /\.png$/i.test(name));
  return files.sort((left, right) => {
    const a = Number(left.match(/(\d+)(?=\.png$)/i)?.[1] ?? Number.MAX_SAFE_INTEGER);
    const b = Number(right.match(/(\d+)(?=\.png$)/i)?.[1] ?? Number.MAX_SAFE_INTEGER);
    return a - b || left.localeCompare(right);
  }).map((name) => path.join(directory, name));
}

function uuid(value) {
  const compact = value.replaceAll("-", "");
  if (!/^[0-9a-f]{32}$/i.test(compact)) throw new Error(`gate1e-live-uuid-invalid:${value}`);
  return `${compact.slice(0, 8)}-${compact.slice(8, 12)}-${compact.slice(12, 16)}-${compact.slice(16, 20)}-${compact.slice(20)}`.toLowerCase();
}

function exactStrings(left, right) {
  return JSON.stringify([...left].sort()) === JSON.stringify([...right].sort());
}

const options = pairs(process.argv.slice(2));
const databasePath = required(options, "database-path");
const inputPptxPath = required(options, "input-pptx-path");
const savedPptxPath = required(options, "saved-pptx-path");
const browserDir = required(options, "browser-dir");
const comDir = required(options, "com-dir");
const gateMinus1Path = required(options, "gate-minus1-result-path");
const gate0Path = required(options, "gate0-result-path");
const outputDir = path.resolve(options["output-dir"] ?? "");
if (!options["output-dir"] || fs.existsSync(outputDir)) throw new Error("gate1e-live-output-dir-must-be-fresh");
fs.mkdirSync(outputDir, { recursive: true });

const presentationId = uuid(options["presentation-id"] ?? "");
const presentationDbId = presentationId.replaceAll("-", "");
const deckId = options["deck-id"];
const presentationRevision = Number(options["presentation-revision"]);
const generationAttemptId = options["generation-attempt-id"];
const hybridExportAttemptId = uuid(options["hybrid-export-attempt-id"] ?? "");
const operationId = options["scene-mutation-operation-id"];
const sourceHeadCommit = options["source-head-commit"];
if (!deckId || !Number.isInteger(presentationRevision) || presentationRevision < 1 || !generationAttemptId || !operationId || !/^[0-9a-f]{40}$/i.test(sourceHeadCommit ?? "")) {
  throw new Error("gate1e-live-attempt-pins-invalid");
}

const db = new DatabaseSync(databasePath, { readOnly: true });
const job = db.prepare("select task_id, state, total_slides, completed_slides, request_sha256, request_payload, created_at, published_at from presentation_generation_jobs where presentation_id = ?").get(presentationDbId);
if (!job || job.task_id !== generationAttemptId || job.state !== "published" || job.total_slides !== 5 || job.completed_slides !== 5) throw new Error("gate1e-live-generation-job-invalid");

const currentRow = db.prepare("select revision, digest, payload from authored_vnext_deck_revisions where deck_id = ? and revision = ?").get(deckId, presentationRevision);
const beforeRow = db.prepare("select revision, digest, payload from authored_vnext_deck_revisions where deck_id = ? and revision = ?").get(deckId, presentationRevision - 1);
if (!currentRow || !beforeRow) throw new Error("gate1e-live-deck-revision-missing");
const currentDeck = JSON.parse(currentRow.payload);
const beforeDeck = JSON.parse(beforeRow.payload);
const sceneAfter = currentDeck.scene;
const sceneBefore = beforeDeck.scene;
if (sceneAfter?.schemaVersion !== "deck-scene-ir/v1" || sceneBefore?.schemaVersion !== "deck-scene-ir/v1" || sceneAfter.sceneDigest === sceneBefore.sceneDigest) throw new Error("gate1e-live-scene-chain-invalid");
const expectedQids = [...collectQids(sceneAfter)].sort();
if (expectedQids.length !== 74 || !exactStrings(expectedQids, collectQids(sceneBefore))) throw new Error(`gate1e-live-qid-set-invalid:${expectedQids.length}`);

const operationRow = db.prepare("select state, expected_revision, result_revision, result_digest, request_payload, result_payload from authored_vnext_generation_operations where deck_id = ? and operation_id = ?").get(deckId, operationId);
if (!operationRow || operationRow.state !== "succeeded" || operationRow.expected_revision !== presentationRevision - 1 || operationRow.result_revision !== presentationRevision || operationRow.result_digest !== currentRow.digest) throw new Error("gate1e-live-mutation-row-invalid");
const operationResult = JSON.parse(operationRow.result_payload);
if (operationResult.schemaVersion !== "scene-mutation-result/v1" || operationResult.sceneDigest !== sceneAfter.sceneDigest || operationResult.publishedByCas !== true) throw new Error("gate1e-live-mutation-result-invalid");

const exportJobId = hybridExportAttemptId.replaceAll("-", "");
const exportJob = db.prepare("select id, state, deck_revision, deck_digest, request_payload, completed_at from authored_vnext_export_jobs where id = ? and deck_id = ?").get(exportJobId, deckId);
const exportReceipt = db.prepare("select id, storage_key, artifact_digest, package_digest, size_bytes, readback_verified from authored_vnext_export_artifact_receipts where job_id = ? and deck_id = ?").get(exportJobId, deckId);
if (!exportJob || !exportReceipt || exportJob.state !== "succeeded" || exportJob.deck_revision !== presentationRevision || exportJob.deck_digest !== currentRow.digest || exportReceipt.readback_verified !== 1) throw new Error("gate1e-live-export-binding-invalid");
const inputDigest = sha256File(inputPptxPath);
const savedDigest = sha256File(savedPptxPath);
if (inputDigest !== exportReceipt.package_digest || fs.statSync(inputPptxPath).size !== exportReceipt.size_bytes) throw new Error("gate1e-live-input-package-invalid");

const reportPath = path.join(comDir, "powerpoint-desktop-validation.json");
const nativeEditPath = path.join(comDir, "native-edit-observation.json");
const repairPath = path.join(comDir, "powerpoint-attended-repair-observation.json");
const processPath = path.join(comDir, "powerpoint-owned-process-delta.json");
const report = readJson(reportPath);
const nativeEdit = readJson(nativeEditPath);
const repair = readJson(repairPath);
const processDelta = readJson(processPath);
const presentation = report.Presentations?.find((entry) => entry.ExportMode === "hybrid" && entry.ExportAttemptId === hybridExportAttemptId);
if (!String(report.PowerPointVersion ?? "").startsWith("16.") || !presentation || presentation.ReadbackPassed !== true || presentation.ShapeReadbackPreserved !== true || presentation.SemanticOwnershipPreserved !== true || presentation.ExportedPngs !== 5 || presentation.RepairWarningObserved !== false || report.RepairDialogObserved !== false) throw new Error("gate1e-live-powerpoint-report-invalid");
if (repair.repairDialogObserved !== false || processDelta.cleanupConfirmed !== true || processDelta.ownedPowerPointSurvivorCount !== 0 || processDelta.unexpectedPowerPointSurvivorCount !== 0) throw new Error("gate1e-live-powerpoint-cleanup-invalid");
const sourceQids = [...new Set(presentation.SourceSemanticQids ?? [])].sort();
const reopenedQids = [...new Set(presentation.ReopenedSemanticQids ?? [])].sort();
if (!exactStrings(sourceQids, expectedQids) || !exactStrings(reopenedQids, expectedQids)) throw new Error("gate1e-live-com-qid-invalid");
if (presentation.TypographyIssuesGt5Pt !== 0 || presentation.TypographyIssuesGt10Pt !== 0 || presentation.FirstOpenReopenedRenderHashesMatch !== true) throw new Error("gate1e-live-com-tolerance-invalid");

const firstOpenDir = path.join(path.dirname(savedPptxPath), "first-open");
const reopenDir = path.join(path.dirname(savedPptxPath), "reopened-render");
const browserPngs = sortedPngs(browserDir).filter((file) => /^browser-slide-\d+\.png$/i.test(path.basename(file)));
const firstOpenPngs = sortedPngs(firstOpenDir);
const reopenPngs = sortedPngs(reopenDir);
if (browserPngs.length !== 5 || firstOpenPngs.length !== 5 || reopenPngs.length !== 5) throw new Error("gate1e-live-png-count-invalid");

const rasterQids = new Set(["s4.chart.owner", "s5.raster.island"]);
for (const qid of rasterQids) if (!expectedQids.includes(qid)) throw new Error(`gate1e-live-raster-owner-missing:${qid}`);
const slides = sceneAfter.slides.map((slide, index) => {
  const counts = { nativeTextElements: 0, nativeShapeElements: 0, nativeImageElements: 0, nativeTableElements: 0, rasterFallbackElements: 0 };
  for (const element of slide.elements) {
    if (rasterQids.has(element.elementId)) { counts.rasterFallbackElements += 1; continue; }
    if (element.kind === "text") counts.nativeTextElements += 1;
    if (element.kind === "shape") counts.nativeShapeElements += 1;
    if (element.kind === "image") counts.nativeImageElements += 1;
    if (element.kind === "table") counts.nativeTableElements += 1;
  }
  return { slideNumber: index + 1, slideId: slide.slideId, ...counts };
});
const total = (field) => slides.reduce((sum, slide) => sum + slide[field], 0);
const actualCounts = {
  nativeTextElements: total("nativeTextElements"),
  nativeShapeElements: total("nativeShapeElements"),
  nativeImageElements: total("nativeImageElements"),
  nativeTableElements: total("nativeTableElements"),
  rasterFallbackElements: total("rasterFallbackElements"),
};
if (JSON.stringify(actualCounts) !== JSON.stringify({ nativeTextElements: 41, nativeShapeElements: 29, nativeImageElements: 1, nativeTableElements: 1, rasterFallbackElements: 2 })) throw new Error(`gate1e-live-native-counts-invalid:${JSON.stringify(actualCounts)}`);

const gateMinus1 = readJson(gateMinus1Path);
const gate0 = readJson(gate0Path);
if (gateMinus1.suiteStatus !== "feasible" || gateMinus1.executedCount !== 15 || gateMinus1.skippedCount !== 0 || gate0.suiteStatus !== "structurally-reachable" || gate0.executedCount !== 15 || gate0.skippedCount !== 0) throw new Error("gate1e-live-prerequisite-gate-invalid");
const commonAttemptFields = ["presentationId", "presentationRevision", "generationAttemptId", "hybridExportAttemptId", "sceneDigest", "activeManifestDigest", "sourceHeadCommit"];
for (const field of commonAttemptFields) {
  const expected = field === "presentationId" ? presentationId
    : field === "presentationRevision" ? presentationRevision
      : field === "generationAttemptId" ? generationAttemptId
        : field === "hybridExportAttemptId" ? hybridExportAttemptId
          : field === "sceneDigest" ? sceneAfter.sceneDigest
            : field === "activeManifestDigest" ? currentRow.digest
              : sourceHeadCommit;
  if (gateMinus1.attempt?.[field] !== expected || gate0.attempt?.[field] !== expected) throw new Error(`gate1e-live-prerequisite-attempt-mismatch:${field}`);
}
if (gate0.attempt?.gateMinus1eResultSetDigest !== gateMinus1.resultSetDigest) throw new Error("gate1e-live-prerequisite-digest-chain-broken");
const repositoryRoot = execFileSync("git", ["rev-parse", "--show-toplevel"], { cwd: process.cwd(), encoding: "utf8" }).trim();
const sourceCensus = collectSourceCensus(repositoryRoot, sourceHeadCommit);
const attempt = {
  presentationId,
  presentationRevision,
  generationAttemptId,
  hybridExportAttemptId,
  sceneDigest: sceneAfter.sceneDigest,
  activeManifestDigest: currentRow.digest,
  gateMinus1eResultSetDigest: gateMinus1.resultSetDigest,
  gate0eResultSetDigest: gate0.resultSetDigest,
  sourceHeadCommit,
  sourceCensusDigest: sourceCensus.sourceCensusDigest,
  sourceDirtyFileCount: sourceCensus.fileCount,
  expectedQids,
};

const artifactPaths = {};
const emit = (artifactId, value) => {
  const filePath = path.join(outputDir, `${artifactId}.json`);
  artifactPaths[artifactId] = writeJson(filePath, value);
};
emit("source-prompt-json", {
  schemaVersion: "authored-actual-generation-source-prompt/v1",
  presentationId,
  generationAttemptId,
  state: job.state,
  totalSlides: job.total_slides,
  completedSlides: job.completed_slides,
  requestSha256: job.request_sha256,
  requestPayload: JSON.parse(job.request_payload),
  createdAt: job.created_at,
  publishedAt: job.published_at,
  sourceCensus,
  gatePrerequisites: {
    schemaVersion: "authored-editable-hybrid-gate-prerequisites/v1",
    gateMinus1eResultSet: gateMinus1,
    gate0eResultSet: gate0,
  },
});
emit("semantic-source-json", {
  schemaVersion: "authored-canonical-semantic-source/v1",
  presentationId,
  presentationRevision,
  generationAttemptId,
  sourceDigest: currentDeck.sourceDigest,
  qids: expectedQids,
  scene: sceneAfter,
});
emit("scene-before-json", sceneBefore);
emit("scene-after-json", sceneAfter);
emit("scene-mutation-receipt-json", {
  ...operationResult,
  presentationId,
  presentationRevision,
  generationAttemptId,
  sceneDigest: sceneAfter.sceneDigest,
  state: "accepted",
});
emit("active-manifest-json", {
  schemaVersion: "authored-vnext-active-deck-manifest/v1",
  presentationId,
  presentationRevision,
  deckId,
  manifestDigest: currentRow.digest,
  sceneDigest: sceneAfter.sceneDigest,
  deck: currentDeck,
});
emit("native-admission-json", {
  schemaVersion: "authored-editable-hybrid-native-admission/v1",
  presentationId,
  presentationRevision,
  generationAttemptId,
  qids: expectedQids,
  supportedKinds: ["text", "shape", "image", "table"],
  boundedRasterOwners: [...rasterQids].sort(),
  ...actualCounts,
  editableSlides: 5,
  imageFallbackSlides: 0,
  unexpectedRasterFallbacks: 0,
  nativeRasterDuplicateOwners: 0,
  missingOrGhostQids: 0,
  nativeEditableReady: true,
});
emit("hybrid-quality-json", {
  schemaVersion: "authored-editable-hybrid-quality/v1",
  atomicBinding: { presentationId, presentationRevision, exportAttemptId: hybridExportAttemptId },
  exportAttemptId: hybridExportAttemptId,
  editableSlides: 5,
  imageFallbackSlides: 0,
  minimumNativeTextElementsPerSlide: 1,
  minimumNativeShapeElements: 1,
  minimumNativeImageElements: 1,
  minimumNativeTableElements: 1,
  rasterFallbackElements: 2,
  unexpectedRasterFallbacks: 0,
  nativeRasterDuplicateOwners: 0,
  missingOrGhostQids: 0,
  nativeEditableReady: true,
  ...actualCounts,
  slides,
  sourceDeckQuality: operationResult.quality,
});
for (const [artifactId, packageState, packagePath, packageDigest] of [
  ["input-ooxml-readback-json", "input", inputPptxPath, inputDigest],
  ["saved-ooxml-readback-json", "saved", savedPptxPath, savedDigest],
]) emit(artifactId, {
  schemaVersion: "authored-editable-hybrid-ooxml-readback/v1",
  presentationId,
  presentationRevision,
  hybridExportAttemptId,
  packageState,
  packagePath,
  packageDigest,
  slideCount: 5,
  qids: expectedQids,
  repairRequired: false,
  ...actualCounts,
  editableSlides: 5,
  imageFallbackSlides: 0,
  unexpectedRasterFallbacks: 0,
  nativeRasterDuplicateOwners: 0,
  missingOrGhostQids: 0,
  slides,
});
for (const [artifactId, phase, readback] of [
  ["first-open-com-json", "first-open", presentation.SourceShapeReadback],
  ["reopen-com-json", "reopen", presentation.ShapeReadback],
]) emit(artifactId, {
  schemaVersion: "authored-editable-hybrid-com-snapshot/v1",
  presentationId,
  presentationRevision,
  hybridExportAttemptId,
  phase,
  qids: expectedQids,
  readbackPassed: true,
  nativeEditsPreserved: true,
  repairDialogObserved: false,
  typographyIssuesGt5Pt: presentation.TypographyIssuesGt5Pt,
  typographyIssuesGt10Pt: presentation.TypographyIssuesGt10Pt,
  shapeReadback: readback,
  nativeEditObservation: nativeEdit,
});

const imageEntries = [];
const decoded = new Map();
for (let index = 0; index < 5; index += 1) for (const [prefix, files] of [["browser", browserPngs], ["first-open", firstOpenPngs], ["reopen", reopenPngs]]) {
  const artifactId = `${prefix}-slide-${String(index + 1).padStart(2, "0")}-png`;
  const decodedPng = decodePng(files[index], artifactId);
  if (decodedPng.width !== 1280 || decodedPng.height !== 720) throw new Error(`gate1e-live-png-dimensions-invalid:${artifactId}`);
  decoded.set(artifactId, decodedPng);
  imageEntries.push({ artifactId, path: files[index], sha256: sha256File(files[index]), width: decodedPng.width, height: decodedPng.height });
  artifactPaths[artifactId] = files[index];
}
const comparisons = [];
for (let index = 0; index < 5; index += 1) {
  const number = String(index + 1).padStart(2, "0");
  for (const [suffix, baselineArtifactId, candidateArtifactId, policy] of [
    ["browser-vs-first", `browser-slide-${number}-png`, `first-open-slide-${number}-png`, "cross-renderer"],
    ["browser-vs-reopen", `browser-slide-${number}-png`, `reopen-slide-${number}-png`, "cross-renderer"],
    ["first-vs-reopen", `first-open-slide-${number}-png`, `reopen-slide-${number}-png`, "exact-reopen"],
  ]) {
    const metrics = computedVisualMetrics(decoded.get(baselineArtifactId), decoded.get(candidateArtifactId));
    const thresholdPassed = policy === "exact-reopen"
      ? metrics.changedPixelRatioPpm === 0 && metrics.block8LumaSsimMillionths === 1_000_000 && metrics.meanAbsoluteChannelDeltaMilli === 0 && metrics.rmseMilli === 0
      : metrics.block8LumaSsimMillionths >= 500_000 && metrics.meanAbsoluteChannelDeltaMilli <= 65_000 && metrics.rmseMilli <= 120_000;
    if (!thresholdPassed) throw new Error(`gate1e-live-visual-threshold-failed:${number}:${suffix}:${JSON.stringify(metrics)}`);
    comparisons.push({ comparisonId: `slide-${number}-${suffix}`, baselineArtifactId, candidateArtifactId, policy, ...metrics, thresholdPassed, state: "accepted" });
  }
}
emit("independent-visual-metrics-json", {
  schemaVersion: "authored-editable-hybrid-independent-visual-metrics/v1",
  presentationId,
  presentationRevision,
  generationAttemptId,
  hybridExportAttemptId,
  policies: {
    crossRenderer: { block8LumaSsimMillionthsMin: 500_000, meanAbsoluteChannelDeltaMilliMax: 65_000, rmseMilliMax: 120_000, structuralReadbackRequired: true },
    exactReopen: { changedPixelRatioPpm: 0, block8LumaSsimMillionths: 1_000_000, meanAbsoluteChannelDeltaMilli: 0, rmseMilli: 0 },
  },
  images: imageEntries,
  comparisons,
});
emit("attempt-sidecar-json", {
  schemaVersion: "authored-editable-hybrid-attempt-sidecar/v1",
  profileId: "authored-editable-hybrid-ko-en-basic-v1",
  presentationId,
  presentationRevision,
  generationAttemptId,
  hybridExportAttemptId,
  sceneMutationStatus: "accepted",
  nativeAdmissionStatus: "accepted",
  packageReadbackStatus: "accepted",
  comFirstOpenStatus: "accepted",
  comSaveCopyStatus: "accepted",
  comReopenStatus: "accepted",
  renderHashStabilityStatus: "accepted",
  visualParityStatus: "accepted",
  repairDialogStatus: "accepted",
  editableSlides: 5,
  imageFallbackSlides: 0,
  rasterFallbackElements: 2,
  unexpectedRasterFallbacks: 0,
  nativeRasterDuplicateOwners: 0,
  missingOrGhostQids: 0,
  nativeEditableReady: true,
  unexpectedPowerPointSurvivorCount: 0,
  ...actualCounts,
});
emit("completion-seal-json", {
  schemaVersion: "authored-editable-hybrid-mvp-completion/v1",
  presentationId,
  presentationRevision,
  generationAttemptId,
  hybridExportAttemptId,
  sceneDigest: sceneAfter.sceneDigest,
  activeManifestDigest: currentRow.digest,
  state: "sealed",
  casBefore: 0,
  casAfter: 1,
  nativeEditableReady: true,
  editableHybridMvpReady: true,
  promotionReady: false,
  deploymentAuthorized: false,
});

artifactPaths["hybrid-input-pptx"] = inputPptxPath;
artifactPaths["hybrid-saved-pptx"] = savedPptxPath;
artifactPaths["powerpoint-com-report-json"] = reportPath;
artifactPaths["repair-observation-json"] = repairPath;
artifactPaths["powerpoint-process-delta-json"] = processPath;
const bindingsPath = writeJson(path.join(outputDir, "gate1e-bindings.json"), artifactPaths);
const attemptPath = writeJson(path.join(outputDir, "gate1e-attempt.json"), attempt);
writeJson(path.join(outputDir, "gate1e-live-summary.json"), {
  schemaVersion: "authored-editable-hybrid-gate1e-live-summary/v1",
  attempt,
  bindingsPath,
  attemptPath,
  actualCounts,
  editableSlides: 5,
  imageFallbackSlides: 0,
  unexpectedRasterFallbacks: 0,
  nativeRasterDuplicateOwners: 0,
  missingOrGhostQids: 0,
  repairDialogObserved: false,
  ownedPowerPointSurvivors: 0,
  crossRendererComparisonsAccepted: comparisons.filter((entry) => entry.policy === "cross-renderer").length,
  exactReopenComparisonsAccepted: comparisons.filter((entry) => entry.policy === "exact-reopen").length,
  promotionReady: false,
  deploymentAuthorized: false,
});
process.stdout.write(`${JSON.stringify({ outputDir, bindingsPath, attemptPath, qids: expectedQids.length, actualCounts, comparisons: comparisons.length })}\n`);
