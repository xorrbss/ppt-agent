/** Process discovery and teardown helpers for isolated Chrome captures. */

import { spawn, type ChildProcess } from "node:child_process";
import fs from "node:fs/promises";

import { traceEvent } from "./capture-trace.ts";

import {
  WindowsCleanupCoordinator,
  parseWindowsProfileScan,
  type WindowsCleanupTelemetry,
  type WindowsProfileProcess,
} from "./windows-cleanup-coordinator.ts";

const KILL_GRACE_MS = 5_000;
// CIM enumeration can be slow on otherwise healthy Windows hosts. Keep the
// helper bounded without turning normal process inspection into a failure.
//
// This budget is only honest while it is spent on a helper that is running.
// It used to be spent on contention instead: every capture ran its own
// process-table scan, twelve captures run at once, and the resulting storm
// timed out helpers that had barely started. The coordinator below makes the
// scan shared and queues the helpers, which is why this stays at ten seconds
// rather than being widened to cover a self-inflicted stall.
const WINDOWS_HELPER_TIMEOUT_MS = 10_000;
/** Helpers allowed to run at once across every concurrent teardown. */
const WINDOWS_HELPER_CONCURRENCY = 4;
/** How long one completed scan may answer teardowns that accept its start. */
const WINDOWS_PROFILE_SCAN_CACHE_MS = 500;
/** Kill attempts before teardown reports the processes it could not remove. */
const WINDOWS_PROFILE_KILL_ATTEMPTS = 4;

export async function accessible(filePath: string): Promise<boolean> {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}

export function describeError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

export function waitForChildExit(child: ChildProcess): Promise<void> {
  if (!child.pid || child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    let done = false;
    const settle = () => {
      if (done) return;
      done = true;
      clearTimeout(grace);
      resolve();
    };
    child.once("exit", settle);
    const grace = setTimeout(settle, KILL_GRACE_MS);
    grace.unref?.();
  });
}

async function runWindowsHelper(
  command: string,
  args: string[],
  options: { env?: NodeJS.ProcessEnv; timeoutMs?: number } = {}
): Promise<Buffer> {
  // The slot is taken before the child is spawned, so the timeout below starts
  // when this helper starts rather than while it waits behind other helpers.
  const queuedAt = performance.now();
  return cleanupCoordinator.runHelper(async () => {
    const startedAt = performance.now();
    try {
      return await spawnWindowsHelper(command, args, options);
    } finally {
      traceEvent("helper", {
        command,
        queueMs: Math.round(startedAt - queuedAt),
        ms: Math.round(performance.now() - startedAt),
      });
    }
  });
}

function spawnWindowsHelper(
  command: string,
  args: string[],
  options: { env?: NodeJS.ProcessEnv; timeoutMs?: number }
): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
      env: options.env,
    });
    const stdout: Buffer[] = [];
    const stderr: Buffer[] = [];
    const timeoutMs = options.timeoutMs ?? WINDOWS_HELPER_TIMEOUT_MS;
    let settled = false;
    const settle = (callback: () => void) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      callback();
    };
    const timer = setTimeout(() => {
      // These helpers are direct children, not Chrome. Terminate only this
      // helper before failing the capture rather than leaving a hung cleanup
      // command to block the profile removal indefinitely.
      child.kill();
      settle(() =>
        reject(
          new Error(
            `Windows cleanup helper ${command} timed out after ${timeoutMs}ms.`
          )
        )
      );
    }, timeoutMs);
    child.stdout?.on("data", (chunk: Buffer) => stdout.push(chunk));
    child.stderr?.on("data", (chunk: Buffer) => stderr.push(chunk));
    child.once("error", (error) =>
      settle(() =>
        reject(new Error(`Windows cleanup helper ${command} failed to start.`, { cause: error }))
      )
    );
    child.once("close", (code, signal) =>
      settle(() => {
        if (code === 0) {
          resolve(Buffer.concat(stdout));
          return;
        }
        const stderrText = Buffer.concat(stderr).toString("utf8").trim();
        reject(
          new Error(
            `Windows cleanup helper ${command} exited with code ${code ?? "unknown"}${signal ? ` (${signal})` : ""}${stderrText ? `: ${stderrText}` : ""}`
          )
        );
      })
    );
  });
}

export async function taskkillProcessTree(pid: number): Promise<void> {
  await runWindowsHelper("taskkill", ["/pid", String(pid), "/T", "/F"]);
}

