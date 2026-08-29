#!/usr/bin/env node
/**
 * Controlled Authored-vNext load and durability evidence harness.
 *
 * This drives only public FastAPI endpoints. Load mode never stops or starts a
 * service. Durability mode owns the external worker process it spawns, requires
 * an isolated app-data/database root, and terminates only that child.
 */

import { createHash } from "node:crypto";
import { createWriteStream } from "node:fs";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import { requestFailureSummary, retryAudit } from "./authored-vnext-controlled-load-retry-audit.mjs";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const EVIDENCE_ROOT = path.join(REPO, "artifacts", "authored-vnext-controlled-load-20260829");
const TERMINAL = new Set(["completed", "error", "failed", "cancelled", "canceled"]);
const PHASES = ["outline", "author", "repair", "checkpoint", "publication", "export"];
const FINGERPRINT = /^sha256:[a-f0-9]{64}$/;
const QUALITY_PROFILES = new Set(["development", "release"]);
const PPTX_MODES = new Set(["fidelity", "hybrid"]);
const RENDER_RUNTIME_METRICS_PATH = "/metrics/authored-render-runtime";

class BenchmarkRequestError extends Error {
  constructor(message, classification, cause = null) {
    super(message);
    this.name = "BenchmarkRequestError";
    this.classification = classification;
    this.details = cause && typeof cause === "object" ? cause : null;
    this.cause = cause ? String(cause?.stack || cause) : null;
  }
}

function fail(message) { throw new Error(message); }
function now() { return new Date().toISOString(); }
function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }
function sha256(bytes) { return createHash("sha256").update(bytes).digest("hex"); }
function fingerprint(value) { return `sha256:${sha256(value)}`; }
function inside(child, parent) {
  const relative = path.relative(path.resolve(parent), path.resolve(child));
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function parseArgs(argv) {
  const out = {};
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (["--validate", "--run", "--durability", "--help"].includes(token)) {
      out[token.slice(2)] = true;
      continue;
    }
    if (!token.startsWith("--") || index + 1 >= argv.length) fail(`invalid argument: ${token}`);
    out[token.slice(2)] = argv[++index];
  }
  return out;
}

function usage() {
  return [
    "Usage:",
    "  node scripts/authored-vnext-controlled-load.mjs --validate --manifest <json>",
    "  node scripts/authored-vnext-controlled-load.mjs --run --manifest <json>",
    "  node scripts/authored-vnext-controlled-load.mjs --durability --manifest <json>",
    "",
    "Credentials are read only from PRESENTON_BENCH_USER/PRESENTON_BENCH_PASSWORD.",
    "Durability mode additionally requires inherited WORKSPACE_OPERATION_WORKER_MODE=external,",
    "APP_DATA_DIRECTORY and DATABASE_URL values rooted below durability.isolatedRoot.",
    "schemaVersion 2 supports matrix.slideCounts (5,10,20), matrix.concurrencyLevels (1,2,5,10),",
    "matrix.cacheStates (cold,warm), and matrix.repetitions. Matrix runs require redacted provider/model/prompt/environment fingerprints, owned resource PIDs, and a cold-control receipt for every cold cell. This harness never restarts a listener.",
  ].join("\n");
}

function requiredFingerprint(value, label) {
  const normalized = String(value || "").toLowerCase();
  if (!FINGERPRINT.test(normalized)) fail(`${label} must be sha256:<64 lowercase hex>`);
  return normalized;
}

function promptInvariant(deck) {
  return JSON.stringify({
    content: deck.content,
    language: deck.language ?? null,
    style: deck.style ?? null,
    instructions: deck.instructions ?? null,
    tone: deck.tone ?? null,
    verbosity: deck.verbosity ?? null,
    includeTitleSlide: deck.includeTitleSlide !== false,
    includeTableOfContents: deck.includeTableOfContents === true,
    webSearch: deck.webSearch === true,
    visionQa: deck.visionQa === true,
    qualityProfile: deck.qualityProfile,
    pptxMode: deck.pptxMode,
  });
}

function profileInvariant(deck) {
  return {
    qualityProfile: deck.qualityProfile,
    pptxMode: deck.pptxMode,
    fingerprint: fingerprint(JSON.stringify({
      qualityProfile: deck.qualityProfile,
      pptxMode: deck.pptxMode,
    })),
  };
}

function normalizeFixedInvariants(input, deck) {
  if (!input || typeof input !== "object") fail("matrix.fixedInvariants is required");
  const providerId = String(input.provider?.id || "").trim();
  const modelId = String(input.model?.id || "").trim();
  if (!providerId || !modelId) fail("matrix.fixedInvariants provider.id and model.id are required");
  const fixedInvariants = {
    provider: { id: providerId, fingerprint: requiredFingerprint(input.provider?.fingerprint, "matrix.fixedInvariants.provider.fingerprint") },
    model: { id: modelId, fingerprint: requiredFingerprint(input.model?.fingerprint, "matrix.fixedInvariants.model.fingerprint") },
    prompt: { fingerprint: requiredFingerprint(input.prompt?.fingerprint, "matrix.fixedInvariants.prompt.fingerprint") },
    environment: { fingerprint: requiredFingerprint(input.environment?.fingerprint, "matrix.fixedInvariants.environment.fingerprint") },
    profile: profileInvariant(deck),
  };
  const computedPromptFingerprint = fingerprint(promptInvariant(deck));
  if (fixedInvariants.prompt.fingerprint !== computedPromptFingerprint) {
    fail("matrix.fixedInvariants.prompt.fingerprint must match the fixed deck prompt invariant");
  }
  return fixedInvariants;
}

function normalizeColdControlReceipts(input, matrix, environmentFingerprint) {
  if (!input || typeof input !== "object" || Array.isArray(input)) fail("matrix.coldControlReceipts is required");
  const expected = new Set(matrix.slideCounts.flatMap((nSlides) => matrix.concurrencyLevels
    .map((concurrency) => `cold-${nSlides}slides-c${concurrency}`)));
  const supplied = Object.keys(input);
  if (supplied.length !== expected.size || supplied.some((key) => !expected.has(key))) {
    fail("matrix.coldControlReceipts must contain exactly one receipt for every cold matrix cell");
  }
  return Object.fromEntries(supplied.map((key) => {
    const receipt = input[key];
    const method = String(receipt?.method || "").trim();
    const target = String(receipt?.target || "").trim();
    const completedAt = String(receipt?.completedAt || "").trim();
    if (!method || !target || !Number.isFinite(Date.parse(completedAt))) {
      fail(`matrix.coldControlReceipts.${key} requires method, target, and completedAt`);
    }
    const receiptEnvironmentFingerprint = requiredFingerprint(receipt?.environmentFingerprint,
      `matrix.coldControlReceipts.${key}.environmentFingerprint`);
    if (receiptEnvironmentFingerprint !== environmentFingerprint) {
      fail(`matrix.coldControlReceipts.${key}.environmentFingerprint must match matrix.fixedInvariants.environment.fingerprint`);
    }
    return [key, { method, target, completedAt, environmentFingerprint: receiptEnvironmentFingerprint }];
  }));
}

