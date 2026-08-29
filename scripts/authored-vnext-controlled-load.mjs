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

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const EVIDENCE_ROOT = path.join(REPO, "artifacts", "authored-vnext-controlled-load-20260829");
const TERMINAL = new Set(["completed", "error", "failed", "cancelled", "canceled"]);

function fail(message) { throw new Error(message); }
function now() { return new Date().toISOString(); }
function sleep(ms) { return new Promise((resolve) => setTimeout(resolve, ms)); }
function sha256(bytes) { return createHash("sha256").update(bytes).digest("hex"); }
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
  ].join("\n");
}

function normalizeManifest(raw) {
  if (raw?.schemaVersion !== 1) fail("manifest schemaVersion must be 1");
  const outputDirectory = path.resolve(REPO, raw.outputDirectory || "");
  if (!inside(outputDirectory, EVIDENCE_ROOT) || outputDirectory === EVIDENCE_ROOT) {
    fail(`outputDirectory must be a child of ${EVIDENCE_ROOT}`);
  }
  const baseUrl = new URL(raw.baseUrl || "http://127.0.0.1:8010");
  if (!["http:", "https:"].includes(baseUrl.protocol)) fail("baseUrl must use http or https");
  const concurrency = Number(raw.concurrency ?? 2);
  const pollIntervalMs = Number(raw.pollIntervalMs ?? 2000);
  const timeoutMs = Number(raw.timeoutMs ?? 1_800_000);
  const resourceIntervalMs = Number(raw.resourceIntervalMs ?? 2000);
  const soakDurationSeconds = Number(raw.soakDurationSeconds ?? 0);
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
    return { ...deck, id, exportAs, nSlides };
  });
  const resourcePids = [...new Set((raw.resourcePids || []).map(Number))];
  if (resourcePids.some((pid) => !Number.isInteger(pid) || pid <= 0)) fail("resourcePids must contain positive integers");
  return {
    schemaVersion: 1, baseUrl: baseUrl.href.replace(/\/$/, ""), outputDirectory,
    concurrency, pollIntervalMs, timeoutMs, resourceIntervalMs, soakDurationSeconds,
    resourcePids, requireWorkspaceOperation: raw.requireWorkspaceOperation !== false,
    decks, durability: raw.durability || null,
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
    const response = await fetch(url, { ...init, signal: controller.signal });
    const bytes = Buffer.from(await response.arrayBuffer());
    const text = bytes.toString("utf8");
    let json = null;
    try { json = JSON.parse(text); } catch { /* binary or non-JSON body */ }
    if (!response.ok) fail(`HTTP ${response.status} ${url}: ${text.slice(0, 600)}`);
    return { response, bytes, text, json };
  } finally {
    clearTimeout(timer);
  }
}

function requestBody(deck) {
  const body = {
    content: deck.content, n_slides: deck.nSlides, template: "authored",
    strategy: "authored", export_as: "pptx",
    include_title_slide: deck.includeTitleSlide !== false,
    include_table_of_contents: deck.includeTableOfContents === true,
    web_search: deck.webSearch === true, vision_qa: deck.visionQa === true,
  };
  for (const [source, target] of [["language", "language"], ["style", "authored_style"],
    ["instructions", "instructions"], ["tone", "tone"], ["verbosity", "verbosity"]]) {
    if (deck[source] != null) body[target] = deck[source];
  }
  return body;
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
  return { operation: item || null, stages };
}

