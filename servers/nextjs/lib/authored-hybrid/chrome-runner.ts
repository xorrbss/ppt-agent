import { spawn, type ChildProcess } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import {
  AUTHORED_HYBRID_BROWSER_SOURCE,
  AUTHORED_HYBRID_COMPLETION_MARKER_NAME,
} from "./browser-source.ts";
import {
  accessible,
  delay,
  describeError,
  taskkillProcessTree,
  terminateWindowsProfileProcesses,
  waitForChildExit,
} from "./chrome-process.ts";
import { resolveAuthoredHybridChromeExecutable } from "./chrome-runtime-discovery.ts";
import {
  applyAuthoredRendererCsp,
  authoredRendererChromeArguments,
  authoredRendererEnvironment,
  authoredRendererIsolationPolicy,
} from "./renderer-isolation.ts";
import { preflightAuthoredHtmlForHybrid } from "./security.ts";

export { resolveAuthoredHybridChromeExecutable } from "./chrome-runtime-discovery.ts";
export {
  __testOnlyAssertWindowsProfileGone,
  __testOnlyRunWindowsHelper,
} from "./chrome-process.ts";
const DEFAULT_TIMEOUT_MS = 20_000;
const MAX_CAPTURE_OUTPUT_BYTES = 64 * 1024 * 1024;

export interface AuthoredHybridChromeOptions {
  chromeExecutable?: string;
  timeoutMs?: number;
}

export interface ChromeRunOptions extends AuthoredHybridChromeOptions {
  html: string;
  dumpDom: boolean;
  screenshot: boolean;
  /** Opaque token emitted by the injected analyser after its result marker. */
  domCompletionToken?: string;
  /** Outer Chrome window size used while calibrating a fixed CSS viewport. */
  windowSizePx?: { width: number; height: number };
}

export interface ChromeRunResult {
  serializedDom?: string;
  screenshotPng?: Buffer;
  blockedRequestCount?: number;
}

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

interface CdpMessage {
  id?: number;
  method?: string;
  sessionId?: string;
  params?: Record<string, unknown>;
  result?: Record<string, unknown>;
  error?: { message?: string };
}

interface PendingCdpCommand {
  resolve: (result: Record<string, unknown>) => void;
  reject: (error: Error) => void;
}

class CdpPipe {
  private nextId = 1;
  private input = "";
  private bytes = 0;
  private readonly pending = new Map<number, PendingCdpCommand>();
  private readonly child: ChildProcess;
  readonly blockedRequests: string[] = [];

  constructor(child: ChildProcess) {
    this.child = child;
    const input = child.stdio[4];
    if (!input || typeof input === "number" || !("on" in input)) {
      throw new Error("Chrome did not expose its CDP read pipe.");
    }
    input.on("data", (chunk: Buffer) => this.receive(chunk));
    input.on("error", (error: Error) => this.rejectAll(error));
    child.once("error", (error) => this.rejectAll(error));
    child.once("close", (code, signal) => {
      this.rejectAll(
        new Error(
          `Chrome authored hybrid CDP pipe closed with code ${code ?? "unknown"}${signal ? ` (${signal})` : ""}.`
        )
      );
    });
  }

  private receive(chunk: Buffer): void {
    this.bytes += chunk.length;
    if (this.bytes > MAX_CAPTURE_OUTPUT_BYTES) {
      this.rejectAll(new Error("Chrome authored hybrid CDP output exceeded its byte limit."));
      return;
    }
    this.input += chunk.toString("utf8");
    while (true) {
      const boundary = this.input.indexOf("\0");
      if (boundary < 0) break;
      const raw = this.input.slice(0, boundary);
      this.input = this.input.slice(boundary + 1);
      if (!raw) continue;
      let message: CdpMessage;
      try {
        message = JSON.parse(raw) as CdpMessage;
      } catch (error) {
        this.rejectAll(new Error("Chrome returned malformed CDP JSON.", { cause: error }));
        continue;
      }
      if (message.id !== undefined) {
        const pending = this.pending.get(message.id);
        if (!pending) continue;
        this.pending.delete(message.id);
        if (message.error) pending.reject(new Error(message.error.message ?? "CDP command failed."));
        else pending.resolve(message.result ?? {});
        continue;
      }
      if (message.method === "Fetch.requestPaused" && message.params) {
        const request = message.params.request as { url?: string } | undefined;
        const requestId = message.params.requestId;
        const url = request?.url ?? "unknown";
        if (typeof requestId === "string") {
          if (/^(?:data:|about:blank$)/i.test(url)) {
            void this.send(
              "Fetch.continueRequest",
              { requestId },
              message.sessionId
            ).catch(() => {});
          } else {
            this.blockedRequests.push(url);
            void this.send(
              "Fetch.failRequest",
              { requestId, errorReason: "BlockedByClient" },
              message.sessionId
            ).catch(() => {});
          }
        }
      }
      if (message.method === "Network.requestWillBeSent" && message.params) {
        const request = message.params.request as { url?: string } | undefined;
        const url = request?.url ?? "unknown";
        if (!/^(?:data:|about:blank$)/i.test(url) && !this.blockedRequests.includes(url)) {
          this.blockedRequests.push(url);
        }
      }
    }
  }