function normalizeManifest(raw) {
  if (![1, 2].includes(raw?.schemaVersion)) fail("manifest schemaVersion must be 1 or 2");
  const outputDirectory = path.resolve(REPO, raw.outputDirectory || "");
  if (!inside(outputDirectory, EVIDENCE_ROOT) || outputDirectory === EVIDENCE_ROOT) {
    fail(`outputDirectory must be a child of ${EVIDENCE_ROOT}`);
  }
  const baseUrl = new URL(raw.baseUrl || "http://127.0.0.1:8010");
  const metricsBaseUrl = new URL(raw.metricsBaseUrl || baseUrl.href);
  if (!["http:", "https:"].includes(baseUrl.protocol)) fail("baseUrl must use http or https");
  if (!["http:", "https:"].includes(metricsBaseUrl.protocol)) fail("metricsBaseUrl must use http or https");
  const concurrency = Number(raw.concurrency ?? 2);
  const pollIntervalMs = Number(raw.pollIntervalMs ?? 2000);
  const timeoutMs = Number(raw.timeoutMs ?? 1_800_000);
  const resourceIntervalMs = Number(raw.resourceIntervalMs ?? 2000);
  const soakDurationSeconds = Number(raw.soak?.durationSeconds ?? raw.soakDurationSeconds ?? 0);
  if (!Number.isInteger(concurrency) || concurrency < 1 || concurrency > 8) fail("concurrency must be 1..8");
  if (!Number.isInteger(pollIntervalMs) || pollIntervalMs < 250 || pollIntervalMs > 30_000) fail("pollIntervalMs must be 250..30000");
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1000 || timeoutMs > 86_400_000) fail("timeoutMs must be 1000..86400000");
  if (!Number.isInteger(resourceIntervalMs) || resourceIntervalMs < 500 || resourceIntervalMs > 60_000) fail("resourceIntervalMs must be 500..60000");
  if (!Number.isInteger(soakDurationSeconds) || soakDurationSeconds < 0 || soakDurationSeconds > 604_800) fail("soakDurationSeconds must be 0..604800");
  if (!Array.isArray(raw.decks) || raw.decks.length < 1 || raw.decks.length > 20) fail("decks must contain 1..20 entries");
  const ids = new Set();
  const decks = raw.decks.map((deck, index) => {
    const id = String(deck.id || `deck-${index + 1}`).trim();
    if (!id || ids.has(id)) fail(`deck id must be non-empty and unique: ${id}`);
    ids.add(id);
    if (typeof deck.content !== "string" || !deck.content.trim()) fail(`${id}: content is required`);
    const exportAs = deck.exportAs || "pptx";
    if (!new Set(["pptx", "pdf"]).has(exportAs)) fail(`${id}: exportAs must be pptx or pdf`);
    const nSlides = Number(deck.nSlides ?? 1);
    if (!Number.isInteger(nSlides) || nSlides < 1 || nSlides > 50) fail(`${id}: nSlides must be 1..50`);
    const qualityProfile = String(deck.qualityProfile ?? "development").trim().toLowerCase();
    if (!QUALITY_PROFILES.has(qualityProfile)) fail(`${id}: qualityProfile must be development or release`);
    const pptxMode = String(deck.pptxMode ?? "fidelity").trim().toLowerCase();
    if (!PPTX_MODES.has(pptxMode)) fail(`${id}: pptxMode must be fidelity or hybrid`);
    if (exportAs !== "pptx" && pptxMode !== "fidelity") fail(`${id}: pptxMode hybrid requires exportAs pptx`);
    return { ...deck, id, exportAs, nSlides, qualityProfile, pptxMode };
  });
  const resourcePids = [...new Set((raw.resourcePids || []).map(Number))];
  if (resourcePids.some((pid) => !Number.isInteger(pid) || pid <= 0)) fail("resourcePids must contain positive integers");
  let matrix = null;
  if (raw.schemaVersion === 2) {
    const input = raw.matrix;
    if (!input || typeof input !== "object") fail("schemaVersion 2 requires matrix");
    const slideCounts = [...new Set((input.slideCounts || []).map(Number))];
    const concurrencyLevels = [...new Set((input.concurrencyLevels || []).map(Number))];
    const cacheStates = [...new Set((input.cacheStates || []).map((value) => String(value).toLowerCase()))];
    const repetitions = Number(input.repetitions ?? 2);
    if (JSON.stringify(slideCounts) !== JSON.stringify([5, 10, 20])) fail("matrix.slideCounts must be exactly [5,10,20]");
    if (JSON.stringify(concurrencyLevels) !== JSON.stringify([1, 2, 5, 10])) fail("matrix.concurrencyLevels must be exactly [1,2,5,10]");
    if (JSON.stringify(cacheStates) !== JSON.stringify(["cold", "warm"])) fail("matrix.cacheStates must be exactly [cold,warm]");
    if (!Number.isInteger(repetitions) || repetitions < 2 || repetitions > 20) fail("matrix.repetitions must be 2..20");
    if (repetitions < Math.max(...concurrencyLevels)) {
      fail("matrix.repetitions must be greater than or equal to the maximum concurrency level");
    }
    if (decks.length !== 1) fail("matrix requires exactly one deck template");
    if (!resourcePids.length) fail("schemaVersion 2 matrix requires non-empty resourcePids");
    const bareMatrix = { slideCounts, concurrencyLevels, cacheStates, repetitions };
    const fixedInvariants = normalizeFixedInvariants(input.fixedInvariants, decks[0]);
    const coldControlReceipts = normalizeColdControlReceipts(input.coldControlReceipts, bareMatrix,
      fixedInvariants.environment.fingerprint);
    matrix = { ...bareMatrix, fixedInvariants, coldControlReceipts };
  }
  return {
    schemaVersion: raw.schemaVersion, baseUrl: baseUrl.href.replace(/\/$/, ""), metricsBaseUrl: metricsBaseUrl.href.replace(/\/$/, ""), outputDirectory,
    concurrency, pollIntervalMs, timeoutMs, resourceIntervalMs, soakDurationSeconds,
    resourcePids, requireWorkspaceOperation: raw.requireWorkspaceOperation !== false,
    decks, matrix, deckInvariants: decks.map((deck) => ({
      deckId: deck.id,
      prompt: { fingerprint: fingerprint(promptInvariant(deck)) },
      profile: profileInvariant(deck),
    })), durability: raw.durability || null,
  };
}

function authHeaders() {
  const user = process.env.PRESENTON_BENCH_USER;
  const password = process.env.PRESENTON_BENCH_PASSWORD;
  if (!user && !password) return {};
  if (!user || !password) fail("both PRESENTON_BENCH_USER and PRESENTON_BENCH_PASSWORD are required");
  return { Authorization: `Basic ${Buffer.from(`${user}:${password}`, "utf8").toString("base64")}` };
}

async function request(url, init, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    let response;
    try { response = await fetch(url, { ...init, signal: controller.signal }); }
    catch (error) {
      const text = String(error?.message || error);
      const classification = controller.signal.aborted ? "request_timeout" :
        /fetch failed/i.test(text) ? "proxy_fetch_failed" :
        /(ECONNREFUSED|ECONNRESET|socket hang up|network)/i.test(text) ? "listener_interrupted" : "network_request_failed";
      throw new BenchmarkRequestError(`${classification}: ${url}: ${text}`, classification, error);
    }
    const bytes = Buffer.from(await response.arrayBuffer());
    const text = bytes.toString("utf8");
    let json = null;
    try { json = JSON.parse(text); } catch { /* binary or non-JSON body */ }
    if (!response.ok) {
      const classification = [502, 503, 504].includes(response.status) ? "proxy_or_listener_http_failure" : "http_request_failed";
      throw new BenchmarkRequestError(`${classification}: HTTP ${response.status} ${url}: ${text.slice(0, 600)}`, classification);
    }
    return { response, bytes, text, json };
  } finally {
    clearTimeout(timer);
  }
}

