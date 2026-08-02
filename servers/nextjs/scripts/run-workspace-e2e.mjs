#!/usr/bin/env node

import { spawn } from "node:child_process";
import { readFile, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";

const LOOPBACK = "127.0.0.1";
const STARTUP_TIMEOUT_MS = 120_000;
// The first request to the presentation route causes Next dev/Turbopack to
// compile the largest route in this suite.  A favicon response proves only
// that the listener is open; without warming the route, Cypress can spend its
// page-load budget waiting on that cold compilation.  Keep the warm-up bounded
// by the same startup deadline so a real compile failure still fails clearly.
const ROUTE_WARMUP_TIMEOUT_MS = STARTUP_TIMEOUT_MS;
const POLL_INTERVAL_MS = 250;
const PROCESS_STOP_TIMEOUT_MS = 5_000;
const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const appDirectory = path.resolve(scriptDirectory, "..");
const fastApiDirectory = path.resolve(appDirectory, "..", "fastapi");
const cacheRoot = path.join(appDirectory, ".next-workspace-e2e");
const nextCli = path.join(appDirectory, "node_modules", "next", "dist", "bin", "next");
const cypressCli = path.join(appDirectory, "node_modules", "cypress", "bin", "cypress");
const nextGeneratedProjectFiles = [
  path.join(appDirectory, "tsconfig.json"),
  path.join(appDirectory, "next-env.d.ts"),
];

let nextProcess;
let liveBackendProcess;
let cypressProcess;
let runDirectory;
let cleanupPromise;
let receivedSignal;
let projectFileSnapshots;

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function throwIfInterrupted() {
  if (receivedSignal) {
    throw new Error(`Workspace E2E run interrupted by ${receivedSignal}.`);
  }
}

function reserveEphemeralPort() {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen({ host: LOOPBACK, port: 0, exclusive: true }, () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close();
        reject(new Error("Could not allocate an isolated loopback port."));
        return;
      }
      server.close((error) => {
        if (error) reject(error);
        else resolve(address.port);
      });
    });
  });
}

function spawnNode(script, args, env) {
  return spawn(process.execPath, [script, ...args], {
    cwd: appDirectory,
    env,
    stdio: "inherit",
    windowsHide: true,
    detached: process.platform !== "win32",
  });
}

function spawnCommand(command, args, { cwd, env }) {
  return spawn(command, args, {
    cwd,
    env,
    stdio: "inherit",
    windowsHide: true,
    detached: process.platform !== "win32",
  });
}

function waitForExit(child) {
  if (child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve({ code: child.exitCode, signal: child.signalCode });
  }
  return new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("exit", (code, signal) => resolve({ code, signal }));
  });
}

async function waitForHttp(
  url,
  child,
  name,
  readinessPath,
  { timeoutMs = STARTUP_TIMEOUT_MS, requestTimeoutMs = 2_000 } = {},
) {
  const deadline = Date.now() + timeoutMs;
  const earlyExit = waitForExit(child).then(({ code, signal }) => {
    throw new Error(`${name} exited before becoming ready (code=${code}, signal=${signal}).`);
  });

  const poll = async () => {
    while (Date.now() < deadline) {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), requestTimeoutMs);
      try {
        const response = await fetch(`${url}${readinessPath}`, {
          redirect: "manual",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`${name} readiness returned ${response.status}.`);
        await response.body?.cancel();
        return;
      } catch {
        await delay(POLL_INTERVAL_MS);
      } finally {
        clearTimeout(timeout);
      }
    }
    throw new Error(`${name} did not become ready within ${timeoutMs / 1_000} seconds.`);
  };

  await Promise.race([poll(), earlyExit]);
}

async function terminateProcessTree(child) {
  if (!child || !child.pid || child.exitCode !== null || child.signalCode !== null) return;

  if (process.platform === "win32") {
    const killer = spawn("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], {
      stdio: "ignore",
      windowsHide: true,
    });
    await waitForExit(killer).catch(() => undefined);
  } else {
    try {
      process.kill(-child.pid, "SIGTERM");
    } catch (error) {
      if (error?.code !== "ESRCH") throw error;
    }
  }

  await Promise.race([waitForExit(child).catch(() => undefined), delay(PROCESS_STOP_TIMEOUT_MS)]);

  if (process.platform !== "win32" && child.exitCode === null && child.signalCode === null) {
    try {
      process.kill(-child.pid, "SIGKILL");
    } catch (error) {
      if (error?.code !== "ESRCH") throw error;
    }
  }
}

async function snapshotProjectFiles() {
  return Promise.all(
    nextGeneratedProjectFiles.map(async (file) => ({ file, contents: await readFile(file) })),
  );
}

async function restoreProjectFiles() {
  if (!projectFileSnapshots) return;
  for (const snapshot of projectFileSnapshots) {
    const current = await readFile(snapshot.file);
    if (!current.equals(snapshot.contents)) {
      await writeFile(snapshot.file, snapshot.contents);
    }
  }
}

function validateRunDirectory(target) {
  const relative = path.relative(cacheRoot, target);
  const targetName = path.basename(target);
  if (!relative || relative.startsWith("..") || path.isAbsolute(relative) || !targetName.startsWith("run-")) {
    throw new Error(`Refusing to remove unexpected E2E cache path: ${target}`);
  }
}

