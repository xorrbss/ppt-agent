/**
 * IPC handlers for the unified setup installer (LibreOffice + Chromium + ImageMagick).
 * - setup:get-status — which dependencies are missing
 * - setup:install-chrome — download Chromium (browser-snapshots) with progress
 */

import { ipcMain, WebContents, shell } from "electron";
import fs from "fs";
import path from "path";
import os from "os";
import { spawn, spawnSync } from "child_process";
import puppeteer from "puppeteer";
import {
  Browser,
  detectBrowserPlatform,
  getInstalledBrowsers,
  install,
  resolveBuildId,
} from "@puppeteer/browsers";
import { getSetupStatus } from "../utils/setup-dependencies";
import {
  getImageMagickDownloadUrl,
  getImageMagickManualInstallCommands,
  isImageMagickInstalled,
} from "../utils/imagemagick-check";

function getPuppeteerCacheDir(): string {
  const configCache =
    (puppeteer as any).configuration?.cacheDirectory ??
    (puppeteer as any).defaultDownloadPath;
  return configCache ?? path.join(os.homedir(), ".cache", "puppeteer");
}

function sendChromeProgress(
  wc: WebContents,
  phase: "downloading" | "extracting" | "done" | "error",
  percent?: number,
  message?: string
) {
  if (!wc.isDestroyed()) {
    wc.send("setup:chrome-progress", { phase, percent, message });
  }
}

function sendChromeLog(wc: WebContents, level: string, text: string) {
  if (!wc.isDestroyed()) {
    wc.send("setup:chrome-log", { level, text });
  }
}

function sendImageMagickProgress(
  wc: WebContents,
  phase: "installing" | "done" | "error",
  percent?: number,
  message?: string
) {
  if (!wc.isDestroyed()) {
    wc.send("setup:imagemagick-progress", { phase, percent, message });
  }
}

function sendImageMagickLog(wc: WebContents, level: string, text: string) {
  if (!wc.isDestroyed()) {
    wc.send("setup:imagemagick-log", { level, text });
  }
}

function commandExists(command: string, versionArgs: string[] = ["--version"]): boolean {
  const result = spawnSync(command, versionArgs, {
    stdio: "pipe",
    windowsHide: true,
  });
  return result.status === 0;
}

function resolveBrewCommand(): string | null {
  if (commandExists("brew")) {
    return "brew";
  }

  const candidates = ["/opt/homebrew/bin/brew", "/usr/local/bin/brew"];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) {
      return candidate;
    }
  }
  return null;
}

function resolveLinuxEscalationCommand(): string | null {
  if (commandExists("pkexec", ["--version"])) return "pkexec";
  if (commandExists("sudo", ["-V"])) return "sudo";
  return null;
}

function logManualImageMagickCommands(wc: WebContents) {
  for (const line of getImageMagickManualInstallCommands()) {
    const level = line.endsWith(":") ? "info" : "cmd";
    sendImageMagickLog(wc, level, line);
  }
}

function runInstallCommand(
  wc: WebContents,
  command: string,
  args: string[]
): Promise<void> {
  sendImageMagickLog(wc, "info", `Running: ${command} ${args.join(" ")}`);
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: process.platform === "win32",
    });

    child.stdout.on("data", (data) => {
      const text = String(data).trim();
      if (text) sendImageMagickLog(wc, "info", text);
    });
    child.stderr.on("data", (data) => {
      const text = String(data).trim();
      if (text) {
        sendImageMagickLog(
          wc,
          text.toLowerCase().includes("error") ? "error" : "info",
          text
        );
      }
    });

    child.on("error", reject);
    child.on("close", (code) => {
      if (code === 0) {
        resolve();
        return;
      }
      reject(new Error(`${command} exited with code ${code}`));
    });
  });
}