function phaseForLabel(label) {
  const value = String(label || "").toLowerCase();
  if (/(outline|plan|structure)/.test(value)) return "outline";
  if (/(author|generate|composition|scene)/.test(value)) return "author";
  if (/(repair|overflow|capacity)/.test(value)) return "repair";
  if (/(checkpoint|persist|seal)/.test(value)) return "checkpoint";
  if (/(publication|publish|complete)/.test(value)) return "publication";
  if (/(export|download|pdf)/.test(value)) return "export";
  return null;
}

function percentile(values, quantile) {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.ceil(quantile * sorted.length) - 1];
}

function phaseSummary(results) {
  const phases = Object.fromEntries(PHASES.map((phase) => [phase, []]));
  for (const result of results) for (const [phase, value] of Object.entries(result.phaseTimingsMs || {})) {
    if (Number.isFinite(value)) phases[phase]?.push(value);
  }
  return Object.fromEntries(PHASES.map((phase) => {
    const values = phases[phase];
    return [phase, { observations: values.length, p50Ms: percentile(values, 0.5), p95Ms: percentile(values, 0.95) }];
  }));
}

function requestBody(deck) {
  const body = {
    content: deck.content, n_slides: deck.nSlides, template: "authored",
    strategy: "authored", export_as: "pptx",
    include_title_slide: deck.includeTitleSlide !== false,
    include_table_of_contents: deck.includeTableOfContents === true,
    web_search: deck.webSearch === true, vision_qa: deck.visionQa === true,
    quality_profile: deck.qualityProfile,
  };
  for (const [source, target] of [["language", "language"], ["style", "authored_style"],
    ["instructions", "instructions"], ["tone", "tone"], ["verbosity", "verbosity"]]) {
    if (deck[source] != null) body[target] = deck[source];
  }
  return body;
}

function assertOperationInvariants(manifest, deck, scenario) {
  if (!manifest.matrix) return;
  const fixed = manifest.matrix.fixedInvariants;
  if (!fixed || fingerprint(promptInvariant(deck)) !== fixed.prompt.fingerprint) {
    fail(`${deck.id}: prompt invariant drift`);
  }
  if (JSON.stringify(fixed.profile) !== JSON.stringify(profileInvariant(deck))) {
    fail(`${deck.id}: quality profile/pptx mode invariant drift`);
  }
  if (!scenario || JSON.stringify(scenario.fixedInvariants) !== JSON.stringify(fixed)) {
    fail(`${deck.id}: provider/model/environment invariant drift`);
  }
  if (scenario.cacheState === "cold" && !scenario.coldControl) fail(`${deck.id}: missing cold-control receipt`);
  if (scenario.cacheState === "warm" && scenario.coldControl?.status !== "warm-no-cold-control") {
    fail(`${deck.id}: warm scenario must not carry a cold-control receipt`);
  }
}

function exportName(serverPath) {
  const raw = String(serverPath || "");
  try {
    const parsed = new URL(raw, "http://presenton.local");
    const named = parsed.searchParams.get("name");
    if (named) return path.basename(named);
    return path.basename(parsed.pathname);
  } catch {
    return raw.split(/[\\/]/).filter(Boolean).at(-1);
  }
}

function verifyMagic(bytes, format) {
  if (format === "pdf" && bytes.subarray(0, 5).toString("ascii") !== "%PDF-") fail("downloaded PDF magic mismatch");
  if (format === "pptx" && !(bytes[0] === 0x50 && bytes[1] === 0x4b)) fail("downloaded PPTX ZIP magic mismatch");
}

function redactedExportEvidence(value, key = "", depth = 0) {
  if (depth > 12) return "[redacted:depth]";
  if (/(authorization|cookie|content|html|password|prompt|secret|token|url|path)/i.test(key)) {
    return "[redacted]";
  }
  if (value === null || typeof value === "boolean" || typeof value === "number") return value;
  if (typeof value === "string") return value.length <= 512 ? value : `[redacted:string:${value.length}]`;
  if (Array.isArray(value)) return value.map((item) => redactedExportEvidence(item, key, depth + 1));
  if (!value || typeof value !== "object") return String(value);
  return Object.fromEntries(Object.entries(value).map(([nestedKey, nestedValue]) => [
    nestedKey,
    redactedExportEvidence(nestedValue, nestedKey, depth + 1),
  ]));
}

function redactedPptxExportEvidence(payload, pptxMode) {
  return {
    success: payload?.success === true,
    pptxMode,
    quality: redactedExportEvidence(payload?.quality, "quality"),
    exportMetadata: redactedExportEvidence(payload?.exportMetadata, "exportMetadata"),
  };
}

function stageTimings(timeline, operationId) {
  const item = timeline?.items?.find((value) => value.operation_id === operationId || value.operationId === operationId);
  const events = [...(item?.events || [])].filter((event) => event.created_at || event.createdAt)
    .sort((a, b) => Date.parse(a.created_at || a.createdAt) - Date.parse(b.created_at || b.createdAt));
  const stages = [];
  for (let index = 0; index < events.length; index += 1) {
    const event = events[index];
    const result = event.result && typeof event.result === "object" ? event.result : {};
    const label = result.stage || result.operation || event.type || event.status || "event";
    const started = Date.parse(event.created_at || event.createdAt);
    const ended = index + 1 < events.length ? Date.parse(events[index + 1].created_at || events[index + 1].createdAt) : started;
    stages.push({ label, durationMsToNextEvent: Math.max(0, ended - started), event });
  }
  const phaseTimingsMs = {};
  for (const phase of PHASES) phaseTimingsMs[phase] = null;
  for (const stage of stages) {
    const phase = phaseForLabel(stage.label);
    if (phase) phaseTimingsMs[phase] = (phaseTimingsMs[phase] || 0) + stage.durationMsToNextEvent;
  }
  return { operation: item || null, events, stages, phaseTimingsMs };
}

function eventsFromPayload(payload) {
  for (const candidate of [payload?.items, payload?.events, payload?.data?.items, payload?.data?.events]) {
    if (Array.isArray(candidate)) return candidate;
  }
  return [];
}

function eventMatchesOperation(event, operationId) {
  const values = [event?.operation_id, event?.operationId, event?.result?.operation_id, event?.result?.operationId];
  return values.filter(Boolean).some((value) => String(value) === String(operationId));
}

