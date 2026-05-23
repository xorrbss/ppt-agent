/**
 * setup-dependencies.ts
 *
 * Single installer window that ensures LibreOffice, ImageMagick, and export
 * Chromium are available before the user starts creating presentations.
 */

import { BrowserWindow, ipcMain } from "electron";
import * as path from "path";
import { baseDir } from "./constants";
import { isLibreOfficeInstalled } from "./libreoffice-check";
import { isImageMagickInstalled } from "./imagemagick-check";
import {
  isExportChromiumAvailable,
  removeBrokenExportChromiumCaches,
} from "./export-chromium";

export interface SetupStatus {
  needsLibreOffice: boolean;
  needsImageMagick: boolean;
  needsChromium: boolean;
}

/** Set by checkDependenciesBeforeWindow; read by setup installer IPC. */
let currentSetupStatus: SetupStatus | null = null;

export function getSetupStatus(): SetupStatus | null {
  return currentSetupStatus;
}

/**
 * Checks LibreOffice, ImageMagick, and export Chromium. If all are present,
 * returns immediately. If any are missing, opens one installer window that runs
 * each missing setup step in sequence. Returns true only when all required
 * dependencies are installed; false when the installer is closed/skipped before
 * completion.
 */
export async function checkDependenciesBeforeWindow(): Promise<boolean> {
  await removeBrokenExportChromiumCaches();

  const [loResult, imageMagickInstalled, chromiumInstalled] = await Promise.all([
    isLibreOfficeInstalled(),
    Promise.resolve(isImageMagickInstalled()),
    Promise.resolve(isExportChromiumAvailable()),
  ]);

  const needsLibreOffice = !loResult.installed;
  const needsImageMagick = !imageMagickInstalled;
  const needsChromium = !chromiumInstalled;

  if (!needsLibreOffice && !needsImageMagick && !needsChromium) {
    return true;
  }

  currentSetupStatus = {
    needsLibreOffice,
    needsImageMagick,
    needsChromium,
  };

  await showSetupInstallerWindow();

  const [postLoResult, postImageMagickInstalled, postChromiumInstalled] = await Promise.all([
    isLibreOfficeInstalled(),
    Promise.resolve(isImageMagickInstalled()),
    Promise.resolve(isExportChromiumAvailable()),
  ]);

  currentSetupStatus = null;
  return postLoResult.installed && postImageMagickInstalled && postChromiumInstalled;
}

/**
 * Opens the unified setup installer window.
 * Resolves when the window is closed.
 */
function showSetupInstallerWindow(): Promise<void> {
  return new Promise((resolve) => {
    const win = new BrowserWindow({
      width: 520,
      height: 600,
      resizable: false,
      center: true,
      title: "Presenton – Setup required",
      icon: path.join(
        baseDir,
        "resources/ui/assets/images/presenton_short_filled.png"
      ),
      webPreferences: {
        webSecurity: false,
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: false,
        preload: path.join(__dirname, "../preloads/setup-installer.js"),
      },
    });

    win.setMenuBarVisibility(false);

    win.loadFile(
      path.join(baseDir, "resources/ui/setup-installer/index.html")
    );

    const onDone = () => {
      if (!win.isDestroyed()) win.close();
    };
    ipcMain.once("setup:done", onDone);

    win.on("closed", () => {
      ipcMain.removeListener("setup:done", onDone);
      resolve();
    });
  });
}
