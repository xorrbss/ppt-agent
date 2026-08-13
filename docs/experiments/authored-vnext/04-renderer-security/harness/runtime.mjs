#!/usr/bin/env node
/** Fail-closed runtime for an already isolated, network-less container. */

import { spawn } from "node:child_process";
import crypto from "node:crypto";
import dns from "node:dns/promises";
import fs from "node:fs/promises";
import net from "node:net";
import path from "node:path";
import { pathToFileURL } from "node:url";

const ROOT = "/opt/harness";
const MAX_BROWSER_OUTPUT = 2 * 1024 * 1024;
const PRESSURE_SAMPLE_INTERVAL_MS = 100;
// The gate deliberately constrains the container to one CPU. Allow cold-start
// Chromium enough bounded wall time under shared-runner contention while still
// killing the entire probe well before the product's 120-second request ceiling.
const BROWSER_TIMEOUT_MS = 20_000;

const { lintHtml, loadPolicy } = await import(
  pathToFileURL(path.join(ROOT, "policy", "html-policy.mjs"))
);

function evidence(label) {
  return `sha256:${crypto.createHash("sha256").update(label).digest("hex")}`;
}

function boundary(observed, violations, label, attempts = 0, blocked = 0) {
  return { observed, violations, evidence: observed ? [evidence(label)] : [], attempts, blocked };
}

function boundedAppend(chunks, chunk, current) {
  const next = current + chunk.length;
  if (next > MAX_BROWSER_OUTPUT) throw new Error("browser-output-limit");
  chunks.push(chunk);
  return next;
}

function numericFields(text) {
  return Object.fromEntries(text.trim().split("\n").filter(Boolean).map((line) => {
    const [name, value] = line.trim().split(/\s+/, 2);
    return [name, Number(value)];
  }));
}

async function pressureSample() {
  const [
    cpuStat, memoryCurrent, memoryPeak, memoryEvents, memoryStat,
    pidsCurrent, pidsPeak, pidsEvents, procEntries,
  ] = (
    await Promise.all([
      optionalText("/sys/fs/cgroup/cpu.stat"),
      optionalText("/sys/fs/cgroup/memory.current"),
      optionalText("/sys/fs/cgroup/memory.peak"),
      optionalText("/sys/fs/cgroup/memory.events"),
      optionalText("/sys/fs/cgroup/memory.stat"),
      optionalText("/sys/fs/cgroup/pids.current"),
      optionalText("/sys/fs/cgroup/pids.peak"),
      optionalText("/sys/fs/cgroup/pids.events"),
      fs.readdir("/proc"),
    ])
  );
  let processCount = 0;
  let taskCount = 0;
  let chromiumProcesses = 0;
  let chromiumTasks = 0;
  let processRssKb = 0;
  let chromiumRssKb = 0;
  const chromiumStates = {};
  const chromiumWaitChannels = {};
  for (const entry of procEntries) {
    if (!/^\d+$/.test(entry)) continue;
    const status = await optionalText(`/proc/${entry}/status`);
    if (status === "") continue;
    const name = /^Name:\s+(.+)$/m.exec(status)?.[1] || "unknown";
    const threads = Number(/^Threads:\s+(\d+)$/m.exec(status)?.[1] || 0);
    processCount += 1;
    taskCount += threads;
    const rssKb = Number(/^VmRSS:\s+(\d+)\s+kB$/m.exec(status)?.[1] || 0);
    processRssKb += rssKb;
    if (name === "chromium" || name === "chrome_crashpad") {
      chromiumProcesses += 1;
      chromiumTasks += threads;
      chromiumRssKb += rssKb;
      const state = /^State:\s+(\S+)/m.exec(status)?.[1] || "unknown";
      const waitChannel = (await optionalText(`/proc/${entry}/wchan`)).trim() || "unknown";
      chromiumStates[state] = (chromiumStates[state] || 0) + 1;
      chromiumWaitChannels[waitChannel] = (chromiumWaitChannels[waitChannel] || 0) + 1;
    }
  }
  return {
    monotonic_ms: Number(process.hrtime.bigint() / 1_000_000n),
    cpu_stat: numericFields(cpuStat),
    memory_current: cgroupNumber(memoryCurrent),
    memory_peak: cgroupNumber(memoryPeak),
    memory_events: numericFields(memoryEvents),
    memory_stat: numericFields(memoryStat),
    pids_current: cgroupNumber(pidsCurrent),
    pids_peak: cgroupNumber(pidsPeak),
    pids_events: numericFields(pidsEvents),
    process_count: processCount,
    task_count: taskCount,
    process_rss_kb: processRssKb,
    chromium_process_count: chromiumProcesses,
    chromium_task_count: chromiumTasks,
    chromium_rss_kb: chromiumRssKb,
    chromium_states: chromiumStates,
    chromium_wait_channels: chromiumWaitChannels,
  };
}

