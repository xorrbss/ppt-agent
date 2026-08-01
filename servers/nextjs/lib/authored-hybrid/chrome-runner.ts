import { spawn, type ChildProcess } from "node:child_process";
import fs from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { AUTHORED_HYBRID_COMPLETION_MARKER_NAME } from "./browser-source.ts";
import { resolveAuthoredHybridChromeExecutable } from "./chrome-runtime-discovery.ts";

export { resolveAuthoredHybridChromeExecutable } from "./chrome-runtime-discovery.ts";

const DEFAULT_TIMEOUT_MS = 20_000;
const MAX_CAPTURE_OUTPUT_BYTES = 64 * 1024 * 1024;
const COMPLETE_PNG_SUFFIX = Buffer.from([
  0, 0, 0, 0, 73, 69, 78, 68, 174, 66, 96, 130,
]);

export interface AuthoredHybridChromeOptions {
  chromeExecutable?: string;
  timeoutMs?: number;
}

interface ChromeRunOptions extends AuthoredHybridChromeOptions {
  html: string;
  dumpDom: boolean;
  screenshot: boolean;
  /** Opaque token emitted by the injected analyser after its result marker. */
  domCompletionToken?: string;
  /** Outer Chrome window size used while calibrating a fixed CSS viewport. */
  windowSizePx?: { width: number; height: number };
}

interface ChromeRunResult {
  serializedDom?: string;
  screenshotPng?: Buffer;
}

interface NetworkDenyProxy {
  url: string;
  close: () => Promise<void>;
}

async function accessible(filePath: string): Promise<boolean> {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}

/**
 * Chrome loads a temporary file, but CSS and responsive-image syntax can still
 * initiate HTTP requests before extraction fails closed. Route every browser
 * request through a local endpoint that immediately drops the connection.
 */
async function startNetworkDenyProxy(): Promise<NetworkDenyProxy> {
  const server = net.createServer((socket) => socket.destroy());
  server.unref();
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      server.off("error", reject);
      resolve();
    });
  });
  const address = server.address();
  if (!address || typeof address === "string") {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    throw new Error("Failed to bind the authored hybrid network deny proxy.");
  }
  return {
    url: `http://127.0.0.1:${address.port}`,
    close: () =>
      new Promise<void>((resolve) => {
        server.close(() => resolve());
      }),
  };
}

const KILL_GRACE_MS = 5_000;
// CIM enumeration can be slow on otherwise healthy Windows hosts. Keep the
// helper bounded without turning normal process inspection into a failure.
const WINDOWS_HELPER_TIMEOUT_MS = 10_000;