  private rejectAll(error: Error): void {
    for (const command of this.pending.values()) command.reject(error);
    this.pending.clear();
  }

  send(
    method: string,
    params: Record<string, unknown> = {},
    sessionId?: string
  ): Promise<Record<string, unknown>> {
    const output = this.child.stdio[3];
    if (!output || typeof output === "number" || !("write" in output)) {
      return Promise.reject(new Error("Chrome did not expose its CDP write pipe."));
    }
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      output.write(`${JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) })}\0`, (error?: Error | null) => {
        if (!error) return;
        this.pending.delete(id);
        reject(error);
      });
    });
  }
}

function stringResult(result: Record<string, unknown>): string | undefined {
  const remote = result.result as { value?: unknown } | undefined;
  return typeof remote?.value === "string" ? remote.value : undefined;
}

async function waitForTrustedCompletion(
  cdp: CdpPipe,
  sessionId: string,
  completionToken?: string
): Promise<void> {
  await cdp.send(
    "Runtime.evaluate",
    {
      expression: "(async()=>{if(document.fonts&&document.fonts.ready)await document.fonts.ready;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));return true})()",
      awaitPromise: true,
      returnByValue: true,
    },
    sessionId
  );
  if (!completionToken) return;
  const selector = `meta[name=${JSON.stringify(AUTHORED_HYBRID_COMPLETION_MARKER_NAME)}][content=${JSON.stringify(completionToken)}]`;
  for (;;) {
    const evaluated = await cdp.send(
      "Runtime.evaluate",
      {
        expression: `Boolean(document.querySelector(${JSON.stringify(selector)}))`,
        returnByValue: true,
      },
      sessionId
    );
    if ((evaluated.result as { value?: unknown } | undefined)?.value === true) return;
    await delay(20);
  }
}

async function captureThroughCdp(
  child: ChildProcess,
  html: string,
  options: ChromeRunOptions
): Promise<ChromeRunResult> {
  const cdp = new CdpPipe(child);
  const context = await cdp.send("Target.createBrowserContext", { disposeOnDetach: true });
  const browserContextId = context.browserContextId;
  if (typeof browserContextId !== "string") throw new Error("Chrome did not create an isolated browser context.");
  const target = await cdp.send("Target.createTarget", { url: "about:blank", browserContextId });
  if (typeof target.targetId !== "string") throw new Error("Chrome did not create an isolated render target.");
  const attachment = await cdp.send("Target.attachToTarget", { targetId: target.targetId, flatten: true });
  const sessionId = attachment.sessionId;
  if (typeof sessionId !== "string") throw new Error("Chrome did not attach the isolated render target.");
  await Promise.all([
    cdp.send("Page.enable", {}, sessionId),
    cdp.send("Runtime.enable", {}, sessionId),
    cdp.send("Network.enable", { maxTotalBufferSize: 0, maxResourceBufferSize: 0 }, sessionId),
    cdp.send("Fetch.enable", { patterns: [{ urlPattern: "*", requestStage: "Request" }] }, sessionId),
    cdp.send("Security.setIgnoreCertificateErrors", { ignore: false }, sessionId),
  ]);
  await cdp.send(
    "Network.setBlockedURLs",
    { urls: ["http://*", "https://*", "file://*", "ftp://*", "ws://*", "wss://*", "blob:*"] },
    sessionId
  );
  const windowSize = options.windowSizePx ?? { width: 1280, height: 720 };
  await cdp.send(
    "Emulation.setDeviceMetricsOverride",
    { width: windowSize.width, height: windowSize.height, deviceScaleFactor: 1, mobile: false },
    sessionId
  );
  await cdp.send(
    "Emulation.setDefaultBackgroundColorOverride",
    { color: { r: 0, g: 0, b: 0, a: 0 } },
    sessionId
  );
  const frames = await cdp.send("Page.getFrameTree", {}, sessionId);
  const frameId = (frames.frameTree as { frame?: { id?: unknown } } | undefined)?.frame?.id;
  if (typeof frameId !== "string") throw new Error("Chrome did not expose the isolated root frame.");
  await cdp.send("Page.setDocumentContent", { frameId, html: applyAuthoredRendererCsp(html) }, sessionId);
  await waitForTrustedCompletion(cdp, sessionId, options.domCompletionToken);
  if (cdp.blockedRequests.length > 0) {
    throw new Error(`Authored renderer blocked ${cdp.blockedRequests.length} network or host request(s).`);
  }
  const result: ChromeRunResult = { blockedRequestCount: 0 };
  if (options.dumpDom) {
    const evaluated = await cdp.send(
      "Runtime.evaluate",
      { expression: "document.documentElement.outerHTML", returnByValue: true },
      sessionId
    );
    result.serializedDom = stringResult(evaluated);
    if (!result.serializedDom) throw new Error("Chrome did not serialize the authored document.");
    if (Buffer.byteLength(result.serializedDom, "utf8") > MAX_CAPTURE_OUTPUT_BYTES) {
      throw new Error("Chrome authored hybrid DOM output exceeded its byte limit.");
    }
    if (options.domCompletionToken && !domCompletionPattern(options.domCompletionToken).test(result.serializedDom)) {
      throw new Error("Chrome serialized the authored document without its trusted completion marker.");
    }
  }
  if (options.screenshot) {
    const capture = await cdp.send(
      "Page.captureScreenshot",
      { format: "png", fromSurface: true, captureBeyondViewport: false },
      sessionId
    );
    if (typeof capture.data !== "string") throw new Error("Chrome did not return the authored screenshot.");
    result.screenshotPng = Buffer.from(capture.data, "base64");
    if (result.screenshotPng.length > MAX_CAPTURE_OUTPUT_BYTES) {
      throw new Error("Chrome authored hybrid screenshot exceeded its byte limit.");
    }
  }
  await cdp.send("Target.disposeBrowserContext", { browserContextId });
  return result;
}