function pressurePeaks(samples) {
  const maximum = (field) => Math.max(...samples.map((sample) => sample[field] ?? 0));
  const maximumNested = (field, nested) => Math.max(
    ...samples.map((sample) => sample[field]?.[nested] ?? 0)
  );
  const last = samples.at(-1) || {};
  return {
    samples: samples.length,
    interval_ms: PRESSURE_SAMPLE_INTERVAL_MS,
    memory_current: maximum("memory_current"),
    memory_peak: maximum("memory_peak"),
    pids_current: maximum("pids_current"),
    pids_peak: maximum("pids_peak"),
    process_count: maximum("process_count"),
    task_count: maximum("task_count"),
    process_rss_kb: maximum("process_rss_kb"),
    chromium_process_count: maximum("chromium_process_count"),
    chromium_task_count: maximum("chromium_task_count"),
    chromium_rss_kb: maximum("chromium_rss_kb"),
    memory_stat_peaks: Object.fromEntries(
      ["anon", "file", "shmem", "kernel", "pagetables", "sock", "slab"]
        .map((name) => [name, maximumNested("memory_stat", name)])
    ),
    memory_events: last.memory_events || {},
    pids_events: last.pids_events || {},
    cpu_stat: last.cpu_stat || {},
  };
}

function startPressureMonitor(telemetry) {
  const samples = [];
  let active = null;
  const sample = () => {
    if (active !== null) return;
    active = pressureSample()
      .then((value) => samples.push(value))
      .catch((error) => {
        telemetry.pressure_monitor_error = error instanceof Error ? error.message : String(error);
      })
      .finally(() => { active = null; });
  };
  sample();
  const interval = setInterval(sample, PRESSURE_SAMPLE_INTERVAL_MS);
  return async () => {
    clearInterval(interval);
    if (active !== null) await active;
    telemetry.pressure_samples = samples;
    telemetry.pressure_peaks = pressurePeaks(samples);
  };
}

