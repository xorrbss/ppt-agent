/**
 * Collect the UI/PowerPoint portion of a Gate 1 bundle from one immutable
 * product attempt.  This is deliberately not an issuer: FastAPI-owned
 * authority artifacts (graph root, completion, phase result and trust
 * attestations) are supplied as already-persisted inputs and copied only by
 * the assembler.  The collector refuses to substitute a previous attempt or
 * to infer attended-dialog/process evidence from a COM render.
 */
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";

import { canonicalSha256, validateGate1Ledger } from "./authored-bounded-release-contracts.mjs";
import { computedVisualMetrics, decodePng } from "./run-authored-bounded-gate1-v3.mjs";

const PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation";
const ROOT = path.resolve(new URL("..", import.meta.url).pathname.replace(/^\/(?:[A-Za-z]:)/, (match) => match.slice(1)));
const MANIFEST_PATH = path.join(ROOT, "docs", "authored-bounded-hybrid-release-v3-contracts", "gate1-phase-manifest.v1.json");

function sha256(bytes) { return `sha256:${crypto.createHash("sha256").update(bytes).digest("hex")}`; }
function readJson(filePath) { return JSON.parse(fs.readFileSync(filePath, "utf8")); }
function real(filePath, label) { try { return fs.realpathSync(filePath); } catch { throw new Error(`gate1-collector-${label}-missing:${filePath}`); } }
function writeFresh(filePath, value) { fs.mkdirSync(path.dirname(filePath), { recursive: true }); fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, { flag: "wx" }); }
function artifact(id, filePath, mediaType) {
  const absolute = real(filePath, id); const bytes = fs.readFileSync(absolute);
  return { artifactId: id, versionId: `sha256-${sha256(bytes).slice(7)}`, path: absolute, sha256: sha256(bytes), byteLength: bytes.length, mediaType };
}
function text(value, label) { if (typeof value !== "string" || !value.trim()) throw new Error(`gate1-collector-${label}-required`); return value.trim(); }
function integer(value, label) { const parsed = Number(value); if (!Number.isInteger(parsed) || parsed < 1) throw new Error(`gate1-collector-${label}-invalid`); return parsed; }
function collectQids(value, target = new Set()) {
  if (Array.isArray(value)) { for (const item of value) collectQids(item, target); return target; }
  if (!value || typeof value !== "object") return target;
  for (const key of ["qid", "dataQid", "elementId"]) if (typeof value[key] === "string" && value[key]) target.add(value[key]);
  if (Array.isArray(value.qids)) for (const qid of value.qids) if (typeof qid === "string" && qid) target.add(qid);
  for (const child of Object.values(value)) collectQids(child, target);
  return target;
}
function sameStrings(left, right) { return JSON.stringify([...left].sort()) === JSON.stringify([...right].sort()); }

