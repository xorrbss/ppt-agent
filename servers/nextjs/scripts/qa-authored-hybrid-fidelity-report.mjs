import fs from "node:fs/promises";
import path from "node:path";

import {
  buildFidelityQaSummary,
  renderFidelityQaMarkdown,
} from "./lib/authored-hybrid-fidelity-report.mjs";

function usage() {
  throw new Error(
    "Usage: qa-authored-hybrid-fidelity-report.mjs <candidate-quality.json> " +
      "<candidate-visual-metrics.json> <candidate-element-map.json> <output-dir> " +
      "[--baseline-quality <quality.json>] [--baseline-visual <visual-metrics.json>] " +
      "[--baseline-element-map <element-map.json>] [--semantics <text-semantics-audit.json>] " +
      "[--release-gates <release-gates.json>] [--libreoffice <report.json>] " +
      "[--powerpoint <report.json>] [--expected-slides 20] [--fail-on-regression]"
  );
}

function parseArguments(argv) {
  const positional = [];
  const options = { expectedSlides: 20, failOnRegression: false };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (!value.startsWith("--")) { positional.push(value); continue; }
    if (value === "--fail-on-regression") { options.failOnRegression = true; continue; }
    const next = argv[++index];
    if (!next) usage();
    const key = {
      "--baseline-quality": "baselineQuality",
      "--baseline-visual": "baselineVisual",
      "--baseline-element-map": "baselineElementMap",
      "--semantics": "semantics",
      "--release-gates": "releaseGates",
      "--libreoffice": "libreOffice",
      "--powerpoint": "powerPoint",
      "--expected-slides": "expectedSlides",
    }[value];
    if (!key) usage();
    options[key] = key === "expectedSlides" ? Number(next) : next;
  }
  if (positional.length !== 4 || !Number.isInteger(options.expectedSlides)) usage();
  return { positional, ...options };
}

async function readJson(filePath) {
  if (!filePath) return null;
  return JSON.parse(await fs.readFile(filePath, "utf8"));
}

const options = parseArguments(process.argv.slice(2));
const [candidateQualityPath, candidateVisualPath, candidateElementMapPath, outputDirectory] = options.positional;
const inputs = await Promise.all([
  readJson(candidateQualityPath), readJson(candidateVisualPath), readJson(candidateElementMapPath),
  readJson(options.baselineQuality), readJson(options.baselineVisual), readJson(options.baselineElementMap),
  readJson(options.semantics), readJson(options.releaseGates), readJson(options.libreOffice), readJson(options.powerPoint),
]);
const summary = buildFidelityQaSummary({
  candidateQuality: inputs[0], candidateVisual: inputs[1], candidateElementMap: inputs[2],
  baselineQuality: inputs[3], baselineVisual: inputs[4], baselineElementMap: inputs[5],
  semantics: inputs[6], releaseGates: inputs[7], libreOffice: inputs[8], powerPoint: inputs[9],
  expectedSlides: options.expectedSlides,
});
const paths = {
  candidateQuality: path.resolve(candidateQualityPath), candidateVisual: path.resolve(candidateVisualPath),
  candidateElementMap: path.resolve(candidateElementMapPath), baselineVisual: options.baselineVisual ? path.resolve(options.baselineVisual) : null,
  montage: inputs[1]?.artifacts?.montage ?? null,
};
await fs.mkdir(outputDirectory, { recursive: true });
const jsonPath = path.join(outputDirectory, "fidelity-qa-summary.json");
const markdownPath = path.join(outputDirectory, "fidelity-qa-report.md");
await Promise.all([
  fs.writeFile(jsonPath, `${JSON.stringify(summary, null, 2)}\n`),
  fs.writeFile(markdownPath, renderFidelityQaMarkdown(summary, paths)),
]);
console.log(JSON.stringify({ passed: summary.aggregate.passed, jsonPath: path.resolve(jsonPath), markdownPath: path.resolve(markdownPath) }));
if (options.failOnRegression && !summary.aggregate.passed) process.exitCode = 2;
