import fs from "node:fs";
import path from "node:path";

import {
  canonicalSha256Excluding,
} from "../authored-bounded-release-contracts.mjs";

const DIGEST = /^sha256:[0-9a-f]{64}$/;
const COMPARISON_ID = /-(\d{2})$/;
const CROSS_RENDERER_PREFIXES = [
  "fidelity-source-vs-first",
  "fidelity-source-vs-reopen",
  "hybrid-source-vs-first",
  "hybrid-source-vs-reopen",
  "hybrid-vs-fidelity-first",
  "hybrid-vs-fidelity-reopen",
];
const EXACT_STABILITY_PREFIXES = [
  "fidelity-first-vs-reopen",
  "hybrid-first-vs-reopen",
];
const GEOMETRY_FIELDS = ["LeftPt", "TopPt", "WidthPt", "HeightPt"];

function readJson(filePath) {
  return JSON.parse(fs.readFileSync(filePath, "utf8"));
}

function sha256File(filePath) {
  return `sha256:${createHash("sha256").update(fs.readFileSync(filePath)).digest("hex")}`;
}

import { createHash } from "node:crypto";

function writeFreshJson(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, `${JSON.stringify(value, null, 2)}\n`, { flag: "wx" });
}

function fail(code) {
  throw new Error(`bounded-v3-cross-renderer:${code}`);
}

function integer(value, label, minimum = 0, maximum = Number.MAX_SAFE_INTEGER) {
  if (!Number.isInteger(value) || value < minimum || value > maximum) fail(`${label}-invalid`);
  return value;
}

function shapeKey(shape) {
  const qids = typeof shape.Qids === "string" ? shape.Qids.split(",").map((entry) => entry.trim()).filter(Boolean).sort().join(",") : "";
  if (!Number.isInteger(shape.SlideNumber) || shape.SlideNumber < 1 || shape.SlideNumber > 8) fail("com-shape-slide-invalid");
  if (!qids && typeof shape.Name !== "string") fail("com-shape-identity-missing");
  return `${shape.SlideNumber}:${qids || shape.Name}`;
}

function shapeMap(shapes, label) {
  if (!Array.isArray(shapes)) fail(`${label}-shape-array-missing`);
  const result = new Map();
  for (const shape of shapes) {
    const key = shapeKey(shape);
    if (result.has(key)) fail(`${label}-duplicate-shape:${key}`);
    result.set(key, shape);
  }
  return result;
}

function normalizedText(value) {
  return value === null || value === undefined ? "" : String(value);
}

function normalizedTypeface(value) {
  return normalizedText(value).trim().toLocaleLowerCase("en-US");
}

function structuralReadback(comPath) {
  const report = readJson(comPath);
  if (report.schemaVersion !== "authored-powerpoint-com-snapshot/v1" || report.authorityDomain !== "actual_product") fail(`com-schema:${path.basename(comPath)}`);
  if (!["fidelity", "hybrid"].includes(report.exportMode) || !["first-open", "reopen"].includes(report.phase)) fail(`com-mode-phase:${path.basename(comPath)}`);
  if (report.repairDialogObserved !== false || report.unexpectedPowerPointProcessCount !== 0) fail(`com-runtime-defect:${report.exportMode}:${report.phase}`);
  const source = shapeMap(report.sourceShapeReadback, "source");
  const reopened = shapeMap(report.reopenedShapeReadback, "reopened");
  const missing = [...source.keys()].filter((key) => !reopened.has(key));
  const unexpected = [...reopened.keys()].filter((key) => !source.has(key));
  const slides = new Map(Array.from({ length: 8 }, (_, index) => [index + 1, {
    slideNumber: index + 1,
    comparedShapeCount: 0,
    missingShapeCount: 0,
    unexpectedShapeCount: 0,
    fontDifferenceCount: 0,
    wrapDifferenceCount: 0,
    textDifferenceCount: 0,
    maxGeometrySideDeltaMicroPoint: 0,
    geometryDifferenceCount: 0,
  }]));
  for (const key of missing) slides.get(source.get(key).SlideNumber).missingShapeCount += 1;
  for (const key of unexpected) slides.get(reopened.get(key).SlideNumber).unexpectedShapeCount += 1;
  for (const [key, before] of source.entries()) {
    const after = reopened.get(key);
    if (!after) continue;
    const slide = slides.get(before.SlideNumber);
    slide.comparedShapeCount += 1;
    if (normalizedTypeface(before.Typeface) !== normalizedTypeface(after.Typeface) || normalizedTypeface(before.EastAsianTypeface) !== normalizedTypeface(after.EastAsianTypeface)) slide.fontDifferenceCount += 1;
    if (integer(before.TextLineBreakCount ?? 0, "source-line-break") !== integer(after.TextLineBreakCount ?? 0, "reopened-line-break")) slide.wrapDifferenceCount += 1;
    if (normalizedText(before.Text) !== normalizedText(after.Text)) slide.textDifferenceCount += 1;
    let changed = false;
    for (const field of GEOMETRY_FIELDS) {
      const left = Number(before[field]);
      const right = Number(after[field]);
      if (!Number.isFinite(left) || !Number.isFinite(right)) fail(`geometry-non-finite:${key}:${field}`);
      const delta = Math.round(Math.abs(left - right) * 1_000_000);
      slide.maxGeometrySideDeltaMicroPoint = Math.max(slide.maxGeometrySideDeltaMicroPoint, delta);
      if (delta !== 0) changed = true;
    }
    if (changed) slide.geometryDifferenceCount += 1;
  }
  return {
    exportMode: report.exportMode,
    phase: report.phase,
    reportDigest: sha256File(comPath),
    missingShapeCount: missing.length,
    unexpectedShapeCount: unexpected.length,
    slides: [...slides.values()],
  };
}