async function cleanup() {
  if (cleanupPromise) return cleanupPromise;
  cleanupPromise = (async () => {
    const errors = [];
    for (const action of [
      () => terminateProcessTree(cypressProcess),
      () => terminateProcessTree(liveBackendProcess),
      () => terminateProcessTree(nextProcess),
      () => restoreProjectFiles(),
      async () => {
        if (!runDirectory) return;
        validateRunDirectory(runDirectory);
        await rm(runDirectory, { recursive: true, force: true, maxRetries: 3, retryDelay: 200 });
      },
    ]) {
      try {
        await action();
      } catch (error) {
        errors.push(error);
      }
    }
    if (errors.length > 0) {
      throw new AggregateError(errors, "Workspace E2E cleanup did not complete cleanly.");
    }
    console.log("[workspace-e2e] Child processes stopped and run-specific cache removed.");
  })();
  return cleanupPromise;
}

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.once(signal, () => {
    receivedSignal = signal;
    console.error(`\n[workspace-e2e] Received ${signal}; stopping child processes.`);
    void cleanup()
      .catch((error) => console.error(`[workspace-e2e] ${error.stack || error.message || error}`))
      .finally(() => {
        process.exitCode = signal === "SIGINT" ? 130 : 143;
      });
  });
}

async function main() {
  const port = await reserveEphemeralPort();
  const liveBackendPort = await reserveEphemeralPort();
  const baseUrl = `http://${LOOPBACK}:${port}`;
  runDirectory = path.join(cacheRoot, `run-${process.pid}-${port}`);
  const relativeRunDirectory = path.relative(appDirectory, runDirectory).split(path.sep).join("/");
  // Next dev rewrites these generated TypeScript entry points for a custom
  // distDir. Restore their exact pre-run bytes after the disposable app stops.
  projectFileSnapshots = await snapshotProjectFiles();
  throwIfInterrupted();
  const childEnvironment = {
    ...process.env,
    NEXT_TELEMETRY_DISABLED: "1",
    NEXT_PUBLIC_CHAT_FIRST_WORKBENCH_ENABLED: "true",
    DISABLE_AUTH: "true",
    CAN_CHANGE_KEYS: "false",
    DISABLE_ANONYMOUS_TRACKING: "true",
    FAST_API_INTERNAL_URL: "http://127.0.0.1:1",
    NEXT_PUBLIC_FAST_API: "",
    PRESENTON_TEST_NEXT_DIST_DIR: relativeRunDirectory,
  };

  console.log(`[workspace-e2e] Starting an isolated Next app at ${baseUrl}.`);
  console.log("[workspace-e2e] Route suite uses Cypress HTTP fixtures; live suite starts FastAPI+SQLite separately.");

  try {
    nextProcess = spawnNode(nextCli, ["dev", "--hostname", LOOPBACK, "--port", String(port)], childEnvironment);
    await waitForHttp(baseUrl, nextProcess, "Next", "/favicon.ico");
    await waitForHttp(
      baseUrl,
      nextProcess,
      "Next presentation route",
      "/presentation?id=route-workspace-deck",
      {
        timeoutMs: ROUTE_WARMUP_TIMEOUT_MS,
        // Do not abort the request that is doing the cold route compilation.
        // An abort/retry loop makes this slower and leaves Cypress racing the
        // compiler instead of receiving a fully rendered route.
        requestTimeoutMs: ROUTE_WARMUP_TIMEOUT_MS,
      },
    );
    throwIfInterrupted();
    console.log("[workspace-e2e] Next presentation route is warm; running the route spec.");

    cypressProcess = spawnNode(
      cypressCli,
      ["run", "--e2e", "--spec", "cypress/e2e/workspaceChatFirstRoute.cy.ts"],
      { ...childEnvironment, CYPRESS_BASE_URL: baseUrl },
    );
    const result = await waitForExit(cypressProcess);
    if (result.code !== 0) {
      const error = new Error(`Cypress exited with code ${result.code} (signal=${result.signal}).`);
      error.exitCode = result.code || 1;
      throw error;
    }

    throwIfInterrupted();
    const liveBackendUrl = `http://${LOOPBACK}:${liveBackendPort}`;
    console.log(`[workspace-e2e] Starting the live FastAPI+SQLite runner harness at ${liveBackendUrl}.`);
    liveBackendProcess = spawnCommand(
      process.platform === "win32" ? "uv.exe" : "uv",
      [
        "run",
        "python",
        "tests/support/workspace_browser_live_harness.py",
        "--host",
        LOOPBACK,
        "--port",
        String(liveBackendPort),
      ],
      {
        cwd: fastApiDirectory,
        env: { ...childEnvironment, PYTHONUNBUFFERED: "1" },
      },
    );
    await waitForHttp(liveBackendUrl, liveBackendProcess, "FastAPI live harness", "/__test__/health");
    throwIfInterrupted();
    console.log("[workspace-e2e] Live harness is ready; running the component-to-backend suite.");

    cypressProcess = spawnNode(
      cypressCli,
      [
        "run",
        "--component",
        "--spec",
        "cypress/component/WorkspaceChatPanelLiveBackend.cy.tsx",
        "--env",
        `WORKSPACE_LIVE_BACKEND=${liveBackendUrl}`,
      ],
      childEnvironment,
    );
    const liveResult = await waitForExit(cypressProcess);
    if (liveResult.code !== 0) {
      const error = new Error(
        `Live Cypress suite exited with code ${liveResult.code} (signal=${liveResult.signal}).`,
      );
      error.exitCode = liveResult.code || 1;
      throw error;
    }
  } finally {
    await cleanup();
  }
}

main().catch((error) => {
  if (!receivedSignal) {
    console.error(`[workspace-e2e] ${error.stack || error.message || error}`);
    process.exitCode = Number.isInteger(error.exitCode) ? error.exitCode : 1;
  }
});