async function runDeck(deck, manifest, ordinal, onStatus = null) {
  const startedAt = Date.now();
  const headers = { Accept: "application/json", "Content-Type": "application/json", ...authHeaders() };
  const admitted = await request(`${manifest.baseUrl}/api/v1/ppt/presentation/generate/async`, {
    method: "POST", headers, body: JSON.stringify(requestBody(deck)),
  }, manifest.timeoutMs);
  const taskId = admitted.json?.id;
  const operationId = admitted.json?.operation_id || admitted.json?.operationId || null;
  if (!taskId) fail(`${deck.id}: admission returned no task id`);
  if (manifest.requireWorkspaceOperation && !operationId) fail(`${deck.id}: admission returned no workspace operation id`);
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
  if (terminal.status !== "completed") fail(`${deck.id}: terminal status ${terminal.status}: ${JSON.stringify(terminal.failure || terminal.error || terminal.message)}`);
  const data = terminal.data || {};
  const presentationId = data.presentation_id || data.presentationId;
  let downloadPath = data.path;
  if (!presentationId || !downloadPath) fail(`${deck.id}: completed result lacks presentation_id/path`);
  let pdfExport = null;
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
  if (operationId) {
    timeline = (await request(`${manifest.baseUrl}/api/v1/ppt/workspace/presentations/${presentationId}/timeline?limit=200`, {
      headers: { Accept: "application/json", ...authHeaders() },
    }, 30_000)).json;
  }
  const observed = stageTimings(timeline, operationId);
  return {
    deckId: deck.id, taskId, operationId, presentationId, exportAs: deck.exportAs,
    startedAt: new Date(startedAt).toISOString(), completedAt: now(), latencyMs: Date.now() - startedAt,
    output: { path: destination, bytes: downloaded.bytes.length, sha256: sha256(downloaded.bytes) },
    generationArtifactPath: data.path,
    pdfExport,
    statusHistory, stages: observed.stages, operation: observed.operation,
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

async function resourceLoop(manifest, state) {
  while (!state.stop) {
    state.samples.push({ observedAt: now(), processes: await sampleResources([...new Set(state.pids)]) });
    await sleep(manifest.resourceIntervalMs);
  }
}

async function atomicJson(target, payload) {
  await mkdir(path.dirname(target), { recursive: true });
  const temporary = `${target}.${process.pid}.tmp`;
  await writeFile(temporary, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  await rename(temporary, target);
}

async function runControlledLoad(manifest, hook = null) {
  await mkdir(manifest.outputDirectory, { recursive: true });
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
      try { results.push(await runDeck(item.deck, manifest, item.ordinal, hook)); }
      catch (error) { failures.push({ deckId: item.deck.id, ordinal: item.ordinal, error: String(error?.stack || error) }); }
    }
  }
  await Promise.all(Array.from({ length: manifest.concurrency }, () => worker()));
  resourceState.stop = true;
  await sampler;
  const wallTimeMs = Date.now() - startedAt;
  const report = {
    schemaVersion: 1, kind: "authored-vnext-controlled-load", startedAt: new Date(startedAt).toISOString(),
    completedAt: now(), wallTimeMs, concurrency: manifest.concurrency, requestedOperations: results.length + failures.length,
    succeededOperations: results.length, failedOperations: failures.length,
    successRate: results.length / Math.max(1, results.length + failures.length),
    perOperationLatencyMs: results.map(({ deckId, latencyMs }) => ({ deckId, latencyMs })),
    results, failures, resources: resourceState.samples,
    sevenDaySoakVerified: manifest.soakDurationSeconds >= 604_800 && wallTimeMs >= 604_800_000,
    notVerified: ["PowerPoint COM reopen/readback", "cross-renderer visual parity"],
  };
  await atomicJson(path.join(manifest.outputDirectory, "controlled-load-report.json"), report);
  return report;
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
      schemaVersion: 1, valid: true, validatedAt: now(), deckCount: manifest.decks.length,
      formats: [...new Set(manifest.decks.map((deck) => deck.exportAs))],
      checkpointProbe,
      productPathExecuted: false, notVerified: ["FastAPI", "worker", "Chromium", "PPTX/PDF I/O", "durability"],
    });
    console.log(`validated: ${manifest.outputDirectory}`);
    return;
  }
  const report = args.durability ? await runDurability(manifest) : await runControlledLoad(manifest);
  console.log(JSON.stringify({ wallTimeMs: report.wallTimeMs, successRate: report.successRate, outputDirectory: manifest.outputDirectory }));
  if (report.failedOperations) process.exitCode = 2;
}

main().catch((error) => { console.error(error?.stack || error); process.exitCode = 1; });