async function durableOperationAudit({ baseUrl, taskId, operationId, headers, requestFn = request }) {
  if (!operationId) return { status: "unavailable", reason: "workspace_operation_id_unavailable", retryAudit: retryAudit([]) };
  try {
    const task = (await requestFn(`${baseUrl}/api/v1/async-tasks/status/${encodeURIComponent(taskId)}`, {
      headers: { Accept: "application/json", ...headers },
    }, 30_000)).json;
    const presentationId = task?.presentation_id || task?.presentationId || task?.data?.presentation_id || task?.data?.presentationId;
    if (!presentationId) return { status: "unavailable", reason: "presentation_id_unavailable", retryAudit: retryAudit([]) };
    const timeline = (await requestFn(`${baseUrl}/api/v1/ppt/workspace/presentations/${presentationId}/timeline?limit=200`, {
      headers: { Accept: "application/json", ...headers },
    }, 30_000)).json;
    const operationEvents = (await requestFn(`${baseUrl}/api/v1/ppt/workspace/presentations/${presentationId}/events?limit=200`, {
      headers: { Accept: "application/json", ...headers },
    }, 30_000)).json;
    const observed = stageTimings(timeline, operationId);
    const endpointEvents = eventsFromPayload(operationEvents).filter((event) => eventMatchesOperation(event, operationId));
    const evidence = [...(observed.operation ? [observed.operation] : []), ...observed.events, ...endpointEvents];
    return {
      status: observed.operation || endpointEvents.length ? "observed" : "unavailable",
      reason: observed.operation || endpointEvents.length ? null : "workspace_operation_events_unavailable",
      presentationId,
      operation: observed.operation,
      stages: observed.stages,
      phaseTimingsMs: observed.phaseTimingsMs,
      retryAudit: retryAudit(evidence),
    };
  } catch (error) {
    return {
      status: "unavailable",
      reason: error instanceof BenchmarkRequestError ? error.classification : "workspace_operation_audit_request_failed",
      retryAudit: retryAudit([]),
    };
  }
}

function cacheTelemetrySnapshot(payload) {
  if (!payload || typeof payload !== "object" || ![
    "authored-render-runtime/v1",
    "authored-render-runtime/v2",
  ].includes(payload.schemaVersion)) {
    fail("authored render runtime metrics response is invalid");
  }
  const chromeRuntimeDigest = requiredFingerprint(payload.chromeRuntimeDigest, "authored render runtime chromeRuntimeDigest");
  if (!payload.cacheIdentity || typeof payload.cacheIdentity !== "object") {
    fail("authored render runtime cacheIdentity is required");
  }
  if (!payload.counters || typeof payload.counters !== "object" || Array.isArray(payload.counters)) {
    fail("authored render runtime counters are required");
  }
  const counters = {};
  for (const [key, value] of Object.entries(payload.counters)) {
    if (!Number.isInteger(value) || value < 0) fail(`authored render runtime counter ${key} is invalid`);
    counters[key] = value;
  }
  return {
    schemaVersion: payload.schemaVersion,
    chromeRuntimeDigest,
    cacheIdentity: {
      render: String(payload.cacheIdentity.render || ""),
      identity: String(payload.cacheIdentity.identity || ""),
      measurement: String(payload.cacheIdentity.measurement || ""),
      domMetrics: String(payload.cacheIdentity.domMetrics || ""),
      occlusionSensor: String(payload.cacheIdentity.occlusionSensor || ""),
      chromeReuseEnabled: payload.cacheIdentity.chromeReuseEnabled === true,
    },
    counters,
  };
}

async function fetchCacheTelemetry(manifest) {
  const response = await request(`${manifest.metricsBaseUrl}${RENDER_RUNTIME_METRICS_PATH}`, {
    headers: { Accept: "application/json", ...authHeaders() },
  }, 30_000);
  return cacheTelemetrySnapshot(response.json);
}

function cacheTelemetryDelta(before, after) {
  if (before.chromeRuntimeDigest !== after.chromeRuntimeDigest) {
    fail("authored render Chrome identity changed during controlled scenario");
  }
  if (JSON.stringify(before.cacheIdentity) !== JSON.stringify(after.cacheIdentity)) {
    fail("authored render cache identity changed during controlled scenario");
  }
  const counters = {};
  for (const key of new Set([...Object.keys(before.counters), ...Object.keys(after.counters)])) {
    counters[key] = (after.counters[key] || 0) - (before.counters[key] || 0);
  }
  return {
    chromeRuntimeDigest: after.chromeRuntimeDigest,
    cacheIdentity: after.cacheIdentity,
    counters,
    measurementRecollections: counters.metrics_recollection_requests || 0,
  };
}

function coldControlResult(scenario, cacheBefore) {
  if (scenario?.cacheState !== "cold") return null;
  return {
    receipt: scenario.coldControl,
    receiptValidated: true,
    observedBeforeSnapshotDigest: fingerprint(cacheBefore),
    cacheStateVerified: false,
    verificationBoundary: "external cold-control receipt is schema/fingerprint validated; this harness does not stop listeners or clear process caches",
  };
}

