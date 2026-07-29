import fs from "fs";
import os from "os";
import path from "path";
import {
  Browser,
  Cache,
  computeExecutablePath,
  detectBrowserPlatform,
  install,
} from "@puppeteer/browsers";
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

function resolveExportChromeInstallOptions():
  | { browser: Browser.CHROME; buildId: string; cacheDir: string; platform: NonNullable<ReturnType<typeof detectBrowserPlatform>> }
  | null {
  const platform = detectBrowserPlatform();
  if (!platform) {
    return null;
  }
  return {
    browser: Browser.CHROME,
    buildId: EXPORT_CHROME_BUILD_ID,
    cacheDir: resolvePuppeteerCacheRoot(),
    platform,
  };
}

/** Pre–Chrome-for-Testing cache layouts still present on some machines. */
function getLegacyExecutableRelativePaths(): string[] {
  if (process.platform === "win32") {
    return [
      path.join("chrome-win64", "chrome.exe"),
      path.join("chrome-win32", "chrome.exe"),
    ];
  }
  if (process.platform === "darwin") {
    return [
      path.join("chrome-mac", "Chromium.app", "Contents", "MacOS", "Chromium"),
      path.join("chrome-mac-arm64", "Chromium.app", "Contents", "MacOS", "Chromium"),
      path.join("chrome-mac-x64", "Chromium.app", "Contents", "MacOS", "Chromium"),
    ];
  }
  return [path.join("chrome-linux64", "chrome")];
}

function resolveLegacyInstalledExportChromiumPath(): string | null {
  const chromeBaseDir = path.join(resolvePuppeteerCacheRoot(), "chrome");
  let revisionDirs: string[] = [];
  try {
    revisionDirs = fs
      .readdirSync(chromeBaseDir, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => path.join(chromeBaseDir, entry.name));
  } catch {
    return null;
  }

  const legacyRelativePaths = getLegacyExecutableRelativePaths();
  for (const revisionDir of revisionDirs) {
    for (const relativePath of legacyRelativePaths) {
      const executablePath = path.join(revisionDir, relativePath);
      if (fs.existsSync(executablePath)) {
        return executablePath;
      }
    }
  }
  return null;
}

export function resolveInstalledExportChromiumPath(): string | null {
  const options = resolveExportChromeInstallOptions();
  if (options) {
    const expectedPath = computeExecutablePath(options);
    if (fs.existsSync(expectedPath)) {
      return expectedPath;
    }

    const cache = new Cache(options.cacheDir);
    for (const installed of cache.getInstalledBrowsers()) {
      if (installed.browser !== Browser.CHROME || installed.buildId !== options.buildId) {
        continue;
      }
      if (fs.existsSync(installed.executablePath)) {
        return installed.executablePath;
      }
    }
  }

  return resolveLegacyInstalledExportChromiumPath();
}

export function isExportChromiumAvailable(): boolean {
  return Boolean(resolveInstalledExportChromiumPath());
}

export async function removeBrokenExportChromiumCaches(): Promise<number> {
  const cacheDir = resolvePuppeteerCacheRoot();
  const cache = new Cache(cacheDir);
  let removedCount = 0;

  for (const installed of cache.getInstalledBrowsers()) {
    if (installed.browser !== Browser.CHROME) {
      continue;
    }
    if (fs.existsSync(installed.executablePath)) {
      continue;
    }
    try {
      await fs.promises.rm(installed.path, { recursive: true, force: true });
      removedCount += 1;
      safeLog(`[Chromium] Removed broken cache: ${installed.path}`);
    } catch {
      // Best effort cleanup only.
    }
  }

  const chromeBaseDir = path.join(cacheDir, "chrome");
  const legacyRelativePaths = getLegacyExecutableRelativePaths();
  let revisionDirs: string[] = [];
  try {
    revisionDirs = fs
      .readdirSync(chromeBaseDir, { withFileTypes: true })
      .filter((entry) => entry.isDirectory())
      .map((entry) => path.join(chromeBaseDir, entry.name));
  } catch {
    return removedCount;
  }

  for (const revisionDir of revisionDirs) {
    const hasLegacyExecutable = legacyRelativePaths.some((relativePath) =>
      fs.existsSync(path.join(revisionDir, relativePath))
    );
    if (hasLegacyExecutable) {
      continue;
    }

    const basename = path.basename(revisionDir);
    if (basename.includes("-")) {
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

  const options = resolveExportChromeInstallOptions();
  if (!options) {
    throw new Error(`Unsupported platform for Chromium export runtime: ${process.platform}-${process.arch}`);
  }

  await fs.promises.mkdir(options.cacheDir, { recursive: true });

  onProgress?.({
    phase: "downloading",
    percent: 0,
    message: `Downloading Chromium ${options.buildId}…`,
  });

  let lastLoggedPercent = -1;
  await install({
    ...options,
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
    const expectedPath = computeExecutablePath(options);
    throw new Error(
      `Chromium download finished but chrome executable was not found at ${expectedPath}. Check your network connection and try again.`
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
