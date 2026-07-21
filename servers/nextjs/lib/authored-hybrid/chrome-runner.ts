import { spawn, type ChildProcess } from "node:child_process";
import fs from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const DEFAULT_TIMEOUT_MS = 20_000;
const MAX_CAPTURE_OUTPUT_BYTES = 64 * 1024 * 1024;

export interface AuthoredHybridChromeOptions {
  chromeExecutable?: string;
  timeoutMs?: number;
}

interface ChromeRunOptions extends AuthoredHybridChromeOptions {
  html: string;
  dumpDom: boolean;
  screenshot: boolean;
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

async function accessible(filePath: string): Promise<boolean> {
  try {
    await fs.access(filePath);
    return true;
  } catch {
    return false;
  }
}

/** First-party Chrome resolution, intentionally independent of generated export code. */
export async function resolveAuthoredHybridChromeExecutable(): Promise<
  string | undefined
> {
  const fromEnv =
    process.env.AUTHORED_HYBRID_CHROME_PATH?.trim() ||
    process.env.PUPPETEER_EXECUTABLE_PATH?.trim() ||
    process.env.CHROME_PATH?.trim();
  if (fromEnv) return fromEnv;

  const candidates: string[] = [];
  if (process.platform === "win32") {
    const programFiles = process.env.ProgramFiles || "C:\\Program Files";
    const programFilesX86 =
      process.env["ProgramFiles(x86)"] || "C:\\Program Files (x86)";
    const localAppData = process.env.LOCALAPPDATA;
    candidates.push(
      path.join(programFiles, "Google/Chrome/Application/chrome.exe"),
      path.join(programFilesX86, "Google/Chrome/Application/chrome.exe")
    );
    if (localAppData) {
      candidates.push(
        path.join(localAppData, "Google/Chrome/Application/chrome.exe")
      );
    }
    candidates.push(
      path.join(programFilesX86, "Microsoft/Edge/Application/msedge.exe"),
      path.join(programFiles, "Microsoft/Edge/Application/msedge.exe")
    );
  } else if (process.platform === "darwin") {
    candidates.push(
      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
      "/Applications/Chromium.app/Contents/MacOS/Chromium",
      "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
    );
  } else {
    candidates.push(
      "/usr/bin/google-chrome",
      "/usr/bin/google-chrome-stable",
      "/usr/bin/chromium",
      "/usr/bin/chromium-browser"
    );
  }

  for (const candidate of candidates) {
    if (await accessible(candidate)) return candidate;
  }
  return undefined;
}

const KILL_GRACE_MS = 5_000;

/** Kill Chrome and resolve only once the process has actually exited, so callers
 * can delete the work directory afterwards. On Windows the detached child holds a
 * lock on `--user-data-dir`; deleting before it exits raises EBUSY and leaks the
 * temp dir. A grace timer guarantees we never hang if `exit` is never reported. */
function terminateChrome(child: ChildProcess): Promise<void> {
  return new Promise((resolve) => {
    if (!child.pid || child.exitCode !== null || child.signalCode !== null) {
      resolve();
      return;
    }
    let done = false;
    let grace: ReturnType<typeof setTimeout>;
    const settle = () => {
      if (done) return;
      done = true;
      clearTimeout(grace);
      resolve();
    };
    child.once("exit", settle);
    grace = setTimeout(settle, KILL_GRACE_MS);
    grace.unref?.();
    if (process.platform === "win32") {
      const killer = spawn(
        "taskkill",
        ["/pid", String(child.pid), "/T", "/F"],
        { stdio: "ignore", windowsHide: true }
      );
      killer.unref();
    } else {
      child.kill("SIGKILL");
    }
  });
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
  timeoutMs: number
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

    const finish = (callback: () => void) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      callback();
    };
    const timer = setTimeout(() => {
      finish(() => {
        void terminateChrome(child).then(() =>
          reject(
            new Error(
              `Chrome authored hybrid capture timed out after ${timeoutMs}ms.`
            )
          )
        );
      });
    }, timeoutMs);

    child.stdout?.on("data", (chunk: Buffer) => {
      try {
        stdoutBytes = appendBounded(stdout, chunk, stdoutBytes);
      } catch (error) {
        finish(() => {
          void terminateChrome(child).then(() => reject(error));
        });
      }
    });
    child.stderr?.on("data", (chunk: Buffer) => {
      try {
        stderrBytes = appendBounded(stderr, chunk, stderrBytes);
      } catch (error) {
        finish(() => {
          void terminateChrome(child).then(() => reject(error));
        });
      }
    });
    child.once("error", (error) => finish(() => reject(error)));
    child.once("close", (code, signal) => {
      if (code === 0) {
        finish(() =>
          resolve({ stdout: Buffer.concat(stdout), stderr: Buffer.concat(stderr) })
        );
        return;
      }
      const stderrText = Buffer.concat(stderr).toString("utf8").trim();
      finish(() =>
        reject(
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
      options.timeoutMs ?? DEFAULT_TIMEOUT_MS
    );
    const result: ChromeRunResult = {};
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
    return result;
  } finally {
    await networkDenyProxy?.close().catch(() => {});
    await fs.rm(workDirectory, { recursive: true, force: true }).catch(() => {});
  }
}