function visualPolicy(policy) {
  if (policy.schemaVersion !== "authored-evidence-policy/v2") fail("policy-schema");
  if (canonicalSha256Excluding(policy, ["$.policyDigest"]) !== policy.policyDigest) fail("policy-digest");
  const visual = policy.visualMetrics;
  const geometry = policy.geometryAndOverflow;
  return {
    policyDigest: policy.policyDigest,
    changedPixelRatioMaxPpm: integer(visual?.changedPixelRatioMaxPpm, "policy-changed-pixel"),
    block8LumaSsimMinMillionths: integer(visual?.block8LumaSsimMinMillionths, "policy-ssim", 0, 1_000_000),
    meanAbsoluteChannelDeltaMaxMilli: integer(visual?.meanAbsoluteChannelDeltaMaxMilli, "policy-mae"),
    rmseMaxMilli: integer(visual?.rmseMaxMilli, "policy-rmse"),
    maxGeometrySideDeltaMicroPoint: integer(geometry?.nativeShapeSideDriftMaxMicroPoint, "policy-geometry"),
    categoricalDifferenceMax: 0,
  };
}

function comparisonPrefix(comparisonId) {
  const suffix = comparisonId.match(COMPARISON_ID);
  if (!suffix) fail(`comparison-id:${comparisonId}`);
  return { slideNumber: Number(suffix[1]), prefix: comparisonId.slice(0, -3) };
}

function validateMetric(entry, label) {
  return {
    comparisonId: entry.comparisonId,
    baselineArtifactId: entry.baselineArtifactId,
    candidateArtifactId: entry.candidateArtifactId,
    changedPixelRatioPpm: integer(entry.changedPixelRatioPpm, `${label}-changed-pixel`, 0, 1_000_000),
    block8LumaSsimMillionths: integer(entry.block8LumaSsimMillionths, `${label}-ssim`, 0, 1_000_000),
    meanAbsoluteChannelDeltaMilli: integer(entry.meanAbsoluteChannelDeltaMilli, `${label}-mae`),
    rmseMilli: integer(entry.rmseMilli, `${label}-rmse`),
  };
}

