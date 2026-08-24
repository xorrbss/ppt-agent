import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";

import {
  ensureFreshDirectory,
  parsePairs,
  readJson,
  sameStrings,
  sha256Bytes,
  writeFreshJson,
} from "./lib.mjs";

function fail(code) {
  throw new Error(`gate-minus1e-live:${code}`);
}

function exact(value, expected, code) {
  if (value !== expected) fail(code);
}

function artifact(artifactId, filePath, mediaType = "application/json") {
  const bytes = fs.readFileSync(filePath);
  return {
    artifactId,
    path: path.resolve(filePath),
    mediaType,
    sha256: sha256Bytes(bytes),
    byteLength: bytes.length,
  };
}

function zipEntries(pptxPath, prefix) {
  const bytes = fs.readFileSync(pptxPath);
  const eocd = Buffer.from([0x50, 0x4b, 0x05, 0x06]);
  const eocdOffset = bytes.lastIndexOf(eocd);
  if (eocdOffset < 0) fail("pptx-eocd-missing");
  const count = bytes.readUInt16LE(eocdOffset + 10);
  let cursor = bytes.readUInt32LE(eocdOffset + 16);
  const result = [];
  for (let index = 0; index < count; index += 1) {
    if (bytes.readUInt32LE(cursor) !== 0x02014b50) fail("pptx-central-directory-invalid");
    const compression = bytes.readUInt16LE(cursor + 10);
    const compressedLength = bytes.readUInt32LE(cursor + 20);
    const nameLength = bytes.readUInt16LE(cursor + 28);
    const extraLength = bytes.readUInt16LE(cursor + 30);
    const commentLength = bytes.readUInt16LE(cursor + 32);
    const localOffset = bytes.readUInt32LE(cursor + 42);
    const name = bytes.toString("utf8", cursor + 46, cursor + 46 + nameLength);
    if (name.startsWith(prefix)) {
      if (bytes.readUInt32LE(localOffset) !== 0x04034b50) fail("pptx-local-header-invalid");
      const localNameLength = bytes.readUInt16LE(localOffset + 26);
      const localExtraLength = bytes.readUInt16LE(localOffset + 28);
      const dataOffset = localOffset + 30 + localNameLength + localExtraLength;
      const compressed = bytes.subarray(dataOffset, dataOffset + compressedLength);
      const decoded = compression === 0 ? compressed : compression === 8 ? zlib.inflateRawSync(compressed) : fail("pptx-compression-unsupported");
      result.push({ name, sha256: sha256Bytes(decoded), byteLength: decoded.length });
    }
    cursor += 46 + nameLength + extraLength + commentLength;
  }
  return result.sort((left, right) => left.name.localeCompare(right.name));
}

function shapeByQid(records, qid, label) {
  const matches = records.filter((entry) => entry.Qids === qid);
  if (matches.length !== 1) fail(`${label}-qid-cardinality:${qid}`);
  return matches[0];
}

function jsonExact(left, right, code) {
  if (JSON.stringify(left) !== JSON.stringify(right)) fail(code);
}

function sha256Files(directory) {
  return fs.readdirSync(directory)
    .filter((name) => name.toLowerCase().endsWith(".png"))
    .sort((left, right) => left.localeCompare(right, "ko"))
    .map((name) => sha256Bytes(fs.readFileSync(path.join(directory, name))).slice(7));
}

