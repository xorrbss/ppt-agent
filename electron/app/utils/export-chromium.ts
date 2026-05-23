import fs from "fs";
import os from "os";
import path from "path";
import { Browser, detectBrowserPlatform, install } from "@puppeteer/browsers";
import { safeLog } from "./safe-console";

/** Must match the Chrome revision expected by the bundled presentation-export runtime. */
const EXPORT_CHROME_BUILD_ID =
  process.env.EXPORT_CHROME_BUILD_ID?.trim() || "146.0.7680.76";

export type ChromiumInstallProgress = {
  phase: "downloading" | "installing" | "done" | "error";
  percent?: number;
  message?: string;
};

function resolvePuppeteerCacheRoot(): string {
  const configured = process.env.PUPPETEER_CACHE_DIR?.trim();
  if (configured) {
    return path.resolve(configured);
  }
  return path.join(os.homedir(), ".cache", "puppeteer");
}

function resolvePuppeteerChromeBaseDir(): string {
  return path.join(resolvePuppeteerCacheRoot(), "chrome");
}

function getExpectedExecutableRelativePath(): string {
  if (process.platform === "win32") {
    return path.join("chrome-win64", "chrome.exe");
  }
  if (process.platform === "darwin") {
    return path.join("chrome-mac", "Chromium.app", "Contents", "MacOS", "Chromium");
  }
  return path.join("chrome-linux64", "chrome");
}

function getChromeRevisionDirectories(): string[] {
  const chromeBaseDir = resolvePuppeteerChromeBaseDir();
  try {
    const entries = fs.readdirSync(chromeBaseDir, { withFileTypes: true });
    return entries
      .filter((entry) => entry.isDirectory())
      .map((entry) => path.join(chromeBaseDir, entry.name));
  } catch {
    return [];
  }
}

export function resolveInstalledExportChromiumPath(): string | null {
  const executableRelativePath = getExpectedExecutableRelativePath();
  for (const revisionDir of getChromeRevisionDirectories()) {
    const executablePath = path.join(revisionDir, executableRelativePath);
    if (fs.existsSync(executablePath)) {
      return executablePath;
    }
  }
  return null;
}

export function isExportChromiumAvailable(): boolean {
  return Boolean(resolveInstalledExportChromiumPath());
}

export async function removeBrokenExportChromiumCaches(): Promise<number> {
  const executableRelativePath = getExpectedExecutableRelativePath();
  let removedCount = 0;
  for (const revisionDir of getChromeRevisionDirectories()) {
    const executablePath = path.join(revisionDir, executableRelativePath);
    if (fs.existsSync(executablePath)) {
      continue;
    }
    try {
      await fs.promises.rm(revisionDir, { recursive: true, force: true });
      removedCount += 1;
      safeLog(`[Chromium] Removed broken cache: ${revisionDir}`);
    } catch {
      // Best effort cleanup only.
    }
  }
  return removedCount;
}

function formatMegabytes(bytes: number): string {
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export async function installExportChromium(
  onProgress?: (progress: ChromiumInstallProgress) => void
): Promise<void> {
  const removed = await removeBrokenExportChromiumCaches();
  if (removed > 0) {
    onProgress?.({
      phase: "installing",
      message: `Removed ${removed} incomplete Chromium download${removed === 1 ? "" : "s"}.`,
    });
  }

  if (isExportChromiumAvailable()) {
    onProgress?.({ phase: "done", percent: 100, message: "Chromium is already installed." });
    return;
  }

  const platform = detectBrowserPlatform();
  if (!platform) {
    throw new Error(`Unsupported platform for Chromium export runtime: ${process.platform}-${process.arch}`);
  }

  const cacheDir = resolvePuppeteerCacheRoot();
  await fs.promises.mkdir(cacheDir, { recursive: true });

  const buildId = EXPORT_CHROME_BUILD_ID;
  onProgress?.({
    phase: "downloading",
    percent: 0,
    message: `Downloading Chromium ${buildId}…`,
  });

  let lastLoggedPercent = -1;
  await install({
    browser: Browser.CHROME,
    buildId,
    cacheDir,
    platform,
    downloadProgressCallback(downloadedBytes, totalBytes) {
      if (totalBytes <= 0) {
        return;
      }
      const percent = Math.min(99, Math.floor((downloadedBytes / totalBytes) * 100));
      if (percent === lastLoggedPercent) {
        return;
      }
      lastLoggedPercent = percent;
      onProgress?.({
        phase: "downloading",
        percent,
        message: `${formatMegabytes(downloadedBytes)} / ${formatMegabytes(totalBytes)}`,
      });
    },
  });

  if (!isExportChromiumAvailable()) {
    throw new Error(
      "Chromium download finished but chrome executable was not found. Check your network connection and try again."
    );
  }

  onProgress?.({
    phase: "done",
    percent: 100,
    message: `Chromium ready (${resolveInstalledExportChromiumPath()})`,
  });
}

export async function ensureExportChromiumReady(): Promise<boolean> {
  await removeBrokenExportChromiumCaches();
  if (isExportChromiumAvailable()) {
    return true;
  }
  await installExportChromium();
  return isExportChromiumAvailable();
}