export function setupSetupInstallHandlers() {
  ipcMain.handle("setup:get-status", () => {
    return (
      getSetupStatus() ?? {
        needsLibreOffice: false,
        needsChrome: false,
        needsImageMagick: false,
      }
    );
  });

  ipcMain.handle(
    "setup:install-chrome",
    async (event): Promise<{ ok: boolean; error?: string }> => {
      const wc = event.sender;

      const cacheDir = getPuppeteerCacheDir();
      const platform = detectBrowserPlatform();
      if (!platform) {
        const msg = "Unable to detect platform.";
        sendChromeLog(wc, "error", msg);
        sendChromeProgress(wc, "error", undefined, msg);
        return { ok: false, error: msg };
      }

      let buildId: string;
      try {
        buildId = await resolveBuildId(
          Browser.CHROMIUM,
          platform,
          "latest" as "latest"
        );
      } catch (err) {
        const msg =
          err instanceof Error
            ? err.message
            : "Unable to resolve Chromium revision.";
        sendChromeLog(wc, "error", msg);
        sendChromeProgress(wc, "error", undefined, msg);
        return { ok: false, error: msg };
      }

      sendChromeLog(wc, "info", `Downloading Chromium r${buildId}…`);
      sendChromeProgress(wc, "downloading", 0, "Connecting…");

      try {
        await install({
          cacheDir,
          platform,
          browser: Browser.CHROMIUM,
          buildId,
          downloadProgressCallback: (downloadedBytes, totalBytes) => {
            if (totalBytes > 0 && !wc.isDestroyed()) {
              const percent = Math.min(
                99,
                Math.round((downloadedBytes / totalBytes) * 100)
              );
              const mb = (n: number) => (n / 1024 / 1024).toFixed(1);
              sendChromeProgress(
                wc,
                "downloading",
                percent,
                `${mb(downloadedBytes)} / ${mb(totalBytes)} MB`
              );
            }
          },
        });
      } catch (err) {
        const message =
          err instanceof Error ? err.message : "Chromium download failed.";
        sendChromeLog(wc, "error", message);
        sendChromeProgress(wc, "error", undefined, message);
        return { ok: false, error: message };
      }

      sendChromeProgress(wc, "extracting", 100, "Extracting…");
      const browsers = await getInstalledBrowsers({ cacheDir });
      const chromium = browsers.find((b) => b.browser === Browser.CHROMIUM);
      if (chromium?.executablePath && fs.existsSync(chromium.executablePath)) {
        sendChromeLog(wc, "ok", `Chromium ready at ${chromium.executablePath}`);
      }
      sendChromeProgress(wc, "done", 100);
      return { ok: true };
    }
  );

  ipcMain.handle(
    "setup:install-imagemagick",
    async (event): Promise<{ ok: boolean; error?: string }> => {
      const wc = event.sender;
      try {
        sendImageMagickProgress(
          wc,
          "installing",
          undefined,
          "Installing ImageMagick..."
        );

        if (process.platform === "linux") {
          if (commandExists("apt-get")) {
            const escalator = resolveLinuxEscalationCommand();
            if (!escalator) {
              throw new Error(
                "Neither pkexec nor sudo is available to run apt-get install."
              );
            }

            await runInstallCommand(wc, escalator, [
              "apt-get",
              "update",
            ]);
            await runInstallCommand(wc, escalator, [
              "apt-get",
              "install",
              "-y",
              "imagemagick",
            ]);
          } else {
            throw new Error(
              "apt-get is unavailable. Install ImageMagick manually using your package manager."
            );
          }
        } else if (process.platform === "darwin") {
          let brewCommand = resolveBrewCommand();
          if (!brewCommand) {
            sendImageMagickLog(
              wc,
              "info",
              "Homebrew not found. Installing Homebrew first..."
            );
            const installHomebrewCommand =
              'NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"';
            await runInstallCommand(wc, "/bin/bash", ["-c", installHomebrewCommand]);
            brewCommand = resolveBrewCommand();
          }

          if (!brewCommand) {
            throw new Error(
              "Homebrew installation completed, but brew was not found on PATH."
            );
          }

          await runInstallCommand(wc, brewCommand, ["install", "imagemagick"]);
        } else if (process.platform === "win32") {
          if (commandExists("choco", ["-v"])) {
            await runInstallCommand(wc, "choco", [
              "install",
              "imagemagick.app",
              "-y",
            ]);
          } else {
            throw new Error(
              "Chocolatey is not installed. Falling back to direct installer download."
            );
          }
        } else {
          throw new Error(
            "Unsupported platform for automatic install. Use manual install from the official download page."
          );
        }

        sendImageMagickProgress(wc, "done", 100, "ImageMagick install finished");
        return { ok: true };
      } catch (error) {
        const message =
          error instanceof Error ? error.message : "ImageMagick install failed";
        sendImageMagickLog(wc, "error", message);
        logManualImageMagickCommands(wc);
        const downloadUrl = getImageMagickDownloadUrl();
        sendImageMagickLog(
          wc,
          "info",
          `Opening manual install link: ${downloadUrl}`
        );
        await shell.openExternal(downloadUrl);
        sendImageMagickProgress(
          wc,
          "error",
          undefined,
          "Finish manual installation, then click Retry."
        );
        return { ok: false, error: message };
      }
    }
  );

  ipcMain.handle(
    "setup:check-imagemagick",
    async (event): Promise<{ ok: boolean; error?: string }> => {
      const wc = event.sender;
      const installed = isImageMagickInstalled();
      if (installed) {
        sendImageMagickProgress(wc, "done", 100, "ImageMagick detected");
        sendImageMagickLog(wc, "ok", "ImageMagick is installed and ready.");
        return { ok: true };
      }
      const message =
        "ImageMagick is not detected yet. Install it, then click Retry.";
      sendImageMagickProgress(wc, "error", undefined, message);
      sendImageMagickLog(wc, "error", message);
      return { ok: false, error: message };
    }
  );
}
