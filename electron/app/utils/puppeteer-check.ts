/**
 * puppeteer-check.ts
 *
 * Ensures Puppeteer's Chromium/Chrome-for-Testing binary is downloaded
 * before the main BrowserWindow is created.
 */
import fs from "fs";
import os from "os";
import path from "path";
import puppeteer from "puppeteer";
import { Browser, detectBrowserPlatform, install } from "@puppeteer/browsers";

function getPuppeteerCacheDir(): string {
  const configCache =
    (puppeteer as any).configuration?.cacheDirectory ??
    (puppeteer as any).defaultDownloadPath;
  return configCache ?? path.join(os.homedir(), ".cache", "puppeteer");
}

function shouldSkipDownload(): boolean {
  if (process.env.PUPPETEER_SKIP_DOWNLOAD) {
    const value = process.env.PUPPETEER_SKIP_DOWNLOAD.trim().toLowerCase();
    return value === "1" || value === "true" || value === "yes";
  }
  return Boolean((puppeteer as any).configuration?.skipDownload);
}

/**
 * Ensures Puppeteer has its browser binary available.
 * Never blocks app startup — always returns `true`.
 */
export type PuppeteerStatus =
  | "checking"
  | "installed"
  | "missing"
  | "downloading"
  | "downloaded"
  | "skipped"
  | "failed";

export async function checkPuppeteerChromiumBeforeWindow(
  onStatus?: (status: PuppeteerStatus) => void
): Promise<boolean> {
  onStatus?.("checking");
  if (shouldSkipDownload()) {
    console.log("[Puppeteer] Skip download enabled.");
    onStatus?.("skipped");
    return true;
  }

  const executablePath = puppeteer.executablePath();
  if (executablePath && fs.existsSync(executablePath)) {
    console.log(`[Puppeteer] Chromium found at ${executablePath}`);
    onStatus?.("installed");
    return true;
  }

  onStatus?.("missing");
  const cacheDir = getPuppeteerCacheDir();
  const platform = detectBrowserPlatform();
  if (!platform) {
    console.warn("[Puppeteer] Unable to detect platform; skipping download.");
    onStatus?.("failed");
    return true;
  }

  const buildId =
    (puppeteer as any).browserVersion ??
    (puppeteer as any).defaultBrowserRevision;

  if (!buildId) {
    console.warn("[Puppeteer] Unable to resolve browser build; skipping download.");
    onStatus?.("failed");
    return true;
  }

  console.warn("[Puppeteer] Chromium missing – downloading now...");
  onStatus?.("downloading");
  try {
    await install({
      cacheDir,
      platform,
      browser: Browser.CHROME,
      buildId,
    });
    const downloadedPath = puppeteer.executablePath();
    if (downloadedPath && fs.existsSync(downloadedPath)) {
      console.log(`[Puppeteer] Chromium downloaded to ${downloadedPath}`);
      onStatus?.("downloaded");
    } else {
      console.log("[Puppeteer] Chromium download finished.");
      onStatus?.("downloaded");
    }
  } catch (error) {
    console.warn("[Puppeteer] Chromium download failed:", error);
    onStatus?.("failed");
  }

  return true;
}