// PPTX is a Zip archive.  Only stored/deflated entries are accepted so a
// malformed package cannot be mistaken for an OOXML readback.
function zipEntries(filePath) {
  const bytes = fs.readFileSync(filePath); const eocd = Buffer.from([0x50, 0x4b, 0x05, 0x06]);
  const index = bytes.lastIndexOf(eocd); if (index < 0 || index + 22 > bytes.length) throw new Error("gate1-collector-pptx-eocd-invalid");
  const count = bytes.readUInt16LE(index + 10); const centralOffset = bytes.readUInt32LE(index + 16); const entries = new Map(); let cursor = centralOffset;
  for (let ordinal = 0; ordinal < count; ordinal += 1) {
    if (cursor + 46 > bytes.length || bytes.readUInt32LE(cursor) !== 0x02014b50) throw new Error("gate1-collector-pptx-central-directory-invalid");
    const method = bytes.readUInt16LE(cursor + 10); const compressedSize = bytes.readUInt32LE(cursor + 20); const uncompressedSize = bytes.readUInt32LE(cursor + 24);
    const nameLength = bytes.readUInt16LE(cursor + 28); const extraLength = bytes.readUInt16LE(cursor + 30); const commentLength = bytes.readUInt16LE(cursor + 32); const localOffset = bytes.readUInt32LE(cursor + 42);
    const name = bytes.toString("utf8", cursor + 46, cursor + 46 + nameLength); cursor += 46 + nameLength + extraLength + commentLength;
    if (localOffset + 30 > bytes.length || bytes.readUInt32LE(localOffset) !== 0x04034b50) throw new Error(`gate1-collector-pptx-local-header-invalid:${name}`);
    const localNameLength = bytes.readUInt16LE(localOffset + 26); const localExtraLength = bytes.readUInt16LE(localOffset + 28); const dataStart = localOffset + 30 + localNameLength + localExtraLength;
    const compressed = bytes.subarray(dataStart, dataStart + compressedSize); const content = method === 0 ? compressed : method === 8 ? zlib.inflateRawSync(compressed) : (() => { throw new Error(`gate1-collector-pptx-method-unsupported:${method}`); })();
    if (content.length !== uncompressedSize || entries.has(name)) throw new Error(`gate1-collector-pptx-entry-invalid:${name}`); entries.set(name, content);
  }
  return entries;
}
function readbackPptx({ filePath, mode, state, pins, qids, repair }) {
  const entries = zipEntries(filePath); const names = [...entries.keys()].sort();
  const slideNames = names.filter((name) => /^ppt\/slides\/slide\d+\.xml$/.test(name)).sort((a, b) => Number(a.match(/\d+/)[0]) - Number(b.match(/\d+/)[0]));
  if (slideNames.length !== 8) throw new Error(`gate1-collector-slide-count-invalid:${mode}:${state}`);
  const ooxmlQids = new Set(); const slideFacts = [];
  for (const name of slideNames) {
    const xml = entries.get(name).toString("utf8");
    for (const match of xml.matchAll(/name="Presenton (?:hybrid (?:text|shape|table|image|chart) |raster visual fragment )?([^" ]+)(?: [^"]*)?"/g)) if (match[1] && /^s\d\d-/.test(match[1])) ooxmlQids.add(match[1]);
    slideFacts.push({ partName: name, sha256: sha256(entries.get(name)), textShapeCount: (xml.match(/<p:sp\b/g) ?? []).length, pictureCount: (xml.match(/<p:pic\b/g) ?? []).length, graphicFrameCount: (xml.match(/<p:graphicFrame\b/g) ?? []).length });
  }
  // A raster fallback is still a semantic element.  Its identity must remain
  // readbackable; accepting only the native subset would let ownership drift.
  if (!sameStrings(ooxmlQids, qids)) throw new Error(`gate1-collector-ooxml-qid-ownership-mismatch:${mode}:${state}:${ooxmlQids.size}/${qids.size}`);
  return {
    schemaVersion: "authored-ooxml-package-readback/v1", authorityDomain: "actual_product",
    presentationId: pins.presentationId, presentationRevision: pins.presentationRevision,
    exportAttemptId: pins[`${mode}ExportAttemptId`], exportMode: mode, packageState: state,
    packageDigest: sha256(fs.readFileSync(filePath)), repairRequired: repair.repairDialogObserved !== false,
    qids: [...qids].sort(), packagePartCount: names.length, slideFacts,
  };
}
function findPresentation(report, filename) {
  const entries = report.Presentations; if (!Array.isArray(entries)) throw new Error("gate1-collector-com-report-presentations-invalid");
  const result = entries.find((entry) => typeof entry.Path === "string" && path.basename(entry.Path).toLowerCase() === filename.toLowerCase());
  if (!result) throw new Error(`gate1-collector-com-report-presentation-missing:${filename}`); return result;
}
function pngs(directory) {
  const absolute = real(directory, "com-render-directory"); const result = new Map();
  for (const entry of fs.readdirSync(absolute, { withFileTypes: true })) {
    if (!entry.isFile() || !/\.png$/i.test(entry.name)) continue;
    const match = entry.name.match(/(?:슬라이드|slide)[ _]?(\d+)/i); if (!match) continue;
    result.set(Number(match[1]), path.join(absolute, entry.name));
  }
  if (result.size !== 8 || [...result.keys()].some((slide) => slide < 1 || slide > 8)) throw new Error(`gate1-collector-com-render-cardinality:${directory}`);
  return result;
}
function repairObservation(value, pins) {
  if (!value || value.schemaVersion !== "authored-powerpoint-attended-repair-observation/v1" || value.authorityDomain !== "actual_product" || value.presentationId !== pins.presentationId || value.presentationRevision !== pins.presentationRevision || value.repairDialogObserved !== false || !Array.isArray(value.windowEvents) || value.windowEvents.length === 0) throw new Error("gate1-collector-attended-repair-observation-required");
  return value;
}
function processCensus(value, pins, mode) {
  if (!value || value.schemaVersion !== "authored-powerpoint-process-census/v1" || value.authorityDomain !== "actual_product" || value.presentationId !== pins.presentationId || value.presentationRevision !== pins.presentationRevision || !Array.isArray(value.modes)) throw new Error("gate1-collector-process-census-required");
  const item = value.modes.find((entry) => entry.mode === mode && entry.unexpectedPowerPointProcessCount === 0 && entry.cleanupConfirmed === true);
  if (!item) throw new Error(`gate1-collector-process-census-mode-required:${mode}`); return item;
}
function comparisons() {
  const result = [];
  for (let slide = 1; slide <= 8; slide += 1) {
    const number = String(slide).padStart(2, "0");
    for (const mode of ["fidelity", "hybrid"]) {
      result.push([`${mode}-source-vs-first-${number}`, `${mode}-browser-slide-${number}-png`, `${mode}-first-open-slide-${number}-png`]);
      result.push([`${mode}-source-vs-reopen-${number}`, `${mode}-browser-slide-${number}-png`, `${mode}-reopen-slide-${number}-png`]);
      result.push([`${mode}-first-vs-reopen-${number}`, `${mode}-first-open-slide-${number}-png`, `${mode}-reopen-slide-${number}-png`]);
    }
    result.push([`hybrid-vs-fidelity-first-${number}`, `fidelity-first-open-slide-${number}-png`, `hybrid-first-open-slide-${number}-png`]);
    result.push([`hybrid-vs-fidelity-reopen-${number}`, `fidelity-reopen-slide-${number}-png`, `hybrid-reopen-slide-${number}-png`]);
  }
  return result;
}
function parseArgs(argv) {
  const options = {}; for (let index = 0; index < argv.length; index += 2) { if (!argv[index]?.startsWith("--") || argv[index + 1] === undefined) throw new Error(`invalid-argument:${argv[index]}`); options[argv[index].slice(2).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = argv[index + 1]; } return options;
}

export function collectProductAttempt(options) {
  const manifest = readJson(MANIFEST_PATH); const attempt = real(options.attemptDir, "attempt-directory"); const output = path.resolve(text(options.outputDir, "output-directory"));
  if (fs.existsSync(output)) throw new Error("gate1-collector-output-must-be-fresh");
  const pins = { presentationId: text(options.presentationId, "presentation-id"), presentationRevision: integer(options.presentationRevision, "presentation-revision"), generationAttemptId: text(options.generationAttemptId, "generation-attempt-id"), fidelityExportAttemptId: text(options.fidelityExportAttemptId, "fidelity-export-attempt-id"), hybridExportAttemptId: text(options.hybridExportAttemptId, "hybrid-export-attempt-id") };
  const semantic = readJson(real(options.semanticSourcePath, "semantic-source")); const qids = [...collectQids(semantic)].sort(); if (!qids.length) throw new Error("gate1-collector-semantic-qids-missing");
  const active = readJson(real(options.activeManifestPath, "active-manifest"));
  const comReport = readJson(path.join(attempt, "powerpoint-com", "powerpoint-desktop-validation.json")); const repair = repairObservation(readJson(real(options.repairObservationPath, "repair-observation")), pins); const process = readJson(real(options.processCensusPath, "process-census"));
  const quality = readJson(path.join(attempt, "hybrid-ui.quality.json"));
  if (quality.atomicBinding?.presentationId !== pins.presentationId || quality.atomicBinding?.presentationRevision !== pins.presentationRevision || quality.exportAttemptId !== pins.hybridExportAttemptId) throw new Error("gate1-collector-hybrid-quality-attempt-mismatch");

  fs.mkdirSync(output, { recursive: true }); const artifacts = new Map(); const add = (id, filePath, mediaType) => artifacts.set(id, artifact(id, filePath, mediaType));
  add("semantic-source-json", options.semanticSourcePath, "application/json"); add("active-manifest-json", options.activeManifestPath, "application/json");
  const originals = { fidelity: path.join(attempt, "fidelity-ui.pptx"), hybrid: path.join(attempt, "hybrid-ui.pptx") };
  const reportByMode = { fidelity: findPresentation(comReport, "fidelity-ui.pptx"), hybrid: findPresentation(comReport, "hybrid-ui.pptx") };
  const repairs = repair;
  for (const mode of ["fidelity", "hybrid"]) {
    const saved = real(reportByMode[mode].ReopenedPath, `${mode}-reopened-pptx`); add(`${mode}-input-pptx`, originals[mode], PPTX); add(`${mode}-saved-pptx`, saved, PPTX);
    const browserDirectory = mode === "fidelity" ? path.join(attempt, "fidelity-browser") : path.join(attempt, "hybrid-browser-policy-1280x720");
    for (let slide = 1; slide <= 8; slide += 1) { const number = String(slide).padStart(2, "0"); add(`${mode}-browser-slide-${number}-png`, path.join(browserDirectory, `slide-${number}.png`), "image/png"); }
    for (const phase of ["first-open", "reopen"]) {
      const rendered = pngs(path.join(path.dirname(saved), phase === "first-open" ? "first-open" : "reopened-render"));
      for (let slide = 1; slide <= 8; slide += 1) { const number = String(slide).padStart(2, "0"); add(`${mode}-${phase}-slide-${number}-png`, rendered.get(slide), "image/png"); }
    }
    for (const state of ["input", "saved"]) {
      const readback = readbackPptx({ filePath: state === "input" ? originals[mode] : saved, mode, state, pins, qids, repair: repairs }); const outputPath = path.join(output, "ooxml", `${mode}-${state}.json`); writeFresh(outputPath, readback); add(`${mode}-${state}-ooxml-json`, outputPath, "application/json");
    }
    const inputDigest = artifacts.get(`${mode}-input-pptx`).sha256; const savedDigest = artifacts.get(`${mode}-saved-pptx`).sha256;
    const qidsFromReadback = (entries) => new Set((entries ?? []).flatMap((entry) => {
      const nameMatches = [...String(entry.Name ?? "").matchAll(/\b(s\d\d-[\w-]+)\b/g)].map((match) => match[1]);
      return [...nameMatches, ...(Array.isArray(entry.Qids) ? entry.Qids : [])];
    }).filter(Boolean));
    const firstOpenQids = qidsFromReadback(reportByMode[mode].SourceShapeReadback);
    const reopenQids = qidsFromReadback(reportByMode[mode].ShapeReadback);
    if (!sameStrings(firstOpenQids, qids)) throw new Error(`gate1-collector-com-qid-ownership-mismatch:${mode}:first-open:${firstOpenQids.size}/${qids.length}`);
    if (!sameStrings(reopenQids, qids)) throw new Error(`gate1-collector-com-qid-ownership-mismatch:${mode}:reopen:${reopenQids.size}/${qids.length}`);
    for (const phase of ["first-open", "reopen"]) {
      const census = processCensus(process, pins, mode); const outputPath = path.join(output, "com", `${mode}-${phase}.json`);
      writeFresh(outputPath, { schemaVersion: "authored-powerpoint-com-snapshot/v1", authorityDomain: "actual_product", presentationId: pins.presentationId, presentationRevision: pins.presentationRevision, exportAttemptId: pins[`${mode}ExportAttemptId`], exportMode: mode, phase, inputPptxDigest: inputDigest, savedCopyPptxDigest: savedDigest, reopenInputPptxDigest: phase === "reopen" ? savedDigest : null, ooxmlReadbackDigest: artifacts.get(`${mode}-${phase === "first-open" ? "input" : "saved"}-ooxml-json`).sha256, qids, repairDialogObserved: repairs.repairDialogObserved, unexpectedPowerPointProcessCount: census.unexpectedPowerPointProcessCount, sourceShapeReadback: reportByMode[mode].SourceShapeReadback ?? [], reopenedShapeReadback: reportByMode[mode].ShapeReadback ?? [] });
      add(`${mode}-${phase}-com-json`, outputPath, "application/json");
    }
  }
  const imageEntries = [...artifacts.values()].filter((entry) => entry.artifactId.endsWith("-png")); const decoded = new Map(imageEntries.map((entry) => [entry.artifactId, decodePng(entry.path, entry.artifactId)]));
  if (imageEntries.length !== 48 || imageEntries.some((entry) => decoded.get(entry.artifactId).width !== 1280 || decoded.get(entry.artifactId).height !== 720)) throw new Error("gate1-collector-png-policy-mismatch");
  const visual = { schemaVersion: "authored-independent-visual-metrics/v1", authorityDomain: "actual_product", presentationId: pins.presentationId, presentationRevision: pins.presentationRevision, generationAttemptId: pins.generationAttemptId, fidelityExportAttemptId: pins.fidelityExportAttemptId, hybridExportAttemptId: pins.hybridExportAttemptId, images: imageEntries.sort((a, b) => a.artifactId.localeCompare(b.artifactId)).map((entry) => ({ artifactId: entry.artifactId, sha256: entry.sha256, width: 1280, height: 720 })), comparisons: comparisons().map(([comparisonId, baselineArtifactId, candidateArtifactId]) => ({ comparisonId, baselineArtifactId, candidateArtifactId, ...computedVisualMetrics(decoded.get(baselineArtifactId), decoded.get(candidateArtifactId)) })) };
  const visualPath = path.join(output, "independent-visual-metrics.json"); writeFresh(visualPath, visual); add("independent-visual-metrics-json", visualPath, "application/json");
  for (const [id, input, type] of [["environment-attestation-json", options.environmentPath, "application/json"], ["worker-attestation-json", options.workerPath, "application/json"], ["ci-seal-json", options.ciPath, "application/json"], ["phase-result-set-json", options.productPhaseResultPath, "application/json"], ["evidence-graph-root-json", options.evidenceGraphRootPath, "application/json"], ["completion-seal-json", options.completionPath, "application/json"]]) add(id, input, type);
  const manual = readJson(real(options.manualObservationsPath, "manual-observations")); if (manual.authorityDomain !== "actual_product" || manual.presentationId !== pins.presentationId || manual.presentationRevision !== pins.presentationRevision || manual.generationAttemptId !== pins.generationAttemptId || manual.fidelityExportAttemptId !== pins.fidelityExportAttemptId || manual.hybridExportAttemptId !== pins.hybridExportAttemptId || !Array.isArray(manual.cells)) throw new Error("gate1-collector-manual-observation-attempt-mismatch"); validateGate1Ledger(manual.cells, 8); if (manual.cells.some((cell) => cell.state !== "accepted" || typeof cell.reviewerId !== "string" || !cell.reviewedAt)) throw new Error("gate1-collector-manual-observation-unreviewed");
  const productPhases = readJson(real(options.productPhaseResultPath, "product-phase-result")).phases; if (!Array.isArray(productPhases) || productPhases.length !== 37) throw new Error("gate1-collector-product-phase-result-invalid");
  const required = new Set(manifest.requiredArtifactIds); const sourceArtifacts = [...artifacts.values()]; if (sourceArtifacts.length !== 69 || sourceArtifacts.some((entry) => !required.has(entry.artifactId))) throw new Error(`gate1-collector-artifact-cardinality:${sourceArtifacts.length}/69`);
  for (const phase of productPhases) if (!required.has(phase.evidenceArtifactIds?.[0]) || phase.authorityDomain !== "actual_product" || phase.evidenceClass !== "actual_product_release_qualification") throw new Error(`gate1-collector-phase-evidence-invalid:${phase.phaseId}`);
  const sourceMap = { schemaVersion: "authored-gate1-actual-artifact-source-map/v1", authorityDomain: "actual_product", presentationId: pins.presentationId, presentationRevision: pins.presentationRevision, generationAttemptId: pins.generationAttemptId, fidelityExportAttemptId: pins.fidelityExportAttemptId, hybridExportAttemptId: pins.hybridExportAttemptId, artifacts: sourceArtifacts, phaseEvidence: productPhases.map(({ ordinal, phaseId, state, evidenceArtifactIds }) => ({ ordinal, phaseId, observedState: state, evidenceArtifactIds })) };
  writeFresh(path.join(output, "artifact-source-map.json"), sourceMap); writeFresh(path.join(output, "collector-summary.json"), { schemaVersion: "authored-gate1-product-attempt-collector-summary/v1", authorityDomain: "actual_product", attemptDirectory: attempt, artifactCount: sourceArtifacts.length, qidCount: qids.length, visualComparisonCount: visual.comparisons.length, manualLedgerCellCount: manual.cells.length, sourceMapDigest: canonicalSha256(sourceMap) });
  return { artifactCount: sourceArtifacts.length, qidCount: qids.length, visualComparisonCount: visual.comparisons.length, sourceMapPath: path.join(output, "artifact-source-map.json") };
}

const invoked = process.argv[1] ? path.resolve(process.argv[1]) : ""; const self = path.resolve(new URL(import.meta.url).pathname.replace(/^\/(?:[A-Za-z]:)/, (match) => match.slice(1)));
if (invoked === self) { try { process.stdout.write(`${JSON.stringify(collectProductAttempt(parseArgs(process.argv.slice(2))))}\n`); } catch (error) { process.stderr.write(`${error.stack || error}\n`); process.exitCode = 2; } }
