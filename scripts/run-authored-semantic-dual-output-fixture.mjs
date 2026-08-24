#!/usr/bin/env node

/**
 * Run the smallest atomic check for the fresh eight-slide fixture and an
 * externally-produced evidence bundle. This command verifies identity and
 * hashes only; it never renders, opens PowerPoint, or promotes a result.
 */

import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { verifyFixture } from "./verify-authored-semantic-fixtures.mjs";
import { verifyEvidenceManifest } from "./verify-authored-semantic-vertical-slice-evidence.mjs";

function fail(message) {
  throw new Error(message);
}

function args(argv) {
  const fixtureIndex = argv.indexOf("--fixture");
  const evidenceIndex = argv.indexOf("--evidence");
  if (fixtureIndex < 0 || evidenceIndex < 0 || !argv[fixtureIndex + 1] || !argv[evidenceIndex + 1]) fail("usage: --fixture <json> --evidence <json>");
  return { fixture: path.resolve(argv[fixtureIndex + 1]), evidence: path.resolve(argv[evidenceIndex + 1]) };
}

export function runFixture(fixturePath, evidencePath) {
  const fixture = verifyFixture(fixturePath);
  const evidence = verifyEvidenceManifest(evidencePath);
  for (const key of ["fixtureId", "presentationId", "presentationRevision", "operationId", "generationAttemptId"]) {
    if (evidence[key] !== fixture[key]) fail(`atomic-lineage-mismatch:${key}`);
  }
  if (fixture.status !== "not-verified" || evidence.status !== "not-verified") fail("success-claim-not-allowed");
  return { status: "not-verified", fixture, evidence };
}

if (process.argv[1] && path.resolve(fileURLToPath(import.meta.url)) === path.resolve(process.argv[1])) {
  try {
    const parsed = args(process.argv.slice(2));
    const result = runFixture(parsed.fixture, parsed.evidence);
    console.log(JSON.stringify(result, null, 2));
  } catch (error) {
    console.error(`authored-semantic-dual-output-invalid:${error.message}`);
    process.exitCode = 1;
  }
}