export function calibrateCrossRenderer(options) {
  const metricsPath = path.resolve(options.metricsPath);
  const policyPath = path.resolve(options.policyPath);
  const outputPath = path.resolve(options.outputPath);
  const metrics = readJson(metricsPath);
  const policy = readJson(policyPath);
  const thresholds = visualPolicy(policy);
  if (!Array.isArray(metrics.comparisons) || metrics.comparisons.length !== 64) fail(`comparison-cardinality:${metrics.comparisons?.length ?? "missing"}/64`);
  const images = new Set((metrics.images || []).map((image) => image.artifactId));
  if (images.size !== 48) fail(`image-cardinality:${images.size}/48`);
  const slideComparisons = new Map(Array.from({ length: 8 }, (_, index) => [index + 1, []]));
  const seen = new Set();
  for (const raw of metrics.comparisons) {
    if (typeof raw.comparisonId !== "string" || seen.has(raw.comparisonId)) fail(`comparison-duplicate:${raw.comparisonId}`);
    seen.add(raw.comparisonId);
    if (!images.has(raw.baselineArtifactId) || !images.has(raw.candidateArtifactId)) fail(`comparison-artifact-binding:${raw.comparisonId}`);
    const { slideNumber, prefix } = comparisonPrefix(raw.comparisonId);
    if (!slideComparisons.has(slideNumber) || ![...CROSS_RENDERER_PREFIXES, ...EXACT_STABILITY_PREFIXES].includes(prefix)) fail(`comparison-axis:${raw.comparisonId}`);
    slideComparisons.get(slideNumber).push({ prefix, ...validateMetric(raw, raw.comparisonId) });
  }
  const visualSlides = [];
  let thresholdFailureCount = 0;
  for (const [slideNumber, comparisons] of slideComparisons.entries()) {
    const prefixes = comparisons.map((entry) => entry.prefix).sort();
    const expected = [...CROSS_RENDERER_PREFIXES, ...EXACT_STABILITY_PREFIXES].sort();
    if (JSON.stringify(prefixes) !== JSON.stringify(expected)) fail(`slide-comparison-set:${slideNumber}`);
    const crossRenderer = comparisons.filter((entry) => CROSS_RENDERER_PREFIXES.includes(entry.prefix));
    const exactStability = comparisons.filter((entry) => EXACT_STABILITY_PREFIXES.includes(entry.prefix));
    const failures = [];
    for (const entry of crossRenderer) {
      if (entry.changedPixelRatioPpm > thresholds.changedPixelRatioMaxPpm) failures.push(`${entry.comparisonId}:changedPixelRatioPpm`);
      if (entry.block8LumaSsimMillionths < thresholds.block8LumaSsimMinMillionths) failures.push(`${entry.comparisonId}:block8LumaSsimMillionths`);
      if (entry.meanAbsoluteChannelDeltaMilli > thresholds.meanAbsoluteChannelDeltaMaxMilli) failures.push(`${entry.comparisonId}:meanAbsoluteChannelDeltaMilli`);
      if (entry.rmseMilli > thresholds.rmseMaxMilli) failures.push(`${entry.comparisonId}:rmseMilli`);
    }
    for (const entry of exactStability) {
      if (entry.changedPixelRatioPpm !== 0 || entry.block8LumaSsimMillionths !== 1_000_000 || entry.meanAbsoluteChannelDeltaMilli !== 0 || entry.rmseMilli !== 0) failures.push(`${entry.comparisonId}:exact-stability`);
    }
    thresholdFailureCount += failures.length;
    visualSlides.push({
      slideNumber,
      crossRendererComparisonCount: crossRenderer.length,
      exactStabilityComparisonCount: exactStability.length,
      observed: {
        maxChangedPixelRatioPpm: Math.max(...crossRenderer.map((entry) => entry.changedPixelRatioPpm)),
        minBlock8LumaSsimMillionths: Math.min(...crossRenderer.map((entry) => entry.block8LumaSsimMillionths)),
        maxMeanAbsoluteChannelDeltaMilli: Math.max(...crossRenderer.map((entry) => entry.meanAbsoluteChannelDeltaMilli)),
        maxRmseMilli: Math.max(...crossRenderer.map((entry) => entry.rmseMilli)),
      },
      thresholdPassed: failures.length === 0,
      failures,
    });
  }
  const comPaths = (options.comPaths || []).map((entry) => path.resolve(entry));
  if (comPaths.length !== 4) fail(`com-report-cardinality:${comPaths.length}/4`);
  const structuralReports = comPaths.map(structuralReadback);
  const reportKeys = structuralReports.map((entry) => `${entry.exportMode}:${entry.phase}`).sort();
  if (JSON.stringify(reportKeys) !== JSON.stringify(["fidelity:first-open", "fidelity:reopen", "hybrid:first-open", "hybrid:reopen"])) fail("com-report-axis-set");
  const structuralSlides = Array.from({ length: 8 }, (_, index) => {
    const slideNumber = index + 1;
    const samples = structuralReports.map((report) => ({ exportMode: report.exportMode, phase: report.phase, ...report.slides[index] }));
    const observed = {
      maxGeometrySideDeltaMicroPoint: Math.max(...samples.map((sample) => sample.maxGeometrySideDeltaMicroPoint)),
      fontDifferenceCount: samples.reduce((sum, sample) => sum + sample.fontDifferenceCount, 0),
      wrapDifferenceCount: samples.reduce((sum, sample) => sum + sample.wrapDifferenceCount, 0),
      textDifferenceCount: samples.reduce((sum, sample) => sum + sample.textDifferenceCount, 0),
      missingShapeCount: samples.reduce((sum, sample) => sum + sample.missingShapeCount, 0),
      unexpectedShapeCount: samples.reduce((sum, sample) => sum + sample.unexpectedShapeCount, 0),
    };
    const thresholdPassed = observed.maxGeometrySideDeltaMicroPoint <= thresholds.maxGeometrySideDeltaMicroPoint && [observed.fontDifferenceCount, observed.wrapDifferenceCount, observed.textDifferenceCount, observed.missingShapeCount, observed.unexpectedShapeCount].every((value) => value === 0);
    return { slideNumber, observed, thresholdPassed, samples };
  });
  const structuralFailureCount = structuralSlides.filter((entry) => !entry.thresholdPassed).length;
  const state = thresholdFailureCount === 0 && structuralFailureCount === 0 ? "accepted" : "rejected";
  const result = {
    schemaVersion: "authored-bounded-v3-cross-renderer-calibration/v1",
    authorityDomain: "actual_product_measurement",
    sourcePurpose: "threshold-calibration-only-not-promotion-authority",
    presentationId: metrics.presentationId,
    presentationRevision: metrics.presentationRevision,
    generationAttemptId: metrics.generationAttemptId,
    fidelityExportAttemptId: metrics.fidelityExportAttemptId,
    hybridExportAttemptId: metrics.hybridExportAttemptId,
    inputs: {
      metrics: { path: metricsPath, sha256: sha256File(metricsPath) },
      policy: { path: policyPath, sha256: sha256File(policyPath), policyDigest: thresholds.policyDigest },
      comReports: structuralReports.map((entry, index) => ({ path: comPaths[index], sha256: entry.reportDigest, exportMode: entry.exportMode, phase: entry.phase })),
    },
    thresholds,
    visualSlides,
    structuralSlides,
    thresholdFailureCount,
    structuralFailureCount,
    state,
    promotionReady: false,
    deploymentAuthorized: false,
    resultDigest: "",
  };
  result.resultDigest = canonicalSha256Excluding(result, ["$.resultDigest"]);
  if (!DIGEST.test(result.resultDigest)) fail("result-digest");
  if (fs.existsSync(outputPath)) fail("output-must-be-fresh");
  writeFreshJson(outputPath, result);
  if (state !== "accepted") fail(`calibration-rejected:${thresholdFailureCount}:${structuralFailureCount}`);
  return result;
}