async function runIsolatedAuthoredHybridChrome(
  options: ChromeRunOptions,
  trustedInstrumentedDocument: boolean
): Promise<ChromeRunResult> {
  const isolationPolicy = authoredRendererIsolationPolicy();
  if (!isolationPolicy.rendererEnabled) {
    throw new Error("Authored vNext renderer is disabled by AUTHORED_VNEXT_RENDERER_ENABLED.");
  }
  if (!trustedInstrumentedDocument) {
    const preflight = preflightAuthoredHtmlForHybrid(options.html);
    if (!preflight.ok) {
      throw new Error(`Authored renderer preflight rejected HTML: ${preflight.reason ?? "unknown"}.`);
    }
  } else if (
    !options.html.includes(AUTHORED_HYBRID_BROWSER_SOURCE) ||
    /<script\b[^>]*\bsrc\s*=/i.test(options.html)
  ) {
    throw new Error("Trusted authored renderer input is missing its internal analyser or contains an external script.");
  }
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
  const profilePath = path.join(workDirectory, "chrome-profile");
  let result: ChromeRunResult | undefined;
  let captureError: unknown;
  let child: ChildProcess | undefined;

  try {
    const windowSize = options.windowSizePx ?? { width: 1280, height: 720 };
    const args = authoredRendererChromeArguments(profilePath, windowSize);
    child = spawn(executable, args, {
      stdio: ["ignore", "ignore", "ignore", "pipe", "pipe"],
      windowsHide: true,
      env: authoredRendererEnvironment(),
    });
    const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    result = await Promise.race([
      captureThroughCdp(child, options.html, options),
      new Promise<never>((_, reject) => {
        const timer = setTimeout(
          () => reject(new Error(`Chrome authored hybrid capture timed out after ${timeoutMs}ms.`)),
          timeoutMs
        );
        timer.unref?.();
      }),
    ]);
  } catch (error) {
    captureError = error;
  }

  if (child) {
    try {
      await terminateChrome(child, profilePath);
    } catch (error) {
      captureError = captureError === undefined
        ? error
        : new AggregateError([captureError, error], "Chrome capture and process cleanup failed.");
    }
  }

  // Preserve the original capture failure as the first aggregate member. A
  // cleanup problem remains observable, but must not erase the reason the
  // caller's Chrome capture originally failed.
  let cleanupError: unknown;
  try {
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

/** Public low-level entry point. It always validates raw authored HTML and can
 * no longer be used to bypass the product preflight. */
export async function runAuthoredHybridChrome(
  options: ChromeRunOptions
): Promise<ChromeRunResult> {
  return runIsolatedAuthoredHybridChrome(options, false);
}

/** @internal Used only after extractor-owned preflight and instrumentation. */
export async function runTrustedSelfContainedAuthoredHybridChrome(
  options: ChromeRunOptions
): Promise<ChromeRunResult> {
  return runIsolatedAuthoredHybridChrome(options, true);
}