async function runDeck(deck, manifest, ordinal, onStatus = null, scenario = null) {
  assertOperationInvariants(manifest, deck, scenario);
  const startedAt = Date.now();
  const resourcePids = [...new Set(manifest.resourcePids)];
  const [resourceBefore, resourceTreeBefore] = await Promise.all([
    sampleResources(resourcePids), sampleResourceTree(resourcePids),
  ]);
  if (!Array.isArray(resourceBefore) || resourceBefore.length !== manifest.resourcePids.length) {
    fail(`${deck.id}: one or more configured resourcePids are unavailable`);
  }
  const admissionStartedAt = Date.now();
  const headers = { Accept: "application/json", "Content-Type": "application/json", ...authHeaders() };
  const admitted = await request(`${manifest.baseUrl}/api/v1/ppt/presentation/generate/async`, {
    method: "POST", headers, body: JSON.stringify(requestBody(deck)),
  }, manifest.timeoutMs);
  const taskId = admitted.json?.id;
  const operationId = admitted.json?.operation_id || admitted.json?.operationId || null;
  if (!taskId) fail(`${deck.id}: admission returned no task id`);
  if (manifest.requireWorkspaceOperation && !operationId) fail(`${deck.id}: admission returned no workspace operation id`);
  const admissionMs = Date.now() - admissionStartedAt;
  const deadline = startedAt + manifest.timeoutMs;
  const statusHistory = [];
  let terminal;
  while (Date.now() < deadline) {
    const polled = await request(`${manifest.baseUrl}/api/v1/ppt/presentation/status/${encodeURIComponent(taskId)}`, {
      headers: { Accept: "application/json", ...authHeaders() },
    }, Math.max(1, deadline - Date.now()));
    terminal = polled.json;
    statusHistory.push({ observedAt: now(), status: terminal?.status, progress: terminal?.progress || null });
    if (onStatus) await onStatus({ deck, taskId, operationId, status: terminal });
    if (TERMINAL.has(String(terminal?.status || "").toLowerCase())) break;
    await sleep(Math.min(manifest.pollIntervalMs, Math.max(1, deadline - Date.now())));
  }
  if (!terminal || !TERMINAL.has(String(terminal.status || "").toLowerCase())) fail(`${deck.id}: timed out`);
  if (terminal.status !== "completed") {
    const durableAudit = await durableOperationAudit({
      baseUrl: manifest.baseUrl, taskId, operationId, headers: authHeaders(),
    });
    throw new BenchmarkRequestError(
      `${deck.id}: terminal status ${terminal.status}: ${JSON.stringify(terminal.failure || terminal.error || terminal.message)}`,
      "operation_failed",
      { taskId, operationId, durableAudit, retryAudit: durableAudit.retryAudit },
    );
  }
  const data = terminal.data || {};
  const presentationId = data.presentation_id || data.presentationId;
  let downloadPath = data.path;
  if (!presentationId || !downloadPath) fail(`${deck.id}: completed result lacks presentation_id/path`);
  let pdfExport = null;
  let pptxExport = null;
  const exportStartedAt = Date.now();
  if (deck.exportAs === "pdf") {
    pdfExport = (await request(`${manifest.baseUrl}/api/export-presentation`, {
      method: "POST",
      headers,
      body: JSON.stringify({ format: "pdf", id: presentationId, title: deck.content.slice(0, 120) }),
    }, Math.max(1, deadline - Date.now()))).json;
    if (pdfExport?.success !== true || typeof pdfExport?.path !== "string") {
      fail(`${deck.id}: PDF export returned no downloadable path`);
    }
    downloadPath = pdfExport.path;
  } else if (deck.pptxMode === "hybrid") {
    if (!operationId) fail(`${deck.id}: hybrid PPTX export requires operationId`);
    const exported = (await request(`${manifest.baseUrl}/api/export-presentation`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        format: "pptx",
        id: presentationId,
        title: deck.content.slice(0, 120),
        pptxMode: "hybrid",
        operationId,
      }),
    }, Math.max(1, deadline - Date.now()))).json;
    if (exported?.success !== true || typeof exported?.path !== "string") {
      fail(`${deck.id}: Hybrid PPTX export returned no downloadable path`);
    }
    pptxExport = redactedPptxExportEvidence(exported, deck.pptxMode);
    downloadPath = exported.path;
  }
  const name = exportName(downloadPath);
  if (!name) fail(`${deck.id}: export path has no file name`);
  const downloaded = await request(new URL(downloadPath, `${manifest.baseUrl}/`).href, {
    headers: { ...authHeaders() },
  }, Math.max(1, deadline - Date.now()));
  verifyMagic(downloaded.bytes, deck.exportAs);
  const destination = path.join(manifest.outputDirectory, "exports", `${String(ordinal).padStart(4, "0")}-${deck.id}-${name}`);
  await mkdir(path.dirname(destination), { recursive: true });
  await writeFile(destination, downloaded.bytes);
  let timeline = null;
  let operationEvents = null;
  if (operationId) {
    timeline = (await request(`${manifest.baseUrl}/api/v1/ppt/workspace/presentations/${presentationId}/timeline?limit=200`, {
      headers: { Accept: "application/json", ...authHeaders() },
    }, 30_000)).json;
    operationEvents = (await request(`${manifest.baseUrl}/api/v1/ppt/workspace/presentations/${presentationId}/events?limit=200`, {
      headers: { Accept: "application/json", ...authHeaders() },
    }, 30_000)).json;
  }
  const observed = stageTimings(timeline, operationId);
  if (!observed.operation) fail(`${deck.id}: timeline contains no matching workspace operation`);
  const endpointEvents = eventsFromPayload(operationEvents).filter((event) => eventMatchesOperation(event, operationId));
  if (!endpointEvents.length) fail(`${deck.id}: events endpoint contains no matching workspace operation events`);
  const retry = retryAudit([...observed.events, ...endpointEvents]);
  if (Number.isFinite(retry.retryCount) && retry.retryCount !== 0) {
    throw new BenchmarkRequestError(`${deck.id}: hidden/internal retry count ${retry.retryCount}`,
      "hidden_internal_retry_detected", retry);
  }
  const [resourceAfter, resourceTreeAfter] = await Promise.all([
    sampleResources(resourcePids), sampleResourceTree(resourcePids),
  ]);
  const phaseTimingsMs = { ...observed.phaseTimingsMs, export: Date.now() - exportStartedAt };
  return {
    deckId: deck.id, taskId, operationId, presentationId, exportAs: deck.exportAs,
    qualityProfile: deck.qualityProfile, pptxMode: deck.pptxMode,
    startedAt: new Date(startedAt).toISOString(), completedAt: now(), latencyMs: Date.now() - startedAt,
    output: { path: destination, bytes: downloaded.bytes.length, sha256: sha256(downloaded.bytes) },
    generationArtifactPath: data.path,
    pdfExport, pptxExport,
    statusHistory, stages: observed.stages, operation: observed.operation, retryAudit: retry,
    admissionMs, phaseTimingsMs,
    resources: {
      before: resourceBefore, after: resourceAfter,
      incrementalDelta: resourceDelta(resourceBefore, resourceAfter),
      attribution: manifest.concurrency === 1 ? "single-operation process delta" : "shared-process snapshot; not exclusively attributable under concurrency",
      processTree: {
        before: resourceTreeBefore,
        after: resourceTreeAfter,
        jobExclusiveAttribution: "unverified",
      },
    },
  };
}

async function commandJson(executable, args) {
  return new Promise((resolve) => {
    const child = spawn(executable, args, { windowsHide: true, stdio: ["ignore", "pipe", "pipe"] });
    let stdout = ""; let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("close", (code) => resolve({ code, stdout, stderr }));
    child.on("error", (error) => resolve({ code: -1, stdout, stderr: String(error) }));
  });
}

async function sampleResources(pids) {
  if (!pids.length || process.platform !== "win32") return null;
  const script = [
    `$ids=@(${pids.join(",")})`,
    "$rows=@(Get-Process -Id $ids -ErrorAction SilentlyContinue | ForEach-Object {",
    "[ordered]@{pid=$_.Id;cpuSeconds=$_.CPU;workingSetBytes=$_.WorkingSet64;privateBytes=$_.PrivateMemorySize64;handles=$_.HandleCount}})",
    "$rows | ConvertTo-Json -Compress",
  ].join("; ");
  const result = await commandJson("pwsh", ["-NoLogo", "-NoProfile", "-Command", script]);
  if (result.code !== 0) return { error: result.stderr.trim() || `pwsh exit ${result.code}` };
  try { return JSON.parse(result.stdout || "[]"); } catch { return { error: "resource sample JSON invalid" }; }
}

function finiteMetric(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric >= 0 ? numeric : null;
}