function describeError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function waitForChildExit(child: ChildProcess): Promise<void> {
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

async function taskkillProcessTree(pid: number): Promise<void> {
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

async function terminateWindowsProfileProcesses(profilePath: string): Promise<void> {
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

function domCompletionPattern(token: string): RegExp {
  const escapedToken = token.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return new RegExp(
    `<meta\\b(?=[^>]*\\bname\\s*=\\s*["']${AUTHORED_HYBRID_COMPLETION_MARKER_NAME}["'])(?=[^>]*\\bcontent\\s*=\\s*["']${escapedToken}["'])[^>]*>`,
    "i"
  );
}

/** Kill Chrome and resolve only once the process has actually exited, so callers
 * can delete the work directory afterwards. On Windows the detached child holds a
 * lock on `--user-data-dir`; deleting before it exits raises EBUSY and leaks the
 * temp dir. A grace timer guarantees we never hang if `exit` is never reported. */
async function terminateChrome(
  child: ChildProcess,
  profilePath?: string
): Promise<void> {
  if (process.platform === "win32") {
    let launcherTerminationError: unknown;
    if (child.pid && child.exitCode === null && child.signalCode === null) {
      try {
        await taskkillProcessTree(child.pid);
      } catch (error) {
        // chrome.exe can hand off to another process or exit between the state
        // check and taskkill. Keep the error until the unique-profile cleanup
        // below establishes whether a Chrome process actually survived.
        launcherTerminationError = error;
      }
    }

    let profileTerminationError: unknown;
    if (profilePath) {
      try {
        await terminateWindowsProfileProcesses(profilePath);
      } catch (error) {
        profileTerminationError = error;
      }
    }

    await waitForChildExit(child);

    if (child.exitCode === null && child.signalCode === null) {
      try {
        child.kill();
      } catch {
        // The process may have exited after the state check. The final profile
        // scan is authoritative for captures, because every Chrome process in
        // this run receives the unique --user-data-dir argument.
      }
      await waitForChildExit(child);
    }

    if (profilePath) {
      try {
        await terminateWindowsProfileProcesses(profilePath);
        profileTerminationError = undefined;
      } catch (error) {
        profileTerminationError = error;
      }
    }

    const launcherStillRunning =
      !profilePath && child.exitCode === null && child.signalCode === null;
    if (profileTerminationError !== undefined || launcherStillRunning) {
      const errors: unknown[] = [];
      if (launcherTerminationError !== undefined) {
        errors.push(launcherTerminationError);
      }
      if (profileTerminationError !== undefined) {
        errors.push(profileTerminationError);
      }
      if (launcherStillRunning) {
        errors.push(new Error("Windows Chrome launcher is still running."));
      }
      throw new AggregateError(
        errors,
        "Windows Chrome could not be fully terminated."
      );
    }
    return;
  }
  if (child.pid && child.exitCode === null && child.signalCode === null) {
    child.kill("SIGKILL");
  }
  await waitForChildExit(child);
}

function appendBounded(
  chunks: Buffer[],
  chunk: Buffer,
  currentBytes: number
): number {
  const nextBytes = currentBytes + chunk.length;
  if (nextBytes > MAX_CAPTURE_OUTPUT_BYTES) {
    throw new Error(
      `Chrome authored hybrid output exceeded ${MAX_CAPTURE_OUTPUT_BYTES} bytes.`
    );
  }
  chunks.push(chunk);
  return nextBytes;
}

async function spawnChrome(
  executable: string,
  args: string[],
  timeoutMs: number,
  completedScreenshotPath?: string,
  expectDomOutput = false,
  completionToken?: string,
  profilePath?: string
): Promise<{ stdout: Buffer; stderr: Buffer }> {
  return new Promise((resolve, reject) => {
    const child = spawn(executable, args, {
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    const stdout: Buffer[] = [];
    const stderr: Buffer[] = [];
    let stdoutBytes = 0;
    let stderrBytes = 0;
    let settled = false;
    let screenshotPollInFlight = false;
    let screenshotPoll: ReturnType<typeof setInterval> | undefined;
    let timer: ReturnType<typeof setTimeout>;
    const completionPattern = completionToken
      ? domCompletionPattern(completionToken)
      : undefined;
    let domOutputComplete = !expectDomOutput;
    let screenshotOutputComplete = !completedScreenshotPath;
    let stdoutTail = "";

    const finish = (callback: () => void) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      if (screenshotPoll) clearInterval(screenshotPoll);
      callback();
    };
    const afterTermination = (
      onClean: () => void,
      primaryError?: unknown
    ) => {
      void terminateChrome(child, profilePath).then(
        () => {
          if (primaryError !== undefined) {
            reject(primaryError);
            return;
          }
          onClean();
        },
        (cleanupError) => {
          reject(
            primaryError === undefined
              ? cleanupError
              : new AggregateError(
                  [primaryError, cleanupError],
                  `Chrome capture failed and Windows cleanup also failed: ${describeError(primaryError)}`
                )
          );
        }
      );
    };
    const harvestCompletedOutput = () => {
      if (!domOutputComplete || !screenshotOutputComplete) return;
      finish(() => {
        // Windows Chrome may retain a browser child after it has written the
        // requested artifacts. Those artifacts are the complete capture
        // contract, so do not wait for an unrelated background process to exit.
        afterTermination(() =>
          resolve({ stdout: Buffer.concat(stdout), stderr: Buffer.concat(stderr) })
        );
      });
    };
    timer = setTimeout(() => {
      finish(() => {
        afterTermination(
          () => {},
          new Error(`Chrome authored hybrid capture timed out after ${timeoutMs}ms.`)
        );
      });
    }, timeoutMs);
    if (completedScreenshotPath) {
      // Chrome's macOS unified-headless process can leave the requested PNG
      // complete on disk but keep the browser process alive indefinitely. IEND
      // is the definitive end of a PNG stream, so harvest that completed output
      // and terminate the otherwise-idle process instead of waiting for the
      // outer watchdog. When a DOM dump is also requested, wait for both
      // complete artifacts before returning; this keeps the extraction
      // fail-closed while avoiding Windows launcher-process hangs.
      screenshotPoll = setInterval(() => {
        if (screenshotPollInFlight || settled) return;
        screenshotPollInFlight = true;
        void fs
          .readFile(completedScreenshotPath)
          .then((png) => {
            if (
              png.length >= 24 &&
              png.subarray(-COMPLETE_PNG_SUFFIX.length).equals(COMPLETE_PNG_SUFFIX)
            ) {
              screenshotOutputComplete = true;
              harvestCompletedOutput();
            }
          })
          .catch(() => {})
          .finally(() => {
            screenshotPollInFlight = false;
          });
      }, 100);
      screenshotPoll.unref?.();
    }

    child.stdout?.on("data", (chunk: Buffer) => {
      try {
        stdoutBytes = appendBounded(stdout, chunk, stdoutBytes);
        // Only the analyser appends this per-capture marker, and it does so
        // after writing its observation. A user-authored `</html>` is not an
        // output boundary: it may occur anywhere in a streamed DOM dump.
        stdoutTail = `${stdoutTail}${chunk.toString("utf8")}`.slice(-1024);
        if (expectDomOutput && completionPattern?.test(stdoutTail)) {
          domOutputComplete = true;
          harvestCompletedOutput();
        }
      } catch (error) {
        finish(() => {
          afterTermination(() => {}, error);
        });
      }
    });
    child.stderr?.on("data", (chunk: Buffer) => {
      try {
        stderrBytes = appendBounded(stderr, chunk, stderrBytes);
      } catch (error) {
        finish(() => {
          afterTermination(() => {}, error);
        });
      }
    });
    child.once("error", (error) =>
      finish(() => {
        afterTermination(() => {}, error);
      })
    );
    child.once("close", (code, signal) => {
      if (code === 0) {
        // On Windows chrome.exe can act as a launcher: the process we spawned
        // exits successfully before its browser child has flushed the requested
        // PNG. Keep the existing bounded IEND poll alive for screenshot-only
        // captures instead of deleting the work directory under that child.
        if (
          completedScreenshotPath ||
          (expectDomOutput && completionPattern && !domOutputComplete)
        ) return;
        finish(() => {
          afterTermination(() =>
            resolve({ stdout: Buffer.concat(stdout), stderr: Buffer.concat(stderr) })
          );
        });
        return;
      }
      const stderrText = Buffer.concat(stderr).toString("utf8").trim();
      finish(() =>
        afterTermination(
          () => {},
          new Error(
            `Chrome authored hybrid capture exited with code ${code ?? "unknown"}${signal ? ` (${signal})` : ""}${stderrText ? `: ${stderrText}` : ""}`
          )
        )
      );
    });
  });
}

export async function runAuthoredHybridChrome(
  options: ChromeRunOptions
): Promise<ChromeRunResult> {
  const executable =
    options.chromeExecutable ??
    (await resolveAuthoredHybridChromeExecutable());
  if (!executable) {
    throw new Error(
      "Chrome/Chromium was not found for authored hybrid extraction. Set AUTHORED_HYBRID_CHROME_PATH, PUPPETEER_EXECUTABLE_PATH, or CHROME_PATH."
    );
  }
  if (!(await accessible(executable))) {
    throw new Error(`Configured authored hybrid Chrome is not accessible: ${executable}`);
  }

  const baseTempDirectory =
    process.env.TEMP_DIRECTORY?.trim() || path.join(os.tmpdir(), "presenton");
  await fs.mkdir(baseTempDirectory, { recursive: true });
  const workDirectory = await fs.mkdtemp(
    path.join(baseTempDirectory, "authored-hybrid-")
  );
  const htmlPath = path.join(workDirectory, "slide.html");
  const screenshotPath = path.join(workDirectory, "backplate.png");
  const profilePath = path.join(workDirectory, "chrome-profile");
  let networkDenyProxy: NetworkDenyProxy | undefined;
  let result: ChromeRunResult | undefined;
  let captureError: unknown;

  try {
    networkDenyProxy = await startNetworkDenyProxy();
    await fs.writeFile(htmlPath, options.html, "utf8");
    const windowSize = options.windowSizePx ?? { width: 1280, height: 720 };
    const args = [
      "--headless=new",
      "--disable-gpu",
      "--disable-dev-shm-usage",
      "--hide-scrollbars",
      "--no-first-run",
      "--no-default-browser-check",
      "--disable-background-networking",
      "--no-sandbox",
      "--allow-file-access-from-files",
      `--proxy-server=${networkDenyProxy.url}`,
      "--proxy-bypass-list=<-loopback>",
      "--force-device-scale-factor=1",
      `--window-size=${windowSize.width},${windowSize.height}`,
      "--run-all-compositor-stages-before-draw",
      "--virtual-time-budget=8000",
      "--default-background-color=00000000",
      `--user-data-dir=${profilePath}`,
    ];
    if (options.dumpDom) args.push("--dump-dom");
    if (options.screenshot) args.push(`--screenshot=${screenshotPath}`);
    args.push(pathToFileURL(htmlPath).href);

    const completed = await spawnChrome(
      executable,
      args,
      options.timeoutMs ?? DEFAULT_TIMEOUT_MS,
      options.screenshot ? screenshotPath : undefined,
      options.dumpDom,
      options.domCompletionToken,
      profilePath
    );
    result = {};
    if (options.dumpDom) result.serializedDom = completed.stdout.toString("utf8");
    if (options.screenshot) {
      try {
        result.screenshotPng = await fs.readFile(screenshotPath);
      } catch (error) {
        throw new Error(
          "Chrome completed but did not create the authored hybrid backplate PNG.",
          { cause: error }
        );
      }
    }
  } catch (error) {
    captureError = error;
  }

  // Preserve the original capture failure as the first aggregate member. A
  // cleanup problem remains observable, but must not erase the reason the
  // caller's Chrome capture originally failed.
  let cleanupError: unknown;
  try {
    await networkDenyProxy?.close().catch(() => {});
    await fs
      .rm(workDirectory, {
        recursive: true,
        force: true,
        // Windows can retain chrome_debug.log briefly after taskkill reports
        // success. Node retries only transient filesystem errors here.
        maxRetries: process.platform === "win32" ? 5 : 0,
        retryDelay: 100,
      });
  } catch (error) {
    cleanupError = error;
  }
  if (captureError !== undefined) {
    if (cleanupError !== undefined) {
      throw new AggregateError(
        [captureError, cleanupError],
        `Chrome capture failed and its work-directory cleanup also failed: ${describeError(captureError)}`
      );
    }
    throw captureError;
  }
  if (cleanupError !== undefined) throw cleanupError;
  if (!result) {
    throw new Error("Chrome capture completed without a result.");
  }
  return result;
}