function parseArgs(argv) {
  const options = { comPaths: [] };
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    const value = argv[index + 1];
    if (!key?.startsWith("--") || value === undefined) fail(`invalid-argument:${key}`);
    if (key === "--com-report") options.comPaths.push(value);
    else options[key.slice(2).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = value;
  }
  for (const key of ["metricsPath", "policyPath", "outputPath"]) if (!options[key]) fail(`argument-missing:${key}`);
  return options;
}

const invoked = process.argv[1] ? path.resolve(process.argv[1]) : "";
const self = path.resolve(new URL(import.meta.url).pathname.replace(/^\/(?:[A-Za-z]:)/, (match) => match.slice(1)));
if (invoked === self) {
  try {
    const result = calibrateCrossRenderer(parseArgs(process.argv.slice(2)));
    process.stdout.write(`${JSON.stringify({ state: result.state, slideCount: result.visualSlides.length, comparisonCount: result.visualSlides.reduce((sum, entry) => sum + entry.crossRendererComparisonCount + entry.exactStabilityComparisonCount, 0), thresholdFailureCount: result.thresholdFailureCount, structuralFailureCount: result.structuralFailureCount, resultDigest: result.resultDigest })}\n`);
  } catch (error) {
    process.stderr.write(`${error.stack || error}\n`);
    process.exitCode = 2;
  }
}
