#!/usr/bin/env node

import { readFile } from "node:fs/promises";
import { createHash } from "node:crypto";
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

function canonical(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
}

function receiptDigest(value) {
  return `sha256:${createHash("sha256").update(canonical(value), "utf8").digest("hex")}`;
}

async function paginatedValues(fetchImpl, url, headers) {
  const values = [];
  const visited = new Set();
  let next = url;
  while (next) {
    if (visited.has(next)) fail("Bitbucket pagination loop detected");
    visited.add(next);
    const response = await fetchImpl(next, { headers });
    if (!response.ok) fail(`Bitbucket verification failed with HTTP ${response.status}`);
    const body = await response.json();
    if (!Array.isArray(body.values)) fail("Bitbucket verification response has no values array");
    values.push(...body.values);
    next = typeof body.next === "string" && body.next ? body.next : "";
  }
  return values;
}

function successful(value) {
  return value?.state?.result?.name === "SUCCESSFUL" || value?.state === "SUCCESSFUL";
}

function recency(value) {
  const timestamp = Date.parse(value?.updated_on ?? value?.completed_on ?? value?.created_on ?? "");
  if (Number.isFinite(timestamp)) return timestamp;
  const buildNumber = Number(value?.build_number);
  return Number.isFinite(buildNumber) ? buildNumber : 0;
}

function latest(values) {
  return [...values].sort((left, right) => recency(right) - recency(left))[0];
}

function latestCommitStatuses(values) {
  const byKey = new Map();
  values.forEach((value, index) => {
    const key = String(value?.key ?? value?.uuid ?? `anonymous-${index}`);
    const current = byKey.get(key);
    if (!current || recency(value) > recency(current)) byKey.set(key, value);
  });
  return [...byKey.values()];
}

export async function verifyLive(fetchImpl = fetch, environment = process.env) {
  const config = JSON.parse(await readFile(configPath, "utf8"));
  const token = environment[config.liveVerification.environmentToken];
  if (!token) fail("Bitbucket authentication is required for live required-check verification");
  const commit = environment.BITBUCKET_COMMIT?.trim();
  if (!/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/i.test(commit ?? "")) fail("BITBUCKET_COMMIT is required for live required-check verification");
  const headers = { Authorization: `Bearer ${token}`, Accept: "application/json" };
  const api = `https://api.bitbucket.org/2.0/repositories/${config.repository}`;
  const restrictions = await paginatedValues(fetchImpl, `${api}/branch-restrictions`, headers);
  const missing = config.protectedBranches.filter((branch) =>
    !restrictions.some((entry) =>
      entry?.kind === config.liveVerification.branchRestrictionKind &&
      entry?.pattern === branch && Number(entry?.value) > 0,
    ),
  );
  if (missing.length > 0) {
    fail(`Bitbucket passing-build merge check is missing for: ${missing.join(", ")}`);
  }
  const [pipelines, statuses] = await Promise.all([
    paginatedValues(fetchImpl, `${api}/pipelines/?target.commit.hash=${encodeURIComponent(commit)}`, headers),
    paginatedValues(fetchImpl, `${api}/commit/${encodeURIComponent(commit)}/statuses`, headers),
  ]);
  const matchingPipelines = pipelines.filter((item) => item?.target?.commit?.hash === commit);
  const pipeline = latest(matchingPipelines);
  if (!pipeline?.uuid || !successful(pipeline)) {
    fail("latest Bitbucket pipeline for the current commit is not successful");
  }
  const steps = await paginatedValues(fetchImpl, `${api}/pipelines/${encodeURIComponent(pipeline.uuid)}/steps/`, headers);
  const missingSteps = config.requiredPipelineSteps.filter((name) => !steps.some((step) => step?.name === name && successful(step)));
  if (missingSteps.length) fail(`current commit pipeline is missing successful required steps: ${missingSteps.join(", ")}`);
  const successfulStatuses = latestCommitStatuses(statuses).filter(successful);
  if (successfulStatuses.length === 0) fail("current commit has no successful commit status");
  const unsigned = {
    schemaVersion: "authored-bitbucket-required-check-receipt/v1",
    repository: config.repository,
    commit: commit.toLowerCase(),
    pipelineUuid: pipeline.uuid,
    protectedBranches: [...config.protectedBranches].sort(),
    requiredPipelineSteps: [...config.requiredPipelineSteps].sort(),
    successfulCommitStatusKeys: successfulStatuses
      .map((status) => String(status?.key ?? ""))
      .filter(Boolean)
      .sort(),
    verifiedAt: new Date().toISOString(),
  };
  return { status: "verified", ...unsigned, receiptDigest: receiptDigest(unsigned) };
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