function resolveProcessTree(rows, rootPids) {
  if (!Array.isArray(rows)) return { status: "unverified", reason: "process_snapshot_unavailable", processes: [] };
  const roots = [...new Set(rootPids)].filter((pid) => Number.isInteger(pid) && pid > 0);
  const byPid = new Map(rows.map((row) => [Number(row.pid), row]).filter(([pid]) => Number.isInteger(pid) && pid > 0));
  const children = new Map();
  for (const [pid, row] of byPid) {
    const parentPid = Number(row.parentPid);
    if (!children.has(parentPid)) children.set(parentPid, []);
    children.get(parentPid).push(pid);
  }
  const attribution = new Map();
  const queue = [];
  for (const rootPid of roots) {
    if (!byPid.has(rootPid)) continue;
    attribution.set(rootPid, { rootPid, descendantDepth: 0 });
    queue.push(rootPid);
  }
  while (queue.length) {
    const parentPid = queue.shift();
    const parent = attribution.get(parentPid);
    for (const childPid of children.get(parentPid) || []) {
      if (attribution.has(childPid)) continue;
      attribution.set(childPid, { rootPid: parent.rootPid, descendantDepth: parent.descendantDepth + 1 });
      queue.push(childPid);
    }
  }
  const processes = [...attribution.entries()].map(([pid, scope]) => {
    const row = byPid.get(pid);
    const imageName = String(row.imageName || "").slice(0, 128);
    return {
      pid,
      parentPid: Number.isInteger(Number(row.parentPid)) ? Number(row.parentPid) : null,
      rootPid: scope.rootPid,
      descendantDepth: scope.descendantDepth,
      imageName,
      processRole: /(chrome|chromium|msedge)/i.test(imageName) ? "chrome"
        : /(python|node|pwsh|powershell)/i.test(imageName) ? "worker" : "other",
      cpuSeconds: finiteMetric(row.cpuSeconds),
      workingSetBytes: finiteMetric(row.workingSetBytes),
      privateBytes: finiteMetric(row.privateBytes),
      handles: finiteMetric(row.handles),
    };
  });
  const observedRoots = roots.filter((pid) => byPid.has(pid));
  return {
    status: observedRoots.length === roots.length ? "assessed" : observedRoots.length ? "partial" : "unverified",
    reason: observedRoots.length === roots.length ? null : "one_or_more_root_pids_unavailable",
    configuredRootPids: roots,
    observedRootPids: observedRoots,
    processes,
    attributionMethod: "same-snapshot parent chain from configured roots",
    jobExclusiveAttribution: "unverified",
  };
}

async function sampleResourceTree(pids) {
  if (!pids.length || process.platform !== "win32") {
    return { status: "unverified", reason: "Windows process ancestry unavailable", processes: [] };
  }
  const script = [
    "$rows=@(Get-CimInstance Win32_Process -ErrorAction Stop | ForEach-Object {",
    "[ordered]@{pid=[int]$_.ProcessId;parentPid=[int]$_.ParentProcessId;imageName=[string]$_.Name;cpuSeconds=(([double]$_.KernelModeTime+[double]$_.UserModeTime)/10000000);workingSetBytes=[double]$_.WorkingSetSize;privateBytes=[double]$_.PrivatePageCount;handles=[double]$_.HandleCount}})",
    "$rows | ConvertTo-Json -Compress",
  ].join("; ");
  const result = await commandJson("pwsh", ["-NoLogo", "-NoProfile", "-Command", script]);
  if (result.code !== 0) return { status: "unverified", reason: "process_tree_sample_failed", processes: [] };
  try {
    const payload = JSON.parse(result.stdout || "[]");
    return resolveProcessTree(Array.isArray(payload) ? payload : [payload], pids);
  } catch {
    return { status: "unverified", reason: "process tree sample JSON invalid", processes: [] };
  }
}

async function resourceLoop(manifest, state) {
  while (!state.stop) {
    const pids = [...new Set(state.pids)];
    const [processes, processTree] = await Promise.all([sampleResources(pids), sampleResourceTree(pids)]);
    state.samples.push({ observedAt: now(), processes, processTree });
    await sleep(manifest.resourceIntervalMs);
  }
}

function resourceTotals(processes) {
  if (!Array.isArray(processes)) return null;
  return processes.reduce((total, process) => ({
    cpuSeconds: total.cpuSeconds + (Number(process.cpuSeconds) || 0),
    workingSetBytes: total.workingSetBytes + (Number(process.workingSetBytes) || 0),
    privateBytes: total.privateBytes + (Number(process.privateBytes) || 0),
    handles: total.handles + (Number(process.handles) || 0),
  }), { cpuSeconds: 0, workingSetBytes: 0, privateBytes: 0, handles: 0 });
}

function resourceDelta(before, after) {
  const initial = resourceTotals(before);
  const final = resourceTotals(after);
  if (!initial || !final) return null;
  return Object.fromEntries(Object.entries(final).map(([key, value]) => [key, value - initial[key]]));
}

function resourceSummary(samples) {
  const totals = samples.map((sample) => resourceTotals(sample.processes)).filter(Boolean);
  const baseline = totals[0] || null;
  const treeSamples = samples.map((sample) => sample.processTree).filter((sample) => sample && Array.isArray(sample.processes));
  if (!baseline) return {
    status: "not_assessed",
    reason: "no Windows resource samples",
    processTree: {
      status: "unverified",
      reason: treeSamples.at(-1)?.reason || "process tree samples unavailable",
      configuredRootPids: treeSamples[0]?.configuredRootPids || [],
      observedDescendantPids: [],
      chromeDescendantPeak: null,
      workerDescendantPeak: null,
      jobExclusiveAttribution: "unverified",
    },
  };
  const peak = totals.reduce((current, value) => ({
    cpuSeconds: Math.max(current.cpuSeconds, value.cpuSeconds),
    workingSetBytes: Math.max(current.workingSetBytes, value.workingSetBytes),
    privateBytes: Math.max(current.privateBytes, value.privateBytes),
    handles: Math.max(current.handles, value.handles),
  }), { ...baseline });
  const rolePeak = (role) => Math.max(0, ...treeSamples.map((sample) => sample.processes
    .filter((process) => process.descendantDepth > 0 && process.processRole === role).length));
  const observedDescendantPids = [...new Set(treeSamples.flatMap((sample) => sample.processes
    .filter((process) => process.descendantDepth > 0).map((process) => process.pid)))];
  const treeStatus = treeSamples.some((sample) => sample.status === "assessed") ? "assessed"
    : treeSamples.some((sample) => sample.status === "partial") ? "partial" : "unverified";
  return {
    status: "assessed", baseline, peak,
    incrementalPeak: Object.fromEntries(Object.entries(peak).map(([key, value]) => [key, value - baseline[key]])),
    samplingScope: "sum of configured resourcePids; task snapshots carry a shared-process attribution label",
    processTree: {
      status: treeStatus,
      configuredRootPids: treeSamples[0]?.configuredRootPids || [],
      observedDescendantPids,
      chromeDescendantPeak: treeStatus === "unverified" ? null : rolePeak("chrome"),
      workerDescendantPeak: treeStatus === "unverified" ? null : rolePeak("worker"),
      attributionMethod: "same-snapshot parent chain from configured roots",
      jobExclusiveAttribution: "unverified",
    },
  };
}