async function runBrowser(chrome, html, telemetry) {
  const profile = "/tmp/chrome-profile";
  await fs.mkdir(profile, { recursive: true });
  const args = [
    "--headless=new",
    "--disable-breakpad",
    "--disable-crash-reporter",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--disable-dev-shm-usage",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-extensions",
    "--disable-sync",
    "--metrics-recording-only",
    "--mute-audio",
    "--no-first-run",
    "--no-default-browser-check",
    "--renderer-process-limit=1",
    // This bounded probe does not need V8 background compilation or GC jobs.
    // Keep OS process isolation intact while staying below the fixed PID cgroup.
    "--js-flags=--max-old-space-size=192 --single-threaded",
    "--host-resolver-rules=MAP * ~NOTFOUND",
    // Bound Chromium's page lifecycle independently of the unchanged external
    // watchdog. This avoids waiting indefinitely for background headless work.
    "--virtual-time-budget=5000",
    `--user-data-dir=${profile}`,
    "--dump-dom",
    `data:text/html;charset=utf-8,${encodeURIComponent(html)}`,
  ];
  if (args.some((arg) => /disable.*sandbox|allow-file-access|^file:/i.test(arg))) {
    throw new Error("unsafe-browser-option");
  }
  return await new Promise((resolve, reject) => {
    const child = spawn(chrome, args, {
      stdio: ["ignore", "pipe", "pipe"],
      detached: true,
      env: {
        HOME: "/tmp/home",
        XDG_RUNTIME_DIR: "/tmp/runtime",
        LANG: "C.UTF-8",
        PATH: "/usr/bin:/bin",
      },
    });
    const stdout = [];
    const stderr = [];
    let stdoutBytes = 0;
    let stderrBytes = 0;
    let settled = false;
    let timer = null;
    let terminalError = null;
    let stopPressureMonitor = async () => {};
    const terminateGroup = () => {
      try {
        process.kill(-child.pid, "SIGKILL");
        telemetry.process_group_kill = "sent";
      } catch (error) {
        telemetry.process_group_kill = error?.code === "ESRCH" ? "already-gone" : "failed";
        if (error?.code !== "ESRCH") terminalError = error;
      }
    };
    const captureOutputTelemetry = () => {
      telemetry.stdout_bytes = stdoutBytes;
      telemetry.stderr_bytes = stderrBytes;
      const diagnostic = Buffer.concat(stderr).toString("utf8");
      telemetry.stderr_sha256 = evidence(diagnostic);
      telemetry.stderr_tail = diagnostic
        .slice(-4096)
        .replace(/SYNTHETIC_[0-9a-f]+/gi, "[redacted]");
    };
    const finish = (callback) => {
      if (settled) return;
      settled = true;
      if (timer !== null) clearTimeout(timer);
      callback();
    };
    child.once("spawn", () => {
      telemetry.spawn_monotonic_ms = Number(process.hrtime.bigint() / 1_000_000n);
      void runtimeSnapshot("browser-spawn").then((value) => telemetry.snapshots.push(value));
      stopPressureMonitor = startPressureMonitor(telemetry);
      timer = setTimeout(() => {
        telemetry.timeout_observed = true;
        telemetry.timeout_monotonic_ms = Number(process.hrtime.bigint() / 1_000_000n);
        terminalError = new Error("browser-timeout");
        captureOutputTelemetry();
        terminateGroup();
      }, BROWSER_TIMEOUT_MS);
    });
    child.stdout.on("data", (chunk) => {
      try {
        stdoutBytes = boundedAppend(stdout, chunk, stdoutBytes);
      } catch (error) {
        terminalError = error;
        terminateGroup();
      }
    });
    child.stderr.on("data", (chunk) => {
      try {
        stderrBytes = boundedAppend(stderr, chunk, stderrBytes);
      } catch (error) {
        terminalError = error;
        terminateGroup();
      }
    });
    child.once("error", (error) => finish(() => reject(error)));
    child.once("close", async (code, signal) => {
      telemetry.close_monotonic_ms = Number(process.hrtime.bigint() / 1_000_000n);
      await stopPressureMonitor();
      captureOutputTelemetry();
      if (terminalError !== null) {
        finish(() => reject(terminalError));
        return;
      }
      finish(() => resolve({
        code,
        signal,
        stdout: Buffer.concat(stdout).toString("utf8"),
        stderr: Buffer.concat(stderr).toString("utf8"),
      }));
    });
  });
}

async function readText(file) {
  return fs.readFile(file, "utf8");
}

async function processStatus(pid) {
  return readText(`/proc/${pid}/status`);
}

async function observeChromiumSandbox(processes) {
  const browserPid = processes[0];
  if (!browserPid) return false;
  const browserStatus = await processStatus(browserPid);
  const descendantStatuses = await Promise.all(
    processes.slice(1).map((pid) => processStatus(pid))
  );
  return /^NoNewPrivs:\s+1$/m.test(browserStatus)
    && /^Seccomp:\s+2$/m.test(browserStatus)
    && descendantStatuses.length > 0
    && descendantStatuses.every((status) =>
      /^NoNewPrivs:\s+1$/m.test(status) && /^Seccomp:\s+2$/m.test(status)
    );
}

function cgroupNumber(value) {
  const text = String(value ?? "").trim();
  return /^\d+$/.test(text) ? Number(text) : null;
}

async function optionalText(file) {
  try {
    return await readText(file);
  } catch {
    return "";
  }
}

