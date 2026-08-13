import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { lintHtml, loadPolicy, sanitizeHtml } from "../policy/html-policy.mjs";

const testDirectory = path.dirname(fileURLToPath(import.meta.url));
const experimentRoot = path.resolve(testDirectory, "..");
const corpusRoot = path.join(experimentRoot, "corpus");
const manifestPath = path.join(corpusRoot, "manifest.json");
const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
const policy = loadPolicy();

assert.equal(manifest.mode, "static-analysis-only");
assert.equal(manifest.defaultExecutionAllowed, false);
assert.equal(manifest.networkPolicy.dns, "deny");
assert.equal(manifest.networkPolicy.http, "deny");
assert.equal(manifest.networkPolicy.loopback, "deny");
assert.equal(manifest.networkPolicy.metadata, "deny");
assert.deepEqual(manifest.networkPolicy.allowedExternalTargets, []);
assert.equal(manifest.networkPolicy.metadataToken, policy.reservedMetadataToken);
assert.ok(manifest.cases.length <= manifest.limits.maxCases);

const forbiddenMetadataEndpoints = [
  /169\.254\.169\.254/i,
  /100\.100\.100\.200/i,
  /metadata\.google\.internal/i,
  /fd00:ec2::254/i,
];

const results = [];
for (const testCase of manifest.cases) {
  assert.equal(testCase.executionAllowed, false, `${testCase.id}: corpus cases must never be executable`);
  const absolutePath = path.resolve(corpusRoot, testCase.file);
  assert.ok(absolutePath.startsWith(`${corpusRoot}${path.sep}`), `${testCase.id}: path escaped corpus root`);
  const stat = fs.statSync(absolutePath);
  assert.ok(stat.size <= manifest.limits.maxCaseBytes, `${testCase.id}: fixture exceeds byte limit`);
  const source = fs.readFileSync(absolutePath, "utf8");

  for (const forbidden of forbiddenMetadataEndpoints) {
    assert.doesNotMatch(source, forbidden, `${testCase.id}: real metadata endpoint is forbidden`);
  }
  for (const match of source.matchAll(/https?:\/\/[^\s"'<>)]*/gi)) {
    const parsed = new URL(match[0]);
    const host = parsed.hostname.replace(/^\[|\]$/g, "").toLowerCase();
    const isLoopback = host === "localhost" || host === "::1" || /^127(?:\.\d{1,3}){3}$/.test(host);
    assert.ok(host.endsWith(".invalid") || isLoopback, `${testCase.id}: non-reserved HTTP host ${host}`);
    if (isLoopback) assert.equal(parsed.port, "9", `${testCase.id}: loopback fixture must use discard port 9`);
  }
  for (const match of source.matchAll(/file:\/\/\/[^\s"'<>)]*/gi)) {
    assert.ok(match[0].startsWith("file:///__AUTHORED_LOCAL_CANARY_ONLY__/"), `${testCase.id}: file URL is not a fictional canary mount`);
  }

  const lint = lintHtml(source, policy);
  const actualRuleIds = [...new Set(lint.issues.map((issue) => issue.ruleId))].sort();
  for (const expectedRuleId of testCase.expectedRuleIds) {
    assert.ok(actualRuleIds.includes(expectedRuleId), `${testCase.id}: expected ${expectedRuleId}; got ${actualRuleIds.join(", ")}`);
  }
  if (testCase.expectedRuleIds.length === 0) {
    assert.equal(lint.ok, true, `${testCase.id}: safe control was rejected: ${JSON.stringify(lint.issues)}`);
  } else {
    assert.equal(lint.ok, false, `${testCase.id}: hostile fixture was accepted`);
    assert.equal(testCase.hostStabilityRisk, true, `${testCase.id}: hostile case must be marked host-risk`);
  }

  const sanitized = sanitizeHtml(source, policy);
  const sanitizedLint = lintHtml(sanitized, policy);
  assert.equal(sanitizedLint.ok, true, `${testCase.id}: sanitizer output still violates policy: ${JSON.stringify(sanitizedLint.issues)}`);
  assert.doesNotMatch(sanitized, /<\s*(?:script|iframe|object|embed|svg|math|template)\b/i);
  assert.doesNotMatch(sanitized, /\son[a-z]+\s*=/i);
  results.push({ id: testCase.id, inputRuleIds: actualRuleIds, sanitizedBytes: Buffer.byteLength(sanitized, "utf8") });
}

const structuralChecks = [
  {
    name: "numeric HTML entity URL smuggling",
    html: '<img src="java&#x73;cript:void(0)" alt="entity">',
    ruleId: "U006",
  },
  {
    name: "control whitespace URL smuggling",
    html: '<img src="java\nscript:void(0)" alt="whitespace">',
    ruleId: "U006",
  },
  {
    name: "CSS escape ambiguity",
    html: '<style>.x { background: u\\72l(https://escape.authored-security.invalid/x); }</style>',
    ruleId: "C008",
  },
  {
    name: "non-allowlisted CSS property",
    html: '<style>.x { content: "rejected"; }</style>',
    ruleId: "C005",
  },
  {
    name: "unbalanced HTML",
    html: '<main><p>ambiguous</main>',
    ruleId: "H004",
  },
];
for (const check of structuralChecks) {
  const lint = lintHtml(check.html, policy);
  assert.ok(lint.issues.some((issue) => issue.ruleId === check.ruleId), `${check.name}: expected ${check.ruleId}`);
  const sanitized = sanitizeHtml(check.html, policy);
  assert.equal(lintHtml(sanitized, policy).ok, true, `${check.name}: sanitizer did not produce allowlisted HTML`);
}

console.log(JSON.stringify({ ok: true, cases: results.length, structuralChecks: structuralChecks.length, results }, null, 2));
