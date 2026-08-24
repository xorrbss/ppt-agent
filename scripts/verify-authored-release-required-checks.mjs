#!/usr/bin/env node

import { readFile } from "node:fs/promises";
import { createHmac, timingSafeEqual } from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { canonicalJson, canonicalSha256 } from "./authored-bounded-release-contracts.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const configPath = path.join(root, "config", "authored-release-required-checks.json");
const pipelinePath = path.join(root, "bitbucket-pipelines.yml");
const MAX_RECEIPT_TTL_MS = 15 * 60 * 1000;
const MAX_FUTURE_SKEW_MS = 30 * 1000;
const digestPattern = /^sha256:[a-f0-9]{64}$/;
const commitPattern = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
const uuidPattern = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const canonicalSecondZPattern = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/;
const attemptBindingKeys = [
  "activeManifestDigest",
  "evidencePolicyDigest",
  "executionNonce",
  "fidelityExportAttemptId",
  "generationAttemptId",
  "hybridExportAttemptId",
  "presentationId",
  "presentationRevision",
  "schemaVersion",
  "sourceHeadCommit",
  "sourceTreeCensusDigest",
].sort();
const liveReceiptKeys = [
  "attemptBinding",
  "authoritySignature",
  "commit",
  "expiresAt",
  "latestStatePerStepSuccessful",
  "paginationComplete",
  "pipelineUuid",
  "protectedBranches",
  "receiptDigest",
  "repository",
  "requiredPipelineSteps",
  "schemaVersion",
  "status",
  "successfulCommitStatusKeys",
  "successfulStepNames",
  "verifiedAt",
].sort();

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
  const [configSource, pipelineSource] = await Promise.all([
    readFile(configPath, "utf8"),
    readFile(pipelinePath, "utf8"),
  ]);
  const pipeline = pipelineSource.replace(/\r\n?/gu, "\n");
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

function receiptDigest(value) {
  return canonicalSha256(value);
}

function exactScalarArray(value, expected) {
  return Array.isArray(value)
    && value.every((item) => typeof item === "string")
    && JSON.stringify(value) === JSON.stringify(expected);
}

function unsignedReceipt(receipt) {
  const value = structuredClone(receipt);
  delete value.receiptDigest;
  delete value.authoritySignature;
  return value;
}

function canonicalSecondZ(value) {
  return value.toISOString().replace(/\.\d{3}Z$/, "Z");
}

function parseCanonicalSecondZ(value) {
  if (typeof value !== "string" || !canonicalSecondZPattern.test(value)) return Number.NaN;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) && canonicalSecondZ(new Date(timestamp)) === value ? timestamp : Number.NaN;
}

function loadAttemptBinding(environment, candidate) {
  let value = candidate;
  if (value === undefined) {
    const source = environment.AUTHORED_RELEASE_CI_ATTEMPT_BINDING_JSON;
    if (typeof source !== "string" || !source.trim()) fail("Authored CI attempt binding is required");
    try {
      value = JSON.parse(source);
    } catch {
      fail("Authored CI attempt binding JSON is invalid");
    }
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) fail("Authored CI attempt binding must be an object");
  if (JSON.stringify(Object.keys(value).sort()) !== JSON.stringify(attemptBindingKeys)) fail("Authored CI attempt binding schema is not exact");
  if (
    value.schemaVersion !== "authored-ci-attempt-binding/v3"
    || !uuidPattern.test(value.presentationId)
    || !Number.isInteger(value.presentationRevision)
    || value.presentationRevision < 1
    || !uuidPattern.test(value.generationAttemptId)
    || !uuidPattern.test(value.fidelityExportAttemptId)
    || !uuidPattern.test(value.hybridExportAttemptId)
    || !digestPattern.test(value.activeManifestDigest)
    || !digestPattern.test(value.sourceTreeCensusDigest)
    || !commitPattern.test(value.sourceHeadCommit)
    || !digestPattern.test(value.evidencePolicyDigest)
    || typeof value.executionNonce !== "string"
    || !/^[A-Za-z0-9_-]{16,128}$/.test(value.executionNonce)
  ) {
    fail("Authored CI attempt binding authority is invalid");
  }
  return structuredClone(value);
}