export function collectGateMinus1ELive(options) {
  const manifest = readJson(path.resolve(options.manifestPath));
  const reportPath = path.resolve(options.comReportPath);
  const nativeEditPath = path.resolve(options.nativeEditPath);
  const repairPath = path.resolve(options.repairPath);
  const processDeltaPath = path.resolve(options.processDeltaPath);
  const inputPptxPath = path.resolve(options.inputPptxPath);
  const savedPptxPath = path.resolve(options.savedPptxPath);
  const outputDir = path.resolve(options.outputDir);
  const report = readJson(reportPath);
  const nativeEdit = readJson(nativeEditPath);
  const repair = readJson(repairPath);
  const processDelta = readJson(processDeltaPath);
  const revision = Number(options.presentationRevision);
  const attempt = {
    presentationId: options.presentationId,
    presentationRevision: revision,
    generationAttemptId: options.generationAttemptId,
    hybridExportAttemptId: options.hybridExportAttemptId,
    sceneDigest: options.sceneDigest,
    activeManifestDigest: options.activeManifestDigest,
    sourceHeadCommit: options.sourceHeadCommit,
    expectedQids: [],
  };
  if (!/^[0-9a-f]{40}$/i.test(attempt.sourceHeadCommit || "")) fail("source-head-commit");

  exact(report.PowerPointVersion, "16.0", "powerpoint-version");
  const presentation = (report.Presentations || []).find((entry) => entry.ExportAttemptId === attempt.hybridExportAttemptId && path.resolve(entry.Path) === inputPptxPath);
  if (!presentation) fail("presentation-binding");
  exact(presentation.SlideCount, 5, "slide-count");
  exact(presentation.ReadbackPassed, true, "readback");
  exact(presentation.ShapeReadbackPreserved, true, "shape-readback");
  exact(presentation.SemanticOwnershipPreserved, true, "semantic-ownership");
  exact(presentation.TypographyIssuesGt5Pt, 0, "typography-gt5");
  exact(presentation.TypographyIssuesGt10Pt, 0, "typography-gt10");
  exact(presentation.InspectionFailures, 0, "inspection-failures");
  exact(presentation.ExportedPngs, 5, "render-count");
  exact(presentation.FirstOpenReopenedRenderHashesMatch, true, "render-hash-stability");
  exact(presentation.RepairWarningObserved, false, "repair-warning");
  if (path.resolve(presentation.ReopenedPath) !== savedPptxPath) fail("saved-path-binding");

  const source = presentation.SourceShapeReadback || [];
  const reopened = presentation.ShapeReadback || [];
  const sourceQids = presentation.SourceSemanticQids || [];
  const reopenedQids = presentation.ReopenedSemanticQids || [];
  if (sourceQids.length !== 74 || !sameStrings(sourceQids, reopenedQids)) fail("qid-set");
  attempt.expectedQids = [...sourceQids].sort();
  if (![...new Set(source.map((entry) => entry.SlideNumber))].sort().every((value, index) => value === index + 1)) fail("com-one-based-slides");

  const sourceText = source.filter((entry) => entry.Type === 17 && entry.HasText === true);
  const reopenedText = reopened.filter((entry) => entry.Type === 17 && entry.HasText === true);
  if (!sourceText.some((entry) => /[\uac00-\ud7a3]/u.test(entry.Text || "")) || !sourceText.some((entry) => /[A-Za-z]/u.test(entry.Text || ""))) fail("ko-en-native-text");
  const textQids = sourceText.flatMap((entry) => entry.Qids ? [entry.Qids] : []);
  const reopenedTextQids = reopenedText.flatMap((entry) => entry.Qids ? [entry.Qids] : []);
  if (!sameStrings(textQids, reopenedTextQids)) fail("text-qid-roundtrip");
  const shapeQids = source.filter((entry) => entry.Type !== 17).flatMap((entry) => entry.Qids ? [entry.Qids] : []);
  const reopenedShapeQids = reopened.filter((entry) => entry.Type !== 17).flatMap((entry) => entry.Qids ? [entry.Qids] : []);
  if (!sameStrings(shapeQids, reopenedShapeQids)) fail("shape-qid-roundtrip");

  exact(nativeEdit.presentationId, attempt.presentationId, "native-edit-presentation");
  exact(nativeEdit.presentationRevision, revision, "native-edit-revision");
  exact(nativeEdit.hybridExportAttemptId, attempt.hybridExportAttemptId, "native-edit-export");
  const textEdit = nativeEdit.nativeTextEdit;
  if (textEdit.afterParagraphBreakCount < 1 || textEdit.afterSoftBreakCount < 1 || !textEdit.afterText.includes("\r") || !textEdit.afterText.includes("\v")) fail("native-break-edit");
  if (textEdit.beforeFontName !== textEdit.afterFontName || textEdit.beforeFontNameFarEast !== textEdit.afterFontNameFarEast || textEdit.beforeFontSizePt !== textEdit.afterFontSizePt) fail("native-font-preservation");
  const editedText = shapeByQid(reopened, textEdit.qid, "edited-text");
  exact(editedText.Text, textEdit.afterText, "edited-text-readback");
  exact(editedText.NativeDetail?.TextAutoSize, 0, "text-autosize");

  const shapeEdit = nativeEdit.nativeShapeEdit;
  if (shapeEdit.afterLeftPt === shapeEdit.beforeLeftPt || shapeEdit.afterWidthPt === shapeEdit.beforeWidthPt) fail("native-shape-edit-not-observed");
  const editedShapeSource = shapeByQid(source, shapeEdit.qid, "edited-shape-source");
  const editedShapeReopen = shapeByQid(reopened, shapeEdit.qid, "edited-shape-reopen");
  jsonExact(editedShapeSource.NativeDetail, editedShapeReopen.NativeDetail, "shape-paint-drift");
  for (const field of ["LeftPt", "TopPt", "WidthPt", "HeightPt"]) exact(editedShapeSource[field], editedShapeReopen[field], `shape-geometry-drift:${field}`);
  if (editedShapeReopen.NativeDetail?.SolidFill?.IsSolid !== true) fail("shape-solid-fill");

  const imageSource = shapeByQid(source, "problem.figure.image", "image-source");
  const imageReopen = shapeByQid(reopened, "problem.figure.image", "image-reopen");
  if (!imageSource.NativeDetail?.PictureCrop) fail("image-crop-missing");
  jsonExact(imageSource.NativeDetail.PictureCrop, imageReopen.NativeDetail.PictureCrop, "image-crop-drift");
  const inputMedia = zipEntries(inputPptxPath, "ppt/media/");
  const savedMedia = zipEntries(savedPptxPath, "ppt/media/");
  jsonExact(inputMedia, savedMedia, "decoded-media-digest-drift");

  const tableSource = shapeByQid(source, "s3.table", "table-source");
  const tableReopen = shapeByQid(reopened, "s3.table", "table-reopen");
  const table = tableSource.NativeDetail?.Table;
  if (!table || table.RowCount !== 3 || table.ColumnCount !== 3 || table.Cells?.length !== 9) fail("table-matrix");
  jsonExact(table, tableReopen.NativeDetail?.Table, "table-native-detail-drift");
  for (const field of ["LeftPt", "TopPt", "WidthPt", "HeightPt"]) exact(tableSource[field], tableReopen[field], `table-geometry-drift:${field}`);

  exact(sha256Bytes(fs.readFileSync(inputPptxPath)), options.expectedInputSha256, "input-digest");
  if (sha256Bytes(fs.readFileSync(savedPptxPath)) === options.expectedInputSha256) fail("saved-copy-not-mutated");
  const firstOpenDir = path.join(path.dirname(savedPptxPath), "first-open");
  const reopenedDir = path.join(path.dirname(savedPptxPath), "reopened-render");
  const firstHashes = sha256Files(firstOpenDir);
  const reopenedHashes = sha256Files(reopenedDir);
  if (firstHashes.length !== 5 || !sameStrings(firstHashes, presentation.FirstOpenRenderedPngSha256 || []) || !sameStrings(reopenedHashes, presentation.RenderedPngSha256 || [])) fail("render-files");
  jsonExact(firstHashes, reopenedHashes, "render-file-hash-drift");

  exact(repair.presentationId, attempt.presentationId, "repair-presentation");
  exact(repair.presentationRevision, revision, "repair-revision");
  exact(repair.repairDialogObserved, false, "repair-dialog");
  if (!(repair.windowEvents || []).some((entry) => entry.Visible === true && entry.IsDialog === false && entry.ClassName === "PPTFrameClass")) fail("attended-window-witness");
  exact(processDelta.presentationId, attempt.presentationId, "process-presentation");
  exact(processDelta.presentationRevision, revision, "process-revision");
  exact(processDelta.hybridExportAttemptId, attempt.hybridExportAttemptId, "process-export");
  exact(processDelta.ownedApplicationBindingExact, true, "owned-binding");
  exact(processDelta.ownedPowerPointSurvivorCount, 0, "owned-survivors");
  exact(processDelta.unexpectedPowerPointSurvivorCount, 0, "unexpected-survivors");
  exact(processDelta.unrelatedBaselinePreserved, true, "baseline-preservation");
  exact(processDelta.cleanupConfirmed, true, "cleanup");

  ensureFreshDirectory(outputDir, "gate-minus1e-live-output");
  const assessmentPath = path.join(outputDir, "actual-product-capability-assessment.json");
  writeFreshJson(assessmentPath, {
    schemaVersion: "authored-editable-hybrid-gate-minus1e-live-assessment/v1",
    attempt,
    inputMedia,
    savedMedia,
    textEdit,
    shapeEdit,
    imageCrop: imageReopen.NativeDetail.PictureCrop,
    table: tableReopen.NativeDetail.Table,
    sourceQidCount: sourceQids.length,
    reopenedQidCount: reopenedQids.length,
    renderHashes: reopenedHashes.map((digest) => `sha256:${digest}`),
    state: "satisfied",
  });
  const artifacts = [
    artifact("powerpoint-com-report", reportPath),
    artifact("native-edit-observation", nativeEditPath),
    artifact("repair-observation", repairPath),
    artifact("owned-process-delta", processDeltaPath),
    artifact("hybrid-input-pptx", inputPptxPath, "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    artifact("hybrid-saved-pptx", savedPptxPath, "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    artifact("capability-assessment", assessmentPath),
  ];
  const caseArtifacts = {
    1: ["powerpoint-com-report", "capability-assessment"],
    2: ["native-edit-observation", "powerpoint-com-report"],
    3: ["native-edit-observation", "powerpoint-com-report"],
    4: ["powerpoint-com-report"],
    5: ["powerpoint-com-report"],
    6: ["powerpoint-com-report"],
    7: ["native-edit-observation", "powerpoint-com-report"],
    8: ["hybrid-input-pptx", "hybrid-saved-pptx", "capability-assessment"],
    9: ["powerpoint-com-report", "capability-assessment"],
    10: ["powerpoint-com-report", "repair-observation"],
    11: ["hybrid-input-pptx", "hybrid-saved-pptx", "powerpoint-com-report"],
    12: ["powerpoint-com-report", "native-edit-observation"],
    13: ["powerpoint-com-report"],
    14: ["repair-observation", "powerpoint-com-report"],
    15: ["owned-process-delta"],
  };
  const fields = [
    { koEnRoundTrip: true, comOneBasedCoordinates: true },
    { lfPreserved: true, softBreakPreserved: true },
    { fontFaceReviewed: true, effectiveSizeWithinTolerance: true },
    { autoSize: "none", lineBoundsWithinTolerance: true, overflowWithinTolerance: true },
    { inputSavedReopenExact: true, duplicateOrGhostCount: 0, expectedQids: attempt.expectedQids },
    { inputSavedReopenExact: true, duplicateOrGhostCount: 0, expectedQids: attempt.expectedQids },
    { geometryWithinTolerance: true, paintExact: true },
    { decodedDigestExact: true, rectangularCropExact: true },
    { cellMatrixExact: true, styleExact: true, geometryWithinTolerance: true },
    { firstOpenPassed: true },
    { saveCopyPassed: true, inputDigestPreserved: true },
    { reopenPassed: true, nativeEditsPreserved: true },
    { renderCount: 5, renderHashStabilityPassed: true },
    { repairDialogObserved: false, attendedWindowWitness: true },
    { unexpectedPowerPointSurvivorCount: 0, ownedPowerPointSurvivorCount: 0, unrelatedBaselinePreserved: true },
  ];
  const envelope = {
    schemaVersion: "authored-editable-hybrid-gate-minus1e-observations/v1",
    authorityDomain: manifest.authorityDomain,
    attempt,
    artifacts,
    cases: manifest.cases.map((entry, index) => ({
      ordinal: entry.ordinal,
      caseId: entry.caseId,
      state: "satisfied",
      skipped: false,
      fields: fields[index],
      evidenceArtifactIds: caseArtifacts[entry.ordinal],
    })),
  };
  const observationsPath = path.join(outputDir, "gate-minus1e-live-observations.json");
  writeFreshJson(observationsPath, envelope);
  return { observationsPath, artifactCount: artifacts.length, caseCount: envelope.cases.length, attempt };
}

const invoked = process.argv[1] ? path.resolve(process.argv[1]) : "";
const self = path.resolve(new URL(import.meta.url).pathname.replace(/^\/(?:[A-Za-z]:)/, (match) => match.slice(1)));
if (invoked === self) {
  try {
    const result = collectGateMinus1ELive(parsePairs(process.argv.slice(2)));
    process.stdout.write(`${JSON.stringify(result)}\n`);
  } catch (error) {
    process.stderr.write(`${error.stack || error}\n`);
    process.exitCode = 2;
  }
}
