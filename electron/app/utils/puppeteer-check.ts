/**
 * puppeteer-check.ts
 *
 * Detects Chromium (or Chrome) for Puppeteer. We support Chromium from
 * browser-snapshots; the setup installer installs Chromium into the cache.
 */
import fs from "fs";
import puppeteer from "puppeteer";
import { Browser, getInstalledBrowsers } from "@puppeteer/browsers";
import {
  getPuppeteerCacheDir,
  getPuppeteerRuntime,
} from "./puppeteer-config";
import { safeWarn } from "./safe-console";

function normalizePathCandidate(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed || undefined;
}

function getConfiguredExecutablePath(): string | undefined {
  const runtime = getPuppeteerRuntime();
  return normalizePathCandidate(
    process.env.PUPPETEER_EXECUTABLE_PATH ?? runtime.configuration?.executablePath
  );
}

function shouldSkipDownload(): boolean {
  if (process.env.PUPPETEER_SKIP_DOWNLOAD) {
    const value = process.env.PUPPETEER_SKIP_DOWNLOAD.trim().toLowerCase();
    return value === "1" || value === "true" || value === "yes";
  }
  return Boolean((puppeteer as any).configuration?.skipDownload);
}

/** Status for the unified setup installer (what’s missing). */
export interface SetupStatus {
  needsLibreOffice: boolean;
  needsChrome: boolean;
  needsImageMagick: boolean;
}

/**
 * Returns the path to the browser executable to use for Puppeteer: either
 * Chrome (Puppeteer default) if present, or Chromium from the cache.
 */
export async function getPuppeteerExecutablePath(): Promise<string | undefined> {
  const cacheDir = getPuppeteerCacheDir();
  const configuredExecutablePath = getConfiguredExecutablePath();
  if (configuredExecutablePath && fs.existsSync(configuredExecutablePath)) {
    return configuredExecutablePath;
  }
  if (configuredExecutablePath) {
    safeWarn(
      `[Puppeteer] Configured executable path does not exist: ${configuredExecutablePath}`
    );
  }
  let chromePath: string | undefined;
  if (!shouldSkipDownload()) {
    try {
      chromePath = puppeteer.executablePath();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      safeWarn(`[Puppeteer] Failed to resolve default executable path: ${message}`);
    }
  }
  if (chromePath && fs.existsSync(chromePath)) return chromePath;
  const browsers = await getInstalledBrowsers({ cacheDir }).catch((error) => {
    const message = error instanceof Error ? error.message : String(error);
    safeWarn(`[Puppeteer] Failed to inspect installed browsers: ${message}`);
    return [];
  });
  const chromium = browsers.find((b) => b.browser === Browser.CHROMIUM);
  if (chromium?.executablePath && fs.existsSync(chromium.executablePath)) {
    return chromium.executablePath;
  }
  return undefined;
}

/**
 * Returns true if a supported browser (Chrome or Chromium) is already installed.
 */
export async function isChromeInstalled(): Promise<boolean> {
  try {
    const execPath = await getPuppeteerExecutablePath();
    return Boolean(execPath);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    safeWarn(`[Puppeteer] Browser availability check failed: ${message}`);
    return false;
  }
}

/**
 * Status for Puppeteer/Chromium (used by UI). Installation is done via the
 * unified setup window, not here.
 */
export type PuppeteerStatus =
  | "checking"
  | "installed"
  | "missing"
  | "downloading"
  | "downloaded"
  | "skipped"
  | "failed";

/**
 * Checks whether Chromium (or Chrome) is available. Does not install;
 * use the unified setup window to install.
 */
export async function checkPuppeteerChromiumBeforeWindow(
  onStatus?: (status: PuppeteerStatus) => void
): Promise<boolean> {
  onStatus?.("checking");
  let executablePath: string | undefined;
  try {
    executablePath = await getPuppeteerExecutablePath();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    safeWarn(`[Puppeteer] Browser check failed: ${message}`);
    onStatus?.("failed");
    return false;
  }
  if (executablePath) {
    console.log(`[Puppeteer] Browser found at ${executablePath}`);
    onStatus?.("installed");
    return true;
  }
  if (shouldSkipDownload()) {
    console.log("[Puppeteer] Skip download enabled.");
    onStatus?.("skipped");
    return true;
  }
  onStatus?.("missing");
  return true;
}
