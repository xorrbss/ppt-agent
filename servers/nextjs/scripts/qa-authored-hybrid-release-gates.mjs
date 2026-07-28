import fs from "node:fs/promises";
import path from "node:path";

import { evaluateAuthoredHybridReleaseGates } from "./lib/authored-hybrid-release-gates.mjs";

function usage() {
  throw new Error(
    "Usage: qa-authored-hybrid-release-gates.mjs <baseline-quality.json> " +
      "<candidate-quality.json> <baseline-visual-metrics.json> " +
      "<candidate-visual-metrics.json> <candidate-element-map.json> <output.json> " +
      "[--baseline-element-map <element-map.json>] [--expected-slides 20]"
  );
}

function parseArguments(argv) {
  const positional = [];
  const options = { expectedSlides: 20 };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (!value.startsWith("--")) {
      positional.push(value);
      continue;
    }
    const next = argv[++index];
    if (!next) usage();
    if (value === "--baseline-element-map") options.baselineElementMap = next;
    else if (value === "--expected-slides") options.expectedSlides = Number(next);
    else usage();
  }
  if (positional.length !== 6 || !Number.isInteger(options.expectedSlides)) usage();
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
] = await Promise.all([
  readJson(baselineQualityPath),
  readJson(candidateQualityPath),
  readJson(baselineVisualPath),
  readJson(candidateVisualPath),
  readJson(candidateElementMapPath),
  options.baselineElementMap ? readJson(options.baselineElementMap) : null,
]);
const result = evaluateAuthoredHybridReleaseGates({
  baselineQuality,
  candidateQuality,
  baselineVisual,
  candidateVisual,
  baselineElementMap,
  candidateElementMap,
  expectedSlides: options.expectedSlides,
});
await fs.mkdir(path.dirname(outputPath), { recursive: true });
await fs.writeFile(outputPath, `${JSON.stringify(result, null, 2)}\n`);
console.log(JSON.stringify({ passed: result.passed, outputPath: path.resolve(outputPath) }));
if (!result.passed) process.exitCode = 2;