// One scan for every profile, filtered in-process. The query it replaces took
// the target profile through an environment variable and returned only the
// matching process IDs, which made each concurrent teardown need a scan of its
// own. Emitting `<pid>\t<user-data-dir>` for every occurrence lets one scan
// answer them all; the match pattern itself is unchanged.
const WINDOWS_PROFILE_SCAN_COMMAND =
  "$pattern='(?i)(?:^|\\s)(?:\"--user-data-dir=([^\"]*)\"|''--user-data-dir=([^'']*)''|--user-data-dir=(?:\"([^\"]*)\"|''([^'']*)''|([^\\s\"'']+)))';Get-CimInstance Win32_Process | ForEach-Object { $owner=$_.ProcessId;$line=[string]$_.CommandLine;foreach ($m in [regex]::Matches($line,$pattern)) { if ($m.Groups[1].Success) { $v=$m.Groups[1].Value } elseif ($m.Groups[2].Success) { $v=$m.Groups[2].Value } elseif ($m.Groups[3].Success) { $v=$m.Groups[3].Value } elseif ($m.Groups[4].Success) { $v=$m.Groups[4].Value } else { $v=$m.Groups[5].Value };\"$owner`t$v\" } }";

async function scanWindowsProfileProcesses(): Promise<WindowsProfileProcess[]> {
  const output = await runWindowsHelper("powershell.exe", [
    "-NoProfile",
    "-NonInteractive",
    "-Command",
    WINDOWS_PROFILE_SCAN_COMMAND,
  ]);
  return parseWindowsProfileScan(output.toString("utf8"));
}

const cleanupCoordinator = new WindowsCleanupCoordinator({
  scan: scanWindowsProfileProcesses,
  helperConcurrency: WINDOWS_HELPER_CONCURRENCY,
  cacheTtlMs: WINDOWS_PROFILE_SCAN_CACHE_MS,
});

function windowsProcessesUsingProfile(
  profilePath: string,
  options: { notBefore?: number } = {}
): Promise<number[]> {
  return cleanupCoordinator.profileProcesses(profilePath, options);
}

/** Kernel-free counters for the shared scan and the helper queue. */
export function windowsCleanupTelemetry(): WindowsCleanupTelemetry {
  return cleanupCoordinator.telemetry();
}

export async function terminateWindowsProfileProcesses(profilePath: string): Promise<void> {
  // chrome.exe may hand off to a browser process and exit before Node sees its
  // `close` event. The per-capture profile is unique, so it is a safe process
  // identity even after the original launcher PID no longer exists.
  const helperErrors: unknown[] = [];
  // Every scan this teardown reads must have started after the teardown did,
  // and after each kill wave must post-date those kills. Without that the
  // shared scan could answer with a process table from before the kill and
  // teardown would either re-kill dead IDs or accept a stale all-clear.
  let notBefore = Date.now();
  for (let attempt = 0; attempt < WINDOWS_PROFILE_KILL_ATTEMPTS; attempt += 1) {
    const pids = await windowsProcessesUsingProfile(profilePath, { notBefore });
    if (pids.length === 0) break;
    const terminations = await Promise.allSettled(
      pids.map((pid) => taskkillProcessTree(pid))
    );
    helperErrors.push(
      ...terminations
        .filter((result) => result.status === "rejected")
        .map((result) => (result as PromiseRejectedResult).reason)
    );
    notBefore = Date.now();
    await delay(100);
  }
  const remaining = await windowsProcessesUsingProfile(profilePath, { notBefore });
  if (remaining.length > 0) {
    const residualError = new Error(
      `Windows Chrome cleanup left profile-associated process IDs: ${remaining.join(", ")}.`
    );
    throw new AggregateError(
      [...helperErrors, residualError],
      "Windows Chrome cleanup could not remove every profile-associated process."
    );
  }
}

export const __testOnlyRunWindowsHelper = runWindowsHelper;
export const __testOnlyAssertWindowsProfileGone = async (
  profilePath: string
): Promise<void> => {
  // An assertion about the process table right now must not be answered from a
  // scan taken before the caller looked.
  const remaining = await windowsProcessesUsingProfile(profilePath, {
    notBefore: Date.now(),
  });
  if (remaining.length > 0) {
    throw new Error(
      `Windows Chrome cleanup left profile-associated process IDs: ${remaining.join(", ")}.`
    );
  }
};