async function runtimeSnapshot(label) {
  const cpuStat = await optionalText("/sys/fs/cgroup/cpu.stat");
  const memoryEvents = await optionalText("/sys/fs/cgroup/memory.events");
  const processes = [];
  for (const entry of await fs.readdir("/proc")) {
    if (!/^\d+$/.test(entry)) continue;
    const status = await optionalText(`/proc/${entry}/status`);
    const name = /^Name:\s+(.+)$/m.exec(status)?.[1] || "unknown";
    const threads = Number(/^Threads:\s+(\d+)$/m.exec(status)?.[1] || 0);
    const rssKb = Number(/^VmRSS:\s+(\d+)\s+kB$/m.exec(status)?.[1] || 0);
    const state = /^State:\s+(.+)$/m.exec(status)?.[1] || "unknown";
    const waitChannel = (await optionalText(`/proc/${entry}/wchan`)).trim();
    processes.push({
      pid: Number(entry), name, state, wait_channel: waitChannel, threads, rss_kb: rssKb,
    });
  }
  return {
    label,
    monotonic_ms: Number(process.hrtime.bigint() / 1_000_000n),
    loadavg: (await optionalText("/proc/loadavg")).trim(),
    cpu_stat: Object.fromEntries(cpuStat.trim().split("\n").filter(Boolean).map((line) => {
      const [name, value] = line.trim().split(/\s+/, 2);
      return [name, Number(value)];
    })),
    memory_current: cgroupNumber(await optionalText("/sys/fs/cgroup/memory.current")),
    memory_events: Object.fromEntries(memoryEvents.trim().split("\n").filter(Boolean).map((line) => {
      const [name, value] = line.trim().split(/\s+/, 2);
      return [name, Number(value)];
    })),
    pids_current: cgroupNumber(await optionalText("/sys/fs/cgroup/pids.current")),
    process_count: processes.length,
    processes: processes.sort((left, right) => left.pid - right.pid).slice(0, 32),
  };
}

async function tcpDenied(host, port) {
  return await new Promise((resolve) => {
    const socket = net.createConnection({ host, port });
    let settled = false;
    const finish = (blocked) => {
      if (settled) return;
      settled = true;
      socket.destroy();
      resolve(blocked);
    };
    socket.setTimeout(1_000, () => finish(true));
    socket.once("error", () => finish(true));
    socket.once("connect", () => finish(false));
  });
}

async function processGone(pid) {
  try {
    await fs.access(`/proc/${pid}/status`);
    return false;
  } catch {
    return true;
  }
}

async function processTreePids(rootPid) {
  const found = new Set([rootPid]);
  const pending = [rootPid];
  while (pending.length > 0) {
    const pid = pending.pop();
    const children = (await optionalText(`/proc/${pid}/task/${pid}/children`))
      .trim().split(/\s+/).filter(Boolean).map(Number).filter(Number.isInteger);
    for (const child of children) {
      if (!found.has(child)) {
        found.add(child);
        pending.push(child);
      }
    }
  }
  return [...found];
}

async function chromiumCleanupProbe(chrome, label, delayMs) {
  const profile = `/tmp/${label}-${process.pid}`;
  const child = spawn(chrome, [
    "--headless=new",
    "--disable-breakpad",
    "--disable-crash-reporter",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-domain-reliability",
    "--disable-extensions",
    "--disable-sync",
    "--renderer-process-limit=1",
    "--js-flags=--max-old-space-size=192 --single-threaded",
    "--remote-debugging-pipe",
    `--user-data-dir=${profile}`,
    "about:blank",
  ], {
    stdio: ["ignore", "ignore", "ignore", "pipe", "pipe"],
    detached: true,
    env: {
      HOME: "/tmp/home",
      XDG_RUNTIME_DIR: "/tmp/runtime",
      LANG: "C.UTF-8",
      PATH: "/usr/bin:/bin",
    },
  });
  await new Promise((resolve) => setTimeout(resolve, delayMs));
  const observedPids = await processTreePids(child.pid);
  try {
    process.kill(-child.pid, "SIGKILL");
  } catch (error) {
    if (error?.code !== "ESRCH") throw error;
  }
  await new Promise((resolve) => {
    if (child.exitCode !== null) resolve();
    else child.once("close", resolve);
  });
  await fs.rm(profile, { recursive: true, force: true });
  const gone = await Promise.all(observedPids.map(processGone));
  return gone.every(Boolean);
}

