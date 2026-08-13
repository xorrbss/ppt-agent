/** Process discovery and teardown helpers for isolated Chrome captures. */

import { spawn, type ChildProcess } from "node:child_process";
import fs from "node:fs/promises";

const KILL_GRACE_MS = 5_000;
// CIM enumeration can be slow on otherwise healthy Windows hosts. Keep the
// helper bounded without turning normal process inspection into a failure.
const WINDOWS_HELPER_TIMEOUT_MS = 10_000;

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

async function windowsProcessesUsingProfile(profilePath: string): Promise<number[]> {
  const output = await runWindowsHelper(
    "powershell.exe",
    [
      "-NoProfile",
      "-NonInteractive",
      "-Command",
      "$target=[Environment]::GetEnvironmentVariable('PRESENTON_AUTHORED_HYBRID_PROFILE');$pattern='(?i)(?:^|\\s)(?:\"--user-data-dir=([^\"]*)\"|''--user-data-dir=([^'']*)''|--user-data-dir=(?:\"([^\"]*)\"|''([^'']*)''|([^\\s\"'']+)))';Get-CimInstance Win32_Process | Where-Object { $line=[string]$_.CommandLine;$values=@([regex]::Matches($line,$pattern) | ForEach-Object { if ($_.Groups[1].Success) { $_.Groups[1].Value } elseif ($_.Groups[2].Success) { $_.Groups[2].Value } elseif ($_.Groups[3].Success) { $_.Groups[3].Value } elseif ($_.Groups[4].Success) { $_.Groups[4].Value } else { $_.Groups[5].Value } });@($values | Where-Object { $_ -ceq $target }).Count -gt 0 } | ForEach-Object { $_.ProcessId }",
    ],
    {
      env: {
        ...process.env,
        PRESENTON_AUTHORED_HYBRID_PROFILE: profilePath,
      },
    }
  );
  return [...new Set(
    output
      .toString("utf8")
      .split(/\s+/)
      .map((value) => Number(value))
      .filter((value) => Number.isSafeInteger(value) && value > 0)
  )];
}

export async function terminateWindowsProfileProcesses(profilePath: string): Promise<void> {
  // chrome.exe may hand off to a browser process and exit before Node sees its
  // `close` event. The per-capture profile is unique, so it is a safe process
  // identity even after the original launcher PID no longer exists.
  const helperErrors: unknown[] = [];
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const pids = await windowsProcessesUsingProfile(profilePath);
    if (pids.length === 0) break;
    const terminations = await Promise.allSettled(
      pids.map((pid) => taskkillProcessTree(pid))
    );
    helperErrors.push(
      ...terminations
        .filter((result) => result.status === "rejected")
        .map((result) => (result as PromiseRejectedResult).reason)
    );
    await delay(100);
  }
  const remaining = await windowsProcessesUsingProfile(profilePath);
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
  const remaining = await windowsProcessesUsingProfile(profilePath);
  if (remaining.length > 0) {
    throw new Error(
      `Windows Chrome cleanup left profile-associated process IDs: ${remaining.join(", ")}.`
    );
  }
};
