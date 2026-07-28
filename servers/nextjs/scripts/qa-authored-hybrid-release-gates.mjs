import fs from "node:fs/promises";
import path from "node:path";

import {
  DEFAULT_AUTHORED_HYBRID_RELEASE_THRESHOLDS,
  evaluateAuthoredHybridReleaseGates,
} from "./lib/authored-hybrid-release-gates.mjs";

function usage() {
  throw new Error(
    "Usage: qa-authored-hybrid-release-gates.mjs <baseline-quality.json> " +
      "<candidate-quality.json> <baseline-visual-metrics.json> " +
      "<candidate-visual-metrics.json> <candidate-element-map.json> <output.json> " +
      "[--baseline-element-map <element-map.json>] " +
      "[--baseline-text-semantics <report.json>] " +
      "[--candidate-text-semantics <report.json>] " +
      "[--candidate-package-health <report.json>] " +
      "[--candidate-powerpoint-validation <report.json>] [--expected-slides 20] " +
      "[--slide-mae-increase 0.10] [--slide-bad-pixel-increase 0.001] " +
      "[--text-bbox-movement-px 1] [--line-count-delta 0] " +
      "[--new-overflow-clipping 0]"
  );
}

function parseArguments(argv) {
  const positional = [];
  const options = {
    expectedSlides: 20,
    thresholds: { ...DEFAULT_AUTHORED_HYBRID_RELEASE_THRESHOLDS },
  };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (!value.startsWith("--")) {
      positional.push(value);
      continue;
    }
    const next = argv[++index];
    if (!next) usage();
    if (value === "--baseline-element-map") options.baselineElementMap = next;
    else if (value === "--baseline-text-semantics") {
      options.baselineTextSemantics = next;
    } else if (value === "--candidate-text-semantics") {
      options.candidateTextSemantics = next;
    } else if (value === "--candidate-package-health") {
      options.candidatePackageHealth = next;
    } else if (value === "--candidate-powerpoint-validation") {
      options.candidatePowerPointValidation = next;
    } else if (value === "--expected-slides") {
      options.expectedSlides = Number(next);
    } else if (value === "--slide-mae-increase") {
      options.thresholds.slideMaeIncrease = Number(next);
    } else if (value === "--slide-bad-pixel-increase") {
      options.thresholds.slideBadPixelRatioIncrease = Number(next);
    } else if (value === "--text-bbox-movement-px") {
      options.thresholds.textBboxMovementPx = Number(next);
    } else if (value === "--line-count-delta") {
      options.thresholds.lineCountDelta = Number(next);
    } else if (value === "--new-overflow-clipping") {
      options.thresholds.newOverflowClipping = Number(next);
    } else usage();
  }
  if (
    positional.length !== 6 ||
    !Number.isInteger(options.expectedSlides) ||
    options.expectedSlides < 1 ||
    Object.values(options.thresholds).some(
      (value) => !Number.isFinite(value) || value < 0
    )
  ) {
    usage();
  }
  return { positional, ...options };
}

async function readJson(filePath) {
  return JSON.parse(await fs.readFile(filePath, "utf8"));
}

const options = parseArguments(process.argv.slice(2));
const [
  baselineQualityPath,
  candidateQualityPath,
  baselineVisualPath,
  candidateVisualPath,
  candidateElementMapPath,
  outputPath,
] = options.positional;
const [
  baselineQuality,
  candidateQuality,
  baselineVisual,
  candidateVisual,
  candidateElementMap,
  baselineElementMap,
  baselineTextSemantics,
  candidateTextSemantics,
  candidatePackageHealth,
  candidatePowerPointValidation,
] = await Promise.all([
  readJson(baselineQualityPath),
  readJson(candidateQualityPath),
  readJson(baselineVisualPath),
  readJson(candidateVisualPath),
  readJson(candidateElementMapPath),
  options.baselineElementMap ? readJson(options.baselineElementMap) : null,
  options.baselineTextSemantics
    ? readJson(options.baselineTextSemantics)
    : null,
  options.candidateTextSemantics
    ? readJson(options.candidateTextSemantics)
    : null,
  options.candidatePackageHealth
    ? readJson(options.candidatePackageHealth)
    : null,
  options.candidatePowerPointValidation
    ? readJson(options.candidatePowerPointValidation)
    : null,
]);
const result = evaluateAuthoredHybridReleaseGates({
  baselineQuality,
  candidateQuality,
  baselineVisual,
  candidateVisual,
  baselineElementMap,
  candidateElementMap,
  baselineTextSemantics,
  candidateTextSemantics,
  candidatePackageHealth,
  candidatePowerPointValidation,
  expectedSlides: options.expectedSlides,
  thresholds: options.thresholds,
  // A release CLI invocation must fail closed when hard-gate evidence was not
  // generated. Direct evaluator callers may opt into individual evidence sets
  // for focused unit tests.
  requirePackageHealth: true,
  requirePowerPointValidation: true,
  requireTextSemantics: true,
  requireLayoutEvidence: true,
});
await fs.mkdir(path.dirname(outputPath), { recursive: true });
await fs.writeFile(outputPath, `${JSON.stringify(result, null, 2)}\n`);
console.log(JSON.stringify({ passed: result.passed, outputPath: path.resolve(outputPath) }));
if (!result.passed) process.exitCode = 2;