async function atomicJson(target, payload) {
  await mkdir(path.dirname(target), { recursive: true });
  const temporary = `${target}.${process.pid}.tmp`;
  await writeFile(temporary, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  await rename(temporary, target);
}

async function runSingleControlledLoad(manifest, hook = null, scenario = null) {
  await mkdir(manifest.outputDirectory, { recursive: true });
  const cacheBefore = await fetchCacheTelemetry(manifest);
  const poolBefore = await sampleResources([...new Set(manifest.resourcePids)]);
  if (!Array.isArray(poolBefore) || poolBefore.length !== manifest.resourcePids.length) {
    fail("one or more configured resourcePids are unavailable before controlled scenario");
  }
  const coldControl = coldControlResult(scenario, cacheBefore);
  const startedAt = Date.now();
  const resourceState = { stop: false, pids: manifest.resourcePids, samples: [] };
  const sampler = resourceLoop(manifest, resourceState);
  const results = []; const failures = [];
  let ordinal = 0;
  const soakDeadline = manifest.soakDurationSeconds ? startedAt + manifest.soakDurationSeconds * 1000 : null;
  const queue = [];
  do {
    for (const deck of manifest.decks) queue.push({ deck, ordinal: ++ordinal });
    if (!soakDeadline) break;
  } while (Date.now() < soakDeadline && queue.length < manifest.concurrency * 2);
  let cursor = 0;
  async function worker() {
    while (true) {
      if (cursor >= queue.length) {
        if (soakDeadline && Date.now() < soakDeadline) {
          for (const deck of manifest.decks) queue.push({ deck, ordinal: ++ordinal });
        } else return;
      }
      const item = queue[cursor++];
      try { results.push(await runDeck(item.deck, manifest, item.ordinal, hook, scenario)); }
       catch (error) {
        const retryDetail = error instanceof BenchmarkRequestError
          ? error.details?.retryAudit || (Number.isFinite(error.details?.retryCount) ? error.details : null)
          : null;
        failures.push({ deckId: item.deck.id, ordinal: item.ordinal,
          classification: error instanceof BenchmarkRequestError ? error.classification : "operation_failed",
          retryAudit: Number.isFinite(retryDetail?.retryCount) ? retryDetail.retryCount : null,
          retryAuditStatus: retryDetail?.status || "unavailable",
          retryAuditDetail: retryDetail || { status: "unavailable", providerAttemptCount: null, retryCount: null,
            recoveryReason: null, recoveryStatus: null,
            unavailable: ["providerAttemptCount", "retryCount", "attemptNumber", "recoveryReason", "recoveryStatus"] },
          durableOperationAudit: error instanceof BenchmarkRequestError ? error.details?.durableAudit || null : null,
          taskId: error instanceof BenchmarkRequestError ? error.details?.taskId || null : null,
          operationId: error instanceof BenchmarkRequestError ? error.details?.operationId || null : null,
          error: String(error?.stack || error) });
       }
    }
  }
  await Promise.all(Array.from({ length: manifest.concurrency }, () => worker()));
  resourceState.stop = true;
  await sampler;
  const cacheAfter = await fetchCacheTelemetry(manifest);
  const poolAfter = await sampleResources([...new Set(manifest.resourcePids)]);
  if (!Array.isArray(poolAfter) || poolAfter.length !== manifest.resourcePids.length) {
    fail("one or more configured resourcePids are unavailable after controlled scenario");
  }
  const cacheTelemetry = {
    before: cacheBefore,
    after: cacheAfter,
    delta: cacheTelemetryDelta(cacheBefore, cacheAfter),
    coldControl,
  };
  const poolTelemetry = {
    before: poolBefore,
    after: poolAfter,
    incrementalDelta: resourceDelta(poolBefore, poolAfter),
    samplingScope: "sum of configured owned resourcePids; shared-process attribution applies when concurrency exceeds one",
  };
  const wallTimeMs = Date.now() - startedAt;
  const report = {
    schemaVersion: 2, kind: "authored-vnext-controlled-load", scenario, startedAt: new Date(startedAt).toISOString(),
    completedAt: now(), wallTimeMs, concurrency: manifest.concurrency, requestedOperations: results.length + failures.length,
    outputDirectory: manifest.outputDirectory,
    succeededOperations: results.length, failedOperations: failures.length,
    successRate: results.length / Math.max(1, results.length + failures.length),
    latencyMs: { observations: results.length, p50Ms: percentile(results.map((result) => result.latencyMs), 0.5), p95Ms: percentile(results.map((result) => result.latencyMs), 0.95) },
    phaseTimingsMs: phaseSummary(results),
    perOperationLatencyMs: results.map(({ deckId, latencyMs }) => ({ deckId, latencyMs })),
    results, failures, resources: { samples: resourceState.samples, summary: resourceSummary(resourceState.samples) },
    requestFailures: requestFailureSummary(failures),
    cacheTelemetry, poolTelemetry,
    fixedInvariants: manifest.matrix?.fixedInvariants || null,
    deckInvariants: manifest.deckInvariants,
    soakVerification: {
      requestedDurationSeconds: manifest.soakDurationSeconds,
      twoHourVerified: manifest.soakDurationSeconds >= 7200 && wallTimeMs >= 7_200_000,
      sevenDayVerified: manifest.soakDurationSeconds >= 604_800 && wallTimeMs >= 604_800_000,
    },
    notVerified: ["PowerPoint COM reopen/readback", "cross-renderer visual parity"],
  };
  await atomicJson(path.join(manifest.outputDirectory, "controlled-load-report.json"), report);
  return report;
}

function matrixScenarios(manifest) {
  if (!manifest.matrix) return [{ manifest, scenario: null }];
  const scenarios = [];
  for (const cacheState of manifest.matrix.cacheStates) for (const nSlides of manifest.matrix.slideCounts)
    for (const concurrency of manifest.matrix.concurrencyLevels) {
      const label = `${cacheState}-${nSlides}slides-c${concurrency}`;
      scenarios.push({
        scenario: { cacheState, nSlides, concurrency, repetitions: manifest.matrix.repetitions,
          fixedInvariants: manifest.matrix.fixedInvariants,
          coldControl: cacheState === "cold" ? manifest.matrix.coldControlReceipts[label] : { status: "warm-no-cold-control" },
        },
        manifest: { ...manifest, concurrency, soakDurationSeconds: 0,
          outputDirectory: path.join(manifest.outputDirectory, "matrix", label),
          decks: Array.from({ length: manifest.matrix.repetitions }, (_, index) => ({ ...manifest.decks[0], id: `${label}-r${index + 1}`, nSlides })) },
      });
    }
  return scenarios;
}

async function runControlledLoad(manifest, hook = null) {
  const scenarios = matrixScenarios(manifest);
  if (scenarios.length === 1 && !manifest.matrix) return runSingleControlledLoad(manifest, hook);
  const startedAt = Date.now();
  const reports = [];
  for (const item of scenarios) reports.push(await runSingleControlledLoad(item.manifest, hook, item.scenario));
  const results = reports.flatMap((report) => report.results);
  const matrixReport = {
    schemaVersion: 2, kind: "authored-vnext-controlled-load-matrix", startedAt: reports[0]?.startedAt || now(), completedAt: now(), wallTimeMs: Date.now() - startedAt,
    matrix: manifest.matrix, fixedInvariants: manifest.matrix.fixedInvariants, scenarios: reports.map((report) => ({ scenario: report.scenario, outputDirectory: report.outputDirectory || null,
      successRate: report.successRate, latencyMs: report.latencyMs, phaseTimingsMs: report.phaseTimingsMs, requestFailures: report.requestFailures,
      cacheTelemetry: report.cacheTelemetry, soakVerification: report.soakVerification })),
    aggregate: { operations: results.length, latencyMs: { observations: results.length, p50Ms: percentile(results.map((result) => result.latencyMs), .5), p95Ms: percentile(results.map((result) => result.latencyMs), .95) }, phaseTimingsMs: phaseSummary(results) },
    soakVerification: { twoHourVerified: false, sevenDayVerified: false, reason: "matrix runs are bounded benchmarks, not soak evidence" },
    productPathExecuted: true,
  };
  await atomicJson(path.join(manifest.outputDirectory, "controlled-load-matrix-report.json"), matrixReport);
  return { ...matrixReport, successRate: reports.reduce((sum, report) => sum + report.successRate, 0) / reports.length, failedOperations: reports.reduce((sum, report) => sum + report.failedOperations, 0) };
}

function durabilityConfig(manifest) {
  const config = manifest.durability;
  if (!config || config.confirmIsolated !== "AUTHORED_VNEXT_CONTROLLED_LOAD_V1") fail("durability.confirmIsolated acknowledgement is required");
  const isolatedRoot = path.resolve(REPO, config.isolatedRoot || "");
  if (!inside(isolatedRoot, EVIDENCE_ROOT)) fail("durability.isolatedRoot must be below the controlled evidence root");
  const appData = path.resolve(process.env.APP_DATA_DIRECTORY || "");
  if (!process.env.APP_DATA_DIRECTORY || !inside(appData, isolatedRoot)) fail("APP_DATA_DIRECTORY must be below durability.isolatedRoot");
  if ((process.env.WORKSPACE_OPERATION_WORKER_MODE || "").toLowerCase() !== "external") fail("WORKSPACE_OPERATION_WORKER_MODE=external is required");
  const databaseUrl = process.env.DATABASE_URL || "";
  if (!databaseUrl.startsWith("sqlite+") || !databaseUrl.replace(/\\/g, "/").includes(isolatedRoot.replace(/\\/g, "/"))) fail("isolated SQLite DATABASE_URL below durability.isolatedRoot is required");
  const workerCwd = path.resolve(REPO, config.workerCwd || "servers/fastapi");
  if (workerCwd !== path.join(REPO, "servers", "fastapi")) fail("workerCwd must be the repository FastAPI directory");
  const python = path.resolve(config.python || process.env.PRESENTON_BENCH_PYTHON || "");
  if (!config.python && !process.env.PRESENTON_BENCH_PYTHON) fail("durability.python or PRESENTON_BENCH_PYTHON is required");
  const leaseSeconds = Number(config.leaseSeconds ?? 8);
  if (!Number.isInteger(leaseSeconds) || leaseSeconds < 2 || leaseSeconds > 120) fail("durability.leaseSeconds must be 2..120");
  if (manifest.concurrency !== 1 || manifest.decks.length !== 1 || manifest.soakDurationSeconds) fail("durability mode requires exactly one deck, concurrency=1 and no soak");
  return { isolatedRoot, workerCwd, python, leaseSeconds };
}

async function stopOwnedWorker(owned) {
  if (!owned?.child || owned.child.exitCode != null) return;
  owned.child.kill("SIGKILL");
  await Promise.race([new Promise((resolve) => owned.child.once("exit", resolve)), sleep(10_000)]);
}

function startOwnedWorker(config, outputDirectory, sequence) {
  const log = createWriteStream(path.join(outputDirectory, `owned-worker-${sequence}.log`), { flags: "a" });
  const child = spawn(config.python, ["-m", "services.workspace_operation_worker"], {
    cwd: config.workerCwd, env: process.env, windowsHide: true, stdio: ["ignore", log, log],
  });
  child.on("error", (error) => log.write(`worker spawn error: ${String(error)}\n`));
  if (!Number.isInteger(child.pid) || child.pid <= 0) fail("owned worker did not return a PID");
  return { child, pid: child.pid, startedAt: now(), log, sequence };
}

async function runDurability(manifest) {
  const config = durabilityConfig(manifest);
  await mkdir(manifest.outputDirectory, { recursive: true });
  const workers = []; let crashed = false; let current = startOwnedWorker(config, manifest.outputDirectory, 1);
  manifest.resourcePids.push(current.pid);
  workers.push({ pid: current.pid, startedAt: current.startedAt, action: "spawned" });
  await sleep(1500);
  try {
    const report = await runControlledLoad(manifest, async ({ status }) => {
      if (crashed || !["running", "in-progress", "in_progress", "processing"].includes(String(status?.status || "").toLowerCase())) return;
      crashed = true;
      workers.push({ pid: current.pid, observedAt: now(), action: "killed-owned-child" });
      await stopOwnedWorker(current);
      current.log.end();
      await sleep((config.leaseSeconds + 1) * 1000);
      current = startOwnedWorker(config, manifest.outputDirectory, 2);
      manifest.resourcePids.push(current.pid);
      workers.push({ pid: current.pid, startedAt: current.startedAt, action: "restarted-owned-child" });
    });
    const raw = JSON.stringify(report.results);
    const recoveryObserved = raw.includes("operation_recovery_required");
    const durability = { crashed, recoveryObserved, workers, isolatedRoot: config.isolatedRoot };
    if (!crashed) fail("operation completed before an owned-worker crash could be injected");
    if (!recoveryObserved) fail("public timeline did not prove operation_recovery_required");
    report.durability = durability;
    await atomicJson(path.join(manifest.outputDirectory, "controlled-load-report.json"), report);
    return report;
  } finally {
    await stopOwnedWorker(current);
    current.log.end();
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) { console.log(usage()); return; }
  const modes = [args.validate, args.run, args.durability].filter(Boolean).length;
  if (modes !== 1 || !args.manifest) fail(usage());
  const manifest = normalizeManifest(JSON.parse(await readFile(path.resolve(args.manifest), "utf8")));
  if (args.validate) {
    let checkpointProbe = null;
    if (args["checkpoint-python"]) {
      const probeOutput = path.join(manifest.outputDirectory, "checkpoint-probe.json");
      const probeRoot = path.join(manifest.outputDirectory, "checkpoint-probe-runtime");
      const probe = await commandJson(path.resolve(args["checkpoint-python"]), [
        path.join(REPO, "scripts", "authored-vnext-controlled-load-checkpoint-probe.py"),
        "--root", probeRoot, "--output", probeOutput,
      ]);
      if (probe.code !== 0) fail(`checkpoint probe failed (${probe.code}): ${probe.stderr || probe.stdout}`);
      checkpointProbe = JSON.parse(probe.stdout);
    }
    await atomicJson(path.join(manifest.outputDirectory, "manifest-validation.json"), {
      schemaVersion: 2, valid: true, validatedAt: now(), deckCount: manifest.decks.length, matrix: manifest.matrix,
      fixedInvariants: manifest.matrix?.fixedInvariants || null,
      formats: [...new Set(manifest.decks.map((deck) => deck.exportAs))],
      checkpointProbe,
      productPathExecuted: false,
      soakVerification: { twoHourVerified: false, sevenDayVerified: false, reason: "validate mode does not execute wall-clock soak" },
      notVerified: ["FastAPI", "worker", "Chromium", "PPTX/PDF I/O", "cold/warm cache state", "two-hour soak", "seven-day soak", "durability"],
    });
    console.log(`validated: ${manifest.outputDirectory}`);
    return;
  }
  const report = args.durability ? await runDurability(manifest) : await runControlledLoad(manifest);
  console.log(JSON.stringify({ wallTimeMs: report.wallTimeMs, successRate: report.successRate, outputDirectory: manifest.outputDirectory }));
  if (report.failedOperations) process.exitCode = 2;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => { console.error(error?.stack || error); process.exitCode = 1; });
}

export { durableOperationAudit, resolveProcessTree };