export async function verifyLiveReceipt(receipt, environment = process.env, now = new Date(), expectedAttemptBinding) {
  const config = JSON.parse(await readFile(configPath, "utf8"));
  const evidenceKey = environment.AUTHORED_RELEASE_CI_EVIDENCE_HMAC_KEY;
  if (!evidenceKey || Buffer.byteLength(evidenceKey, "utf8") < 32) {
    fail("Authored CI evidence signing key is required for live receipt verification");
  }
  const commit = environment.BITBUCKET_COMMIT?.trim().toLowerCase();
  if (!commitPattern.test(commit ?? "")) {
    fail("BITBUCKET_COMMIT is required for live receipt verification");
  }
  const attemptBinding = loadAttemptBinding(environment, expectedAttemptBinding);
  if (attemptBinding.sourceHeadCommit !== commit) fail("Authored CI attempt binding does not match current commit");
  if (!receipt || typeof receipt !== "object" || Array.isArray(receipt)) fail("Authored CI receipt must be an object");
  if (JSON.stringify(Object.keys(receipt).sort()) !== JSON.stringify(liveReceiptKeys)) fail("Authored CI receipt schema is not exact");
  const requiredSteps = [...config.requiredPipelineSteps].sort();
  const protectedBranches = [...config.protectedBranches].sort();
  const successfulKeys = receipt.successfulCommitStatusKeys;
  if (
    receipt.schemaVersion !== "authored-bitbucket-required-check-receipt/v1"
    || receipt.status !== "verified"
    || receipt.repository !== config.repository
    || receipt.commit !== commit
    || canonicalJson(receipt.attemptBinding) !== canonicalJson(attemptBinding)
    || typeof receipt.pipelineUuid !== "string"
    || receipt.pipelineUuid.trim().length < 3
    || receipt.paginationComplete !== true
    || receipt.latestStatePerStepSuccessful !== true
    || !exactScalarArray(receipt.protectedBranches, protectedBranches)
    || !exactScalarArray(receipt.requiredPipelineSteps, requiredSteps)
    || !exactScalarArray(receipt.successfulStepNames, requiredSteps)
    || !Array.isArray(successfulKeys)
    || successfulKeys.length === 0
    || !exactScalarArray(successfulKeys, [...new Set(successfulKeys)].sort())
  ) {
    fail("Authored CI receipt authority scope is invalid");
  }
  const verifiedAt = parseCanonicalSecondZ(receipt.verifiedAt);
  const expiresAt = parseCanonicalSecondZ(receipt.expiresAt);
  const observedAt = now instanceof Date ? now.getTime() : Date.parse(now);
  if (
    !Number.isFinite(verifiedAt)
    || !Number.isFinite(expiresAt)
    || !Number.isFinite(observedAt)
    || verifiedAt > observedAt + MAX_FUTURE_SKEW_MS
    || expiresAt <= observedAt
    || expiresAt <= verifiedAt
    || expiresAt - verifiedAt > MAX_RECEIPT_TTL_MS
  ) {
    fail("Authored CI receipt is stale or has an invalid TTL");
  }
  const expectedDigest = receiptDigest(unsignedReceipt(receipt));
  const expectedSignature = `hmac-sha256:${createHmac("sha256", evidenceKey).update(expectedDigest, "utf8").digest("hex")}`;
  const observedDigest = Buffer.from(String(receipt.receiptDigest), "utf8");
  const expectedDigestBytes = Buffer.from(expectedDigest, "utf8");
  const observedSignature = Buffer.from(String(receipt.authoritySignature), "utf8");
  const expectedSignatureBytes = Buffer.from(expectedSignature, "utf8");
  if (
    observedDigest.length !== expectedDigestBytes.length
    || observedSignature.length !== expectedSignatureBytes.length
    || !timingSafeEqual(observedDigest, expectedDigestBytes)
    || !timingSafeEqual(observedSignature, expectedSignatureBytes)
  ) {
    fail("Authored CI receipt canonical authority signature is invalid");
  }
  return true;
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
  return { values, paginationComplete: true };
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

function latestRequiredSteps(values, requiredNames) {
  return requiredNames.map((name) => {
    const matching = values.filter((value) => value?.name === name);
    if (matching.length === 0) fail(`current commit pipeline is missing required step: ${name}`);
    const newestRecency = Math.max(...matching.map(recency));
    const newest = matching.filter((value) => recency(value) === newestRecency);
    if (newest.length !== 1) fail(`current commit pipeline has ambiguous latest required step: ${name}`);
    return newest[0];
  });
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

export async function verifyLive(fetchImpl = fetch, environment = process.env, now = new Date(), attemptBindingInput) {
  const config = JSON.parse(await readFile(configPath, "utf8"));
  const token = environment[config.liveVerification.environmentToken];
  if (!token) fail("Bitbucket authentication is required for live required-check verification");
  const evidenceKey = environment.AUTHORED_RELEASE_CI_EVIDENCE_HMAC_KEY;
  if (!evidenceKey || Buffer.byteLength(evidenceKey, "utf8") < 32) {
    fail("Authored CI evidence signing key is required for live required-check verification");
  }
  const commit = environment.BITBUCKET_COMMIT?.trim();
  if (!/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/i.test(commit ?? "")) fail("BITBUCKET_COMMIT is required for live required-check verification");
  const attemptBinding = loadAttemptBinding(environment, attemptBindingInput);
  if (attemptBinding.sourceHeadCommit !== commit.toLowerCase()) fail("Authored CI attempt binding does not match current commit");
  const headers = { Authorization: `Bearer ${token}`, Accept: "application/json" };
  const api = `https://api.bitbucket.org/2.0/repositories/${config.repository}`;
  const restrictionsPage = await paginatedValues(fetchImpl, `${api}/branch-restrictions`, headers);
  const restrictions = restrictionsPage.values;
  const missing = config.protectedBranches.filter((branch) =>
    !restrictions.some((entry) =>
      entry?.kind === config.liveVerification.branchRestrictionKind &&
      entry?.pattern === branch && Number(entry?.value) > 0,
    ),
  );
  if (missing.length > 0) {
    fail(`Bitbucket passing-build merge check is missing for: ${missing.join(", ")}`);
  }
  const [pipelinesPage, statusesPage] = await Promise.all([
    paginatedValues(fetchImpl, `${api}/pipelines/?target.commit.hash=${encodeURIComponent(commit)}`, headers),
    paginatedValues(fetchImpl, `${api}/commit/${encodeURIComponent(commit)}/statuses`, headers),
  ]);
  const pipelines = pipelinesPage.values;
  const statuses = statusesPage.values;
  const matchingPipelines = pipelines.filter((item) => item?.target?.commit?.hash === commit);
  const pipeline = latest(matchingPipelines);
  if (!pipeline?.uuid || !successful(pipeline)) {
    fail("latest Bitbucket pipeline for the current commit is not successful");
  }
  const stepsPage = await paginatedValues(fetchImpl, `${api}/pipelines/${encodeURIComponent(pipeline.uuid)}/steps/`, headers);
  const latestSteps = latestRequiredSteps(stepsPage.values, config.requiredPipelineSteps);
  const missingSteps = latestSteps.filter((step) => !successful(step)).map((step) => step.name);
  if (missingSteps.length) fail(`current commit pipeline is missing successful required steps: ${missingSteps.join(", ")}`);
  const successfulStatuses = latestCommitStatuses(statuses).filter(successful);
  if (successfulStatuses.length === 0) fail("current commit has no successful commit status");
  const observedAt = now instanceof Date ? new Date(now.getTime()) : new Date(now);
  if (!Number.isFinite(observedAt.getTime())) fail("Authored CI receipt verification time is invalid");
  const verifiedAt = new Date(Math.floor(observedAt.getTime() / 1000) * 1000);
  const unsigned = {
    status: "verified",
    schemaVersion: "authored-bitbucket-required-check-receipt/v1",
    repository: config.repository,
    commit: commit.toLowerCase(),
    pipelineUuid: pipeline.uuid,
    protectedBranches: [...config.protectedBranches].sort(),
    requiredPipelineSteps: [...config.requiredPipelineSteps].sort(),
    successfulStepNames: latestSteps.map((step) => step.name).sort(),
    paginationComplete: restrictionsPage.paginationComplete && pipelinesPage.paginationComplete && statusesPage.paginationComplete && stepsPage.paginationComplete,
    latestStatePerStepSuccessful: latestSteps.every(successful),
    successfulCommitStatusKeys: successfulStatuses
      .map((status) => String(status?.key ?? ""))
      .filter(Boolean)
      .sort(),
    verifiedAt: canonicalSecondZ(verifiedAt),
    expiresAt: canonicalSecondZ(new Date(verifiedAt.getTime() + MAX_RECEIPT_TTL_MS)),
    attemptBinding,
  };
  const digest = receiptDigest(unsigned);
  const authoritySignature = `hmac-sha256:${createHmac("sha256", evidenceKey).update(digest, "utf8").digest("hex")}`;
  const receipt = { ...unsigned, receiptDigest: digest, authoritySignature };
  await verifyLiveReceipt(receipt, environment, verifiedAt, attemptBinding);
  return receipt;
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