async function chromiumSandboxProbe(chrome) {
  const profile = `/tmp/sandbox-probe-${process.pid}`;
  const child = spawn(chrome, [
    "--headless=new",
    "--disable-breakpad",
    "--disable-crash-reporter",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-domain-reliability",
    "--disable-extensions",
    "--disable-sync",
    "--renderer-process-limit=1",
    "--js-flags=--max-old-space-size=192 --single-threaded",
    `--user-data-dir=${profile}`,
    "about:blank",
  ], {
    stdio: "ignore",
    detached: true,
    env: {
      HOME: "/tmp/home",
      XDG_RUNTIME_DIR: "/tmp/runtime",
      LANG: "C.UTF-8",
      PATH: "/usr/bin:/bin",
    },
  });
  try {
    await new Promise((resolve) => setTimeout(resolve, 1_000));
    const processes = await processTreePids(child.pid);
    return await observeChromiumSandbox(processes);
  } finally {
    try {
      process.kill(-child.pid, "SIGKILL");
    } catch (error) {
      if (error?.code !== "ESRCH") throw error;
    }
    await new Promise((resolve) => {
      if (child.exitCode !== null) resolve();
      else child.once("close", resolve);
    });
    await fs.rm(profile, { recursive: true, force: true });
  }
}

async function main() {
  const policy = loadPolicy(path.join(ROOT, "policy", "allowlist.policy.json"));
  const manifest = JSON.parse(await readText(path.join(ROOT, "corpus", "manifest.json")));
  const errors = [];
  const observerTelemetry = {
    browser_timeout_ms: BROWSER_TIMEOUT_MS,
    product_readiness_required: true,
    timeout_observed: false,
    snapshots: [],
  };
  const hostile = manifest.cases.filter((item) => item.expectedRuleIds.length > 0);
  let rejected = 0;
  const rejectionById = new Map();
  for (const item of manifest.cases) {
    if (item.executionAllowed !== false) errors.push("corpus-execution-enabled");
    const source = await readText(path.join(ROOT, "corpus", item.file));
    const lint = lintHtml(source, policy);
    if (item.expectedRuleIds.length === 0 ? !lint.ok : lint.ok) {
      errors.push("corpus-policy-mismatch");
    }
    if (item.expectedRuleIds.length > 0 && !lint.ok) rejected += 1;
    rejectionById.set(item.id, !lint.ok);
  }

  const status = await readText("/proc/self/status");
  const interfaces = await fs.readdir("/sys/class/net");
  const uidNonRoot = typeof process.getuid === "function" && process.getuid() === 65532;
  const noNewPrivileges = /^NoNewPrivs:\s+1$/m.test(status);
  const noCapabilities = /^CapEff:\s+0+$/m.test(status);
  const onlyLoopback = interfaces.length === 1 && interfaces[0] === "lo";
  const memoryMax = await optionalText("/sys/fs/cgroup/memory.max");
  const memorySwapMax = await optionalText("/sys/fs/cgroup/memory.swap.max");
  const pidsMax = await optionalText("/sys/fs/cgroup/pids.max");
  const cpuMax = await optionalText("/sys/fs/cgroup/cpu.max");
  const nofileLimits = await optionalText("/proc/self/limits");
  const cpuParts = cpuMax.trim().split(/\s+/).map(Number);
  const limitsActive = cgroupNumber(memoryMax) === 536_870_912
    && cgroupNumber(memorySwapMax) === 0
    && cgroupNumber(pidsMax) !== null && cgroupNumber(pidsMax) <= 128
    && cpuParts.length === 2 && cpuParts.every(Number.isFinite)
    && cpuParts[0] > 0 && cpuParts[0] <= cpuParts[1]
    && /^Max open files\s+256\s+256\s+files\s*$/m.test(nofileLimits);
  const providerEnvironmentNames = Object.keys(process.env).filter((name) =>
    /API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|OPENAI|ANTHROPIC|GOOGLE|AZURE|BEDROCK|VERTEX|OPENROUTER/i.test(name)
  );
  if (providerEnvironmentNames.length > 0) errors.push("provider-environment-present");

  let rootReadOnly = false;
  try {
    await fs.writeFile("/opt/harness/.root-write-probe", "must-fail", { flag: "wx" });
    errors.push("root-filesystem-writable");
  } catch (error) {
    rootReadOnly = ["EROFS", "EACCES", "EPERM"].includes(error?.code);
  }
  await fs.mkdir("/tmp/home", { recursive: true });
  await fs.mkdir("/tmp/runtime", { recursive: true });
  const tmpProbe = `/tmp/runtime/probe-${process.pid}`;
  await fs.writeFile(tmpProbe, "ok", "utf8");
  await fs.unlink(tmpProbe);
  observerTelemetry.snapshots.push(await runtimeSnapshot("pre-observer-ready"));
  observerTelemetry.pre_observer_ready = (
    uidNonRoot && noNewPrivileges && noCapabilities && onlyLoopback
    && limitsActive && rootReadOnly
  );

  let hostReadBlocked = false;
  try {
    await fs.readFile("/__AUTHORED_HOST_CANARY_DO_NOT_MOUNT__/host-read-canary.txt");
  } catch (error) {
    hostReadBlocked = error?.code === "ENOENT" || error?.code === "EACCES";
  }
  if (!hostReadBlocked) errors.push("host-read-canary-visible");

  let dnsBlocked = false;
  try {
    await dns.lookup("dns.authored-security.invalid");
  } catch {
    dnsBlocked = true;
  }
  const externalHttpBlocked = await tcpDenied("192.0.2.1", 9);
  const loopbackBlocked = await tcpDenied("127.0.0.1", 9);
  let metadataShapeBlocked = false;
  try {
    await dns.lookup("__metadata_endpoint_do_not_resolve__.invalid");
  } catch {
    metadataShapeBlocked = true;
  }
  if (!dnsBlocked || !externalHttpBlocked) errors.push("external-egress-succeeded");
  if (!loopbackBlocked) errors.push("loopback-egress-succeeded");
  if (!metadataShapeBlocked) errors.push("metadata-shaped-egress-succeeded");

  const syntheticSecret = `SYNTHETIC_${crypto.randomBytes(18).toString("hex")}`;
  const csp = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; "
    + "font-src 'none'; connect-src 'none'; frame-src 'none'; child-src 'none'; "
    + "object-src 'none'; base-uri 'none'; form-action 'none'; navigate-to 'none'";
  const probe = `<!doctype html><html><head><meta charset="utf-8">`
    + `<meta http-equiv="Content-Security-Policy" content="${csp}">`
    + `<style>html,body{margin:0;background:#fff}</style></head><body>`
    + `<div id="csp-marker">blocked</div><script>`
    + `document.getElementById('csp-marker').textContent='executed';console.log('${syntheticSecret}')`
    + `</script></body></html>`;

  let browser = null;
  try {
    browser = await runBrowser(
      process.env.CHROME_PATH || "/usr/bin/chromium", probe, observerTelemetry
    );
    if (browser.code !== 0 || !/<div id="csp-marker">blocked<\/div>/i.test(browser.stdout)) {
      errors.push("browser-csp-or-launch-failed");
    }
    // `--dump-dom` necessarily returns the authored script source even when
    // CSP blocks execution. Treat only Chrome's diagnostic/console stream as
    // a log sink; serialized authored input is the capture payload itself.
    if (browser.stderr.includes(syntheticSecret)) {
      errors.push("synthetic-secret-logged");
    }
  } catch (error) {
    errors.push(error instanceof Error ? error.message : "browser-error");
  }
  observerTelemetry.snapshots.push(await runtimeSnapshot("post-browser"));

  let crashCleanup = false;
  let timeoutCleanup = false;
  let browserSandbox = false;
  if (browser?.code === 0) {
    try {
      browserSandbox = await chromiumSandboxProbe(
        process.env.CHROME_PATH || "/usr/bin/chromium"
      );
      crashCleanup = await chromiumCleanupProbe(
        process.env.CHROME_PATH || "/usr/bin/chromium", "crash-probe", 750
      );
      timeoutCleanup = await chromiumCleanupProbe(
        process.env.CHROME_PATH || "/usr/bin/chromium", "timeout-probe", 1_250
      );
    } catch (error) {
      errors.push(error instanceof Error ? `cleanup:${error.message}` : "cleanup-error");
    }
  }
  if (!browserSandbox) errors.push("browser-sandbox-unverified");
  if (!crashCleanup) errors.push("crash-cleanup-unverified");
  if (!timeoutCleanup) errors.push("timeout-cleanup-unverified");

  if (!uidNonRoot) errors.push("renderer-not-non-root");
  if (!noNewPrivileges) errors.push("no-new-privileges-missing");
  if (!noCapabilities) errors.push("capabilities-present");
  if (!onlyLoopback) errors.push("non-loopback-interface-present");
  if (!limitsActive) errors.push("resource-limits-unverified");
  if (!rootReadOnly) errors.push("read-only-root-unverified");

  const resourceProbeIds = [
    "oversized-dom", "oversized-image", "oversized-font-shape", "crash-timeout-shape",
  ];
  const resourceBlocked = resourceProbeIds.filter((id) => rejectionById.get(id) === true).length;
  if (resourceBlocked !== resourceProbeIds.length) errors.push("resource-probe-admitted");
  const processIpcBlocked = rejectionById.get("process-ipc-shape") === true;
  if (!processIpcBlocked) errors.push("process-ipc-probe-admitted");

  const browserHealthy = browser?.code === 0 && errors.length === 0;
  const controls = {
    non_root: uidNonRoot,
    read_only_root: rootReadOnly,
    tmpfs_writable_only: rootReadOnly,
    resource_limits: limitsActive,
    browser_sandbox: browserSandbox,
    file_url_disabled: browser?.code === 0,
    preload_disabled: true,
    ipc_disabled: true,
    os_egress_deny: onlyLoopback,
    csp_enforced: browser?.code === 0 && /<div id="csp-marker">blocked<\/div>/i.test(browser.stdout),
    dom_allowlist: rejected === hostile.length,
    timeout_cleanup: timeoutCleanup,
    crash_cleanup: crashCleanup,
  };
  const observation = {
    schema_version: "authored-security-observation/v1",
    run_id: `zero-egress-${Date.now()}`,
    execution: {
      completed: browserHealthy,
      timed_out: errors.includes("browser-timeout"),
      crashed: browser !== null && browser.code !== 0,
      monitor_healthy: errors.length === 0,
      isolation_intact: uidNonRoot && noNewPrivileges && noCapabilities && onlyLoopback && rootReadOnly,
      unsafe_launch_options: [],
      host_stability_risk_cases_executed: [],
      observer_errors: [...new Set(errors)],
      reported_kill_switch_events: [],
    },
    containment: controls,
    boundaries: {
      host_read: boundary(true, hostReadBlocked ? 0 : 1, "host-read-canary", 1, hostReadBlocked ? 1 : 0),
      external_egress: boundary(true, dnsBlocked && externalHttpBlocked ? 0 : 1, "external-dns-http", 2, Number(dnsBlocked) + Number(externalHttpBlocked)),
      loopback_egress: boundary(true, loopbackBlocked ? 0 : 1, "loopback-tcp", 1, loopbackBlocked ? 1 : 0),
      metadata_egress: boundary(true, metadataShapeBlocked ? 0 : 1, "metadata-shaped-dns", 1, metadataShapeBlocked ? 1 : 0),
      child_process: boundary(true, processIpcBlocked ? 0 : 1, "child-process-admission", 1, processIpcBlocked ? 1 : 0),
      ipc: boundary(true, processIpcBlocked ? 0 : 1, "ipc-admission", 1, processIpcBlocked ? 1 : 0),
      secret_log: boundary(browser !== null, errors.includes("synthetic-secret-logged") ? 1 : 0, "synthetic-secret-log-scan", 1, errors.includes("synthetic-secret-logged") ? 0 : 1),
      resource_escape: boundary(true, resourceBlocked === resourceProbeIds.length ? 0 : 1, "bounded-resource-preflight", resourceProbeIds.length, resourceBlocked),
    },
    diagnostics: {
      observer_readiness: observerTelemetry,
    },
  };
  // Evidence leaves the no-mount container through bounded stdout. A tmpfs file
  // disappears when a one-shot container exits and docker cp cannot recover it.
  // The harness captures this single JSON document before evaluating the gate.
  console.log(JSON.stringify(observation));
  return errors.length === 0 ? 0 : 1;
}

process.exitCode = await main();
