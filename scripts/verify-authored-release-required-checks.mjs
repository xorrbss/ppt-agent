#!/usr/bin/env node

import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const configPath = path.join(root, "config", "authored-release-required-checks.json");
const pipelinePath = path.join(root, "bitbucket-pipelines.yml");

function fail(message) {
  const error = new Error(message);
  error.code = "AUTHORED_RELEASE_REQUIRED_CHECKS_BLOCKED";
  throw error;
}

function branchBlock(source, branch) {
  const marker = `    ${branch}:\n`;
  const start = source.indexOf(marker);
  if (start < 0) return "";
  const after = source.slice(start + marker.length);
  const next = after.search(/\n    [^\s].*:\n/u);
  return next < 0 ? after : after.slice(0, next);
}

export async function verifyConfigOnly() {
  const [configSource, pipeline] = await Promise.all([
    readFile(configPath, "utf8"),
    readFile(pipelinePath, "utf8"),
  ]);
  const config = JSON.parse(configSource);
  if (
    config.schemaVersion !== 1 ||
    !Array.isArray(config.requiredPipelineSteps) ||
    !Array.isArray(config.requiredPipelineAnchors) ||
    !Array.isArray(config.protectedBranches)
  ) {
    fail("invalid authored release required-check configuration");
  }
  for (const step of config.requiredPipelineSteps) {
    if (typeof step !== "string" || !step.trim() || !pipeline.includes(`name: ${step}`)) {
      fail(`pipeline does not define required step: ${step}`);
    }
  }
  for (const branch of config.protectedBranches) {
    const block = branchBlock(pipeline, branch);
    for (const anchor of config.requiredPipelineAnchors) {
      if (!block || !block.includes(anchor)) {
        fail(`protected branch ${branch} does not run required pipeline anchor: ${anchor}`);
      }
    }
  }
  const pullRequestStart = pipeline.indexOf('    "**":\n');
  const pullRequestBlock = pullRequestStart < 0
    ? ""
    : pipeline.slice(pullRequestStart, pipeline.indexOf("\n  branches:", pullRequestStart));
  for (const anchor of config.requiredPipelineAnchors) {
    if (!pullRequestBlock.includes(anchor)) {
      fail(`pull requests do not run required pipeline anchor: ${anchor}`);
    }
  }
  return { status: "configured", repository: config.repository, protectedBranches: config.protectedBranches };
}

export async function verifyLive(fetchImpl = fetch, environment = process.env) {
  const config = JSON.parse(await readFile(configPath, "utf8"));
  const token = environment[config.liveVerification.environmentToken];
  if (!token) fail("Bitbucket authentication is required for live required-check verification");
  const response = await fetchImpl(
    `https://api.bitbucket.org/2.0/repositories/${config.repository}/branch-restrictions`,
    { headers: { Authorization: `Bearer ${token}`, Accept: "application/json" } },
  );
  if (!response.ok) fail(`Bitbucket required-check verification failed with HTTP ${response.status}`);
  const body = await response.json();
  const restrictions = Array.isArray(body.values) ? body.values : [];
  const missing = config.protectedBranches.filter((branch) =>
    !restrictions.some((entry) =>
      entry?.kind === config.liveVerification.branchRestrictionKind &&
      entry?.pattern === branch,
    ),
  );
  if (missing.length > 0) {
    fail(`Bitbucket passing-build merge check is missing for: ${missing.join(", ")}`);
  }
  return { status: "verified", repository: config.repository, protectedBranches: config.protectedBranches };
}

async function main() {
  const live = process.argv.slice(2).includes("--live");
  const result = await verifyConfigOnly();
  const liveResult = live ? await verifyLive() : { status: "not-run", reasonCode: "bitbucket_live_verification_not_requested" };
  process.stdout.write(`${JSON.stringify({ config: result, live: liveResult }, null, 2)}\n`);
}

if (path.resolve(process.argv[1] ?? "") === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    process.stderr.write(`${error.code ?? "AUTHORED_RELEASE_REQUIRED_CHECKS_FAILED"}: ${error.message}\n`);
    process.exitCode = 2;
  });
}
